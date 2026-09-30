"""recognize_ticket 和 get_ticket 工具的集成测试。

覆盖场景：
- recognize_ticket: 创建、缺字段、幂等命中、幂等冲突
- get_ticket: 查询存在、不存在
"""
import pytest

from ticket_mcp.models import Priority
from ticket_mcp.storage import InMemoryCRMStore, InMemoryTicketStore
from ticket_mcp.tools import Services, _handle_recognize_ticket, _handle_get_ticket


@pytest.fixture
def store():
    """每个测试一个全新的 services 容器（含 ticket_store + crm_store）。"""
    return Services(
        ticket_store=InMemoryTicketStore(),
        crm_store=InMemoryCRMStore(),
    )


class TestRecognizeTicket:
    """recognize_ticket 工具的测试集合。"""

    @pytest.mark.asyncio
    async def test_create_ticket_success(self, store: InMemoryTicketStore) -> None:
        """正常创建：返回包含 ticket_id 的摘要。"""
        result = await _handle_recognize_ticket(
            arguments={
                "customer_name": "王经理",
                "title": "仓储系统报错",
                "description": "客户催得很紧，需要紧急处理",
                "priority": "P0",
                "source_message_id": "msg-001",
                "idempotency_key": "msg-001",
            },
            services=store,
        )

        assert "已创建" in result
        assert "TKT-" in result
        assert "P0" in result

        # 存储中应该有一条记录
        tickets = store.ticket_store.list_all()
        assert len(tickets) == 1
        assert tickets[0].priority == Priority.P0

    @pytest.mark.asyncio
    async def test_missing_required_field(
        self, store: InMemoryTicketStore
    ) -> None:
        """缺 title：返回校验错误。"""
        result = await _handle_recognize_ticket(
            arguments={
                "description": "没有标题",
                "idempotency_key": "msg-002",
            },
            services=store,
        )

        assert "VALIDATION_ERROR" in result or "错误" in result
        assert store.ticket_store.list_all() == []

    @pytest.mark.asyncio
    async def test_idempotent_replay(self, store: InMemoryTicketStore) -> None:
        """幂等命中：同一 key 重复调用返回同一 ticket_id。"""
        args = {
            "customer_name": "李总",
            "title": "申请试用",
            "description": "希望试用新功能",
            "priority": "P2",
            "idempotency_key": "msg-003",
        }

        result1 = await _handle_recognize_ticket(args, store)
        result2 = await _handle_recognize_ticket(args, store)

        assert "已创建" in result1
        assert "幂等命中" in result2 or "已返回原工单" in result2
        assert len(store.ticket_store.list_all()) == 1

    @pytest.mark.asyncio
    async def test_idempotency_conflict(
        self, store: InMemoryTicketStore
    ) -> None:
        """幂等冲突：同一 key 但参数不同。"""
        await _handle_recognize_ticket(
            arguments={
                "customer_name": "客户A",
                "title": "原标题",
                "description": "原描述",
                "priority": "P2",
                "idempotency_key": "msg-004",
            },
            services=store,
        )

        result = await _handle_recognize_ticket(
            arguments={
                "customer_name": "客户B",
                "title": "新标题",
                "description": "新描述",
                "priority": "P2",
                "idempotency_key": "msg-004",
            },
            services=store,
        )

        assert "IDEMPOTENCY_CONFLICT" in result or "幂等键" in result


class TestGetTicket:
    """get_ticket 工具的测试集合。"""

    @pytest.mark.asyncio
    async def test_get_existing_ticket(self, store: InMemoryTicketStore) -> None:
        """查询已存在的工单。"""
        create_result = await _handle_recognize_ticket(
            arguments={
                "title": "测试工单",
                "description": "测试描述",
                "idempotency_key": "msg-005",
            },
            services=store,
        )

        # 从摘要中提取 ticket_id
        ticket_id = None
        for line in create_result.split("\n"):
            if "TKT-" in line:
                tokens = line.split()
                for tok in tokens:
                    if tok.startswith("TKT-"):
                        ticket_id = tok.rstrip("：:")
                        break
                if ticket_id:
                    break

        assert ticket_id is not None, f"无法提取 ticket_id: {create_result}"

        result = await _handle_get_ticket(
            arguments={"ticket_id": ticket_id},
            services=store,
        )

        assert "测试工单" in result
        assert ticket_id in result

    @pytest.mark.asyncio
    async def test_get_nonexistent_ticket(
        self, store: InMemoryTicketStore
    ) -> None:
        """查询不存在的工单：返回错误。"""
        result = await _handle_get_ticket(
            arguments={"ticket_id": "TKT-9999999999-9999"},
            services=store,
        )

        assert "TICKET_NOT_FOUND" in result or "未找到" in result or "不存在" in result
