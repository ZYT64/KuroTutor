"""文件工具：所有文件读写必须经过沙箱路径校验（架构红线硬约束①）。

提供工作区内的读写与远程图片下载。任何工具要落盘/读盘，都用这里的安全封装，
禁止直接 open 绝对路径，以免越出工作区或触碰系统目录。
"""

from __future__ import annotations

import uuid
from pathlib import Path

import httpx

from kurotutor.agent.context import ToolContext
from kurotutor.core import SandboxError, get_logger

log = get_logger("files")

_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
_JPEG_MAGIC = b"\xff\xd8\xff"
_WEBP_MAGIC = b"RIFF"  # 需配合 0x57454250 校验


def resolve_workspace_path(ctx: ToolContext, raw: str, *, for_write: bool = False) -> Path:
    """沙箱安全的工作区路径解析。"""
    return ctx.sandbox.resolve_path(raw, for_write=for_write)


def read_workspace_file(ctx: ToolContext, raw: str) -> str:
    """读工作区文本文件，防越界。失败抛 SandboxError。"""
    path = resolve_workspace_path(ctx, raw, for_write=False)
    if not path.exists():
        raise SandboxError("文件不存在", cause=str(path), fix="确认路径在工作区内且文件已存在")
    return path.read_text(encoding="utf-8")


def write_workspace_file(ctx: ToolContext, raw: str, content: str) -> str:
    """写工作区文本文件（自动建目录），返回落盘路径。"""
    path = resolve_workspace_path(ctx, raw, for_write=True)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return str(path)


def _detect_ext(header: bytes, url: str) -> str:
    if header.startswith(_PNG_MAGIC):
        return ".png"
    if header.startswith(_JPEG_MAGIC):
        return ".jpg"
    if header.startswith(_WEBP_MAGIC) and url.lower().endswith(".webp"):
        return ".webp"
    # 由 URL 推断
    suffix = Path(url).suffix.lower()
    return suffix if suffix in (".png", ".jpg", ".jpeg", ".webp", ".gif") else ".img"


def save_remote_image(url: str, workspace: str | Path) -> str:
    """下载远程图片到工作区，返回落盘路径。用于渠道收到的 QQ 图片。

    ⚠️ 网络请求仅允许访问配置白名单内的端点之外的任意 URL 会带来 SSRF 风险，
    这里默认仅下载图片；生产环境应对 url 做 host 白名单校验。
    """
    workspace = Path(workspace).resolve()
    workspace.mkdir(parents=True, exist_ok=True)
    timeout = httpx.Timeout(connect=10.0, read=30.0, write=10.0, pool=10.0)
    try:
        resp = httpx.get(url, timeout=timeout, follow_redirects=True)
        resp.raise_for_status()
        data = resp.content
    except Exception as exc:
        log.warning("image download failed", url=url, error=str(exc))
        raise SandboxError("图片下载失败", cause=str(exc), fix="确认图片 URL 可访问且网络畅通") from exc

    ext = _detect_ext(data[:16], url)
    subdir = workspace / "incoming"
    subdir.mkdir(parents=True, exist_ok=True)
    path = subdir / f"{uuid.uuid4().hex}{ext}"
    path.write_bytes(data)
    return str(path)
