"""kuro schedule —— 定时任务管理。"""

from __future__ import annotations

from pathlib import Path

import typer
from sqlmodel import select

from kurotutor.cli.common import load_runtime
from kurotutor.storage import ScheduleTask, Student, session_scope

from . import ui

app = typer.Typer(help="定时任务管理", add_completion=False)

_KIND_ZH = {
    "prepare": "备课",
    "reminder": "提醒",
    "class_start": "开课",
    "class_end": "下课",
    "homework": "作业提醒",
    "review": "复习推送",
    "report": "周报",
}


@app.command("list")
def list_task(config: Path | None = typer.Option(None, "--config", "-c")) -> None:
    """列出定时任务。"""
    engine = load_runtime(config).engine
    with session_scope(engine) as db:
        tasks = db.exec(select(ScheduleTask).order_by(ScheduleTask.fire_at.desc()).limit(50)).all()
        rows = []
        for t in tasks:
            st = db.get(Student, t.student_id) if t.student_id else None
            rows.append(
                [
                    str(t.id),
                    _KIND_ZH.get(t.kind, t.kind),
                    st.nickname if st else "（全局）",
                    t.fire_at.strftime("%Y-%m-%d %H:%M"),
                    t.status,
                    "启用" if t.enabled else "停用",
                ]
            )
    if not rows:
        ui.warn("暂无定时任务。记录错题会自动排复习任务。")
        return
    ui.list_table(f"定时任务（共 {len(rows)} 条）", ["ID", "类型", "学生", "到期时间", "状态", "启用"], rows)


@app.command("show")
def show_task(
    task_id: int = typer.Argument(..., help="任务 ID"),
    config: Path | None = typer.Option(None, "--config", "-c"),
) -> None:
    """查看单个任务详情。"""
    engine = load_runtime(config).engine
    with session_scope(engine) as db:
        t = db.get(ScheduleTask, task_id)
        if t is None:
            ui.err(f"任务 {task_id} 不存在。")
            raise typer.Exit(1)
        st = db.get(Student, t.student_id) if t.student_id else None
        ui.kv_table(
            f"任务 #{t.id}",
            [
                ("类型", _KIND_ZH.get(t.kind, t.kind)),
                ("学生", st.nickname if st else "（全局）"),
                ("到期时间", t.fire_at.strftime("%Y-%m-%d %H:%M")),
                ("状态", t.status),
                (
                    "上次运行",
                    t.last_run_at.strftime("%Y-%m-%d %H:%M") if t.last_run_at else "[dim]从未[/dim]",
                ),
                ("载荷", t.payload),
            ],
        )


@app.command("cancel")
def cancel_task(
    task_id: int = typer.Argument(..., help="任务 ID"),
    config: Path | None = typer.Option(None, "--config", "-c"),
) -> None:
    """取消一个待处理任务。"""
    from kurotutor.services.scheduler import cancel_task as _cancel

    engine = load_runtime(config).engine
    if _cancel(engine, task_id):
        ui.ok(f"已取消任务 #{task_id}。")
    else:
        ui.warn(f"任务 #{task_id} 不存在或已非待处理。")
