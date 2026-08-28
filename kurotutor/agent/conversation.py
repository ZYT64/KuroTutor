"""对话编排（无感分段 + 分层压缩 + 语境感知打断）。

规格 4.5：学生无感，自动开新段；超长历史分层压缩。
- :func:`decide_segment`：判断本轮是「并入当前段」还是「另开新段」（无感分段 + 语境打断）。
- :func:`compress_history`：最近若干轮原文，更早轮压缩成一条摘要（分层压缩）。
- :func:`is_redirect` / :func:`is_topic_switch`：语境感知打断的子判定。

全部为纯函数，可离线测试。打断缓存（队列原语）在 :class:`~kurotutor.agent.queue.PriorityQueue` 已具备，
此处负责「何时/怎样」的编排决策。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from kurotutor.services.llm import ChatMessage

# 距上一条消息超过该秒数，视为新话题（无感分段·时间维度)
DEFAULT_GAP_SECONDS = 900  # 15 分钟
# 分层压缩：保留最近多少轮原文
RECENT_KEEP = 6


class Action:
    APPEND = "append"  # 并入当前段
    SPLIT = "split"  # 另开新段（学生无感）


@dataclass
class SegmentDecision:
    action: str
    reason: str


def is_redirect(text: str) -> bool:
    """语境感知打断·修正：学生要纠正老师/自己之前的说法（重定向但保留上下文）。"""
    return any(
        k in (text or "") for k in ("不对", "纠正", "其实应该", "我说错了", "不是这样", "更正", "你刚才说错")
    )


def is_topic_switch(text: str) -> bool:
    """语境感知打断·换题：student 明确要切换到另一个话题（开新轮）。"""
    return any(
        k in (text or "")
        for k in ("换个话题", "聊点别的", "不谈这个", "我们说点别的", "换个问题", "另外想问你")
    )


def decide_segment(
    prev_text: str,
    new_text: str,
    *,
    prev_time: datetime | None,
    now: datetime | None = None,
    gap_after_seconds: int = DEFAULT_GAP_SECONDS,
) -> SegmentDecision:
    """决定并入还是开新段。

    确定性规则：明确换题→新段；修正→并入重讲；时间间隔过大→新段；
    否则（短时间连续）默认并入。语义相关性判断（需嵌入/LLM）留作后续增强，
    这里优先保证可靠与学生无感。
    """
    now = (now or datetime.now(UTC)).replace(tzinfo=None)
    prev_time = prev_time.replace(tzinfo=None) if prev_time is not None else None
    if is_topic_switch(new_text):
        return SegmentDecision(Action.SPLIT, "student 明确换题")
    if is_redirect(new_text) or is_redirect(prev_text):
        return SegmentDecision(Action.APPEND, "修正，并入重讲")
    if prev_time is not None and (now - prev_time).total_seconds() > gap_after_seconds:
        return SegmentDecision(Action.SPLIT, f"时间间隔超 {gap_after_seconds // 60} 分钟")
    return SegmentDecision(Action.APPEND, "短时间连续，并入")


def compress_history(messages: list[ChatMessage], keep: int = RECENT_KEEP) -> list[ChatMessage]:
    """分层压缩：保留最近 ``keep`` 条原文，更早的合并成一条摘要消息。"""
    if len(messages) <= keep:
        return messages
    recent = messages[-keep:]
    older = messages[:-keep]
    older_text = " ".join(f"{m.role}:{m.content}" for m in older if m.content)[:500]
    summary = ChatMessage(
        role="assistant",
        content=f"[更早的对话已省略] {older_text}...(共 {len(older)} 条)",
    )
    return [summary] + recent
