"""入口「确认→记录」闭环测试（确定性，离线）。

验证：存在待确认错题 + 学生确认 → 写入错题本、清除待确认记录。
"""

from __future__ import annotations

import asyncio
import json

from sqlmodel import select

from kurotutor.agent.entry import MessageEntry
from kurotutor.storage import PendingRecord, Student, WrongQuestion, session_scope


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _make_student(engine) -> Student:
    with session_scope(engine) as db:
        st = Student(external_id="confirm", nickname="小明")
        db.add(st)
        db.flush()
        return st


def test_confirmation_records_and_clears(config, engine, registry):
    st = _make_student(engine)
    payload = {
        "subject": "数学",
        "knowledge_point": "数学/函数/一元二次方程求根",
        "question": "解 x^2-5x+6=0",
        "correct_answer": "x=2,3",
        "analysis": "因式分解",
        "source": "photo",
    }
    with session_scope(engine) as db:
        db.add(PendingRecord(student_id=st.id, payload=json.dumps(payload, ensure_ascii=False)))

    entry = MessageEntry(config, registry, engine)
    resp = _run(entry.handle(student_id=st.id, text="好，记下来吧"))

    assert resp.ok
    with session_scope(engine) as db:
        wq = db.exec(select(WrongQuestion).where(WrongQuestion.student_id == st.id)).all()
        pend = db.exec(select(PendingRecord).where(PendingRecord.student_id == st.id)).all()
    assert len(wq) == 1
    assert wq[0].question_text == "解 x^2-5x+6=0"
    assert len(pend) == 0


def test_direct_answer_detection():
    from kurotutor.agent.entry import _wants_direct_answer

    assert _wants_direct_answer("别引导了直接告诉我")
    assert _wants_direct_answer("我卡住了，讲吧")
    assert _wants_direct_answer("直接给答案")
    assert not _wants_direct_answer("我不太确定，你再提示我一下")


def test_confirmation_detection():
    from kurotutor.agent.entry import _is_record_confirmation

    assert _is_record_confirmation("好，记下来吧")
    assert _is_record_confirmation("嗯记一下")
    assert _is_record_confirmation("要记")
    assert not _is_record_confirmation("这道题好难")


def test_no_confirm_when_just_guiding(config, engine, registry):
    # 学生只是表达「难/卡住」，不是确认记录 → 不应触发写入
    st = _make_student(engine)
    entry = MessageEntry(config, registry, engine)
    _run(entry.handle(student_id=st.id, text="这题好难啊"))
    with session_scope(engine) as db:
        wq = db.exec(select(WrongQuestion).where(WrongQuestion.student_id == st.id)).all()
    assert len(wq) == 0
