"""日志配置。

为什么日志必须输出到 stderr：
- MCP 协议使用 stdio 通信，stdout 是协议通道
- 如果日志输出到 stdout，会破坏 JSON-RPC 消息格式导致协议失败
- 所有日志强制走 stderr 是 MCP server 的最佳实践

扩展点：
- 后续可接入 structlog 或 JSON formatter，方便日志聚合
- 需要输出到文件时，可加 file handler
"""
import logging
import sys


def setup_logging(level: str = "INFO", debug: bool = False) -> None:
    """配置全局日志。

    Args:
        level: 日志级别字符串（DEBUG/INFO/WARNING/ERROR）
        debug: 是否启用调试模式（输出更详细信息）
    """
    log_level = getattr(logging, level.upper(), logging.INFO)

    # 简洁的格式：时间 + 级别 + 模块名 + 消息
    fmt = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    datefmt = "%Y-%m-%d %H:%M:%S"

    # 强制输出到 stderr，避免污染 stdout（MCP 协议通道）
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter(fmt=fmt, datefmt=datefmt))

    # 配置 root logger
    root = logging.getLogger()
    root.setLevel(level=log_level)
    # 清除已有 handler（避免重复输出）
    root.handlers.clear()
    root.addHandler(handler)

    # 静默第三方库的冗余日志
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)

    if debug:
        logging.getLogger("ticket_mcp").setLevel(logging.DEBUG)


def get_logger(name: str | None = None) -> logging.Logger:
    """获取模块 logger。

    Args:
        name: 通常传 __name__，会自动继承父 logger 的配置
    """
    return logging.getLogger(name or "ticket_mcp")
