"""调度器失败重试测试：瞬时失败顺延重试，超过上限才标 failed。

背景：备课/推送依赖外部服务（模型/QQ），一次抖动不该让任务永久失败——
充值/网络恢复后任务应能自动完成。
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

from sqlmodel import select

from kurotutor.services import scheduler
from kurotutor.storage import ScheduleTask, TaskStatus, session_scope


def _boom(task):
    raise RuntimeError("模拟瞬时故障")


def _mk_due_task(engine) -> int:
    return scheduler.create_task(
        engine,
        student_id=1,
        kind=scheduler.Kinds.REMINDER,
        fire_at=datetime.now(UTC) - timedelta(minutes=1),
        payload={"message": "重试测试"},
    )


def test_failure_reschedules_with_backoff_then_marks_failed(config, engine):
    task_id = _mk_due_task(engine)
    handlers = {scheduler.Kinds.REMINDER: _boom}

    base = datetime.now(UTC).replace(tzinfo=None)
    # 第 1~3 次失败：任务保持 pending、顺延 10 分钟、计数递增
    # 每轮把 now 推进到顺延后的 fire_at 之后，模拟时间流逝
    for expected_retry in (1, 2, 3):
        now = base + timedelta(minutes=1 + 10 * (expected_retry - 1))
        scheduler.process_due(engine, handlers, now=now)
        with session_scope(engine) as db:
            t = db.get(ScheduleTask, task_id)
            assert t.status == TaskStatus.PENDING
            assert t.fire_at > t.last_run_at  # 顺延到未来
            assert json.loads(t.payload)["_retry"] == expected_retry

    # 第 4 次到期仍失败 → 放弃，标 failed
    scheduler.process_due(engine, handlers, now=base + timedelta(minutes=31))
    with session_scope(engine) as db:
        t = db.get(ScheduleTask, task_id)
        assert t.status == TaskStatus.FAILED


def test_success_after_retry_completes(config, engine):
    task_id = _mk_due_task(engine)
    calls = {"n": 0}

    def _flaky(task):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("第一次抖动")

    handlers = {scheduler.Kinds.REMINDER: _flaky}
    base = datetime.now(UTC).replace(tzinfo=None)
    scheduler.process_due(engine, handlers, now=base + timedelta(minutes=1))
    scheduler.process_due(engine, handlers, now=base + timedelta(minutes=12))
    with session_scope(engine) as db:
        t = db.get(ScheduleTask, task_id)
        assert t.status == TaskStatus.DONE  # 恢复后自动完成


def test_pending_tasks_not_leaked(config, engine):
    task_id = _mk_due_task(engine)
    handlers = {scheduler.Kinds.REMINDER: lambda t: None}
    scheduler.process_due(engine, handlers)
    with session_scope(engine) as db:
        rows = db.exec(select(ScheduleTask).where(ScheduleTask.id == task_id)).all()
        assert rows[0].status == TaskStatus.DONE
