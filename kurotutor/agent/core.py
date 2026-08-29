"""Agent 主循环（工具驱动）。

流程：:

    组装上下文（指令 + 工具定义 + 历史 + 当前消息）
      → LLM 调用
      → 解析工具调用
      → 沙箱校验
      → 执行工具 → 结果回填
      → 循环直到最终回复

稳定性（项目宪法 5.3）：错误不崩溃、可重试、有日志、行为可预测。
- Provider 失败：捕获后返回用户可读的错误消息，不抛异常打断会话。
- 工具异常：由注册表装成 [工具错误]... 回填给模型，让它重新决策。
- 迭代上限：防止模型无限循环调用工具。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from kurotutor.agent.context import ToolContext
from kurotutor.agent.prompts import build_system_prompt
from kurotutor.agent.registry import ToolRegistry
from kurotutor.agent.sandbox import Sandbox
from kurotutor.config.schema import AppConfig
from kurotutor.core import ProviderError, get_logger, log_event
from kurotutor.services.llm import (
    ChatMessage,
    LLMProvider,
    ToolCall,
    build_llm_provider,
)

log = get_logger("agent")

# 单轮对话最多允许的工具调用迭代次数（防失控）
_MAX_ITERATIONS = 8


@dataclass
class AgentResponse:
    """Agent 一轮对话的最终响应。"""

    text: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    usage: dict[str, int] = field(default_factory=dict)
    ok: bool = True
    error: str = ""


class Agent:
    """一次学生消息 → Agent 自主处理 → 最终回复。"""

    def __init__(
        self,
        config: AppConfig,
        registry: ToolRegistry,
        engine: Any,
        *,
        student=None,
        session_id: int | None = None,
        provider: LLMProvider | None = None,
    ):
        self._config = config
        self._registry = registry
        self._engine = engine
        if provider is None:
            if config.models is None or config.models.llm is None:
                raise ProviderError(
                    "未配置文本模型",
                    cause="models.llm 缺失",
                    fix="在 kuro.json 或 kuro.example.json 中配置任意 LLM（也可用 echo 离线测试）",
                )
            provider = build_llm_provider(config.models.llm)
        self._provider = provider
        self.sandbox = Sandbox(config, student_id=student.id if student is not None else None)
        self._ctx = ToolContext(
            config=config,
            engine=engine,
            sandbox=self.sandbox,
            logger=log,
            student=student,
            session_id=session_id,
        )
        self._system = build_system_prompt(student)

    @property
    def registry(self) -> ToolRegistry:
        return self._registry

    async def run(
        self,
        user_message: str,
        *,
        history: Sequence[ChatMessage] | None = None,
        working_context: dict | None = None,
    ) -> AgentResponse:
        """处理一条学生消息，返回最终回复文本。

        ``working_context``：上一轮正在讲的内容（题干/方法），注入为背景系统消息，
        让学生在追问或换题时能顺滑衔接。
        """
        messages: list[ChatMessage] = [ChatMessage(role="system", content=self._system)]
        if working_context:
            ctx_note = (
                f"【背景】你上一轮正在给学生讲这道题：{working_context.get('question_text', '')}"
                f"（方法：{working_context.get('method', '')}）。"
                "若学生接着问这道题或它的变式，请衔接上；若学生已换话题，忽略即可。"
            )
            messages.append(ChatMessage(role="system", content=ctx_note))
        if history:
            messages.extend(history)
        messages.append(ChatMessage(role="user", content=user_message))

        schemas = self._registry.schemas()
        last_text = ""
        for _ in range(_MAX_ITERATIONS):
            try:
                result = await self._provider.complete(messages, tools=schemas or None)
            except ProviderError as exc:
                log_event(log, "llm failed", level="error", error=str(exc))
                return AgentResponse(
                    text="老师暂时联系不上，请稍后再试。",
                    ok=False,
                    error=str(exc),
                )
            except Exception as exc:  # 兜底，绝不崩溃
                log_event(log, "agent unexpected error", level="error", error=repr(exc))
                return AgentResponse(text="发生了意料之外的问题，请稍后再试。", ok=False, error=repr(exc))

            if not result.tool_calls:
                last_text = result.content
                return AgentResponse(
                    text=last_text.strip(),
                    tool_calls=result.tool_calls,
                    usage=result.usage,
                )

            # 有工具调用：回填 assistant 消息 + 各工具结果
            try:
                messages.append(
                    ChatMessage(role="assistant", content=result.content or "", tool_calls=result.tool_calls)
                )
                for tc in result.tool_calls:
                    log_event(log, "tool call", name=tc.name, args=tc.arguments)
                    output = await self._registry.execute(self._ctx, tc.name, tc.arguments)
                    messages.append(ChatMessage(role="tool", content=output, tool_call_id=tc.id))
                last_text = result.content or ""
            except Exception as exc:  # 兜底：任何处理异常都不应中断整轮对话
                log_event(log, "tool loop error", level="error", error=repr(exc))
                return AgentResponse(
                    text="处理这一步时出了点问题，我们换个方式继续。", ok=False, error=repr(exc)
                )

        # 达到迭代上限仍无最终文本
        return AgentResponse(
            text=last_text.strip() or "这轮处理步骤较多，我先把结论放这里，有需要可以继续追问。",
            usage={},
        )
