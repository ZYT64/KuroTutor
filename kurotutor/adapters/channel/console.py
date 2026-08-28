"""控制台渠道（本地联调 / 无 QQ 环境测试用）。

在终端逐行输入学生消息，把每一行交给 :class:`Router` 处理并打印回复。
作用是让「无 QQ 后端」的环境也能完整跑通 Agent 链路，验证真实场景。
"""

from __future__ import annotations

import asyncio
import sys
from typing import Any

from kurotutor.adapters.base import ChannelAdapter
from kurotutor.adapters.message import OutboundMessage
from kurotutor.adapters.router import Router
from kurotutor.agent.registry import ToolRegistry
from kurotutor.config.schema import AppConfig
from kurotutor.core import get_logger, log_event

log = get_logger("console")


class ConsoleChannel(ChannelAdapter):
    """终端模拟学生发消息给 KuroTutor。"""

    name = "console"

    def __init__(
        self,
        config: AppConfig,
        registry: ToolRegistry,
        engine: Any,
        *,
        student_external_id: str = "console-student",
    ):
        super().__init__(config)
        self._registry = registry
        self._engine = engine
        self._router = Router(config, registry, engine)
        self._external_id = student_external_id

    async def send(self, student_external_id: str, out: OutboundMessage) -> None:
        text = out.text or ""
        if out.lecture_path:
            text += f"\n[讲义已生成：{out.lecture_path}]"
        if out.voice_path:
            text += f"\n[语音已合成：{out.voice_path}]"
        print(f"\n[KuroTutor] {text}", flush=True)

    async def start(self) -> None:
        print(
            "KuroTutor 控制台渠道已启动。输入消息回车发送；/photo <图片路径> [备注] 模拟发图；/quit 退出。",
            flush=True,
        )
        loop = asyncio.get_running_loop()
        while True:
            line = await loop.run_in_executor(None, sys.stdin.readline)
            if not line:
                break
            text = line.rstrip("\n")
            if not text.strip():
                continue
            if text.strip() == "/quit":
                break
            try:
                images: list[str] = []
                send_text = text
                if text.startswith("/photo"):
                    # 兼容 /photo <路径> [备注]：用空格切分，首个 token 为图片路径
                    parts = text.split(maxsplit=2)
                    if len(parts) < 2:
                        print("[用法] /photo <图片路径> [学生备注]", flush=True)
                        continue
                    images = [parts[1]]
                    send_text = parts[2] if len(parts) > 2 else "这道题我不会，帮我看看。"
                    print("[老师正在看你的图…]", flush=True)
                responses = await self._router.handle(self._external_id, send_text, images)
                for out in responses:
                    await self.send(self._external_id, out)
            except Exception as exc:  # 渠道兜底：单条消息失败不影响整体运行
                log_event(log, "console handle error", level="error", error=repr(exc))
                print(f"[渠道异常] {exc}", flush=True)

    async def close(self) -> None:
        print("控制台渠道已关闭。", flush=True)
