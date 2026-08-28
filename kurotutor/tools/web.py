"""网络工具：网页抓取 + 网络搜索（供应商可配置：默认 Bing 免密钥，可选 Tavily/DuckDuckGo）。

安全：仅允许 http/https，拒绝本地/内网地址（防 SSRF）；抓取限长度。
"""

from __future__ import annotations

import html as _html
import re
from typing import Any
from urllib.parse import urlparse

import httpx

from kurotutor.agent.context import ToolContext
from kurotutor.core import log_event
from kurotutor.core.errors import ToolError

log = __import__("logging").getLogger("web")

_SUPPORTED_SCHEMES = ("http", "https")
_BLOCKED_HOSTS = {"localhost", "127.0.0.1", "0.0.0.0", "::1", "[::1]"}
_TIMEOUT = httpx.Timeout(connect=8.0, read=20.0, write=8.0, pool=8.0)


def _validate_url(url: str) -> str:
    url = url.strip()
    parsed = urlparse(url)
    if parsed.scheme not in _SUPPORTED_SCHEMES:
        raise ToolError("仅支持 http/https 链接", cause=f"scheme={parsed.scheme}", fix="检查链接协议")
    host = (parsed.hostname or "").lower()
    if host in _BLOCKED_HOSTS or host.endswith(".local") or host == "localhost":
        raise ToolError("不允许访问本地/内网地址", cause=host, fix="这是为了防 SSRF，请换公网链接")
    return url


def fetch_page_text(url: str, limit: int = 3000) -> str:
    """抓网页正文（同步核心，web_fetch 工具与其他模块共用）。失败抛 httpx.HTTPError。"""
    resp = httpx.get(url, timeout=_TIMEOUT, follow_redirects=True)
    resp.raise_for_status()
    plain = re.sub(r"<script[\s\S]*?</script>|<style[\s\S]*?</style>", " ", resp.text, flags=re.I)
    plain = re.sub(r"<[^>]+>", " ", plain)
    return _html.unescape(re.sub(r"\s+", " ", plain)).strip()[:limit]


async def web_fetch(ctx: ToolContext, kwargs: dict[str, Any]) -> str:
    """抓取一个网页，返回去 HTML 标签后的正文文本（截断）。参数：url。"""
    url = _validate_url((kwargs.get("url") or "").strip())
    if not url:
        return "请提供 url。"
    try:
        plain = fetch_page_text(url)
    except httpx.HTTPError as exc:
        raise ToolError("网页抓取失败", cause=str(exc), fix="确认链接可访问、网络畅通") from exc
    limit = int(kwargs.get("limit") or 3000)
    return plain[:limit] or "（页面正文为空，可能是动态页面）"


def make_search_fn(ctx: ToolContext, *, prefer_tavily: bool = False):
    """按配置构造搜索函数 (query, limit) -> [结果行]。

    prefer_tavily=True 时（如找真题场景）配置了 key 就优先 Tavily（质量高于 Bing 刮削）。
    """
    spec = ctx.config.models.search if ctx.config.models else None
    api_key = (spec.api_key or "").strip() if spec else ""
    configured = (spec.provider or "").strip().lower() if spec else ""

    def _search(query: str, limit: int) -> list[str]:
        order = []
        if api_key and (configured == "tavily" or prefer_tavily):
            order = [lambda q, n: _search_tavily(q, n, api_key)]
        if configured == "duckduckgo":
            order += [_search_duckduckgo, _search_bing]
        else:
            order += [_search_bing, _search_duckduckgo]
        for fn in order:
            try:
                results = fn(query, limit)
                if results:
                    return results
            except Exception:
                continue
        return []

    return _search


async def web_search(ctx: ToolContext, kwargs: dict[str, Any]) -> str:
    """网络搜索（免密钥 Bing 默认；可选 Tavily，需在 models.search 配 api_key）。

    参数：query、limit、provider（bing/tavily/duckduckgo，缺省用 models.search.provider，再缺省 bing）。
    所选供应商失败自动沿 bing → duckduckgo 兜底。
    """
    query = (kwargs.get("query") or "").strip()
    if not query:
        return "请提供搜索关键词（query）。"
    limit = int(kwargs.get("limit") or 5)

    configured = _configured_search_provider(ctx)
    provider = (kwargs.get("provider") or configured or "bing").strip().lower()
    chains: dict[str, list] = {
        "bing": [_search_bing],
        "tavily": [_search_tavily, _search_bing],
        "duckduckgo": [_search_duckduckgo],
    }
    chain = chains.get(provider)
    if chain is None:
        return f"未知搜索供应商：{provider}（支持 bing / tavily / duckduckgo）。"

    notes: list[str] = []
    results: list[str] = []
    for fn in chain:
        if fn is _search_tavily:
            api_key = _search_api_key(ctx)
            if not api_key:
                notes.append("Tavily 未配置 api_key（models.search.api_key），已回退 Bing。")
                continue
            results = await _try(fn, query, limit, api_key)
        else:
            results = await _try(fn, query, limit)
        if results:
            break
    if not results:
        return "未搜索到结果。" + ("；".join(notes) if notes else "")
    head = f"搜索结果（{provider}）：\n" if (provider != "bing" or configured) else "搜索结果：\n"
    return head + "\n".join(results) + (("\n" + "；".join(notes)) if notes else "")


