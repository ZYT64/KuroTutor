"""跨页题策略离线测试：缝合、残句自动合并、文档入口、工作上下文共存。"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from kurotutor.agent.context import ToolContext
from kurotutor.config.loader import load_config_from_data
from kurotutor.services.layout import TextLine, stitch_crops
from kurotutor.storage import Student, WorkingContext, session_scope


def _cfg(tmp_path):
    return load_config_from_data(
        {"models": {"llm": {"provider": "echo", "model": "echo"}}}, project_root=tmp_path
    )


def _ctx(config, engine) -> ToolContext:
    from kurotutor.agent.sandbox import Sandbox

    with session_scope(engine) as db:
        st = Student(external_id="crosspage", nickname="同学")
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


# ---- 缝合纯逻辑 --------------------------------------------------------------
def test_stitch_crops_vertical(tmp_path):
    from PIL import Image

    a = tmp_path / "a.png"
    b = tmp_path / "b.png"
    Image.new("RGB", (100, 40), "white").save(a)
    Image.new("RGB", (80, 60), "white").save(b)
    out = tmp_path / "merged.png"
    result = stitch_crops([str(a), str(b)], str(out))
    assert Path(result).exists()
    im = Image.open(result)
    assert im.size == (100, 100), "宽度对齐到最宽（100），高度相加（40+60）"


def test_stitch_crops_empty_raises(tmp_path):
    import pytest

    from kurotutor.core.errors import ProviderError

    with pytest.raises(ProviderError):
        stitch_crops([], str(tmp_path / "x.png"))


# ---- split_photo 跨页自动缝合 ------------------------------------------------
def _seed_tail(ctx, png_path: Path) -> None:
    with session_scope(ctx.engine) as db:
        wc = db.get(WorkingContext, ctx.student.id)
        if wc is None:
            wc = WorkingContext(student_id=ctx.student.id)
            db.add(wc)
        wc.current_problem = json.dumps({"last_split_tail": {"path": str(png_path)}}, ensure_ascii=False)
        db.add(wc)


def _write_png(path: Path, size=(300, 200)) -> Path:
    from PIL import Image

    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size, "white").save(path, format="PNG")
    return path


def _fake_lines():
    """首题号之前有跨页残句的版面：残句 / 1题 / 2题。"""
    return [
        TextLine("温物体放出热量，内能___。", (0, 10, 200, 30)),
        TextLine("吸收热量，内能___。", (0, 40, 200, 60)),
        TextLine("1. 解 x=2", (0, 100, 200, 120)),
        TextLine("2. 求 y=3", (0, 200, 200, 220)),
    ]


def _patch_split(monkeypatch, isp, vision_json):
    from PIL import Image

    from kurotutor.services.layout import plan_question_spans

    async def fake_cut(image_path, out_dir, spec):
        residual, spans = plan_question_spans(_fake_lines())
        img = Image.new("RGB", (300, 260), "white")
        paths = []
        if residual:
            img.crop((0, residual[0], 300, residual[1])).save(f"{out_dir}/q0_residual.png")
            paths.append(f"{out_dir}/q0_residual.png")
        for k, (t, b) in enumerate(spans, 1):
            img.crop((0, t, 300, b)).save(f"{out_dir}/q{k}.png")
            paths.append(f"{out_dir}/q{k}.png")
        return paths

    monkeypatch.setattr(isp, "cut_by_question_numbers", fake_cut)

    class _FakeVision:
        async def understand(self, path, prompt, *, detail=None):
            return vision_json

        async def aclose(self):
            pass

    monkeypatch.setattr(isp, "build_vision_provider", lambda spec: _FakeVision())


def test_split_auto_merges_cross_page(engine, registry, monkeypatch, tmp_path):
    import kurotutor.tools.image_split as isp
    from kurotutor.config.schema import ModelSpec

    _patch_split(monkeypatch, isp, '{"continuous": true}')
    cfg = _cfg(tmp_path)
    cfg.models.layout = ModelSpec(provider="baidu", model="general", api_key="k", client_secret="s")
    cfg.models.vision = ModelSpec(provider="openai", model="m", api_key="k")
    ctx = _ctx(cfg, engine)
    _write_png(Path(ctx.workspace) / "incoming" / "page2.png")
    tail = _write_png(Path(ctx.workspace) / "qbench" / "prev_tail.png")
    _seed_tail(ctx, tail)

    out = _run(registry.execute(ctx, "split_photo", {"path": "incoming/page2.png"}))
    assert "拼合" in out or "缝合" in out, f"应自动缝合：{out}"
    qdir = Path(ctx.workspace) / "qbench"
    merged = list(qdir.glob("q_cross_merged*.png"))
    assert merged, "应产出缝合后的完整题图"
    # 尾块状态更新为本页最后一块
    with session_scope(ctx.engine) as db:
        wc = db.get(WorkingContext, ctx.student.id)
        data = json.loads(wc.current_problem)
    assert "last_split_tail" in data
    assert Path(data["last_split_tail"]["path"]).exists()


def test_split_keeps_separate_when_not_continuous(engine, registry, monkeypatch, tmp_path):
    import kurotutor.tools.image_split as isp
    from kurotutor.config.schema import ModelSpec

    _patch_split(monkeypatch, isp, '{"continuous": false}')
    cfg = _cfg(tmp_path)
    cfg.models.layout = ModelSpec(provider="baidu", model="general", api_key="k", client_secret="s")
    cfg.models.vision = ModelSpec(provider="openai", model="m", api_key="k")
    ctx = _ctx(cfg, engine)
    _write_png(Path(ctx.workspace) / "incoming" / "page2.png")
    tail = _write_png(Path(ctx.workspace) / "qbench" / "prev_tail.png")
    _seed_tail(ctx, tail)

    out = _run(registry.execute(ctx, "split_photo", {"path": "incoming/page2.png"}))
    assert "residual" in out
    assert not list((Path(ctx.workspace) / "qbench").glob("q_cross_merged*")), "不连续时不应缝合"
    assert not (Path(ctx.workspace) / "qbench" / "_probe_merge.png").exists(), "探针图应清理"


def test_merge_crops_tool(engine, registry, tmp_path):
    cfg = _cfg(tmp_path)
    ctx = _cfg_ctx = _ctx(cfg, engine)
    a = _write_png(Path(ctx.workspace) / "qbench" / "t1.png", (200, 50))
    b = _write_png(Path(ctx.workspace) / "qbench" / "t2.png", (200, 70))
    out = _run(registry.execute(ctx, "merge_crops", {"paths": [str(a), str(b)]}))
    assert "拼接" in out
    from PIL import Image

    merged = Path(ctx.workspace) / "qbench" / "merged.png"
    assert merged.exists()
    assert Image.open(merged).size == (200, 120)


def test_merge_crops_needs_two(engine, registry, tmp_path):
    cfg = _cfg(tmp_path)
    ctx = _ctx(cfg, engine)
    out = _run(registry.execute(ctx, "merge_crops", {"paths": ["only_one.png"]}))
    assert "至少两张" in out


def test_working_context_write_preserves_tail_key(engine, tmp_path):
    from kurotutor.tools.solve_photo import _write_working_context

    cfg = _cfg(tmp_path)
    engine = engine  # conftest fixture
    ctx = _ctx(cfg, engine)
    tail = _write_png(Path(ctx.workspace) / "qbench" / "tail.png")
    _seed_tail(ctx, tail)

    _write_working_context(engine, ctx.student.id, {"题干": "1. 解x=2", "答案": "x=2"})
    with session_scope(engine) as db:
        data = json.loads(db.get(WorkingContext, ctx.student.id).current_problem)
    assert data["题干"] == "1. 解x=2", "题目字段正常写入"
    assert "last_split_tail" in data, "切题尾块不应被讲解上下文覆盖"


def test_split_document_rejects_unknown_suffix(engine, registry, tmp_path):
    cfg = _cfg(tmp_path)
    ctx = _ctx(cfg, engine)
    p = _write_png(Path(ctx.workspace) / "incoming" / "doc.xyz")
    out = _run(registry.execute(ctx, "split_document", {"path": str(p)}))
    assert "不支持的文档格式" in out

def test_split_photo_second_call_hits_cache(engine, registry, monkeypatch, tmp_path):
    """同一张图重复切题 → 第二次直接返回缓存（不再重复 OCR/裁剪）。"""
    import kurotutor.tools.image_split as isp
    from kurotutor.config.schema import ModelSpec

    calls = {"n": 0}

    async def fake_cut(image_path, out_dir, spec):
        calls["n"] += 1
        from PIL import Image

        img = Image.new("RGB", (300, 260), "white")
        img.crop((0, 0, 300, 30)).save(f"{out_dir}/q1.png")
        return [f"{out_dir}/q1.png"]

    monkeypatch.setattr(isp, "cut_by_question_numbers", fake_cut)
    cfg = _cfg(tmp_path)
    cfg.models.layout = ModelSpec(provider="baidu", model="general", api_key="k", client_secret="s")
    ctx = _ctx(cfg, engine)
    _write_png(Path(ctx.workspace) / "incoming" / "page_cache.png")

    _run(registry.execute(ctx, "split_photo", {"path": "incoming/page_cache.png"}))
    out2 = _run(registry.execute(ctx, "split_photo", {"path": "incoming/page_cache.png"}))
    assert calls["n"] == 1, f"同图第二次不应重新切（实际切了 {calls['n']} 次）"
    assert "缓存" in out2
    assert "q1.png" in out2
