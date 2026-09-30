"""MCP 工具实现与 handler 构造（v0.2.0）。

工具总览：

| 工具 | 类别 | 说明 |
|---|---|---|
| recognize_ticket | Ticket | 从对话识别并创建工单 |
| get_ticket | Ticket | 按 ID 查工单 |
| search_customer | CRM | 按公司名搜客户 |
| get_customer | CRM | 按 ID 查客户 |
| create_customer | CRM | 创建客户档案 |
| search_contact | CRM | 按姓名搜联系人 |
| get_contact | CRM | 按 ID 查联系人 |
| create_contact | CRM | 创建联系人 |
| create_follow_up | CRM | 创建跟进记录 |
| search_follow_up | CRM | 按客户/联系人查跟进 |
| create_todo | CRM | 创建待办 |
| list_todos | CRM | 列出待办 |
| update_todo_status | CRM | 更新待办状态 |

mcp 2.2 协议：handler 通过 Server 构造函数的 on_list_tools / on_call_tool 注入。
"""
import logging
import time
from dataclasses import dataclass
from typing import Any

import mcp_types as types

from .exceptions import (
    ContactNotFoundError,
    CustomerNotFoundError,
    TicketMCPError,
)
from .models import (
    Contact,
    CreateContactRequest,
    CreateCustomerRequest,
    CreateFollowUpRequest,
    CreateTodoRequest,
    Customer,
    FollowUp,
    GetContactRequest,
    GetCustomerRequest,
    GetTicketRequest,
    RecognizeTicketRequest,
    SearchContactRequest,
    SearchCustomerRequest,
    SearchFollowUpRequest,
    TicketRecord,
    Todo,
    TodoStatus,
    ToolError,
    UpdateTodoStatusRequest,
)
from .storage import CRMStore, TicketStore

logger = logging.getLogger(__name__)


@dataclass
class Services:
    """handler 依赖注入容器。

    为什么不直接传两个 store 参数：
    - handler 签名稳定（只接 Services），新增 store 不破坏调用方
    - 测试可整体替换 Services
    - 类型提示更清晰
    """
    ticket_store: TicketStore
    crm_store: CRMStore


# ID 生成
#
# 为什么用 时间戳+序号 而不是 纯时间戳：
# 纯毫秒时间戳在高频调用时会撞（同 1ms 内两个调用 ID 一样），
# INSERT OR REPLACE 会把数据覆盖掉。
# 用 itertools.count() 加进程内原子计数器，1ms 内的多次调用保证唯一。
# 进程重启后计数器重置，但时间戳会变，仍能避免冲突。
import itertools as _itertools

_id_counters: dict[str, _itertools.count] = {
    "TKT": _itertools.count(),
    "CUS": _itertools.count(),
    "CON": _itertools.count(),
    "FUP": _itertools.count(),
    "TODO": _itertools.count(),
}


def _make_id(prefix: str) -> str:
    """生成唯一 ID：{prefix}-{timestamp_ms}-{seq:04d}。"""
    ts = int(time.time() * 1000)
    seq = next(_id_counters[prefix])
    return f"{prefix}-{ts}-{seq:04d}"


def _make_ticket_id() -> str:
    return _make_id("TKT")


def _make_customer_id() -> str:
    return _make_id("CUS")


def _make_contact_id() -> str:
    return _make_id("CON")


def _make_follow_up_id() -> str:
    return _make_id("FUP")


def _make_todo_id() -> str:
    return _make_id("TODO")


# 通用错误处理
def _err(code: str, msg: str) -> str:
    return ToolError(error_code=code, message=msg).to_text()


# ============================================================
# Ticket handlers
# ============================================================

async def _handle_recognize_ticket(arguments: dict, services: Services) -> str:
    try:
        req = RecognizeTicketRequest(**arguments)
    except Exception as e:
        return _err("VALIDATION_ERROR", str(e))

    existing = services.ticket_store.find_by_idempotency_key(req.idempotency_key)
    if existing is not None:
        if (existing.customer_name != req.customer_name
            or existing.title != req.title
            or existing.description != req.description
            or existing.priority != req.priority):
            logger.warning("idempotency_conflict", extra={"key": req.idempotency_key})
            return _err("IDEMPOTENCY_CONFLICT", f"幂等键 {req.idempotency_key} 已被使用且参数不一致")
        logger.info("idempotent_replay", extra={"ticket_id": existing.ticket_id, "key": req.idempotency_key})
        return f"（幂等命中，已返回原工单）\n{existing.to_summary()}"

    ticket = TicketRecord(
        ticket_id=_make_ticket_id(),
        customer_name=req.customer_name,
        title=req.title,
        description=req.description,
        priority=req.priority,
        source_message_id=req.source_message_id,
        source_channel=req.source_channel,
        idempotency_key=req.idempotency_key,
    )

    try:
        services.ticket_store.save(ticket)
    except TicketMCPError as e:
        logger.exception("save_failed", extra={"ticket_id": ticket.ticket_id})
        return _err(e.error_code, str(e))
    except Exception as e:
        logger.exception("save_failed_unexpected")
        return _err("STORAGE_ERROR", f"保存工单失败: {e}")

    logger.info("ticket_created", extra={"ticket_id": ticket.ticket_id})
    return ticket.to_summary()


