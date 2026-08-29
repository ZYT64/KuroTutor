"""自动切题工具（产品规格书 4.8，题集→题库用）。

切题 fallback 链：题号锚定切块（RapidOCR 默认）→ 墨迹投影 → 百度专用切题
→ 版面 OCR 分组 → 视觉「仅转写」。低性能服务器只需发 HTTP 或本地 ONNX。
跨页策略：上一页尾块 + 下一页 q0_residual 由视觉模型判断连续性，连续则自动缝合为完整题。
文档入口：PDF 原生逐页渲染；Word/PPT 经 LibreOffice 无头转 PDF 后复用同一条链路。
"""

from __future__ import annotations

import contextlib
import json
from pathlib import Path
from typing import Any

from kurotutor.agent.context import ToolContext
from kurotutor.core import get_logger, log_event
from kurotutor.core.errors import ToolError
from kurotutor.services import imgprep
from kurotutor.services.layout import (
    build_layout_provider,
    crop_questions,
    cut_blocks_by_ink,
    cut_by_question_numbers,
    group_lines_into_questions,
    layout_text,
    office_to_pdf,
    pdf_to_page_images,
    question_split_cut,
    stitch_crops,
)
from kurotutor.services.vision import build_vision_provider, extract_json, resolve_vision_spec
from kurotutor.storage import WorkingContext, session_scope

log = get_logger("image_split")

_SPLIT_PROMPT = (
    "这是一张可能包含多道题目的图片。请先统计题数，然后按顺序把每道题的完整内容（大题含所有小题、共用题干）"
    "转写清楚。注意：手写字迹是学生作答，不要转写进题干；只转写印刷的题目本体。"
    "只输出一个 JSON 对象：\n"
    '{"problem_count":N,"questions":["题目1全文","题目2全文",...]}'
    "\n若只有一道题就填 1；大题带小题视为一道整体，不要拆散。看不清就留空，不要编造。"
)

_CONTINUOUS_PROMPT = (
    "这张图片由两张试卷裁片上下拼接而成。请仔细判断：上半部分末尾的题目和下半部分开头的内容，"
    "是否为同一道题的连续两部分（例如上半题干/填空未完，下半紧接着继续叙述或接小题）？"
    "还是两段互不相关的内容？重点看文字是否自然衔接。只输出 JSON："
    '{"continuous": true} 或 {"continuous": false}，不要其他文字。'
)

# WorkingContext.current_problem JSON 中保存「上一页尾块」的键
_TAIL_KEY = "last_split_tail"
_CACHE_KEY = "last_split_result"  # {path, mtime, result}：同一张图短时间重复切题直接回缓存


async def split_photo(ctx: ToolContext, kwargs: dict[str, Any]) -> str:
    """切题。参数：path（图片路径）。自动处理跨页残句（与上一页尾块缝合）。"""
    path = (kwargs.get("path") or "").strip()
    if not path:
        return "请提供图片路径（path）。"
    if not path.startswith(("http://", "https://")):
        path = str(ctx.sandbox.resolve_path(path, for_write=False))

    cached = _load_split_cache(ctx, path)
    if cached:
        return cached

    crops = await _crop_pipeline(ctx, path)
    if crops is None:
        # ⑥ 回退：仅转写（不裁剪）
        return await _transcribe_only(ctx, path)

    # 跨页：本页头部残句 × 上一页尾块 → 视觉判断连续则缝合
    prev_tail = _load_prev_tail(ctx)
    crops, merge_note = await _crosspage_merge(ctx, crops, prev_tail)
    _save_prev_tail(ctx, crops[-1])

    issues, texts = await _review_crops(ctx, crops)
    text_of = dict(texts)
    lines = [f"已把这一页切成 {len(crops)} 块："]
    if merge_note:
        lines.append(merge_note)
    for i, p in enumerate(crops, 1):
        tag = "（上一页跨页残句，非完整题）" if "residual" in p else ""
        lines.append(f"第{i}块图片：{p} {tag}")
        t = text_of.get(p)
        if t and t.strip():
            lines.append(f"   块内文字：{t}")
    if issues:
        lines.append("⚠️ 完整性提示：")
        lines.extend(issues)
    lines.append("题目图片已存入 qbench/。上面每块已附 OCR 文字——"
    "bank_add 录入时直接引用文字与图片路径，无需再逐图视觉识别。本页已处理完成，无需重复调用本工具。")
    result = "\n".join(lines)
    _save_split_cache(ctx, path, result)
    return result


