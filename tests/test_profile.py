"""画像服务测试：掌握度更新与错题询问策略。"""

from __future__ import annotations

from kurotutor.services.profile import ProfileService, WrongbookPolicy
from kurotutor.storage import Student, session_scope


def _student(engine) -> Student:
    with session_scope(engine) as db:
        st = Student(external_id="profile", nickname="小明")
        db.add(st)
        db.flush()
        return st


def test_correct_raises_mastery(engine):
    st = _student(engine)
    svc = ProfileService(engine)
    m1 = svc.update_after_answer(
        student_id=st.id, subject="数学", chapter="函数", name="二次函数", is_correct=True
    )
    m2 = svc.update_after_answer(
        student_id=st.id, subject="数学", chapter="函数", name="二次函数", is_correct=True
    )
    assert m1 == 0.5 + 0.5 * 0.3  # 0.5*(1-0.3)+1*0.3
    assert m2 > m1


def test_wrong_lowers_mastery(engine):
    st = _student(engine)
    svc = ProfileService(engine)
    m1 = svc.update_after_answer(
        student_id=st.id, subject="数学", chapter="函数", name="二次函数", is_correct=True
    )
    m2 = svc.update_after_answer(
        student_id=st.id, subject="数学", chapter="函数", name="二次函数", is_correct=False
    )
    assert m2 < m1


def test_none_does_not_update(engine):
    st = _student(engine)
    svc = ProfileService(engine)
    m = svc.update_after_answer(student_id=st.id, subject="数学", chapter="", name="几何", is_correct=None)
    assert m == 0.5


def test_policy_skips_when_correct(engine):
    st = _student(engine)
    svc = ProfileService(engine)
    svc.update_after_answer(student_id=st.id, subject="数学", chapter="", name="几何", is_correct=True)
    policy = svc.wrongbook_policy(student_id=st.id, subject="数学", chapter="", name="几何", is_correct=True)
    assert policy == WrongbookPolicy.SKIP


def test_policy_skips_on_careless_unknown_first_wrong(engine):
    st = _student(engine)
    svc = ProfileService(engine)
    # 新知识点、粗心/未知类做错（少打扰：不追问）→ 默认不存
    policy = svc.wrongbook_policy(
        student_id=st.id,
        subject="数学",
        chapter="",
        name="函数",
        is_correct=False,
        difficulty="easy",
        error_category="careless",
    )
    assert policy == WrongbookPolicy.SKIP
    # 未给出难度/犯错类别时也按不打扰处理
    policy2 = svc.wrongbook_policy(
        student_id=st.id, subject="数学", chapter="", name="函数", is_correct=False
    )
    assert policy2 == WrongbookPolicy.SKIP


def test_policy_asks_on_conceptual_medium(engine):
    st = _student(engine)
    svc = ProfileService(engine)
    # 概念性中难题做错 → 主动问，但不直接记
    policy = svc.wrongbook_policy(
        student_id=st.id,
        subject="数学",
        chapter="",
        name="函数",
        is_correct=False,
        difficulty="medium",
        error_category="conceptual",
    )
    assert policy == WrongbookPolicy.ASK


def test_policy_records_on_repeated_wrong(engine):
    from kurotutor.storage import KnowledgePoint, WrongQuestion, WrongStatus

    st = _student(engine)
    # 该知识点已待复习的错题累计 2 次 → 同类连续错 → 直接存
    with session_scope(engine) as db:
        kp = KnowledgePoint(student_id=st.id, subject="数学", name="函数", mastery=0.4, confidence=0.5)
        db.add(kp)
        db.flush()
        for _ in range(2):
            db.add(
                WrongQuestion(
                    student_id=st.id,
                    subject="数学",
                    knowledge_point_id=kp.id,
                    status=WrongStatus.TO_REVIEW,
                    times_wrong=1,
                )
            )
    svc = ProfileService(engine)
    policy = svc.wrongbook_policy(student_id=st.id, subject="数学", chapter="", name="函数", is_correct=False)
    assert policy == WrongbookPolicy.RECORD
