"""学习周报服务：统计近 7 天学情 → LLM 润色 → 导出 Word 文档。

统计维度（产品规格书）：掌握度变化、错题统计、复习完成情况、活跃度。
可由 Agent 手动生成，也可经统一调度（kind=report）每周自动推送。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlmodel import select

from kurotutor.core.logging import get_logger, log_event
from kurotutor.storage import KnowledgePoint, Message, Session, Student, WrongQuestion, session_scope

log = get_logger("report")


def _week_ago() -> datetime:
    return datetime.now(UTC) - timedelta(days=7)


def gather_stats(engine: Any, student_id: int) -> dict[str, Any]:
    """近 7 天学情统计（纯数据，LLM 润色前用）。"""
    since = _week_ago()
    with session_scope(engine) as db:
        wrongs = db.exec(
            select(WrongQuestion).where(
                WrongQuestion.student_id == student_id, WrongQuestion.created_at >= since
            )
        ).all()
        kps = db.exec(
            select(KnowledgePoint).where(
                KnowledgePoint.student_id == student_id, KnowledgePoint.confidence >= 0.2
            )
        ).all()
        reviewed = db.exec(
            select(WrongQuestion).where(
                WrongQuestion.student_id == student_id,
                WrongQuestion.last_review_at >= since,
            )
        ).all()
        session_ids = db.exec(
            select(Session.id).where(Session.student_id == student_id)
        ).all()
        user_msgs = (
            db.exec(
                select(Message).where(
                    Message.session_id.in_(session_ids),
                    Message.role == "user",
                    Message.created_at >= since,
                )
            ).all()
            if session_ids
            else []
        )
        student = db.get(Student, student_id)

    by_subject: dict[str, int] = {}
    for w in wrongs:
        by_subject[w.subject] = by_subject.get(w.subject, 0) + 1
    mastered = sum(1 for k in kps if k.mastery >= 0.7)
    weak = sorted(
        (k for k in kps if k.confidence >= 0.2 and k.mastery < 0.5), key=lambda k: k.mastery
    )
    return {
        "days": 7,
        "wrong_total": len(wrongs),
        "wrong_by_subject": by_subject,
        "reviewed_count": len(reviewed),
        "mastered_points": mastered,
        "tracked_points": len(kps),
        "weak_points": [
            {"name": k.name, "subject": k.subject, "mastery": round(k.mastery, 2)} for k in weak[:5]
        ],
        "practice_messages": len(user_msgs),
        "nickname": (student.nickname if student else "") or "同学",
        "stage": (student.stage if student else "") or "junior",
    }


_STATS_PROMPT = (
    "你是学生的私人老师，要给学生{nickname}写一份周报。以下是本周学情统计（JSON）：\n{stats}\n"
    "请写一份亲切、具体的周报：1) 本周亮点（先夸具体的）；2) 错题与薄弱点（点出学科和知识点，"
    "给可执行的改进建议）；3) 下周建议（2-3 条）。语气鼓励但不回避问题，300 字内。"
    "只输出周报正文。"
)


def build_weekly_report(engine: Any, student_id: int, *, llm_spec: Any, workspace: str) -> dict[str, Any]:
    """生成周报：统计 → LLM 润色 → Word 导出。返回 {"text", "path"}。"""
    from kurotutor.services.docs import write_document
    from kurotutor.services.llm import build_llm_provider

    stats = gather_stats(engine, student_id)
    llm = build_llm_provider(llm_spec)

    import asyncio as _asyncio

    async def _run():
        from kurotutor.services.llm import ChatMessage

        prompt = _STATS_PROMPT.format(nickname=stats["nickname"], stats=stats)
        r = await llm.complete([ChatMessage(role="user", content=prompt)], temperature=0.5)
        return (r.content or "").strip()

    try:
        text = _asyncio.run(_run())
    except Exception as exc:
        log_event(log, "weekly report llm failed", level="warning", error=repr(exc))
        text = ""
    finally:
        import contextlib

        with contextlib.suppress(Exception):
            _asyncio.run(llm.aclose())

    if not text:
        # 兜底：纯统计版
        text = (
            f"本周错题 {stats['wrong_total']} 道（{stats['wrong_by_subject'] or '无'}），"
            f"复习 {stats['reviewed_count']} 次，"
            f"掌握知识点 {stats['mastered_points']}/{stats['tracked_points']}。"
        )

    # Word 导出（轻量标记 → docx）
    md_lines = [f"# {stats['nickname']}的学习周报", ""]
    md_lines += [ln for ln in text.splitlines() if ln.strip()]
    md_lines += [
        "",
        "## 本周数据",
        f"- 新增错题：{stats['wrong_total']} 道",
        f"- 错题学科分布：{stats['wrong_by_subject'] or '无'}",
        f"- 完成复习：{stats['reviewed_count']} 次",
        f"- 知识点掌握：{stats['mastered_points']}/{stats['tracked_points']} 达到熟练",
        f"- 互动消息：{stats['practice_messages']} 条",
    ]
    if stats["weak_points"]:
        md_lines.append("- 薄弱点：" + "、".join(w["name"] for w in stats["weak_points"]))
    content = "\n".join(md_lines)
    out_dir = Path(workspace) / "exports"
    out_dir.mkdir(parents=True, exist_ok=True)
    now = datetime.now(UTC).astimezone()
    out = str(out_dir / f"学习周报_{now:%Y%m%d}.docx")
    try:
        write_document(out, content)
    except Exception as exc:
        log_event(log, "weekly report docx failed", level="warning", error=repr(exc))
        out = ""
    return {"text": text, "path": out, "stats": stats}
