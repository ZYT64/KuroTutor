"""OpenMAIC 互动课堂客户端（THU-MAIC/OpenMAIC 托管模式）。

流程：health（能力探测）→ submit（提交生成任务）→ poll（轮询到完成，取课堂链接）。
鉴权：所有请求带 ``Authorization: Bearer <access_code>``。
配额：托管站每天 10 次生成，403 表示当日额度用尽（次日 0 点重置）。
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx

from kurotutor.config.schema import OpenMAICConfig
from kurotutor.core import ProviderError, get_logger

log = get_logger("openmaic")

_TIMEOUT = httpx.Timeout(connect=10.0, read=30.0, write=30.0, pool=10.0)
# 生成任务轮询：官方建议 ~60s 间隔；总上限 40 分钟（生成通常 3-10 分钟）
_POLL_INTERVAL = 60.0
_POLL_MAX = 40


def _headers(cfg: OpenMAICConfig) -> dict[str, str]:
    if not (cfg.access_code or "").strip():
        raise ProviderError(
            "OpenMAIC 未配置访问码",
            cause="openmaic.access_code 为空",
            fix="登录 open.maic.chat → 访问码设置生成 sk- 码，填入 kuro.json 的 openmaic.access_code",
        )
    return {"Authorization": f"Bearer {cfg.access_code.strip()}"}


async def health(cfg: OpenMAICConfig) -> dict[str, Any]:
    """健康检查 + 能力探测。返回 {status, version, capabilities?}。"""
    url = cfg.base_url.rstrip("/") + "/api/health"
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            r = await client.get(url, headers=_headers(cfg))
    except httpx.RequestError as exc:
        raise ProviderError(
            "无法连接 OpenMAIC 服务",
            cause=str(exc)[:120],
            fix="检查网络可达性，或稍后重试",
        ) from exc
    if r.status_code == 401:
        raise ProviderError(
            "OpenMAIC 访问码无效",
            cause="服务端返回 401",
            fix="到 open.maic.chat 的「访问码设置」检查/重新生成访问码",
        )
    if r.status_code != 200:
        raise ProviderError(
            "OpenMAIC 服务异常",
            cause=f"HTTP {r.status_code}: {r.text[:120]}",
            fix="稍后重试，或改用本地部署模式",
        )
    data = r.json()
    return data if isinstance(data, dict) else {}


async def submit_generation(
    cfg: OpenMAICConfig, *, requirement: str, language: str = "zh-CN"
) -> dict[str, Any]:
    """提交课堂生成任务。返回 {jobId, pollUrl, pollIntervalMs?}。"""
    url = cfg.base_url.rstrip("/") + "/api/generate-classroom"
    body = {"requirement": requirement[:8000], "language": language}
    # 能力探测：托管站支持哪些可选项就带哪些（关闭项不传，保持向后兼容）
    try:
        caps = (await health(cfg)).get("capabilities") or {}
    except ProviderError:
        caps = {}
    for flag, cap in (
        ("enableTTS", "tts"),
        ("enableWebSearch", "webSearch"),
    ):
        if caps.get(cap) is True:
            body[flag] = True
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(connect=10, read=60, write=30, pool=10)) as client:
            r = await client.post(url, json=body, headers=_headers(cfg))
    except httpx.RequestError as exc:
        raise ProviderError("提交 OpenMAIC 生成任务失败", cause=str(exc)[:120], fix="检查网络后重试") from exc
    if r.status_code == 401:
        raise ProviderError("OpenMAIC 访问码无效", cause="401", fix="到 open.maic.chat 重新生成访问码")
    if r.status_code == 403:
        raise ProviderError(
            "OpenMAIC 今日生成额度已用完",
            cause=r.text[:120],
            fix="托管站每天 10 次，次日 0 点重置；或改用本地部署模式",
        )
    if r.status_code not in (200, 202):  # 202 = 已受理排队
        raise ProviderError(
            "提交 OpenMAIC 生成任务失败",
            cause=f"HTTP {r.status_code}: {r.text[:120]}",
            fix="稍后重试",
        )
    data = r.json()
    if not isinstance(data, dict) or not data.get("jobId"):
        raise ProviderError(
            "OpenMAIC 返回内容异常",
            cause=str(data)[:200],
            fix="确认托管站版本是否更新了接口",
        )
    return data


async def poll_generation(
    cfg: OpenMAICConfig, poll_url: str, *, max_rounds: int = _POLL_MAX
) -> dict[str, Any]:
    """轮询生成任务直到完成。返回最终状态（含 classroomUrl/id），超时抛错。

    托管站返回的 pollUrl 是 http:// 明文（会被 301 重定向），统一改用
    base_url 按 jobId 重新构造，并开启跟随重定向兜底。
    """
    job_id = poll_url.rstrip("/").rsplit("/", 1)[-1]
    url = cfg.base_url.rstrip("/") + "/api/generate-classroom/" + job_id
    async with httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=True) as client:
        for i in range(max_rounds):
            try:
                r = await client.get(url, headers=_headers(cfg))
            except httpx.RequestError as exc:
                if i == max_rounds - 1:
                    raise ProviderError(
                        "轮询 OpenMAIC 任务失败", cause=str(exc)[:120], fix="稍后在课堂上重试"
                    ) from exc
                await asyncio.sleep(_POLL_INTERVAL)
                continue
            if r.status_code != 200:
                raise ProviderError("轮询 OpenMAIC 任务失败", cause=f"HTTP {r.status_code}", fix="稍后重试")
            data = r.json()
            status = str(data.get("status") or "").lower()
            if status in ("succeeded", "success", "completed", "done"):
                return data
            if status in ("failed", "error"):
                raise ProviderError(
                    "OpenMAIC 课堂生成失败",
                    cause=str(data.get("error") or data)[:200],
                    fix="调整备课内容后重试，或稍后再试",
                )
            if i < max_rounds - 1:
                await asyncio.sleep(_POLL_INTERVAL)
    raise ProviderError(
        "OpenMAIC 课堂生成超时",
        cause=f"轮询 {max_rounds} 轮未完成",
        fix="课堂链接稍后可能仍会生成完毕；可手动重试",
    )


def extract_classroom_url(data: dict[str, Any], base_url: str) -> str:
    """从轮询结果里提取课堂链接；先看顶层与 result 嵌套，没有就按 id 拼。"""
    sources = [data, data.get("result") or {}]
    for src in sources:
        if not isinstance(src, dict):
            continue
        for key in ("classroomUrl", "url", "classroom_url"):
            v = str(src.get(key) or "").strip()
            if v:
                return v
        for key in ("classroomId", "id"):
            cid = str(src.get(key) or "").strip()
            if cid and key == "classroomId":
                return f"{base_url.rstrip('/')}/classroom/{cid}"
    return ""
