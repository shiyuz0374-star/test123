"""存储抽象层。

设计原则：
- 通过抽象基类定义接口
- 默认提供 InMemory 实现（开发/测试用）
- 后续可扩展 SQLite / Postgres / ExternalCRM 实现
- 切换实现只需在 server.py 替换实例化

当前两套存储：
- TicketStore: 工单（既有）
- CRMStore: 客户 / 联系人 / 跟进 / 待办（新增）

为什么分开两个 Store 而不是合并：
- 职责清晰（工单 vs 客户运营是不同业务域）
- 后续可独立选存储后端（如客户用 CRM 外部接口，工单用本地 DB）
- 测试可单独替换其中之一
"""
import threading
from abc import ABC, abstractmethod

from .exceptions import (
    CustomerNotFoundError,
    ContactNotFoundError,
    FollowUpNotFoundError,
    IdempotencyConflictError,
    TicketNotFoundError,
    TodoNotFoundError,
)
from .models import (
    Contact,
    Customer,
    FollowUp,
    TicketRecord,
    Todo,
)

# ============================================================
# TicketStore（既有，保持不变）
# ============================================================


class TicketStore(ABC):
    """工单存储抽象接口。

    所有存储实现必须实现以下方法。
    """

    @abstractmethod
    def save(self, ticket: TicketRecord) -> None:
        """保存工单。如果 ID 已存在则覆盖（一般不会发生，因为 ID 是时间戳生成的）。"""
        raise NotImplementedError

    @abstractmethod
    def find_by_id(self, ticket_id: str) -> TicketRecord:
        """按 ID 查询。找不到抛 TicketNotFoundError。"""
        raise NotImplementedError

    @abstractmethod
    def find_by_idempotency_key(self, key: str) -> TicketRecord | None:
        """按幂等键查询。返回 None 表示未找到。"""
        raise NotImplementedError

    @abstractmethod
    def list_all(self) -> list[TicketRecord]:
        """列出所有工单（仅用于调试/管理）。"""
        raise NotImplementedError


class InMemoryTicketStore(TicketStore):
    """内存实现。

    特点：
    - 进程重启数据丢失（仅适合开发/Day 1）
    - 线程安全（用 threading.Lock）
    - 性能足够支撑单测和小流量

    何时替换：
    - 需要持久化 → SQLiteTicketStore
    - 多实例部署 → PostgresTicketStore（外部 DB）
    - 接真实 CRM → ExternalCRMTicketStore
    """

    def __init__(self) -> None:
        self._by_id: dict[str, TicketRecord] = {}
        self._by_key: dict[str, str] = {}  # idempotency_key -> ticket_id
        self._lock = threading.Lock()

    def save(self, ticket: TicketRecord) -> None:
        with self._lock:
            self._by_id[ticket.ticket_id] = ticket
            self._by_key[ticket.idempotency_key] = ticket.ticket_id

    def find_by_id(self, ticket_id: str) -> TicketRecord:
        with self._lock:
            ticket = self._by_id.get(ticket_id)
        if ticket is None:
            raise TicketNotFoundError(ticket_id)
        return ticket

    def find_by_idempotency_key(self, key: str) -> TicketRecord | None:
        with self._lock:
            ticket_id = self._by_key.get(key)
            if ticket_id is None:
                return None
            return self._by_id.get(ticket_id)

    def list_all(self) -> list[TicketRecord]:
        with self._lock:
            return list(self._by_id.values())


def create_ticket_store(backend: str = "memory", db_path: str = "") -> TicketStore:
    """工厂函数：根据配置返回对应的 TicketStore 实现。

    Args:
        backend: "memory" 或 "sqlite"
        db_path: SQLite 文件路径（backend=sqlite 时必填）

    Returns:
        TicketStore 实例
    """
    if backend == "sqlite":
        if not db_path:
            raise ValueError("db_path is required when backend='sqlite'")
        # 延迟导入，避免 memory-only 部署时强依赖
        from .sqlite_store import SQLiteStores, create_sqlite_stores
        stores = create_sqlite_stores(db_path)
        return stores.ticket
    return InMemoryTicketStore()


# ============================================================
# CRMStore（新增）
# ============================================================


class CRMStore(ABC):
    """CRM 存储抽象（客户/联系人/跟进/待办）。

    设计原则（与 TicketStore 一致）：
    - 接口与实现分离
    - 异常在 store 层抛，由调用方捕获转 ToolError
    - 幂等通过 idempotency_key 做软约束（store 层不强制，由调用方/业务层控制）
    """

    # ---------- Customer ----------

    @abstractmethod
    def save_customer(self, customer: Customer) -> None:
        raise NotImplementedError

    @abstractmethod
    def find_customer_by_id(self, customer_id: str) -> Customer:
        raise NotImplementedError

    @abstractmethod
    def search_customers(self, query: str) -> list[Customer]:
        """按公司名模糊匹配（不区分大小写）。"""
        raise NotImplementedError

    @abstractmethod
    def customer_exists_by_name(self, name: str) -> Customer | None:
        """精确匹配同名客户（用于去重）。返回 Customer 或 None。"""
        raise NotImplementedError

    # ---------- Contact ----------

    @abstractmethod
    def save_contact(self, contact: Contact) -> None:
        raise NotImplementedError

    @abstractmethod
    def find_contact_by_id(self, contact_id: str) -> Contact:
        raise NotImplementedError

    @abstractmethod
    def search_contacts(self, query: str, customer_id: str | None = None) -> list[Contact]:
        """按姓名模糊匹配，可选限定客户范围。"""
        raise NotImplementedError

    # ---------- FollowUp ----------

    @abstractmethod
    def save_follow_up(self, follow_up: FollowUp) -> None:
        raise NotImplementedError

    @abstractmethod
    def find_follow_up_by_id(self, follow_up_id: str) -> FollowUp:
        raise NotImplementedError

    @abstractmethod
    def search_follow_ups(
        self,
        customer_id: str | None = None,
        contact_id: str | None = None,
        status: str | None = None,
    ) -> list[FollowUp]:
        """按客户/联系人/状态筛选跟进。"""
        raise NotImplementedError

    # ---------- Todo ----------

    @abstractmethod
    def save_todo(self, todo: Todo) -> None:
        raise NotImplementedError

    @abstractmethod
    def find_todo_by_id(self, todo_id: str) -> Todo:
        raise NotImplementedError

    @abstractmethod
    def list_todos(
        self,
        status: str | None = None,
        customer_id: str | None = None,
    ) -> list[Todo]:
        """列出待办，可按状态/客户筛选。"""
        raise NotImplementedError

    @abstractmethod
    def find_todo_by_idempotency_key(self, key: str) -> Todo | None:
        raise NotImplementedError


