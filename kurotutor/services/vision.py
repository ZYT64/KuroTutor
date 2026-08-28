"""可插拔视觉理解 Provider（拍照解题 / 批改 / 笔记解析共用）。

与 :mod:`kurotutor.services.llm` 同构：抽象接口 + OpenAI 兼容实现 + 工厂。
当前实现默认走 OpenAI 兼容 ``/chat/completions`` 的多模态消息格式
（``user`` 消息内 ``[{type:text},{type:image_url,image_url:{url:data:...}}]``），
因此 DeepSeek / 通义 Qwen-VL / 火山等厂商只要兼容该格式即可即插即用，
**Vendor 不写死**，由配置里的 ``models.vision`` 决定。

图片传入方式：本地文件路径（自动 base64 data URL）或公开 http(s) URL 原样透传。
"""

from __future__ import annotations

import base64
import json
import mimetypes
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

import httpx

from kurotutor.config.schema import ModelSpec
from kurotutor.core import ProviderError, get_logger

log = get_logger("vision")

_TIMEOUT = httpx.Timeout(connect=10.0, read=180.0, write=60.0, pool=10.0)
_MAX_RETRIES = 2


def extract_json(text: str) -> dict[str, Any]:
    """从视觉模型输出里提取第一个 JSON 对象；失败返回空 dict。"""
    if not text:
        return {}
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return {}
    candidate = text[start : end + 1]
    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        return {}


class VisionProvider(ABC):
    """视觉理解抽象接口。"""

    @abstractmethod
    async def understand(
        self,
        image_path: str,
        prompt: str,
        *,
        detail: str | None = None,
    ) -> str:
        """理解一张图片并返回文本结果。失败抛 :class:`ProviderError`。"""
        raise NotImplementedError

    async def aclose(self) -> None:  # 默认无资源
        return None

    def close(self) -> None:
        return None


class OpenAICompatVisionProvider(VisionProvider):
    """OpenAI 兼容多模态端点实现。"""

    def __init__(self, spec: ModelSpec):
        if not spec.api_key:
            raise ProviderError(
                "视觉模型未配置 API 密钥",
                cause="models.vision.api_key 为空",
                fix="在 kuro.json 或环境变量 KURO_MODELS__VISION__API_KEY 中填写密钥",
            )
        base_url = (spec.base_url or "https://api.openai.com/v1").rstrip("/")
        self._base_url = base_url
        self._model = spec.model
        self._client = httpx.AsyncClient(
            timeout=_TIMEOUT,
            headers={
                "Authorization": f"Bearer {spec.api_key}",
                "Content-Type": "application/json",
            },
        )
        self._default_detail = spec.model_dump().get("detail")  # 可从配置 extra 带 detail

    @staticmethod
    def _image_data_url(path: str) -> str:
        """把本地图片转成 base64 data URL（MIME 按内容/扩展名推断）。"""
        p = Path(path)
        mime = mimetypes.guess_type(p.name)[0] or "image/png"
        data = base64.b64encode(p.read_bytes()).decode("utf-8")
        return f"data:{mime};base64,{data}"

    @staticmethod
    def _is_url(text: str) -> bool:
        return text.startswith(("http://", "https://"))

    async def understand(
        self,
        image_path: str,
        prompt: str,
        *,
        detail: str | None = None,
    ) -> str:
        image_ref = image_path if self._is_url(image_path) else self._image_data_url(image_path)
        image_url: dict = {"url": image_ref}
        # detail 只在显式给出时传入，避免不兼容厂商因未知字段报错
        if detail:
            image_url["detail"] = detail
        elif self._default_detail:
            image_url["detail"] = self._default_detail

        body = {
            "model": self._model,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {"type": "image_url", "image_url": image_url},
                    ],
                }
            ],
            "max_tokens": 2048,
        }
        url = f"{self._base_url}/chat/completions"
        last_err: Exception | None = None
        for attempt in range(_MAX_RETRIES + 1):
            try:
                resp = await self._client.post(url, json=body)
                if resp.status_code == 429 or resp.status_code >= 500:
                    last_err = ProviderError(
                        "视觉服务暂时不可用",
                        cause=f"HTTP {resp.status_code}",
                        fix="稍后重试，或检查模型配额/余额",
                    )
                    if attempt < _MAX_RETRIES:
                        await self._sleep_backoff(attempt)
                        continue
                    raise last_err
                if resp.status_code != 200:
                    raise ProviderError(
                        "视觉请求失败",
                        cause=f"HTTP {resp.status_code}: {resp.text[:200]}",
                        fix="检查 models.vision 配置（provider/model/base_url/api_key）",
                    )
                return self._parse(resp.json())
            except (httpx.RequestError, httpx.TimeoutException) as exc:
                last_err = ProviderError("无法连接视觉服务", cause=str(exc), fix="检查网络与 base_url 可达性")
                if attempt < _MAX_RETRIES:
                    await self._sleep_backoff(attempt)
                    continue
                raise last_err from exc
        raise last_err or ProviderError("视觉调用失败", fix="请检查配置")  # pragma: no cover

    @staticmethod
    def _parse(data: dict) -> str:
        choices = data.get("choices") or []
        if not choices:
            return ""
        message = choices[0].get("message") or {}
        return (message.get("content") or "").strip()

    @staticmethod
    async def _sleep_backoff(attempt: int) -> None:
        import asyncio

        await asyncio.sleep(0.5 * (2**attempt))

    async def aclose(self) -> None:
        await self._client.aclose()

    def close(self) -> None:
        import asyncio
        from contextlib import suppress

        with suppress(RuntimeError):
            asyncio.get_running_loop().create_task(self._client.aclose())


_PROVIDERS: dict[str, type[VisionProvider]] = {
    "openai": OpenAICompatVisionProvider,
    "openai-compat": OpenAICompatVisionProvider,
}


def build_vision_provider(spec: ModelSpec) -> VisionProvider:
    """按配置里的 provider 字段实例化视觉 Provider。"""
    provider_cls = _PROVIDERS.get(spec.provider.lower())
    if provider_cls is None:
        raise ProviderError(
            f"未知的视觉 Provider：{spec.provider}",
            cause="models.vision.provider 不在内置列表内",
            fix="改用 openai / openai-compat，或扩展 _PROVIDERS",
        )
    return provider_cls(spec)
