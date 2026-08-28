"""统一调度器（产品规格书 4.6：所有定时任务本质上都是统一调度）。

备课/提醒/开课/下课/作业提醒/复习推送/周报 全部落地为一条 :class:`ScheduleTask` 记录。
- :func:`create_task` / :func:`cancel_task` 建、撤任务（持久化，重启恢复）。
- :func:`due_tasks` 取出到期且启用的任务。
- :func:`process_due` 按 kind 分发给对应回调，成功后置 done，失败置 failed（不阻塞后续）。
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from sqlmodel import select

from kurotutor.core import get_logger, log_event
from kurotutor.storage import ScheduleTask, TaskStatus, session_scope

log = get_logger("scheduler")


# 任务类型常量
class Kinds:
    PREPARE = "prepare"  # 备课
    REMINDER = "reminder"  # 提醒
    CLASS_START = "class_start"  # 开课
    CLASS_END = "class_end"  # 下课
    HOMEWORK = "homework"  # 作业提醒
    REVIEW = "review"  # 复习推送
    REPORT = "report"  # 周报


def _now() -> datetime:
    return datetime.now(UTC)


def create_task(
    engine: Any, *, student_id: int | None, kind: str, fire_at: datetime, payload: dict | None = None
) -> int:
    """创建一条定时任务，返回其 id。"""
    import json

    with session_scope(engine) as db:
        task = ScheduleTask(
            student_id=student_id,
            kind=kind,
            fire_at=fire_at,
            payload=json.dumps(payload or {}, ensure_ascii=False),
            status=TaskStatus.PENDING,
        )
        db.add(task)
        db.flush()
        return task.id


def cancel_task(engine: Any, task_id: int) -> bool:
    """取消任务（置为 cancelled）。返回是否命中。"""
    with session_scope(engine) as db:
        task = db.get(ScheduleTask, task_id)
        if task is None or task.status != TaskStatus.PENDING:
            return False
        task.status = TaskStatus.CANCELLED
        db.add(task)
        return True


def due_tasks(engine: Any, *, now: datetime | None = None, limit: int = 50) -> list[ScheduleTask]:
    """取出到期、启用、待处理的任务（按到期时间升序）。"""
    now = now or _now()
    with session_scope(engine) as db:
        return db.exec(
            select(ScheduleTask)
            .where(
                ScheduleTask.status == TaskStatus.PENDING,
                ScheduleTask.enabled,
                ScheduleTask.fire_at <= now,
            )
            .order_by(ScheduleTask.fire_at)
            .limit(limit)
        ).all()


def process_due(
    engine: Any, handlers: dict[str, Callable[[ScheduleTask], None]], *, now: datetime | None = None
) -> int:
    """处理所有到期任务。``handlers[kind]`` 处理该类型；成功→done，失败→failed。

    处理在进程内同步执行；如需异步/网络，请自行在 handler 里 await 或提交后台。
    返回处理的任务数。单个任务失败不影响其余任务。
    """
    count = 0
    for task in due_tasks(engine, now=now):
        handler = handlers.get(task.kind)
        if handler is None:
            log_event(log, "no handler for task", kind=task.kind, task_id=task.id)
            _set_status(engine, task.id, TaskStatus.FAILED)
            continue
        try:
            handler(task)
            _set_status(engine, task.id, TaskStatus.DONE)
            count += 1
        except Exception as exc:  # 单个任务失败不阻塞
            log_event(log, "task failed", level="error", task_id=task.id, kind=task.kind, error=repr(exc))
            _set_status(engine, task.id, TaskStatus.FAILED)
    return count


def list_tasks(engine: Any, *, student_id: int | None = None, limit: int = 50) -> list[ScheduleTask]:
    with session_scope(engine) as db:
        stmt = select(ScheduleTask).order_by(ScheduleTask.fire_at.desc()).limit(limit)
        if student_id is not None:
            stmt = (
                select(ScheduleTask)
                .where(ScheduleTask.student_id == student_id)
                .order_by(ScheduleTask.fire_at.desc())
                .limit(limit)
            )
        return db.exec(stmt).all()


def _set_status(engine: Any, task_id: int, status: str) -> None:
    from datetime import UTC, datetime

    with session_scope(engine) as db:
        task = db.get(ScheduleTask, task_id)
        if task is None:
            return
        task.status = status
        task.last_run_at = datetime.now(UTC)
        db.add(task)
