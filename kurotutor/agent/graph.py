"""LangGraph Agent：结构化的工具驱动编排。

用 LangGraph StateGraph 替代原来的 for 循环，提供：
- 结构化路由（输入分类 → 对应处理节点 → Agent 决策 → 工具执行）
- 状态管理（对话历史、文件信息、工具调用记录全在 state 里）
- 条件边（有 tool_calls → 执行工具；无 → 返回最终回复）

复用全部现有基础设施：51 个工具注册表、系统提示词、沙箱、LLM Provider。
"""

from __future__ import annotations

from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

from kurotutor.core import ProviderError, get_logger
from kurotutor.services.llm import ChatMessage

log = get_logger("agent.graph")

# 单轮最多允许的工具调用迭代次数
_MAX_ITERATIONS = 8


class GraphState(TypedDict):
    """LangGraph 状态：全节点共享。"""

    # 对话消息列表（纯 dict，手动管理追加）
    messages: list[dict[str, Any]]
    # 工具调用计数（防失控）
    iterations: int
    # 最终回复文本
    final_text: str
    # 错误信息
    error: str


def build_graph(
    provider: Any,
    tool_registry: Any,
    system_prompt: str,
    tool_context: Any,
) -> Any:
    """构建 LangGraph Agent 图。

    Args:
        provider: LLM Provider（完整我们的 complete 接口）
        tool_registry: 工具注册表（51 个工具）
        system_prompt: 系统提示词
        tool_context: ToolContext（工具执行需要）
    """
    # 工具 schema 列表（传给 LLM 的 tools 参数）
    tool_schemas = tool_registry.schemas()

    async def agent_node(state: GraphState) -> dict[str, Any]:
        """主 Agent 节点：调 LLM，返回工具调用或最终回复。"""
        messages = state["messages"]
        iterations = state.get("iterations", 0)

        if iterations >= _MAX_ITERATIONS:
            last_text = ""
            for m in reversed(messages):
                if m.get("role") == "assistant" and m.get("content"):
                    last_text = m["content"]
                    break
            return {
                "final_text": last_text or "这轮处理步骤较多，我先把结论放这里，有需要可以继续追问。",
                "error": "max_iterations",
            }

        try:
            # 构建完整的消息列表（含系统提示词）
            full_messages = [ChatMessage(role="system", content=system_prompt)]
            for m in messages:
                role = m.get("role", "user")
                content = m.get("content", "")
                tool_calls = m.get("tool_calls")
                tool_call_id = m.get("tool_call_id")
                if tool_calls:
                    full_messages.append(
                        ChatMessage(role=role, content=content, tool_calls=tool_calls)
                    )
                elif tool_call_id:
                    full_messages.append(
                        ChatMessage(role=role, content=content, tool_call_id=tool_call_id)
                    )
                else:
                    full_messages.append(ChatMessage(role=role, content=content))

            result = await provider.complete(
                full_messages, tools=tool_schemas or None
            )
        except ProviderError as exc:
            log.warning(f"llm failed in graph: {exc}")
            return {
                "final_text": "老师暂时联系不上，请稍后再试。",
                "error": str(exc),
            }
        except Exception as exc:
            log.error(f"agent graph unexpected error: {exc!r}")
            return {
                "final_text": "发生了意料之外的问题，请稍后再试。",
                "error": repr(exc),
            }

        # 无工具调用 → 最终回复
        if not result.tool_calls:
            return {
                "messages": [{"role": "assistant", "content": result.content or ""}],
                "final_text": (result.content or "").strip(),
                "iterations": iterations + 1,
            }

        # 有工具调用 → 返回 assistant 消息（含 tool_calls），由条件边路由到工具节点
        msg = {
            "role": "assistant",
            "content": result.content or "",
            "tool_calls": [
                {"id": tc.id, "name": tc.name, "arguments": tc.arguments}
                for tc in result.tool_calls
            ],
        }
        return {"messages": [msg], "iterations": iterations + 1}

    async def tool_node(state: GraphState) -> dict[str, Any]:
        """执行所有工具调用，结果回填为 tool 消息。"""
        messages = state["messages"]
        # 找最后一条 assistant 消息里的 tool_calls
        last_tool_calls = []
        for m in reversed(messages):
            if m.get("tool_calls"):
                last_tool_calls = m["tool_calls"]
                break

        results = []
        for tc in last_tool_calls:
            name = tc.get("name", "")
            args = tc.get("arguments", {})
            call_id = tc.get("id", "")
            log.info(f"graph tool call: {name} {args}")
            try:
                output = await tool_registry.execute(tool_context, name, args)
            except Exception as exc:
                output = f"[工具错误] {exc!r}"
            results.append(
                {
                    "role": "tool",
                    "content": output,
                    "tool_call_id": call_id,
                }
            )
        return {"messages": state["messages"] + results}

    def route_after_agent(state: GraphState) -> str:
        """条件边：有 tool_calls → tools；否则 → END。"""
        messages = state.get("messages", [])
        if messages:
            last = messages[-1]
            if last.get("role") == "assistant" and last.get("tool_calls"):
                return "tools"
        # 有最终文本 → 结束
        if state.get("final_text"):
            return END
        return "tools"  # 兜底：不该到这，但防死循环

    # ---- 构建图 ----
    graph = StateGraph(GraphState)
    graph.add_node("agent", agent_node)
    graph.add_node("tools", tool_node)
    graph.add_edge(START, "agent")
    graph.add_conditional_edges("agent", route_after_agent, {"tools": "tools", END: END})
    graph.add_edge("tools", "agent")

    return graph.compile()
