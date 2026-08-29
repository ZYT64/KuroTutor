"""校本同步工具。"""

from __future__ import annotations

from typing import Any

from kurotutor.agent.context import ToolContext
from kurotutor.services.memory import get_school_progress, set_school_progress


async def school_sync(ctx: ToolContext, kwargs: dict[str, Any]) -> str:
    """校本同步：登记/查看学校教材版本、当前章节、考试安排。参数：op（set/get）、textbook、chapter、exam_date、note。"""
    if ctx.student is None:
        return "当前没有学生上下文。"
    op = str(kwargs.get("op") or "get").strip().lower()
    if op == "set":
        row = set_school_progress(
            ctx.engine,
            ctx.student.id,
            textbook=str(kwargs.get("textbook") or ""),
            chapter=str(kwargs.get("chapter") or ""),
            exam_date=str(kwargs.get("exam_date") or ""),
            note=str(kwargs.get("note") or ""),
        )
        parts = ["已登记校本进度："]
        if row.textbook:
            parts.append(f"教材 {row.textbook}")
        if row.chapter:
            parts.append(f"当前章节「{row.chapter}」")
        if row.exam_date:
            parts.append(f"考试：{row.exam_date}")
        if row.note:
            parts.append(row.note)
        parts.append("之后的出题和备课都会优先贴合学校进度。")
        return "；".join(parts)
    row = get_school_progress(ctx.engine, ctx.student.id)
    if row is None or not (row.chapter or row.textbook or row.exam_date):
        return (
            "还没登记学校进度。告诉我你们的教材版本、现在学到哪一章、有没有临近考试，"
            "我会记下来并让出题和备课跟着学校节奏走。"
        )
    parts = ["校本进度："]
    if row.textbook:
        parts.append(f"教材 {row.textbook}")
    if row.chapter:
        parts.append(f"当前「{row.chapter}」")
    if row.exam_date:
        parts.append(f"考试 {row.exam_date}")
    if row.note:
        parts.append(row.note)
    return "；".join(parts) + f"（更新于 {row.updated_at}）"
