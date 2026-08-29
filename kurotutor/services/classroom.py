"""定时课堂服务：排课 → 备课 → 开课推送 → 课后闭环 → 系列课动态排下一节。

统一调度：所有课堂动作落地为 ScheduleTask（prepare/class_start/class_end），
由 serve 后台轮询 process_due 驱动；应急改期 = 移动 Instance + 重排任务。
时间约定：对外（工具/展示）用本地时间 ISO，入库统一转 UTC（与复习调度一致）。
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlmodel import select

from kurotutor.core.errors import ToolError
from kurotutor.core.logging import get_logger, log_event
from kurotutor.services import scheduler
from kurotutor.storage import (
    CourseInstance,
    CoursePlan,
    CourseStatus,
    KnowledgePoint,
    Student,
    session_scope,
)

log = get_logger("classroom")


# ---- 时间工具 ---------------------------------------------------------------


def to_utc(local_iso: str) -> datetime:
    """本地时间 ISO（无时区按本机时区）→ UTC。"""
    try:
        dt = datetime.fromisoformat(local_iso.strip())
    except ValueError as exc:
        raise ToolError(
            "时间格式无法解析",
            cause=f"{local_iso!r}",
            fix="用 ISO 格式，如 2026-08-30T15:00:00（本机时区）",
        ) from exc
    if dt.tzinfo is None:
        dt = dt.astimezone()
    return dt.astimezone(UTC)


def fmt_local(dt_utc: datetime) -> str:
    """UTC → 本地可读（给学生看）。"""
    return dt_utc.astimezone().strftime("%m-%d %H:%M")


# ---- 排课 -------------------------------------------------------------------


def create_course(
    engine: Any,
    *,
    student_id: int,
    subject: str,
    topic: str,
    start_local: str,
    minutes: int = 45,
    series_count: int = 0,
    goal: str = "",
    outline: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """排课。

    - 单堂课：series_count=0，创建 1 个 Instance；
    - 系列课：outline 为 LLM 设计的大纲（[{title, topic}]），从 start_local 起每周同一时间排
      series_count 节，并挂第一节备课任务。
    返回 {"plan_id", "instances": [{id, title, start}], "task_ids"}。
    """
    start_utc = to_utc(start_local)
    minutes = max(15, min(int(minutes), 180))
    kind = "series" if series_count else "single"
    with session_scope(engine) as db:
        plan = CoursePlan(
            student_id=student_id,
            kind=kind,
            title=f"{subject}·{topic}" + (f"（{series_count} 节）" if series_count else ""),
            subject=subject,
            goal=goal,
            schedule=json.dumps({"start_local": start_local, "minutes": minutes}, ensure_ascii=False),
            status=CourseStatus.PLANNED,
        )
        db.add(plan)
        db.flush()
        plan_id = plan.id
        sections = outline if outline else [{"title": topic, "topic": topic}]
        instances: list[dict[str, Any]] = []
        for i, sec in enumerate(sections[: max(series_count, 1)]):
            start = start_utc + timedelta(weeks=i)
            inst = CourseInstance(
                plan_id=plan_id,
                student_id=student_id,
                title=str(sec.get("title") or topic),
                start_at=start,
                status=CourseStatus.PLANNED,
            )
            db.add(inst)
            db.flush()
            instances.append({"id": inst.id, "title": inst.title, "start": start})
    # 调度任务（第一节：提前 1 小时备课、准点开课、下课后闭环）
    task_ids = _schedule_tasks_for(engine, plan_id, student_id, instances[0], minutes)
    # 系列课：后续各节只挂开课/下课（备课在其前一节结束时触发）
    for inst in instances[1:]:
        scheduler.create_task(
            engine,
            student_id=student_id,
            kind=scheduler.Kinds.CLASS_START,
            fire_at=inst["start"],
            payload={"instance_id": inst["id"], "plan_id": plan_id},
        )
    return {
        "plan_id": plan_id,
        "instances": [
            {"id": i["id"], "title": i["title"], "start": fmt_local(i["start"])} for i in instances
        ],
        "task_ids": task_ids,
    }


def _schedule_tasks_for(engine: Any, plan_id: int, student_id: int, inst: dict, minutes: int) -> list[int]:
    ids = [
        scheduler.create_task(
            engine,
            student_id=student_id,
            kind=scheduler.Kinds.PREPARE,
            fire_at=inst["start"] - timedelta(hours=1),
            payload={"instance_id": inst["id"], "plan_id": plan_id},
        ),
        scheduler.create_task(
            engine,
            student_id=student_id,
            kind=scheduler.Kinds.CLASS_START,
            fire_at=inst["start"],
            payload={"instance_id": inst["id"], "plan_id": plan_id},
        ),
        scheduler.create_task(
            engine,
            student_id=student_id,
            kind=scheduler.Kinds.CLASS_END,
            fire_at=inst["start"] + timedelta(minutes=minutes),
            payload={"instance_id": inst["id"], "plan_id": plan_id},
        ),
    ]
    return ids


def design_outline(llm, *, subject: str, goal: str, count: int, stage: str = "初中") -> list[dict[str, str]]:
    """系列课大纲：LLM 按目标设计 count 节课主题（同步入口，内部跑独立事件循环）。"""
    import asyncio as _asyncio

    prompt = (
        f"你是{stage}{subject}老师。学生目标：{goal or '系统提升'}。请设计 {count} 节 1v1 课的大纲：\n"
        "由浅入深、前后衔接，每节 45 分钟。只输出 JSON："
        '{{"outline":[{"title":"课标题","topic":"本节核心知识点"}]}}'
    )

    async def _run():
        from kurotutor.services.llm import ChatMessage
        from kurotutor.services.vision import extract_json

        r = await llm.complete([ChatMessage(role="user", content=prompt)], temperature=0.4)
        data = extract_json(r.content or "")
        return (data or {}).get("outline") or []

    return _asyncio.run(_run())


# ---- 备课 -------------------------------------------------------------------


def prepare_course(
    engine: Any,
    instance_id: int,
    *,
    workspace: str | None = None,
    llm_spec: Any = None,
) -> dict[str, Any]:
    """备课：读画像薄弱点 → 生成讲义落盘 → Instance 置 READY。

    workspace/llm_spec 缺省时读全局配置（serve 进程）；测试可显式注入。
    """
    from kurotutor.config.loader import load_config
    from kurotutor.services.llm import build_llm_provider

    cfg = load_config()
    workspace = workspace or cfg.workspace
    llm_spec = llm_spec or cfg.models.llm
    with session_scope(engine) as db:
        inst = db.get(CourseInstance, instance_id)
        if inst is None:
            raise ToolError("课程不存在", cause=f"instance_id={instance_id}")
        plan = db.get(CoursePlan, inst.plan_id)
        student = db.get(Student, inst.student_id)
        topic = inst.title
        subject = plan.subject if plan else ""
        weak = db.exec(
            select(KnowledgePoint)
            .where(
                KnowledgePoint.student_id == inst.student_id,
                KnowledgePoint.subject == subject,
                KnowledgePoint.confidence >= 0.2,
            )
            .order_by(KnowledgePoint.mastery.asc())
            .limit(2)
        ).all()
        weak_names = [f"{k.name}（掌握度 {k.mastery:.0%}）" for k in weak]
        stage_cn = {"primary": "小学", "junior": "初中", "senior": "高中", "university": "大学"}.get(
            student.stage if student else "", "初中"
        )
        nickname = (student.nickname if student else "") or "同学"

    outline_prompt = (
        f"你是{stage_cn}{subject}老师，正在为一节 45 分钟的 1v1 课备课。\n"
        f"课题：{topic}；学生：{nickname}。\n"
        + (f"学生薄弱点：{'、'.join(weak_names)}（备课要针对性覆盖）。" if weak_names else "")
        + "\n请输出这节课的：教学目标（2 条）、知识讲解要点（3-5 条）、"
        "例题 1 道（含解析）、课堂练习 2 道（不含答案）、课后作业 1 项。\n"
        "只输出 JSON：{\"goal\":[],\"points\":[],\"example\":{\"text\":\"\",\"analysis\":\"\"},"
        "\"practice\":[\"\"],\"homework\":\"\"}"
    )
    llm = build_llm_provider(llm_spec)
    try:
        from kurotutor.services.vision import extract_json

        r = asyncio.run(_asyncio_call(llm, outline_prompt))
        lesson = extract_json(r.content or "") or {}
    finally:
        with_suppress_close(llm)

    lessons = Path(workspace) / f"u{inst.student_id}" / "lessons"
    lessons.mkdir(parents=True, exist_ok=True)
    safe = "".join(c for c in topic if c not in '\\/:*?"<>|')[:40]
    lecture_path = lessons / f"{safe}.md"
    md = _render_lesson_md(topic, subject, lesson)
    lecture_path.write_text(md, encoding="utf-8")

    # 讲义 Word 版（QQ 发文件用；docs 不可用时保留 md 不阻塞备课）
    docx_path = lessons / f"{safe}.docx"
    try:
        from kurotutor.services.docs import write_document

        write_document(str(docx_path), md)
    except Exception as exc:  # LibreOffice/依赖缺失等，降级为仅 md
        log_event(log, "lecture docx render failed, keep md only", level="warning", error=str(exc))
        docx_path = None

    # OpenMAIC 互动课堂：提交生成任务（托管站约 3-10 分钟），后台轮询回填链接
    classroom_note = ""
    openmaic_job = None
    try:
        openmaic_job = _start_openmaic_classroom(engine, instance_id, cfg, topic, subject, stage_cn, md)
    except Exception as exc:
        log_event(log, "openmaic submit failed (prepare unaffected)", level="warning", error=str(exc))
    if openmaic_job:
        classroom_note = "互动课堂正在生成，开课时把链接发给你。"

    with session_scope(engine) as db:
        inst = db.get(CourseInstance, instance_id)
        inst.lecture_path = str(lecture_path)
        if docx_path is not None:
            inst.lecture_docx_path = str(docx_path)
        inst.status = CourseStatus.READY
        db.add(inst)

    greet = f"{nickname}" if nickname else "同学"
    start_at = fmt_local(_instance_start(engine, instance_id))
    text = (
        f"📚 备课完成！{greet}，你「{topic}」的课已经准备好了（{start_at} 上课）。\n"
        f"讲义已生成：{docx_path.name if docx_path else lecture_path.name}。"
        + (classroom_note + " " if classroom_note else "")
        + "上课时我会按讲义带你过一遍，还有例题和课堂练习。"
    )
    return {
        "text": text,
        "lecture_path": str(lecture_path),
        "docx_path": str(docx_path) if docx_path else "",
        "lesson": lesson,
    }


def _start_openmaic_classroom(
    engine: Any,
    instance_id: int,
    cfg: Any,
    topic: str,
    subject: str,
    stage_cn: str,
    lecture_md: str,
) -> dict[str, Any] | None:
    """提交 OpenMAIC 课堂生成任务；未配置访问码返回 None。成功后起后台线程轮询回填链接。"""
    maic = getattr(cfg, "openmaic", None)
    if maic is None or not (maic.access_code or "").strip():
        return None
    from kurotutor.services import openmaic as maic_svc
    from kurotutor.services.llm import build_llm_provider

    # 用主模型把讲义压成 500 字内的课程简介作为生成要求（省配额、聚焦课堂）
    brief_prompt = (
        f"把下面这份{stage_cn}{subject}讲义压缩成 300 字以内的课堂生成简介，"
        "包含：课题、教学目标、核心知识点、例题与练习安排。直接输出正文。\n\n" + lecture_md[:3000]
    )
    llm = build_llm_provider(cfg.models.llm)
    try:
        r = asyncio.run(_asyncio_call(llm, brief_prompt))
        brief = (r.content or "").strip()[:1500] or f"{stage_cn}{subject}课程：{topic}"
    finally:
        with_suppress_close(llm)

    job = asyncio.run(maic_svc.submit_generation(maic, requirement=brief))

    def _poll_and_save() -> None:
        try:
            result = asyncio.run(maic_svc.poll_generation(maic, str(job.get("pollUrl") or job["jobId"])))
            url = maic_svc.extract_classroom_url(result, maic.base_url)
            if url:
                with session_scope(engine) as db:
                    inst = db.get(CourseInstance, instance_id)
                    if inst:
                        inst.classroom_url = url
                        db.add(inst)
        except Exception as exc:
            log_event(log, "openmaic generation incomplete", level="warning",
              instance=instance_id, error=str(exc))

    threading.Thread(target=_poll_and_save, name=f"openmaic-{instance_id}", daemon=True).start()
    return job


def _instance_start(engine: Any, instance_id: int) -> datetime:
    with session_scope(engine) as db:
        inst = db.get(CourseInstance, instance_id)
        return inst.start_at if inst else datetime.now(UTC)


def _render_lesson_md(topic: str, subject: str, lesson: dict) -> str:
    lines = [f"# {subject}·{topic}（课堂讲义）", ""]
    for g in lesson.get("goal") or []:
        lines.append(f"- 🎯 {g}")
    lines += ["", "## 知识讲解要点", ""]
    for pnt in lesson.get("points") or []:
        lines.append(f"- {pnt}")
    ex = lesson.get("example") or {}
    if ex.get("text"):
        lines += ["", "## 例题", "", ex["text"], "", f"**解析**：{ex.get('analysis', '')}"]
    prac = lesson.get("practice") or []
    if prac:
        lines += ["", "## 课堂练习", ""]
        lines += [f"{i}. {p}" for i, p in enumerate(prac, 1)]
    if lesson.get("homework"):
        lines += ["", "## 课后作业", "", lesson["homework"]]
    return "\n".join(lines)


async def _asyncio_call(llm, prompt: str):
    from kurotutor.services.llm import ChatMessage

    return await llm.complete([ChatMessage(role="user", content=prompt)], temperature=0.4)


def with_suppress_close(llm):
    import contextlib

    with contextlib.suppress(Exception):
        close = getattr(llm, "aclose", None)
        if close:
            import asyncio as _a

            _a.run(close())


# ---- 开课 / 课后闭环 ---------------------------------------------------------


def start_class_text(engine: Any, instance_id: int) -> dict[str, str] | None:
    """开课推送：讲义要点 + 今日目标 + 互动课堂链接。返回 {text, docx_path}；无课返回 None。"""
    with session_scope(engine) as db:
        inst = db.get(CourseInstance, instance_id)
        if inst is None:
            return None
        db.get(CoursePlan, inst.plan_id)
        student = db.get(Student, inst.student_id)
        nickname = (student.nickname if student else "") or "同学"
        lecture = Path(inst.lecture_path) if inst.lecture_path else None
        docx_path = inst.lecture_docx_path or ""
        classroom_url = inst.classroom_url or ""
    md = lecture.read_text(encoding="utf-8") if lecture and lecture.exists() else ""
    points = [ln.lstrip("- ").strip() for ln in md.splitlines() if ln.startswith("- ")][:5]
    goal_lines = [ln.replace("- 🎯", "").replace("-", "").strip() for ln in md.splitlines() if "🎯" in ln][:2]
    text = f"🔔 {nickname}，上课啦！今天我们讲「{inst.title}」。\n"
    if goal_lines:
        text += "目标：" + "；".join(goal_lines) + "\n"
    if points:
        text += "今天的内容：\n" + "\n".join(f"· {p}" for p in points) + "\n"
    if classroom_url:
        text += f"🏫 互动课堂（AI 老师+AI 同学陪你学）：{classroom_url}\n"
    elif inst_has_pending_maic(engine, instance_id):
        text += "🏫 互动课堂还在生成中，稍后会补发链接。\n"
    text += "准备好了回复我，我们开始。有不懂的随时打断我。"
    with session_scope(engine) as db:
        inst = db.get(CourseInstance, instance_id)
        inst.status = CourseStatus.ONGOING
        db.add(inst)
    return {"text": text, "docx_path": docx_path}


def inst_has_pending_maic(engine: Any, instance_id: int) -> bool:
    """该课是否配了 OpenMAIC 但链接尚未回填（生成中）。"""
    maic_cfg = getattr(load_config_safe(), "openmaic", None)
    if maic_cfg is None or not (maic_cfg.access_code or "").strip():
        return False
    with session_scope(engine) as db:
        inst = db.get(CourseInstance, instance_id)
        return bool(inst and not inst.classroom_url)


def load_config_safe() -> Any:
    from kurotutor.config.loader import load_config

    return load_config()


def end_class(engine: Any, instance_id: int, *, llm_spec: Any = None) -> str | None:
    """课后闭环：总结 → 作业 → 进度 → 系列课排下一节并触发备课。返回推送文本。"""
    from kurotutor.config.loader import load_config
    from kurotutor.services.llm import build_llm_provider

    llm_spec = llm_spec or load_config().models.llm
    with session_scope(engine) as db:
        inst = db.get(CourseInstance, instance_id)
        if inst is None:
            return None
        plan = db.get(CoursePlan, inst.plan_id)
        student = db.get(Student, inst.student_id)
        nickname = (student.nickname if student else "") or "同学"
        md = Path(inst.lecture_path).read_text(encoding="utf-8") if inst.lecture_path else ""
        # 系列课：找下一节未上的课
        next_inst = (
            db.exec(
                select(CourseInstance)
                .where(CourseInstance.plan_id == inst.plan_id, CourseInstance.id != inst.id,
                       CourseInstance.status == CourseStatus.PLANNED)
                .order_by(CourseInstance.start_at)
                .limit(1)
            ).first()
            if plan
            else None
        )
    summary_prompt = (
        f"你刚上完一节 1v1 课「{inst.title}」，讲义如下：\n{md[:1500]}\n"
        f"请给学生 {nickname} 发下课总结：1) 今天学了什么（3 点内）；2) 课后作业（讲义里的作业或"
        "一道练习）；3) 一句鼓励。语气亲切，200 字内。"
    )
    llm = build_llm_provider(llm_spec)
    try:
        r = asyncio.run(_asyncio_call(llm, summary_prompt))
        summary = (r.content or "").strip()
    except Exception as exc:
        log_event(log, "class summary failed", level="warning", error=repr(exc))
        summary = f"今天「{inst.title}」的课程结束啦，记得完成讲义里的课后作业，有不懂的随时问我。"
    finally:
        with_suppress_close(llm)

    text = f"下课啦！{nickname}，今天的课到这里 🌙\n\n{summary}"
    if next_inst:
        text += f"\n\n📅 下一节课：「{next_inst.title}」，{fmt_local(next_inst.start_at)} 见！"

    with session_scope(engine) as db:
        inst = db.get(CourseInstance, instance_id)
        inst.status = CourseStatus.FINISHED
        db.add(inst)

    # 系列课动态排下一节：提前 1 小时备课
    if next_inst:
        _schedule_tasks_for(
            engine, next_inst.plan_id, next_inst.student_id,
            {"id": next_inst.id, "start": next_inst.start_at}, 45,
        )
        # 移除下一节旧的 class_start（避免重复推送）——保留最早的
    return text


# ---- 查询 / 应急 ------------------------------------------------------------


def list_courses(engine: Any, student_id: int) -> list[dict[str, Any]]:
    with session_scope(engine) as db:
        rows = db.exec(
            select(CourseInstance)
            .where(CourseInstance.student_id == student_id)
            .order_by(CourseInstance.start_at.desc())
            .limit(20)
        ).all()
    return [
        {
            "id": r.id,
            "title": r.title,
            "start": fmt_local(r.start_at),
            "status": r.status,
            "prepared": bool(r.lecture_path),
        }
        for r in rows
    ]


def cancel_course(engine: Any, student_id: int, instance_id: int) -> bool:
    """应急取消：Instance 取消 + 该课全部待执行任务取消。"""
    with session_scope(engine) as db:
        inst = db.get(CourseInstance, instance_id)
        if inst is None or inst.student_id != student_id:
            return False
        inst.status = CourseStatus.CANCELLED
        db.add(inst)
        plan_id = inst.plan_id
    from kurotutor.storage import ScheduleTask, TaskStatus

    with session_scope(engine) as db:
        tasks = db.exec(
            select(ScheduleTask).where(
                ScheduleTask.student_id == student_id, ScheduleTask.status == TaskStatus.PENDING
            )
        ).all()
        for t in tasks:
            payload = {}
            with contextlib.suppress(Exception):
                payload = json.loads(t.payload or "{}")
            if payload.get("instance_id") == instance_id and payload.get("plan_id") == plan_id:
                t.status = TaskStatus.CANCELLED
                db.add(t)
    return True


def reschedule_course(engine: Any, student_id: int, instance_id: int, new_start_local: str) -> bool:
    """应急改期：移动 Instance 起始时间并重排该课任务。"""
    new_start = to_utc(new_start_local)
    with session_scope(engine) as db:
        inst = db.get(CourseInstance, instance_id)
        if inst is None or inst.student_id != student_id:
            return False
        inst.start_at = new_start
        db.add(inst)
        plan_id = inst.plan_id
        minutes = 45
    from kurotutor.storage import ScheduleTask, TaskStatus

    with session_scope(engine) as db:
        tasks = db.exec(
            select(ScheduleTask).where(
                ScheduleTask.student_id == student_id, ScheduleTask.status == TaskStatus.PENDING
            )
        ).all()
        for t in tasks:
            payload = {}
            with contextlib.suppress(Exception):
                payload = json.loads(t.payload or "{}")
            if payload.get("instance_id") == instance_id and payload.get("plan_id") == plan_id:
                delta = {
                    scheduler.Kinds.PREPARE: timedelta(hours=-1),
                    scheduler.Kinds.CLASS_START: timedelta(0),
                    scheduler.Kinds.CLASS_END: timedelta(minutes=minutes),
                }.get(t.kind)
                if delta is not None:
                    t.fire_at = new_start + delta
                    db.add(t)
    return True
