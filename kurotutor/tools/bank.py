"""题集工具：把值得重做/重看的题录入学生专属题库（错题 + 好题）。

与错题本（wrongbook，复习闭环）互补：题集是「收藏这道题」。录入策略由 Agent 按
系统提示词中的【题集录入策略】自主判断（自动录 / 问一句 / 不录），本工具只负责落库与去重。
"""

from __future__ import annotations

import re
from typing import Any

from sqlmodel import select

from kurotutor.agent.context import ToolContext
from kurotutor.storage import QuestionItem, session_scope

# 模糊去重用：剔除空白/中英文标点/符号/运算符全半角变体，大小写归一后比对
_STEM_NOISE = re.compile(
    r"[\s，。、；：？！“”‘’（）《》〈〉【】「」·…—．.,;:?!()（）\[\]【】{}<>\"'`~*#@$%^&\\|/+-"
    r"−＝－×÷≈≠≥≤^=]+"
)


def _text(v: Any) -> str:
    return (str(v) if v is not None else "").strip()


def _norm_stem(s: str) -> str:
    """题干规范化：去空白标点、小写化，用于模糊判重（OCR 转写差异不误判同题）。"""
    return _STEM_NOISE.sub("", s.casefold())[:120]


async def bank_add(ctx: ToolContext, kwargs: dict[str, Any]) -> str:
    """录题入题集。

    参数：question（必填）、kind（error/good，默认 good）、subject、knowledge_point、image_path、reason。
    """
    if ctx.student is None:
        return "当前没有学生上下文，无法录题。"
    question = _text(kwargs.get("question"))
    if not question and not _text(kwargs.get("image_path")):
        return "请提供题目内容（question）或题图路径（image_path），至少一项。"
    kind = _text(kwargs.get("kind")).lower() or "good"
    if kind not in ("error", "good"):
        return "kind 只支持 error（错题）或 good（好题）。"

    subject = _text(kwargs.get("subject"))
    knowledge_point = _text(kwargs.get("knowledge_point"))
    image_path = _text(kwargs.get("image_path"))
    reason = _text(kwargs.get("reason"))
    source = _text(kwargs.get("source")) or "tutoring"

    with session_scope(ctx.engine) as db:
        # 去重护栏：同一学生 + 相同题干（规范化后模糊比对，OCR/转写差异不重复收录）
        stem = _norm_stem(question)
        dup = db.exec(
            select(QuestionItem)
            .where(QuestionItem.student_id == ctx.student.id)
            .where(QuestionItem.kind == kind)
            .limit(50)
        ).all()
        for item in dup:
            if stem and _norm_stem(item.question_text) == stem:
                return f"这道题已在题集里了（{item.created_at:%m-%d} 录入），不重复收录。"
        item = QuestionItem(
            student_id=ctx.student.id,
            kind=kind,
            subject=subject,
            knowledge_point=knowledge_point,
            question_text=question,
            image_path=image_path,
            reason=reason,
            source=source,
        )
        db.add(item)
        db.flush()
        qid = item.id
    label = "错题" if kind == "error" else "好题"
    parts = [f"已把这道{label}录入题集（编号 {qid}）。"]
    if knowledge_point:
        parts.append(f"知识点：{knowledge_point}")
    if reason:
        parts.append(f"收录理由：{reason}")
    return "；".join(parts) + "。"


async def bank_list(ctx: ToolContext, kwargs: dict[str, Any]) -> str:
    """查看学生题集。参数：kind（error/good，可选，缺省全部）、limit（默认 10）。"""
    if ctx.student is None:
        return "当前没有学生上下文。"
    kind = _text(kwargs.get("kind")).lower() or None
    limit = min(int(kwargs.get("limit") or 10), 50)
    with session_scope(ctx.engine) as db:
        stmt = (
            select(QuestionItem)
            .where(QuestionItem.student_id == ctx.student.id)
            .order_by(QuestionItem.id.desc())
            .limit(limit)
        )
        if kind:
            stmt = stmt.where(QuestionItem.kind == kind)
        rows = db.exec(stmt).all()
    if not rows:
        return "题集还是空的。讲题过程中遇到值得收藏的题，我会问你要不要收进来。"
    lines = [f"题集共 {len(rows)} 条（新→旧）："]
    for r in rows:
        head = (r.question_text[:40] + "…") if len(r.question_text) > 40 else (r.question_text or "(图片题)")
        tag = "错题" if r.kind == "error" else "好题"
        lines.append(f"· [{tag}] {head}" + (f"（{r.knowledge_point}）" if r.knowledge_point else ""))
    return "\n".join(lines)


