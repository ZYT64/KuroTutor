"""视觉工具测试（离线）：mock 视觉 Provider，不依赖真实网络。

验证 solve_photo / image_understand 的：
- 路径必填校验；
- 沙箱越界拒绝（非工作区图片）；
- 视觉 Provider 被调用并透传结果；
- 未配置视觉模型时给出可读错误。
"""

from __future__ import annotations

import asyncio

from kurotutor.agent.context import ToolContext
from kurotutor.config.loader import load_config_from_data
from kurotutor.storage import Student, session_scope
from kurotutor.tools import solve_photo


def _vision_config(tmp_path):
    """一份带视觉模型的最小配置（llm/vision 都配置，视觉供 mock Provider）。"""
    return load_config_from_data(
        {
            "models": {
                "llm": {"provider": "echo", "model": "echo"},
                "vision": {"provider": "openai", "model": "deepseek-v4-flash-vision-exp", "api_key": "k"},
            }
        },
        project_root=tmp_path,
    )


class _FakeVision:
    def __init__(self, res="【题目】x^2-5x+6=0\n【思路】因式分解"):
        self.res = res
        self.calls = []
        self.closed = False

    async def understand(self, image_path, prompt, *, detail=None):
        self.calls.append((image_path, prompt, detail))
        return self.res

    async def aclose(self):
        self.closed = True


def _ctx(config, engine) -> ToolContext:
    from kurotutor.agent.sandbox import Sandbox

    with session_scope(engine) as db:
        st = Student(external_id="vis", nickname="小明")
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


def test_solve_photo_requires_path(config, engine, registry):
    ctx = _ctx(config, engine)
    out = _run(registry.execute(ctx, "solve_photo", {}))
    assert "请提供" in out


def test_solve_photo_rejects_out_of_workspace(config, engine, registry, tmp_path):
    ctx = _ctx(config, engine)
    outside = tmp_path.parent / "evil.png"
    out = _run(registry.execute(ctx, "solve_photo", {"path": str(outside)}))
    # 沙箱捕获为工具错误信息（不崩溃）
    assert "越出工作区" in out or "沙箱" in out


def test_solve_photo_calls_mock_vision(engine, registry, monkeypatch, tmp_path):
    fake = _FakeVision()
    monkeypatch.setattr(solve_photo, "build_vision_provider", lambda spec: fake)
    ctx = _ctx(_vision_config(tmp_path), engine)
    # 在工作区放一张假图
    path = ctx.sandbox.resolve_path("incoming/mock.png", for_write=True)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"fakepng")
    out = _run(registry.execute(ctx, "solve_photo", {"path": "incoming/mock.png"}))
    assert "【题目】" in out
    assert fake.calls and fake.calls[0][0].endswith("mock.png")
    assert fake.closed is True
    assert "思路" in out


def test_image_understand_uses_prompt(engine, registry, monkeypatch, tmp_path):
    fake = _FakeVision()
    monkeypatch.setattr(solve_photo, "build_vision_provider", lambda spec: fake)
    ctx = _ctx(_vision_config(tmp_path), engine)
    path = ctx.sandbox.resolve_path("incoming/mock.png", for_write=True)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"fakepng")
    out = _run(registry.execute(ctx, "image_understand", {"path": "incoming/mock.png", "prompt": "描述它"}))
    assert fake.calls[0][1] == "描述它"
    assert out


def test_no_vision_config_gives_friendly_error(engine, registry, tmp_path, monkeypatch):
    from kurotutor.config.loader import load_config_from_data

    cfg = load_config_from_data(
        {"models": {"llm": {"provider": "echo", "model": "echo"}}}, project_root=tmp_path
    )
    ctx = _ctx(cfg, engine)
    out = _run(registry.execute(ctx, "solve_photo", {"path": "incoming/mock.png"}))
    assert "未配置视觉模型" in out


# ---- 闭环：确定性记录 / 沉淀 ----------------------------------------------------

_JSON_WRONG = (
    '{"subject":"数学","question_type":"一元二次方程求根",'
    '"question_text":"解 x^2-5x+6=0","correct_answer":"x=2,3",'
    '"method":"因式分解（十字相乘）","steps":"1. 找两数积为6和-5\\n2. (x-2)(x-3)=0",'
    '"pitfalls":"注意符号","student_entry_answer":"x=2","student_correct":false}'
)
_JSON_ASK = (
    '{"subject":"数学","question_type":"一元二次方程求根",'
    '"question_text":"解 x^2-5x+6=0","correct_answer":"x=2,3",'
    '"method":"因式分解","steps":"...","pitfalls":"...","problem_count":1,'
    '"difficulty":"medium","error_category":"conceptual",'
    '"student_entry_answer":"x=2","student_correct":false}'
)


def _write_mock_image(ctx):
    path = ctx.sandbox.resolve_path("incoming/mock.png", for_write=True)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"fakepng")
    return "incoming/mock.png"


