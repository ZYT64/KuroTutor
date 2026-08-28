"""作业批改工具（product 规格 4.1 批改）。

用视觉模型读取作业图片（含学生手写作答），逐题判分并归类：
- 每题给出 题目/学生作答/正确答案/是否对/错因/知识点；
- 迭代错题 → 自动记入错题本（去重）并更新画像掌握度；
- 返回逐题批改报告。

视觉 Provider 完全由配置 `models.vision` 决定（可插拔）。JSON 解析失败优雅降级。
"""

from __future__ import annotations

from typing import Any

from kurotutor.agent.context import ToolContext
from kurotutor.core.errors import ToolError
from kurotutor.services.profile import ProfileService
from kurotutor.services.vision import build_vision_provider, extract_json
from kurotutor.tools.wrongbook import record_wrong_question

_GRADE_PROMPT = (
    "你是一位严格的老师，正在批改学生发来的一张作业图片（上面有题目和学生的作答）。"
    "请逐题批改，只输出一个 JSON 对象，不要输出其他文字：\n"
    '{"items":[{"question":"题目转写（含题号）",'
    '"student_answer":"学生作答","correct_answer":"正确答案",'
    '"is_correct":true或false,"error_type":"careless/conceptual/unknown",'
    '"knowledge_point":"知识点，如 数学/函数/二次函数"}],'
    '"summary":"整体点评（一两句）"}'
    "\n看不清就真相写，不要编造；每道题都要判对错。"
)


def _build_vision(ctx: ToolContext):
    if ctx.config.models is None or ctx.config.models.vision is None:
        raise ToolError("未配置视觉模型，无法批改作业", cause="models.vision 缺失", fix="配置 models.vision")
    try:
        return build_vision_provider(ctx.config.models.vision)
    except Exception as exc:
        raise ToolError(str(exc), fix="检查 models.vision 配置") from exc


async def grade_homework(ctx: ToolContext, kwargs: dict[str, Any]) -> str:
    """批改作业图。参数：path（图片路径）。"""
    path = (kwargs.get("path") or "").strip()
    if not path:
        return "请提供作业图片路径（path）。"
    if not path.startswith(("http://", "https://")):
        path = str(ctx.sandbox.resolve_path(path, for_write=False))

    provider = _build_vision(ctx)
    try:
        raw = await provider.understand(path, _GRADE_PROMPT, detail="high")
    except Exception as exc:
        raise ToolError(str(exc), fix="视觉模型调用失败，请稍后重试或检查配置") from exc
    finally:
        await provider.aclose()

    data = extract_json(raw)
    items = data.get("items") if isinstance(data, dict) else None
    if not isinstance(items, list):
        return raw or "未能解析出批改结果，请换一张更清晰的作业图。"
    if not items:
        return "作业图里没识别到题目。"

    correct = 0
    wrongs: list[str] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        q = _s(item.get("question"))
        if not q:
            continue
        is_correct = item.get("is_correct") is True or str(item.get("is_correct")).lower() in (
            "true",
            "1",
            "对",
            "是",
        )
        if is_correct:
            correct += 1
        else:
            wrongs.append(item)
            _record_wrong(ctx, item)

    lines = [f"共批改 {len(items)} 题，答对 {correct} 题，答错 {len(wrongs)} 题。"]
    if data.get("summary"):
        lines.append(f"点评：{data['summary']}")
    for item in wrongs:
        label = (
            "概念不清"
            if item.get("error_type") == "conceptual"
            else ("粗心" if item.get("error_type") == "careless" else "待确认")
        )
        lines.append(
            f"- ❌ {_s(item.get('question'))}｜学生：{_s(item.get('student_answer'))}；"
            f"正解：{_s(item.get('correct_answer'))}｜{label}"
        )
    if wrongs:
        lines.append("错题已记录到错题本，我会安排复习。")
    else:
        lines.append("全对，很稳！")
    return "\n".join(lines)


def _record_wrong(ctx: ToolContext, item: dict[str, Any]) -> None:
    """把一道错题写入错题本（去重），并更新画像掌握度。"""
    subject, kp_name = _split_kp(_s(item.get("knowledge_point")))
    is_correct = False
    ProfileService(ctx.engine).update_after_answer(
        student_id=ctx.student.id,
        subject=subject,
        chapter="",
        name=kp_name,
        is_correct=is_correct,
        error_type="conceptual"
        if item.get("error_type") == "conceptual"
        else ("careless" if item.get("error_type") == "careless" else "unknown"),
    )
    record_wrong_question(
        ctx.engine,
        ctx.student.id,
        {
            "subject": subject,
            "knowledge_point": f"{subject}/{kp_name}",
            "question": _s(item.get("question")),
            "student_answer": _s(item.get("student_answer")),
            "correct_answer": _s(item.get("correct_answer")),
            "error_type": "概念不清"
            if item.get("error_type") == "conceptual"
            else ("计算错误" if item.get("error_type") == "careless" else "unknown"),
            "source": "homework",
        },
    )


def _split_kp(raw: str) -> tuple[str, str]:
    parts = [p for p in raw.split("/") if p]
    if len(parts) >= 2:
        return parts[0], "/".join(parts[1:])
    if len(parts) == 1:
        return parts[0], "综合"
    return "综合", "未分类"


def _s(v: Any) -> str:
    return str(v).strip() if v is not None else ""
