"""可插拔 LLM Provider。

项目宪法定调「所有模型 Provider 可插拔，不硬编码」。本模块定义统一接口
:class:`LLMProvider`，并实现两类：

- :class:`OpenAICompatProvider` —— 走任意 OpenAI 兼容 ``/chat/completions`` 端点
  （国产厂商 DeepSeek / 通义 Qwen / 火山方舟 / Moonshot 等大多兼容此格式），
  支持 ``tools`` 函数调用。
- :class:`EchoProvider` —— 离线兜底：不联网、返回固定文本，仅用于 ``kuro doctor``
  健康检查与单元测试，**绝不用于真实教学**。

通过 ``build_llm_provider(spec)`` 按配置里的 ``provider`` 字段工厂化选择。
"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

import httpx

from kurotutor.config.schema import ModelSpec
from kurotutor.core import ProviderError, get_logger

log = get_logger("llm")

# 请求超时（连接+读写）。教学场景单次对话，读放大给足时间。
_TIMEOUT = httpx.Timeout(connect=10.0, read=120.0, write=30.0, pool=10.0)
# 自动重试次数（网络抖动、限流）
_MAX_RETRIES = 2


class _RetryableStatus(Exception):
    """流式路径遇到 429/5xx 的内部标记，交给 ``complete`` 重试循环统一退避。"""


@dataclass
class ToolCall:
    """模型发起的一次工具调用。"""

    id: str
    name: str
    arguments: dict[str, Any]  # 已解析的 JSON 参数

    @classmethod
    def from_raw(cls, raw: dict[str, Any]) -> ToolCall:
        args = raw.get("function", {}).get("arguments", "{}") or "{}"
        try:
            parsed = json.loads(args)
        except json.JSONDecodeError:
            parsed = {}
        return cls(
            id=raw.get("id") or "",
            name=raw.get("function", {}).get("name", ""),
            arguments=parsed,
        )


@dataclass
class ChatMessage:
    """一条对话消息。tool_call 结果用 role="tool" + tool_call_id 回填。"""

    role: str
    content: str | None = None
    name: str | None = None
    tool_calls: list[ToolCall] = field(default_factory=list)
    tool_call_id: str | None = None

    def serialize(self) -> dict[str, Any]:
        """转为发送给 OpenAI 兼容端点的 dict。"""
        msg: dict[str, Any] = {"role": self.role}
        if self.content is not None:
            msg["content"] = self.content
        if self.name:
            msg["name"] = self.name
        if self.tool_calls:
            msg["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {"name": tc.name, "arguments": json.dumps(tc.arguments, ensure_ascii=False)},
                }
                for tc in self.tool_calls
            ]
        if self.tool_call_id:
            msg["tool_call_id"] = self.tool_call_id
        return msg


@dataclass
class ChatResult:
    """一次模型调用的结果。"""

    content: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    finish_reason: str = "stop"
    usage: dict[str, int] = field(default_factory=dict)


class LLMProvider(ABC):
    """LLM Provider 抽象接口。所有 Provider 必须实现 :meth:`complete`。"""

    @abstractmethod
    async def complete(
        self,
        messages: Sequence[ChatMessage],
        *,
        tools: list[dict[str, Any]] | None = None,
        temperature: float = 0.7,
        max_tokens: int | None = None,
    ) -> ChatResult:
        """发送消息序列，返回模型回复。失败抛 :class:`ProviderError`。"""
        raise NotImplementedError

    async def aclose(self) -> None:  # 默认无资源需要释放
        return None

    def close(self) -> None:
        return None


class OpenAICompatProvider(LLMProvider):
    """OpenAI 兼容 ``/chat/completions`` 端点实现（支持 tools 函数调用）。"""

    def __init__(self, spec: ModelSpec):
        if not spec.api_key:
            raise ProviderError(
                "文本模型未配置 API 密钥",
                cause="models.llm.api_key 为空",
                fix="在 kuro.json 或环境变量 KURO_MODELS__LLM__API_KEY 中填写密钥",
            )
        base_url = (spec.base_url or "https://api.openai.com/v1").rstrip("/")
        self._base_url = base_url
        self._model = spec.model
        self._api_key = spec.api_key
        self._client = httpx.AsyncClient(
            timeout=_TIMEOUT,
            headers={
                "Authorization": f"Bearer {spec.api_key}",
                "Content-Type": "application/json",
            },
        )

    async def complete(
        self,
        messages: Sequence[ChatMessage],
        *,
        tools: list[dict[str, Any]] | None = None,
        temperature: float = 0.7,
        max_tokens: int | None = None,
    ) -> ChatResult:
        body: dict[str, Any] = {
            "model": self._model,
            "messages": [m.serialize() for m in messages],
            "temperature": temperature,
        }
        if max_tokens:
            body["max_tokens"] = max_tokens
        if tools:
            body["tools"] = tools
            body["tool_choice"] = "auto"

        url = f"{self._base_url}/chat/completions"
        last_err: Exception | None = None
        for attempt in range(_MAX_RETRIES + 1):
            try:
                if not tools:
                    # 长文本生成走流式：GLM 等厂商非流式首字节可达 2-3 分钟，会撞 read
                    # 超时；流式首 token 秒回，read 超时只约束块间隔，长生成不再失败。
                    return await self._complete_streaming(url, body)
                resp = await self._client.post(url, json=body)
                if resp.status_code == 429 or resp.status_code >= 500:
                    last_err = ProviderError(
                        "模型服务暂时不可用",
                        cause=f"HTTP {resp.status_code}",
                        fix="稍后重试，或检查模型配额/余额",
                    )
                    if attempt < _MAX_RETRIES:
                        await self._sleep_backoff(attempt)
                        continue
                    raise last_err
                if resp.status_code != 200:
                    raise ProviderError(
                        "模型请求失败",
                        cause=f"HTTP {resp.status_code}: {resp.text[:200]}",
                        fix="检查模型配置（provider/model/base_url/api_key）是否正确",
                    )
                data = resp.json()
                return self._parse(data)
            except _RetryableStatus as exc:
                last_err = ProviderError(
                    "模型服务暂时不可用",
                    cause=str(exc),
                    fix="稍后重试，或检查模型配额/余额",
                )
                if attempt < _MAX_RETRIES:
                    await self._sleep_backoff(attempt)
                    continue
                raise last_err from exc
            except (httpx.RequestError, httpx.TimeoutException) as exc:
                last_err = ProviderError(
                    "无法连接模型服务",
                    cause=f"{type(exc).__name__}: {exc}",
                    fix="检查网络与 base_url 可达性",
                )
                # 连接类错误：销毁旧客户端重建（陈旧连接池/断连的 keep-alive 会导致后续也失败）
                await self._client.aclose()
                self._client = httpx.AsyncClient(
                    timeout=_TIMEOUT,
                    headers={
                        "Authorization": f"Bearer {self._api_key}",
                        "Content-Type": "application/json",
                    },
                )
                if attempt < _MAX_RETRIES:
                    log.warning("connection error, rebuilding client and retrying: %s", type(exc).__name__)
                    await self._sleep_backoff(attempt)
                    continue
                raise last_err from exc
        raise last_err or ProviderError("模型调用失败", fix="请检查配置")  # pragma: no cover

    async def _complete_streaming(self, url: str, body: dict[str, Any]) -> ChatResult:
        """流式接收一次补全（无 tools 调用路径），跨块累积内容与用量。"""
        stream_body = {**body, "stream": True}
        log.debug("streaming request to %s", url)
        parts: list[str] = []
        finish = "stop"
        usage: dict[str, Any] = {}
        async with self._client.stream("POST", url, json=stream_body) as resp:
            if resp.status_code == 429 or resp.status_code >= 500:
                raise _RetryableStatus(f"HTTP {resp.status_code}")
            if resp.status_code != 200:
                text = (await resp.aread()).decode("utf-8", errors="replace")
                raise ProviderError(
                    "模型请求失败",
                    cause=f"HTTP {resp.status_code}: {text[:200]}",
                    fix="检查模型配置（provider/model/base_url/api_key）是否正确",
                )
            async for line in resp.aiter_lines():
                if not line.startswith("data:"):
                    continue
                chunk = line[len("data:") :].strip()
                if chunk == "[DONE]":
                    break
                try:
                    data = json.loads(chunk)
                except json.JSONDecodeError:
                    continue  # 跳过不完整/心跳块
                choice = (data.get("choices") or [{}])[0]
                delta = choice.get("delta") or {}
                if delta.get("content"):
                    parts.append(delta["content"])
                if choice.get("finish_reason"):
                    finish = choice["finish_reason"]
                if data.get("usage"):
                    usage = data["usage"]
        return ChatResult(
            content="".join(parts),
            finish_reason=finish,
            usage={k: int(v) for k, v in usage.items() if isinstance(v, (int, float))},
        )

    def _parse(self, data: dict[str, Any]) -> ChatResult:
        choices = data.get("choices") or []
        if not choices:
            return ChatResult(
                content="", finish_reason=data.get("choices", [{}])[-1].get("finish_reason", "stop")
            )
        choice = choices[0]
        message = choice.get("message") or {}
        tool_calls = [ToolCall.from_raw(tc) for tc in (message.get("tool_calls") or [])]
        content = message.get("content") or ""
        if not content and not tool_calls:
            content = message.get("content") or ""
        usage = data.get("usage") or {}
        return ChatResult(
            content=content,
            tool_calls=tool_calls,
            finish_reason=choice.get("finish_reason") or "stop",
            usage={k: int(v) for k, v in usage.items() if isinstance(v, (int, float))},
        )

    @staticmethod
    async def _sleep_backoff(attempt: int) -> None:
        import asyncio

        await asyncio.sleep(0.5 * (2**attempt))

    async def aclose(self) -> None:
        await self._client.aclose()

    def close(self) -> None:
        import asyncio
        from contextlib import suppress

        with suppress(RuntimeError):  # 无运行中事件循环时忽略
            asyncio.get_running_loop().create_task(self._client.aclose())


class EchoProvider(LLMProvider):
    """离线测试 Provider：不联网，返回固定文本。

    仅用于 ``kuro doctor`` 健康检查与单元测试，保证 Agent 主循环
    在无真实密钥时也能端到端跑通。绝不用于真实教学。
    """

    def __init__(self, spec: ModelSpec):
        self._spec = spec

    async def complete(
        self,
        messages: Sequence[ChatMessage],
        *,
        tools: list[dict[str, Any]] | None = None,
        temperature: float = 0.7,
        max_tokens: int | None = None,
    ) -> ChatResult:
        last_user = next((m.content for m in reversed(messages) if m.role == "user"), "")
        text = last_user or "（Echo 测试：无输入）"
        return ChatResult(
            content=f"[echo] 已收到：{text}",
            finish_reason="stop",
            usage={"prompt_tokens": 0, "completion_tokens": 0},
        )


# 已内置的 provider 实现，供工厂按名选择
_PROVIDERS: dict[str, type[LLMProvider]] = {
    "openai": OpenAICompatProvider,
    "openai-compat": OpenAICompatProvider,
    "echo": EchoProvider,
    "mock": EchoProvider,
}


def build_llm_provider(spec: ModelSpec) -> LLMProvider:
    """按配置里的 provider 字段实例化 LLM Provider。"""
    provider_cls = _PROVIDERS.get(spec.provider.lower())
    if provider_cls is None:
        raise ProviderError(
            f"未知的 LLM Provider：{spec.provider}",
            cause="models.llm.provider 不在内置列表内",
            fix="改用 openai / openai-compat / echo，或扩展 _PROVIDERS",
        )
    return provider_cls(spec)


def tool_schema(name: str, description: str, parameters: dict[str, Any]) -> dict[str, Any]:
    """构造 OpenAI 风格的 tool 定义。"""
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": parameters,
        },
    }
