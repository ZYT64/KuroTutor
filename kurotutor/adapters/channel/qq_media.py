"""QQ 单聊富媒体分片上传（官方 2026-07 新版接口）。

官方文档（bot.q.qq.com/wiki，2026-07 更新）已移除 base64 ``file_data`` 直传参数，
本地文件唯一的上传路径是四步分片流程::

    ① POST /v2/users/{openid}/upload_prepare   → upload_id + block_size + 各分片预签名 URL
    ② 逐片 HTTP PUT 到预签名 URL（无需鉴权头，签名在 URL 里）
    ③ POST /v2/users/{openid}/upload_part_finish（每片完成后确认）
    ④ POST /v2/users/{openid}/files + upload_id 合并 → file_info

拿到 file_info 后用发消息接口 ``msg_type=7 + media.file_info`` 发送。

file_type：1=图片（png/jpg/gif/webp/bmp）2=视频（mp4）3=语音（silk）4=文件（任意格式）。
大小限制（软/硬）：图片 20/200MB，视频 30/200MB，语音 20/200MB，文件 200/200MB；
超过软限制服务端自动降级为文件类型，超过硬限制报错。
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import httpx

from kurotutor.core import get_logger

log = get_logger("qq-media")

# md5_10m 校验窗口：文件前 10002432 字节（约 10MB）的 MD5，用于服务端秒传判断
_MD5_10M_WINDOW = 10002432
# 分片 PUT 的超时（上传接口官方建议超时 ≥ 5 秒；大分片放大给足）
_PUT_TIMEOUT = httpx.Timeout(connect=10.0, read=60.0, write=300.0, pool=10.0)


class QQMediaError(RuntimeError):
    """富媒体上传失败（现象 + 原因，供上层兜底文案与日志使用）。"""


async def upload_c2c_file(http: Any, openid: str, file_path: str, file_type: int) -> str:
    """把本地文件分片上传到 QQ 单聊，返回 file_info（供 msg_type=7 发送）。

    ``http``：botpy 客户端的 HTTP 层（自动携带 QQBot token）。
    失败抛 :class:`QQMediaError`，调用方负责兜底。
    """
    file_info = await _upload_c2c(http, openid, file_path, file_type, srv_send_msg=False)
    if not file_info:
        raise QQMediaError("合并上传未返回 file_info")
    return file_info


async def direct_send_c2c_file(http: Any, openid: str, file_path: str, file_type: int) -> bool:
    """分片上传并直发（srv_send_msg=true，一步完成，作为主动消息发送）。

    用于被动回复失败（如 msg_id 过期）时的降级。成功返回 True。
    """
    await _upload_c2c(http, openid, file_path, file_type, srv_send_msg=True)
    return True


async def _upload_c2c(
    http: Any, openid: str, file_path: str, file_type: int, *, srv_send_msg: bool
) -> str:
    """分片上传核心流程；``srv_send_msg=True`` 时合并即直发，返回空串。"""
    from botpy.http import Route

    path = Path(file_path)
    data = path.read_bytes()
    if not data:
        raise QQMediaError(f"文件为空：{path.name}")

    # ① 预上传：文件大小/名称 + 三个校验值
    prep = await http.request(
        Route("POST", "/v2/users/{openid}/upload_prepare", openid=openid),
        json={
            "file_type": int(file_type),
            "file_size": str(len(data)),
            "file_name": path.name,
            "md5": hashlib.md5(data).hexdigest(),
            "sha1": hashlib.sha1(data).hexdigest(),
            "md5_10m": hashlib.md5(data[:_MD5_10M_WINDOW]).hexdigest(),
        },
    )
    if not isinstance(prep, dict) or not prep.get("upload_id"):
        raise QQMediaError(f"upload_prepare 未返回 upload_id：{prep!r}")
    upload_id = str(prep["upload_id"])
    parts = prep.get("parts") or []
    if not parts:
        raise QQMediaError(f"upload_prepare 未返回分片列表：{prep!r}")

    # ②③ 逐片 PUT 预签名 URL + part_finish 确认
    async with httpx.AsyncClient(timeout=_PUT_TIMEOUT) as client:
        offset = 0
        for part in parts:
            block = int(part.get("block_size") or 0)
            chunk = data[offset : offset + block]
            idx = int(part.get("index", offset // max(block, 1)))
            url = str(part.get("presigned_url") or "")
            if not url:
                raise QQMediaError(f"分片 {idx} 缺少 presigned_url")
            resp = await client.put(url, content=chunk)
            if resp.status_code // 100 != 2:
                raise QQMediaError(f"分片 {idx} PUT 失败：HTTP {resp.status_code}")
            await http.request(
                Route("POST", "/v2/users/{openid}/upload_part_finish", openid=openid),
                json={
                    "upload_id": upload_id,
                    "part_index": idx,
                    "block_size": str(len(chunk)),
                    "md5": hashlib.md5(chunk).hexdigest(),
                },
            )
            offset += len(chunk)
        if offset != len(data):
            raise QQMediaError(f"分片覆盖不完整：已传 {offset}/{len(data)} 字节")

    # ④ 合并 → file_info（srv_send_msg=true 时消息已直发，响应无 file_info）
    merged = await http.request(
        Route("POST", "/v2/users/{openid}/files", openid=openid),
        json={
            "file_type": int(file_type),
            "srv_send_msg": srv_send_msg,
            "file_name": path.name,
            "upload_id": upload_id,
        },
    )
    if isinstance(merged, dict):
        return str(merged.get("file_info") or "")
    return ""