class InMemoryCRMStore(CRMStore):
    """内存 CRM 实现。

    与 InMemoryTicketStore 风格一致：
    - 进程重启数据丢失
    - 线程安全
    - 用 dict + Lock

    后续替换：
    - SQLiteCRMStore（文件持久化）
    - ExternalCRMStore（接 Salesforce / HubSpot 等）
    """

    def __init__(self) -> None:
        self._customers: dict[str, Customer] = {}
        self._contacts: dict[str, Contact] = {}
        self._follow_ups: dict[str, FollowUp] = {}
        self._todos: dict[str, Todo] = {}
        self._todos_by_key: dict[str, str] = {}  # idempotency_key -> todo_id
        self._lock = threading.Lock()

    # ---------- Customer ----------

    def save_customer(self, customer: Customer) -> None:
        with self._lock:
            self._customers[customer.id] = customer

    def find_customer_by_id(self, customer_id: str) -> Customer:
        with self._lock:
            customer = self._customers.get(customer_id)
        if customer is None:
            raise CustomerNotFoundError(customer_id)
        return customer

    def search_customers(self, query: str) -> list[Customer]:
        q = query.lower()
        with self._lock:
            return [
                c for c in self._customers.values()
                if q in c.name.lower() or c.name.lower() in q
            ]

    def customer_exists_by_name(self, name: str) -> Customer | None:
        target = name.strip().lower()
        with self._lock:
            for c in self._customers.values():
                if c.name.strip().lower() == target:
                    return c
        return None

    # ---------- Contact ----------

    def save_contact(self, contact: Contact) -> None:
        with self._lock:
            self._contacts[contact.id] = contact

    def find_contact_by_id(self, contact_id: str) -> Contact:
        with self._lock:
            contact = self._contacts.get(contact_id)
        if contact is None:
            raise ContactNotFoundError(contact_id)
        return contact

    def search_contacts(self, query: str, customer_id: str | None = None) -> list[Contact]:
        q = query.lower()
        with self._lock:
            contacts = list(self._contacts.values())
            if customer_id is not None:
                contacts = [c for c in contacts if c.customer_id == customer_id]
            return [c for c in contacts if q in c.name.lower()]

    # ---------- FollowUp ----------

    def save_follow_up(self, follow_up: FollowUp) -> None:
        with self._lock:
            self._follow_ups[follow_up.id] = follow_up

    def find_follow_up_by_id(self, follow_up_id: str) -> FollowUp:
        with self._lock:
            fu = self._follow_ups.get(follow_up_id)
        if fu is None:
            raise FollowUpNotFoundError(follow_up_id)
        return fu

    def search_follow_ups(
        self,
        customer_id: str | None = None,
        contact_id: str | None = None,
        status: str | None = None,
    ) -> list[FollowUp]:
        with self._lock:
            fus = list(self._follow_ups.values())
            if customer_id is not None:
                fus = [f for f in fus if f.customer_id == customer_id]
            if contact_id is not None:
                fus = [f for f in fus if f.contact_id == contact_id]
            if status is not None:
                fus = [f for f in fus if f.status.value == status]
            # 按创建时间倒序
            return sorted(fus, key=lambda f: f.created_at, reverse=True)

    # ---------- Todo ----------

    def save_todo(self, todo: Todo) -> None:
        with self._lock:
            self._todos[todo.id] = todo
            if todo.idempotency_key:
                self._todos_by_key[todo.idempotency_key] = todo.id

    def find_todo_by_id(self, todo_id: str) -> Todo:
        with self._lock:
            todo = self._todos.get(todo_id)
        if todo is None:
            raise TodoNotFoundError(todo_id)
        return todo

    def list_todos(
        self,
        status: str | None = None,
        customer_id: str | None = None,
    ) -> list[Todo]:
        with self._lock:
            todos = list(self._todos.values())
            if status is not None:
                todos = [t for t in todos if t.status.value == status]
            if customer_id is not None:
                todos = [t for t in todos if t.customer_id == customer_id]
            # 按截止时间升序（无截止放最后）
            return sorted(
                todos,
                key=lambda t: (t.due_at is None, t.due_at or t.created_at),
            )

    def find_todo_by_idempotency_key(self, key: str) -> Todo | None:
        with self._lock:
            todo_id = self._todos_by_key.get(key)
            if todo_id is None:
                return None
            return self._todos.get(todo_id)


def create_crm_store(backend: str = "memory", db_path: str = "") -> CRMStore:
    """工厂函数：根据配置返回 CRMStore 实现。"""
    if backend == "sqlite":
        if not db_path:
            raise ValueError("db_path is required when backend='sqlite'")
        from .sqlite_store import create_sqlite_stores
        stores = create_sqlite_stores(db_path)
        return stores.crm
    return InMemoryCRMStore()
