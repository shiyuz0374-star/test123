"""配置加载模块。

设计原则：
- 所有配置通过环境变量注入，零硬编码
- 使用 pydantic-settings 做类型校验，启动时即报错而非运行时
- 配置项集中管理，新增配置只需在 Settings 类中加字段
- 所有"运行时可调"的参数都暴露在这里，避免散落在代码各处

为什么不用 .env 自动加载：
- pydantic-settings 默认会读 .env 文件
- 但 .env 文件应该加入 .gitignore，部署时由运维注入
- 开发时手动复制 .env.example 为 .env 即可
"""
from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """全局配置。

    所有字段都可被同名环境变量覆盖，前缀 TICKET_MCP_。
    例如：TICKET_MCP_LOG_LEVEL=DEBUG 会覆盖 log_level 的默认值。
    """

    # 服务标识
    server_name: str = Field(
        default="ticket-mcp",
        description="MCP 服务名称，OpenClaw 侧会用此标识",
    )

    # 日志
    log_level: str = Field(
        default="INFO",
        description="日志级别: DEBUG / INFO / WARNING / ERROR",
    )
    debug: bool = Field(
        default=False,
        description="是否输出请求/响应详情（生产环境关闭）",
    )

    # 业务
    default_priority: str = Field(
        default="P2",
        description="工单默认紧急程度",
    )

    # 存储后端：memory | sqlite
    storage_backend: str = Field(
        default="memory",
        description="存储后端类型：memory（默认，重启丢失）或 sqlite（文件持久化）",
    )
    storage_db_path: str = Field(
        default="./ticket_mcp.db",
        description="SQLite 数据库文件路径（仅 storage_backend=sqlite 时生效）",
    )

    model_config = SettingsConfigDict(
        env_prefix="TICKET_MCP_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )


@lru_cache
def get_settings() -> Settings:
    """获取全局配置单例。

    使用 lru_cache 避免重复读取环境变量。
    测试时可以通过 settings.cache_clear() 重置。
    """
    return Settings()
