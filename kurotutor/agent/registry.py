"""工具注册表。

架构红线第 1 条「Agent‑first：所有功能 = Agent 工具」。本模块是工具的唯一登记处：
- 装饰器 / 方法注册，统一校验名称唯一。
- 每个工具携带 JSON Schema（``parameters``），供模型用来生成函数调用。
- 执行时把 :class:`ToolContext` 注入 handler，实现依赖倒置（工具不直接持配置/DB，
  而是通过上下文注入），避免硬编码与循环依赖。

工具约定：``async def handler(ctx: ToolContext, **kwargs) -> str``，返回字符串即回填给模型的结果。
失败抛 :class:`~kurotutor.core.errors.ToolError`，由 Agent 主循环捕获并作为错误消息回填。
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from kurotutor.core.errors import ToolError
from kurotutor.services.llm import tool_schema

from .context import ToolContext  # noqa: F401  (供类型标注与 __all__ 复用)


@dataclass
class Tool:
    """一个已注册的工具。"""

    name: str
    description: str
    parameters: dict[str, Any]
    handler: Callable[[ToolContext, dict[str, Any]], Awaitable[str]]
    category: str = "general"
    # 工具是否需要沙箱权限校验（文件类工具必须 True）
    sandbox_required: bool = False

    def schema(self) -> dict[str, Any]:
        return tool_schema(self.name, self.description, self.parameters)


class ToolRegistry:
    """工具的登记、检索与执行。"""

    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(
        self,
        name: str,
        description: str,
        parameters: dict[str, Any],
        handler: Callable[[ToolContext, dict[str, Any]], Awaitable[str]],
        *,
        category: str = "general",
        sandbox_required: bool = False,
    ) -> Tool:
        if name in self._tools:
            raise ToolError(f"工具名重复：{name}", fix="改名或先移除同名工具")
        tool = Tool(
            name=name,
            description=description,
            parameters=parameters,
            handler=handler,
            category=category,
            sandbox_required=sandbox_required,
        )
        self._tools[name] = tool
        return tool

    def tool(
        self,
        name: str,
        description: str,
        parameters: dict[str, Any],
        *,
        category: str = "general",
        sandbox_required: bool = False,
    ) -> Callable[[Callable[..., Awaitable[str]]], Tool]:
        """装饰器用法：``@registry.tool(...)``。"""

        def decorator(fn: Callable[..., Awaitable[str]]) -> Tool:
            tool = self.register(
                name,
                description,
                parameters,
                fn,  # type: ignore[arg-type]
                category=category,
                sandbox_required=sandbox_required,
            )
            return tool

        return decorator

    def get(self, name: str) -> Tool:
        tool = self._tools.get(name)
        if tool is None:
            raise ToolError(f"工具不存在：{name}", fix="确认工具已安装且名称正确")
        return tool

    def __contains__(self, name: str) -> bool:
        return name in self._tools

    def schemas(self) -> list[dict[str, Any]]:
        """所有工具的 JSON Schema 定义，注入模型。"""
        return [t.schema() for t in self._tools.values()]

    def list(self) -> list[Tool]:
        return list(self._tools.values())

    def count(self) -> int:
        return len(self._tools)

    async def execute(self, ctx: ToolContext, name: str, args: dict[str, Any]) -> str:
        """执行一个工具。参数校验与错误包装统一在这里做。

        任何失败（工具不存在 / 工具自身的 ToolError / 意外异常）都被包装成
        字符串回填给模型，绝不向调用方抛异常，保证「错误不崩溃」。
        """
        try:
            tool = self.get(name)
            result = await tool.handler(ctx, args)
        except ToolError as exc:
            # 工具自身抛出的错误 / 工具不存在：把现象+建议回填给模型，让它重新决策
            return f"[工具错误] {exc}"
        except Exception as exc:  # 兜底：任何意外错误都不应崩溃会话
            return f"[工具异常] {type(exc).__name__}: {exc}"
        if result is None:
            result = ""
        return str(result)
