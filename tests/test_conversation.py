"""对话编排（无感分段 / 分层压缩 / 语境打断）测试。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from kurotutor.agent.conversation import (
    Action,
    compress_history,
    decide_segment,
    is_redirect,
    is_topic_switch,
)
from kurotutor.services.llm import ChatMessage


def test_recent_messages_merge_by_default():
    prev = "这道题用因式分解"
    new = "那和等于负数呢？"
    d = decide_segment(prev, new, prev_time=datetime.now(UTC))
    assert d.action == Action.APPEND


def test_time_gap_splits():
    prev = "我们聊二次函数"
    new = "忘了说，今天我考了物理"
    long_ago = datetime.now(UTC) - timedelta(minutes=30)
    d = decide_segment(prev, new, prev_time=long_ago)
    assert d.action == Action.SPLIT


def test_explicit_topic_switch_splits():
    d = decide_segment("讲数学", "换个话题，聊英语吧", prev_time=datetime.now(UTC))
    assert d.action == Action.SPLIT


def test_redirect_merges():
    assert is_redirect("不对，其实应该这样")
    d = decide_segment("其实这里的系数是正数", "不对，应该是负数", prev_time=datetime.now(UTC))
    assert d.action == Action.APPEND


def test_topic_switch_marker():
    assert is_topic_switch("我们聊点别的吧")
    assert not is_topic_switch("继续讲这道题")


def test_compress_history_keeps_recent_and_summarizes_old():
    msgs = [
        ChatMessage(role="user" if i % 2 == 0 else "assistant", content=f"第{i}条消息") for i in range(20)
    ]
    comp = compress_history(msgs, keep=6)
    assert len(comp) <= 7  # 1 摘要 + 6 原文
    assert comp[0].content.startswith("[更早的对话已省略]")
    assert comp[-1].content == "第19条消息"


def test_compress_short_history_unchanged():
    msgs = [ChatMessage(role="user", content="hi")] * 3
    assert compress_history(msgs, keep=6) == msgs


def test_split_carries_background_into_new_session(config, engine, registry, monkeypatch):
    import asyncio

    from sqlmodel import select

    import kurotutor.agent.entry as entry_mod
    from kurotutor.agent.conversation import Action, SegmentDecision
    from kurotutor.agent.entry import MessageEntry
    from kurotutor.storage import Message, Session, Student, session_scope

    # 建学生 + 一个会话 + 一条旧消息
    with session_scope(engine) as db:
        st = Student(external_id="split-mem", nickname="小明")
        db.add(st)
        db.flush()
        sess = Session(student_id=st.id)
        db.add(sess)
        db.flush()
        db.add(Message(session_id=sess.id, role="user", content="讲讲勾股定理"))
        student_id = st.id
        old_session_id = sess.id

    # 强制「无感分段」判定为 SPLIT，模拟长时间隔后自动开新段
    monkeypatch.setattr(entry_mod, "decide_segment", lambda *a, **k: SegmentDecision(Action.SPLIT, "test"))

    entry = MessageEntry(config, registry, engine)
    resp = asyncio.run(entry.handle(student_id=student_id, text="换个话题", session_id=old_session_id))

    assert resp.ok
    with session_scope(engine) as db:
        new_sess = db.exec(
            select(Session).where(Session.student_id == student_id).order_by(Session.id.desc()).limit(1)
        ).first()
        assert new_sess.id != old_session_id  # 确实开了新段
        msgs = db.exec(select(Message).where(Message.session_id == new_sess.id).order_by(Message.id)).all()
    assert any("此前对话背景" in m.content and "勾股定理" in m.content for m in msgs), "新段未携带背景"
