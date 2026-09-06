"""提醒工具：学生说「X 点提醒我…」时落成定时任务，到点经渠道推送。

之前只有调度器消费端（REMINDER 任务 → 主动推送），没有写端工具——
学生在对话里要提醒只能得到口头答应。本模块补齐「Agent-first」入口：
- set_reminder：建提醒（支持相对时间与 ISO 绝对时间，自然语言由 Agent 先换算）
- reminder_list：查看未触发的提醒
- reminder_cancel：取消某条提醒
"""

from __future__ import annotations

import contextlib
import re
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlmodel import select

from kurotutor.agent.context import ToolContext
from kurotutor.core.errors import ToolError
from kurotutor.services import scheduler
from kurotutor.storage import ScheduleTask, TaskStatus, session_scope

# 相对时间：30分钟后 / 45m / in 90min / 2小时后
_REL_MIN = re.compile(r"(?:in\s+)?(\d+)\s*(分钟|分钟|min(?:ute)?s?|m)(?:\s*后)?", re.I)
_REL_HOUR = re.compile(r"(?:in\s+)?(\d+(?:\.\d+)?)\s*(小时|hour|h)(?:\s*后)?", re.I)
_ISO = re.compile(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}")


def _parse_time(raw: str) -> datetime:
    """解析提醒时间：相对分钟/小时或 ISO；解析失败抛 ToolError。"""
    s = raw.strip()
    m = _REL_MIN.search(s) or _REL_HOUR.search(s)
    if m:
        n = float(m.group(1))
        unit = m.group(2).lower()
        minutes = n if unit.startswith(("分", "m")) else n * 60
        return datetime.now(UTC) + timedelta(minutes=minutes)
    if _ISO.match(s):
        try:
            dt = datetime.fromisoformat(s.replace(" ", "T"))
        except ValueError as exc:
            raise ToolError(
                "时间格式不对", cause=str(exc),
                fix="用 ISO 时间（2026-09-07T15:00）或相对时间（30分钟后）",
            ) from exc
        if dt.tzinfo is None:
            # 无时区按学生本地时钟解释：减去本地偏移得到真实 UTC 时刻
            import time as _time

            offset = datetime.fromtimestamp(_time.time()).astimezone().utcoffset() or timedelta()
            return (dt - offset).replace(tzinfo=UTC)
        return dt.astimezone(UTC)
    raise ToolError(
        "看不懂这个时间",
        cause=f"无法解析：{raw}",
        fix='用「30分钟后」这类相对时间，或 ISO 时间（2026-09-07T15:00）',
    )


async def set_reminder(ctx: ToolContext, kwargs: dict[str, Any]) -> str:
    """设一条提醒。参数：time（如 30分钟后 / 2026-09-07T15:00）、text（提醒内容）。"""
    if ctx.student is None:
        return "当前没有学生上下文。"
    raw = str(kwargs.get("time") or "").strip()
    text = str(kwargs.get("text") or "").strip()
    if not raw or not text:
        return "设提醒需要：time（什么时候）和 text（提醒什么）。"
    try:
        fire_at = _parse_time(raw)
    except ToolError as exc:
        return f"设置失败：{exc}"
    if fire_at <= datetime.now(UTC):
        return "这个时间已经过去了，请给一个未来的时间。"
    scheduler.create_task(
        ctx.engine,
        student_id=ctx.student.id,
        kind=scheduler.Kinds.REMINDER,
        fire_at=fire_at,
        payload={"message": text},
    )
    local = fire_at.astimezone()
    return f"好的，我会提醒你：{text}（{local:%m月%d日 %H:%M}）。到点我给你发消息。"


async def reminder_list(ctx: ToolContext, kwargs: dict[str, Any]) -> str:
    """列出还没触发的提醒。"""
    if ctx.student is None:
        return "当前没有学生上下文。"
    with session_scope(ctx.engine) as db:
        rows = db.exec(
            select(ScheduleTask)
            .where(
                ScheduleTask.student_id == ctx.student.id,
                ScheduleTask.kind == scheduler.Kinds.REMINDER,
                ScheduleTask.status == TaskStatus.PENDING,
            )
            .order_by(ScheduleTask.fire_at.asc())
        ).all()
    if not rows:
        return "现在没有待触发的提醒。"
    lines = []
    for i, t in enumerate(rows, 1):
        local = t.fire_at.astimezone()
        msg = ""
        with contextlib.suppress(ValueError, TypeError):
            import json

            msg = str((json.loads(t.payload or "{}") or {}).get("message") or "")
        lines.append(f"{i}. {local:%m月%d日 %H:%M} —— {msg or '（提醒）'}（编号 {t.id}）")
    return "你的提醒：\n" + "\n".join(lines)


async def reminder_cancel(ctx: ToolContext, kwargs: dict[str, Any]) -> str:
    """取消提醒。参数：task_id（reminder_list 里的编号）。"""
    if ctx.student is None:
        return "当前没有学生上下文。"
    raw = kwargs.get("task_id")
    try:
        task_id = int(raw)
    except (TypeError, ValueError):
        return "请提供要取消的提醒编号（task_id，用 reminder_list 查）。"
    with session_scope(ctx.engine) as db:
        t = db.get(ScheduleTask, task_id)
        if t is None or t.student_id != ctx.student.id or t.kind != scheduler.Kinds.REMINDER:
            return f"编号 {task_id} 不是你的提醒。"
        ok = scheduler.cancel_task(ctx.engine, task_id)
    if not ok:
        return "取消失败，这条提醒可能已经触发过了。"
    return f"已取消：{t.fire_at.astimezone():%m月%d日 %H:%M} 的提醒。"
