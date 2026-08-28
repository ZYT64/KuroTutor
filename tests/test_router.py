"""Router 集成测试：学生建档、echo 回复、消息落库、长内容双模式切分。"""

from __future__ import annotations

import asyncio

from sqlmodel import select

from kurotutor.adapters import Router
from kurotutor.adapters.message import split_text
from kurotutor.storage import Message, Student, session_scope


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def test_handle_creates_student_and_returns_echo(config, engine, registry):
    router = Router(config, registry, engine)
    outs = _run(router.handle("ext-001", "你好老师"))
    assert len(outs) == 1
    assert "已收到" in outs[0].text
    with session_scope(engine) as db:
        st = db.exec(select(Student).where(Student.external_id == "ext-001")).first()
        assert st is not None
        assert st.nickname == ""  # 不复用渠道编号当昵称


def test_messages_persisted_to_db(config, engine, registry):
    router = Router(config, registry, engine)
    _run(router.handle("ext-002", "帮我看看二次函数"))
    with session_scope(engine) as db:
        msgs = db.exec(select(Message)).all()
        assert len(msgs) >= 2  # 至少 user + assistant 各一条
        roles = {m.role for m in msgs}
        assert {"user", "assistant"} <= roles


def test_second_message_reuses_same_student(config, engine, registry):
    router = Router(config, registry, engine)
    _run(router.handle("ext-003", "第一条"))
    _run(router.handle("ext-003", "第二条"))
    with session_scope(engine) as db:
        sts = db.exec(select(Student).where(Student.external_id == "ext-003")).all()
        assert len(sts) == 1  # 同学生不重复建档


def test_long_text_splitter_preserves_reading():
    long = ("这个知识点其实不难，我们先从定义出发，把每一步都讲清楚。") * 60
    chunks = split_text(long, limit=500)
    assert len(chunks) > 1
    assert all(len(c) <= 500 for c in chunks)
    assert "".join(chunks).replace(" ", "") == long.replace(" ", "")


def test_short_text_no_split(config, engine, registry):
    router = Router(config, registry, engine)
    outs = router._to_outbound("简短回复")
    assert len(outs) == 1


def test_long_content_first_time_adds_note_and_remembers(config, engine, registry):
    # 首次超长：首条加引导说明，并记住偏好（split）；第二次不再重复
    long = ("这个知识点我们分步骤讲清楚，每一步都给出依据，避免跳步理解，循序渐进。") * 120
    with session_scope(engine) as db:
        st = Student(external_id="longpref", nickname="小明")
        db.add(st)
        db.flush()
        sid = st.id
    router = Router(config, registry, engine)
    outs = router._to_outbound(long, student_id=sid)
    assert len(outs) > 1
    assert "我先给你分成几条发" in outs[0].text
    # 内部已在首次调用时落定偏好为 split → 第二次不再返回首次
    assert router._set_long_pref_if_unset(sid) is False
