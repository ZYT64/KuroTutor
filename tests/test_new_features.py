"""M1 剩余功能离线测试：批改 / 讲义 / 切题 / 网络 / 向量 / 语音（mock Provider）。"""

from __future__ import annotations

import asyncio
from pathlib import Path

from kurotutor.agent.context import ToolContext
from kurotutor.config.loader import load_config_from_data
from kurotutor.services.llm import ChatResult
from kurotutor.storage import Student, WrongQuestion, session_scope


def _cfg(tmp_path, *, with_vision=True, with_llm=True):
    models = {}
    if with_llm:
        models["llm"] = {"provider": "echo", "model": "echo"}
    if with_vision:
        models["vision"] = {"provider": "openai", "model": "m", "api_key": "k"}
    return load_config_from_data({"models": models}, project_root=tmp_path)


def _ctx(config, engine) -> ToolContext:
    from kurotutor.agent.sandbox import Sandbox

    with session_scope(engine) as db:
        st = Student(external_id="newfeat", nickname="小明")
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


def _write_img(ctx, name="mock.png"):
    p = ctx.sandbox.resolve_path(f"incoming/{name}", for_write=True)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"img")
    return f"incoming/{name}"


# ---- 作业批改 ----------------------------------------------------------------
def test_grade_homework(engine, registry, monkeypatch, tmp_path):
    from sqlmodel import select

    import kurotutor.tools.grade_homework as gh

    class _FakeVision:
        async def understand(self, path, prompt, *, detail=None):
            return (
                '{"items":['
                '{"question":"1. 解x^2=4","student_answer":"x=2","correct_answer":"x=2,-2",'
                '"is_correct":false,"error_type":"conceptual","knowledge_point":"数学/方程/二次方程"},'
                '{"question":"2. 1+1","student_answer":"2","correct_answer":"2","is_correct":true}'
                '],"summary":"整体不错，注意多解"}'
            )

        async def aclose(self):
            pass

    monkeypatch.setattr(gh, "build_vision_provider", lambda spec: _FakeVision())
    ctx = _ctx(_cfg(tmp_path), engine)
    rel = _write_img(ctx, "hw.png")
    out = _run(registry.execute(ctx, "grade_homework", {"path": rel}))
    assert "共批改 2 题" in out
    assert "答错 1 题" in out
    with session_scope(engine) as db:
        rows = db.exec(select(WrongQuestion).where(WrongQuestion.student_id == ctx.student.id)).all()
    assert len(rows) == 1  # 只错了 1 题


# ---- 讲义生成 ----------------------------------------------------------------
def test_lecture_gen_writes_file(engine, registry, monkeypatch, tmp_path):
    import kurotutor.services.lecture as lecture_mod

    class _FakeLLM:
        async def complete(self, messages, *, tools=None, temperature=0.7, max_tokens=None):
            return ChatResult(content="# 二次函数讲义\n\n## 知识框架\n\n内容。\n\n## 易错点\n\n注意符号。")

        async def aclose(self):
            pass

    monkeypatch.setattr(lecture_mod, "build_llm_provider", lambda spec: _FakeLLM())
    ctx = _ctx(_cfg(tmp_path), engine)
    out = _run(registry.execute(ctx, "lecture_gen", {"topic": "二次函数"}))
    assert "已生成讲义" in out
    assert "二次函数" in out
    # 落盘在工作区 lectures/ 下
    assert (Path(ctx.workspace) / "lectures" / "二次函数.md").exists()


# ---- 切题 --------------------------------------------------------------------
def test_split_photo(engine, registry, monkeypatch, tmp_path):
    import kurotutor.tools.image_split as isp

    class _FakeVision:
        async def understand(self, path, prompt, *, detail=None):
            return '{"problem_count":2,"questions":["1. 解x=2","2. 求y=3"]}'

        async def aclose(self):
            pass

    monkeypatch.setattr(isp, "build_vision_provider", lambda spec: _FakeVision())
    ctx = _ctx(_cfg(tmp_path), engine)
    rel = _write_img(ctx, "page.png")
    out = _run(registry.execute(ctx, "split_photo", {"path": rel}))
    assert "2 道题" in out
    assert "1. 解x=2" in out


