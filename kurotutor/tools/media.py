"""媒体发送工具：把工作区里的文件作为图片或文件卡片发给学生。

Agent 生成物（函数图/文档/讲义/周报）由各工具自动登记发送；当学生需要
其他工作区文件（如切题后的题图、处理结果）时，由 Agent 显式调用本工具。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from kurotutor.agent.context import ToolContext
from kurotutor.core.errors import ToolError


async def send_media(ctx: ToolContext, kwargs: dict[str, Any]) -> str:
    """把工作区文件发给学生。参数：path（工作区内文件路径）、kind（image/file，缺省自动判断）。"""
    path = str(kwargs.get("path") or "").strip()
    if not path:
        return "请提供要发送的文件路径（path）。"
    if path.startswith(("http://", "https://")):
        return "send_media 只能发本地文件，网络文件请先下载到工作区。"
    try:
        resolved = ctx.sandbox.resolve_path(path, for_write=False)
    except ToolError as exc:
        return f"发送失败：{exc}"
    kind = str(kwargs.get("kind") or "").strip().lower() or None
    ok = ctx.emit_media(resolved, kind=kind)
    if not ok:
        return f"文件不存在或无法访问：{path}"
    return f"已登记发送：{Path(resolved).name}，将随本条回复一起发给学生。"
