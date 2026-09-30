"""数据模型。

设计原则：
- 所有对外契约（API 入参/出参）都用 pydantic 定义
- 字段带 description，会自动生成 JSON Schema 给 MCP 客户端
- 字段校验放在模型层，工具层只关心业务逻辑
- 内部数据也用 BaseModel 而非 dict，方便类型提示和测试

为什么用 BaseModel 而非 dataclass：
- pydantic 自带 JSON 序列化/反序列化
- pydantic-settings 风格统一
- 校验失败信息更友好
"""
from datetime import datetime, timezone
from enum import Enum

from pydantic import BaseModel, Field, field_validator


class Priority(str, Enum):
    """工单紧急程度。"""

    P0 = "P0"  # 紧急 / 线上故障
    P1 = "P1"  # 高
    P2 = "P2"  # 中（默认）
    P3 = "P3"  # 低


class TicketStatus(str, Enum):
    """工单状态。"""

    NEW = "new"
    CONFIRMED = "confirmed"
    IN_PROGRESS = "in_progress"
    CLOSED = "closed"


class RecognizeTicketRequest(BaseModel):
    """recognize_ticket 工具的入参。

    与 OpenClaw 侧的 Skill 调用契约保持一致。
    """

    customer_name: str | None = Field(
        default=None,
        max_length=100,
        description="客户名称或代号，用于关联 CRM 客户档案",
    )
    title: str = Field(
        ...,
        min_length=1,
        max_length=200,
        description="一句话工单标题",
    )
    description: str = Field(
        ...,
        min_length=1,
        description="工单详细描述",
    )
    priority: Priority = Field(
        default=Priority.P2,
        description="紧急程度：P0/P1/P2/P3",
    )
    source_message_id: str | None = Field(
        default=None,
        description="来源消息 ID，用于溯源",
    )
    source_channel: str = Field(
        default="openclaw",
        description="来源渠道标识",
    )
    idempotency_key: str = Field(
        ...,
        min_length=1,
        max_length=200,
        description="幂等键，基于 source_message_id 派生",
    )

    @field_validator("idempotency_key")
    @classmethod
    def _validate_idempotency_key(cls, v: str) -> str:
        """校验幂等键只包含安全字符。"""
        if any(c in v for c in ("\n", "\r", "\t", " ")):
            raise ValueError("idempotency_key cannot contain whitespace")
        return v


class TicketRecord(BaseModel):
    """工单记录（内部数据模型 + 对外返回结构）。

    内部存储和外部返回使用同一模型。
    后续如需区分（如对外隐藏某些内部字段），可以拆分。
    """

    ticket_id: str = Field(..., description="工单 ID，格式 TKT-{timestamp_ms}")
    customer_name: str | None = None
    title: str
    description: str
    priority: Priority
    status: TicketStatus = Field(default=TicketStatus.NEW)
    source_message_id: str | None = None
    source_channel: str = "openclaw"
    idempotency_key: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    def to_summary(self) -> str:
        """生成给 LLM 看的简短摘要（用于 MCP 工具返回值）。"""
        cust = self.customer_name or "(未指定)"
        return (
            f"工单 {self.ticket_id} 已创建\n"
            f"  客户：{cust}\n"
            f"  标题：{self.title}\n"
            f"  紧急：{self.priority.value}\n"
            f"  状态：{self.status.value}"
        )


class GetTicketRequest(BaseModel):
    """get_ticket 工具的入参。"""

    ticket_id: str = Field(
        ...,
        pattern=r"^TKT-\d+-\d+$",
        description="工单 ID，格式 TKT-{timestamp_ms}",
    )


class ToolError(BaseModel):
    """工具调用错误时的统一返回结构。

    为什么不抛异常给 MCP 客户端：
    - MCP 协议允许 tool 返回文本内容，错误也是文本
    - 让工具"软失败"而不是协议错误，便于上层（如 Agent）重试或修正
    - 真正的协议级错误（如 schema 校验）由 pydantic / mcp SDK 处理
    """

    error_code: str
    message: str

    def to_text(self) -> str:
        """生成 MCP 返回的文本内容。"""
        return f"错误 [{self.error_code}]：{self.message}"


