"""M3 模块测试：代码沙箱、入学诊断。"""

from __future__ import annotations

import asyncio
import json

from sqlmodel import select

from kurotutor.agent.context import ToolContext
from kurotutor.config.loader import load_config_from_data
from kurotutor.services.codeexec import run_python
from kurotutor.services.diagnostic import level_of
from kurotutor.storage import KnowledgePoint, Student, session_scope


def _cfg(tmp_path):
    return load_config_from_data(
        {"models": {"llm": {"provider": "echo", "model": "echo"}}}, project_root=tmp_path
    )


def _ctx(config, engine) -> ToolContext:
    from kurotutor.agent.sandbox import Sandbox

    with session_scope(engine) as db:
        st = Student(external_id="m3user", nickname="小新")
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


# ---- 代码沙箱 ----------------------------------------------------------------
def test_sandbox_normal_compute():
    r = run_python("print(12345 ** 2)")
    assert "152399025" in r["stdout"]


def test_sandbox_rejects_import_os():
    import pytest

    from kurotutor.core.errors import ToolError

    with pytest.raises(ToolError):
        run_python("import os\nprint(os.listdir('.'))")


def test_sandbox_rejects_eval():
    import pytest

    from kurotutor.core.errors import ToolError

    with pytest.raises(ToolError):
        run_python("eval('1+1')")


def test_sandbox_timeout_kills_loop():
    import pytest

    from kurotutor.core.errors import ToolError

    with pytest.raises(ToolError) as e:
        run_python("while True: pass", timeout=3)
    assert "超时" in str(e.value)


# ---- 入学诊断 ----------------------------------------------------------------
def test_diagnostic_level_of():
    assert level_of(4, 4) == "优秀"
    assert level_of(2, 4) == "中等"
    assert level_of(0, 4) == "基础薄弱"


def test_diagnostic_full_flow(engine, registry, monkeypatch, tmp_path):
    import kurotutor.tools.diagnostic as dg

    DIAG_JSON = json.dumps(
        {
            "questions": [
                {
                    "text": "计算 12+13",
                    "answer": "25",
                    "analysis": "加法",
                    "knowledge_point": "数学/有理数/加法",
                    "difficulty": "easy",
                },
                {
                    "text": "解方程 2x=10",
                    "answer": "x=5",
                    "analysis": "两边除以2",
                    "knowledge_point": "数学/方程/一元一次",
                    "difficulty": "medium",
                },
            ]
        },
        ensure_ascii=False,
    )
    VERDICTS = ['{"correct": true, "verdict": "对"}', '{"correct": false, "verdict": "算错了"}']

    class _Fake:
        def __init__(self):
            self._i = 0

        async def complete(self, messages, *, tools=None, temperature=0.7, max_tokens=None):
            r = DIAG_JSON if self._i == 0 else VERDICTS[min(self._i - 1, len(VERDICTS) - 1)]
            self._i += 1
            from kurotutor.services.llm import ChatResult

            return ChatResult(content=r)

        async def aclose(self):
            pass

    monkeypatch.setattr(dg, "build_llm_provider", lambda spec: _Fake())
    # 真题链打桩为「无结果」：诊断回退 AI 生成（原链路行为），且单测不触网
    import kurotutor.tools.quiz as qz

    async def _no_real(ctx, llm, **kw):
        return [], "未找到合适真题"

    monkeypatch.setattr(qz, "find_real_questions_via_ctx", _no_real)
    cfg = _cfg(tmp_path)
    ctx = _ctx(cfg, engine)

    out = _run(registry.execute(ctx, "diagnostic_start", {"subject": "数学", "count": 2}))
    assert "入学诊断开始" in out
    out2 = _run(registry.execute(ctx, "diagnostic_submit", {"answers": "25 | x=3"}))
    assert "入学诊断完成" in out2
    assert "答对 1/2" in out2
    # 画像基线：错题对应知识点掌握度被写入（低分）
    with session_scope(engine) as db:
        kps = db.exec(select(KnowledgePoint).where(KnowledgePoint.student_id == ctx.student.id)).all()
    assert kps, "诊断应写入知识点掌握度"
    # 诊断状态清理
    from kurotutor.services.diagnostic import _load_active

    assert _load_active(ctx) is None


# ---- 诊断真题链（adapt_real_questions + diagnostic_start 优先真题） ----------
def test_adapt_real_questions():
    from kurotutor.services.diagnostic import adapt_real_questions

    raw = [
        {"text": "难题", "answer": "42", "difficulty": "hard", "knowledge_point": "数学/函数/二次"},
        {"text": "无答案题", "answer": "", "difficulty": "easy"},  # 判分需要答案 → 剔除
        {"text": "基础题", "answer": "7", "difficulty": "easy"},
        {"text": "中档题", "answer": "x=1", "difficulty": "unknown"},  # 非法难度 → medium
        "不是字典",  # 非法条目 → 剔除
    ]
    out = adapt_real_questions(raw, subject="数学", count=4)
    assert [q["difficulty"] for q in out] == ["easy", "medium", "hard"]  # 由易到难排序
    assert all(q["real"] for q in out)
    assert all(q["answer"] for q in out)
    assert len(out) == 3
    # count 截断
    out2 = adapt_real_questions(raw, subject="数学", count=2)
    assert len(out2) == 2 and out2[0]["difficulty"] == "easy"