async def _handle_get_ticket(arguments: dict, services: Services) -> str:
    try:
        req = GetTicketRequest(**arguments)
    except Exception as e:
        return _err("VALIDATION_ERROR", str(e))

    try:
        ticket = services.ticket_store.find_by_id(req.ticket_id)
    except TicketMCPError as e:
        return _err(e.error_code, str(e))
    return ticket.to_summary()


# ============================================================
# Customer handlers
# ============================================================

async def _handle_search_customer(arguments: dict, services: Services) -> str:
    try:
        req = SearchCustomerRequest(**arguments)
    except Exception as e:
        return _err("VALIDATION_ERROR", str(e))

    results = services.crm_store.search_customers(req.query)
    if not results:
        return f"未找到匹配 '{req.query}' 的客户。"
    lines = [f"匹配 '{req.query}' 的客户（{len(results)} 条）："]
    for c in results:
        lines.append(f"  - {c.id}: {c.name} ({c.status.value})")
    return "\n".join(lines)


async def _handle_get_customer(arguments: dict, services: Services) -> str:
    try:
        req = GetCustomerRequest(**arguments)
    except Exception as e:
        return _err("VALIDATION_ERROR", str(e))

    try:
        customer = services.crm_store.find_customer_by_id(req.customer_id)
    except CustomerNotFoundError as e:
        return _err(e.error_code, str(e))
    return customer.to_summary()


async def _handle_create_customer(arguments: dict, services: Services) -> str:
    """处理 create_customer 调用。

    业务规则：
    - 同名客户已存在且参数一致 → 返回原 ID（幂等）
    - 同名客户已存在但参数不一致 → 报错 IDEMPOTENCY_CONFLICT
    - 不存在 → 创建新客户
    """
    try:
        req = CreateCustomerRequest(**arguments)
    except Exception as e:
        return _err("VALIDATION_ERROR", str(e))

    existing = services.crm_store.customer_exists_by_name(req.name)
    if existing is not None:
        if existing.industry != req.industry or existing.notes != req.notes:
            return _err(
                "IDEMPOTENCY_CONFLICT",
                f"同名客户 '{req.name}' 已存在（{existing.id}），但参数不一致",
            )
        logger.info("customer_replay", extra={"customer_id": existing.id})
        return f"（幂等命中，已返回原客户）\n{existing.to_summary()}"

    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    customer = Customer(
        id=_make_customer_id(),
        name=req.name,
        industry=req.industry,
        notes=req.notes,
        created_at=now,
        updated_at=now,
    )

    try:
        services.crm_store.save_customer(customer)
    except TicketMCPError as e:
        return _err(e.error_code, str(e))
    except Exception as e:
        return _err("STORAGE_ERROR", f"保存客户失败: {e}")

    logger.info("customer_created", extra={"customer_id": customer.id})
    return customer.to_summary()


# ============================================================
# Contact handlers
# ============================================================

async def _handle_search_contact(arguments: dict, services: Services) -> str:
    try:
        req = SearchContactRequest(**arguments)
    except Exception as e:
        return _err("VALIDATION_ERROR", str(e))

    results = services.crm_store.search_contacts(req.query, customer_id=req.customer_id)
    if not results:
        scope = f"客户 {req.customer_id} 下" if req.customer_id else "全局"
        return f"未在 {scope} 找到匹配 '{req.query}' 的联系人。"
    lines = [f"匹配 '{req.query}' 的联系人（{len(results)} 条）："]
    for c in results:
        lines.append(
            f"  - {c.id}: {c.name} | 客户={c.customer_id} | {c.role or '未填'} | {c.phone or '无电话'}"
        )
    return "\n".join(lines)


