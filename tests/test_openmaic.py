"""OpenMAIC 客户端单元测试（mock httpx，不触网）。"""

import pytest

from kurotutor.config.schema import OpenMAICConfig
from kurotutor.core.errors import ProviderError
from kurotutor.services import openmaic as maic

_CFG = OpenMAICConfig(base_url="https://open.maic.chat", access_code="sk-test")


class _Resp:
    def __init__(self, status=200, payload=None, text=""):
        self.status_code = status
        self._payload = payload or {}
        self.text = text or str(self._payload)

    def json(self):
        return self._payload


class _Client:
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls: list[tuple[str, dict]] = []

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def get(self, url, **kw):
        self.calls.append(("GET", {"url": url, **kw}))
        return self._responses.pop(0)

    async def post(self, url, **kw):
        self.calls.append(("POST", {"url": url, **kw}))
        return self._responses.pop(0)


def test_health_ok(monkeypatch):
    import asyncio

    client = _Client([_Resp(200, {"status": "ok", "capabilities": {"tts": True}})])
    monkeypatch.setattr(maic.httpx, "AsyncClient", lambda **kw: client)
    out = asyncio.run(maic.health(_CFG))
    assert out["status"] == "ok"
    assert client.calls[0][1]["headers"]["Authorization"] == "Bearer sk-test"


def test_health_bad_code(monkeypatch):
    import asyncio

    monkeypatch.setattr(maic.httpx, "AsyncClient", lambda **kw: _Client([_Resp(401, text="unauthorized")]))
    with pytest.raises(ProviderError, match="访问码无效"):
        asyncio.run(maic.health(_CFG))


def test_health_no_code():
    import asyncio

    with pytest.raises(ProviderError, match="访问码"):
        asyncio.run(maic.health(OpenMAICConfig(access_code="")))


def test_submit_accepts_202(monkeypatch):
    import asyncio

    client = _Client([
        _Resp(200, {"status": "ok", "capabilities": {"tts": True}}),  # health
        _Resp(202, {"success": True, "jobId": "J1", "status": "queued",
                    "pollUrl": "/api/generate-classroom/J1"}),
    ])
    monkeypatch.setattr(maic.httpx, "AsyncClient", lambda **kw: client)
    out = asyncio.run(maic.submit_generation(_CFG, requirement="二次函数入门课"))
    assert out["jobId"] == "J1"
    body = client.calls[1][1]["json"]
    assert body["enableTTS"] is True  # 能力探测后开启
    assert body["language"] == "zh-CN"


def test_submit_quota(monkeypatch):
    import asyncio

    client = _Client([
        _Resp(200, {"status": "ok"}),
        _Resp(403, text="Daily quota exhausted"),
    ])
    monkeypatch.setattr(maic.httpx, "AsyncClient", lambda **kw: client)
    with pytest.raises(ProviderError, match="额度"):
        asyncio.run(maic.submit_generation(_CFG, requirement="x"))


def test_poll_until_done(monkeypatch):
    import asyncio

    async def _fake_sleep(_):
        return None

    monkeypatch.setattr(maic.asyncio, "sleep", _fake_sleep)
    client = _Client([
        _Resp(200, {"status": "queued"}),
        _Resp(200, {"status": "running"}),
        _Resp(200, {"status": "succeeded", "classroomId": "abc9"}),
    ])
    monkeypatch.setattr(maic.httpx, "AsyncClient", lambda **kw: client)
    out = asyncio.run(maic.poll_generation(_CFG, "/api/generate-classroom/J1"))
    url = maic.extract_classroom_url(out, _CFG.base_url)
    assert url == "https://open.maic.chat/classroom/abc9"