async def bank_remove(ctx: ToolContext, kwargs: dict[str, Any]) -> str:
    """从题集移除一条。参数：question_id。"""
    if ctx.student is None:
        return "当前没有学生上下文。"
    qid = kwargs.get("question_id")
    if qid is None:
        return "请提供要移除的编号（question_id），可用 bank_list 查询。"
    with session_scope(ctx.engine) as db:
        item = db.get(QuestionItem, int(qid))
        if item is None or item.student_id != ctx.student.id:
            return f"编号 {qid} 不在你的题集里。"
        db.delete(item)
    return f"已从题集移除编号 {qid}。"


async def bank_extract(ctx: ToolContext, kwargs: dict[str, Any]) -> str:
    """按条件筛选题集并组卷导出（PDF / Word）。

    参数：kind、subject、keyword、limit、format（pdf/docx，默认 pdf），均可选。
    图片题整图嵌入；文字题按中文排版渲染。生成文件放在工作区 exports/ 下。
    """
    if ctx.student is None:
        return "当前没有学生上下文。"
    kind = _text(kwargs.get("kind")).lower() or None
    subject = _text(kwargs.get("subject")) or None
    keyword = _text(kwargs.get("keyword")) or None
    limit = min(int(kwargs.get("limit") or 50), 200)

    with session_scope(ctx.engine) as db:
        stmt = (
            select(QuestionItem)
            .where(QuestionItem.student_id == ctx.student.id)
            .order_by(QuestionItem.kind, QuestionItem.id)
            .limit(limit)
        )
        if kind:
            stmt = stmt.where(QuestionItem.kind == kind)
        if subject:
            stmt = stmt.where(QuestionItem.subject == subject)
        rows = db.exec(stmt).all()
    if keyword:
        rows = [r for r in rows if keyword in r.question_text or keyword in r.knowledge_point]
    if not rows:
        return "没有符合条件的题：题集里找不到匹配的题目。可先用 bank_list 看看题集里有什么。"

    fmt = (_text(kwargs.get("format")) or "pdf").lower()
    if fmt not in ("pdf", "docx"):
        return "format 只支持 pdf 或 docx（组卷导出格式）。"
    try:
        out_path = _compose_paper(rows, ctx.workspace, fmt)
    except Exception as exc:
        return f"组卷生成失败：{exc}（请稍后重试或联系管理员查看日志）"

    label_parts = []
    if kind:
        label_parts.append("错题" if kind == "error" else "好题")
    if subject:
        label_parts.append(subject)
    if keyword:
        label_parts.append(f"含「{keyword}」")
    cond = "、".join(label_parts) if label_parts else "全部"
    errors = sum(1 for r in rows if r.kind == "error")
    return (
        f"已从题集提取 {len(rows)} 道题（筛选：{cond}；其中错题 {errors}、好题 {len(rows) - errors}），"
        f"组卷完成（{fmt.upper()}）：{out_path}\n可以直接把这份卷子发给学生。"
    )


def _compose_paper(rows: list[QuestionItem], workspace: str, fmt: str) -> str:
    """组卷：统一拼轻量标记（题头/题图/题干/理由），按格式渲染为 PDF 或 Word。"""
    from datetime import datetime as _dt
    from pathlib import Path as _Path

    from kurotutor.services.docs import write_document

    lines = [f"# 题集 · {_dt.now():%Y-%m-%d}"]
    for i, r in enumerate(rows, 1):
        tag = "错题" if r.kind == "error" else "好题"
        head = f"{i}. [{tag}]" + (f" {r.knowledge_point}" if r.knowledge_point else "")
        lines.append(f"## {head}")
        if r.image_path and _Path(r.image_path).exists():
            lines.append(f"![]({r.image_path})")
        if r.question_text:
            lines.append(r.question_text)
        if r.reason:
            lines.append(f"收录理由：{r.reason}")
    content = "\n".join(lines)
    out_dir = _Path(workspace) / "exports"
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"题集_{_dt.now():%Y%m%d_%H%M}.{fmt}"
    return write_document(str(out), content)
