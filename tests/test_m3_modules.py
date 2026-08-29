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
