"""kuro student —— 学生管理（列表 / 详情 / 合规删除）。"""

from __future__ import annotations

from pathlib import Path

import typer
from sqlmodel import delete, select

from kurotutor.cli.common import load_runtime
from kurotutor.storage import (
    CourseInstance,
    CoursePlan,
    KnowledgePoint,
    NotebookEntry,
    ScheduleTask,
    Session,
    Student,
    WrongQuestion,
    session_scope,
)

from . import ui

app = typer.Typer(help="学生管理", add_completion=False)


@app.command("list")
def list_(config: Path | None = typer.Option(None, "--config", "-c")) -> None:
    """列出全部学生。"""
    with session_scope(load_runtime(config).engine) as db:
        students = db.exec(select(Student).order_by(Student.created_at)).all()
    if not students:
        ui.warn("还没有学生。学生在 QQ 私聊互动后自动建档。")
        return
    rows = [
        [str(s.id), s.nickname, s.stage, s.external_id, s.created_at.strftime("%Y-%m-%d")] for s in students
    ]
    ui.list_table(f"学生列表（共 {len(students)} 人）", ["ID", "昵称", "学段", "渠道标识", "建档"], rows)


@app.command("show")
def show(
    student_id: int = typer.Argument(..., help="学生 ID"),
    config: Path | None = typer.Option(None, "--config", "-c"),
) -> None:
    """查看学生详情（画像 / 错题 / 笔记 / 知识点）。"""
    engine = load_runtime(config).engine
    with session_scope(engine) as db:
        student = db.get(Student, student_id)
        if student is None:
            ui.err(f"学生 {student_id} 不存在。")
            ui.info("用 `kuro student list` 查看现有学生。")
            raise typer.Exit(1)
        kps = db.exec(
            select(KnowledgePoint)
            .where(KnowledgePoint.student_id == student_id)
            .order_by(KnowledgePoint.mastery)
        ).all()
        wq_total = len(db.exec(select(WrongQuestion).where(WrongQuestion.student_id == student_id)).all())
        wq_to_review = len(
            db.exec(
                select(WrongQuestion).where(
                    WrongQuestion.student_id == student_id, WrongQuestion.status == "to_review"
                )
            ).all()
        )
        notes = len(db.exec(select(NotebookEntry).where(NotebookEntry.student_id == student_id)).all())

        ui.kv_table(
            f"学生 #{student.id} 详情",
            [
                ("昵称", student.nickname),
                ("学段", student.stage),
                ("渠道标识", student.external_id),
                ("地区/学校", student.region_name or "[dim]（空）[/dim]"),
                ("建档时间", student.created_at.strftime("%Y-%m-%d %H:%M")),
                ("错题总数", f"{wq_total}（待复习 {wq_to_review}）"),
                ("笔记条数", str(notes)),
            ],
        )

        if kps:
            weak_rows = [
                [
                    f"{kp.subject}·{kp.name}",
                    f"{kp.mastery:.0%}",
                    f"{kp.confidence:.0%}",
                    kp.last_practice_at.strftime("%m-%d") if kp.last_practice_at else "[dim]未练习[/dim]",
                ]
                for kp in kps[:10]
            ]
            ui.list_table(
                f"知识点掌握度（薄弱优先，前 {min(10, len(kps))}）",
                ["知识点", "掌握度", "置信度", "最近练习"],
                weak_rows,
            )
        else:
            ui.warn("暂无知识点画像（学生尚未产生解题/练习记录）。")

    ui.info("提示：`kuro student remove` 可删除该学生全部数据（合规）。")


@app.command("remove")
def remove(
    student_id: int = typer.Argument(..., help="学生 ID"),
    yes: bool = typer.Option(False, "--yes", "-y", help="确认删除，跳过二次确认"),
    config: Path | None = typer.Option(None, "--config", "-c"),
) -> None:
    """删除学生及其全部关联数据（隐私合规）。"""
    engine = load_runtime(config).engine
    with session_scope(engine) as db:
        student = db.get(Student, student_id)
        if student is None:
            ui.err(f"学生 {student_id} 不存在。")
            raise typer.Exit(1)
        if not yes:
            ui.warn(
                f"即将删除学生「{student.nickname}」的全部数据：画像、错题、笔记、会话、课程。此操作不可恢复。"
            )
            if not typer.confirm("确认删除？", default=False):
                ui.info("已取消。")
                raise typer.Exit(0)
        # 级联删除（先子后主）
        for model in (
            ScheduleTask,
            NotebookEntry,
            WrongQuestion,
            KnowledgePoint,
            CourseInstance,
            CoursePlan,
            Session,
        ):
            db.exec(delete(model).where(model.student_id == student_id))
        db.delete(student)
        session_id = student_id
    ui.ok(f"已删除学生 #{session_id} 及其全部数据。")
