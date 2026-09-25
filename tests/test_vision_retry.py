"""视觉服务可靠性测试：空结果显式失败、429 错误解析（余额/限流区分）。

背景回归：视觉服务限流/返回空时，上层曾拿到空字符串，Agent 把服务故障
错误归因为「学生拍糊了」，甚至在没有识别结果时编造题目内容。
"""

from __future__ import annotations

import asyncio

import pytest

from kurotutor.config.schema import ModelSpec
from kurotutor.core.errors import ProviderError
from kurotutor.services.vision import OpenAICompatVisionProvider, _rate_limit_detail


def _provider(tmp_path, png_path) -> OpenAICompatVisionProvider:
    spec = ModelSpec(
        provider="openai", model="glm-vision-test", api_key="k" * 8,
        base_url="https://vision.example/v1",
    )
    return OpenAICompatVisionProvider(spec)


class _FakeResp:
    def __init__(self, status_code=200, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload or {}
        self.text = text

    def json(self):
        return self._payload


class _FakeClient:
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = 0

    async def post(self, url, json=None):
        self.calls += 1
        return self._responses[min(self.calls - 1, len(self._responses) - 1)]

    async def aclose(self):
        return None


def test_rate_limit_detail_detects_balance_error():
    cause, fix = _rate_limit_detail('{"error":{"code":"1113","message":"余额不足或无可用资源包,请充值。"}}')
    assert "余额不足" in cause
    assert "充值" in fix


def test_rate_limit_detail_generic_throttle():
    cause, fix = _rate_limit_detail('{"error":{"code":"1302","message":"并发上限"}}')
    assert "并发上限" in cause
    assert "配额" in fix or "稍后" in fix


def test_understand_empty_result_raises(tmp_path):
    from PIL import Image

    img = tmp_path / "hw.png"
    Image.new("RGB", (80, 60), "white").save(img)
    p = _provider(tmp_path, img)
    p._client = _FakeClient([_FakeResp(payload={"choices": [{"message": {"content": "  "}}]})])
    with pytest.raises(ProviderError, match="空结果"):
        asyncio.run(p.understand(str(img), "图里是什么"))


def test_understand_429_parses_vendor_reason(tmp_path):
    from PIL import Image

    img = tmp_path / "hw.png"
    Image.new("RGB", (80, 60), "white").save(img)
    p = _provider(tmp_path, img)
    body = '{"error":{"code":"1113","message":"余额不足或无可用资源包,请充值。"}}'
    p._client = _FakeClient([_FakeResp(status_code=429, text=body)])
    with pytest.raises(ProviderError) as ei:
        asyncio.run(p.understand(str(img), "图里是什么"))
    assert "余额不足" in str(ei.value.cause)
    assert "充值" in ei.value.fix