class CustomerStatus(str, Enum):
    """客户状态。"""

    LEAD = "lead"           # 线索
    QUALIFIED = "qualified" # 已确认
    ACTIVE = "active"       # 活跃客户
    CHURNED = "churned"     # 流失


class ContactRole(str, Enum):
    """联系人角色（典型业务角色）。"""

    PURCHASING = "purchasing"      # 采购
    TECHNICAL = "technical"        # 技术
    DECISION_MAKER = "decision"    # 决策人
    USER = "user"                  # 使用方
    OTHER = "other"


class FollowUpStatus(str, Enum):
    """跟进状态。"""

    PENDING = "pending"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class TodoStatus(str, Enum):
    """待办状态。"""

    OPEN = "open"
    DONE = "done"
    CANCELLED = "cancelled"


class Customer(BaseModel):
    """客户档案（公司）。

    字段说明：
    - id: 格式 CUS-{13位时间戳毫秒}
    - name: 公司名称（必填，唯一性在 store 层做约束；这里只做长度校验）
    - industry: 行业（可选）
    - status: 默认 LEAD
    """

    id: str = Field(..., description="客户 ID，格式 CUS-{timestamp_ms}")
    name: str = Field(..., min_length=1, max_length=200, description="公司名称")
    industry: str | None = Field(default=None, max_length=100, description="行业")
    status: CustomerStatus = Field(default=CustomerStatus.LEAD)
    notes: str | None = Field(default=None, description="备注")
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    def to_summary(self) -> str:
        return (
            f"客户 {self.id}\n"
            f"  公司：{self.name}\n"
            f"  状态：{self.status.value}\n"
            f"  行业：{self.industry or '(未填)'}\n"
            f"  备注：{self.notes or '(无)'}"
        )


class Contact(BaseModel):
    """联系人档案（个人）。

    一个客户可以有多个联系人；is_primary=true 的为主联系人。
    """

    id: str = Field(..., description="联系人 ID，格式 CON-{timestamp_ms}")
    customer_id: str = Field(..., description="关联客户 ID")
    name: str = Field(..., min_length=1, max_length=100)
    phone: str | None = Field(default=None, max_length=20)
    email: str | None = Field(default=None, max_length=200)
    role: str | None = Field(default=None, max_length=50, description="职位/角色")
    is_primary: bool = Field(default=False, description="是否主联系人")
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    def to_summary(self) -> str:
        return (
            f"联系人 {self.id}\n"
            f"  姓名：{self.name}\n"
            f"  客户：{self.customer_id}\n"
            f"  角色：{self.role or '(未填)'}\n"
            f"  电话：{self.phone or '(未填)'}\n"
            f"  邮件：{self.email or '(未填)'}\n"
            f"  主联系人：{'是' if self.is_primary else '否'}"
        )


class FollowUp(BaseModel):
    """跟进记录。

    一次拜访/沟通的事件记录，可以关联到客户和联系人。
    """

    id: str = Field(..., description="跟进 ID，格式 FUP-{timestamp_ms}")
    customer_id: str = Field(..., description="关联客户 ID")
    contact_id: str | None = Field(default=None, description="关联联系人 ID")
    content: str = Field(..., min_length=1, description="沟通内容纪要")
    follow_up_at: datetime | None = Field(default=None, description="下次跟进时间")
    status: FollowUpStatus = Field(default=FollowUpStatus.PENDING)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    def to_summary(self) -> str:
        return (
            f"跟进 {self.id}\n"
            f"  客户：{self.customer_id}\n"
            f"  联系人：{self.contact_id or '(无)'}\n"
            f"  状态：{self.status.value}\n"
            f"  下次跟进：{self.follow_up_at.isoformat() if self.follow_up_at else '(未排)'}\n"
            f"  内容：{self.content[:200]}{'...' if len(self.content) > 200 else ''}"
        )