async def _handle_get_contact(arguments: dict, services: Services) -> str:
    try:
        req = GetContactRequest(**arguments)
    except Exception as e:
        return _err("VALIDATION_ERROR", str(e))

    try:
        contact = services.crm_store.find_contact_by_id(req.contact_id)
    except ContactNotFoundError as e:
        return _err(e.error_code, str(e))
    return contact.to_summary()


async def _handle_create_contact(arguments: dict, services: Services) -> str:
    try:
        req = CreateContactRequest(**arguments)
    except Exception as e:
        return _err("VALIDATION_ERROR", str(e))

    # 校验客户存在
    try:
        services.crm_store.find_customer_by_id(req.customer_id)
    except CustomerNotFoundError as e:
        return _err(e.error_code, f"客户 {req.customer_id} 不存在，无法创建联系人")

    # 同客户下同姓名 → 幂等
    same_name = services.crm_store.search_contacts(req.name, customer_id=req.customer_id)
    if same_name:
        existing = same_name[0]
        return f"（幂等命中，同客户已存在同名联系人）\n{existing.to_summary()}"

    contact = Contact(
        id=_make_contact_id(),
        customer_id=req.customer_id,
        name=req.name,
        phone=req.phone,
        email=req.email,
        role=req.role,
        is_primary=req.is_primary,
    )

    try:
        services.crm_store.save_contact(contact)
    except TicketMCPError as e:
        return _err(e.error_code, str(e))
    except Exception as e:
        return _err("STORAGE_ERROR", f"保存联系人失败: {e}")

    logger.info("contact_created", extra={"contact_id": contact.id, "customer_id": req.customer_id})
    return contact.to_summary()


# ============================================================
# FollowUp handlers
# ============================================================

async def _handle_create_follow_up(arguments: dict, services: Services) -> str:
    try:
        req = CreateFollowUpRequest(**arguments)
    except Exception as e:
        return _err("VALIDATION_ERROR", str(e))

    try:
        services.crm_store.find_customer_by_id(req.customer_id)
    except CustomerNotFoundError as e:
        return _err(e.error_code, f"客户 {req.customer_id} 不存在")

    if req.contact_id:
        try:
            services.crm_store.find_contact_by_id(req.contact_id)
        except ContactNotFoundError as e:
            return _err(e.error_code, f"联系人 {req.contact_id} 不存在")

    fu = FollowUp(
        id=_make_follow_up_id(),
        customer_id=req.customer_id,
        contact_id=req.contact_id,
        content=req.content,
        follow_up_at=req.follow_up_at,
    )

    try:
        services.crm_store.save_follow_up(fu)
    except TicketMCPError as e:
        return _err(e.error_code, str(e))
    except Exception as e:
        return _err("STORAGE_ERROR", f"保存跟进失败: {e}")

    logger.info("follow_up_created", extra={"follow_up_id": fu.id})
    return fu.to_summary()


async def _handle_search_follow_up(arguments: dict, services: Services) -> str:
    try:
        req = SearchFollowUpRequest(**arguments)
    except Exception as e:
        return _err("VALIDATION_ERROR", str(e))

    results = services.crm_store.search_follow_ups(
        customer_id=req.customer_id,
        contact_id=req.contact_id,
        status=req.status.value if req.status else None,
    )
    if not results:
        return "未找到匹配的跟进记录。"
    lines = [f"找到 {len(results)} 条跟进："]
    for f in results:
        lines.append(f"  - {f.to_summary()}")
    return "\n".join(lines)


# ============================================================
# Todo handlers
# ============================================================

async def _handle_create_todo(arguments: dict, services: Services) -> str:
    try:
        req = CreateTodoRequest(**arguments)
    except Exception as e:
        return _err("VALIDATION_ERROR", str(e))

    if req.idempotency_key:
        existing = services.crm_store.find_todo_by_idempotency_key(req.idempotency_key)
        if existing is not None:
            logger.info("todo_replay", extra={"todo_id": existing.id})
            return f"（幂等命中，已返回原待办）\n{existing.to_summary()}"

    # 校验关联
    if req.customer_id:
        try:
            services.crm_store.find_customer_by_id(req.customer_id)
        except CustomerNotFoundError as e:
            return _err(e.error_code, f"客户 {req.customer_id} 不存在")
    if req.contact_id:
        try:
            services.crm_store.find_contact_by_id(req.contact_id)
        except ContactNotFoundError as e:
            return _err(e.error_code, f"联系人 {req.contact_id} 不存在")

    todo = Todo(
        id=_make_todo_id(),
        title=req.title,
        description=req.description,
        due_at=req.due_at,
        customer_id=req.customer_id,
        contact_id=req.contact_id,
        follow_up_id=req.follow_up_id,
        idempotency_key=req.idempotency_key,
    )

    try:
        services.crm_store.save_todo(todo)
    except TicketMCPError as e:
        return _err(e.error_code, str(e))
    except Exception as e:
        return _err("STORAGE_ERROR", f"保存待办失败: {e}")

    logger.info("todo_created", extra={"todo_id": todo.id})
    return todo.to_summary()


