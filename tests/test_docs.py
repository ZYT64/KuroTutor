"""通用文档工具（doc_read/write/edit、pdf_ops、imgprep 预处理）测试。"""

from __future__ import annotations

import asyncio
from pathlib import Path

from kurotutor.agent.context import ToolContext
from kurotutor.config.loader import load_config_from_data
from kurotutor.storage import Student, session_scope


def _cfg(tmp_path):
    return load_config_from_data(
        {"models": {"llm": {"provider": "echo", "model": "echo"}}}, project_root=tmp_path
    )


def _ctx(config, engine) -> ToolContext:
    from kurotutor.agent.sandbox import Sandbox

    with session_scope(engine) as db:
        st = Student(external_id="docuser", nickname="同学")
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


CONTENT = "# 二次函数讲义\n## 知识框架\n- 顶点式 y=a(x-h)^2+k\n配方法求最值。\n"


def test_doc_write_read_roundtrip_docx(engine, registry, tmp_path):
    cfg = _cfg(tmp_path)
    ctx = _ctx(cfg, engine)
    rel = "docs/讲义.docx"
    out = _run(registry.execute(ctx, "doc_write", {"path": rel, "content": CONTENT}))
    assert "已生成" in out
    text = _run(registry.execute(ctx, "doc_read", {"path": rel}))
    assert "二次函数讲义" in text
    assert "顶点式" in text
    assert "配方法求最值" in text


def test_doc_write_read_roundtrip_pptx(engine, registry, tmp_path):
    cfg = _cfg(tmp_path)
    ctx = _ctx(cfg, engine)
    rel = "docs/课件.pptx"
    _run(registry.execute(ctx, "doc_write", {"path": rel, "content": CONTENT}))
    text = _run(registry.execute(ctx, "doc_read", {"path": rel}))
    assert "二次函数讲义" in text
    assert "知识框架" in text


def test_doc_write_read_roundtrip_pdf(engine, registry, tmp_path):
    cfg = _cfg(tmp_path)
    ctx = _ctx(cfg, engine)
    rel = "docs/讲义.pdf"
    _run(registry.execute(ctx, "doc_write", {"path": rel, "content": CONTENT}))
    text = _run(registry.execute(ctx, "doc_read", {"path": rel}))
    assert "二次函数讲义" in text


def test_doc_edit_append_and_replace(engine, registry, tmp_path):
    cfg = _cfg(tmp_path)
    ctx = _ctx(cfg, engine)
    rel = "docs/讲义.docx"
    _run(registry.execute(ctx, "doc_write", {"path": rel, "content": CONTENT}))
    out = _run(
        registry.execute(
            ctx, "doc_edit", {"path": rel, "op": "append", "content": "## 易错点\n- 忘记讨论开口方向"}
        )
    )
    assert "已更新" in out
    text = _run(registry.execute(ctx, "doc_read", {"path": rel}))
    assert "易错点" in text
    # 替换
    out2 = _run(
        registry.execute(
            ctx,
            "doc_edit",
            {
                "path": rel,
                "op": "replace",
                "content": "配方法求最值。",
                "replacement": "配方法与判别式求最值。",
            },
        )
    )
    assert "替换" in out2
    text2 = _run(registry.execute(ctx, "doc_read", {"path": rel}))
    assert "判别式" in text2
    # 替换不存在的文本 → 友好报错
    out3 = _run(
        registry.execute(
            ctx, "doc_edit", {"path": rel, "op": "replace", "content": "不存在的句子xyz", "replacement": "y"}
        )
    )
    assert "没有找到要替换的文本" in out3


def test_pdf_ops_merge_and_extract(engine, registry, tmp_path):
    cfg = _cfg(tmp_path)
    ctx = _ctx(cfg, engine)
    a = "docs/a.pdf"
    b = "docs/b.pdf"
    _run(registry.execute(ctx, "doc_write", {"path": a, "content": "# 文档A\n内容。"}))
    _run(registry.execute(ctx, "doc_write", {"path": b, "content": "# 文档B\n内容。\n第二段。"}))
    out = _run(registry.execute(ctx, "pdf_ops", {"op": "merge", "paths": [a, b], "path": "docs/merged.pdf"}))
    assert "完成" in out
    out2 = _run(
        registry.execute(
            ctx,
            "pdf_ops",
            {"op": "extract", "src": "docs/merged.pdf", "pages": "2", "path": "docs/only_b.pdf"},
        )
    )
    assert "共 1 页" in out2
    text = _run(registry.execute(ctx, "doc_read", {"path": "docs/only_b.pdf"}))
    assert "文档B" in text and "文档A" not in text


def test_pdf_ops_bad_pages(engine, registry, tmp_path):
    cfg = _cfg(tmp_path)
    ctx = _ctx(cfg, engine)
    _run(registry.execute(ctx, "doc_write", {"path": "docs/one.pdf", "content": "# 单页\n内容。"}))
    out = _run(
        registry.execute(
            ctx, "pdf_ops", {"op": "extract", "src": "docs/one.pdf", "pages": "99-100", "path": "docs/x.pdf"}
        )
    )
    assert "没有有效的页码" in out


def test_imgprep_deskew_and_preprocess(tmp_path):
    # 生成一张带 3° 倾斜文字行的图

    from PIL import Image, ImageDraw

    from kurotutor.services.imgprep import estimate_skew_angle, preprocess_image

    img = Image.new("RGB", (800, 600), "white")
    d = ImageDraw.Draw(img)
    for i in range(12):
        d.line([(80, 80 + i * 40), (720, 80 + i * 40)], fill="black", width=3)
    rotated = img.rotate(-3.0, resample=Image.BICUBIC, fillcolor="white")
    src = tmp_path / "skewed.png"
    rotated.save(src)
    angle = estimate_skew_angle(Image.open(src))
    assert abs(angle - 3.0) < 1.2, f"应估计出约 3° 倾斜，实际 {angle}"
    out = preprocess_image(str(src), str(tmp_path / "prep"))
    assert Path(out).exists(), "预处理产物应落盘"
    corrected = Image.open(out)
    angle2 = estimate_skew_angle(corrected)
    assert abs(angle2) < 1.2, f"矫正后应近水平，实际 {angle2}"


def test_imgprep_flat_image_no_rotation(tmp_path):
    from PIL import Image

    from kurotutor.services.imgprep import estimate_skew_angle

    img = Image.new("RGB", (400, 300), "white")
    src = tmp_path / "blank.png"
    img.save(src)
    assert estimate_skew_angle(Image.open(src)) == 0.0, "无文本信号不应旋转"


def test_imgprep_preprocess_missing_file_returns_original(tmp_path):
    from kurotutor.services.imgprep import preprocess_image

    missing = str(tmp_path / "nope.png")
    assert preprocess_image(missing, str(tmp_path)) == missing
