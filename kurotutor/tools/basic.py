"""基础工具：取当前时间。用于 Agent 感知「现在几点」，支撑排课/提醒/复习调度判断。"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from kurotutor.agent.context import ToolContext


async def now(ctx: ToolContext, kwargs: dict[str, Any]) -> str:
    """返回当前本地时间与星期，供排课/提醒决策。"""
    dt = datetime.now()
    weekdays = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
    return f"现在是 {dt.strftime('%Y-%m-%d %H:%M')}（{weekdays[dt.weekday()]}，本地时间）。"
