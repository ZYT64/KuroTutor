"""kuro export —— 数据导出（错题本 / 学习报告）。"""

from __future__ import annotations

from pathlib import Path

import typer
from sqlmodel import select

from kurotutor.cli.common import load_runtime
from kurotutor.storage import (
    KnowledgeCard,
    KnowledgePoint,
    NotebookEntry,
    Student,
    WrongQuestion,
    session_scope,
)

from . import ui

app = typer.Typer(help="数据导出", add_completion=False)


def _out_dir(config) -> Path:
    return Path(config.data_dir) / "exports"


@app.command("wrongbook")
def export_wrongbook(
    student_id: int = typer.Argument(..., help="学生 ID"),
    config: Path | None = typer.Option(None, "--config", "-c"),
) -> None:
    """导出错题本为 Markdown。"""
    rt = load_runtime(config)
    engine = rt.engine
    with session_scope(engine) as db:
        st = db.get(Student, student_id)
        if st is None:
            ui.err(f"学生 {student_id} 不存在。")
            raise typer.Exit(1)
        wqs = db.exec(
            select(WrongQuestion)
            .where(WrongQuestion.student_id == student_id)
            .order_by(WrongQuestion.subject)
        ).all()
        kps = {
            k.id: k.name
            for k in db.exec(select(KnowledgePoint).where(KnowledgePoint.student_id == student_id)).all()
        }
    lines = [f"# {st.nickname} 的错题本\n", f"共 {len(wqs)} 道错题。\n"]
    for wq in wqs:
        kp = kps.get(wq.knowledge_point_id, "")
        lines += [
            f"## 【{wq.subject}】{wq.question_text}",
            f"- 知识点：{kp or '未分类'}",
            f"- 学生作答：{wq.student_answer or '（空）'}",
            f"- 正确答案：{wq.correct_answer or '（空）'}",
            f"- 讲解：{wq.analysis or '（空）'}",
            f"- 错因：{wq.error_type} · 状态：{wq.status} · 错 {wq.times_wrong} 次\n",
        ]
    out = _out_dir(rt.config) / f"wrongbook_{student_id}.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines), encoding="utf-8")
    ui.ok(f"已导出错题本 → {out}")


@app.command("report")
def export_report(
    student_id: int = typer.Argument(..., help="学生 ID"),
    config: Path | None = typer.Option(None, "--config", "-c"),
) -> None:
    """导出学习报告（画像 + 错题统计）。"""
    rt = load_runtime(config)
    engine = rt.engine
    with session_scope(engine) as db:
        st = db.get(Student, student_id)
        if st is None:
            ui.err(f"学生 {student_id} 不存在。")
            raise typer.Exit(1)
        kps = db.exec(
            select(KnowledgePoint)
            .where(KnowledgePoint.student_id == student_id)
            .order_by(KnowledgePoint.mastery)
        ).all()
        status_count: dict[str, int] = {}
        for wq in db.exec(select(WrongQuestion).where(WrongQuestion.student_id == student_id)).all():
            status_count[wq.status] = status_count.get(wq.status, 0) + 1
        notes = len(db.exec(select(NotebookEntry).where(NotebookEntry.student_id == student_id)).all())
        cards = len(db.exec(select(KnowledgeCard).where(KnowledgeCard.student_id == student_id)).all())
    lines = [
        f"# {st.nickname} 学习报告",
        "",
        f"- 学段：{st.stage} · 建档：{st.created_at.strftime('%Y-%m-%d')}",
        f"- 错题：{sum(status_count.values())} 道（待复习 {status_count.get('to_review', 0)} / "
        f"已掌握 {status_count.get('mastered', 0)}）",
        f"- 笔记 {notes} 条 · 方法卡 {cards} 张",
        "",
        "## 薄弱知识点（掌握度低优先）",
        "",
    ]
    if kps:
        for kp in kps[:15]:
            lines.append(f"- {kp.subject}·{kp.name}：掌握 {kp.mastery:.0%}（置信 {kp.confidence:.0%}）")
    else:
        lines.append("- 暂无知识点画像")
    out = _out_dir(rt.config) / f"report_{student_id}.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines), encoding="utf-8")
    ui.ok(f"已导出学习报告 → {out}")
