"""SQLite 存储实现。

设计原则：
- 一个文件就是一个 DB（./ticket_mcp.db 或自定义路径）
- 启动时自动建表（DDL idempotent）
- 用 sqlite3 内置模块，零外部依赖
- 异常、事务、连接释放按规则 13 处理

连接模型：
- SQLiteTicketStore 和 SQLiteCRMStore 共享同一个 DB 文件 + 同一个连接
- 通过 threading.RLock 保护多线程读写
- sqlite3 默认 check_same_thread=True，但 OpenClaw 的 MCP 调用是单线程 asyncio 事件循环里发的
- 长事务：用上下文管理器包起来

后续替换：
- 多进程部署 → 切到 PostgreSQL / MySQL（同样实现 TicketStore/CRMStore 接口即可）
- 接真实 CRM → 替换 SQLiteCRMStore 实现，TicketStore 不动
"""
import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime
from typing import Any

from .exceptions import (
    ContactNotFoundError,
    CustomerNotFoundError,
    FollowUpNotFoundError,
    TicketNotFoundError,
    TodoNotFoundError,
)
from .models import (
    Contact,
    Customer,
    CustomerStatus,
    FollowUp,
    FollowUpStatus,
    Priority,
    TicketRecord,
    TicketStatus,
    Todo,
    TodoStatus,
)
from .storage import CRMStore, TicketStore


