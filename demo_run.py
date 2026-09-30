"""MCP 端到端流程演示脚本。

按 MCP JSON-RPC 2.0 协议构造 tools/call 请求，直接调 ticket-mcp 的 handler
（handler 签名与 MCP 协议层一致）。每步的 request/response 都序列化到 JSON。

客户信息（测试数据）：
- 公司：杭州云帆智能
- 联系人：陈可欣（运营总监），电话 13800004567
- 沟通纪要：关注仓储系统升级、多仓库存同步、异常预警；想要三仓场景方案
- 承诺：本周三前发方案，下周一联系确认是否安排演示
"""
import asyncio
import json
import os
import sys
from datetime import datetime, timedelta, timezone

# 加入项目 src 到路径
sys.path.insert(0, r"E:\projects\ticket-mcp\src")

from ticket_mcp.models import (
    CreateContactRequest,
    CreateCustomerRequest,
    CreateFollowUpRequest,
    CreateTodoRequest,
    SearchContactRequest,
    SearchCustomerRequest,
)
from ticket_mcp.storage import InMemoryCRMStore, InMemoryTicketStore
from ticket_mcp.tools import (
    Services,
    _handle_create_contact,
    _handle_create_customer,
    _handle_create_follow_up,
    _handle_create_todo,
    _handle_search_contact,
    _handle_search_customer,
)


def mcp_call(tool_name: str, arguments: dict, request_id: int) -> dict:
    """按 MCP JSON-RPC 2.0 构造 tools/call 请求。"""
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "method": "tools/call",
        "params": {
            "name": tool_name,
            "arguments": arguments,
        },
    }


def extract_id(text: str, prefix: str) -> str | None:
    """从 handler 返回文本里抓 ID（'CUS-123' / 'CON-123' / 'TODO-123'）。"""
    for line in text.split("\n"):
        if prefix in line:
            tokens = line.split()
            for t in tokens:
                if t.startswith(prefix):
                    return t.rstrip("：:,")
    return None


