"""个性化出题模块测试：绘图（纯逻辑）、生成（mock LLM）、判分闭环（错→错题本+排复习）。"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from sqlmodel import select

from kurotutor.agent.context import ToolContext
from kurotutor.config.loader import load_config_from_data
from kurotutor.services.llm import ChatResult
from kurotutor.services.plot import _compile_expr, plot_functions
from kurotutor.storage import Student, WrongQuestion, session_scope


def _cfg_vision(tmp_path):
    return load_config_from_data(
        {
            "models": {
                "llm": {"provider": "echo", "model": "echo"},
                "vision": {"provider": "openai", "model": "m", "api_key": "k"},
            }
        },
        project_root=tmp_path,
    )


def _cfg(tmp_path):
    return load_config_from_data(
        {"models": {"llm": {"provider": "echo", "model": "echo"}}}, project_root=tmp_path
    )


def _ctx(config, engine) -> ToolContext:
    from kurotutor.agent.sandbox import Sandbox

    with session_scope(engine) as db:
        st = Student(external_id="quizzer", nickname="小明")
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


# ---- 绘图（纯逻辑） -----------------------------------------------------------
def test_compile_expr_basic():
    f = _compile_expr("x^2 - 2*x + 1")
    assert abs(f(3) - 4) < 1e-9
    g = _compile_expr("sin(x) + pi/2")
    assert abs(g(0) - 1.5707963) < 1e-6


def test_compile_expr_rejects_dangerous():
    import pytest

    from kurotutor.core.errors import ToolError

    for bad in ["__import__('os')", "open('/etc/passwd')", "x.__class__", "y + 1"]:
        f = _compile_expr(bad)  # 编译是惰性的，求值时才拒绝
        with pytest.raises(ToolError):
            f(1.0)


def test_plot_functions_writes_png(tmp_path):
    out = tmp_path / "p.png"
    result = plot_functions(str(out), ["x^2-2*x-3", "2*x+1"], x_min=-6, x_max=6, title="测试")
    p = Path(result)
    assert p.exists() and p.stat().st_size > 2000
    from PIL import Image

    assert Image.open(p).size == (900, 640)


# ---- 出题 + 判分闭环（mock LLM） ----------------------------------------------
def _mock_llm_factory(responses: list[str]):
    """按顺序返回内容的假 LLM Provider。"""

    class _Fake:
        def __init__(self):
            self._i = 0

        async def complete(self, messages, *, tools=None, temperature=0.7, max_tokens=None):
            r = responses[min(self._i, len(responses) - 1)]
            self._i += 1
            return ChatResult(content=r)

        async def aclose(self):
            pass

    return _Fake


QUIZ_JSON = (
    '{"questions":['
    '{"text":"解方程 x^2-5x+6=0","answer":"x=2 或 x=3","analysis":"因式分解 (x-2)(x-3)=0",'
    '"knowledge_point":"数学/方程/一元二次方程","difficulty":"easy"},'
    '{"text":"已知 x^2+bx+9=0 有两等根，求 b","answer":"b=±6","analysis":"判别式=0",'
    '"knowledge_point":"数学/方程/判别式","difficulty":"medium"}]}'
)

VERDICT_OK = '{"correct": true, "verdict": "完全正确"}'
VERDICT_BAD = '{"correct": false, "verdict": "漏了一个根"}'


def test_quiz_generate_and_wrong_closure(engine, registry, monkeypatch, tmp_path):
    import kurotutor.tools.quiz as qz

    llm = _mock_llm_factory([QUIZ_JSON, VERDICT_OK, VERDICT_BAD])()
    monkeypatch.setattr(qz, "build_llm_provider", lambda spec: llm)

    cfg = _cfg(tmp_path)
    ctx = _ctx(cfg, engine)

    # 出题
    out = _run(
        registry.execute(ctx, "quiz_generate", {"topic": "一元二次方程", "count": 2, "source": "generate"})
    )
    assert "已准备 2 道题" in out
    assert "先不要透露答案" in out

    # 判分：第 1 题对、第 2 题错
    out2 = _run(registry.execute(ctx, "quiz_check", {"answers": "x=2 和 x=3 | b=6"}))
    assert "第1题：✅ 答对" in out2
    assert "第2题：❌ 答错" in out2
    assert "错题已记入错题本并排了复习" in out2

    with session_scope(engine) as db:
        wrongs = db.exec(select(WrongQuestion)).all()
    assert len(wrongs) == 1
    assert "x^2+bx+9=0" in wrongs[0].question_text
    assert wrongs[0].source == "quiz"


def test_quiz_generate_requires_direction(engine, registry, tmp_path):
    cfg = _cfg(tmp_path)
    ctx = _ctx(cfg, engine)
    out = _run(registry.execute(ctx, "quiz_generate", {}))
    assert "出题方向" in out


def test_quiz_check_without_active_quiz(engine, registry, tmp_path):
    cfg = _cfg(tmp_path)
    ctx = _ctx(cfg, engine)
    out = _run(registry.execute(ctx, "quiz_check", {"answers": "42"}))
    assert "没有进行中的出题" in out


def test_quiz_check_rejects_bad_index(engine, registry, monkeypatch, tmp_path):
    import kurotutor.tools.quiz as qz

    monkeypatch.setattr(qz, "build_llm_provider", lambda spec: _mock_llm_factory([QUIZ_JSON])())
    cfg = _cfg(tmp_path)
    ctx = _ctx(cfg, engine)
    _run(registry.execute(ctx, "quiz_generate", {"topic": "方程", "count": 2, "source": "generate"}))
    out = _run(registry.execute(ctx, "quiz_check", {"answers": "x", "question_index": 5}))
    assert "超出范围" in out


def test_active_quiz_persisted_in_working_context(engine, registry, monkeypatch, tmp_path):
    import kurotutor.tools.quiz as qz
    from kurotutor.storage import WorkingContext

    monkeypatch.setattr(qz, "build_llm_provider", lambda spec: _mock_llm_factory([QUIZ_JSON])())
    cfg = _cfg(tmp_path)
    ctx = _ctx(cfg, engine)
    _run(registry.execute(ctx, "quiz_generate", {"topic": "方程", "count": 2, "source": "generate"}))
    with session_scope(engine) as db:
        wc = db.get(WorkingContext, ctx.student.id)
        data = json.loads(wc.current_problem)
    assert len(data["active_quiz"]) == 2
    assert data["active_quiz"][0]["answer"]

def test_quiz_generate_web_real_questions(engine, registry, monkeypatch, tmp_path):
    """source=web：网上找真题（fake 搜索/抓取/LLM 提取）。"""
    import kurotutor.tools.quiz as qz

    monkeypatch.setattr(qz, "build_llm_provider", lambda spec: _mock_llm_factory([QUIZ_JSON])())

    async def fake_find(llm, **kw):
        return (
            [
                {
                    "text": "2023 某市中考真题：解方程 x^2=9",
                    "answer": "x=±3",
                    "analysis": "直接开平方",
                    "knowledge_point": "数学/方程/平方根",
                    "difficulty": "easy",
                    "source_url": "https://example.com/exam",
                    "real": True,
                }
            ],
            "来自网页：https://example.com/exam",
        )

    monkeypatch.setattr(qz.quiz_svc, "find_real_questions", fake_find)
    cfg = _cfg(tmp_path)
    ctx = _ctx(cfg, engine)
    out = _run(registry.execute(ctx, "quiz_generate", {"topic": "方程", "source": "web"}))
    assert "【真题】" in out
    assert "example.com/exam" in out
    assert "网上没找到" not in out


def test_quiz_generate_web_empty_falls_back(engine, registry, monkeypatch, tmp_path):
    """source=auto：网上找不到 → 自动回退智能生成。"""
    import kurotutor.tools.quiz as qz

    monkeypatch.setattr(qz, "build_llm_provider", lambda spec: _mock_llm_factory([QUIZ_JSON])())

    async def fake_empty(llm, **kw):
        return [], "搜索无结果"

    monkeypatch.setattr(qz.quiz_svc, "find_real_questions", fake_empty)
    cfg = _cfg(tmp_path)
    ctx = _ctx(cfg, engine)
    out = _run(registry.execute(ctx, "quiz_generate", {"topic": "方程", "source": "auto"}))
    assert "智能生成" in out
    assert "已准备 2 道题" in out


def test_weakest_points_pick_lowest_mastery(engine, registry, tmp_path):
    """画像有掌握度数据时，缺省出题方向取最薄弱知识点。"""
    from kurotutor.storage import KnowledgePoint

    cfg = _cfg(tmp_path)
    ctx = _ctx(cfg, engine)
    with session_scope(engine) as db:
        db.add(
            KnowledgePoint(
                student_id=ctx.student.id, subject="数学", chapter="函数", name="二次函数图像",
                mastery=0.2, confidence=0.8,
            )
        )
        db.add(
            KnowledgePoint(
                student_id=ctx.student.id, subject="数学", chapter="方程", name="一元二次方程",
                mastery=0.9, confidence=0.8,
            )
        )
    pts = qz_module_weakest(ctx)
    assert pts and "二次函数图像" in pts[0]


def qz_module_weakest(ctx):
    from kurotutor.tools.quiz import _weakest_points

    return _weakest_points(ctx, n=2)

# ---- 题图校验（mock 视觉） ----------------------------------------------------
def test_verify_quiz_images_relocate_and_drop(engine, monkeypatch, tmp_path):
    import kurotutor.tools.quiz as qz

    class FakeVision:
        """第一次判不匹配（Q1），第二次判匹配（归位到 Q2）。"""

        def __init__(self):
            self.seq = [False, True]

        async def understand(self, path, prompt, *, detail=None):
            return '{"match": ' + str(self.seq.pop(0)).lower() + '}'

        async def aclose(self):
            pass

    monkeypatch.setattr(qz, "build_vision_provider", lambda spec: FakeVision())

    cfg = _cfg_vision(tmp_path)
    ctx = _ctx(cfg, engine)
    img = Path(ctx.workspace) / "qbank_images" / "a.png"
    img.parent.mkdir(parents=True, exist_ok=True)
    from PIL import Image

    Image.new("RGB", (300, 200), "white").save(img, format="PNG")
    questions = [
        {"text": "题一：勾股定理计算", "answer": "", "image_path": str(img)},
        {"text": "题二：二次函数图像", "answer": "", "image_path": ""},
    ]
    _run(qz._verify_quiz_images(ctx, questions))
    assert not questions[0].get("image_path"), "Q1 不匹配应被移走"
    assert questions[1]["image_path"] == str(img), "图应归位到 Q2"


def test_quiz_dedupe_recent(engine, registry, tmp_path):
    """出题防重复：7 天内出过的题（含题集）被剔除，变体写法也认得出。"""
    import asyncio as _asyncio

    import kurotutor.tools.quiz as qz

    cfg = _cfg(tmp_path)
    ctx = _ctx(cfg, engine)

    q1 = {"text": "已知方程 x^2 - 5x + 6 = 0，求两根。", "answer": "2 和 3"}
    qz._remember_quiz(ctx, [q1])
    # 同题变体（全角/符号写法不同）
    kept, dropped = qz._dedupe_questions(ctx, [
        {"text": "已知方程 x2−5x+6=0, 求两根！", "answer": "2 和 3"},
        {"text": "解不等式 2x > 10", "answer": "x > 5"},
    ])
    assert dropped == 1 and len(kept) == 1 and kept[0]["text"].startswith("解不等式")

    # 题集里的题也要避开
    _asyncio.run(registry.execute(ctx, "bank_add", {"question": "计算 (3+5)×2 的结果", "kind": "good"}))
    kept2, dropped2 = qz._dedupe_questions(ctx, [{"text": "计算 (3+5)x2 的结果？", "answer": "16"}])
    assert dropped2 == 1 and kept2 == []
