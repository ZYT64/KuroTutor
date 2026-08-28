"""统一消息格式（渠道无关）。

所有渠道（QQ / 控制台 / 未来其他）收发统一用本模块的数据结构，
保证 :class:`~kurotutor.agent.registry.ToolContext` 之后的逻辑不被渠道绑定。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from kurotutor.agent.queue import Priority


@dataclass
class InboundMessage:
    """渠道收到的学生消息（已解析为统一格式）。"""

    student_external_id: str
    text: str = ""
    images: list[str] = field(default_factory=list)  # 已落盘工作区的图片路径
    voice_path: str = ""  # 语音文件路径（若为语音消息）
    message_id: str = ""
    session_id: int | None = None
    priority: Priority = Priority.P0
    timestamp: float = 0.0


@dataclass
class OutboundMessage:
    """发往渠道的响应。支持文本/图片/音频/讲义多通道。"""

    text: str = ""
    images: list[str] = field(default_factory=list)
    voice_path: str = ""
    lecture_path: str = ""  # 讲义文档落盘路径（长内容模式①）
    split: bool = True  # 是否按长内容规则分条
    priority: Priority = Priority.P0


# 长内容分条阈值（产品规格书 4.4）：超过该字数走双模式
LONG_TEXT_THRESHOLD = 2000
# 分条单条上限
SPLIT_LIMIT = 500


def should_split(text: str) -> bool:
    """判断是否需要走长内容双模式。"""
    return len(text) > LONG_TEXT_THRESHOLD


def split_text(text: str, limit: int = SPLIT_LIMIT) -> list[str]:
    """把超长文本按语义分段拆为不超过 ``limit`` 字的若干条。

    优先在句子结尾（。！？）断开，其次在标点/空格，尽量不截断词句。
    """
    if len(text) <= limit:
        return [text]
    chunks: list[str] = []
    remainder = text
    sentence_ends = "。！？!?；;\n．"
    while remainder:
        if len(remainder) <= limit:
            chunks.append(remainder)
            break
        # 在 limit 内找最后一个句子结尾
        window = remainder[:limit]
        cut = -1
        for i in range(len(window) - 1, max(len(window) - 80, -1), -1):
            if window[i] in sentence_ends:
                cut = i + 1
                break
        if cut <= 0:  # 找不到则退化为直观长度或最后一个空格
            cut = window.rfind(" ")
            if cut <= 0:
                cut = limit
        chunks.append(window[:cut].strip())
        remainder = remainder[cut:].strip()
    return chunks
