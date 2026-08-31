"""错题本工具：记错题、查错题。

产品规格书 4.2 错题闭环的第一步：采集 → 归类知识点 → 入库。
本模块实现真实的存储写入与查询，供 Agent 在解题/批改后决定是否记入。
"""

from __future__ import annotations

from typing import Any

from sqlmodel import select

from kurotutor.agent.context import ToolContext
from kurotutor.core import get_logger, log_event
from kurotutor.storage import (
    KnowledgePoint,
    WrongQuestion,
    WrongStatus,
    session_scope,
)

# 固定错因标签（6 类，Agent 只能从中选，不能自创）
ERROR_TYPES = {
    "careless": "粗心失误",
    "conceptual": "概念不清",
    "method": "方法不对",
    "computation": "计算错误",
    "forget": "知识遗忘",
    "unknown": "待确认",
}


def _validate_error_type(raw: Any) -> str:
    """校验错因标签，不在固定集内的归为 unknown。"""
    v = (str(raw) if raw else "").strip().lower()
    return v if v in ERROR_TYPES else "unknown"

log = get_logger("wrongbook")

# 知识点归类策略：knowledge_point 是「学科/章节/名称」或纯名称
_KNOWN_SUBJECTS = ("数学", "语文", "英语", "物理", "化学", "生物", "历史", "地理", "政治")


def _split_kp(raw: str) -> tuple[str, str, str]:
    """把知识点字符串解析为 (学科, 章节, 名称)。支持「数学/函数/二次函数」格式。"""
    raw = raw.strip()
    parts = [p for p in raw.split("/") if p]
    if len(parts) >= 3:
        return parts[0], parts[1], "/".join(parts[2:])
    if len(parts) == 2:
        return parts[0], "", parts[1]
    if raw and raw in _KNOWN_SUBJECTS:
        return raw, "", "综合"
    return "综合", "", raw or "未分类"


def record_wrong_question(engine: Any, student_id: int, kwargs: dict[str, Any]) -> str:
    """把一道错题写入错题本（供工具与入口共用）。返回回执文案。"""
    subject = (kwargs.get("subject") or "").strip()
    kp_name = (kwargs.get("knowledge_point") or "").strip()
    question = (kwargs.get("question") or "").strip()
    if not question and not kwargs.get("image_path"):
        return "请提供题目内容（question 或 image_path）。"
    if not kp_name:
        kp_name = "未分类"
    subject, chapter, name = _split_kp(kp_name)
    if not subject:
        subject = (kwargs.get("subject") or "综合").strip() or "综合"

    with session_scope(engine) as db:
        # 去重护栏：同学生 + 同题目文本 已存在则不重复记录（防止 Agent 手动与确定性路径重复）
        dup = db.exec(
            select(WrongQuestion).where(
                WrongQuestion.student_id == student_id,
                WrongQuestion.question_text == question,
            )
        ).first()
        if dup is not None:
            return f"该题已在错题本（#{dup.id}），未重复记录。"

        kp = db.exec(
            select(KnowledgePoint).where(
                KnowledgePoint.student_id == student_id,
                KnowledgePoint.subject == subject,
                KnowledgePoint.name == name,
            )
        ).first()
        if kp is None:
            kp = KnowledgePoint(student_id=student_id, subject=subject, chapter=chapter, name=name)
            db.add(kp)
            db.flush()
        wq = WrongQuestion(
            student_id=student_id,
            subject=subject,
            knowledge_point_id=kp.id,
            source=(kwargs.get("source") or "text"),
            question_text=question,
            image_path=(kwargs.get("image_path") or ""),
            student_answer=(kwargs.get("student_answer") or ""),
            correct_answer=(kwargs.get("correct_answer") or ""),
            analysis=(kwargs.get("analysis") or ""),
            error_type=_validate_error_type(kwargs.get("error_type")),
            status=WrongStatus.TO_REVIEW,
        )
        db.add(wq)
        db.flush()
        kp.last_practice_at = wq.created_at
        wq_id = wq.id
    # 记录后自动排一条到期复习推送任务（幂等）
    try:
        from kurotutor.services.review import schedule_review_task

        schedule_review_task(engine, student_id=student_id, wq_id=wq_id)
    except Exception as exc:  # 排期失败不影响记录本身
        log_event(log, "schedule review failed", level="warning", error=repr(exc))
    return (
        f"已记入错题本（编号 #{wq_id}，学科 {subject}，知识点「{name}」）。"
        f"归因为「{wq.error_type}」，状态待复习。"
    )


async def add_wrong_question(ctx: ToolContext, kwargs: dict[str, Any]) -> str:
    """工具 handler：记入一道错题。"""
    return record_wrong_question(ctx.engine, ctx.student.id, kwargs)


async def query_wrong_questions(ctx: ToolContext, kwargs: dict[str, Any]) -> str:
    """查询错题。参数：subject（可选），status（可选），limit。"""
    subject = (kwargs.get("subject") or "").strip()
    status = (kwargs.get("status") or "").strip()
    limit = int(kwargs.get("limit") or 20)
    with session_scope(ctx.engine) as db:
        stmt = select(WrongQuestion).where(WrongQuestion.student_id == ctx.student.id)
        if subject:
            stmt = stmt.where(WrongQuestion.subject == subject)
        if status:
            stmt = stmt.where(WrongQuestion.status == status)
        stmt = stmt.order_by(WrongQuestion.created_at.desc()).limit(limit)
        rows = db.exec(stmt).all()
        # 回填知识点名称（避免只显示数字 ID 给学生/Agent）
        kp_ids = {wq.knowledge_point_id for wq in rows if wq.knowledge_point_id}
        kp_names: dict[int, str] = {}
        if kp_ids:
            for kp in db.exec(select(KnowledgePoint).where(KnowledgePoint.id.in_(kp_ids))).all():
                kp_names[kp.id] = kp.name
    if not rows:
        return "错题本里暂无符合条件的记录。"
    lines = [f"错题本共 {len(rows)} 条："]
    for wq in rows:
        kp = kp_names.get(wq.knowledge_point_id, "")
        state = {
            WrongStatus.TO_REVIEW: "待复习",
            WrongStatus.REVIEWING: "复习中",
            WrongStatus.MASTERED: "已掌握",
            WrongStatus.ARCHIVED: "已归档",
        }.get(wq.status, wq.status)
        kp_text = f"（{kp}）" if kp else ""
        line = (
            f"- #{wq.id} [{(wq.subject or '')}]{kp_text} "
            f"{wq.question_text[:60]}｜状态：{state}，错 {wq.times_wrong} 次"
        )
        lines.append(line)
    return "\n".join(lines)
