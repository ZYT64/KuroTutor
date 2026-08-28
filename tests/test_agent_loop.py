"""Agent 主循环测试：文本回复、工具调用循环、Provider 故障兜底。"""

from __future__ import annotations

import asyncio

from kurotutor.agent.core import Agent
from kurotutor.agent.registry import ToolRegistry
from kurotutor.core.errors import ProviderError
from kurotutor.services.llm import ChatResult, LLMProvider, ToolCall
from kurotutor.storage import Student, session_scope


def _make_student(engine) -> Student:
    with session_scope(engine) as db:
        st = Student(external_id="loop-student", nickname="小明", stage="senior")
        db.add(st)
        db.flush()
        sid = st.id
    with session_scope(engine) as db:
        return db.get(Student, sid)


class ScriptedProvider(LLMProvider):
    """按剧本返回：第一次工具调用，之后最终文本。"""

    def __init__(self, tool_name: str, tool_args: dict, final_text: str, error: Exception | None = None):
        self._tool_name = tool_name
        self._tool_args = tool_args
        self._final_text = final_text
        self._error = error
        self.calls = 0

    async def complete(self, messages, *, tools=None, temperature=0.7, max_tokens=None) -> ChatResult:
        self.calls += 1
        if self._error is not None:
            raise self._error
        if self.calls == 1:
            return ChatResult(tool_calls=[ToolCall(id="c1", name=self._tool_name, arguments=self._tool_args)])
        return ChatResult(content=self._final_text)


def _tool_registry() -> ToolRegistry:
    registry = ToolRegistry()

    async def add_two(ctx, args):
        return f"计算结果：{args['a']} + {args['b']} = {args['a'] + args['b']}"

    registry.register(
        "test_add",
        "两个整数相加。",
        {
            "type": "object",
            "properties": {"a": {"type": "integer"}, "b": {"type": "integer"}},
            "required": ["a", "b"],
        },
        add_two,
        category="test",
    )
    return registry


def test_echo_text(config, engine):
    registry = _tool_registry()
    agent = Agent(config, registry, engine, student=_make_student(engine))
    agent._provider = None  # 占位
    # 用 echo 文本路径校验默认构造
    from kurotutor.services.llm import EchoProvider

    agent._provider = EchoProvider(config.models.llm)
    resp = asyncio.run(agent.run("你好"))
    assert resp.ok
    assert "已收到" in resp.text


def test_tool_calling_loop_executes_tool(config, engine):
    registry = _tool_registry()
    provider = ScriptedProvider("test_add", {"a": 2, "b": 3}, "结果：2+3=5")
    agent = Agent(config, registry, engine, student=_make_student(engine), provider=provider)
    resp = asyncio.run(agent.run("帮我算 2+3"))
    assert resp.ok
    assert resp.text == "结果：2+3=5"
    assert provider.calls == 2  # 第 1 次工具调用，第 2 次终稿


def test_tool_error_is_fed_back_not_crash(config, engine):
    registry = _tool_registry()

    async def boom(ctx, args):
        raise ValueError("模拟工具崩溃")

    registry.register("boom", "必炸。", {"type": "object", "properties": {}}, boom, category="test")
    provider = ScriptedProvider("boom", {}, "继续回复")
    agent = Agent(config, registry, engine, student=_make_student(engine), provider=provider)
    resp = asyncio.run(agent.run("触发"))
    # 工具异常被包装回填，模型仍能给出最终文本，会话不崩溃
    assert resp.ok
    assert resp.text == "继续回复"


def test_provider_error_graceful(config, engine):
    registry = _tool_registry()
    provider = ScriptedProvider("test_add", {}, "", error=ProviderError("服务不可用", fix="稍后再试"))
    agent = Agent(config, registry, engine, student=_make_student(engine), provider=provider)
    resp = asyncio.run(agent.run("你好"))
    assert resp.ok is False
    assert "请稍后再试" in resp.text


def test_unknown_tool_returns_friendly_result(config, engine):
    registry = _tool_registry()
    provider = ScriptedProvider("no_such_tool", {}, "继续")
    agent = Agent(config, registry, engine, student=_make_student(engine), provider=provider)
    resp = asyncio.run(agent.run("调用不存在工具"))
    assert resp.ok
    assert resp.text == "继续"
