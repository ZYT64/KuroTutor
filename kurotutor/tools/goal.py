"""目标管理 + 打卡激励工具。"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from sqlmodel import select

from kurotutor.agent.context import ToolContext
from kurotutor.storage import CheckIn, StudentGoal, session_scope


async def goal_set(ctx: ToolContext, kwargs: dict[str, Any]) -> str:
    """登记/更新学习目标。参数：goal（必填）、subject、target_date、progress。"""
    if ctx.student is None:
        return "当前没有学生上下文。"
    goal = str(kwargs.get("goal") or "").strip()
    if not goal:
        return "请告诉我目标内容（goal），如『期末数学上 110 分』。"
    subject = str(kwargs.get("subject") or "").strip()
    target_date = str(kwargs.get("target_date") or "").strip()
    progress = str(kwargs.get("progress") or "").strip()
    with session_scope(ctx.engine) as db:
        existing = db.exec(
            select(StudentGoal).where(
                StudentGoal.student_id == ctx.student.id,
                StudentGoal.status == "active",
                StudentGoal.subject == subject,
            )
        ).all()
        for g in existing:
            if g.goal[:30] == goal[:30]:
                return f"这个目标已经登记过了（{g.created_at:%m-%d}），可以在 goal_list 里查看。"
        g = StudentGoal(
            student_id=ctx.student.id,
            subject=subject,
            goal=goal,
            target_date=target_date,
            progress=progress,
            status="active",
        )
        db.add(g)
        db.flush()
        gid = g.id
    parts = [f"🎯 目标已登记（编号 {gid}）：{goal}"]
    if target_date:
        parts.append(f"目标日期：{target_date}")
    parts.append("我会按这个目标帮你安排出题和课程，进度随时可查（goal_list）。")
    return "；".join(parts)


async def goal_list(ctx: ToolContext, kwargs: dict[str, Any]) -> str:
    """查看学习目标（active + 近期完成）。"""
    if ctx.student is None:
        return "当前没有学生上下文。"
    with session_scope(ctx.engine) as db:
        rows = db.exec(
            select(StudentGoal)
            .where(StudentGoal.student_id == ctx.student.id)
            .order_by(StudentGoal.id.desc())
            .limit(10)
        ).all()
    active = [r for r in rows if r.status == "active"]
    if not rows:
        return "还没有登记目标。告诉我你的目标（如『期末数学上 110 分』），我帮你追踪进度。"
    lines = [f"学习目标（{len(active)} 个进行中）："]
    for r in rows:
        mark = "🎯" if r.status == "active" else ("✅" if r.status == "done" else "🗑️")
        line = f"{mark} #{r.id} {r.goal}"
        if r.subject:
            line += f"（{r.subject}）"
        if r.target_date:
            line += f" 截止：{r.target_date}"
        if r.progress:
            line += f" 进度：{r.progress}"
        lines.append(line)
    return "\n".join(lines)


async def goal_update(ctx: ToolContext, kwargs: dict[str, Any]) -> str:
    """更新目标进度/状态。参数：goal_id、progress（可选）、status（done/dropped，可选）。"""
    if ctx.student is None:
        return "当前没有学生上下文。"
    gid = kwargs.get("goal_id")
    if gid is None:
        return "请提供 goal_id（goal_list 里查）。"
    progress = str(kwargs.get("progress") or "").strip()
    status = str(kwargs.get("status") or "").strip().lower()
    if status and status not in ("active", "done", "dropped"):
        return "status 只支持 active/done/dropped。"
    with session_scope(ctx.engine) as db:
        g = db.get(StudentGoal, int(gid))
        if g is None or g.student_id != ctx.student.id:
            return f"编号 {gid} 不在你的目标里。"
        if progress:
            g.progress = progress[:200]
        if status:
            g.status = status
        db.add(g)
        g_goal, g_status = g.goal, g.status
    if g_status == "done":
        return f"🎉 恭喜！目标「{g_goal}」达成！这是坚持的功劳，下一个目标想好了随时说。"
    return f"目标 #{gid} 已更新。"


async def daily_checkin(ctx: ToolContext, kwargs: dict[str, Any]) -> str:
    """每日学习打卡：连续天数统计 + 里程碑鼓励。参数：note（今日一句话，可选）。"""
    if ctx.student is None:
        return "当前没有学生上下文。"
    note = str(kwargs.get("note") or "").strip()
    today = datetime.now().strftime("%Y-%m-%d")
    with session_scope(ctx.engine) as db:
        dup = db.exec(
            select(CheckIn).where(CheckIn.student_id == ctx.student.id, CheckIn.date == today)
        ).first()
        if dup:
            streak = _streak(db, ctx.student.id)
            return f"今天（{today}）已经打过卡啦 ✅ 连续 {streak} 天的记录我记着呢，明天再来！"
        ci = CheckIn(student_id=ctx.student.id, date=today, note=note[:200])
        db.add(ci)
        dates = db.exec(
            select(CheckIn.date)
            .where(CheckIn.student_id == ctx.student.id)
            .order_by(CheckIn.date.desc())
            .limit(400)
        ).all()
    # 连续天数（含今天）
    streak = 0
    day = datetime.now()
    dset = set(dates)
    while day.strftime("%Y-%m-%d") in dset:
        streak += 1
        day -= timedelta(days=1)
    milestone = ""
    if streak in (3, 7, 14, 30, 60, 100):
        milestone = f"\n🏅 连续打卡 {streak} 天——这是里程碑！坚持本身就是最难得的学习能力。"
    lines = [f"✅ 打卡成功（{today}）！连续打卡 {streak} 天 🔥"]
    if note:
        lines.append(f"今日一句话：{note}")
    lines.append(
        milestone or ("每天进步一点点，坚持就是胜利。" if streak > 1 else "万事开头难，今天已经赢在起点。")
    )
    return "\n".join(lines)


def _streak(db, student_id: int) -> int:
    dates = db.exec(
        select(CheckIn.date).where(CheckIn.student_id == student_id).order_by(CheckIn.date.desc()).limit(400)
    ).all()
    day = datetime.now()
    dset = set(dates)
    n = 0
    while day.strftime("%Y-%m-%d") in dset:
        n += 1
        day -= timedelta(days=1)
    return n
