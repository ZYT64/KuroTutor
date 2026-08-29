"""kuro serve —— 启动服务（连接渠道，开始接收学生消息）。"""

from __future__ import annotations

import asyncio
from pathlib import Path

import typer

from kurotutor.adapters.message import OutboundMessage
from kurotutor.cli.common import load_runtime
from kurotutor.core import get_logger, log_event
from kurotutor.services import scheduler
from kurotutor.services.push import make_handlers

from . import ui

log = get_logger("serve")

# 后台调度轮询间隔（秒）
_POLL_SECONDS = 60


def serve_command(
    channel: str = typer.Option("console", "--channel", "-ch", help="渠道：console / qq"),
    student: str = typer.Option("console-student", "--student", help="控制台渠道模拟的学生标识"),
    config: Path | None = typer.Option(None, "--config", "-c"),
) -> None:
    """启动 KuroTutor 服务。默认用控制台渠道本地联调（真实学生从 QQ 接入）。"""
    rt = load_runtime(config, require_models=True)
    ui.heading("启动 KuroTutor 服务")
    ui.info(f"渠道：{channel} · 工作区：{rt.config.workspace}")

    if channel == "console":
        from kurotutor.adapters.channel import ConsoleChannel

        adapter = ConsoleChannel(rt.config, rt.registry, rt.engine, student_external_id=student)
    elif channel == "qq":
        from kurotutor.adapters.channel import QQBotpyChannel

        adapter = QQBotpyChannel(rt.config, rt.registry, rt.engine)
    else:
        ui.err(f"未知渠道：{channel}（支持 console / qq）")
        raise typer.Exit(2)

    async def _run() -> None:
        loop = asyncio.get_running_loop()
        engine = rt.engine

        # 推送回调：把到期任务（复习/提醒）经渠道发给学生。deliver 在调度线程被调，
        # 用 call_soon_threadsafe 把发送交回主事件循环。
        def deliver(external_id: str, text: str, file_path: str = "") -> None:
            async def _send() -> None:
                try:
                    out = OutboundMessage(text=text, lecture_path=file_path)
                    await adapter.send(external_id, out)
                except Exception as exc:
                    log_event(log, "push send failed", level="warning", error=repr(exc))

            loop.call_soon_threadsafe(lambda: asyncio.ensure_future(_send()))

        handlers = make_handlers(engine, deliver)

        async def _scheduler_loop() -> None:
            while True:
                try:
                    await asyncio.to_thread(scheduler.process_due, engine, handlers)
                except Exception as exc:
                    log_event(log, "scheduler error", level="error", error=repr(exc))
                await asyncio.sleep(_POLL_SECONDS)

        sched_task = asyncio.create_task(_scheduler_loop())
        try:
            await adapter.start()
        except Exception as exc:
            log_event(log, "serve failed", level="error", error=repr(exc))
            ui.err(f"服务启动失败：{exc}")
            sched_task.cancel()
            await adapter.close()
            raise typer.Exit(1) from exc
        finally:
            sched_task.cancel()
            await adapter.close()

    try:
        asyncio.run(_run())
    except KeyboardInterrupt:
        ui.info("已收到中断，服务停止。")
