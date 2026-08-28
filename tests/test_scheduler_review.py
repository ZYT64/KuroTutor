"""调度器 + 复习引擎测试。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from kurotutor.services import review, scheduler
from kurotutor.storage import Student, WrongQuestion, WrongStatus, session_scope
from kurotutor.storage.models import KnowledgePoint, ScheduleTask


def _student(engine) -> Student:
    with session_scope(engine) as db:
        st = Student(external_id="sch", nickname="小明")
        db.add(st)
        db.flush()
        return st


def test_interval_grows_with_mastery():
    assert review.interval_seconds(0.9, 1, WrongStatus.REVIEWING) > review.interval_seconds(
        0.2, 1, WrongStatus.REVIEWING
    )


def test_interval_shrinks_with_repeated_wrong():
    assert review.interval_seconds(0.5, 3, WrongStatus.REVIEWING) < review.interval_seconds(
        0.5, 1, WrongStatus.REVIEWING
    )


def test_next_review_at_never_reviewed_is_now(engine):
    st = _student(engine)
    with session_scope(engine) as db:
        wq = WrongQuestion(student_id=st.id, subject="数学", question_text="q", status=WrongStatus.TO_REVIEW)
        db.add(wq)
        db.flush()
        wq_id = wq.id
        db.expire_all()
    with session_scope(engine) as db:
        wq = db.get(WrongQuestion, wq_id)
        assert review.next_review_at(wq, 0.3) <= datetime.now(UTC) + timedelta(seconds=1)


def test_scheduler_create_due_process(engine):
    with session_scope(engine) as db:
        db.add(Student(external_id="s2"))
    due = scheduler.create_task(
        engine, student_id=None, kind="reminder", fire_at=datetime.now(UTC) - timedelta(minutes=1)
    )
    assert len(scheduler.due_tasks(engine)) >= 1
    fired = []

    def handler(task):
        fired.append(task.id)

    n = scheduler.process_due(engine, {"reminder": handler})
    assert n >= 1
    with session_scope(engine) as db:
        t = db.get(ScheduleTask, due)
        assert t.status == "done"
    assert fired


def test_schedule_review_task_is_idempotent(engine):
    with session_scope(engine) as db:
        db.add(Student(external_id="s3"))
    t1 = review.schedule_review_task(engine, student_id=None, wq_id=99)
    t2 = review.schedule_review_task(engine, student_id=None, wq_id=99)
    assert t1 == t2


def test_record_review_advances_status(engine):
    st = _student(engine)
    with session_scope(engine) as db:
        db.add(KnowledgePoint(student_id=st.id, subject="数学", name="函数", mastery=0.4, confidence=0.4))
        wq = WrongQuestion(
            student_id=st.id, subject="数学", question_text="q", status=WrongStatus.REVIEWING, times_wrong=1
        )
        db.add(wq)
        db.flush()
        wq_id = wq.id
    out = review.record_review(engine, wq_id=wq_id, mastered=True)
    assert "掌握" in out
    with session_scope(engine) as db:
        wq = db.get(WrongQuestion, wq_id)
        assert wq.status == WrongStatus.MASTERED
        assert wq.times_wrong == 0


def test_review_push_fires_and_delivers(engine):
    from kurotutor.services.push import make_handlers

    st = _student(engine)
    # 造一条到期错题（从未复习 → 到期）
    with session_scope(engine) as db:
        db.add(
            WrongQuestion(
                student_id=st.id, subject="数学", question_text="错题内容", status=WrongStatus.TO_REVIEW
            )
        )
    scheduler.create_task(
        engine,
        student_id=st.id,
        kind=scheduler.Kinds.REVIEW,
        fire_at=datetime.now(UTC) - timedelta(minutes=1),
    )
    delivered: list[tuple[str, str]] = []
    handlers = make_handlers(engine, lambda ext, text: delivered.append((ext, text)))
    n = scheduler.process_due(engine, handlers)
    assert n >= 1
    assert delivered, "复习推送未触发 deliver"
    ext, text = delivered[0]
    assert ext == "sch"  # Student.external_id
    assert "复习" in text or "到复习时间" in text
