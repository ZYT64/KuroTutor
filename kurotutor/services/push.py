"""推送服务：把到期任务（复习/提醒/课堂）生成消息并发给学生。

- :func:`build_review_text`：把该生到期错题转成一条复习推送文本（无到期返回 None）。
- :func:`make_handlers`：构造 :func:`~kurotutor.services.scheduler.process_due` 需要的
  ``{kind: handler}`` 分发表；handler 查学生 external_id 后调用 ``deliver(external_id, text)``。

``deliver`` 由调用方（serve）注入，负责真正落地到渠道（控制台打印 / QQ 主动推送）。
QQ 私信主动推送每日每人限 2 条（botpy 限制），由渠道自行限额。
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from kurotutor.core import get_logger, log_event
from kurotutor.services import scheduler
from kurotutor.services.review import due_for_student
from kurotutor.storage import Student, session_scope

log = get_logger("push")


def build_review_text(engine: Any, student_id: int) -> str | None:
    """生成复习推送文本；该生无到期错题返回 None。"""
    due = due_for_student(engine, student_id)
    if not due:
        return None
    lines = ["💡 到复习时间啦，这几道错题帮你巩固一下："]
    for wq in due[:8]:
        lines.append(f"- {(wq.subject or '')}: {wq.question_text[:50]}")
    lines.append("回复『开始复习』我一道道出题，帮你真正掌握。")
    return "\n".join(lines)


def make_handlers(engine: Any, deliver: Callable[[str, str], None]) -> dict[str, Callable[[Any], None]]:
    """构造 process_due 的分发表。``deliver(external_id, text)`` 负责发送。"""

    def handle_review(task: Any) -> None:
        if task.student_id is None:
            return
        text = build_review_text(engine, task.student_id)
        if not text:
            return
        external_id = _external_id(engine, task.student_id)
        if external_id:
            log_event(log, "push review", student=external_id)
            deliver(external_id, text)

    def handle_message(task: Any) -> None:
        # 提醒/作业提醒/周报：payload 里带 message，直接推送
        import contextlib
        import json

        payload: dict = {}
        with contextlib.suppress(ValueError, TypeError):
            payload = json.loads(task.payload or "{}")
        text = payload.get("message")
        if not text:
            return
        external_id = _external_id(engine, task.student_id) if task.student_id else None
        if external_id:
            deliver(external_id, text)

    def _instance_payload(task: Any) -> int | None:
        import contextlib
        import json

        with contextlib.suppress(ValueError, TypeError):
            payload = json.loads(task.payload or "{}")
            iid = payload.get("instance_id")
            if iid is not None:
                return int(iid)
        return None

    def handle_prepare(task: Any) -> None:
        """到点自动备课：生成讲义 → 推送就绪通知。"""
        iid = _instance_payload(task)
        if iid is None or task.student_id is None:
            return
        try:
            from kurotutor.services.classroom import prepare_course

            result = prepare_course(engine, iid)
        except Exception as exc:
            log_event(log, "prepare failed", level="warning", instance=iid, error=repr(exc))
            return
        external_id = _external_id(engine, task.student_id)
        if external_id:
            deliver(external_id, result["text"])

    def handle_class_start(task: Any) -> None:
        """到点开课：推送讲义要点与今日目标。"""
        iid = _instance_payload(task)
        if iid is None or task.student_id is None:
            return
        try:
            from kurotutor.services.classroom import start_class_text

            text = start_class_text(engine, iid)
        except Exception as exc:
            log_event(log, "class start failed", level="warning", instance=iid, error=repr(exc))
            return
        external_id = _external_id(engine, task.student_id)
        if external_id and text:
            deliver(external_id, text)

    def handle_class_end(task: Any) -> None:
        """到点下课：课后闭环（总结/作业/进度/下一节排课）。"""
        iid = _instance_payload(task)
        if iid is None or task.student_id is None:
            return
        try:
            from kurotutor.services.classroom import end_class

            text = end_class(engine, iid)
        except Exception as exc:
            log_event(log, "class end failed", level="warning", instance=iid, error=repr(exc))
            return
        external_id = _external_id(engine, task.student_id)
        if external_id and text:
            deliver(external_id, text)

    def handle_report(task: Any) -> None:
        """周报：生成 → 推送 → 自动排下一周（payload.weekly 时循环）。"""
        import contextlib
        import json

        with contextlib.suppress(ValueError, TypeError):
            payload = json.loads(task.payload or "{}")
        if not (payload or {}).get("weekly"):
            handle_message(task)
            return
        if task.student_id is None:
            return
        try:
            from kurotutor.config.loader import load_config
            from kurotutor.services.report import build_weekly_report

            cfg = load_config()
            result = build_weekly_report(
                engine, task.student_id, llm_spec=cfg.models.llm, workspace=cfg.workspace
            )
        except Exception as exc:
            log_event(log, "weekly report failed", level="warning", error=repr(exc))
            return
        external_id = _external_id(engine, task.student_id)
        if external_id:
            text = result["text"] + (f"\n\n📄 周报文档：{result['path']}" if result["path"] else "")
            deliver(external_id, text)
        # 循环：排下一周
        from datetime import timedelta as _td

        from kurotutor.services import scheduler as _sched

        _sched.create_task(
            engine,
            student_id=task.student_id,
            kind=_sched.Kinds.REPORT,
            fire_at=task.fire_at + _td(days=7),
            payload={"weekly": True},
        )

    return {
        scheduler.Kinds.REVIEW: handle_review,
        scheduler.Kinds.REMINDER: handle_message,
        scheduler.Kinds.HOMEWORK: handle_message,
        scheduler.Kinds.REPORT: handle_report,
        scheduler.Kinds.PREPARE: handle_prepare,
        scheduler.Kinds.CLASS_START: handle_class_start,
        scheduler.Kinds.CLASS_END: handle_class_end,
    }


def _external_id(engine: Any, student_id: int) -> str | None:
    with session_scope(engine) as db:
        st = db.get(Student, student_id)
        return st.external_id if st else None