async def _handle_list_todos(arguments: dict, services: Services) -> str:
    status = arguments.get("status")
    customer_id = arguments.get("customer_id")

    todos = services.crm_store.list_todos(status=status, customer_id=customer_id)
    if not todos:
        return "暂无待办。"
    lines = [f"待办 {len(todos)} 条："]
    for t in todos:
        lines.append(f"  - {t.to_summary()}")
    return "\n".join(lines)


async def _handle_update_todo_status(arguments: dict, services: Services) -> str:
    try:
        req = UpdateTodoStatusRequest(**arguments)
    except Exception as e:
        return _err("VALIDATION_ERROR", str(e))

    try:
        todo = services.crm_store.find_todo_by_id(req.todo_id)
    except TicketMCPError as e:
        return _err(e.error_code, str(e))

    todo.status = req.status
    services.crm_store.save_todo(todo)

    logger.info("todo_status_updated", extra={"todo_id": todo.id, "status": req.status.value})
    return f"已更新：\n{todo.to_summary()}"


# ============================================================
# 工具定义 + handler 映射 + factory
# ============================================================


_TOOL_DEFINITIONS: list[types.Tool] = [
    # Ticket
    types.Tool(
        name="recognize_ticket",
        description="从对话内容中识别并创建工单。",
        inputSchema={
            "type": "object",
            "required": ["title", "description", "idempotency_key"],
            "properties": {
                "customer_name": {"type": "string"},
                "title": {"type": "string"},
                "description": {"type": "string"},
                "priority": {"type": "string", "enum": ["P0", "P1", "P2", "P3"], "default": "P2"},
                "source_message_id": {"type": "string"},
                "source_channel": {"type": "string", "default": "openclaw"},
                "idempotency_key": {"type": "string"},
            },
        },
    ),
    types.Tool(
        name="get_ticket",
        description="按工单 ID 查询工单。",
        inputSchema={
            "type": "object",
            "required": ["ticket_id"],
            "properties": {"ticket_id": {"type": "string", "pattern": r"^TKT-\d+$"}},
        },
    ),
    # Customer
    types.Tool(
        name="search_customer",
        description="按公司名模糊搜索客户。create_customer 前先调这个做去重检查。",
        inputSchema={
            "type": "object",
            "required": ["query"],
            "properties": {"query": {"type": "string", "description": "公司名（模糊匹配）"}},
        },
    ),
    types.Tool(
        name="get_customer",
        description="按客户 ID 查询详情。",
        inputSchema={
            "type": "object",
            "required": ["customer_id"],
            "properties": {"customer_id": {"type": "string", "pattern": r"^CUS-\d+$"}},
        },
    ),
    types.Tool(
        name="create_customer",
        description="创建客户档案。同名客户存在则幂等返回。",
        inputSchema={
            "type": "object",
            "required": ["name"],
            "properties": {
                "name": {"type": "string"},
                "industry": {"type": "string"},
                "notes": {"type": "string"},
                "idempotency_key": {"type": "string"},
            },
        },
    ),
    # Contact
    types.Tool(
        name="search_contact",
        description="按姓名搜联系人，可限定客户。",
        inputSchema={
            "type": "object",
            "required": ["query"],
            "properties": {
                "query": {"type": "string"},
                "customer_id": {"type": "string", "description": "限定客户范围"},
            },
        },
    ),
    types.Tool(
        name="get_contact",
        description="按联系人 ID 查询详情。",
        inputSchema={
            "type": "object",
            "required": ["contact_id"],
            "properties": {"contact_id": {"type": "string", "pattern": r"^CON-\d+$"}},
        },
    ),
    types.Tool(
        name="create_contact",
        description="创建联系人。customer_id 必须已存在。",
        inputSchema={
            "type": "object",
            "required": ["customer_id", "name"],
            "properties": {
                "customer_id": {"type": "string"},
                "name": {"type": "string"},
                "phone": {"type": "string"},
                "email": {"type": "string"},
                "role": {"type": "string"},
                "is_primary": {"type": "boolean", "default": False},
                "idempotency_key": {"type": "string"},
            },
        },
    ),
    # FollowUp
    types.Tool(
        name="create_follow_up",
        description="创建跟进记录（一次拜访/沟通的事件纪要）。",
        inputSchema={
            "type": "object",
            "required": ["customer_id", "content"],
            "properties": {
                "customer_id": {"type": "string"},
                "contact_id": {"type": "string"},
                "content": {"type": "string"},
                "follow_up_at": {"type": "string", "format": "date-time"},
                "idempotency_key": {"type": "string"},
            },
        },
    ),
    types.Tool(
        name="search_follow_up",
        description="按客户/联系人/状态查跟进。",
        inputSchema={
            "type": "object",
            "properties": {
                "customer_id": {"type": "string"},
                "contact_id": {"type": "string"},
                "status": {"type": "string", "enum": ["pending", "completed", "cancelled"]},
            },
        },
    ),
    # Todo
    types.Tool(
        name="create_todo",
        description="创建待办（承诺类动作，如'下周四前发报价'）。可关联客户/联系人/跟进。",
        inputSchema={
            "type": "object",
            "required": ["title"],
            "properties": {
                "title": {"type": "string"},
                "description": {"type": "string"},
                "due_at": {"type": "string", "format": "date-time"},
                "customer_id": {"type": "string"},
                "contact_id": {"type": "string"},
                "follow_up_id": {"type": "string"},
                "idempotency_key": {"type": "string"},
            },
        },
    ),
    types.Tool(
        name="list_todos",
        description="列待办，可按状态/客户筛选。",
        inputSchema={
            "type": "object",
            "properties": {
                "status": {"type": "string", "enum": ["open", "done", "cancelled"]},
                "customer_id": {"type": "string"},
            },
        },
    ),
    types.Tool(
        name="update_todo_status",
        description="更新待办状态。",
        inputSchema={
            "type": "object",
            "required": ["todo_id", "status"],
            "properties": {
                "todo_id": {"type": "string", "pattern": r"^TODO-\d+$"},
                "status": {"type": "string", "enum": ["open", "done", "cancelled"]},
            },
        },
    ),
]