def _load_split_cache(ctx: ToolContext, path: str) -> str | None:
    """同一张图（路径+修改时间相同）刚切过 → 直接返回上次结果，避免 Agent 重复调用重复 OCR。"""
    if ctx.student is None:
        return None
    try:
        mtime = Path(path).stat().st_mtime
    except OSError:
        return None
    with session_scope(ctx.engine) as db:
        wc = db.get(WorkingContext, ctx.student.id)
        if wc is None:
            return None
        try:
            data = json.loads(wc.current_problem or "{}")
        except Exception:
            return None
        cache = data.get(_CACHE_KEY) or {}
        if cache.get("path") == path and cache.get("mtime") == mtime:
            result = str(cache.get("result") or "")
            if result:
                return result + "\n（注：这张图刚切过，以上为缓存结果，请勿重复调用。）"
    return None


def _save_split_cache(ctx: ToolContext, path: str, result: str) -> None:
    if ctx.student is None:
        return
    try:
        mtime = Path(path).stat().st_mtime
    except OSError:
        return
    with session_scope(ctx.engine) as db:
        wc = db.get(WorkingContext, ctx.student.id)
        if wc is None:
            wc = WorkingContext(student_id=ctx.student.id)
            db.add(wc)
        try:
            data = json.loads(wc.current_problem or "{}")
        except Exception:
            data = {}
        data[_CACHE_KEY] = {"path": path, "mtime": mtime, "result": result[:4000]}
        wc.current_problem = json.dumps(data, ensure_ascii=False)
        db.add(wc)


async def _review_crops(ctx: ToolContext, crops: list[str]) -> tuple[list[str], list[tuple[str, str]]]:
    """对切图产物做一次轻量 OCR（最多 12 块），同时产出：

    - 完整性问题列表（截断疑点/字迹乱提示）；
    - 每块的 OCR 文字（截断 300 字），供 Agent 直接录入 bank_add，免去逐图视觉识别。
    """
    issues: list[str] = []
    texts: list[tuple[str, str]] = []
    spec = ctx.config.models.layout if ctx.config.models else None
    if spec is None:
        return issues, texts
    provider = None
    try:
        provider = build_layout_provider(spec)
    except Exception:
        return issues, texts
    try:
        for idx, crop in enumerate(crops[:12], 1):
            if "residual" in Path(crop).name:  # 残句块不参与题目录入盘点
                continue
            try:
                ocr_lines = await provider.layout(crop)
                text = "".join(ln.text for ln in ocr_lines)
            except Exception:
                text = ""
            texts.append((crop, text[:300]))
            problem = imgprep.assess_text_completeness(text)
            if problem:
                issues.append(
                    f"· 第{idx}块：{problem}，可能是下一页还有内容或裁剪边界没对齐，进题库前先人工确认"
                )
            if len(text.strip()) < 8:
                issues.append(
                    f"· 第{idx}块：几乎没有识别到文字——可能字迹较乱、拍摄模糊或不完整，建议重拍这张"
                )
    except Exception as exc:
        log_event(log, "crop review failed", level="warning", error=repr(exc))
    finally:
        with contextlib.suppress(Exception):
            await provider.aclose()
    return issues, texts


