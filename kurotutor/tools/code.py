"""代码沙箱工具：Agent 执行 Python 验证计算。"""

from __future__ import annotations

import asyncio
from typing import Any

from kurotutor.agent.context import ToolContext
from kurotutor.services.codeexec import run_python


async def code_run(ctx: ToolContext, kwargs: dict[str, Any]) -> str:
    """执行 Python 代码（隔离子进程，10 秒超时，可读写工作区文件）。
    用于验证计算、处理数据、读写工作区文件（相对路径=工作区路径）。
    参数：code、timeout。
    """
    code = str(kwargs.get("code") or "").strip()
    if not code:
        return "请提供要执行的 Python 代码（code）。"
    timeout = kwargs.get("timeout") or 10
    try:
        result = await asyncio.to_thread(
            run_python, code, timeout=int(timeout), workspace=str(ctx.workspace)
        )
    except Exception as exc:
        return f"执行被拒绝：{exc}"
    out = result.get("stdout") or ""
    err = result.get("stderr") or ""
    lines = []
    if out.strip():
        lines.append(f"输出：\n{out.strip()[:1500]}")
    if err.strip():
        lines.append(f"错误：\n{err.strip()[:500]}")
    if not lines:
        lines.append("（代码执行成功，无输出）")
    return "\n".join(lines)
