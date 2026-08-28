"""渠道适配器包：抽象接口 + 统一消息 + 路由 + 具体渠道。"""

from kurotutor.adapters.base import ChannelAdapter
from kurotutor.adapters.message import (
    InboundMessage,
    OutboundMessage,
    should_split,
    split_text,
)
from kurotutor.adapters.router import Router

__all__ = [
    "ChannelAdapter",
    "InboundMessage",
    "OutboundMessage",
    "Router",
    "split_text",
    "should_split",
]
