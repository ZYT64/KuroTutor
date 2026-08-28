"""业务工具真实闭环测试：工具写入 → 数据库落库 → 工具查询回读。

证明「错题本 / 知识库方法卡片 / 笔记本」工具是真实的存储读写，而非空壳。
"""

from __future__ import annotations

import asyncio

from kurotutor.agent.context import ToolContext
from kurotutor.storage import Student, session_scope


def _make_student(engine) -> Student:
    with session_scope(engine) as db:
        st = Student(external_id="tools-student", nickname="小红", stage="junior")
        db.add(st)
        db.flush()
        return st


def _ctx(config, engine, student) -> ToolContext:
    import logging

    from kurotutor.agent.sandbox import Sandbox

    return ToolContext(
        config=config,
        engine=engine,
        sandbox=Sandbox(config),
        logger=logging.getLogger("test.tools"),
        student=student,
    )


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def test_kb_deposit_and_search(config, engine, registry):
    student = _make_student(engine)
    ctx = _ctx(config, engine, student)
    # 沉淀一张方法卡片
    out = _run(
        registry.execute(
            ctx,
            "kb_deposit",
            {
                "subject": "数学",
                "question_type": "二次函数求根",
                "method": "用求根公式或因式分解",
                "steps": "1.整理成标准式\n2.用求根公式",
                "pitfalls": "忘记 Δ 判断实数根",
                "source": "一次解题",
            },
        )
    )
    assert "沉淀方法卡片" in out
    # 检索到它
    found = _run(registry.execute(ctx, "kb_search", {"subject": "数学", "query": "求根"}))
    assert "二次函数求根" in found
    assert "二次函数求根" in found


def test_wrongbook_add_and_query(config, engine, registry):
    student = _make_student(engine)
    ctx = _ctx(config, engine, student)
    _run(
        registry.execute(
            ctx,
            "wrongbook_add",
            {
                "subject": "数学",
                "knowledge_point": "数学/函数/二次函数",
                "question": "解方程 x^2-4x+3=0",
                "student_answer": "x=1",
                "correct_answer": "x=1,3",
                "analysis": "十字相乘 (x-1)(x-3)=0",
                "error_type": "计算错误",
            },
        )
    )
    rows = _run(registry.execute(ctx, "wrongbook_query", {"subject": "数学"}))
    assert "二次函数" in rows
    assert "待复习" in rows


def test_notebook_add_and_query(config, engine, registry):
    student = _make_student(engine)
    ctx = _ctx(config, engine, student)
    _run(
        registry.execute(
            ctx,
            "notebook_add",
            {
                "subject": "数学",
                "topic": "函数",
                "summary": "函数的定义与性质补课笔记",
            },
        )
    )
    rows = _run(registry.execute(ctx, "notebook_query", {"keyword": "函数"}))
    assert "补课笔记" in rows
