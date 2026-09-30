"""MCP server 入口。

启动方式：
- python -m ticket_mcp.server
- 或安装后用 ticket-mcp 命令

传输协议：
- stdio（本地集成最稳定；OpenClaw 通过 stdio 启动子进程）

注意事项：
- 日志必须输出到 stderr，否则会污染 stdio 协议通信
- 服务是阻塞的，由 MCP 调用方（OpenClaw）通过 stdin 发送请求
- 不要在主线程做任何阻塞操作；长任务应该异步化
"""
import asyncio
import logging
import sys

from mcp.server.lowlevel.server import Server
from mcp.server.stdio import stdio_server

from .config import get_settings
from .storage import create_crm_store, create_ticket_store
from .tools import Services, build_tool_server_kwargs
from .logging import setup_logging


logger = logging.getLogger(__name__)


async def main() -> None:
    """MCP server 主入口。

    启动流程：
    1. 加载配置
    2. 配置日志（必须先于其他模块的日志输出）
    3. 创建存储实例
    4. 构造 Server 实例并注入工具 handlers
    5. 启动 stdio server，进入事件循环
    """
    settings = get_settings()
    setup_logging(level=settings.log_level, debug=settings.debug)

    logger.info(
        "mcp_server_starting",
        extra={"server_name": settings.server_name},
    )

    ticket_store = create_ticket_store(
        backend=settings.storage_backend,
        db_path=settings.storage_db_path,
    )
    crm_store = create_crm_store(
        backend=settings.storage_backend,
        db_path=settings.storage_db_path,
    )
    services = Services(ticket_store=ticket_store, crm_store=crm_store)

    # mcp 2.2 风格：handler 通过 Server 构造函数的 on_* 参数注入
    server = Server(
        settings.server_name,
        **build_tool_server_kwargs(services),
    )

    # stdio 传输：阻塞运行，直到 MCP 调用方关闭 stdin
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            server.create_initialization_options(),
        )


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("mcp_server_stopped_by_user")
        sys.exit(0)
    except Exception:
        logger.exception("mcp_server_crashed")
        sys.exit(1)
