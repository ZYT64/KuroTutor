"""定时课堂测试：排课→备课→开课→课后闭环→系列课→应急改期/取消。"""

from __future__ import annotations

import asyncio
import json
from datetime import timedelta
from pathlib import Path

from sqlmodel import select

from kurotutor.agent.context import ToolContext
from kurotutor.config.loader import load_config_from_data
from kurotutor.services import scheduler
from kurotutor.storage import (
    CourseInstance,
    CourseStatus,
    ScheduleTask,
    Student,
    TaskStatus,
    session_scope,
)
from kurotutor.tools.registry import build_default_registry


class _FakeLLM:
    """按序返回内容的假 LLM（outline → 备课教案 → 课堂总结）。"""

    def __init__(self, responses):
        self.responses = list(responses)

    async def complete(self, messages, *, tools=None, temperature=0.7, max_tokens=None):
        r = self.responses.pop(0) if self.responses else "{}"
        from kurotutor.services.llm import ChatResult

        return ChatResult(content=r)

    async def aclose(self):
        pass


OUTLINE = json.dumps(
    {
        "outline": [
            {"title": "二次函数图像入门", "topic": "抛物线定义与图像"},
            {"title": "二次函数最值问题", "topic": "区间最值"},
        ]
    },
    ensure_ascii=False,
)
LESSON = json.dumps(
    {
        "goal": ["掌握抛物线图像", "会求顶点"],
        "points": ["顶点式", "对称轴", "开口方向"],
        "example": {"text": "求 y=x²-2x-3 的顶点", "analysis": "配方法"},
        "practice": ["练习1", "练习2"],
        "homework": "课本 P35 第 3 题",
    },
    ensure_ascii=False,
)
SUMMARY = "今天学了抛物线图像，作业课本 P35 第 3 题，加油！"

START = "2026-09-06T15:00:00"  # 周日 15:00（本机时区）


def _cfg(tmp_path):
    return load_config_from_data(
        {"models": {"llm": {"provider": "echo", "model": "echo"}}}, project_root=tmp_path
    )


def _cfg_vision(tmp_path):
    return load_config_from_data(
        {"models": {"llm": {"provider": "echo", "model": "echo"}}}, project_root=tmp_path
    )


def _ctx(config, engine) -> ToolContext:
    from kurotutor.agent.sandbox import Sandbox

    with session_scope(engine) as db:
        st = Student(external_id="courier", nickname="小课")
        db.add(st)
        db.flush()
        sid = st.id
    with session_scope(engine) as db:
        student = db.get(Student, sid)
    return ToolContext(config=config, engine=engine, sandbox=Sandbox(config), logger=None, student=student)


def _call(ctx, name, kwargs):
    """跑一次工具调用（独立事件循环）。"""
    reg = build_default_registry()
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(reg.execute(ctx, name, kwargs))
    finally:
        loop.close()


def test_create_single_course(engine, tmp_path):
    cfg = _cfg(tmp_path)
    ctx = _ctx(cfg, engine)
    out = _call(ctx, "schedule_class", {"subject": "数学", "topic": "二次函数", "start_at": START})
    assert "排课成功" in out
    with session_scope(engine) as db:
        insts = db.exec(select(CourseInstance)).all()
        tasks = db.exec(select(ScheduleTask)).all()
    assert len(insts) == 1
    assert len(tasks) == 3, "备课/开课/下课 三条任务"
    kinds = {t.kind for t in tasks}
    assert {scheduler.Kinds.PREPARE, scheduler.Kinds.CLASS_START, scheduler.Kinds.CLASS_END} == kinds
    # fire_at 为 UTC：本地 15:00（UTC+8）→ 开课 07:00、备课 06:00
    prep = next(t for t in tasks if t.kind == scheduler.Kinds.PREPARE)
    assert prep.fire_at.hour == 6


def test_create_series_course(engine, monkeypatch, tmp_path):
    import kurotutor.tools.classroom as cr

    monkeypatch.setattr(cr, "build_llm_provider", lambda spec: _FakeLLM([OUTLINE]))
    cfg = _cfg(tmp_path)
    ctx = _ctx(cfg, engine)
    out = _call(
        ctx,
        "schedule_class",
        {"subject": "数学", "topic": "二次函数", "start_at": START, "series_count": 2, "goal": "期末 110 分"},
    )
    assert "系列课大纲" in out
    assert "第1节" in out and "第2节" in out
    with session_scope(engine) as db:
        insts = db.exec(select(CourseInstance).order_by(CourseInstance.start_at)).all()
    assert len(insts) == 2
    assert insts[1].start_at - insts[0].start_at == timedelta(weeks=1), "系列课每周一节"


