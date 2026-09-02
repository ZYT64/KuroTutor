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
import hashlib
from pathlib import Path
from typing import Any

from kurotutor.adapters.base import ChannelAdapter
from kurotutor.adapters.channel.qq_media import direct_send_c2c_file, upload_c2c_file
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
        # 调试日志：dump 消息与附件的全部属性（排查文件收不到的问题）
        attaches = getattr(message, "attachments", None) or []
        for a in attaches:
            if hasattr(a, "__dict__"):
                raw_attrs = {k: repr(v)[:120] for k, v in vars(a).items()}
            else:
                raw_attrs = {"_type": str(type(a))}
            log_event(log, "qq attachment dump", **raw_attrs)
        log_event(
            log, "qq message received",
            content=(message.content or "")[:60],
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
                files = self._download_attachments(message)
                # 自动解压 zip
                files = self._extract_zips(files)
                # 区分图片和文档：图片走 solve_photo（视觉），文档走 doc_read/ocr_read
                img_exts = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp")
                images = [f for f in files if Path(f).suffix.lower() in img_exts]
                docs = [f for f in files if f not in images]
                # 把文件的完整工作区路径告诉 Agent（不是文件名——Agent 猜不到路径）
                ws = str(Path(self._workspace).resolve())
                doc_info = []
                for d in docs:
                    rp = Path(d).resolve()
                    rel = str(rp.relative_to(ws)) if str(rp).startswith(ws) else d
                    doc_info.append(rel)
                if doc_info and not text:
                    text = (
                        "我发了一些文件，解压后的路径如下：\n"
                        + "\n".join(f"- {p}" for p in doc_info)
                        + "\n请帮我看看内容。"
                    )
                elif doc_info and text:
                    text += "\n[附带文件路径：\n" + "\n".join(f"- {p}" for p in doc_info) + "]"
                if images and not text:
                    text = "这道题我不会，帮我看看。"
                responses = await self._router.handle(openid, text, images)
                for out in responses:
                    await self._send(message, out)
            except Exception as exc:
                log_event(log, "qq handle error", level="error", error=repr(exc))
                with contextlib.suppress(Exception):
                    await self._text_reply(message, "老师这边出了点问题，请稍后再试。")

    def _extract_zips(self, files: list[str]) -> list[str]:
        """自动解压 zip 文件，返回解压后所有文件的路径列表（原 zip 替换为其内容）。"""
        import zipfile as _zf

        result: list[str] = []
        for f in files:
            if not f.lower().endswith(".zip"):
                result.append(f)
                continue
            try:
                dest = Path(f).parent / (Path(f).stem + "_extracted")
                dest.mkdir(parents=True, exist_ok=True)
                extracted = []
                with _zf.ZipFile(f) as zf:
                    for name in zf.namelist():
                        if ".." in name:
                            continue  # 拒绝路径穿越
                        target = dest / name
                        if name.endswith("/"):
                            target.mkdir(parents=True, exist_ok=True)
                            continue
                        target.parent.mkdir(parents=True, exist_ok=True)
                        with zf.open(name) as src, open(target, "wb") as dst:
                            dst.write(src.read())
                        extracted.append(str(target))
                result.extend(extracted)
                log_event(log, "zip extracted", file=Path(f).name, files=len(extracted))
            except Exception as exc:
                log_event(log, "zip extract failed", level="warning", file=Path(f).name, error=str(exc))
                result.append(f)  # 解压失败保留原文件
        return result

    def _download_attachments(self, message: Any) -> list[str]:
        """下载 C2C 消息里的图片/文件附件到工作区，返回本地路径列表。

        图片类附件存为 .png（与原有逻辑兼容）；
        文件类附件保留原始文件名后缀（让 Agent 知道是什么类型）。
        """
        import httpx as _httpx

        workspace = Path(self._workspace).resolve()
        workspace.mkdir(parents=True, exist_ok=True)
        paths: list[str] = []
        for attach in getattr(message, "attachments", []) or []:
            url = getattr(attach, "url", "") or ""
            ctype = getattr(attach, "content_type", "") or ""
            filename = getattr(attach, "filename", "") or ""
            log_event(log, "qq attachment", ctype=ctype, url=url[:80], filename=filename)
            if not url:
                continue
            try:
                timeout = _httpx.Timeout(connect=10.0, read=60.0, write=10.0, pool=10.0)
                resp = _httpx.get(url, timeout=timeout, follow_redirects=True)
                resp.raise_for_status()
                # 确定文件名：优先原始文件名，否则按内容类型生成
                if filename and "." in filename:
                    safe_name = "".join(c for c in filename if c not in '\\/:*?"<>|')[:80]
                elif "image" in ctype:
                    safe_name = f"{hashlib.md5(url.encode()).hexdigest()[:16]}.png"
                else:
                    safe_name = f"{hashlib.md5(url.encode()).hexdigest()[:16]}.dat"
                dest = workspace / "incoming" / safe_name
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(resp.content)
                paths.append(str(dest))
                log_event(log, "attachment saved", file=safe_name, size=len(resp.content))
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

    # 单文件发送上限：官方硬限制 200MB，但整文件读入内存（树莓派也要能跑），收到 100MB
    MEDIA_MAX_BYTES = 100 * 1024 * 1024

    async def _send(self, message: Any, out: OutboundMessage) -> None:
        """发送一条回复：文字 + 图片/文件富媒体（被动回复，不占主动消息频控）。"""
        msg_id = str(getattr(message, "id", "") or "")
        if out.text:
            await self._text_reply(message, out.text)
        for img_path in out.images:
            await self._send_one_media(message, msg_id, img_path, file_type=1,
                                       name="图片")
        files = list(getattr(out, "files", []) or [])
        if out.lecture_path and out.lecture_path not in files:
            files.append(out.lecture_path)
        for file_path in files:
            await self._send_one_media(message, msg_id, file_path, file_type=4,
                                       name=Path(file_path).name)

    async def _send_one_media(
        self, message: Any, msg_id: str, path: str, *, file_type: int, name: str
    ) -> None:
        """发送单个媒体文件：前置校验 → 分片上传 + msg_type=7 → 失败文字兜底。"""
        p = Path(path)
        if not p.exists():
            log_event(log, "media file missing", level="warning", file=path)
            return
        if p.stat().st_size > self.MEDIA_MAX_BYTES:
            await self._text_reply(
                message,
                f"📎 {name} 生成好了，但超过了 QQ 单文件 100MB 的上限发不出来，我看看怎么给你拆小一点。",
            )
            return
        openid = getattr(message.author, "user_openid", "") or ""
        sent = await self._reply_media(openid, str(p), file_type=file_type, msg_id=msg_id)
        if not sent:
            await self._text_reply(
                message,
                f"📎 {name} 已经生成好了，但 QQ 这边暂时发不出来，需要的话跟我说一声，我换个方式给你。",
            )

    async def _reply_media(
        self, openid: str, file_path: str, file_type: int = 1, msg_id: str = ""
    ) -> bool:
        """发送图片/文件（官方四步分片上传 → msg_type=7）。

        有 ``msg_id`` 走被动回复（60 分钟内最多 4 条，不占主动消息频控）；
        无 msg_id 则为主动消息（受频控：未认证 5/qps 且 30/qpm，单好友日限 1000 条）。
        被动发送失败（如 msg_id 过期）降级为主动直发重试一次。
        """
        if self._client is None or getattr(self._client, "http", None) is None:
            return False
        from botpy.http import Route

        try:
            file_info = await upload_c2c_file(self._client.http, openid, file_path, file_type)
        except Exception as exc:
            log_event(log, "reply media upload failed", level="warning",
                      file=Path(file_path).name, error=repr(exc))
            return False
        body: dict[str, Any] = {"msg_type": 7, "media": {"file_info": file_info}}
        if msg_id:
            body["msg_id"] = msg_id
            body["msg_seq"] = self._next_seq(msg_id)
        try:
            await self._client.http.request(
                Route("POST", "/v2/users/{openid}/messages", openid=openid), json=body,
            )
            log_event(log, "reply media sent", file=Path(file_path).name,
                      mode="passive" if msg_id else "active")
            return True
        except Exception as exc:
            log_event(log, "reply media send failed", level="warning",
                      file=Path(file_path).name, error=repr(exc), had_msg_id=bool(msg_id))
        # 降级：重新上传并 srv_send_msg=true 直发（主动消息，一步完成）
        try:
            if await direct_send_c2c_file(self._client.http, openid, file_path, file_type):
                log_event(log, "reply media sent (direct)", file=Path(file_path).name)
                return True
        except Exception as exc:
            log_event(log, "reply media direct send failed", level="warning",
                      file=Path(file_path).name, error=repr(exc))
        return False

    async def send(self, student_external_id: str, out: OutboundMessage) -> None:
        """主动推送（开课/复习/周报等）。

        官方频控（2026-07 文档）：Bot 维度未认证 5/qps 且 30/qpm；
        单关系维度 20/qpm，每个好友每天最多接收 1000 条——失败记日志降级，
        不重试轰炸。媒体走官方四步分片上传 → msg_type=7。
        """
        if self._client is None:
            log_event(log, "qq proactive send skipped: client not ready", student=student_external_id)
            return
        from botpy.http import Route

        try:
            for img_path in out.images:
                if Path(img_path).exists():
                    await self._reply_media(student_external_id, img_path, file_type=1)
            if out.lecture_path and Path(out.lecture_path).exists():
                sent = await self._reply_media(student_external_id, out.lecture_path, file_type=4)
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

    async def close(self) -> None:
        if self._client is not None:
            with contextlib.suppress(Exception):
                await asyncio.to_thread(self._client.close)