async def main():
    services = Services(
        ticket_store=InMemoryTicketStore(),
        crm_store=InMemoryCRMStore(),
    )

    records = []  # 每个 step 存 {step, request, response_text}

    # 计算下周一日期（今天 2026-09-30 Wed → 下周一 = 2026-10-05）
    next_mon = datetime(2026, 10, 5, 9, 0, 0, tzinfo=timezone.utc)
    this_wed = datetime(2026, 9, 30, 23, 59, 0, tzinfo=timezone.utc)  # 今天 EOD

    # ============ Step 1: 搜索客户 ============
    req = mcp_call("search_customer", {"query": "杭州云帆智能"}, 1)
    resp_text = await _handle_search_customer(req["params"]["arguments"], services)
    records.append({"step": 1, "name": "search_customer", "request": req, "response_text": resp_text})

    # ============ Step 2: 搜索联系人 ============
    req = mcp_call("search_contact", {"query": "陈可欣"}, 2)
    resp_text = await _handle_search_contact(req["params"]["arguments"], services)
    records.append({"step": 2, "name": "search_contact", "request": req, "response_text": resp_text})

    # ============ Step 3: 创建客户 ============
    create_cust_args = {
        "name": "杭州云帆智能",
        "industry": "智能科技",
        "notes": "第四季度计划升级仓库流程；关注多仓库存同步、异常预警；预算未定",
        "idempotency_key": "demo-2026-09-30-hz-yunfan",
    }
    req = mcp_call("create_customer", create_cust_args, 3)
    resp_text = await _handle_create_customer(create_cust_args, services)
    customer_id = extract_id(resp_text, "CUS-")
    records.append({"step": 3, "name": "create_customer", "request": req, "response_text": resp_text, "extracted_id": customer_id})

    # ============ Step 4: 创建联系人 ============
    create_con_args = {
        "customer_id": customer_id,
        "name": "陈可欣",
        "phone": "13800004567",
        "role": "运营总监",
        "is_primary": True,
        "idempotency_key": "demo-2026-09-30-chen-kexin",
    }
    req = mcp_call("create_contact", create_con_args, 4)
    resp_text = await _handle_create_contact(create_con_args, services)
    contact_id = extract_id(resp_text, "CON-")
    records.append({"step": 4, "name": "create_contact", "request": req, "response_text": resp_text, "extracted_id": contact_id})

    # ============ Step 5: 创建跟进记录 ============
    create_fu_args = {
        "customer_id": customer_id,
        "contact_id": contact_id,
        "content": (
            "和杭州云帆智能运营总监陈可欣聊仓储管理系统。\n"
            "- 第四季度计划升级仓库流程\n"
            "- 关注点：多仓库存同步、异常预警\n"
            "- 需求：针对三仓场景的方案\n"
            "- 预算：未定\n"
            "- 承诺：本周三前发方案，下周一再联系确认是否安排演示"
        ),
        "follow_up_at": next_mon.isoformat(),
        "idempotency_key": "demo-2026-09-30-followup",
    }
    req = mcp_call("create_follow_up", create_fu_args, 5)
    resp_text = await _handle_create_follow_up(create_fu_args, services)
    follow_up_id = extract_id(resp_text, "FUP-")
    records.append({"step": 5, "name": "create_follow_up", "request": req, "response_text": resp_text, "extracted_id": follow_up_id})

    # ============ Step 6: 待办 - 本周三前发方案 ============
    create_todo1_args = {
        "title": "向陈可欣发送杭州云帆智能三仓场景方案",
        "description": "针对多仓库存同步 + 异常预警。预算未定时先给框架版。",
        "due_at": this_wed.isoformat(),
        "customer_id": customer_id,
        "contact_id": contact_id,
        "follow_up_id": follow_up_id,
        "idempotency_key": "demo-2026-09-30-todo-send-plan",
    }
    req = mcp_call("create_todo", create_todo1_args, 6)
    resp_text = await _handle_create_todo(create_todo1_args, services)
    todo1_id = extract_id(resp_text, "TODO-")
    records.append({"step": 6, "name": "create_todo_send_plan", "request": req, "response_text": resp_text, "extracted_id": todo1_id})

    # ============ Step 7: 待办 - 下周一联系陈可欣 ============
    create_todo2_args = {
        "title": "联系陈可欣：是否安排仓储管理方案演示",
        "description": "下周一电话或微信联系。需确认演示时间。",
        "due_at": next_mon.isoformat(),
        "customer_id": customer_id,
        "contact_id": contact_id,
        "follow_up_id": follow_up_id,
        "idempotency_key": "demo-2026-09-30-todo-followup-mon",
    }
    req = mcp_call("create_todo", create_todo2_args, 7)
    resp_text = await _handle_create_todo(create_todo2_args, services)
    todo2_id = extract_id(resp_text, "TODO-")
    records.append({"step": 7, "name": "create_todo_followup_mon", "request": req, "response_text": resp_text, "extracted_id": todo2_id})

    # ============ 输出：每步 request/response + 最终 store 状态 ============
    output = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "scenario": "杭州云帆智能 / 陈可欣 三仓场景演示",
        "extracted_ids": {
            "customer_id": customer_id,
            "contact_id": contact_id,
            "follow_up_id": follow_up_id,
            "todo_send_plan_id": todo1_id,
            "todo_followup_mon_id": todo2_id,
        },
        "steps": records,
    }

    out_path = r"E:\projects\ticket-mcp\demo_records.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"已写入: {out_path}")
    print(f"步骤数: {len(records)}")
    print(f"客户 ID: {customer_id}")
    print(f"联系人 ID: {contact_id}")
    print(f"跟进 ID: {follow_up_id}")
    print(f"待办1（发方案）: {todo1_id}")
    print(f"待办2（下周一联系）: {todo2_id}")


asyncio.run(main())
