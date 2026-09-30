"""存储层 + 数据模型的集成测试。

覆盖场景：
- 数据模型校验（必填、枚举、字段长度）
- 业务异常（error_code、message）
- 存储抽象（save / find_by_id / find_by_idempotency_key / list_all）
- 线程安全（in-memory 实现）

注意：
- 不测试 tools.py（Step 2 才实现）
- 不测试 server.py（Step 2 才实现）
"""
import threading

import pytest

from ticket_mcp.exceptions import (
    IdempotencyConflictError,
    TicketMCPError,
    TicketNotFoundError,
)
from ticket_mcp.models import (
    GetTicketRequest,
    Priority,
    RecognizeTicketRequest,
    TicketRecord,
    TicketStatus,
    ToolError,
)
from ticket_mcp.storage import InMemoryTicketStore


# ---------------------------------------------------------------------------
# 数据模型
# ---------------------------------------------------------------------------
class TestModels:
    """models.py 的测试集合。"""

    def test_recognize_request_defaults(self) -> None:
        """默认 priority = P2, default channel = openclaw。"""
        req = RecognizeTicketRequest(
            title="仓储报错",
            description="客户催得紧",
            idempotency_key="msg-001",
        )
        assert req.priority == Priority.P2
        assert req.source_channel == "openclaw"
        assert req.customer_name is None

    def test_recognize_request_priority_enum(self) -> None:
        """priority 必须合法枚举值。"""
        with pytest.raises(Exception):
            RecognizeTicketRequest(
                title="x",
                description="y",
                priority="P9",  # 非法
                idempotency_key="k",
            )

    def test_recognize_request_idempotency_key_whitespace(self) -> None:
        """idempotency_key 不能含空白。"""
        with pytest.raises(Exception):
            RecognizeTicketRequest(
                title="x",
                description="y",
                idempotency_key="has space",
            )

    def test_get_ticket_request_pattern(self) -> None:
        """ticket_id 必须匹配 TKT-{digits}-{digits}。"""
        # 合法
        req = GetTicketRequest(ticket_id="TKT-1700000000000-0000")
        assert req.ticket_id == "TKT-1700000000000-0000"
        # 非法
        with pytest.raises(Exception):
            GetTicketRequest(ticket_id="BAD-001")

    def test_ticket_record_to_summary(self) -> None:
        """to_summary 输出包含关键字段。"""
        rec = TicketRecord(
            ticket_id="TKT-1700000000000",
            customer_name="王经理",
            title="仓储报错",
            description="详细描述",
            priority=Priority.P0,
            idempotency_key="msg-x",
        )
        summary = rec.to_summary()
        assert "TKT-1700000000000" in summary
        assert "王经理" in summary
        assert "P0" in summary

    def test_tool_error_to_text(self) -> None:
        """ToolError 转文本格式正确。"""
        err = ToolError(error_code="X_CODE", message="some msg")
        text = err.to_text()
        assert "X_CODE" in text
        assert "some msg" in text


# ---------------------------------------------------------------------------
# 业务异常
# ---------------------------------------------------------------------------
class TestExceptions:
    """exceptions.py 的测试集合。"""

    def test_ticket_not_found_error(self) -> None:
        err = TicketNotFoundError("TKT-999")
        assert err.error_code == "TICKET_NOT_FOUND"
        assert err.ticket_id == "TKT-999"
        assert "TKT-999" in str(err)

    def test_idempotency_conflict_error(self) -> None:
        err = IdempotencyConflictError("key-1")
        assert err.error_code == "IDEMPOTENCY_CONFLICT"
        assert err.idempotency_key == "key-1"

    def test_base_error_default_code(self) -> None:
        err = TicketMCPError("oops")
        assert err.error_code == "INTERNAL_ERROR"


# ---------------------------------------------------------------------------
# 存储层
# ---------------------------------------------------------------------------
class TestInMemoryStore:
    """InMemoryTicketStore 的测试集合。"""

    @pytest.fixture
    def store(self) -> InMemoryTicketStore:
        return InMemoryTicketStore()

    def _make_ticket(self, key: str = "msg-1") -> TicketRecord:
        return TicketRecord(
            ticket_id="TKT-1700000000000",
            customer_name="客户A",
            title="t",
            description="d",
            priority=Priority.P2,
            idempotency_key=key,
        )

    def test_save_and_find_by_id(self, store: InMemoryTicketStore) -> None:
        """save 后可以通过 ID 找到。"""
        t = self._make_ticket()
        store.save(t)
        found = store.find_by_id(t.ticket_id)
        assert found.ticket_id == t.ticket_id
        assert found.priority == Priority.P2

    def test_find_by_id_not_found(self, store: InMemoryTicketStore) -> None:
        """不存在的 ID 抛 TicketNotFoundError。"""
        with pytest.raises(TicketNotFoundError):
            store.find_by_id("TKT-9999999999")

    def test_find_by_idempotency_key(self, store: InMemoryTicketStore) -> None:
        """按幂等键查找。"""
        t = self._make_ticket(key="k1")
        store.save(t)
        found = store.find_by_idempotency_key("k1")
        assert found is not None
        assert found.ticket_id == t.ticket_id

    def test_find_by_idempotency_key_missing(self, store: InMemoryTicketStore) -> None:
        """幂等键不存在返回 None。"""
        assert store.find_by_idempotency_key("nope") is None

    def test_list_all(self, store: InMemoryTicketStore) -> None:
        """list_all 返回所有工单。"""
        store.save(self._make_ticket(key="k1"))
        store.save(TicketRecord(
                ticket_id="TKT-1700000000001",
                customer_name="x",
                title="y",
                description="z",
                priority=Priority.P1,
                idempotency_key="k2",
            ))
        assert len(store.list_all()) == 2

    def test_thread_safety(self, store: InMemoryTicketStore) -> None:
        """多线程并发 save 不应崩溃。"""
        def worker(i: int) -> None:
            store.save(TicketRecord(
                ticket_id=f"TKT-{1700000000000 + i}",
                customer_name=f"c{i}",
                title="t",
                description="d",
                priority=Priority.P2,
                idempotency_key=f"k{i}",
            ))

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(store.list_all()) == 20
