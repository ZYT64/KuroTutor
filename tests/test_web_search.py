"""web_search 供应商选项测试（默认 Bing / 可选 Tavily，均离线 mock）。"""

from __future__ import annotations

import asyncio

from kurotutor.agent.context import ToolContext
from kurotutor.config.loader import load_config_from_data
from kurotutor.storage import Student, session_scope


def _cfg(tmp_path, *, search=None):
    models = {"llm": {"provider": "echo", "model": "echo"}}
    if search:
        models["search"] = search
    return load_config_from_data({"models": models}, project_root=tmp_path)


def _ctx(config, engine) -> ToolContext:
    from kurotutor.agent.sandbox import Sandbox

    with session_scope(engine) as db:
        st = Student(external_id="searcher", nickname="同学")
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


def test_tavily_provider_parses_results(engine, registry, monkeypatch, tmp_path):
    import kurotutor.tools.web as web

    class _Resp:
        def raise_for_status(self):
            pass

        def json(self):
            return {
                "results": [
                    {
                        "title": "勾股定理 - 维基百科",
                        "url": "https://zh.wikipedia.org/x",
                        "content": "直角三角形两直角边平方和等于斜边平方。",
                    }
                ]
            }

    calls = {}

    def fake_post(url, json=None, timeout=None):
        calls["url"] = url
        calls["key"] = (json or {}).get("api_key")
        return _Resp()

    monkeypatch.setattr(web.httpx, "post", fake_post)
    cfg = _cfg(tmp_path, search={"provider": "tavily", "model": "", "api_key": "tvly-test"})
    ctx = _ctx(cfg, engine)
    out = _run(registry.execute(ctx, "web_search", {"query": "勾股定理", "provider": "tavily"}))
    assert "搜索结果（tavily）" in out
    assert "勾股定理 - 维基百科" in out
    assert calls["url"] == "https://api.tavily.com/search"
    assert calls["key"] == "tvly-test"


def test_tavily_without_key_falls_back_with_note(engine, registry, monkeypatch, tmp_path):
    import kurotutor.tools.web as web

    class _Resp:
        text = '<li class="b_algo"><h2><a href="https://example.com/a">结果A</a></h2><p>摘要</p></li>'

        def raise_for_status(self):
            pass

    monkeypatch.setattr(web.httpx, "get", lambda *a, **k: _Resp())
    cfg = _cfg(tmp_path, search={"provider": "tavily", "model": "", "api_key": ""})
    ctx = _ctx(cfg, engine)
    out = _run(registry.execute(ctx, "web_search", {"query": "x", "provider": "tavily"}))
    assert "未配置 api_key" in out, "无 key 应提示并回退"
    assert "结果A" in out, "回退 Bing 应有结果"


def test_unknown_provider_rejected(engine, registry, tmp_path):
    cfg = _cfg(tmp_path)
    ctx = _ctx(cfg, engine)
    out = _run(registry.execute(ctx, "web_search", {"query": "x", "provider": "google"}))
    assert "未知搜索供应商" in out


def test_configured_default_provider_used(engine, registry, monkeypatch, tmp_path):
    import kurotutor.tools.web as web

    seen = {}

    def fake_post(url, json=None, timeout=None):
        seen["url"] = url

        class _R:
            def raise_for_status(self):
                pass

            def json(self):
                return {"results": [{"title": "T", "url": "https://a.b", "content": "c"}]}

        return _R()

    monkeypatch.setattr(web.httpx, "post", fake_post)
    cfg = _cfg(tmp_path, search={"provider": "tavily", "model": "", "api_key": "k"})
    ctx = _ctx(cfg, engine)
    out = _run(registry.execute(ctx, "web_search", {"query": "x"}))  # 不传 provider → 用配置的 tavily
    assert "搜索结果（tavily）" in out
    assert seen["url"] == "https://api.tavily.com/search"
