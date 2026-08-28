"""渠道适配器抽象接口（架构红线第 3 条：渠道独立模块）。

渠道只负责「收发」，不负责教学逻辑。教学逻辑统一走
:class:`~kurotutor.agent.entry.MessageEntry`。一个渠道实现：
1. 连接并接收学生消息 → 转成 :class:`InboundMessage` 交给 dispatcher；
2. 把 dispatcher 产出的 :class:`OutboundMessage` 发送给学生。

当前仅实现 QQ 私聊；控制台渠道用于本地联调与测试。
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from kurotutor.adapters.message import OutboundMessage
from kurotutor.config.schema import AppConfig


class ChannelAdapter(ABC):
    """渠道抽象接口。所有渠道必须实现连接、发送、关闭。"""

    name: str = "base"

    def __init__(self, config: AppConfig):
        self._config = config

    @abstractmethod
    async def send(self, student_external_id: str, out: OutboundMessage) -> None:
        """发送一条响应给指定学生。长内容切分由调用方在发前完成。"""
        raise NotImplementedError

    @abstractmethod
    async def start(self) -> None:
        """建立连接并开始接收消息。``on_inbound`` 在构造时注入。"""
        raise NotImplementedError

    @abstractmethod
    async def close(self) -> None:
        """优雅关闭连接，释放资源。"""
        raise NotImplementedError