async def _crop_pipeline(ctx: ToolContext, path: str) -> list[str] | None:
    """一条页图 → 题块列表的裁剪管线。无法裁剪（需转写）时返回 None。"""
    # ⓪ 鲁棒性预处理：透视/边界矫正 → 倾斜矫正 → 低对比增强（失败自动用原图）
    out_dir = ctx.student_dir("qbench/prep")
    cleaned = imgprep.preprocess_image(path, str(out_dir))
    path = cleaned
    # ① 题号锚定切块（含图）：从每题题号裁到下一题，整块保留（图/公式一起）；文字交给视觉读
    crops = await _crop_via_numbers(ctx, path)
    if not crops:
        # ② 墨迹投影切块（纯图像、含图、免费本地）
        crops = await _crop_via_ink(ctx, path)
    if not crops:
        # ③ 百度专用切题 API（paper_cut，直接按题返回检测框）
        crops = await _crop_via_papercut(ctx, path)
    if not crops:
        # ④ 版面 OCR 行 + 题目分组（剔除步骤）
        crops = await _crop_via_layout(ctx, path)
    return crops or None


async def _crosspage_merge(
    ctx: ToolContext, crops: list[str], prev_tail: str | None
) -> tuple[list[str], str | None]:
    """若本页第一块是跨页残句且上一页尾块在手，让视觉模型判断是否连续；连续则缝合。

    返回 (新裁片列表, 给用户的说明)。不满足条件/无法判断/判断不连续时原样返回。
    """
    if not crops or "residual" not in Path(crops[0]).name or not prev_tail:
        return crops, None
    out_dir = Path(crops[0]).parent
    probe = str(out_dir / "_probe_merge.png")
    try:
        stitch_crops([prev_tail, crops[0]], probe)
    except Exception as exc:
        log_event(log, "stitch probe failed", level="warning", error=repr(exc))
        return crops, None
    verdict = await _judge_continuous(ctx, probe)
    if verdict is not True:
        with contextlib.suppress(Exception):
            Path(probe).unlink()
        if verdict is None:
            return crops, "检测到跨页残句，但未配置视觉模型，无法自动判断是否与上一页连续；已分开保留。"
        return crops, None
    final = _unique_path(out_dir, "q_cross_merged.png")
    with contextlib.suppress(Exception):
        Path(probe).replace(final)
    if not Path(final).exists():
        return crops, None
    return [final] + crops[1:], (
        f"上一页尾块与本题残句已确认为同一道题，自动拼合为完整题图：{final}"
    )


def _unique_path(out_dir: Path, name: str) -> str:
    """输出路径防覆盖：q_cross_merged.png 已存在时追加序号。"""
    stem, suffix = Path(name).stem, Path(name).suffix
    candidate = out_dir / name
    n = 2
    while candidate.exists():
        candidate = out_dir / f"{stem}_{n}{suffix}"
        n += 1
    return str(candidate)


async def _judge_continuous(ctx: ToolContext, stitched_path: str) -> bool | None:
    """视觉模型二元判断拼接图是否为同一题的连续两部分。无法判断返回 None。"""
    if ctx.config.models is None or ctx.config.models.vision is None:
        return None
    provider = build_vision_provider(resolve_vision_spec(ctx.config))
    try:
        raw = await provider.understand(stitched_path, _CONTINUOUS_PROMPT)
    except Exception as exc:
        log_event(log, "crosspage judge failed", level="warning", error=repr(exc))
        return None
    finally:
        await provider.aclose()
    data = extract_json(raw)
    if not isinstance(data, dict) or not isinstance(data.get("continuous"), bool):
        return None
    return data["continuous"]


def _load_prev_tail(ctx: ToolContext) -> str | None:
    """读上一页保存的尾块路径（学生工作上下文）；不存在/文件已删返回 None。"""
    if ctx.student is None:
        return None
    with session_scope(ctx.engine) as db:
        wc = db.get(WorkingContext, ctx.student.id)
        if wc is None:
            return None
        try:
            data = json.loads(wc.current_problem or "{}")
        except Exception:
            return None
        tail = (data.get(_TAIL_KEY) or {}).get("path")
        if tail and Path(tail).exists():
            return tail
    return None