def test_prepare_and_end_closure(engine, monkeypatch, tmp_path):
    import kurotutor.services.classroom as cl

    fake = _FakeLLM([LESSON, SUMMARY])
    monkeypatch.setattr("kurotutor.services.llm.build_llm_provider", lambda spec: fake)

    cfg = _cfg(tmp_path)
    ctx = _ctx(cfg, engine)
    _call(ctx, "schedule_class", {"subject": "数学", "topic": "二次函数", "start_at": START})

    with session_scope(engine) as db:
        iid = db.exec(select(CourseInstance)).first().id

    # 备课（显式注入 workspace/llm_spec，避免测试读写真实配置）
    loop = asyncio.new_event_loop()
    try:
        result = loop.run_until_complete(
            asyncio.to_thread(
                cl.prepare_course, engine, iid, workspace=str(tmp_path / "ws"), llm_spec=cfg.models.llm
            )
        )
    finally:
        loop.close()
    assert Path(result["lecture_path"]).exists()
    md = Path(result["lecture_path"]).read_text(encoding="utf-8")
    assert "课堂讲义" in md and "课后作业" in md
    with session_scope(engine) as db:
        assert db.get(CourseInstance, iid).status == CourseStatus.READY

    text = cl.start_class_text(engine, iid)
    assert "上课啦" in text and "二次函数" in text
    with session_scope(engine) as db:
        assert db.get(CourseInstance, iid).status == CourseStatus.ONGOING

    text2 = cl.end_class(engine, iid, llm_spec=cfg.models.llm)
    assert "下课" in text2 and SUMMARY[:10] in text2
    with session_scope(engine) as db:
        assert db.get(CourseInstance, iid).status == CourseStatus.FINISHED


def test_series_end_schedules_next(engine, monkeypatch, tmp_path):
    import kurotutor.services.classroom as cl
    import kurotutor.tools.classroom as cr

    fake = _FakeLLM([OUTLINE, LESSON, SUMMARY])
    monkeypatch.setattr(cr, "build_llm_provider", lambda spec: fake)
    monkeypatch.setattr("kurotutor.services.llm.build_llm_provider", lambda spec: fake)

    cfg = _cfg(tmp_path)
    ctx = _ctx(cfg, engine)
    _call(
        ctx,
        "schedule_class",
        {"subject": "数学", "topic": "二次函数", "start_at": START, "series_count": 2, "goal": "期末冲刺"},
    )

    with session_scope(engine) as db:
        insts = db.exec(select(CourseInstance).order_by(CourseInstance.start_at)).all()
        first_id, second_id = insts[0].id, insts[1].id

    cl.end_class(engine, first_id, llm_spec=cfg.models.llm)

    with session_scope(engine) as db:
        tasks = db.exec(
            select(ScheduleTask).where(
                ScheduleTask.kind == scheduler.Kinds.PREPARE, ScheduleTask.status == TaskStatus.PENDING
            )
        ).all()
    payload_iids = [
        json.loads(t.payload or "{}").get("instance_id") for t in tasks if t.payload
    ]
    assert second_id in payload_iids, "系列课下课时应为第二节自动挂备课任务"


def test_reschedule_and_cancel(engine, tmp_path):
    cfg = _cfg(tmp_path)
    ctx = _ctx(cfg, engine)
    _call(ctx, "schedule_class", {"subject": "数学", "topic": "二次函数", "start_at": START})
    with session_scope(engine) as db:
        iid = db.exec(select(CourseInstance)).first().id

    NEW = "2026-09-07T10:00:00"
    out = _call(ctx, "reschedule_class", {"course_id": iid, "new_start": NEW})
    assert "已改期" in out
    with session_scope(engine) as db:
        inst = db.get(CourseInstance, iid)
        tasks = db.exec(select(ScheduleTask)).all()
    assert inst.start_at.hour == 2, "本地 10:00（UTC+8）→ UTC 02:00"
    changed = [t for t in tasks if t.kind == scheduler.Kinds.CLASS_START]
    assert changed[0].fire_at.hour == 2, "任务时间应随改期移动"

    out2 = _call(ctx, "cancel_class", {"course_id": iid})
    assert "已取消" in out2
    with session_scope(engine) as db:
        assert db.get(CourseInstance, iid).status == CourseStatus.CANCELLED
        pending = db.exec(select(ScheduleTask).where(ScheduleTask.status == TaskStatus.PENDING)).all()
    assert not pending, "取消后相关待执行任务应全部取消"
