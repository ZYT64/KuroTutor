"""QQ 渠道跨线程推送测试。

根因回归：调度器在主 loop 调 channel.send()，而 botpy 的 aiohttp session
绑定在 worker 线程自己的 loop 上，直接调用报「Timeout context manager should
be used inside a task」。本文件证明 _call_bot 会把主 loop 的调用正确转发到
bot loop 执行，同 loop（被动回复路径）则原地执行。
"""

from __future__ import annotations

import asyncio
import threading
import time

from kurotutor.adapters.channel.qq import QQBotpyChannel


def _make_channel() -> QQBotpyChannel:
    # 绕过 __init__ 的完整依赖（config/engine/registry），只测线程转发逻辑
    ch = QQBotpyChannel.__new__(QQBotpyChannel)
    ch._bot_loop = None
    return ch


def _start_bot_loop() -> tuple[asyncio.AbstractEventLoop, threading.Event, threading.Thread]:
    loop = asyncio.new_event_loop()
    ready = threading.Event()

    def _run():
        asyncio.set_event_loop(loop)
        ready.set()
        loop.run_forever()

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    ready.wait(timeout=5)
    return loop, ready, t


def _stop_bot_loop(loop: asyncio.AbstractEventLoop) -> None:
    loop.call_soon_threadsafe(loop.stop)


def test_call_bot_forwards_to_bot_loop():
    ch = _make_channel()
    bot_loop, _ready, _t = _start_bot_loop()
    ch._bot_loop = bot_loop
    try:
        ran_on: dict[str, object] = {}

        async def _job():
            ran_on["loop"] = asyncio.get_running_loop()
            return "ok"

        async def _main():
            return await ch._call_bot(_job)

        result = asyncio.run(_main())
        assert result == "ok"
        assert ran_on["loop"] is bot_loop  # 确实在 botpy 的 loop 里执行
    finally:
        _stop_bot_loop(bot_loop)
        time.sleep(0.1)


def test_call_bot_runs_inplace_without_bot_loop():
    ch = _make_channel()  # _bot_loop=None：被动回复路径（同 loop）应原地执行
    ran_on: dict[str, object] = {}

    async def _job():
        ran_on["loop"] = asyncio.get_running_loop()
        return 42

    async def _main():
        return await ch._call_bot(_job)

    async def _runner():
        main_loop = asyncio.get_running_loop()
        result = await ch._call_bot(_job)
        return main_loop, result

    main_loop, result = asyncio.run(_runner())
    assert result == 42
    assert ran_on["loop"] is main_loop  # 没有 bot loop 时在当前 loop 原地执行


def test_safe_http_proxies_request_to_bot_loop():
    ch = _make_channel()
    bot_loop, _ready, _t = _start_bot_loop()
    ch._bot_loop = bot_loop

    class _FakeHttp:
        def __init__(self):
            self.calls: list[str] = []

        async def request(self, route, **kwargs):
            self.calls.append((route, kwargs.get("json")))
            return {"file_info": "FI"}

    fake = _FakeHttp()

    class _FakeClient:
        http = fake

    ch._client = _FakeClient()
    try:
        async def _main():
            return await ch.safe_http.request("ROUTE-X", json={"a": 1})

        resp = asyncio.run(_main())
        assert resp == {"file_info": "FI"}
        assert fake.calls == [("ROUTE-X", {"a": 1})]  # 请求经 safe_http 到达 botpy http
    finally:
        _stop_bot_loop(bot_loop)
        time.sleep(0.1)