def test_split_photo_crops_questions(engine, registry, monkeypatch, tmp_path):
    import kurotutor.tools.image_split as isp
    from kurotutor.services.layout import TextLine

    class _FakeLayout:
        async def layout(self, image_path):
            return [
                TextLine("1. 解 x=2", (0, 0, 100, 20)),
                TextLine("由因式分解得步骤", (0, 40, 120, 60)),
                TextLine("2. 求 y=3", (0, 100, 100, 120)),
            ]

        async def aclose(self):
            pass

    monkeypatch.setattr(isp, "build_layout_provider", lambda spec: _FakeLayout())
    # 配置 layout 为「云端百度」并给真实 Key（这里仅用于触发版面路径）
    from kurotutor.config.schema import ModelSpec

    cfg = _cfg(tmp_path)
    cfg.models.layout = ModelSpec(provider="baidu", model="general", api_key="k", client_secret="s")
    ctx = _ctx(cfg, engine)
    from PIL import Image as PILImage

    real = ctx.sandbox.resolve_path("incoming/page.png", for_write=True)
    real.parent.mkdir(parents=True, exist_ok=True)
    PILImage.new("RGB", (400, 300), "white").save(real)
    out = _run(registry.execute(ctx, "split_photo", {"path": "incoming/page.png"}))
    assert "切成 2 块" in out
    assert "qbench" in out

    qdir = Path(ctx.workspace) / "qbench"
    pngs = list(qdir.glob("q*.png"))
    assert len(pngs) == 2


# ---- 网络（URL 校验） --------------------------------------------------------
def test_web_url_validation_rejects_localhost():
    from kurotutor.core.errors import KuroError
    from kurotutor.tools.web import _validate_url

    assert _validate_url("https://example.com/a") == "https://example.com/a"
    for bad in ("http://localhost/", "http://127.0.0.1/", "ftp://x"):
        try:
            _validate_url(bad)
            raise AssertionError(f"应拒绝：{bad}")
        except KuroError:
            pass


# ---- 语料库 ----------------------------------------------------------------
def test_corpus_add_and_search(engine, registry, tmp_path):
    from sqlmodel import select

    from kurotutor.storage import CorpusEntry

    ctx = _ctx(_cfg(tmp_path), engine)
    _run(
        registry.execute(
            ctx,
            "corpus_add",
            {"subject": "数学", "title": "二次函数讲义", "content": "顶点式 y=a(x-h)^2+k 的顶点是 (h,k)。"},
        )
    )
    _run(
        registry.execute(
            ctx,
            "corpus_add",
            {"subject": "物理", "title": "牛顿第二定律", "content": "F=ma，加速度与力成正比。"},
        )
    )
    found = _run(registry.execute(ctx, "corpus_search", {"subject": "数学", "query": "顶点"}))
    assert "顶点式" in found
    with session_scope(engine) as db:
        rows = db.exec(select(CorpusEntry).where(CorpusEntry.student_id == ctx.student.id)).all()
    assert len(rows) == 2


# ---- 向量 / 重排（纯逻辑） ---------------------------------------------------
def test_group_lines_into_questions():
    from kurotutor.services.layout import TextLine, group_lines_into_questions, layout_text

    lines = [
        TextLine("1. 解 x^2=4", (0, 0, 100, 20)),
        TextLine("步骤：开方", (0, 40, 120, 60)),
        TextLine("2. 求 y=3", (0, 100, 100, 120)),
        TextLine("3. 计算 1+1", (0, 160, 120, 180)),
    ]
    groups = group_lines_into_questions(lines)
    assert len(groups) == 3
    texts = layout_text(groups)
    assert texts[0].startswith("1.")
    assert "步骤" not in texts[0]  # 解答/步骤行被剔除，只留题目
    assert texts[2].startswith("3.")


def test_group_by_gap_when_no_markers():
    from kurotutor.services.layout import TextLine, group_lines_into_questions

    # 无题号，靠间距聚类
    lines = [
        TextLine("题意描述", (0, 0, 200, 20)),
        TextLine("继续描述", (0, 30, 200, 50)),
        TextLine("另一题内容", (0, 200, 200, 220)),
    ]
    groups = group_lines_into_questions(lines)
    assert len(groups) == 2


def test_cosine_and_keyword_rerank():
    from kurotutor.kb.embeddings import cosine
    from kurotutor.kb.reranker import rerank_by_keyword

    assert abs(cosine([1, 0], [1, 0]) - 1.0) < 1e-6
    assert abs(cosine([1, 0], [0, 1])) < 1e-6
    items = [
        {"question_type": "二次函数求根", "method": "因式分解"},
        {"question_type": "诗歌鉴赏", "method": "意象"},
    ]
    ranked = rerank_by_keyword("求根", items)
    assert len(ranked) == 1
    assert ranked[0]["question_type"] == "二次函数求根"