def test_solve_photo_no_double_ask(engine, registry, monkeypatch, tmp_path):
    from sqlmodel import select

    from kurotutor.storage import PendingRecord

    fake = _FakeVision(_JSON_ASK)
    monkeypatch.setattr(solve_photo, "build_vision_provider", lambda spec: fake)
    ctx = _ctx(_vision_config(tmp_path), engine)
    rel = _write_mock_image(ctx)
    # 已有一条待确认错题 → 本轮即使符合「询问」也不重复问（最多一问）
    with session_scope(engine) as db:
        db.add(PendingRecord(student_id=ctx.student.id, payload='{"question":"旧题"}'))
    out = _run(registry.execute(ctx, "solve_photo", {"path": rel, "student_answer": "x=2"}))
    assert "【询问】" not in out
    with session_scope(engine) as db:
        pend = db.exec(select(PendingRecord).where(PendingRecord.student_id == ctx.student.id)).all()
    assert len(pend) == 1  # 不重复添加


def test_solve_photo_writes_working_context(engine, registry, monkeypatch, tmp_path):
    from sqlmodel import select

    from kurotutor.storage import WorkingContext

    fake = _FakeVision(_JSON_WRONG)
    monkeypatch.setattr(solve_photo, "build_vision_provider", lambda spec: fake)
    ctx = _ctx(_vision_config(tmp_path), engine)
    rel = _write_mock_image(ctx)
    _run(registry.execute(ctx, "solve_photo", {"path": rel, "student_answer": "x=2"}))
    with session_scope(engine) as db:
        wc = db.exec(select(WorkingContext).where(WorkingContext.student_id == ctx.student.id)).first()
    assert wc is not None
    import json as _json

    data = _json.loads(wc.current_problem)
    assert data.get("question_text") == "解 x^2-5x+6=0"
    assert data.get("method") == "因式分解（十字相乘）"


def test_solve_photo_detects_multi_problem(engine, registry, monkeypatch, tmp_path):
    fake = _FakeVision(_JSON_ASK.replace('"problem_count":1', '"problem_count":3'))
    monkeypatch.setattr(solve_photo, "build_vision_provider", lambda spec: fake)
    ctx = _ctx(_vision_config(tmp_path), engine)
    rel = _write_mock_image(ctx)
    out = _run(registry.execute(ctx, "solve_photo", {"path": rel}))
    assert "3 道题" in out


def test_solve_photo_asks_on_first_wrong(engine, registry, monkeypatch, tmp_path):
    from sqlmodel import select

    from kurotutor.storage import WrongQuestion

    fake = _FakeVision(_JSON_ASK)
    monkeypatch.setattr(solve_photo, "build_vision_provider", lambda spec: fake)
    ctx = _ctx(_vision_config(tmp_path), engine)
    rel = _write_mock_image(ctx)
    out = _run(registry.execute(ctx, "solve_photo", {"path": rel, "student_answer": "x=2"}))
    # 新知识点首次做错 → 询问（不误写错题本）
    assert "询问" in out
    with session_scope(engine) as db:
        wq = db.exec(select(WrongQuestion).where(WrongQuestion.student_id == ctx.student.id)).all()
    assert len(wq) == 0
    assert fake.closed is True


def test_solve_photo_records_and_deposits_when_weak(engine, registry, monkeypatch, tmp_path):
    from sqlmodel import select

    from kurotutor.storage import KnowledgeCard, KnowledgePoint, WrongQuestion

    fake = _FakeVision(_JSON_WRONG)
    monkeypatch.setattr(solve_photo, "build_vision_provider", lambda spec: fake)
    ctx = _ctx(_vision_config(tmp_path), engine)
    rel = _write_mock_image(ctx)
    # 预置一个「薄弱且有历史」的知识点 → 应触发直存
    with session_scope(engine) as db:
        db.add(
            KnowledgePoint(
                student_id=ctx.student.id,
                subject="数学",
                name="一元二次方程求根",
                mastery=0.4,
                confidence=0.6,
            )
        )
    out = _run(registry.execute(ctx, "solve_photo", {"path": rel, "student_answer": "x=2"}))
    assert "已记入错题本" in out
    assert "已沉淀方法卡片" in out
    with session_scope(engine) as db:
        wq = db.exec(select(WrongQuestion).where(WrongQuestion.student_id == ctx.student.id)).all()
        cards = db.exec(select(KnowledgeCard).where(KnowledgeCard.student_id == ctx.student.id)).all()
    assert len(wq) == 1
    assert len(cards) == 1
    assert ctx.state.get("solve_photo", {}).get("policy") == "record"


def test_notebook_photo_parses_and_stores(engine, registry, monkeypatch, tmp_path):
    from sqlmodel import select

    from kurotutor.storage import NotebookEntry
    from kurotutor.tools import notebook as notebook_mod

    fake = _FakeVision('{"subject":"数学","topic":"函数","summary":"函数定义与性质","content":"函数是……"}')
    monkeypatch.setattr(notebook_mod, "build_vision_provider", lambda spec: fake)
    ctx = _ctx(_vision_config(tmp_path), engine)
    rel = _write_mock_image(ctx)
    out = _run(registry.execute(ctx, "notebook_photo", {"path": rel}))
    assert "已解析并存入笔记本" in out
    assert "数学" in out  # 归类到「数学」笔记本
    with session_scope(engine) as db:
        entries = db.exec(select(NotebookEntry).where(NotebookEntry.student_id == ctx.student.id)).all()
    assert len(entries) == 1
    assert entries[0].source == "image"
