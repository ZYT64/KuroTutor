"""拍照解题工具（确定性闭环版）。

产品规格书 4.1「拍照解题」：视觉读题 → 引导式讲解 → 按画像决定错题询问。

分工：
- 视觉模型负责【准确读题 + 判分 + 结构化方法】，按要求输出一段 JSON；
- 本工具【确定性】地：折算画像掌握度、沉淀方法卡片（kb_deposit，去重）、
  按错题策略（skip/ask/record）记录或询问；
- 文本 Agent 拿到结构化结果后，负责任的教学打磨（先思路后答案、学段语气）。

这样「沉淀方法卡 / 记错题」不再依赖模型心情，而是真实发生并落库。
JSON 解析失败会优雅降级（只返回读取结果，不中断会话，不误写数据）。
"""

from __future__ import annotations

import json
from typing import Any

from sqlmodel import delete, select

from kurotutor.agent.context import ToolContext
from kurotutor.core.errors import ToolError
from kurotutor.services.profile import ProfileService, WrongbookPolicy
from kurotutor.services.vision import build_vision_provider, extract_json
from kurotutor.storage import PendingRecord, WorkingContext, session_scope
from kurotutor.tools import kb, wrongbook

# 让视觉模型只输出一个 JSON 对象，字段可控、可解析
_SOLVE_PROMPT = (
    "你是一位严格的数学老师，正在看学生发来的一张题目图片。"
    "请读取图片里的题目（含所有文字、小问、配图信息），并判断学生的作答是否正确。"
    "图片中如有手写字迹或涂改：那是学生的作答/演算，当作 student_entry_answer 的参考，"
    "不要混入题目本体（question_text 只保留印刷的题目内容）。"
    "图片可能有倾斜或缺边：结合上下文把被裁断的字补全理解；确实不完整就把相应字段留空并在心里提醒自己让学生补拍。"
    "若图片看不清或不是题目，请如实把相应字段留空，不要编造。\n"
    "只输出下面这一个 JSON 对象，不要输出任何其他文字或解释：\n"
    '{"subject":"学科","question_type":"题型，如『一元二次方程求根』",'
    '"question_text":"题目完整转写","correct_answer":"最终正确答案",'
    '"method":"解题方法总述","steps":"分步推导（用换行分隔）",'
    '"pitfalls":"易错点（用换行分隔）",'
    '"problem_count":1,'
    '"difficulty":"easy 或 medium 或 hard",'
    '"error_category":"careless 或 conceptual 或 unknown",'
    '"student_entry_answer":"学生的作答（我提供的那个，若我未提供则为空字符串）",'
    '"student_correct":true 或 false 或 null}'
    "\nstudent_correct 判断规则：我提供了学生作答时才给 true/false，否则一律 null；"
    "作答若是部分正确也要判 false。"
    "problem_count：图片里题目数量，一页多题时如实填。"
    "difficulty：按题目本身难度判断。"
    "error_category：学生若答错，判是『粗心算错』(careless，如符号/计算) 还是『概念不清』"
    "(conceptual，如方法不会/原理不懂)；无法判断给 unknown。"
)


def _vision_from_tool_error(ctx: ToolContext):
    if ctx.config.models is None or ctx.config.models.vision is None:
        raise ToolError(
            "未配置视觉模型，无法处理图片",
            cause="models.vision 缺失",
            fix="在 kuro.json 中配置 models.vision（如 deepseek-v4-flash-vision-exp）",
        )
    try:
        return build_vision_provider(ctx.config.models.vision)
    except Exception as exc:
        raise ToolError(str(exc), fix="检查 models.vision 的 provider/model/api_key 配置") from exc


def _parse_solution(text: str) -> dict[str, Any]:
    """从视觉输出里提取 JSON 对象；失败返回空 dict。"""
    return extract_json(text)


