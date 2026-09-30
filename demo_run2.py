"""MCP 端到端流程演示脚本 #2：苏州青禾生物 / 周启明。

按 MCP JSON-RPC 2.0 构造 tools/call 请求，直接调 ticket-mcp handler。
每步 request/response 序列化到 demo_records2.json。

客户信息（2026-09-29 下午电话沟通）：
- 公司：苏州青禾生物
- 联系人：周启明（采购经理）——手机号未提供
- 沟通纪要：准备新建一条冷链仓储线，关注温湿度实时监控 + 超限告警
- 状态：正在比较三家供应商，预计下个月中旬决定
- 承诺：两天内发资料（deadline 2026-10-01），下周再约需求沟通
- 缺失字段：预算、手机号、收件邮箱
"""
import sys
import json
from datetime import datetime, timedelta, timezone

sys.path.insert(0, r"E:\projects\ticket-mcp\src")

from ticket_mcp.tools import (
    Services,
    _handle_search_contact,
    _handle_search_customer,
    _handle_create_customer,
    _handle_create_contact,
    _handle_create_follow_up,
    _handle_create_todo,
)
from ticket_mcp.storage import InMemoryCRMStore, InMemoryTicketStore


def mcp_call(tool_name: str, arguments: dict, request_id: int) -> dict:
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "method": "tools/call",
        "params": {"name": tool_name, "arguments": arguments},
    }


def extract_id(text: str, prefix: str) -> str | None:
    for line in text.split("\n"):
        if prefix in line:
            for t in line.split():
                if t.startswith(prefix):
                    return t.rstrip("：:,")
    return None


import asyncio


async def main():
    services = Services(
        ticket_store=InMemoryTicketStore(),
        crm_store=InMemoryCRMStore(),
    )

    # 关键日期
    today = datetime(2026, 9, 30, 9, 0, 0, tzinfo=timezone.utc)  # 今天周三
    deadline_materials = datetime(2026, 10, 1, 23, 59, 0, tzinfo=timezone.utc)  # 周四 EOD：两天内
    next_tue = datetime(2026, 10, 6, 9, 0, 0, tzinfo=timezone.utc)  # 下周二：约需求沟通
    records = []

    # Step 1: 搜客户
    req = mcp_call("search_customer", {"query": "苏州青禾生物"}, 1)
    resp = await _handle_search_customer(req["params"]["arguments"], services)
    records.append({"step": 1, "name": "search_customer", "request": req, "response_text": resp})

    # Step 2: 搜联系人
    req = mcp_call("search_contact", {"query": "周启明"}, 2)
    resp = await _handle_search_contact(req["params"]["arguments"], services)
    records.append({"step": 2, "name": "search_contact", "request": req, "response_text": resp})

    # Step 3: 建客户
    create_cust_args = {
        "name": "苏州青禾生物",
        "industry": "生物科技",
        "notes": (
            "准备新建一条冷链仓储线；关注温湿度实时监控 + 超限告警。"
            "正在比较三家供应商，预计下个月中旬决定。"
            "⚠️ 缺失：预算、手机号、收件邮箱"
        ),
        "idempotency_key": "demo2-2026-09-30-suzhou-qinghe",
    }
    req = mcp_call("create_customer", create_cust_args, 3)
    resp = await _handle_create_customer(create_cust_args, services)
    customer_id = extract_id(resp, "CUS-")
    records.append({"step": 3, "name": "create_customer", "request": req, "response_text": resp, "extracted_id": customer_id})

    # Step 4: 建联系人（无 phone——客户没给）
    create_con_args = {
        "customer_id": customer_id,
        "name": "周启明",
        "role": "采购经理",
        "is_primary": True,
        "idempotency_key": "demo2-2026-09-30-zhou-qiming",
    }
    req = mcp_call("create_contact", create_con_args, 4)
    resp = await _handle_create_contact(create_con_args, services)
    contact_id = extract_id(resp, "CON-")
    records.append({"step": 4, "name": "create_contact", "request": req, "response_text": resp, "extracted_id": contact_id})

    # Step 5: 跟进纪要
    create_fu_args = {
        "customer_id": customer_id,
        "contact_id": contact_id,
        "content": (
            "2026-09-29 下午电话沟通。\n"
            "- 苏州青禾生物准备新建一条冷链仓储线\n"
            "- 关注点：温湿度实时监控、超限告警\n"
            "- 状态：还在比较三家供应商，预计下个月中旬决定是否采购\n"
            "- 缺失：预算、手机号、具体收件邮箱（客户均未提供）"
        ),
        "follow_up_at": next_tue.isoformat(),
        "idempotency_key": "demo2-2026-09-30-followup",
    }
    req = mcp_call("create_follow_up", create_fu_args, 5)
    resp = await _handle_create_follow_up(create_fu_args, services)
    follow_up_id = extract_id(resp, "FUP-")
    records.append({"step": 5, "name": "create_follow_up", "request": req, "response_text": resp, "extracted_id": follow_up_id})

    # Step 6: 待办 - 两天内发资料
    todo1_args = {
        "title": "向周启明发送苏州青禾生物冷链方案 + 实施周期说明",
        "description": "客户未给预算/手机号/邮箱——发送前需补充收件邮箱。",
        "due_at": deadline_materials.isoformat(),
        "customer_id": customer_id,
        "contact_id": contact_id,
        "follow_up_id": follow_up_id,
        "idempotency_key": "demo2-2026-09-30-todo-send-materials",
    }
    req = mcp_call("create_todo", todo1_args, 6)
    resp = await _handle_create_todo(todo1_args, services)
    todo1_id = extract_id(resp, "TODO-")
    records.append({"step": 6, "name": "create_todo_send_materials", "request": req, "response_text": resp, "extracted_id": todo1_id})

    # Step 7: 待办 - 下周二约需求沟通
    todo2_args = {
        "title": "联系周启明：约下周需求沟通",
        "description": "需补齐：手机号、收件邮箱、预算口径",
        "due_at": next_tue.isoformat(),
        "customer_id": customer_id,
        "contact_id": contact_id,
        "follow_up_id": follow_up_id,
        "idempotency_key": "demo2-2026-09-30-todo-call",
    }
    req = mcp_call("create_todo", todo2_args, 7)
    resp = await _handle_create_todo(todo2_args, services)
    todo2_id = extract_id(resp, "TODO-")
    records.append({"step": 7, "name": "create_todo_call_next_week", "request": req, "response_text": resp, "extracted_id": todo2_id})

    output = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "scenario": "苏州青禾生物 / 周启明 冷链方案",
        "extracted_ids": {
            "customer_id": customer_id,
            "contact_id": contact_id,
            "follow_up_id": follow_up_id,
            "todo_send_materials_id": todo1_id,
            "todo_call_id": todo2_id,
        },
        "missing_fields": ["budget", "contact_mobile", "delivery_email"],
        "steps": records,
    }

    out_path = r"E:\projects\ticket-mcp\demo_records2.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"已写入: {out_path}")
    print(f"步骤数: {len(records)}")
    print(f"客户 ID: {customer_id}")
    print(f"联系人 ID: {contact_id}")
    print(f"跟进 ID: {follow_up_id}")
    print(f"待办1（发资料，10-01 EOD）: {todo1_id}")
    print(f"待办2（下周约沟通，10-06）: {todo2_id}")


asyncio.run(main())
