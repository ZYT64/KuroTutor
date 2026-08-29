"""学习周报工具：生成/导出周报，订阅每周自动推送。"""

from __future__ import annotations

import asyncio
from typing import Any

from sqlmodel import select

from kurotutor.agent.context import ToolContext
from kurotutor.services import scheduler
from kurotutor.services.report import build_weekly_report
from kurotutor.storage import ScheduleTask, TaskStatus, session_scope


async def weekly_report(ctx: ToolContext, kwargs: dict[str, Any]) -> str:
    """生成本周学习周报（Word 导出 + 摘要）。学生要看周报/学情总结时使用。"""
    if ctx.student is None:
        return "当前没有学生上下文。"
    if ctx.config.models is None or ctx.config.models.llm is None:
        return "未配置文本模型。"
    result = await asyncio.to_thread(
        build_weekly_report,
        ctx.engine,
        ctx.student.id,
        llm_spec=ctx.config.models.llm,
        workspace=ctx.config.workspace,
    )
    lines = [result["text"]]
    if result["path"]:
        lines.append(f"\n📄 周报文档已生成：{result['path']}")
    return "\n".join(lines)


async def report_subscribe(ctx: ToolContext, kwargs: dict[str, Any]) -> str:
    """订阅/退订每周自动周报推送（每周日晚 20:00）。参数：op（subscribe/unsubscribe）。"""
    if ctx.student is None:
        return "当前没有学生上下文。"
    op = str(kwargs.get("op") or "subscribe").strip().lower()
    from datetime import UTC, datetime, timedelta

    if op == "unsubscribe":
        with session_scope(ctx.engine) as db:
            tasks = db.exec(
                select(ScheduleTask).where(
                    ScheduleTask.student_id == ctx.student.id,
                    ScheduleTask.kind == scheduler.Kinds.REPORT,
                    ScheduleTask.status == TaskStatus.PENDING,
                )
            ).all()
            n = 0
            for t in tasks:
                t.status = TaskStatus.CANCELLED
                db.add(t)
                n += 1
        return f"已退订周报（取消 {n} 个定时任务）。"
    with session_scope(ctx.engine) as db:
        existing = db.exec(
            select(ScheduleTask).where(
                ScheduleTask.student_id == ctx.student.id,
                ScheduleTask.kind == scheduler.Kinds.REPORT,
                ScheduleTask.status == TaskStatus.PENDING,
            )
        ).all()
    if existing:
        return "周报订阅已开启（每周日晚 8 点推送），无需重复订阅。"
    # 下一个周日晚 20:00（本地）→ UTC 入库
    now = datetime.now().astimezone()
    days_ahead = (6 - now.weekday()) % 7  # 周一=0 … 周日=6
    nxt = (now + timedelta(days=days_ahead)).replace(hour=20, minute=0, second=0, microsecond=0)
    if nxt <= now:
        nxt += timedelta(days=7)
    scheduler.create_task(
        ctx.engine,
        student_id=ctx.student.id,
        kind=scheduler.Kinds.REPORT,
        fire_at=nxt.astimezone(UTC),
        payload={"weekly": True},
    )
    return "已订阅周报：每周日晚 8 点推送本周学习总结（Word 文档）。"