_TOOL_HANDLERS = {
    "recognize_ticket": _handle_recognize_ticket,
    "get_ticket": _handle_get_ticket,
    "search_customer": _handle_search_customer,
    "get_customer": _handle_get_customer,
    "create_customer": _handle_create_customer,
    "search_contact": _handle_search_contact,
    "get_contact": _handle_get_contact,
    "create_contact": _handle_create_contact,
    "create_follow_up": _handle_create_follow_up,
    "search_follow_up": _handle_search_follow_up,
    "create_todo": _handle_create_todo,
    "list_todos": _handle_list_todos,
    "update_todo_status": _handle_update_todo_status,
}


def build_tool_server_kwargs(services: Services) -> dict[str, Any]:
    """构造 mcp 2.2 Server 的 on_* 关键字参数。

    Returns:
        dict 包含 on_list_tools / on_call_tool handler
    """

    async def on_list_tools(ctx: Any, params: Any) -> types.ListToolsResult:
        logger.info("list_tools_called", extra={"count": len(_TOOL_DEFINITIONS)})
        return types.ListToolsResult(tools=list(_TOOL_DEFINITIONS))

    async def on_call_tool(
        ctx: Any,
        params: types.CallToolRequestParams,
    ) -> types.CallToolResult:
        name = params.name
        arguments = dict(params.arguments or {})

        logger.info(
            "tool_call",
            extra={"tool_name": name, "arguments_keys": list(arguments.keys())},
        )

        handler_fn = _TOOL_HANDLERS.get(name)
        if handler_fn is None:
            err = ToolError(error_code="UNKNOWN_TOOL", message=f"未知工具: {name}")
            return types.CallToolResult(
                content=[types.TextContent(type="text", text=err.to_text())],
                isError=True,
            )

        result_text = await handler_fn(arguments, services)
        is_error = result_text.startswith("[") and (
            "VALIDATION_ERROR" in result_text
            or "STORAGE_ERROR" in result_text
            or "NOT_FOUND" in result_text
            or "CONFLICT" in result_text
        )
        return types.CallToolResult(
            content=[types.TextContent(type="text", text=result_text)],
            isError=is_error,
        )

    return {
        "on_list_tools": on_list_tools,
        "on_call_tool": on_call_tool,
    }
