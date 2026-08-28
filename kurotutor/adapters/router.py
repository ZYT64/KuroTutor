"""消息路由：Student 解析 → Agent 处理 → 长内容切分 → 响应列表。

渠道收到消息后调用 :meth:`Router.handle`，得到待发送的响应列表，
再交给渠道逐条发送。路由层把「学生是谁、内容怎么分流」与「怎么收」解耦。
"""

from __future__ import annotations

from typing import Any

from sqlmodel import select

from kurotutor.adapters.message import (
    OutboundMessage,
    should_split,
    split_text,
)
from kurotutor.agent.entry import MessageEntry
from kurotutor.agent.queue import Priority
from kurotutor.agent.registry import ToolRegistry
from kurotutor.config.schema import AppConfig
from kurotutor.core import get_logger, log_event
from kurotutor.storage import Session, Student, WorkingContext, session_scope

log = get_logger("router")


class Router:
    """把一条入站消息变成一组出站响应。"""

    def __init__(self, config: AppConfig, registry: ToolRegistry, engine: Any):
        self._config = config
        self._registry = registry
        self._engine = engine
        self._entry = MessageEntry(config, registry, engine)

    def _get_or_create_student(self, db: Any, external_id: str) -> Student:
        student = db.exec(select(Student).where(Student.external_id == external_id)).first()
        if student is None:
            # 不复用渠道编号当昵称（学生看到 "960FB..." 会困惑）；昵称留空，由教学端叫「同学」
            student = Student(external_id=external_id, nickname="")
            db.add(student)
            db.flush()
            log_event(log, "new student", external_id=external_id)
        return student

    async def handle(
        self, external_id: str, text: str, images: list[str] | None = None
    ) -> list[OutboundMessage]:
        """处理一条学生消息，返回响应列表（可能为空表示无需回）。"""
        with session_scope(self._engine) as db:
            student = self._get_or_create_student(db, external_id)
            student_id = student.id
            # 复用该学生最近一次的会话，保证跨轮历史连续（无则让入口新建）
            last_session = db.exec(
                select(Session)
                .where(Session.student_id == student_id)
                .order_by(Session.updated_at.desc())
                .limit(1)
            ).first()
            session_id = last_session.id if last_session is not None else None

        response = await self._entry.handle(
            student_id=student_id, text=text, images=images, session_id=session_id
        )

        if not response.ok:
            return [OutboundMessage(text=response.error or response.text, priority=Priority.P0)]

        return self._to_outbound(response.text, student_id=student_id)

    def _to_outbound(
        self, text: str, *, student_id: int | None = None, priority: Priority = Priority.P0
    ) -> list[OutboundMessage]:
        """把 Agent 最终文本转换为出站消息，超长走双模式分条。

        首次超长时向学生说明「分条发送」，并记录偏好（split）。
        「写讲义」模式在讲义生成落地后再开放选择，这里只承诺已具备的能力。
        """
        text = text.strip()
        if not text:
            return []
        messages: list[OutboundMessage] = []
        if should_split(text):
            first_time = self._set_long_pref_if_unset(student_id)
            chunks = split_text(text)
            if first_time:
                chunks = [
                    f"这段内容有点长，我先给你分成几条发（文末的讲义整理功能还在完善中，之后会自动切换）。\n\n{chunks[0]}"
                ] + chunks[1:]
            for chunk in chunks:
                messages.append(OutboundMessage(text=chunk, priority=priority))
        else:
            messages.append(OutboundMessage(text=text, priority=priority))
        return messages

    def _set_long_pref_if_unset(self, student_id: int | None) -> bool:
        """把长内容偏好落为 split；若原本未设置（首次）返回 True，便于加引导语。"""
        if student_id is None:
            return False
        with session_scope(self._engine) as db:
            wc = db.get(WorkingContext, student_id)
            if wc is None:
                db.add(WorkingContext(student_id=student_id, long_pref="split"))
                return True  # 首次：加引导语
            if wc.long_pref in ("", "unset"):
                wc.long_pref = "split"
                db.add(wc)
                return True  # 首次：加引导语
            # 已确立偏好（lecture 尚未建成前，仍按 split 处理）
            if wc.long_pref != "split":
                wc.long_pref = "split"
                db.add(wc)
            return False
