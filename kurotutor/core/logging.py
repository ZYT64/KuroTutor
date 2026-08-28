"""结构化日志。

替代裸 ``print / console.log``（项目宪法「工程防御规范」）。提供：
- 统一的 ``get_logger(name)``，输出到 stdout（rich 高亮）。
- ``log_level`` 从配置读取，缺省 info。
- 关键结构化字段（事件、学生、工具）由调用方以 kwargs 附带。
"""

from __future__ import annotations

import logging
from typing import Any

import rich.logging

_LEVEL_MAP = {
    "debug": logging.DEBUG,
    "info": logging.INFO,
    "warning": logging.WARNING,
    "error": logging.ERROR,
}

_CONFIGURED = False
_DEFAULT_LEVEL = "info"


def configure_logging(level: str = "info") -> None:
    """配置全局日志（幂等）。日志格式带进程上下文，方便追踪。"""
    global _CONFIGURED
    if _CONFIGURED:
        return
    logger = logging.getLogger("kurotutor")
    logger.setLevel(_LEVEL_MAP.get(level, logging.INFO))
    handler = rich.logging.RichHandler(rich_tracebacks=True, markup=True, show_path=False, show_time=True)
    handler.setFormatter(logging.Formatter("%(name)s - %(message)s"))
    logger.addHandler(handler)
    logger.propagate = False
    _CONFIGURED = True


def set_default_level(level: str) -> None:
    global _DEFAULT_LEVEL
    _DEFAULT_LEVEL = level
    logging.getLogger("kurotutor").setLevel(_LEVEL_MAP.get(level, logging.INFO))


def get_logger(name: str = "kurotutor") -> logging.Logger:
    """取得带标准前缀的日志器，首次调用时以默认级别配置全局。"""
    configure_logging(_DEFAULT_LEVEL)
    return logging.getLogger(f"kurotutor.{name}")


def logger_for(name: str, level: str = "info") -> logging.Logger:
    """为特定组件取日志器，忽略全局级别（供需要逐模块覆盖的场景）。"""
    configure_logging(level)
    return logging.getLogger(f"kurotutor.{name}")


def log_event(
    logger: logging.Logger,
    event: str,
    level: str = "info",
    **fields: Any,
) -> None:
    """记录一个事件，把结构化字段以 key=value 形式附加，便于检索。

    标准 ``logging`` 的 ``info(msg, **kwargs)`` 不接收任意字段，故集中到这里。
    """
    meta = " ".join(f"{k}={v!r}" for k, v in fields.items())
    getattr(logger, level)("%s %s", event, meta)
