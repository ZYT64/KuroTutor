"""讲义生成工具：把某一主题生成为 Markdown 讲义文件（长内容模式①）。"""

from __future__ import annotations

from typing import Any

from kurotutor.agent.context import ToolContext
from kurotutor.services.lecture import generate_lecture


async def lecture_gen(ctx: ToolContext, kwargs: dict[str, Any]) -> str:
    """生成讲义。参数：topic（主题），subject（学科，可选）。"""
    return await generate_lecture(ctx, kwargs)