def _save_prev_tail(ctx: ToolContext, path: str) -> None:
    """把本页最后一块存为尾块（与题目讲解上下文共存于同一 JSON，互不覆盖）。"""
    if ctx.student is None:
        return
    with session_scope(ctx.engine) as db:
        wc = db.get(WorkingContext, ctx.student.id)
        if wc is None:
            wc = WorkingContext(student_id=ctx.student.id)
            db.add(wc)
        try:
            data = json.loads(wc.current_problem or "{}")
        except Exception:
            data = {}
        data[_TAIL_KEY] = {"path": path}
        wc.current_problem = json.dumps(data, ensure_ascii=False)
        db.add(wc)


async def split_document(ctx: ToolContext, kwargs: dict[str, Any]) -> str:
    """题集文档切题：PDF 原生；Word/PPT 经 LibreOffice 转 PDF。逐页切题并自动缝合跨页题。

    参数：path（文档路径，支持 .pdf/.docx/.doc/.pptx/.ppt）。
    """
    path = (kwargs.get("path") or "").strip()
    if not path:
        return "请提供文档路径（path）。"
    resolved = str(ctx.sandbox.resolve_path(path, for_write=False))
    suffix = Path(resolved).suffix.lower()
    pages_dir = ctx.student_dir("qbench/doc_pages")
    try:
        if suffix == ".pdf":
            pages = pdf_to_page_images(resolved, str(pages_dir))
        elif suffix in (".doc", ".docx", ".ppt", ".pptx"):
            pdf = office_to_pdf(resolved, str(pages_dir))
            pages = pdf_to_page_images(pdf, str(pages_dir))
        else:
            return f"不支持的文档格式：{suffix}（支持 .pdf / .docx / .doc / .pptx / .ppt）"
    except ToolError:
        raise
    except Exception as exc:
        raise ToolError(str(exc), fix="确认文档存在且未加密") from exc
    if not pages:
        return "文档里没有可渲染的页面。"

    out_lines = [f"文档共 {len(pages)} 页，逐页切题中…"]
    prev_tail: str | None = _load_prev_tail(ctx)  # 文档前若有零散发图，也能接上
    total = 0
    for page_img in pages:
        crops = await _crop_pipeline(ctx, page_img)
        if crops is None:
            out_lines.append(f"· {Path(page_img).name}：无可裁剪题块（已整页保留 {page_img}）")
            prev_tail = page_img
            continue
        crops, merge_note = await _crosspage_merge(ctx, crops, prev_tail)
        prev_tail = crops[-1]
        issues, texts = await _review_crops(ctx, crops)
        text_of = dict(texts)
        tag = f"（{merge_note}）" if merge_note else ""
        out_lines.append(f"· {Path(page_img).name}：切出 {len(crops)} 块 {tag}".rstrip())
        for p in crops:
            out_lines.append(f"  - {p}")
            t = text_of.get(p)
            if t and t.strip():
                out_lines.append(f"    块内文字：{t}")
        if issues:
            out_lines.extend(f"  ⚠️ {i.lstrip('· ')}" for i in issues)
        total += len(crops)
    _save_prev_tail(ctx, prev_tail)
    out_lines.append(
        f"合计 {total} 块题图，已存入 qbench/。"
        "上面每块已附 OCR 文字——bank_add 录入时直接引用文字与图片路径，无需再逐图视觉识别。"
    )
    return "\n".join(out_lines)


async def merge_crops(ctx: ToolContext, kwargs: dict[str, Any]) -> str:
    """把多张题图按顺序垂直拼接为一张完整题图（跨页题缝合）。参数：paths（路径列表，按上下顺序）、out（可选输出路径）。"""
    raw_paths = kwargs.get("paths") or []
    if isinstance(raw_paths, str):
        raw_paths = [p.strip() for p in raw_paths.split(",") if p.strip()]
    if len(raw_paths) < 2:
        return "请提供至少两张图片路径（paths 列表，按上下顺序）。"
    resolved: list[str] = []
    for p in raw_paths:
        p = str(p).strip()
        if not p.startswith(("http://", "https://")):
            p = str(ctx.sandbox.resolve_path(p, for_write=False))
        resolved.append(p)
    out_kw = kwargs.get("out")
    out = (
        str(ctx.sandbox.resolve_path(str(out_kw), for_write=True))
        if out_kw
        else _unique_path(ctx.student_dir("qbench"), "merged.png")
    )
    try:
        result = stitch_crops(resolved, out)
    except Exception as exc:
        return f"拼接失败：{exc}（请确认所有图片路径都存在）"
    return f"已把 {len(resolved)} 张题图按顺序拼接为一张完整题图：{result}"


