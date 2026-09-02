"""媒体发送链路测试。

证明三层链路真实可用：
1. 工具层：plot_function 生成图片后自动登记（ctx.emit_media）；
2. 路由层：AgentResponse.media 正确挂载到 OutboundMessage.images/files；
3. 渠道层：QQ 分片上传严格遵循官方 2026-07 协议
   （upload_prepare → 分片 PUT → upload_part_finish → files 合并拿 file_info）。
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from pathlib import Path

import pytest

from kurotutor.adapters.channel.qq_media import (
    QQMediaError,
    direct_send_c2c_file,
    upload_c2c_file,
)
from kurotutor.adapters.message import OutboundMessage
from kurotutor.adapters.router import Router
from kurotutor.agent.context import ToolContext
from kurotutor.storage import Student, session_scope


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# ---------- 1. ToolContext.emit_media ----------


def _ctx(config, engine) -> ToolContext:
    from kurotutor.agent.sandbox import Sandbox

    with session_scope(engine) as db:
        st = Student(external_id="media-student", nickname="小媒", stage="junior")
        db.add(st)
        db.flush()
        sid = st.id
    # 重新取一个绑定会话外的学生对象（避免 detached）
    with session_scope(engine) as db:
        st = db.get(Student, sid)
        return ToolContext(
            config=config,
            engine=engine,
            sandbox=Sandbox(config),
            logger=logging.getLogger("test.media"),
            student=st,
        )


def test_emit_media_infers_kind_and_dedups(config, engine, tmp_path):
    ctx = _ctx(config, engine)
    img = tmp_path / "plot.png"
    img.write_bytes(b"png")
    doc = tmp_path / "讲义.docx"
    doc.write_bytes(b"docx")

    assert ctx.emit_media(img) is True
    assert ctx.emit_media(doc) is True
    assert ctx.emit_media(img) is True  # 重复登记不产生第二条
    assert len(ctx.produced_media) == 2
    kinds = {m["path"]: m["kind"] for m in ctx.produced_media}
    assert kinds[str(img)] == "image"
    assert kinds[str(doc)] == "file"


def test_emit_media_missing_file_is_safe(config, engine):
    ctx = _ctx(config, engine)
    assert ctx.emit_media("/nonexistent/file.png") is False
    assert ctx.produced_media == []


# ---------- 2. Router 挂载媒体 ----------


def test_router_attach_media_splits_channels(config, engine, tmp_path):
    img = tmp_path / "q1.png"
    img.write_bytes(b"png")
    doc = tmp_path / "讲义.docx"
    doc.write_bytes(b"docx")
    out = OutboundMessage(text="讲解")
    Router._attach_media(
        out,
        [
            {"path": str(img), "kind": "image", "name": "q1.png"},
            {"path": str(doc), "kind": "file", "name": "讲义.docx"},
            {"path": "/nonexistent/x.png", "kind": "image", "name": "x.png"},
        ],
    )
    assert out.images == [str(img)]
    assert out.files == [str(doc)]


def test_router_attach_media_dedups(config, engine, tmp_path):
    img = tmp_path / "q1.png"
    img.write_bytes(b"png")
    out = OutboundMessage()
    item = {"path": str(img), "kind": "image", "name": "q1.png"}
    Router._attach_media(out, [item, item])
    assert out.images == [str(img)]


# ---------- 3. plot_function 自动登记 ----------


def test_plot_function_emits_image(config, engine, registry):
    from kurotutor.tools.quiz import plot_function

    ctx = _ctx(config, engine)
    result = _run(plot_function(ctx, {"expressions": "x^2-2*x-3"}))
    assert "已生成" in result
    assert len(ctx.produced_media) == 1
    media = ctx.produced_media[0]
    assert media["kind"] == "image"
    assert Path(media["path"]).exists()


# ---------- 4. QQ 分片上传协议 ----------


class _FakeRoute:
    def __init__(self, path: str):
        self.path = path


class _FakeHttp:
    """记录 request 调用并按路径关键字返回预置响应。"""

    def __init__(self, responses: dict[str, dict]):
        self.calls: list[tuple[str, dict | None]] = []
        self._responses = responses

    async def request(self, route, **kwargs):
        self.calls.append((route.path, kwargs.get("json")))
        for key, resp in self._responses.items():
            if key in route.path:
                return resp
        return {}


class _FakeResponse:
    status_code = 200

    def raise_for_status(self):
        return None


class _FakeAsyncClient:
    """替身 httpx.AsyncClient：记录 PUT 调用。"""

    puts: list[tuple[str, bytes]] = []

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def put(self, url, content=b""):
        _FakeAsyncClient.puts.append((url, bytes(content)))
        return _FakeResponse()


_PREPARE = {
    "upload_id": "upload_x1",
    "block_size": "10",
    "parts": [
        {"index": 0, "presigned_url": "https://cos.example/p0", "block_size": "10"},
        {"index": 1, "presigned_url": "https://cos.example/p1", "block_size": "5"},
    ],
}


def test_upload_c2c_file_follows_official_flow(monkeypatch, tmp_path):
    import kurotutor.adapters.channel.qq_media as qm

    f = tmp_path / "讲义.docx"
    data = b"A" * 10 + b"B" * 5
    f.write_bytes(data)

    http = _FakeHttp({"upload_prepare": _PREPARE, "upload_part_finish": {}, "/files": {"file_info": "FI123"}})
    monkeypatch.setattr(qm.httpx, "AsyncClient", _FakeAsyncClient)
    _FakeAsyncClient.puts = []

    file_info = _run(upload_c2c_file(http, "OPENID_X", str(f), file_type=4))

    assert file_info == "FI123"
    # ① prepare：文件大小/名称 + 三个校验值
    paths = [c[0] for c in http.calls]
    assert paths[0].endswith("/upload_prepare")
    prep_body = http.calls[0][1]
    assert prep_body["file_type"] == 4
    assert prep_body["file_size"] == "15"
    assert prep_body["file_name"] == "讲义.docx"
    assert prep_body["md5"] == hashlib.md5(data).hexdigest()
    assert prep_body["sha1"] == hashlib.sha1(data).hexdigest()
    assert prep_body["md5_10m"] == hashlib.md5(data).hexdigest()  # 文件 <10MB，与整文件一致
    # ②③ 分片 PUT + finish：两片、内容正确、finish 带 part_index/block_size/md5
    assert [p for p, _ in _FakeAsyncClient.puts] == ["https://cos.example/p0", "https://cos.example/p1"]
    assert _FakeAsyncClient.puts[0][1] == b"A" * 10
    assert _FakeAsyncClient.puts[1][1] == b"B" * 5
    finish_calls = [c for c in http.calls if "upload_part_finish" in c[0]]
    assert [c[1]["part_index"] for c in finish_calls] == [0, 1]
    assert [c[1]["block_size"] for c in finish_calls] == ["10", "5"]
    assert finish_calls[0][1]["md5"] == hashlib.md5(b"A" * 10).hexdigest()
    assert finish_calls[1][1]["md5"] == hashlib.md5(b"B" * 5).hexdigest()
    # ④ 合并：upload_id + srv_send_msg=false
    merge_path, merge_body = http.calls[-1]
    assert merge_path.endswith("/files")
    assert merge_body == {
        "file_type": 4,
        "srv_send_msg": False,
        "file_name": "讲义.docx",
        "upload_id": "upload_x1",
    }


def test_upload_raises_without_file_info(monkeypatch, tmp_path):
    import kurotutor.adapters.channel.qq_media as qm

    f = tmp_path / "a.png"
    f.write_bytes(b"x")

    http = _FakeHttp({"upload_prepare": _PREPARE, "upload_part_finish": {}, "/files": {}})
    monkeypatch.setattr(qm.httpx, "AsyncClient", _FakeAsyncClient)
    with pytest.raises(QQMediaError, match="file_info"):
        _run(upload_c2c_file(http, "OPENID_X", str(f), file_type=1))


def test_direct_send_uploads_with_srv_send_msg(monkeypatch, tmp_path):
    import kurotutor.adapters.channel.qq_media as qm

    f = tmp_path / "讲义.docx"
    f.write_bytes(b"hello")
    http = _FakeHttp({"upload_prepare": _PREPARE, "upload_part_finish": {}, "/files": {}})
    monkeypatch.setattr(qm.httpx, "AsyncClient", _FakeAsyncClient)

    assert _run(direct_send_c2c_file(http, "OPENID_X", str(f), file_type=4)) is True
    merge_body = http.calls[-1][1]
    assert merge_body["srv_send_msg"] is True
    assert merge_body["upload_id"] == "upload_x1"
