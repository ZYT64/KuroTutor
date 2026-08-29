"""消息入口。

渠道收到学生一条私聊消息后，交给 :class:`MessageEntry` 处理：
查找/创建会话 → 装配历史 → 交给 :class:`Agent` 跑 → 持久化消息 → 返回最终回复。
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session as OrmSession
from sqlmodel import delete, select

from kurotutor.agent.conversation import (
    Action,
    compress_history,
    decide_segment,
)
from kurotutor.agent.core import Agent, AgentResponse
from kurotutor.agent.registry import ToolRegistry
from kurotutor.config.schema import AppConfig
from kurotutor.core import get_logger, log_event
from kurotutor.services.llm import ChatMessage
from kurotutor.storage import PendingRecord, Session, Student, WorkingContext
from kurotutor.tools.wrongbook import record_wrong_question

log = get_logger("entry")

# 学生「确认记录」的触发词（仅在存在待确认错题时生效，避免误触发）
_CONFIRM_TOKENS = (
    "记入",
    "记下",
    "记录",
    "记吧",
    "记进",
    "加错题",
    "错题本",
    "要记",
    "好记",
    "嗯记",
    "记着",
    "记一下",
)
# 学生「明确要答案」的信号 —— 用于放宽「引导式」，避免一直追问
_ANSWER_TOKENS = (
    "直接告诉",
    "直接给答案",
    "别引导",
    "别问了",
    "讲吧",
    "我不会",
    "卡住了",
    "卡死",
    "给答案",
    "我要答案",
    "做不来",
    "看不懂",
    "直接做给我",
    "给我答案",
    "速解",
    "快给我",
)


def _is_record_confirmation(text: str) -> bool:
    return any(tok in text for tok in _CONFIRM_TOKENS)


def _wants_direct_answer(text: str) -> bool:
    return any(tok in text for tok in _ANSWER_TOKENS)


def persist_message(db: OrmSession, *, session_id: int, role: str, content: str) -> None:
    """写入一条消息到数据库（L1 记忆层）。"""
    from kurotutor.storage.models import Message

    db.add(
        Message(
            session_id=session_id,
            role=role,
            content=content,
            created_at=datetime.now(UTC),
        )
    )


def _history_from_db(db: OrmSession, session_id: int, limit: int = 12) -> list[ChatMessage]:
    """读取最近若干条消息作为本轮历史（最近原文，更早由压缩层接管）。"""
    from sqlmodel import select

    from kurotutor.storage.models import Message

    rows = db.exec(
        select(Message).where(Message.session_id == session_id).order_by(Message.id.desc()).limit(limit)
    ).all()
    # 按时间正序返回
    chats: list[ChatMessage] = []
    for row in reversed(rows):
        if row.role in ("user", "assistant"):
            chats.append(ChatMessage(role=row.role, content=row.content))
    return chats


def _last_message(db: OrmSession, session_id: int) -> tuple[str, datetime] | None:
    """取该会话最近一条 user/assistant 消息的 (内容, 时间)；无则 None。"""
    from kurotutor.storage.models import Message

    row = db.exec(
        select(Message)
        .where(Message.session_id == session_id, Message.role.in_(["user", "assistant"]))
        .order_by(Message.id.desc())
        .limit(1)
    ).first()
    if row is None:
        return None
    return row.content, row.created_at


class MessageEntry:
    """单条学生消息的完整处理入口。"""

    def __init__(self, config: AppConfig, registry: ToolRegistry, engine: Any):
        self._config = config
        self._registry = registry
        self._engine = engine

    async def handle(
        self,
        *,
        student_id: int,
        text: str | None = None,
        images: list[str] | None = None,
        session_id: int | None = None,
    ) -> AgentResponse:
        """处理一条学生消息。images 为图片落盘路径（当前工作区内）。

        分四段，避免 SQLite 锁冲突：
        1) 短事务：定位/建会话、取学生、取历史 → 提交（释放写锁）。
        2) 确认处理：若学生确认记错题且存在待确认记录 → 确定性落库并清除。
        3) 跑 Agent：此间工具会另开会话写库，必须无未提交事务抢占。
        4) 新事务：持久化本轮 user/assistant 消息。
        """
        from kurotutor.storage import session_scope

        # 段 1：短事务（提交即释放写锁），定位会话并做「无感分段 + 分层压缩」
        raw_text = text or ""
        with session_scope(self._engine) as db:
            student = db.get(Student, student_id)
            if student is None:
                raise ValueError(f"学生不存在：{student_id}")
            if session_id is None:
                sess = Session(student_id=student_id)
                db.add(sess)
                db.flush()
                session_id = sess.id
            else:
                sess = db.get(Session, session_id)
                if sess is None:
                    sess = Session(student_id=student_id)
                    db.add(sess)
                    db.flush()
                    session_id = sess.id
            # 无感分段：最近一条消息的时间/话题决定并入还是开新段
            from kurotutor.storage.models import Message

            current_history = _history_from_db(db, session_id, limit=40)
            last = _last_message(db, session_id)
            if last is not None:
                decision = decide_segment(last[0], raw_text, prev_time=last[1])
                if decision.action == Action.SPLIT:
                    # 另开新段：把旧会话压缩成「背景」写入新段，新段（含后续轮次）也能记住之前内容
                    bg = compress_history(current_history)
                    bg_text = "\n".join(m.content for m in bg if m.content)
                    sess = Session(student_id=student_id)
                    db.add(sess)
                    db.flush()
                    session_id = sess.id
                    history = []
                    if bg_text.strip():
                        db.add(
                            Message(
                                session_id=session_id,
                                role="assistant",
                                content=f"【此前对话背景】\n{bg_text[:800]}",
                                created_at=datetime.now(UTC),
                            )
                        )
                        history = [
                            ChatMessage(role="assistant", content=f"【此前对话背景】\n{bg_text[:800]}")
                        ]
                else:
                    history = compress_history(current_history)
            else:
                history = compress_history(current_history)

        # 组合用户可见内容（图片路径占位，供模型理解）
        user_content = text or ""
        if images:
            img_note = "".join(f"\n[用户发送了图片，路径：{p}]" for p in images)
            user_content = (user_content + img_note).strip() or img_note.strip()

        # 段 2：确认记录处理 + 答案出口 + 跨轮上下文
        pending = _load_pending(self._engine, student_id)
        if pending and user_content and _is_record_confirmation(user_content):
            payload = json.loads(pending.payload or "{}")
            if payload.get("question"):
                record_wrong_question(self._engine, student_id, payload)
                _clear_pending(self._engine, student_id)
                user_content = (user_content + "\n（系统：已按你的确认，把这道题记入错题本。）").strip()

        # 学生明确要答案 → 追加强提示，避免「引导式」无出口
        if user_content and _wants_direct_answer(user_content):
            user_content = (
                user_content + "\n【学生明确要答案】请直接给出完整解答和关键方法，不要再引导提问。"
            ).strip()

        # 上一轮正在讲的题 → 注入为背景，供顺滑衔接
        working_context = _load_working_context(self._engine, student_id)

        # 段 3：跑 Agent（此刻无未提交事务，工具可自由写库）
        agent = Agent(
            self._config,
            self._registry,
            self._engine,
            student=student,
            session_id=session_id,
        )
        response = await agent.run(user_content, history=history, working_context=working_context)

        # 段 4：新事务持久化本轮消息
        with session_scope(self._engine) as db:
            persist_message(db, session_id=session_id, role="user", content=user_content)
            if response.text:
                persist_message(db, session_id=session_id, role="assistant", content=response.text)
            sess = db.get(Session, session_id)
            if sess is not None:
                sess.updated_at = datetime.now(UTC)

        # 段 5（自进化·后台）：每 6 条学生发言，异步提取长期记忆事实（失败静默，不影响回复）
        if response.ok and user_content:
            self._maybe_extract_facts(student_id=student_id, user_text=user_content)
        return response

    def _maybe_extract_facts(self, *, student_id: int, user_text: str) -> None:
        """每 6 条学生发言触发一次事实提取（后台线程，静默失败）。"""
        import asyncio as _asyncio
        import contextlib as _contextlib

        from kurotutor.services.memory import count_user_messages, extract_and_store_facts

        def _job():
            try:
                n = count_user_messages(self._engine, student_id)
                if n >= 6 and n % 6 == 0:
                    new = extract_and_store_facts(
                        self._engine, student_id, self._config.models.llm, user_text
                    )
                    if new:
                        log_event(log, "memory facts stored", student=student_id, count=len(new))
            except Exception as exc:  # 自进化永不影响主流程
                import traceback as _tb

                log_event(log, "memory facts skipped", level="warning",
                          error=repr(exc), tb=_tb.format_exc()[-400:])

        with _contextlib.suppress(RuntimeError):
            _asyncio.get_running_loop().run_in_executor(None, _job)


def _load_pending(engine: Any, student_id: int) -> PendingRecord | None:
    from kurotutor.storage import session_scope

    with session_scope(engine) as db:
        return db.exec(
            select(PendingRecord)
            .where(PendingRecord.student_id == student_id)
            .order_by(PendingRecord.id.desc())
            .limit(1)
        ).first()


def _clear_pending(engine: Any, student_id: int) -> None:
    from kurotutor.storage import session_scope

    with session_scope(engine) as db:
        db.exec(delete(PendingRecord).where(PendingRecord.student_id == student_id))


def _load_working_context(engine: Any, student_id: int) -> dict | None:
    """读取该学生上一轮正在讲的题（跨轮背景），无则返回 None。"""
    from kurotutor.storage import session_scope

    with session_scope(engine) as db:
        wc = db.get(WorkingContext, student_id)
        if wc is None or not wc.current_problem:
            return None
        try:
            data = json.loads(wc.current_problem)
        except (ValueError, TypeError):
            return None
        return data if isinstance(data, dict) and data.get("question_text") else None
