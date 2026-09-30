"""业务异常体系。

设计原则：
- 所有业务异常继承自 TicketMCPError
- 提供 error_code 便于上层做国际化或客户端侧错误展示
- 异常信息应该对人可读，且不泄露实现细节

为什么单独定义：
- 内置异常（ValueError 等）信息含糊，不利于客户端展示
- 集中异常便于在 MCP error handler 中做统一映射
- 异常体系稳定后，前端/客户端可基于 error_code 做提示
"""


class TicketMCPError(Exception):
    """所有业务异常的基类。

    Attributes:
        error_code: 稳定的错误码，便于客户端处理
        message: 人可读的错误信息
    """

    def __init__(self, message: str, error_code: str = "INTERNAL_ERROR") -> None:
        super().__init__(message)
        self.error_code = error_code
        self.message = message

    def __str__(self) -> str:
        return f"[{self.error_code}] {self.message}"


class ValidationError(TicketMCPError):
    """输入参数校验失败。"""

    def __init__(self, message: str) -> None:
        super().__init__(message, error_code="VALIDATION_ERROR")


class TicketNotFoundError(TicketMCPError):
    """工单不存在。"""

    def __init__(self, ticket_id: str) -> None:
        super().__init__(
            f"Ticket not found: {ticket_id}",
            error_code="TICKET_NOT_FOUND",
        )
        self.ticket_id = ticket_id


class IdempotencyConflictError(TicketMCPError):
    """幂等键冲突：同一消息被重复提交但参数不一致。

    这通常意味着调用方逻辑有问题，需要排查。
    """

    def __init__(self, idempotency_key: str) -> None:
        super().__init__(
            f"Idempotency key already used with different parameters: {idempotency_key}",
            error_code="IDEMPOTENCY_CONFLICT",
        )
        self.idempotency_key = idempotency_key


class StorageError(TicketMCPError):
    """存储层错误。"""

    def __init__(self, message: str) -> None:
        super().__init__(message, error_code="STORAGE_ERROR")


class CustomerNotFoundError(TicketMCPError):
    """客户不存在。"""

    def __init__(self, customer_id: str) -> None:
        super().__init__(
            f"Customer not found: {customer_id}",
            error_code="CUSTOMER_NOT_FOUND",
        )
        self.customer_id = customer_id


class ContactNotFoundError(TicketMCPError):
    """联系人不存在。"""

    def __init__(self, contact_id: str) -> None:
        super().__init__(
            f"Contact not found: {contact_id}",
            error_code="CONTACT_NOT_FOUND",
        )
        self.contact_id = contact_id


class FollowUpNotFoundError(TicketMCPError):
    """跟进记录不存在。"""

    def __init__(self, follow_up_id: str) -> None:
        super().__init__(
            f"FollowUp not found: {follow_up_id}",
            error_code="FOLLOWUP_NOT_FOUND",
        )
        self.follow_up_id = follow_up_id


class TodoNotFoundError(TicketMCPError):
    """待办不存在。"""

    def __init__(self, todo_id: str) -> None:
        super().__init__(
            f"Todo not found: {todo_id}",
            error_code="TODO_NOT_FOUND",
        )
        self.todo_id = todo_id
