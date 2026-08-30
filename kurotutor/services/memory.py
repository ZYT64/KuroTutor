"""校本同步与长期记忆服务：学校进度登记 + 对话后事实提取（自进化）。"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from sqlmodel import func, select

from kurotutor.core.logging import get_logger, log_event
from kurotutor.storage import Message, SchoolProgress, Session, Student, session_scope

log = get_logger("memory")


def get_school_progress(engine: Any, student_id: int) -> SchoolProgress | None:
    with session_scope(engine) as db:
        return db.get(SchoolProgress, student_id)


def set_school_progress(engine: Any, student_id: int, **fields: Any) -> SchoolProgress:
    with session_scope(engine) as db:
        row = db.get(SchoolProgress, student_id)
        if row is None:
            row = SchoolProgress(student_id=student_id)
            db.add(row)
        for k, v in fields.items():
            if v:
                setattr(row, k, str(v)[:200])
        from datetime import datetime as _dt

        row.updated_at = _dt.now().strftime("%Y-%m-%d %H:%M")
        db.add(row)
        return row


# ---- 自进化：对话后事实提取（长期记忆写入） ----------------------------------

_FACT_PROMPT = (
    "从下面这段师生对话中，提取**值得长期记住的学生事实**（目标/考试安排/偏好/纠正/家庭情况等），"
    "每条一句话、以『学生』开头。没有值得记的就返回空数组。"
    '只输出 JSON：{{"facts":["..."]}}。\n\n对话：\n{convo}'
)


def extract_and_store_facts(
    engine: Any, student_id: int, llm_spec: Any, convo_text: str, *, max_facts: int = 3
) -> list[str]:
    """从对话提取学生事实并追加到 Student.note（[事实] 行，去重）。返回新增事实列表。"""
    if not convo_text or len(convo_text.strip()) < 40:
        return []
    from kurotutor.services.llm import ChatMessage, build_llm_provider
    from kurotutor.services.vision import extract_json

    llm = build_llm_provider(llm_spec)

    import asyncio as _asyncio

    async def _run():
        prompt = _FACT_PROMPT.format(convo=convo_text[:2000])
        r = await llm.complete([ChatMessage(role="user", content=prompt)], temperature=0.2)
        data = extract_json(r.content or "")
        return [str(f).strip() for f in (data or {}).get("facts") or [] if str(f).strip()][:max_facts]

    try:
        facts = _asyncio.run(_run())
    except Exception as exc:
        log_event(log, "fact extraction failed", level="warning", error=repr(exc))
        return []
    finally:
        import contextlib

        with contextlib.suppress(Exception):
            _asyncio.run(llm.aclose())
    if not facts:
        return []
    with session_scope(engine) as db:
        st = db.get(Student, student_id)
        if st is None:
            return []
        note = st.note or ""
        new = [f for f in facts if f[:30] not in note]  # 去重：前 30 字相同视为已记
        if not new:
            return []
        stamp_lines = [f"[记忆 {_asyncio_done_stamp()}] {f}" for f in new]
        st.note = (note + "\n" if note else "") + "\n".join(stamp_lines)
        db.add(st)
    return new


def _asyncio_done_stamp() -> str:
    from datetime import datetime as _dt

    return _dt.now().strftime("%m-%d")


def count_user_messages(engine: Any, student_id: int) -> int:
    """学生累计 user 消息条数（事实提取频率控制用）。消息经 Session 关联学生。"""
    with session_scope(engine) as db:
        session_ids = db.exec(
            select(Session.id).where(Session.student_id == student_id)
        ).all()
        if not session_ids:
            return 0
        n = db.exec(
            select(func.count()).select_from(Message).where(Message.session_id.in_(session_ids))
        ).one()
    return int(n)


# ---- Agent 自进化：教训存储与注入 -----------------------------------------------

_LESSONS_KEY = "agent_lessons"


def store_agent_lesson(engine: Any, student_id: int, lesson: str) -> None:
    """存一条 Agent 自我教训（最多保留 10 条，新覆盖旧）。"""
    from kurotutor.storage import WorkingContext
    from kurotutor.storage import session_scope as scope

    with scope(engine) as db:
        wc = db.get(WorkingContext, student_id)
        if wc is None:
            wc = WorkingContext(student_id=student_id)
            db.add(wc)
        try:
            data = json.loads(wc.current_problem or "{}")
        except Exception:
            data = {}
        lessons = data.get(_LESSONS_KEY, [])
        lessons.append({"text": lesson[:200], "ts": datetime.now(UTC).isoformat()})
        data[_LESSONS_KEY] = lessons[-10:]  # 保留最近 10 条
        wc.current_problem = json.dumps(data, ensure_ascii=False)
        db.add(wc)


def get_agent_lessons(engine: Any, student_id: int) -> list[str]:
    """读取最近的 Agent 自我教训文本列表。"""
    from kurotutor.storage import WorkingContext
    from kurotutor.storage import session_scope as scope

    with scope(engine) as db:
        wc = db.get(WorkingContext, student_id)
        if wc is None:
            return []
        try:
            data = json.loads(wc.current_problem or "{}")
        except Exception:
            return []
        lessons = data.get(_LESSONS_KEY, [])
        return [item["text"] for item in lessons if isinstance(item, dict) and item.get("text")]


def reflect_and_store_lesson(
    engine: Any,
    llm: Any,
    student_id: int,
    *,
    user_message: str,
    agent_response: str,
    tool_calls: list[dict],
    iterations: int,
    error: str,
) -> None:
    """对话后自我反思：如果犯错或用了太多轮，用 LLM 生成教训并存起来。"""
    import json as _json

    # 只在有问题的对话后才触发反思
    has_error = bool(error)
    too_many_iterations = iterations >= 4
    if not has_error and not too_many_iterations:
        return

    tool_summary = _json.dumps(
        [{"name": tc.get("name", ""), "args": str(tc.get("arguments", ""))[:80]} for tc in tool_calls[-5:]],
        ensure_ascii=False,
    )
    prompt = (
        "你是一个 AI 家教的自我反思模块。刚才的对话中出了些问题，"
        "请生成一句简洁的教训（50 字以内），"
        "格式：『遇到[情况]时，应该[正确做法]。』\n"
        f"学生消息：{user_message[:200]}\n"
        f"Agent 回复：{agent_response[:200]}\n"
        f"使用的工具：{tool_summary}\n"
        f"迭代次数：{iterations}（>=4 表示效率低）\n"
        f"错误：{error[:200] if error else '无（但迭代次数过多）'}\n"
        "只输出教训文本，不要其他内容。"
    )

    async def _reflect():
        from kurotutor.services.llm import ChatMessage as CM

        r = await llm.complete([CM(role="user", content=prompt)], temperature=0.3)
        return (r.content or "").strip()[:200]

    import asyncio as _aio

    try:
        lesson = _aio.run(_reflect())
        if lesson:
            store_agent_lesson(engine, student_id, lesson)
            log.info("agent lesson stored", lesson=lesson[:60])
    except Exception as exc:
        log.warning("agent reflection failed", error=str(exc))
