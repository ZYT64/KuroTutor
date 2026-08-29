"""数据保留与遗忘：会话消息、已完成调度任务按期清理（规格 L1「过期归档/遗忘」落地）。

策略（保守默认，可配置）：
- Message：默认保留 180 天，超期删除（对话内容已被长期记忆/画像/方法卡吸收）；
- ScheduleTask：已完成的任务默认保留 90 天，超期删除；
- 清理动作幂等、每日最多执行一次（由 serve 循环驱动）。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlmodel import delete

from kurotutor.core import get_logger, log_event
from kurotutor.storage import Message, ScheduleTask, TaskStatus, session_scope

log = get_logger("retention")

# 默认保留期（天）
DEFAULT_MESSAGE_DAYS = 180
DEFAULT_TASK_DAYS = 90


def run_retention(
    engine: Any, *, message_days: int = DEFAULT_MESSAGE_DAYS, task_days: int = DEFAULT_TASK_DAYS
) -> dict[str, int]:
    """执行一轮清理。返回 {messages_deleted, tasks_deleted}。"""
    now = datetime.now(UTC)
    with session_scope(engine) as db:
        msg_cut = now - timedelta(days=max(int(message_days), 30))  # 下限 30 天，防误配
        r1 = db.exec(delete(Message).where(Message.created_at < msg_cut))
        task_cut = now - timedelta(days=max(int(task_days), 14))
        r2 = db.exec(
            delete(ScheduleTask)
            .where(ScheduleTask.status == TaskStatus.DONE)
            .where(ScheduleTask.fire_at < task_cut)
        )
        deleted = {"messages_deleted": r1.rowcount or 0, "tasks_deleted": r2.rowcount or 0}
    if deleted["messages_deleted"] or deleted["tasks_deleted"]:
        log_event(log, "retention cleanup done", **deleted)
    return deleted
