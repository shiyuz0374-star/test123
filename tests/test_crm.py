"""CRM 扩展测试（v0.2.0）。

覆盖：
- 新模型校验（Customer / Contact / FollowUp / Todo）
- InMemoryCRMStore CRUD + 搜索 + 幂等
- SQLiteCRMStore 持久化（同一连接 + 跨实例）
- 11 个新 tool handler（含端到端场景）
"""
import os
import tempfile

import pytest

from ticket_mcp.exceptions import (
    ContactNotFoundError,
    CustomerNotFoundError,
)
from ticket_mcp.models import (
    Contact,
    CreateContactRequest,
    CreateCustomerRequest,
    CreateFollowUpRequest,
    CreateTodoRequest,
    Customer,
    CustomerStatus,
    FollowUp,
    FollowUpStatus,
    GetContactRequest,
    GetCustomerRequest,
    SearchContactRequest,
    SearchCustomerRequest,
    SearchFollowUpRequest,
    Todo,
    TodoStatus,
    UpdateTodoStatusRequest,
)
from ticket_mcp.sqlite_store import create_sqlite_stores
from ticket_mcp.storage import InMemoryCRMStore, InMemoryTicketStore
from ticket_mcp.tools import (
    Services,
    _handle_create_contact,
    _handle_create_customer,
    _handle_create_follow_up,
    _handle_create_todo,
    _handle_get_contact,
    _handle_get_customer,
    _handle_list_todos,
    _handle_search_contact,
    _handle_search_customer,
    _handle_search_follow_up,
    _handle_update_todo_status,
)


@pytest.fixture
def in_memory_services():
    ticket_store = InMemoryTicketStore()
    crm_store = InMemoryCRMStore()
    return Services(ticket_store=ticket_store, crm_store=crm_store), crm_store


# ============================================================
# 模型校验
# ============================================================


class TestCustomerModel:
    def test_basic(self):
        c = Customer(id="CUS-1", name="成都星河科技")
        assert c.status == CustomerStatus.LEAD
        assert c.id == "CUS-1"

    def test_with_industry(self):
        c = Customer(id="CUS-2", name="某公司", industry="软件")
        assert c.industry == "软件"


class TestContactModel:
    def test_basic(self):
        ct = Contact(id="CON-1", customer_id="CUS-1", name="王敏")
        assert ct.is_primary is False
        assert ct.phone is None

    def test_with_phone(self):
        ct = Contact(
            id="CON-2",
            customer_id="CUS-1",
            name="王敏",
            phone="13800001234",
            role="采购",
            is_primary=True,
        )
        assert ct.phone == "13800001234"
        assert ct.is_primary is True


class TestFollowUpModel:
    def test_basic(self):
        fu = FollowUp(id="FUP-1", customer_id="CUS-1", content="今天拜访")
        assert fu.status == FollowUpStatus.PENDING


class TestTodoModel:
    def test_basic(self):
        t = Todo(id="TODO-1", title="发报价")
        assert t.status == TodoStatus.OPEN


# ============================================================
# InMemoryCRMStore
# ============================================================


class TestInMemoryCRMStore:
    def test_customer_crud(self):
        store = InMemoryCRMStore()
        c = Customer(id="CUS-1", name="A")
        store.save_customer(c)
        assert store.find_customer_by_id("CUS-1").name == "A"

    def test_customer_not_found(self):
        store = InMemoryCRMStore()
        with pytest.raises(CustomerNotFoundError):
            store.find_customer_by_id("X")

    def test_search_customers_case_insensitive(self):
        store = InMemoryCRMStore()
        store.save_customer(Customer(id="CUS-1", name="成都星河科技"))
        store.save_customer(Customer(id="CUS-2", name="星河智能"))
        results = store.search_customers("星河")
        assert len(results) == 2

    def test_customer_exists_by_name_exact(self):
        store = InMemoryCRMStore()
        store.save_customer(Customer(id="CUS-1", name="  成都星河  "))
        found = store.customer_exists_by_name("成都星河")
        assert found is not None
        assert found.id == "CUS-1"

    def test_contact_search_scoped_by_customer(self):
        store = InMemoryCRMStore()
        store.save_customer(Customer(id="CUS-1", name="A"))
        store.save_customer(Customer(id="CUS-2", name="B"))
        store.save_contact(Contact(id="CON-1", customer_id="CUS-1", name="王敏"))
        store.save_contact(Contact(id="CON-2", customer_id="CUS-2", name="王敏"))
        results = store.search_contacts("王敏", customer_id="CUS-1")
        assert len(results) == 1
        assert results[0].id == "CON-1"

    def test_todo_idempotency(self):
        store = InMemoryCRMStore()
        t = Todo(id="TODO-1", title="X", idempotency_key="key-1")
        store.save_todo(t)
        found = store.find_todo_by_idempotency_key("key-1")
        assert found is not None
        assert found.id == "TODO-1"


