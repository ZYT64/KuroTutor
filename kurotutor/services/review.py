"""复习引擎（产品规格书 4.2 错题闭环的「间隔重复」）。

错题闭环的最后一段：采集 → 记录 → **按间隔到期主动推送复习 → 复测 → 掌握/强化**。
- :func:`interval_seconds`：由掌握度/错误次数/复习状态 计算下一次间隔（纯函数，可测）。
- :func:`next_review_at`：某道错题的到期时间。
- :func:`due_for_student`：该学生现阶段到期应复习的错题。
- :func:`record_review`：一次复测后的状态推进（掌握则 mastered，未掌握则强化）。
- :func:`schedule_review_task`：为某道错题排一条到期复习推送任务（交给调度器）。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlmodel import select

from kurotutor.core import get_logger
from kurotutor.services import scheduler
from kurotutor.storage import KnowledgePoint, WrongQuestion, WrongStatus, session_scope
from kurotutor.storage.models import ScheduleTask, TaskStatus

log = get_logger("review")


# 单位（秒）
def _day(n: float) -> int:
    return int(n * 86400)


def interval_seconds(mastery: float, times_wrong: int, status: str) -> int:
    """间隔重复：掌握度越高间隔越长，错误次数越多间隔越短。"""
    if status == WrongStatus.MASTERED:
        return _day(30)
    base_days = 1 if status in (WrongStatus.TO_REVIEW, WrongStatus.REVIEWING) else 2
    factor = 1.0 + max(0.0, min(1.0, mastery)) * 4.0  # mastery 0..1 → 1..5
    if times_wrong > 1:
        factor *= 0.5  # 反复错 → 缩短间隔强化
    return max(_day(1), int(base_days * 86400 * factor))


def _mastery_of(engine: Any, student_id: int, kp_id: int | None) -> float:
    if kp_id is None:
        return 0.5
    with session_scope(engine) as db:
        kp = db.get(KnowledgePoint, kp_id)
        return kp.mastery if kp else 0.5


def next_review_at(wq: WrongQuestion, mastery: float) -> datetime:
    """计算一题的到期时间；从未复习过的（status=to_review 且无 last_review_at）视为立即到期。"""
    now = datetime.now(UTC)
    if wq.status == WrongStatus.MASTERED:
        return now + timedelta(seconds=interval_seconds(mastery, wq.times_wrong, wq.status))
    if wq.last_review_at is None:
        # 从未复习：尽快进入复习（首次）
        return now
    interval = interval_seconds(mastery, wq.times_wrong, wq.status)
    return wq.last_review_at + timedelta(seconds=interval)


def due_for_student(engine: Any, student_id: int, *, now: datetime | None = None) -> list[WrongQuestion]:
    """该学生到期应复习的错题（排除已掌握/已归档）。"""
    now = now or datetime.now(UTC)
    with session_scope(engine) as db:
        rows = db.exec(
            select(WrongQuestion).where(
                WrongQuestion.student_id == student_id,
                WrongQuestion.status.in_([WrongStatus.TO_REVIEW, WrongStatus.REVIEWING]),
            )
        ).all()
    due: list[WrongQuestion] = []
    for wq in rows:
        if wq.last_review_at is None:
            due.append(wq)  # 从未复习：到期
            continue
        mastery = _mastery_of(engine, student_id, wq.knowledge_point_id)
        if next_review_at(wq, mastery) <= now:
            due.append(wq)
    return due


def record_review(engine: Any, *, wq_id: int, mastered: bool) -> str:
    """记录一次复测结果并推进状态。返回回执。"""
    now = datetime.now(UTC)
    with session_scope(engine) as db:
        wq = db.get(WrongQuestion, wq_id)
        if wq is None:
            return "错题不存在。"
        wq.last_review_at = now
        if mastered:
            wq.times_wrong = max(0, wq.times_wrong - 1)
            wq.status = WrongStatus.MASTERED if wq.times_wrong == 0 else WrongStatus.REVIEWING
        else:
            wq.times_wrong += 1
            wq.status = WrongStatus.REVIEWING
        times_wrong = wq.times_wrong
        status = wq.status
        kp_id = wq.knowledge_point_id
        student_id = wq.student_id
        subject = wq.subject
        db.add(wq)
    # 同步画像掌握度（开新会话，不依赖悬挂对象）
    if kp_id is not None:
        from kurotutor.services.profile import ProfileService

        ProfileService(engine).update_after_answer(
            student_id=student_id, subject=subject, chapter="", name="复习", is_correct=mastered
        )
    return f"已标记复习结果：{'掌握 ✅' if mastered else '还需强化 ⚠️'}（第 {times_wrong} 次，状态 {status}）"


def schedule_review_task(
    engine: Any, *, student_id: int, wq_id: int, delay_seconds: int | None = None
) -> int:
    """为一道错题排一条到期复习任务（幂等：已有待处理任务则不重复）。"""
    import json

    with session_scope(engine) as db:
        existing = db.exec(
            select(ScheduleTask).where(
                ScheduleTask.student_id == student_id,
                ScheduleTask.kind == scheduler.Kinds.REVIEW,
                ScheduleTask.status == TaskStatus.PENDING,
            )
        ).all()
        for t in existing:
            try:
                if json.loads(t.payload or "{}").get("wq_id") == wq_id:
                    return t.id  # 已排过，不重复
            except (ValueError, TypeError):
                continue
    if delay_seconds is None:
        delay_seconds = interval_seconds(0.3, 1, WrongStatus.TO_REVIEW)
    fire_at = datetime.now(UTC) + timedelta(seconds=delay_seconds)
    return scheduler.create_task(
        engine,
        student_id=student_id,
        kind=scheduler.Kinds.REVIEW,
        fire_at=fire_at,
        payload={"wq_id": wq_id},
    )
