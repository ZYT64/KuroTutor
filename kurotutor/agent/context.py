"""Agent 运行时上下文。

通过依赖注入把配置、数据库引擎、沙箱、当前学生等基础设施交给工具，
避免工具直接 import 配置/存储而引入硬编码与循环依赖（项目宪法架构防腐规范）。

``ToolContext`` 是一次会话级对象；每个工具 handler 的第一个参数都是它。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.engine import Engine

from kurotutor.agent.sandbox import Sandbox
from kurotutor.config.schema import AppConfig
from kurotutor.storage.models import Student


@dataclass
class ToolContext:
    """注入给工具函数的上下文。"""

    config: AppConfig
    engine: Engine
    sandbox: Sandbox
    logger: Any
    student: Student | None = None
    session_id: int | None = None
    # 单次会话内的临时状态（如当前错题上下文），供多步工具协同
    state: dict[str, Any] = field(default_factory=dict)

    @property
    def workspace(self) -> Any:
        return self.sandbox.workspace
