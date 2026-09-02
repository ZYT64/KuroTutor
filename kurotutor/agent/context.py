"""Agent 运行时上下文。

通过依赖注入把配置、数据库引擎、沙箱、当前学生等基础设施交给工具，
避免工具直接 import 配置/存储而引入硬编码与循环依赖（项目宪法架构防腐规范）。

``ToolContext`` 是一次会话级对象；每个工具 handler 的第一个参数都是它。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sqlalchemy.engine import Engine

from kurotutor.agent.sandbox import Sandbox
from kurotutor.config.schema import AppConfig
from kurotutor.core import get_logger
from kurotutor.storage.models import Student

# 视为图片的扩展名（其余生成物按文件发送）
_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}

_log = get_logger("ctx")


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
    # 本轮工具生成的待发送媒体 [{"path", "kind", "name"}]，渠道层负责投递
    produced_media: list[dict[str, str]] = field(default_factory=list)

    @property
    def workspace(self) -> Any:
        return self.sandbox.workspace

    def student_dir(self, rel: str, *, for_write: bool = True) -> Any:
        """当前学生的专属子目录（workspace/u<id>/<rel>），多用户文件隔离用。"""
        return self.sandbox.student_path(rel, for_write=for_write)

    def emit_media(self, path: str | Path, *, kind: str | None = None) -> bool:
        """登记一个本轮要发给学生的文件（工具生成物 → 渠道投递）。

        kind 缺省按后缀推断（image/file）。文件不存在时记日志返回 False，
        绝不抛异常影响主流程；同一文件只登记一次。
        """
        p = Path(path)
        if not p.exists():
            _log.warning("emit_media: 文件不存在 %s", p)
            return False
        if any(m.get("path") == str(p) for m in self.produced_media):
            return True
        resolved = kind or ("image" if p.suffix.lower() in _IMAGE_EXTS else "file")
        self.produced_media.append(
            {"path": str(p), "kind": resolved, "name": p.name}
        )
        return True
