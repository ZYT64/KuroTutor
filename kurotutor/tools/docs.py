"""Agent 通用文档工具：Word / PPT / PDF 的读、写、编辑、合并拆页、格式互转。

学生发来课件/讲义/试卷文档要讲、要改、要合并拆分，或要求产出文档时使用。
所有路径经沙箱校验（工作区限定）。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from kurotutor.agent.context import ToolContext
from kurotutor.core.errors import ToolError
from kurotutor.services import docs as docs_svc


def _resolve(ctx: ToolContext, path: str, *, for_write: bool) -> str:
    if path.startswith(("http://", "https://")):
        raise ToolError("文档工具不支持网络路径", fix="先把文件下载到工作区再操作")
    return str(ctx.sandbox.resolve_path(path, for_write=for_write))


async def doc_read(ctx: ToolContext, kwargs: dict[str, Any]) -> str:
    """读文档内容（.docx/.pptx/.pdf/.txt/.md → 结构化文本）。参数：path。"""
    path = (kwargs.get("path") or "").strip()
    if not path:
        return "请提供文档路径（path）。"
    try:
        text = docs_svc.read_document(_resolve(ctx, path, for_write=False))
    except Exception as exc:
        return f"读取失败：{exc}"
    if not text.strip():
        return "文档是空的。"
    return f"已读取 {Path(path).name}，内容如下：\n{text}"


async def doc_write(ctx: ToolContext, kwargs: dict[str, Any]) -> str:
    """生成文档（.docx/.pptx/.pdf）。参数：path（输出路径，后缀定格式）、content（轻量标记）。

    标记规则：`# ` 大标题；`## ` 节标题（pptx 中为新一页）；`- ` 列表项；普通行为段落。
    """
    path = (kwargs.get("path") or "").strip()
    content = kwargs.get("content")
    if not path or content is None or not str(content).strip():
        return "请提供输出路径（path）与内容（content，支持 # 标题 / ## 节 / - 列表 轻量标记）。"
    try:
        out = docs_svc.write_document(_resolve(ctx, path, for_write=True), str(content))
    except Exception as exc:
        return f"生成失败：{exc}"
    return f"文档已生成：{out}"


async def doc_edit(ctx: ToolContext, kwargs: dict[str, Any]) -> str:
    """编辑既有文档。

    参数：path、op（append/replace）、content（追加内容或被替换原文）、replacement（新文本）。
    """
    path = (kwargs.get("path") or "").strip()
    op = (kwargs.get("op") or "").strip()
    content = str(kwargs.get("content") or "")
    replacement = str(kwargs.get("replacement") or "")
    if not path or not op:
        return "请提供文档路径（path）与操作（op=append/replace）。"
    if op == "replace" and not content:
        return "replace 操作需要 content（要被替换的原文）与 replacement（新文本）。"
    try:
        out = docs_svc.edit_document(
            _resolve(ctx, path, for_write=True), op, content, replacement=replacement
        )
    except Exception as exc:
        return f"编辑失败：{exc}"
    return f"文档已更新：{out}"


async def pdf_ops(ctx: ToolContext, kwargs: dict[str, Any]) -> str:
    """PDF 页级操作。

    op=merge：合并 paths 列表 → path 输出；op=extract：从 src 抽 pages（如 1,3,5-8）→ path 输出。
    """
    op = (kwargs.get("op") or "").strip()
    out = (kwargs.get("path") or "").strip()
    if not op or not out:
        return "请提供 op（merge/extract）与输出路径（path）。"
    try:
        if op == "merge":
            raw = kwargs.get("paths") or []
            if isinstance(raw, str):
                raw = [p.strip() for p in raw.split(",") if p.strip()]
            resolved = [_resolve(ctx, str(p), for_write=False) for p in raw]
            result = docs_svc.pdf_merge(resolved, _resolve(ctx, out, for_write=True))
        elif op == "extract":
            src = (kwargs.get("src") or "").strip()
            pages = str(kwargs.get("pages") or "")
            if not src or not pages:
                return "extract 需要 src（源 PDF）与 pages（页码，如 1,3,5-8）。"
            result = docs_svc.pdf_extract_pages(
                _resolve(ctx, src, for_write=False), pages, _resolve(ctx, out, for_write=True)
            )
        else:
            return "op 只支持 merge（合并）/ extract（抽页）。"
    except Exception as exc:
        return f"PDF 操作失败：{exc}"
    return f"完成：{result}"


async def doc_convert(ctx: ToolContext, kwargs: dict[str, Any]) -> str:
    """文档格式互转（经 LibreOffice）。参数：path（源文档）、target_fmt（如 pdf/docx/pptx/txt）。"""
    path = (kwargs.get("path") or "").strip()
    target = (kwargs.get("target_fmt") or "").strip()
    if not path or not target:
        return "请提供源文档路径（path）与目标格式（target_fmt，如 pdf/docx/pptx/txt）。"
    out_dir = ctx.sandbox.resolve_path("exports", for_write=True)
    try:
        result = docs_svc.convert_document(_resolve(ctx, path, for_write=False), str(out_dir), target)
    except Exception as exc:
        return f"转换失败：{exc}"
    return f"转换完成：{result}"