class Todo(BaseModel):
    """待办。

    可关联到客户/联系人/跟进；用于"下周四前发报价"这类承诺类动作。
    """

    id: str = Field(..., description="待办 ID，格式 TODO-{timestamp_ms}")
    title: str = Field(..., min_length=1, max_length=200)
    description: str | None = Field(default=None, description="详细说明")
    due_at: datetime | None = Field(default=None, description="截止时间")
    status: TodoStatus = Field(default=TodoStatus.OPEN)
    customer_id: str | None = Field(default=None, description="关联客户 ID")
    contact_id: str | None = Field(default=None, description="关联联系人 ID")
    follow_up_id: str | None = Field(default=None, description="关联跟进 ID")
    idempotency_key: str | None = Field(default=None, description="幂等键")
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    def to_summary(self) -> str:
        return (
            f"待办 {self.id}\n"
            f"  标题：{self.title}\n"
            f"  截止：{self.due_at.isoformat() if self.due_at else '(未设)'}\n"
            f"  状态：{self.status.value}\n"
            f"  关联：客户={self.customer_id or '无'} 联系人={self.contact_id or '无'} 跟进={self.follow_up_id or '无'}"
        )


# ---------- 工具入参模型 ----------


class CreateCustomerRequest(BaseModel):
    """create_customer 工具入参。"""

    name: str = Field(..., min_length=1, max_length=200, description="公司名称")
    industry: str | None = Field(default=None, max_length=100)
    notes: str | None = None
    idempotency_key: str | None = Field(default=None, max_length=200)


class SearchCustomerRequest(BaseModel):
    """search_customer 工具入参。"""

    query: str = Field(..., min_length=1, max_length=200, description="公司名（模糊匹配）")


class GetCustomerRequest(BaseModel):
    """get_customer 工具入参。"""

    customer_id: str = Field(..., pattern=r"^CUS-\d+-\d+$")


class CreateContactRequest(BaseModel):
    """create_contact 工具入参。"""

    customer_id: str = Field(..., min_length=1)
    name: str = Field(..., min_length=1, max_length=100)
    phone: str | None = Field(default=None, max_length=20)
    email: str | None = Field(default=None, max_length=200)
    role: str | None = Field(default=None, max_length=50)
    is_primary: bool = False
    idempotency_key: str | None = Field(default=None, max_length=200)


class SearchContactRequest(BaseModel):
    """search_contact 工具入参。"""

    query: str = Field(..., min_length=1, max_length=100, description="姓名（模糊匹配）")
    customer_id: str | None = Field(default=None, description="限定客户范围")


class GetContactRequest(BaseModel):
    """get_contact 工具入参。"""

    contact_id: str = Field(..., pattern=r"^CON-\d+-\d+$")


class CreateFollowUpRequest(BaseModel):
    """create_follow_up 工具入参。"""

    customer_id: str = Field(..., min_length=1)
    contact_id: str | None = None
    content: str = Field(..., min_length=1, description="沟通纪要")
    follow_up_at: datetime | None = Field(default=None, description="下次跟进时间")
    idempotency_key: str | None = Field(default=None, max_length=200)


class SearchFollowUpRequest(BaseModel):
    """search_follow_up 工具入参。"""

    customer_id: str | None = Field(default=None)
    contact_id: str | None = Field(default=None)
    status: FollowUpStatus | None = None


class CreateTodoRequest(BaseModel):
    """create_todo 工具入参。"""

    title: str = Field(..., min_length=1, max_length=200)
    description: str | None = None
    due_at: datetime | None = Field(default=None, description="截止时间")
    customer_id: str | None = None
    contact_id: str | None = None
    follow_up_id: str | None = None
    idempotency_key: str | None = Field(default=None, max_length=200)


class UpdateTodoStatusRequest(BaseModel):
    """update_todo_status 工具入参。"""

    todo_id: str = Field(..., pattern=r"^TODO-\d+-\d+$")
    status: TodoStatus
