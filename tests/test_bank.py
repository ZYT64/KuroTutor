"""题集（bank_add/list/remove）与录入策略提示词测试。"""

from __future__ import annotations

import asyncio
from pathlib import Path

from sqlmodel import select

from kurotutor.agent.context import ToolContext
from kurotutor.agent.prompts import build_system_prompt
from kurotutor.config.loader import load_config_from_data
from kurotutor.storage import QuestionItem, Student, session_scope


def _cfg(tmp_path):
    return load_config_from_data(
        {"models": {"llm": {"provider": "echo", "model": "echo"}}}, project_root=tmp_path
    )


def _ctx(config, engine) -> ToolContext:
    from kurotutor.agent.sandbox import Sandbox

    with session_scope(engine) as db:
        st = Student(external_id="banker", nickname="小收")
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


def test_bank_add_and_list(engine, registry, tmp_path):
    cfg = _cfg(tmp_path)
    ctx = _ctx(cfg, engine)
    out = _run(
        registry.execute(
            ctx,
            "bank_add",
            {
                "question": "已知 f(x)=x²+2x，求最小值",
                "kind": "good",
                "subject": "数学",
                "knowledge_point": "数学/函数/二次函数",
                "reason": "经典二次函数最值模型",
            },
        )
    )
    assert "已把这道好题录入题集" in out
    assert "经典二次函数最值模型" in out

    listing = _run(registry.execute(ctx, "bank_list", {}))
    assert "题集共 1 条" in listing
    assert "已知 f(x)=x²+2x" in listing


def test_bank_add_error_kind(engine, registry, tmp_path):
    cfg = _cfg(tmp_path)
    ctx = _ctx(cfg, engine)
    out = _run(
        registry.execute(
            ctx,
            "bank_add",
            {"question": "解方程 2x+5=11", "kind": "error", "reason": "学生移项符号错了"},
        )
    )
    assert "错题" in out
    with session_scope(engine) as db:
        rows = db.exec(select(QuestionItem)).all()
    assert len(rows) == 1 and rows[0].kind == "error"


def test_bank_add_dedup(engine, registry, tmp_path):
    cfg = _cfg(tmp_path)
    ctx = _ctx(cfg, engine)
    q = {"question": "计算 (3+5)×2 的结果", "kind": "good"}
    _run(registry.execute(ctx, "bank_add", q))
    again = _run(registry.execute(ctx, "bank_add", q))
    assert "不重复收录" in again
    with session_scope(engine) as db:
        rows = db.exec(select(QuestionItem)).all()
    assert len(rows) == 1


def test_bank_add_fuzzy_dedup(engine, registry, tmp_path):
    """模糊去重：标点/空白/全半角/大小写差异不误判为新题。"""
    cfg = _cfg(tmp_path)
    ctx = _ctx(cfg, engine)
    first = {"question": "已知方程 x^2 - 5x + 6 = 0，求两根。", "kind": "good"}
    _run(registry.execute(ctx, "bank_add", first))
    # OCR 转写变体：全角标点、空格、符号写法不同，但语义同题
    variant = {"question": "已知方程 x2−5x+6=0, 求两根！", "kind": "good"}
    out = _run(registry.execute(ctx, "bank_add", variant))
    assert "不重复收录" in out
    with session_scope(engine) as db:
        rows = db.exec(select(QuestionItem)).all()
    assert len(rows) == 1


def test_bank_add_requires_content(engine, registry, tmp_path):
    cfg = _cfg(tmp_path)
    ctx = _ctx(cfg, engine)
    out = _run(registry.execute(ctx, "bank_add", {"kind": "good"}))
    assert "至少一项" in out


def test_bank_add_invalid_kind(engine, registry, tmp_path):
    cfg = _cfg(tmp_path)
    ctx = _ctx(cfg, engine)
    out = _run(registry.execute(ctx, "bank_add", {"question": "x", "kind": "super"}))
    assert "error（错题）或 good（好题）" in out


def test_bank_list_empty_state(engine, registry, tmp_path):
    cfg = _cfg(tmp_path)
    ctx = _ctx(cfg, engine)
    out = _run(registry.execute(ctx, "bank_list", {}))
    assert "题集还是空的" in out


def test_bank_remove(engine, registry, tmp_path):
    cfg = _cfg(tmp_path)
    ctx = _ctx(cfg, engine)
    _run(registry.execute(ctx, "bank_add", {"question": "好题A", "kind": "good"}))
    out = _run(registry.execute(ctx, "bank_remove", {"question_id": 1}))
    assert "已从题集移除" in out
    out2 = _run(registry.execute(ctx, "bank_remove", {"question_id": 1}))
    assert "不在你的题集里" in out2


def test_bank_extract_renders_pdf(engine, registry, tmp_path):
    from PIL import Image

    cfg = _cfg(tmp_path)
    ctx = _ctx(cfg, engine)
    img = Path(ctx.workspace) / "qbench" / "good_q.png"
    img.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (400, 120), "white").save(img, format="PNG")
    _run(
        registry.execute(
            ctx,
            "bank_add",
            {"question": "好题：一题多解的几何题", "kind": "good", "subject": "数学", "image_path": str(img)},
        )
    )
    _run(
        registry.execute(
            ctx,
            "bank_add",
            {"question": "错题：二次函数符号错", "kind": "error", "subject": "数学"},
        )
    )

    out = _run(registry.execute(ctx, "bank_extract", {"subject": "数学"}))
    assert "提取 2 道题" in out
    pdf_path = out.split("组卷完成（PDF）：")[-1].strip().split("\n")[0]
    p = Path(pdf_path)
    assert p.exists() and p.stat().st_size > 1000, "PDF 应真实生成且非空"

    # 空结果友好提示
    out2 = _run(registry.execute(ctx, "bank_extract", {"keyword": "不存在的关键词"}))
    assert "没有符合条件" in out2


def test_bank_extract_docx_format(engine, registry, tmp_path):
    cfg = _cfg(tmp_path)
    ctx = _ctx(cfg, engine)
    _run(
        registry.execute(
            ctx,
            "bank_add",
            {"question": "错题：一元二次方程判别式漏讨论", "kind": "error", "subject": "数学"},
        )
    )
    out = _run(registry.execute(ctx, "bank_extract", {"format": "docx"}))
    assert "组卷完成（DOCX）" in out
    p = Path(out.split("组卷完成（DOCX）：")[-1].strip().split("\n")[0])
    assert p.exists() and p.suffix == ".docx" and p.stat().st_size > 500
    # 无效格式拒绝
    out2 = _run(registry.execute(ctx, "bank_extract", {"format": "pptx"}))
    assert "只支持 pdf 或 docx" in out2


def test_prompt_contains_bank_policy():
    prompt = build_system_prompt(None)
    assert "【题集录入策略】" in prompt
    assert "完全没懂" in prompt  # 自动录入档
    assert "好题" in prompt
