"""提醒工具测试：设置/列出/取消，payload 字段与推送消费端（handle_message）对齐。"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

from sqlmodel import select

from kurotutor.agent.context import ToolContext
from kurotutor.services import scheduler
from kurotutor.storage import ScheduleTask, Student, TaskStatus, session_scope
from kurotutor.tools.reminder import _parse_time, reminder_cancel, reminder_list, set_reminder


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _make_ctx(config, engine, tmp_path):
    import logging

    from kurotutor.agent.sandbox import Sandbox

    with session_scope(engine) as db:
        st = Student(external_id="rem-student", nickname="小醒", stage="junior")
        db.add(st)
        db.flush()
        sid = st.id
    with session_scope(engine) as db:
        st = db.get(Student, sid)
        return ToolContext(
            config=config, engine=engine, sandbox=Sandbox(config),
            logger=logging.getLogger("test.reminder"), student=st,
        )


def test_set_reminder_relative_time(config, engine, tmp_path):
    ctx = _make_ctx(config, engine, tmp_path)
    out = _run(set_reminder(ctx, {"time": "30分钟后", "text": "背单词"}))
    assert "背单词" in out and "提醒" in out
    with session_scope(engine) as db:
        rows = db.exec(
            select(ScheduleTask).where(
                ScheduleTask.student_id == ctx.student.id,
                ScheduleTask.kind == scheduler.Kinds.REMINDER,
            )
        ).all()
    assert len(rows) == 1
    # payload 字段必须是 message（handle_message 消费端读这个键）
    import json

    assert json.loads(rows[0].payload)["message"] == "背单词"
    fire_at = rows[0].fire_at
    if fire_at.tzinfo is None:  # SQLite 读回 naive，按 UTC 解释
        fire_at = fire_at.replace(tzinfo=UTC)
    delta = fire_at - datetime.now(UTC)
    assert timedelta(minutes=28) < delta < timedelta(minutes=31)


def test_set_reminder_rejects_past_and_missing(config, engine, tmp_path):
    ctx = _make_ctx(config, engine, tmp_path)
    out = _run(set_reminder(ctx, {"time": "2020-01-01T10:00", "text": "早过了"}))
    assert "过去" in out
    out2 = _run(set_reminder(ctx, {"time": "30分钟后"}))
    assert "text" in out2


def test_reminder_list_and_cancel(config, engine, tmp_path):
    ctx = _make_ctx(config, engine, tmp_path)
    _run(set_reminder(ctx, {"time": "1小时后", "text": "做数学作业"}))
    out = _run(reminder_list(ctx, {}))
    assert "做数学作业" in out and "编号" in out
    # 从列表文案里取编号
    task_id = int(out.split("编号 ")[1].split("）")[0])
    out2 = _run(reminder_cancel(ctx, {"task_id": task_id}))
    assert "已取消" in out2
    with session_scope(engine) as db:
        t = db.get(ScheduleTask, task_id)
        assert t.status == TaskStatus.CANCELLED
    # 取消别人的/不存在的编号
    out3 = _run(reminder_cancel(ctx, {"task_id": 999999}))
    assert "不是你的提醒" in out3


def test_parse_time_naive_iso_uses_local_clock():
    import time as _t

    local = datetime.fromtimestamp(_t.time()).astimezone().utcoffset()
    got = _parse_time("2099-01-01T15:00")
    # 无时区 ISO 按本地钟表时间解释：换算回 UTC 后 + 偏移应还原 15:00
    assert (got + local).strftime("%H:%M") == "15:00"


def test_to_local_fixes_naive_utc(config, engine):
    """DB 读回的 naive 时间按 UTC 补时区再转本地（防 14:22 显示成 06:22）。"""
    import time as _t

    from kurotutor.tools.reminder import _to_local

    offset = datetime.fromtimestamp(_t.time()).astimezone().utcoffset() or timedelta()
    naive = datetime(2099, 1, 1, 6, 22)  # 存储 06:22 UTC
    got = _to_local(naive)
    assert got.utcoffset() is not None
    assert got.strftime("%H:%M") == (naive + offset).strftime("%H:%M")