def _configured_search_provider(ctx: ToolContext) -> str | None:
    """读 models.search.provider（配置缺省 None → bing）。"""
    spec = ctx.config.models.search if ctx.config.models else None
    return (spec.provider or "").strip().lower() or None if spec else None


def _search_api_key(ctx: ToolContext) -> str:
    spec = ctx.config.models.search if ctx.config.models else None
    return (spec.api_key or "").strip() if spec else ""


async def _try(fn, query: str, limit: int, api_key: str | None = None) -> list[str]:
    try:
        if api_key is None:
            return fn(query, limit)
        return fn(query, limit, api_key)
    except Exception as exc:
        log_event(log, "search provider failed", level="warning", provider=fn.__name__, error=repr(exc))
        return []


def _search_tavily(query: str, limit: int, api_key: str) -> list[str]:
    """Tavily 搜索 API（免费档每月 1000 次；结果带正文摘要，质量较好）。"""
    resp = httpx.post(
        "https://api.tavily.com/search",
        json={"api_key": api_key, "query": query, "max_results": limit, "search_depth": "basic"},
        timeout=_TIMEOUT,
    )
    resp.raise_for_status()
    data = resp.json()
    results: list[str] = []
    for item in (data.get("results") or [])[:limit]:
        title = _html.unescape(str(item.get("title") or "")).strip()
        url = str(item.get("url") or "").strip()
        content = _html.unescape(str(item.get("content") or "")).strip()[:120]
        line = f"- {title}\n  {url}"
        if content:
            line += f"\n  {content}"
        results.append(line)
    return results


def _search_bing(query: str, limit: int) -> list[str]:
    """Bing 网页搜索（国内可达、免密钥）。"""
    from urllib.parse import urlencode

    resp = httpx.get(
        "https://www.bing.com/search",
        params=urlencode({"q": query, "setmkt": "zh-CN"}),
        timeout=_TIMEOUT,
        follow_redirects=True,
        headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"},
    )
    resp.raise_for_status()
    # 结果块：<li class="b_algo"> ... <h2><a href="URL">TITLE</a>
    blocks = re.findall(r'<li class="b_algo"[\s\S]*?</li>', resp.text)
    results: list[str] = []
    for block in blocks[:limit]:
        m = re.search(r'<h2[^>]*><a[^>]*href="([^"]+)"[^>]*>([\s\S]*?)</a>', block)
        if not m:
            continue
        url = m.group(1)
        title = _html.unescape(re.sub(r"<[^>]+>", "", m.group(2))).strip()
        # 摘要（有则带上，截断）
        sm = re.search(r'<p[^>]*>([\s\S]*?)</p>', block)
        snippet = _html.unescape(re.sub(r"<[^>]+>", "", sm.group(1))).strip()[:120] if sm else ""
        line = f"- {title}\n  {url}"
        if snippet:
            line += f"\n  {snippet}"
        results.append(line)
    return results


def _search_duckduckgo(query: str, limit: int) -> list[str]:
    """DuckDuckGo HTML 搜索（海外可达；国内网络通常不通，作兜底）。"""
    from urllib.parse import parse_qs, urlencode

    resp = httpx.get(
        "https://html.duckduckgo.com/html/",
        params=urlencode({"q": query}),
        timeout=_TIMEOUT,
        follow_redirects=True,
        headers={"User-Agent": "Mozilla/5.0"},
    )
    resp.raise_for_status()
    links = re.findall(r'class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>', resp.text, flags=re.S)
    results: list[str] = []
    for href, title in links[:limit]:
        real = parse_qs(urlparse(href).query).get("uddg", [None])[0] or href
        title = re.sub(r"<[^>]+>", "", title).strip()
        results.append(f"- {title}\n  {real}")
    return results