# ============================================================
# Schema
# ============================================================


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS customers (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    industry TEXT,
    status TEXT NOT NULL DEFAULT 'lead',
    notes TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS contacts (
    id TEXT PRIMARY KEY,
    customer_id TEXT NOT NULL,
    name TEXT NOT NULL,
    phone TEXT,
    email TEXT,
    role TEXT,
    is_primary INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    FOREIGN KEY (customer_id) REFERENCES customers(id)
);

CREATE TABLE IF NOT EXISTS follow_ups (
    id TEXT PRIMARY KEY,
    customer_id TEXT NOT NULL,
    contact_id TEXT,
    content TEXT NOT NULL,
    follow_up_at TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    created_at TEXT NOT NULL,
    FOREIGN KEY (customer_id) REFERENCES customers(id)
);

CREATE TABLE IF NOT EXISTS todos (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    description TEXT,
    due_at TEXT,
    status TEXT NOT NULL DEFAULT 'open',
    customer_id TEXT,
    contact_id TEXT,
    follow_up_id TEXT,
    idempotency_key TEXT UNIQUE,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tickets (
    id TEXT PRIMARY KEY,
    customer_name TEXT,
    title TEXT NOT NULL,
    description TEXT NOT NULL,
    priority TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'new',
    source_message_id TEXT,
    source_channel TEXT NOT NULL DEFAULT 'openclaw',
    idempotency_key TEXT UNIQUE NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_customers_name ON customers(name);
CREATE INDEX IF NOT EXISTS idx_contacts_customer ON contacts(customer_id);
CREATE INDEX IF NOT EXISTS idx_contacts_name ON contacts(name);
CREATE INDEX IF NOT EXISTS idx_follow_ups_customer ON follow_ups(customer_id);
CREATE INDEX IF NOT EXISTS idx_follow_ups_contact ON follow_ups(contact_id);
CREATE INDEX IF NOT EXISTS idx_todos_status ON todos(status);
CREATE INDEX IF NOT EXISTS idx_todos_customer ON todos(customer_id);
"""


def _iso(dt: datetime | None) -> str | None:
    """datetime → ISO 字符串。"""
    return dt.isoformat() if dt is not None else None


def _from_iso(s: str | None) -> datetime | None:
    """ISO 字符串 → datetime。"""
    return datetime.fromisoformat(s) if s else None


# ============================================================
# 共享连接 + 线程安全
# ============================================================


class _SQLiteConnection:
    """sqlite3 连接的薄包装。

    - 单文件单连接（MCP 是 asyncio 单进程，足够）
    - RLock 保护跨线程访问（虽然 MCP 主线程单线程，tools 内部也无并发，
      但 in-memory 实现也用了 Lock，保持一致）
    - 自动 init schema
    - with 语句自动 commit/rollback
    """

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        self._lock = threading.RLock()
        # check_same_thread=False：sqlite3 默认不允许跨线程；我们的 MCP 是 asyncio 单线程
        # 但 init/cleanup 可能在另一线程。False 允许。
        self._conn = sqlite3.connect(
            db_path,
            check_same_thread=False,
            isolation_level=None,  # autocommit 模式；事务用 BEGIN/COMMIT 显式控制
            timeout=10.0,
        )
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.executescript(SCHEMA_SQL)

    @contextmanager
    def transaction(self):
        """事务上下文管理器。

        用法：
            with conn.transaction():
                conn.execute(...)
                conn.execute(...)

        正常退出 commit，异常退出 rollback。
        """
        with self._lock:
            self._conn.execute("BEGIN")
            try:
                yield self._conn
                self._conn.execute("COMMIT")
            except Exception:
                self._conn.execute("ROLLBACK")
                raise

    def close(self) -> None:
        with self._lock:
            self._conn.close()


# ============================================================
# SQLiteTicketStore
# ============================================================


class SQLiteTicketStore(TicketStore):
    """工单 SQLite 实现。"""

    def __init__(self, conn: _SQLiteConnection) -> None:
        self._conn = conn

    def save(self, ticket: TicketRecord) -> None:
        with self._conn.transaction() as c:
            c.execute(
                """INSERT OR REPLACE INTO tickets
                   (id, customer_name, title, description, priority, status,
                    source_message_id, source_channel, idempotency_key, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    ticket.ticket_id,
                    ticket.customer_name,
                    ticket.title,
                    ticket.description,
                    ticket.priority.value,
                    ticket.status.value,
                    ticket.source_message_id,
                    ticket.source_channel,
                    ticket.idempotency_key,
                    _iso(ticket.created_at),
                ),
            )

    def find_by_id(self, ticket_id: str) -> TicketRecord:
        with self._conn.transaction() as c:
            row = c.execute(
                "SELECT * FROM tickets WHERE id = ?", (ticket_id,)
            ).fetchone()
        if row is None:
            raise TicketNotFoundError(ticket_id)
        return self._row_to_ticket(row)

    def find_by_idempotency_key(self, key: str) -> TicketRecord | None:
        with self._conn.transaction() as c:
            row = c.execute(
                "SELECT * FROM tickets WHERE idempotency_key = ?", (key,)
            ).fetchone()
        if row is None:
            return None
        return self._row_to_ticket(row)

    def list_all(self) -> list[TicketRecord]:
        with self._conn.transaction() as c:
            rows = c.execute("SELECT * FROM tickets ORDER BY created_at DESC").fetchall()
        return [self._row_to_ticket(r) for r in rows]

    @staticmethod
    def _row_to_ticket(row: sqlite3.Row) -> TicketRecord:
        return TicketRecord(
            ticket_id=row["id"],
            customer_name=row["customer_name"],
            title=row["title"],
            description=row["description"],
            priority=Priority(row["priority"]),
            status=TicketStatus(row["status"]),
            source_message_id=row["source_message_id"],
            source_channel=row["source_channel"],
            idempotency_key=row["idempotency_key"],
            created_at=_from_iso(row["created_at"]),
        )


# ============================================================
# SQLiteCRMStore
# ============================================================


class SQLiteCRMStore(CRMStore):
    """CRM SQLite 实现。"""

    def __init__(self, conn: _SQLiteConnection) -> None:
        self._conn = conn

    # ---------- Customer ----------

    def save_customer(self, customer: Customer) -> None:
        with self._conn.transaction() as c:
            c.execute(
                """INSERT OR REPLACE INTO customers
                   (id, name, industry, status, notes, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    customer.id,
                    customer.name,
                    customer.industry,
                    customer.status.value,
                    customer.notes,
                    _iso(customer.created_at),
                    _iso(customer.updated_at),
                ),
            )

    def find_customer_by_id(self, customer_id: str) -> Customer:
        with self._conn.transaction() as c:
            row = c.execute(
                "SELECT * FROM customers WHERE id = ?", (customer_id,)
            ).fetchone()
        if row is None:
            raise CustomerNotFoundError(customer_id)
        return self._row_to_customer(row)

    def search_customers(self, query: str) -> list[Customer]:
        q = f"%{query.lower()}%"
        with self._conn.transaction() as c:
            rows = c.execute(
                "SELECT * FROM customers WHERE LOWER(name) LIKE ? ORDER BY name",
                (q,),
            ).fetchall()
        return [self._row_to_customer(r) for r in rows]

    def customer_exists_by_name(self, name: str) -> Customer | None:
        target = name.strip().lower()
        with self._conn.transaction() as c:
            row = c.execute(
                "SELECT * FROM customers WHERE LOWER(name) = ? LIMIT 1", (target,)
            ).fetchone()
        return self._row_to_customer(row) if row else None

    # ---------- Contact ----------

    def save_contact(self, contact: Contact) -> None:
        with self._conn.transaction() as c:
            c.execute(
                """INSERT OR REPLACE INTO contacts
                   (id, customer_id, name, phone, email, role, is_primary, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    contact.id,
                    contact.customer_id,
                    contact.name,
                    contact.phone,
                    contact.email,
                    contact.role,
                    int(contact.is_primary),
                    _iso(contact.created_at),
                ),
            )

    def find_contact_by_id(self, contact_id: str) -> Contact:
        with self._conn.transaction() as c:
            row = c.execute(
                "SELECT * FROM contacts WHERE id = ?", (contact_id,)
            ).fetchone()
        if row is None:
            raise ContactNotFoundError(contact_id)
        return self._row_to_contact(row)

    def search_contacts(self, query: str, customer_id: str | None = None) -> list[Contact]:
        q = f"%{query.lower()}%"
        with self._conn.transaction() as c:
            if customer_id is not None:
                rows = c.execute(
                    "SELECT * FROM contacts WHERE LOWER(name) LIKE ? AND customer_id = ? ORDER BY name",
                    (q, customer_id),
                ).fetchall()
            else:
                rows = c.execute(
                    "SELECT * FROM contacts WHERE LOWER(name) LIKE ? ORDER BY name",
                    (q,),
                ).fetchall()
        return [self._row_to_contact(r) for r in rows]

    # ---------- FollowUp ----------

    def save_follow_up(self, follow_up: FollowUp) -> None:
        with self._conn.transaction() as c:
            c.execute(
                """INSERT OR REPLACE INTO follow_ups
                   (id, customer_id, contact_id, content, follow_up_at, status, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    follow_up.id,
                    follow_up.customer_id,
                    follow_up.contact_id,
                    follow_up.content,
                    _iso(follow_up.follow_up_at),
                    follow_up.status.value,
                    _iso(follow_up.created_at),
                ),
            )

    def find_follow_up_by_id(self, follow_up_id: str) -> FollowUp:
        with self._conn.transaction() as c:
            row = c.execute(
                "SELECT * FROM follow_ups WHERE id = ?", (follow_up_id,)
            ).fetchone()
        if row is None:
            raise FollowUpNotFoundError(follow_up_id)
        return self._row_to_follow_up(row)

    def search_follow_ups(
        self,
        customer_id: str | None = None,
        contact_id: str | None = None,
        status: str | None = None,
    ) -> list[FollowUp]:
        clauses: list[str] = []
        args: list[Any] = []
        if customer_id is not None:
            clauses.append("customer_id = ?")
            args.append(customer_id)
        if contact_id is not None:
            clauses.append("contact_id = ?")
            args.append(contact_id)
        if status is not None:
            clauses.append("status = ?")
            args.append(status)
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        sql = f"SELECT * FROM follow_ups {where} ORDER BY created_at DESC"
        with self._conn.transaction() as c:
            rows = c.execute(sql, args).fetchall()
        return [self._row_to_follow_up(r) for r in rows]

    # ---------- Todo ----------

    def save_todo(self, todo: Todo) -> None:
        with self._conn.transaction() as c:
            c.execute(
                """INSERT OR REPLACE INTO todos
                   (id, title, description, due_at, status,
                    customer_id, contact_id, follow_up_id, idempotency_key, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    todo.id,
                    todo.title,
                    todo.description,
                    _iso(todo.due_at),
                    todo.status.value,
                    todo.customer_id,
                    todo.contact_id,
                    todo.follow_up_id,
                    todo.idempotency_key,
                    _iso(todo.created_at),
                ),
            )

    def find_todo_by_id(self, todo_id: str) -> Todo:
        with self._conn.transaction() as c:
            row = c.execute("SELECT * FROM todos WHERE id = ?", (todo_id,)).fetchone()
        if row is None:
            raise TodoNotFoundError(todo_id)
        return self._row_to_todo(row)

    def list_todos(
        self,
        status: str | None = None,
        customer_id: str | None = None,
    ) -> list[Todo]:
        clauses: list[str] = []
        args: list[Any] = []
        if status is not None:
            clauses.append("status = ?")
            args.append(status)
        if customer_id is not None:
            clauses.append("customer_id = ?")
            args.append(customer_id)
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        # 按 due_at 升序，无 due_at 的放后面
        sql = f"SELECT * FROM todos {where} ORDER BY (due_at IS NULL), due_at ASC, created_at DESC"
        with self._conn.transaction() as c:
            rows = c.execute(sql, args).fetchall()
        return [self._row_to_todo(r) for r in rows]

    def find_todo_by_idempotency_key(self, key: str) -> Todo | None:
        with self._conn.transaction() as c:
            row = c.execute(
                "SELECT * FROM todos WHERE idempotency_key = ?", (key,)
            ).fetchone()
        return self._row_to_todo(row) if row else None

    # ---------- row → model 转换 ----------

    @staticmethod
    def _row_to_customer(row: sqlite3.Row) -> Customer:
        return Customer(
            id=row["id"],
            name=row["name"],
            industry=row["industry"],
            status=CustomerStatus(row["status"]),
            notes=row["notes"],
            created_at=_from_iso(row["created_at"]),
            updated_at=_from_iso(row["updated_at"]),
        )

    @staticmethod
    def _row_to_contact(row: sqlite3.Row) -> Contact:
        return Contact(
            id=row["id"],
            customer_id=row["customer_id"],
            name=row["name"],
            phone=row["phone"],
            email=row["email"],
            role=row["role"],
            is_primary=bool(row["is_primary"]),
            created_at=_from_iso(row["created_at"]),
        )

    @staticmethod
    def _row_to_follow_up(row: sqlite3.Row) -> FollowUp:
        return FollowUp(
            id=row["id"],
            customer_id=row["customer_id"],
            contact_id=row["contact_id"],
            content=row["content"],
            follow_up_at=_from_iso(row["follow_up_at"]),
            status=FollowUpStatus(row["status"]),
            created_at=_from_iso(row["created_at"]),
        )

    @staticmethod
    def _row_to_todo(row: sqlite3.Row) -> Todo:
        return Todo(
            id=row["id"],
            title=row["title"],
            description=row["description"],
            due_at=_from_iso(row["due_at"]),
            status=TodoStatus(row["status"]),
            customer_id=row["customer_id"],
            contact_id=row["contact_id"],
            follow_up_id=row["follow_up_id"],
            idempotency_key=row["idempotency_key"],
            created_at=_from_iso(row["created_at"]),
        )


# ============================================================
# 工厂
# ============================================================


class SQLiteStores:
    """SQLite 实现的 stores 集合。

    返回两个 store 共用一个连接。
    同时提供 close() 用于优雅退出。
    """

    def __init__(self, db_path: str) -> None:
        self._conn = _SQLiteConnection(db_path)
        self.ticket = SQLiteTicketStore(self._conn)
        self.crm = SQLiteCRMStore(self._conn)

    def close(self) -> None:
        self._conn.close()


def create_sqlite_stores(db_path: str) -> SQLiteStores:
    """创建 SQLite-backed stores 集合。

    Args:
        db_path: SQLite 文件路径

    Returns:
        SQLiteStores（包含 ticket / crm + close 方法）
    """
    return SQLiteStores(db_path)