async def _crop_via_numbers(ctx: ToolContext, path: str) -> list[str]:
    """题号锚定切块（含图）。配置了 layout 才走；失败返回空列表。"""
    spec = ctx.config.models.layout if ctx.config.models else None
    if spec is None:
        return []
    out_dir = ctx.student_dir("qbench")
    try:
        return await cut_by_question_numbers(path, str(out_dir), spec)
    except Exception as exc:
        log_event(log, "number split failed", level="warning", error=repr(exc))
        return []


async def _crop_via_ink(ctx: ToolContext, path: str) -> list[str]:
    """纯图像墨迹切块：切题目区域（含图）。失败返回空列表。"""
    out_dir = ctx.student_dir("qbench")
    try:
        crops = await __import__("asyncio").to_thread(cut_blocks_by_ink, path, str(out_dir))
        return crops if crops else []
    except Exception as exc:
        log_event(log, "ink split failed", level="warning", error=repr(exc))
        return []


async def _crop_via_papercut(ctx: ToolContext, path: str) -> list[str]:
    """百度专用切题 API（配置了才走），失败返回空列表走回退。"""
    spec = ctx.config.models.layout if ctx.config.models else None
    if spec is None or not spec.api_key and not (spec.model_dump().get("client_secret")):
        return []
    out_dir = ctx.student_dir("qbench")
    try:
        return await question_split_cut(spec, path, str(out_dir))
    except Exception as exc:
        log_event(log, "papercut split failed", level="warning", error=repr(exc))
        return []


async def _crop_via_layout(ctx: ToolContext, path: str) -> list[str]:
    """用版面分析（配置了才走）把页切成题图；无配置/失败返回空列表。"""
    spec = ctx.config.models.layout if ctx.config.models else None
    if spec is None:
        return []
    provider = None
    try:
        provider = build_layout_provider(spec)
        lines = await provider.layout(path)
    except Exception as exc:
        log_event(log, "layout failed", level="warning", error=repr(exc))
        return []
    finally:
        if provider is not None:
            with contextlib.suppress(Exception):
                await provider.aclose()
    if not lines:
        return []
    groups = group_lines_into_questions(lines)
    out_dir = ctx.student_dir("qbench")
    paths = crop_questions(path, groups, str(out_dir))
    # 把题面存到 ctx.state 供 agent 引用
    ctx.state["split_texts"] = layout_text(groups)
    return paths


async def _transcribe_only(ctx: ToolContext, path: str) -> str:
    if ctx.config.models is None or ctx.config.models.vision is None:
        raise ToolError("未配置视觉模型，无法切题", fix="配置 models.vision")
    provider = build_vision_provider(resolve_vision_spec(ctx.config))
    try:
        raw = await provider.understand(path, _SPLIT_PROMPT, detail="high")
    except Exception as exc:
        raise ToolError(str(exc), fix="视觉模型调用失败") from exc
    finally:
        await provider.aclose()
    data = extract_json(raw)
    questions = data.get("questions") if isinstance(data, dict) else None
    if not questions:
        return "未识别出题目，直接按整图解题处理即可。"
    lines = [f"识别到 {len(questions)} 道题（仅转写，未切图）："]
    for i, q in enumerate(questions, 1):
        lines.append(f"第{i}题：{q}")
    lines.append("建议配置 layout（如百度 OCR）以把每题切成图片。")
    return "\n".join(lines)
