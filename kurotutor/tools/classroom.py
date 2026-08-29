"""课堂工具：排课（单堂/系列）、查课、应急改期/取消、手动备课。

授课与课后闭环由统一调度（serve 后台轮询）在到点时自动推送驱动；
工具层负责把学生的自然语言排课请求落地（时间由 Agent 解析为 ISO）。
"""

from __future__ import annotations

import asyncio
from typing import Any

from kurotutor.agent.context import ToolContext
from kurotutor.core.errors import ToolError
from kurotutor.services import classroom
from kurotutor.services.llm import build_llm_provider

_STAGE_MAP = {"primary": "小学", "junior": "初中", "senior": "高中", "university": "大学"}


async def schedule_class(ctx: ToolContext, kwargs: dict[str, Any]) -> str:
    """排课。参数：subject、topic、start_at（本地 ISO 时间）、minutes、series_count（系列课节数）、goal。"""
    if ctx.student is None:
        return "当前没有学生上下文，无法排课。"
    subject = str(kwargs.get("subject") or "数学").strip()
    topic = str(kwargs.get("topic") or "").strip()
    start_at = str(kwargs.get("start_at") or "").strip()
    if not topic or not start_at:
        return (
            "排课需要：topic（课题）与 start_at（上课时间，ISO 格式如 2026-08-30T15:00:00）。"
            "学生给的是自然语言时间时，请先换算成 ISO。"
        )
    minutes = int(kwargs.get("minutes") or 45)
    goal = str(kwargs.get("goal") or "").strip()
    series_count = int(kwargs.get("series_count") or 0)

    llm = build_llm_provider(ctx.config.models.llm)
    outline = None
    try:
        if series_count:
            stage = _STAGE_MAP.get(getattr(ctx.student, "stage", "") or "", "初中")
            try:
                outline = await asyncio.to_thread(
                    classroom.design_outline,
                    llm,
                    subject=subject,
                    goal=goal or topic,
                    count=max(2, min(series_count, 12)),
                    stage=stage,
                )
            except Exception:
                outline = None  # 大纲设计失败 → 退化为同主题系列课
    finally:
        await llm.aclose()

    try:
        result = classroom.create_course(
            ctx.engine,
            student_id=ctx.student.id,
            subject=subject,
            topic=topic,
            start_local=start_at,
            minutes=minutes,
            series_count=series_count if outline else 0,
            goal=goal,
            outline=outline,
        )
    except ToolError as exc:
        return f"排课失败：{exc}"

    lines = [f"排课成功！{subject}·{topic}"]
    if outline:
        lines.append("系列课大纲（每周同一时间）：")
        for i, inst in enumerate(result["instances"], 1):
            lines.append(f"  第{i}节「{inst['title']}」{inst['start']}")
    else:
        inst = result["instances"][0]
        lines.append(f"时间：{inst['start']}（{minutes} 分钟）")
    lines.append("开课前 1 小时我会自动备课，到点推送开课。要改时间或取消随时说。")
    return "\n".join(lines)


async def course_list(ctx: ToolContext, kwargs: dict[str, Any]) -> str:
    """查看课程安排（最近 20 节）。"""
    if ctx.student is None:
        return "当前没有学生上下文。"
    rows = classroom.list_courses(ctx.engine, ctx.student.id)
    if not rows:
        return "还没有排课。对我说『帮我约一节数学课，周六下午三点』即可排课。"
    mark = {"planned": "📅 已排", "ready": "📘 已备课", "ongoing": "🔔 上课中",
            "finished": "✅ 已完成", "cancelled": "❌ 已取消"}
    lines = [f"最近 {len(rows)} 节课（新→旧）："]
    for r in rows:
        lines.append(f"· #{r['id']}「{r['title']}」{r['start']} {mark.get(r['status'], r['status'])}")
    return "\n".join(lines)


async def reschedule_class(ctx: ToolContext, kwargs: dict[str, Any]) -> str:
    """应急改期。参数：course_id（课实例编号）、new_start（新时间 ISO）。"""
    if ctx.student is None:
        return "当前没有学生上下文。"
    cid = kwargs.get("course_id")
    new_start = str(kwargs.get("new_start") or "").strip()
    if cid is None or not new_start:
        return "改期需要：course_id（课编号，course_list 里查）与 new_start（新时间 ISO）。"
    try:
        ok = classroom.reschedule_course(ctx.engine, ctx.student.id, int(cid), new_start)
    except ToolError as exc:
        return f"改期失败：{exc}"
    if ok:
        return f"已改期：#{cid} → {classroom.fmt_local(classroom.to_utc(new_start))}。"
    return f"编号 {cid} 不在你的课程里。"


async def cancel_class(ctx: ToolContext, kwargs: dict[str, Any]) -> str:
    """应急取消课程。参数：course_id。"""
    if ctx.student is None:
        return "当前没有学生上下文。"
    cid = kwargs.get("course_id")
    if cid is None:
        return "请提供要取消的课编号（course_id，course_list 里查）。"
    ok = classroom.cancel_course(ctx.engine, ctx.student.id, int(cid))
    return f"已取消课程 #{cid}。想重新排随时说。" if ok else f"编号 {cid} 不在你的课程里。"


async def prepare_class(ctx: ToolContext, kwargs: dict[str, Any]) -> str:
    """手动备课（到点会自动备课；学生着急提前看可手动触发）。参数：course_id。"""
    if ctx.student is None:
        return "当前没有学生上下文。"
    cid = kwargs.get("course_id")
    if cid is None:
        return "请提供课编号（course_id）。"
    try:
        result = await asyncio.to_thread(
            classroom.prepare_course,
            ctx.engine,
            int(cid),
            workspace=ctx.config.workspace,
            llm_spec=ctx.config.models.llm,
        )
    except ToolError as exc:
        return f"备课失败：{exc}"
    return f"{result['text']}\n讲义路径：{result['lecture_path']}"