def test_diagnostic_start_prefers_real_questions(engine, registry, monkeypatch, tmp_path):
    """真题链有结果时：诊断直接用真题（无答案的剔除），不再 AI 生成。"""
    import kurotutor.tools.diagnostic as dg
    import kurotutor.tools.quiz as qz

    async def fake_chain(ctx, llm, **kw):
        return (
            [
                {"text": "真题A", "answer": "3", "difficulty": "easy", "analysis": "口算"},
                {"text": "真题B", "answer": "x=2", "difficulty": "hard"},
                {"text": "真题C", "answer": "", "difficulty": "easy"},  # 无答案剔除
            ],
            "真题（网上找的）· 来自网页",
        )

    monkeypatch.setattr(qz, "find_real_questions_via_ctx", fake_chain)

    class _NoLLM:  # 不应被调用
        async def complete(self, *a, **kw):
            raise AssertionError("真题足够时不应触发生成")

        async def aclose(self):
            pass

    monkeypatch.setattr(dg, "build_llm_provider", lambda spec: _NoLLM())
    cfg = _cfg(tmp_path)
    ctx = _ctx(cfg, engine)

    out = _run(registry.execute(ctx, "diagnostic_start", {"subject": "数学", "count": 2}))
    assert "入学诊断开始" in out
    assert "真题（网上找的）" in out
    assert "真题A" in out and "真题B" in out
    assert "真题C" not in out  # 无答案被剔除
    # 存入 active 的是适配后的题（2 道、由易到难）
    from kurotutor.services.diagnostic import _load_active

    diag = _load_active(ctx)
    assert diag is not None and len(diag["questions"]) == 2
    assert [q["difficulty"] for q in diag["questions"]] == ["easy", "hard"]


def test_diagnostic_tops_up_with_generated(engine, registry, monkeypatch, tmp_path):
    """真题不足 count：AI 生成补足，混排后仍由易到难。"""
    import json as _json

    import kurotutor.tools.diagnostic as dg
    import kurotutor.tools.quiz as qz

    async def fake_chain(ctx, llm, **kw):
        return ([{"text": "真题A", "answer": "3", "difficulty": "hard"}], "真题（网上找的）· 来自网页")

    monkeypatch.setattr(qz, "find_real_questions_via_ctx", fake_chain)

    GEN = _json.dumps(
        {"questions": [
            {"text": "生成A", "answer": "5", "analysis": "加法",
             "knowledge_point": "数学/数/加", "difficulty": "easy"},
            {"text": "生成B", "answer": "x=1", "analysis": "移项",
             "knowledge_point": "数学/方程/一次", "difficulty": "medium"},
        ]},
        ensure_ascii=False,
    )

    class _Fake:
        def __init__(self):
            self.calls = 0

        async def complete(self, messages, *, tools=None, temperature=0.7, max_tokens=None):
            self.calls += 1
            from kurotutor.services.llm import ChatResult

            return ChatResult(content=GEN)

        async def aclose(self):
            pass

    fake_llm = _Fake()
    monkeypatch.setattr(dg, "build_llm_provider", lambda spec: fake_llm)
    cfg = _cfg(tmp_path)
    ctx = _ctx(cfg, engine)

    _run(registry.execute(ctx, "diagnostic_start", {"subject": "数学", "count": 3}))
    assert fake_llm.calls == 1  # 只为补足缺口调了 1 次生成
    from kurotutor.services.diagnostic import _load_active

    diag = _load_active(ctx)
    texts = [q["text"] for q in diag["questions"]]
    assert texts == ["生成A", "生成B", "真题A"]  # 由易到难：easy→medium→hard（真题排最后）
    assert diag["questions"][0].get("real") is not True and diag["questions"][2].get("real") is True


# ---- 目标/打卡（goal.py 集成） ------------------------------------------------
def test_goal_and_checkin_flow(engine, registry, tmp_path):
    cfg = _cfg(tmp_path)
    ctx = _ctx(cfg, engine)
    out = _run(registry.execute(ctx, "goal_set", {"goal": "期中数学上 90 分", "subject": "数学"}))
    assert "目标已登记" in out
    lst = _run(registry.execute(ctx, "goal_list", {}))
    assert "期中数学上 90 分" in lst
    done = _run(registry.execute(ctx, "goal_update", {"goal_id": 1, "status": "done"}))
    assert "恭喜" in done
    checkin = _run(registry.execute(ctx, "daily_checkin", {"note": "练了两道题"}))
    assert "打卡成功" in checkin
    dup = _run(registry.execute(ctx, "daily_checkin", {"note": ""}))
    assert "已经打过卡" in dup
