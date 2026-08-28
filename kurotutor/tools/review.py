"""复习工具：查看到期错题、记录复测结果、排下一次复习。

错题闭环的尾巴：到期主动推复习 → 复测 → 掌握/强化。
- ``review_due``：列出该学生到期待复习的错题。
- ``review_answer``：wq_id + mastered(bool) —— 记录复测结果，推进状态/掌握度。
- ``review_schedule``：wq_id —— 为该题排下一次复习任务。
"""

from __future__ import annotations

from typing import Any

from kurotutor.agent.context import ToolContext
from kurotutor.services.review import (
    due_for_student,
    interval_seconds,
    record_review,
    schedule_review_task,
)
from kurotutor.storage import KnowledgePoint, WrongQuestion, session_scope


async def review_due(ctx: ToolContext, kwargs: dict[str, Any]) -> str:
    """列出学生到期待复习的错题。"""
    due = due_for_student(ctx.engine, ctx.student.id)
    if not due:
        return "现在没有到期需要复习的错题，都掌握得不错～"
    with session_scope(ctx.engine) as db:
        lines = [f"有 {len(due)} 道错题到期，建议现在复习："]
        for wq in due:
            kp = db.get(KnowledgePoint, wq.knowledge_point_id) if wq.knowledge_point_id else None
            kp_text = f"（{kp.name}）" if kp else ""
            lines.append(f"- #{wq.id} [{(wq.subject or '')}]{kp_text} {wq.question_text[:50]}")
    lines.append("回复『开始复习』我给你一道一道出，回复『改天』我再顺延。")
    return "\n".join(lines)


async def review_answer(ctx: ToolContext, kwargs: dict[str, Any]) -> str:
    """记录一次复测结果。参数：wq_id, mastered(bool)。"""
    try:
        wq_id = int(kwargs.get("wq_id"))
    except (TypeError, ValueError):
        return "请提供 wq_id（数字）。"
    mastered = str(kwargs.get("mastered")).lower() in ("true", "1", "是", "yes", "对了")
    return record_review(ctx.engine, wq_id=wq_id, mastered=mastered)


async def review_schedule(ctx: ToolContext, kwargs: dict[str, Any]) -> str:
    """为该题排下一次复习任务。参数：wq_id。"""
    try:
        wq_id = int(kwargs.get("wq_id"))
    except (TypeError, ValueError):
        return "请提供 wq_id（数字）。"
    with session_scope(ctx.engine) as db:
        wq = db.get(WrongQuestion, wq_id)
        if wq is None:
            return "错题不存在。"
        student_id = wq.student_id
        times_wrong = wq.times_wrong
        status = wq.status
        kp_id = wq.knowledge_point_id
    mastery = 0.3
    if kp_id is not None:
        with session_scope(ctx.engine) as db:
            kp = db.get(KnowledgePoint, kp_id)
            mastery = kp.mastery if kp else 0.3
    delay = interval_seconds(mastery, times_wrong, status)
    task_id = schedule_review_task(ctx.engine, student_id=student_id, wq_id=wq_id, delay_seconds=delay)
    return f"已为该题排了下一次复习（约 {delay // 86400 + 1} 天后），任务 #{task_id}。"
