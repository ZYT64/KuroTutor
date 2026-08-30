"""文档识别工具：OCR 识别链 + MinerU 复杂版面解析。

给 Agent 的定位（详见系统提示词【文档识别策略】）：
- ocr_read：普通文字识别，自动走配置的识别链（默认百度 → 腾讯 → 本地）；
- mineru_parse：复杂版面（公式/表格/双栏教材）专属，先看文件类型再决定是否用。
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from kurotutor.agent.context import ToolContext
from kurotutor.core.errors import ToolError
from kurotutor.services import ocr as ocr_svc


def _resolve(ctx: ToolContext, raw: str) -> Path:
    p = ctx.sandbox.resolve_path(raw)
    if not p.exists():
        raise ToolError(f"文件不存在：{raw}", cause="路径无效", fix="确认文件已上传到工作区")
    return p


async def ocr_read(ctx: ToolContext, kwargs: dict[str, Any]) -> str:
    """识别图片/扫描 PDF 里的文字（自动走配置的识别链）。

    参数：path（图片或 PDF 的工作区路径）、max_pages（PDF 最大页数，默认 10）。
    """
    if not (kwargs.get("path") or "").strip():
        raise ToolError("请提供 path（图片或 PDF 的工作区路径）")
    path = _resolve(ctx, kwargs["path"])
    cfg = ctx.config
    chain_desc = " → ".join(list(getattr(cfg.ocr, "chain", []) or ["baidu", "tencent", "local"]))
    try:
        if path.suffix.lower() == ".pdf":
            max_pages = min(int(kwargs.get("max_pages") or 10), 30)
            text, engine = await asyncio.to_thread(
                ocr_svc.ocr_pdf_pages, path, cfg, max_pages=max_pages
            )
        else:
            text, engine = await asyncio.to_thread(
                ocr_svc.ocr_image_with_chain, path.read_bytes(), cfg
            )
    except ocr_svc.OcrError as exc:
        return f"识别失败：{exc}"
    if not text.strip():
        return "识别完成，但页面上没有可识别的文字（可能是空白页或纯图片）。"
    note = f"\n\n（识别引擎：{engine}；识别链：{chain_desc}）" if engine else ""
    return text[:20000] + note


async def mineru_parse(ctx: ToolContext, kwargs: dict[str, Any]) -> str:
    """MinerU 专属解析：复杂版面（公式/表格/双栏教材扫描件）转 Markdown。

    仅在确认文件含复杂公式/表格/版面时使用；普通文字识别请用 ocr_read。
    参数：path（PDF/图片的工作区路径）。
    """
    if not (kwargs.get("path") or "").strip():
        raise ToolError("请提供 path（PDF 或图片的工作区路径）")
    path = _resolve(ctx, kwargs["path"])
    if path.suffix.lower() not in (".pdf", ".png", ".jpg", ".jpeg"):
        return "MinerU 只支持 PDF 或图片文件。"
    try:
        text = await asyncio.to_thread(ocr_svc.mineru_parse_file, path, ctx.config)
    except ocr_svc.OcrError as exc:
        return f"MinerU 解析失败：{exc}"
    return text[:40000] or "（MinerU 返回了空结果）"



