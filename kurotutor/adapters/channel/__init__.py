"""具体渠道实现。"""

from kurotutor.adapters.channel.console import ConsoleChannel
from kurotutor.adapters.channel.qq import QQBotpyChannel

__all__ = ["ConsoleChannel", "QQBotpyChannel"]
