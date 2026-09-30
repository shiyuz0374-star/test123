# ticket-mcp

MCP (Model Context Protocol) server for ticket recognition from conversations.

## 用途

当对话中包含工单相关内容（报修、投诉、申请、问题、紧急请求）时，自动识别并创建工单记录。
作为 OpenClaw 平台的本地 MCP 服务，通过 stdio 传输集成。

## 架构（v0.1.0）

```
OpenClaw 对话层
    ↓ 触发 MCP 调用
ticket-mcp (stdio)
    ├─ server.py    # MCP 入口（stdio 传输）
    ├─ tools.py     # 工具注册与实现
    ├─ storage.py   # 存储抽象（in-memory）
    ├─ models.py    # pydantic 数据模型
    ├─ exceptions.py# 业务异常
    ├─ config.py    # 环境变量配置
    └─ utils/       # 工具（日志等）
```

## 安装

```bash
pip install -e ".[dev]"
```

## 运行

```bash
python -m ticket_mcp.server
```

服务通过 stdio 与 OpenClaw 通信。

## 配置

复制 `.env.example` 为 `.env` 并按需修改：

| 变量 | 默认值 | 说明 |
|---|---|---|
| `TICKET_MCP_LOG_LEVEL` | `INFO` | 日志级别 |
| `TICKET_MCP_SERVER_NAME` | `ticket-mcp` | MCP 服务名 |
| `TICKET_MCP_DEFAULT_PRIORITY` | `P2` | 工单默认紧急程度 |
| `TICKET_MCP_DEBUG` | `false` | 是否输出请求/响应详情 |

## 工具列表

| 工具名 | 描述 |
|---|---|
| `recognize_ticket` | 从对话内容识别并创建工单 |
| `get_ticket` | 根据工单 ID 查询详情 |

## 扩展点

1. **存储后端**：替换 `storage.py` 的 `InMemoryTicketStore` 实现，可对接 SQLite/Postgres/外部 CRM
2. **新增工具**：在 `tools.py` 的 `_TOOL_DEFINITIONS` 和 `_TOOL_HANDLERS` 中追加
3. **触发策略**：未来可在 `tools.py` 中加入分类器（关键词 + LLM 兜底）

## 许可证

MIT
