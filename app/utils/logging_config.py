"""结构化日志配置：structlog + stdlib 桥接，一次配置全局生效。

设计：
- 所有模块继续用标准库 `logging.getLogger(__name__)`，**零改动**；
  structlog 的 ProcessorFormatter 在输出端统一接管格式化。
- `LOG_FORMAT=json`（生产/容器）：每行一个 JSON 对象，便于 ELK/Loki 采集；
- 默认（本地开发）：带颜色的控制台可读输出。
- `structlog.contextvars.merge_contextvars`：请求级上下文（request_id/
  session_id/user_id）由 API 中间件绑定，之后该请求链路里的所有日志
  自动携带这些字段，无需逐条传参。
"""
import logging
import sys

import structlog

from app.conf import config


def setup_logging() -> None:
    """初始化全局日志（幂等，重复调用安全）。"""
    level = getattr(logging, config.LOG_LEVEL.upper(), logging.INFO)
    json_logs = config.LOG_FORMAT.lower() == "json"

    # 输出端共享的处理器链：注入时间戳、级别、logger 名、请求上下文
    shared_processors = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=False),
    ]

    if json_logs:
        renderer = structlog.processors.JSONRenderer(ensure_ascii=False)
    else:
        renderer = structlog.dev.ConsoleRenderer(colors=True)

    formatter = structlog.stdlib.ProcessorFormatter(
        # stdlib 日志记录经过这些处理器后交给 renderer
        foreign_pre_chain=shared_processors,
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            renderer,
        ],
    )

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)

    # 第三方库降噪（httpx 每次 LLM 调用都打 INFO，刷屏）
    for noisy in ("httpx", "httpcore", "faiss.loader"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    # structlog 自身配置（项目内如直接用 structlog.get_logger 也走同一渲染链）
    structlog.configure(
        processors=shared_processors
        + [structlog.stdlib.ProcessorFormatter.wrap_for_formatter],
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )
