"""学情统计：每日快照 + 效果指标计算。

- :func:`take_daily_snapshot` —— 每日落一行 :class:`MasterySnapshot`（serve 循环驱动），
  供效果周报与 WebUI 面板画趋势线。
- :func:`effect_summary` —— 汇总当前效果指标：复习通过率、掌握度变化（对比 7 天前快照）、
  到期未复习数、错题闭环进度。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlmodel import select

from kurotutor.core import get_logger
from kurotutor.storage import (
    KnowledgePoint,
    MasterySnapshot,
    WrongQuestion,
    WrongStatus,
    session_scope,
)

log = get_logger("stats")


def take_daily_snapshot(engine: Any, student_id: int) -> MasterySnapshot:
    """为一名学生落今日快照（同日重复调用覆盖更新）。"""
    from kurotutor.services.review import due_for_student

    today = datetime.now().astimezone().strftime("%Y-%m-%d")
    with session_scope(engine) as db:
        kps = db.exec(
            select(KnowledgePoint).where(
                KnowledgePoint.student_id == student_id,
                KnowledgePoint.confidence >= 0.2,
            )
        ).all()
        avg = round(sum(k.mastery for k in kps) / len(kps), 4) if kps else 0.0
        wrongs = db.exec(
            select(WrongQuestion).where(WrongQuestion.student_id == student_id)
        ).all()
        mastered = sum(1 for w in wrongs if w.status == WrongStatus.MASTERED)
        due = len(due_for_student(engine, student_id))
        row = db.exec(
            select(MasterySnapshot).where(
                MasterySnapshot.student_id == student_id, MasterySnapshot.date == today
            )
        ).first()
        if row is None:
            row = MasterySnapshot(student_id=student_id, date=today)
        row.avg_mastery = avg
        row.wrong_total = len(wrongs)
        row.wrong_mastered = mastered
        row.due_count = due
        db.add(row)
        db.flush()
        snap = row.model_copy()
    return snap


def effect_summary(engine: Any, student_id: int) -> dict[str, Any]:
    """效果指标汇总（周报正文 + WebUI 仪表盘共用）。全部字段可为 0/空（新学生）。"""
    now = datetime.now(UTC)
    week_ago = now - timedelta(days=7)
    with session_scope(engine) as db:
        wrongs = db.exec(
            select(WrongQuestion).where(WrongQuestion.student_id == student_id)
        ).all()
        snaps = db.exec(
            select(MasterySnapshot)
            .where(MasterySnapshot.student_id == student_id)
            .order_by(MasterySnapshot.date.asc())
            .limit(60)
        ).all()

        reviewed_recent = [
            w
            for w in wrongs
            if w.last_review_at and w.last_review_at >= week_ago.replace(tzinfo=None)
        ]
        review_pass_rate = (
            round(
                sum(1 for w in reviewed_recent if w.status == WrongStatus.MASTERED)
                / len(reviewed_recent),
                2,
            )
            if reviewed_recent
            else None
        )
        total = len(wrongs)
        mastered = sum(1 for w in wrongs if w.status == WrongStatus.MASTERED)
        due_now = sum(1 for w in wrongs if w.status in (WrongStatus.TO_REVIEW, WrongStatus.REVIEWING))

        cur = snaps[-1] if snaps else None
        week_ago_snap = next(
            (s for s in snaps if s.date <= (now - timedelta(days=7)).astimezone().strftime("%Y-%m-%d")),
            None,
        )
        mastery_delta = None
        if cur and week_ago_snap:
            mastery_delta = round(cur.avg_mastery - week_ago_snap.avg_mastery, 3)

    return {
        "review_pass_rate": review_pass_rate,  # 近 7 天复习重做通过率（None=本周没复习）
        "reviewed_count": len(reviewed_recent),
        "wrong_total": total,
        "wrong_mastered": mastered,
        "wrong_open": total - mastered,
        "due_count": due_now,  # 当前待复习数
        "mastery_now": round(cur.avg_mastery, 3) if cur else None,
        "mastery_delta": mastery_delta,  # 对比 7 天前（None=快照不足）
        "snapshot_days": len(snaps),
        "trend": [  # 近 30 天掌握度趋势（面板画线用）
            {"date": s.date, "avg_mastery": s.avg_mastery, "due_count": s.due_count}
            for s in snaps[-30:]
        ],
    }


def effect_summary_text(summary: dict[str, Any]) -> str:
    """把效果指标渲染成周报里的人类可读段落。"""
    lines = []
    if summary.get("review_pass_rate") is not None:
        pct = f"{summary['review_pass_rate']:.0%}"
        lines.append(f"· 本周复习重做 {summary['reviewed_count']} 道，通过率 {pct}")
    if summary.get("mastery_delta") is not None:
        delta = summary["mastery_delta"]
        arrow = "↑" if delta > 0 else ("↓" if delta < 0 else "→")
        lines.append(
            f"· 平均掌握度 {summary['mastery_now']:.0%}，较上周 {arrow} {abs(delta):.1%}"
            if summary.get("mastery_now") is not None
            else f"· 掌握度变化 {delta:+.1%}"
        )
    if summary.get("wrong_total"):
        lines.append(
            f"· 错题闭环：累计 {summary['wrong_total']} 道，已攻克 {summary['wrong_mastered']}，"
            f"待复习 {summary['due_count']}"
        )
    if not lines:
        return "（积累一周数据后，这里会出现复习通过率和掌握度趋势）"
    return "\n".join(lines)