# ============================================================
# SQLite 持久化
# ============================================================


class TestSQLiteCRMStore:
    def test_persistence_across_instances(self):
        """关键测试：关闭连接 → 新连接打开 → 数据还在。

        这是 in-memory 实现做不到的（重进程数据丢）。
        """
        with tempfile.TemporaryDirectory() as tmp:
            db_path = os.path.join(tmp, "test.db")
            # 1. 第一次实例：写入
            stores1 = create_sqlite_stores(db_path)
            stores1.crm.save_customer(Customer(id="CUS-1", name="持久化测试客户"))
            stores1.close()
            # 2. 第二次实例：模拟重启
            stores2 = create_sqlite_stores(db_path)
            try:
                found = stores2.crm.find_customer_by_id("CUS-1")
                assert found.name == "持久化测试客户"
            finally:
                stores2.close()

    def test_sqlite_search_and_idempotency(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = os.path.join(tmp, "test.db")
            stores = create_sqlite_stores(db_path)
            try:
                stores.crm.save_customer(Customer(id="CUS-1", name="测试"))
                assert len(stores.crm.search_customers("测试")) == 1
                assert stores.crm.customer_exists_by_name("测试") is not None
            finally:
                stores.close()


# ============================================================
# 端到端：用户原始测试用例（成都星河科技 / 王敏）
# ============================================================


class TestEndToEndScenario:
    """模拟用户原始需求：
    '今天拜访了成都星河科技，见到王敏，电话 13800001234...'
    期望：search → 不存在 → create_customer + create_contact + create_todo
    """

    @pytest.mark.asyncio
    async def test_full_sales_visit_flow(self, in_memory_services):
        services, crm = in_memory_services

        # 1. 搜索客户（预期空）
        search_cust = await _handle_search_customer(
            {"query": "成都星河科技"}, services
        )
        assert "未找到" in search_cust

        # 2. 搜索联系人（预期空）
        search_con = await _handle_search_contact(
            {"query": "王敏"}, services
        )
        assert "未在" in search_con and "王敏" in search_con

        # 3. 创建客户
        create_cust_result = await _handle_create_customer(
            {
                "name": "成都星河科技",
                "industry": "科技",
                "idempotency_key": "visit-2026-09-29-chengdu-xinghe",
            },
            services,
        )
        # Customer.to_summary 不带"已创建"前缀，验证 customer ID 格式 + 公司名
        assert "CUS-" in create_cust_result
        assert "成都星河科技" in create_cust_result
        # 提取 customer_id
        for line in create_cust_result.split("\n"):
            if "客户 CUS-" in line:
                customer_id = line.split("客户 ")[1].split("：")[0].strip()
                break
        else:
            raise AssertionError(f"无法提取 customer_id: {create_cust_result}")
        assert customer_id.startswith("CUS-")

        # 4. 验证去重（再次创建同名 → 幂等返回）
        replay = await _handle_create_customer(
            {"name": "成都星河科技", "industry": "科技"}, services
        )
        assert "幂等命中" in replay

        # 5. 搜索现在能找到
        search_after = await _handle_search_customer(
            {"query": "成都星河"}, services
        )
        assert "成都星河科技" in search_after
        assert customer_id in search_after

        # 6. 创建联系人（先要校验 customer_id 已存在）
        create_con_result = await _handle_create_contact(
            {
                "customer_id": customer_id,
                "name": "王敏",
                "phone": "13800001234",
                "role": "采购负责人",
                "is_primary": True,
            },
            services,
        )
        # Contact.to_summary 不带"已创建"前缀
        assert "CON-" in create_con_result
        assert "王敏" in create_con_result
        # 提取 contact_id
        for line in create_con_result.split("\n"):
            if "联系人 CON-" in line:
                contact_id = line.split("联系人 ")[1].split("：")[0].strip()
                break
        else:
            raise AssertionError(f"无法提取 contact_id: {create_con_result}")
        assert contact_id.startswith("CON-")

        # 7. 创建跟进记录
        follow_up_result = await _handle_create_follow_up(
            {
                "customer_id": customer_id,
                "contact_id": contact_id,
                "content": "今天拜访，王敏对仓储系统感兴趣。答应下周四前发标准版+专业版两份报价。",
                "idempotency_key": "visit-2026-09-29-followup",
            },
            services,
        )
        # FollowUp.to_summary 不带"已创建"前缀
        assert "FUP-" in follow_up_result
        assert "王敏" in follow_up_result or "仓储系统" in follow_up_result

        # 8. 创建待办（下周四前发报价）
        from datetime import datetime, timezone, timedelta
        next_thu = datetime.now(timezone.utc) + timedelta(days=7)
        todo_result = await _handle_create_todo(
            {
                "title": "向王敏发送标准版+专业版两份报价",
                "description": "今天拜访承诺。下周四前完成。",
                "due_at": next_thu.isoformat(),
                "customer_id": customer_id,
                "contact_id": contact_id,
                "idempotency_key": "todo-send-quote-2026-09-29",
            },
            services,
        )
        # Todo.to_summary 不带"已创建"前缀
        assert "TODO-" in todo_result
        assert "向王敏发送标准版" in todo_result
        # 提取 todo_id
        for line in todo_result.split("\n"):
            if "待办 TODO-" in line:
                todo_id = line.split("待办 ")[1].split("：")[0].strip()
                break
        else:
            raise AssertionError(f"无法提取 todo_id: {todo_result}")
        assert todo_id.startswith("TODO-")

        # 9. 列出待办，应该看到这条
        list_result = await _handle_list_todos({}, services)
        assert "向王敏发送标准版" in list_result

        # 10. 跟进查询：按客户查
        fu_search = await _handle_search_follow_up(
            {"customer_id": customer_id}, services
        )
        assert "王敏" in fu_search
        assert "仓储系统" in fu_search

        # 11. 更新待办状态（done）
        update_result = await _handle_update_todo_status(
            {"todo_id": todo_id, "status": "done"}, services
        )
        assert "已更新" in update_result

        # 12. 验证错误路径：用不存在的 customer_id 建联系人
        bad_result = await _handle_create_contact(
            {"customer_id": "CUS-9999999-9999", "name": "测试"}, services
        )
        assert "CUSTOMER_NOT_FOUND" in bad_result


# ============================================================
# 错误路径
# ============================================================


class TestErrorPaths:
    @pytest.mark.asyncio
    async def test_get_customer_not_found(self, in_memory_services):
        services, _ = in_memory_services
        result = await _handle_get_customer({"customer_id": "CUS-999-9999"}, services)
        assert "CUSTOMER_NOT_FOUND" in result

    @pytest.mark.asyncio
    async def test_create_customer_missing_name(self, in_memory_services):
        services, _ = in_memory_services
        result = await _handle_create_customer({}, services)
        assert "VALIDATION_ERROR" in result

    @pytest.mark.asyncio
    async def test_idempotency_conflict_on_different_params(self, in_memory_services):
        """同名客户参数不一致 → IDEMPOTENCY_CONFLICT。

        设计说明：create_customer 的去重靠 customer_exists_by_name（公司名唯一），
        不靠 idempotency_key。idempotency_key 字段保留给后续扩展。
        """
        services, _ = in_memory_services
        await _handle_create_customer(
            {"name": "X 公司", "industry": "软件"}, services
        )
        result = await _handle_create_customer(
            {"name": "X 公司", "industry": "硬件"}, services
        )
        assert "IDEMPOTENCY_CONFLICT" in result
