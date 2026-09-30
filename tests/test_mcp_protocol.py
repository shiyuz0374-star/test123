"""MCP 协议端到端测试。

启动真实的 server.py 子进程，通过 stdio 发 JSON-RPC 消息，
验证：
- initialize 握手
- tools/list 列出所有工具
- tools/call 调用 recognize_ticket 创建工单
- tools/call 调用 get_ticket 查到工单
"""
import json
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.fixture(scope="module")
def mcp_server():
    """启动 MCP server 子进程（模块级，整个测试文件共用）。"""
    proc = subprocess.Popen(
        [sys.executable, "-m", "ticket_mcp.server"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=str(Path(__file__).resolve().parent.parent),
        text=True,
        encoding="utf-8",
        bufsize=1,
    )
    yield proc
    proc.terminate()
    try:
        proc.wait(timeout=2)
    except subprocess.TimeoutExpired:
        proc.kill()


def _send(proc, msg):
    proc.stdin.write(json.dumps(msg) + "\n")
    proc.stdin.flush()


def _read_response(proc, expected_id):
    """读一条 JSON-RPC 响应（按 id 匹配）。"""
    while True:
        line = proc.stdout.readline()
        if not line:
            stderr = proc.stderr.read()
            raise RuntimeError(f"server closed; stderr={stderr[:500]}")
        data = json.loads(line)
        if data.get("id") == expected_id:
            return data


class TestMCPProtocol:
    """端到端 MCP 协议测试。"""

    def test_01_initialize(self, mcp_server):
        """initialize 握手成功。"""
        _send(mcp_server, {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "smoke-test", "version": "0.1.0"},
            },
        })
        resp = _read_response(mcp_server, 1)
        assert "result" in resp, f"unexpected response: {resp}"
        assert resp["result"]["serverInfo"]["name"] == "ticket-mcp"

        # 通知 initialized（客户端必须发，否则 server 可能不会响应后续请求）
        _send(mcp_server, {
            "jsonrpc": "2.0",
            "method": "notifications/initialized",
        })

    def test_02_list_tools(self, mcp_server):
        """list_tools 列出所有工具。"""
        _send(mcp_server, {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/list",
        })
        resp = _read_response(mcp_server, 2)
        tools = resp["result"]["tools"]
        names = {t["name"] for t in tools}
        assert "recognize_ticket" in names, f"missing recognize_ticket; got {names}"
        assert "get_ticket" in names, f"missing get_ticket; got {names}"

    def test_03_call_recognize_ticket(self, mcp_server):
        """调用 recognize_ticket 创建工单。"""
        _send(mcp_server, {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {
                "name": "recognize_ticket",
                "arguments": {
                    "customer_name": "王经理",
                    "title": "仓储报错",
                    "description": "客户催得很紧",
                    "priority": "P0",
                    "idempotency_key": "smoke-001",
                },
            },
        })
        resp = _read_response(mcp_server, 3)
        assert "result" in resp, f"unexpected: {resp}"
        content = resp["result"]["content"][0]["text"]
        assert "已创建" in content
        assert "TKT-" in content
        assert "P0" in content

    def test_04_call_get_ticket(self, mcp_server):
        """调用 get_ticket 查询刚创建的工单。"""
        # 先创建
        _send(mcp_server, {
            "jsonrpc": "2.0",
            "id": 4,
            "method": "tools/call",
            "params": {
                "name": "recognize_ticket",
                "arguments": {
                    "customer_name": "李总",
                    "title": "申请试用",
                    "description": "希望试用新功能",
                    "priority": "P2",
                    "idempotency_key": "smoke-002",
                },
            },
        })
        create_resp = _read_response(mcp_server, 4)
        text = create_resp["result"]["content"][0]["text"]
        # 提取 ticket_id
        ticket_id = None
        for tok in text.split():
            if tok.startswith("TKT-"):
                ticket_id = tok.rstrip("：:,")
                break
        assert ticket_id is not None, f"无法提取 ticket_id: {text}"

        # 查询
        _send(mcp_server, {
            "jsonrpc": "2.0",
            "id": 5,
            "method": "tools/call",
            "params": {
                "name": "get_ticket",
                "arguments": {"ticket_id": ticket_id},
            },
        })
        get_resp = _read_response(mcp_server, 5)
        content = get_resp["result"]["content"][0]["text"]
        assert "申请试用" in content
        assert ticket_id in content
