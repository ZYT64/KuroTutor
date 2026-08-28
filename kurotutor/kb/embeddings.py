"""嵌入服务（知识库·向量检索）。

与 llm/vision 同构：接口 + OpenAI 兼容实现 + 工厂。Vendor 由 ``models.embedding`` 决定。
未配置嵌入模型时返回 None，调用方回退关键词检索（不因缺嵌入而失败）。
"""

from __future__ import annotations

from abc import ABC, abstractmethod

import httpx

from kurotutor.config.schema import ModelSpec
from kurotutor.core import ProviderError, get_logger

log = get_logger("embedding")

_TIMEOUT = httpx.Timeout(connect=10.0, read=60.0, write=30.0, pool=10.0)


class EmbeddingProvider(ABC):
    @abstractmethod
    async def embed(self, texts: list[str]) -> list[list[float]]:
        raise NotImplementedError

    async def aclose(self) -> None:
        return None


class OpenAICompatEmbeddingProvider(EmbeddingProvider):
    """OpenAI 兼容 ``/embeddings`` 端点实现（DeepSeek/通义等）。"""

    def __init__(self, spec: ModelSpec):
        if not spec.api_key:
            raise ProviderError(
                "嵌入模型未配置 API 密钥", cause="models.embedding.api_key 为空", fix="配置密钥"
            )
        self._base_url = (spec.base_url or "https://api.openai.com/v1").rstrip("/")
        self._model = spec.model
        self._client = httpx.AsyncClient(
            timeout=_TIMEOUT,
            headers={"Authorization": f"Bearer {spec.api_key}", "Content-Type": "application/json"},
        )

    async def embed(self, texts: list[str]) -> list[list[float]]:

        resp = await self._client.post(
            f"{self._base_url}/embeddings", json={"model": self._model, "input": texts}
        )
        if resp.status_code != 200:
            raise ProviderError(
                "嵌入请求失败", cause=f"HTTP {resp.status_code}: {resp.text[:200]}", fix="检查嵌入配置"
            )
        data = resp.json()
        # 按 index 排序返回
        items = sorted(data.get("data", []), key=lambda d: d.get("index", 0))
        return [d["embedding"] for d in items]

    async def aclose(self) -> None:
        await self._client.aclose()


_PROVIDERS: dict[str, type[EmbeddingProvider]] = {
    "openai": OpenAICompatEmbeddingProvider,
    "openai-compat": OpenAICompatEmbeddingProvider,
}


def build_embedding_provider(spec: ModelSpec) -> EmbeddingProvider:
    cls = _PROVIDERS.get(spec.provider.lower())
    if cls is None:
        raise ProviderError(f"未知的嵌入 Provider：{spec.provider}", fix="用 openai / openai-compat")
    return cls(spec)


def cosine(a: list[float], b: list[float]) -> float:
    """余弦相似度（纯 Python，避免依赖）。"""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(x * x for x in b) ** 0.5
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)
