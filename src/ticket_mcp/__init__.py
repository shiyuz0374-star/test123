"""ticket-mcp: MCP server for ticket recognition from conversations.

This package exposes a Model Context Protocol (MCP) server that integrates
with OpenClaw (or any MCP-compatible client) to recognize ticket-related
content in conversations and create structured ticket records.

设计原则（对应开发标准）：
- 模块化分层（models / storage / tools / config / exceptions）
- 业务异常独立于内置异常（exceptions.py）
- 数据契约用 pydantic 集中管理（models.py）
- 配置通过环境变量注入，禁硬编码（config.py）
- 关键路径加中文注释，重点写"为什么"
"""
__version__ = "0.1.0"
