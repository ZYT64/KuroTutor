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
import base64
import contextlib
from pathlib import Path
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
        # 被动回复去重：同一 msg_id 多次回复需递增 msg_seq（官方消息去重规则）
        self._msg_seq: dict[str, int] = {}
        # 并发控制：跨学生并行处理、同学生保持顺序（锁）；信号量限制同时在算的消息数
        self._locks: dict[str, asyncio.Lock] = {}
        self._sem = asyncio.Semaphore(4)
        self._tasks: set[asyncio.Task] = set()

    def _next_seq(self, msg_id: str) -> int:
        """同一 msg_id 的被动回复递增 msg_seq；主动消息固定 1。"""
        if not msg_id:
            return 1
        n = self._msg_seq.get(msg_id, 0) + 1
        self._msg_seq[msg_id] = n
        # 防止长对话内存膨胀：只保留最近 200 条消息的计数
        if len(self._msg_seq) > 200:
            for k in list(self._msg_seq)[: 100]:
                self._msg_seq.pop(k, None)
        return n

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
        """收到一条 C2C 私聊消息：派发后台任务处理（跨学生并行、同学生串行）。"""
        openid = str(getattr(message.author, "user_openid", "") or "")
        # 调试日志：记录消息类型与附件信息（排查文件收不到的问题）
        attaches = getattr(message, "attachments", None) or []
        att_info = [
            {"type": getattr(a, "content_type", ""), "url": str(getattr(a, "url", ""))[:80]}
            for a in attaches
        ]
        log_event(
            log, "qq message received",
            content=(message.content or "")[:60], attachments=att_info,
            event_type=type(message).__name__,
        )
        lock = self._locks.setdefault(openid, asyncio.Lock())
        task = asyncio.create_task(self._handle_locked(message, openid, lock))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _handle_locked(self, message: Any, openid: str, lock: asyncio.Lock) -> None:
        """处理一条消息：先触发原生「对方正在输入」，再交给 Agent 处理并回。"""
        async with self._sem, lock:
            try:
                await self._typing_notify(message)
                text = message.content or ""
                images = self._download_attachments(message)
                if images and not text:
                    text = "这道题我不会，帮我看看。"
                responses = await self._router.handle(openid, text, images)
                for out in responses:
                    await self._send(message, out)
            except Exception as exc:
                log_event(log, "qq handle error", level="error", error=repr(exc))
                with contextlib.suppress(Exception):
                    await self._text_reply(message, "老师这边出了点问题，请稍后再试。")

    def _download_attachments(self, message: Any) -> list[str]:
        """下载 C2C 消息里的图片/文件附件到工作区，返回本地路径列表。"""
        from kurotutor.tools.files import save_remote_image

        paths: list[str] = []
        for attach in getattr(message, "attachments", []) or []:
            url = getattr(attach, "url", "") or ""
            ctype = getattr(attach, "content_type", "") or ""
            log_event(log, "qq attachment", ctype=ctype, url=url[:80])
            if not url:
                continue
            try:
                paths.append(save_remote_image(url, self._workspace))
            except Exception as exc:
                log_event(log, "attachment download failed", level="warning", url=url[:80], error=str(exc))
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
                json={"msg_type": 2, "msg_id": msg_id, "msg_seq": self._next_seq(msg_id),
                      "markdown": {"content": content}},
            )
            return
        except Exception:
            pass  # markdown 不可用（未开通权限等）→ 兜底纯文本
        try:
            await self._client.http.request(
                Route("POST", "/v2/users/{openid}/messages", openid=openid),
                json={"msg_type": 0, "msg_id": msg_id, "msg_seq": self._next_seq(msg_id),
                      "content": content},
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
        """主动推送（开课/复习/周报等）。

        官方规则：单聊主动消息每日上限 1000 条/用户（20/qpm）；用户在客户端
        关闭「允许主动发送」后一律失败——失败记日志降级，不重试轰炸。
        文件按官方富媒体流程：整文件上传拿 file_info → msg_type=7 发送。
        """
        if self._client is None:
            log_event(log, "qq proactive send skipped: client not ready", student=student_external_id)
            return
        from botpy.http import Route

        try:
            if out.lecture_path and Path(out.lecture_path).exists():
                sent = await self._send_file(student_external_id, out.lecture_path)
                if not sent:
                    out.text = (out.text + f"\n📎 讲义文件「{Path(out.lecture_path).name}」生成好了，"
                                "但这条通道暂时发不了文件，需要的话说一声我用别的方式给你。").strip()
            if out.text:
                await self._client.http.request(
                    Route("POST", "/v2/users/{openid}/messages", openid=student_external_id),
                    json={"content": out.text[:2000], "msg_type": 0, "msg_id": "",
                          "msg_seq": self._next_seq("")},
                )
                log_event(log, "qq proactive push sent", student=student_external_id)
        except Exception as exc:
            log_event(log, "qq proactive push failed", level="warning",
                      student=student_external_id, error=repr(exc))

    async def _send_file(self, openid: str, path: str) -> bool:
        """按官方富媒体流程发文件：POST /files 上传（srv_send_msg=false）拿
        file_info → msg_type=7 + media.file_info 发送。整文件上传失败时兜底
        尝试 srv_send_msg=true 直发。file_info 有 ttl，拿到立即发送。"""
        from botpy.http import Route

        file_b64 = base64.b64encode(Path(path).read_bytes()).decode()
        try:
            r = await self._client.http.request(
                Route("POST", "/v2/users/{openid}/files", openid=openid),
                json={"file_type": 4, "file_data": file_b64, "srv_send_msg": False},
            )
            file_info = ""
            if isinstance(r, dict):
                file_info = str(r.get("file_info") or (r.get("media") or {}).get("file_info") or "")
            if file_info:
                await self._client.http.request(
                    Route("POST", "/v2/users/{openid}/messages", openid=openid),
                    json={"msg_type": 7, "msg_id": "", "msg_seq": self._next_seq(""),
                          "media": {"file_info": file_info}},
                )
                log_event(log, "qq file sent (media)", student=openid, file=Path(path).name)
                return True
            log_event(log, "qq file upload returned no file_info", level="warning", file=Path(path).name)
        except Exception as exc:
            log_event(log, "qq file upload failed, trying direct send", level="warning",
                      file=Path(path).name, error=repr(exc))
        # 兜底：srv_send_msg=true 直发（部分版本支持）
        try:
            await self._client.http.request(
                Route("POST", "/v2/users/{openid}/files", openid=openid),
                json={"file_type": 4, "file_data": file_b64, "srv_send_msg": True},
            )
            log_event(log, "qq file sent (direct)", student=openid, file=Path(path).name)
            return True
        except Exception as exc:
            log_event(log, "qq file direct send failed", level="warning",
                      file=Path(path).name, error=repr(exc))
            return False

    async def close(self) -> None:
        if self._client is not None:
            with contextlib.suppress(Exception):
                await asyncio.to_thread(self._client.close)