def _text(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def _bool_or_none(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if value in (None, "", "null"):
        return None
    return str(value).lower() in ("true", "1", "是")


async def solve_photo(ctx: ToolContext, kwargs: dict[str, Any]) -> str:
    """拍照解题：视觉读题判分 → 画像/沉淀/错题闭环 → 返回结构化讲解。"""
    path = _text(kwargs.get("path") or kwargs.get("image_path"))
    if not path:
        return "请提供图片路径（path）。"
    if not path.startswith(("http://", "https://")):
        path = str(ctx.sandbox.resolve_path(path, for_write=False))
    student_entry = _text(kwargs.get("student_answer"))

    provider = _vision_from_tool_error(ctx)
    prompt = _SOLVE_PROMPT + (f"\n学生作答：{student_entry}" if student_entry else "")
    try:
        raw = await provider.understand(path, prompt, detail="high")
    except Exception as exc:
        raise ToolError(str(exc), fix="视觉模型调用失败，请稍后重试或检查配置") from exc
    finally:
        await provider.aclose()

    sol = _parse_solution(raw)
    if not sol:
        # 优雅降级：不解析出结构化数据，就直接把原始识别结果返回（不误写库）
        return raw or "视觉模型未能读出图片内容，请换一张更清晰的图片重试。"

    subject = _text(sol.get("subject")) or "综合"
    question_type = _text(sol.get("question_type")) or "未分类"
    question_text = _text(sol.get("question_text"))
    answer = _text(sol.get("correct_answer"))
    method = _text(sol.get("method"))
    steps = _text(sol.get("steps"))
    pitfalls = _text(sol.get("pitfalls"))
    is_correct = _bool_or_none(sol.get("student_correct"))
    difficulty = (_text(sol.get("difficulty")) or "medium").lower()
    error_category = (_text(sol.get("error_category")) or "unknown").lower()
    try:
        problem_count = int(sol.get("problem_count") or 1)
    except (ValueError, TypeError):
        problem_count = 1

    # 知识点解析 + 画像更新
    kp_subject, kp_chapter, kp_name = _split_kp(subject, question_type)
    profile = ProfileService(ctx.engine)
    mastery = profile.update_after_answer(
        student_id=ctx.student.id,
        subject=kp_subject,
        chapter=kp_chapter,
        name=kp_name,
        is_correct=is_correct,
        error_type="conceptual"
        if error_category == "conceptual"
        else ("careless" if error_category == "careless" else "unknown"),
    )
    # 少打扰护栏：已有一条待确认错题时，不再重复询问（但仍保留直接记录）
    policy = profile.wrongbook_policy(
        student_id=ctx.student.id,
        subject=kp_subject,
        chapter=kp_chapter,
        name=kp_name,
        is_correct=is_correct,
        difficulty=difficulty,
        error_category=error_category,
    )
    if policy == WrongbookPolicy.ASK and _has_pending(ctx.engine, ctx.student.id):
        policy = WrongbookPolicy.SKIP

    # 沉淀方法卡片（去重，越用越强）
    card_note = ""
    if method:
        await kb.deposit_card(
            ctx,
            {
                "subject": kp_subject,
                "question_type": kp_name,
                "method": method,
                "steps": steps,
                "pitfalls": pitfalls,
                "source": f"拍照解题：{question_text[:40]}",
            },
        )
        card_note = "已沉淀方法卡片。"

    # 错题闭环
    wrong_note = ""
    record_error_type = (
        "概念不清"
        if error_category == "conceptual"
        else ("计算错误" if error_category == "careless" else "unknown")
    )
    if policy == WrongbookPolicy.RECORD and question_text:
        wrongbook.record_wrong_question(
            ctx.engine,
            ctx.student.id,
            {
                "subject": kp_subject,
                "knowledge_point": f"{kp_subject}/{kp_chapter}/{kp_name}".strip("/"),
                "question": question_text,
                "correct_answer": answer,
                "analysis": method,
                "error_type": record_error_type,
                "image_path": path,
                "source": "photo",
            },
        )
        wrong_note = "已记入错题本（对应薄弱/连续错的点）。"
    elif policy == WrongbookPolicy.ASK and question_text:
        _upsert_pending(
            ctx.engine,
            ctx.student.id,
            {
                "subject": kp_subject,
                "knowledge_point": f"{kp_subject}/{kp_chapter}/{kp_name}".strip("/"),
                "question": question_text,
                "correct_answer": answer,
                "analysis": method,
                "error_type": record_error_type,
                "image_path": path,
                "source": "photo",
            },
        )
        wrong_note = "【询问】这道题你卡在思路上，要不要记进错题本以后巩固？回『要』我就帮你记。"

    # 写入跨轮工作上下文，供后续轮次引用
    _write_working_context(
        ctx.engine,
        ctx.student.id,
        {
            "subject": kp_subject,
            "kp_name": kp_name,
            "question_text": question_text,
            "correct_answer": answer,
            "method": method,
            "steps": steps,
            "pitfalls": pitfalls,
        },
    )

    state = {
        "question_text": question_text,
        "correct_answer": answer,
        "method": method,
        "steps": steps,
        "pitfalls": pitfalls,
        "is_correct": is_correct,
        "policy": policy.value,
        "mastery": mastery,
    }
    ctx.state["solve_photo"] = state

    lines = [
        f"【读取结果】题目：{question_text or '（未读出）'}",
    ]
    if problem_count and problem_count > 1:
        lines.append(f"【图片】这一页检测到 {problem_count} 道题，我们一道一道来，先讲第一道。")
    lines.append(f"【答案】{answer or '（未给出）'}")
    if method:
        lines.append(f"【方法卡片】{kp_subject}·{kp_name}：{method}")
    if card_note:
        lines.append(f"【沉淀】{card_note}")
    if wrong_note:
        lines.append(f"【错题】{wrong_note}")
    return "\n".join(lines)


def _split_kp(subject: str, question_type: str) -> tuple[str, str, str]:
    """把 (学科, 题型) 归一到 (学科, ''/章节, 知识点名)。"""
    qtype = question_type.strip() or "综合"
    return subject, "", qtype


def _upsert_pending(engine: Any, student_id: int, payload: dict[str, Any]) -> None:
    """把待确认错题写入 PendingRecord（每个学生只保留最新一条）。"""
    with session_scope(engine) as db:
        db.exec(delete(PendingRecord).where(PendingRecord.student_id == student_id))
        db.add(PendingRecord(student_id=student_id, payload=json.dumps(payload, ensure_ascii=False)))


def _has_pending(engine: Any, student_id: int) -> bool:
    """该学生是否已有待确认错题（用于「最多一问」护栏）。"""
    with session_scope(engine) as db:
        return (
            db.exec(select(PendingRecord.id).where(PendingRecord.student_id == student_id).limit(1)).first()
            is not None
        )


def _write_working_context(engine: Any, student_id: int, current_problem: dict[str, Any]) -> None:
    """把最近讲解的题目写入跨轮工作上下文（合并写入，保留其他键——如切题尾块 last_split_tail）。"""
    with session_scope(engine) as db:
        wc = db.get(WorkingContext, student_id)
        if wc is None:
            wc = WorkingContext(student_id=student_id)
            db.add(wc)
        try:
            data = json.loads(wc.current_problem or "{}")
        except Exception:
            data = {}
        data.update(current_problem)
        wc.current_problem = json.dumps(data, ensure_ascii=False)
        db.add(wc)


async def image_understand(ctx: ToolContext, kwargs: dict[str, Any]) -> str:
    """通用看图理解：按自定义 prompt 描述图片（供笔记解析 / 批改等使用）。"""
    path = _text(kwargs.get("path"))
    prompt = _text(kwargs.get("prompt"))
    if not path:
        return "请提供图片路径（path）。"
    if not prompt:
        prompt = "请描述这张图片的内容。"
    if not path.startswith(("http://", "https://")):
        path = str(ctx.sandbox.resolve_path(path, for_write=False))

    provider = _vision_from_tool_error(ctx)
    try:
        result = await provider.understand(path, prompt, detail="high")
    finally:
        await provider.aclose()
    return result or "视觉模型未能返回内容。"
