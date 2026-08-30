"""Agent 主循环（LangGraph 编排）。

用 LangGraph StateGraph 替代原来的 for 循环，提供：
- 结构化工具调用循环（agent → tools → agent → ... → END）
- 状态管理（迭代计数、最终回复、错误信息全在 GraphState 里）
- 条件路由（有 tool_calls → 执行工具；无 → 返回最终回复）

复用全部现有基础设施：51 个工具注册表、系统提示词、沙箱、LLM Provider。
对外接口不变（Agent.run → AgentResponse），调用方无感知。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from kurotutor.agent.context import ToolContext
from kurotutor.agent.graph import build_graph
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


@dataclass
class AgentResponse:
    """Agent 一轮对话的最终响应。"""

    text: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    usage: dict[str, int] = field(default_factory=dict)
    ok: bool = True
    error: str = ""


class Agent:
    """一次学生消息 → Agent 自主处理 → 最终回复。

    内部使用 LangGraph StateGraph 编排工具调用循环。
    对外接口与原来完全一致。
    """

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
        self._system = build_system_prompt(student, engine=engine)
        self._graph = build_graph(
            provider=self._provider,
            tool_registry=self._registry,
            system_prompt=self._system,
            tool_context=self._ctx,
        )

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
        # 构建 LangGraph 初始消息列表
        init_messages: list[dict[str, Any]] = []
        if working_context:
            ctx_note = (
                f"【背景】你上一轮正在给学生讲这道题：{working_context.get('question_text', '')}"
                f"（方法：{working_context.get('method', '')}）。"
                "若学生接着问这道题或它的变式，请衔接上；若学生已换话题，忽略即可。"
            )
            init_messages.append({"role": "system", "content": ctx_note})
        if history:
            for h in history:
                init_messages.append({"role": h.role, "content": h.content})
        init_messages.append({"role": "user", "content": user_message})

        initial_state = {
            "messages": init_messages,
            "iterations": 0,
            "final_text": "",
            "error": "",
        }

        try:
            final_state = await self._graph.ainvoke(initial_state)
        except ProviderError as exc:
            log_event(log, "llm failed", level="error", error=str(exc))
            return AgentResponse(
                text="老师暂时联系不上，请稍后再试。",
                ok=False,
                error=str(exc),
            )
        except Exception as exc:
            log_event(log, "agent graph error", level="error", error=repr(exc))
            return AgentResponse(
                text="处理这一步时出了点问题，我们换个方式继续。",
                ok=False,
                error=repr(exc),
            )

        final_text = final_state.get("final_text", "")
        error = final_state.get("error", "")

        if error and not final_text:
            return AgentResponse(
                text=final_text or "处理这一步时出了点问题，我们换个方式继续。",
                ok=False,
                error=error,
            )

        return AgentResponse(text=final_text, ok=not error, error=error)
