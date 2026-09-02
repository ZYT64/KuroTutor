"""M2 模块测试：学习周报、校本同步、事实记忆提取。"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from kurotutor.agent.context import ToolContext
from kurotutor.config.loader import load_config_from_data
from kurotutor.services.llm import ChatResult
from kurotutor.services.memory import extract_and_store_facts, get_school_progress
from kurotutor.storage import KnowledgePoint, Student, WrongQuestion, session_scope


def _cfg(tmp_path, *, with_llm=True):
    models = {}
    if with_llm:
        models["llm"] = {"provider": "echo", "model": "echo"}
    return load_config_from_data({"models": models}, project_root=tmp_path)


def _ctx(config, engine) -> ToolContext:
    from kurotutor.agent.sandbox import Sandbox

    with session_scope(engine) as db:
        st = Student(external_id="m2user", nickname="小二")
        db.add(st)
        db.flush()
        sid = st.id
    with session_scope(engine) as db:
        student = db.get(Student, sid)
    return ToolContext(config=config, engine=engine, sandbox=Sandbox(config), logger=None, student=student)


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# ---- 学习周报 ----------------------------------------------------------------
def test_weekly_report_generates_docx(engine, registry, monkeypatch, tmp_path):
    cfg = _cfg(tmp_path)
    ctx = _ctx(cfg, engine)
    # 造点数据：1 道错题 + 1 个薄弱知识点
    with session_scope(engine) as db:
        db.add(
            WrongQuestion(
                student_id=ctx.student.id, subject="数学", question_text="解方程 x-1=0",
                error_type="conceptual",
            )
        )
        db.add(
            KnowledgePoint(
                student_id=ctx.student.id, subject="数学", chapter="方程", name="一元一次方程",
                mastery=0.3, confidence=0.8,
            )
        )

    class _Fake:
        async def complete(self, messages, *, tools=None, temperature=0.7, max_tokens=None):

            return ChatResult(content="本周你很努力！错题 1 道已弄懂，下周我们练方程应用。")

        async def aclose(self):
            pass


    monkeypatch.setattr("kurotutor.services.llm.build_llm_provider", lambda spec: _Fake())
    out = _run(registry.execute(ctx, "weekly_report", {}))
    assert "周报文档已生成" in out
    # 周报 Word 应登记为待发送媒体（文件真实存在）
    assert len(ctx.produced_media) == 1
    assert ctx.produced_media[0]["kind"] == "file"
    docx = ctx.produced_media[0]["path"]
    assert Path(docx).exists() and Path(docx).stat().st_size > 500


def test_report_subscribe_and_unsubscribe(engine, registry, tmp_path):
    cfg = _cfg(tmp_path)
    ctx = _ctx(cfg, engine)
    out = _run(registry.execute(ctx, "report_subscribe", {"op": "subscribe"}))
    assert "已订阅" in out
    out2 = _run(registry.execute(ctx, "report_subscribe", {"op": "subscribe"}))
    assert "无需重复订阅" in out2
    out3 = _run(registry.execute(ctx, "report_subscribe", {"op": "unsubscribe"}))
    assert "已退订" in out3


# ---- 校本同步 ----------------------------------------------------------------
def test_school_sync_set_and_get(engine, registry, tmp_path):
    cfg = _cfg(tmp_path)
    ctx = _ctx(cfg, engine)
    out = _run(
        registry.execute(
            ctx,
            "school_sync",
            {"op": "set", "textbook": "人教版", "chapter": "二次函数", "exam_date": "10 月中考"},
        )
    )
    assert "已登记校本进度" in out
    out2 = _run(registry.execute(ctx, "school_sync", {"op": "get"}))
    assert "人教版" in out2 and "二次函数" in out2
    row = get_school_progress(ctx.engine, ctx.student.id)
    assert row is not None and row.chapter == "二次函数"


def test_school_sync_empty_state(engine, registry, tmp_path):
    cfg = _cfg(tmp_path)
    ctx = _ctx(cfg, engine)
    out = _run(registry.execute(ctx, "school_sync", {"op": "get"}))
    assert "还没登记学校进度" in out


# ---- 事实记忆提取（自进化） ----------------------------------------------------
def test_extract_and_store_facts(engine, monkeypatch, tmp_path):

    class _Fake:
        async def complete(self, messages, *, tools=None, temperature=0.7, max_tokens=None):

            return ChatResult(
                content=json.dumps(
                    {"facts": ["学生的目标是期末数学上 110 分", "学生下周三有数学月考"]},
                    ensure_ascii=False,
                )
            )

        async def aclose(self):
            pass

    monkeypatch.setattr("kurotutor.services.llm.build_llm_provider", lambda spec: _Fake())
    cfg = _cfg(tmp_path)
    ctx = _ctx(cfg, engine)
    new = extract_and_store_facts(
        ctx.engine, ctx.student.id, cfg.models.llm,
        "学生：老师，我给自己定了个目标，期末数学要上 110 分。老师：好的，有目标真好。"
        "学生：对了老师，下周三有数学月考，我有点紧张。老师：别怕，我们把这周错题过一遍就行。",
    )
    assert len(new) == 2
    with session_scope(engine) as db:
        st = db.get(Student, ctx.student.id)
    assert "110 分" in (st.note or ""), "事实应写入 Student.note（prompt 自动注入）"
    # 去重：相同事实不再追加
    new2 = extract_and_store_facts(
        ctx.engine, ctx.student.id, cfg.models.llm,
        "学生：我想期末数学上 110 分。",
    )
    assert new2 == [], "重复事实不应再写入"

def test_count_user_messages_via_sessions(engine, tmp_path):
    """计数经 Session 关联（Message 无 student_id 字段，回归修复）。"""
    from kurotutor.services.memory import count_user_messages
    from kurotutor.storage import Message, Session

    cfg = _cfg(tmp_path)
    ctx = _ctx(cfg, engine)
    with session_scope(engine) as db:
        sess = Session(student_id=ctx.student.id)
        db.add(sess)
        db.flush()
        for i in range(3):
            db.add(Message(session_id=sess.id, role="user", content=f"m{i}"))
    assert count_user_messages(engine, ctx.student.id) == 3
    assert count_user_messages(engine, ctx.student.id + 999) == 0
