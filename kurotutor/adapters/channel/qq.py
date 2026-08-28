"""QQ 私聊渠道（官方 botpy SDK）。

用 QQ 开放平台官方机器人 SDK ``botpy``（tencent-connect/botpy）接入：
``botpy.Client`` 派生类监听 C2C 私聊事件（``on_c2c_message_create``），
收到的 :class:`botpy.message.C2CMessage` 交给 :class:`Router` 处理，再用 ``message.reply``
被动回复（被动回复无限条数，5 分钟内有效）。

⚠️ ``botpy`` 不在 PyPI，需从 GitHub 安装：
``pip install git+https://github.com/tencent-connect/botpy.git``
未安装时本渠道创建会给出可读提示，不影响控制台渠道与测试（懒导入）。
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import Any

from kurotutor.adapters.base import ChannelAdapter
from kurotutor.adapters.message import OutboundMessage
from kurotutor.adapters.router import Router
from kurotutor.agent.registry import ToolRegistry
from kurotutor.config.schema import AppConfig
from kurotutor.core import ChannelError, get_logger, log_event

log = get_logger("qq")


def _botpy_available() -> bool:
    try:
        import botpy  # noqa: F401

        return True
    except ImportError:
        return False


class QQBotpyChannel(ChannelAdapter):
    """基于官方 botpy 的 QQ 私聊渠道。"""

    name = "qq"

    def __init__(self, config: AppConfig, registry: ToolRegistry, engine: Any):
        super().__init__(config)
        self._config = config
        self._registry = registry
        self._engine = engine
        self._router = Router(config, registry, engine)
        self._workspace = config.workspace
        self._client: Any = None

    def _make_client(self):
        """构造 botpy 客户端（懒导入，未装 SDK 时抛可读错误）。"""
        if not _botpy_available():
            raise ChannelError(
                "未安装官方 QQ SDK botpy",
                cause="缺少 botpy 模块",
                fix="运行 pip install git+https://github.com/tencent-connect/botpy.git 后重试",
            )
        import botpy

        channel = self

        class _KuroBot(botpy.Client):
            async def on_ready(self) -> None:
                log_event(log, "qq bot ready")

            async def on_c2c_message_create(self, message: Any) -> None:
                await channel._on_message(message)

        intents = botpy.Intents(public_messages=True)
        return _KuroBot(intents=intents)

    async def start(self) -> None:
        app_id = self._config.channel.app_id
        secret = self._config.channel.secret
        if not app_id or not secret:
            raise ChannelError(
                "QQ 渠道未配置 app_id / secret",
                cause="kuro.json 的 channel.app_id / channel.secret 为空",
                fix="在 kuro.json 中填入 QQ 开放平台机器人的 AppID 与 AppSecret",
            )
        log_event(log, "starting qq bot", app_id=app_id)

        # botpy 的 Client.run 是同步阻塞、且用构造时所在线程的事件循环；
        # 因此在 worker 线程内构建并运行 client，避免跨线程 loop 问题。
        def _run() -> None:
            # 3.12 下线程无 current loop，botpy 需要；先设一个再运行
            asyncio.set_event_loop(asyncio.new_event_loop())
            client = self._make_client()
            self._client = client
            client.run(appid=app_id, secret=secret)

        try:
            await asyncio.to_thread(_run)
        except Exception as exc:  # 连接失败（鉴权/网络/意图权限）等
            raise ChannelError(
                "QQ 机器人启动失败", cause=str(exc), fix="检查 network/AppID/AppSecret/意图权限"
            ) from exc

    async def _on_message(self, message: Any) -> None:
        """处理一条 C2C 私聊消息：先触发原生「对方正在输入」，再交给 Agent 处理并回。"""
        try:
            await self._typing_notify(message)
            user_openid = getattr(message.author, "user_openid", "") or ""
            text = message.content or ""
            images = self._download_images(message)
            if images and not text:
                text = "这道题我不会，帮我看看。"
            responses = await self._router.handle(str(user_openid), text, images)
            for out in responses:
                await self._send(message, out)
        except Exception as exc:
            log_event(log, "qq handle error", level="error", error=repr(exc))
            with contextlib.suppress(Exception):
                await self._text_reply(message, "老师这边出了点问题，请稍后再试。")

    def _download_images(self, message: Any) -> list[str]:
        """把 C2C 消息里的图片附件下载到工作区，返回本地路径列表。"""
        from kurotutor.tools.files import save_remote_image

        paths: list[str] = []
        for attach in getattr(message, "attachments", []) or []:
            url = getattr(attach, "url", "") or ""
            ctype = getattr(attach, "content_type", "") or ""
            if url and ("image" in ctype or url):
                try:
                    paths.append(save_remote_image(url, self._workspace))
                except Exception as exc:
                    log_event(log, "image download failed", level="warning", url=url, error=str(exc))
        return paths

    async def _text_reply(self, message: Any, content: str) -> None:
        """发送一条 C2C 回复。优先 markdown（msg_type=2 + markdown.content），失败兜底纯文本。"""
        if self._client is None or getattr(self._client, "http", None) is None:
            return
        openid = getattr(message.author, "user_openid", "") or ""
        msg_id = getattr(message, "id", "")
        if not openid:
            return
        from botpy.http import Route

        # 优先 markdown：msg_type=2，markdown.content 放正文（顶层 content 留空）
        try:
            await self._client.http.request(
                Route("POST", "/v2/users/{openid}/messages", openid=openid),
                json={"msg_type": 2, "msg_id": msg_id, "markdown": {"content": content}},
            )
            return
        except Exception:
            pass  # markdown 不可用（未开通权限等）→ 兜底纯文本
        try:
            await self._client.http.request(
                Route("POST", "/v2/users/{openid}/messages", openid=openid),
                json={"msg_type": 0, "msg_id": msg_id, "content": content},
            )
        except Exception as exc:
            log_event(log, "qq reply failed", level="warning", error=str(exc))

    async def _typing_notify(self, message: Any, seconds: int = 30) -> None:
        """触发 QQ 原生「对方正在输入」状态（msg_type=6 + input_notify，最长 60 秒持续）。"""
        if self._client is None or getattr(self._client, "http", None) is None:
            return
        openid = getattr(message.author, "user_openid", "") or ""
        msg_id = getattr(message, "id", "")
        if not openid:
            return
        from botpy.http import Route

        try:
            await self._client.http.request(
                Route("POST", "/v2/users/{openid}/messages", openid=openid),
                json={
                    "msg_type": 6,
                    "msg_id": msg_id,
                    "input_notify": {"input_type": 1, "input_second": seconds},
                },
            )
        except Exception as exc:
            log_event(log, "typing notify failed", level="warning", error=str(exc))

    async def _send(self, message: Any, out: OutboundMessage) -> None:
        """发送一条响应。图片附件需上传媒体（botpy API），此处先发文本；图片回传待媒体接口接入。"""
        if out.text:
            await self._text_reply(message, out.text)
        if out.images:
            log_event(log, "image reply not yet supported via botpy media upload", count=len(out.images))

    async def send(self, student_external_id: str, out: OutboundMessage) -> None:
        # 主动推送：QQ 私信主动消息每日每人限量 2 条，且需 botpy create_dms 会话；此处仅记录，不擅自外发
        log_event(log, "qq proactive send not used", student=student_external_id)

    async def close(self) -> None:
        if self._client is not None:
            with contextlib.suppress(Exception):
                await asyncio.to_thread(self._client.close)
