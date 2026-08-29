"""入学诊断服务：新学生水平摸底 → 画像基线。

流程：diagnostic_start（按学段/学科生成分级诊断题，由易到难）→ 学生作答 →
diagnostic_submit（逐题判分 → 定位知识边界 → 掌握度写入画像（高置信）→ 学情报告）。
"""

from __future__ import annotations

import json
from typing import Any

from kurotutor.core.errors import ProviderError
from kurotutor.services.llm import ChatMessage
from kurotutor.services.vision import extract_json
from kurotutor.storage import WorkingContext, session_scope

_KEY = "active_diagnostic"

_GEN_PROMPT = (
    "你是{stage}{subject}老师，要给一位新学生做**入学诊断**（摸底）。请出 {count} 道**由易到难**的诊断题：\n"
    "1. 覆盖「{subject}」本学段的核心基础考点，从简单计算/概念到综合应用逐级递进；\n"
    "2. 题目要能区分『基础扎实 / 一般 / 薄弱』三档水平；\n"
    "3. 每道题给：text（题目全文）、answer（标准答案）、analysis（解析）、"
    "knowledge_point（『学科/章节/名称』）、difficulty（easy/medium/hard，按顺序递进）；\n"
    "只输出 JSON：{{\"questions\":[{{...}}]}}，不要其他文字。"
)

_VERDICT_PROMPT = (
    "你是严谨的阅卷老师。诊断题：{question}\n参考答案：{answer}\n学生作答：{student}\n"
    "请判分。只输出 JSON：{{\"correct\": true 或 false, \"verdict\": \"一句话判定\"}}"
)

_REPORT_PROMPT = (
    "你是{stage}{subject}老师，刚给新学生{nickname}做完入学诊断（{n} 题，答对 {ok} 道）。\n"
    "逐题结果：{detail}\n"
    "请写诊断结论：1) 总体水平评价（基础/中等/优秀 一档）；2) 已掌握的考点；3) 薄弱考点与"
    "建议起点；4) 一句鼓励。200 字内，直接输出正文。"
)


def _save_active(ctx: Any, diag: dict[str, Any]) -> None:
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
        data[_KEY] = diag
        wc.current_problem = json.dumps(data, ensure_ascii=False)
        db.add(wc)


def _load_active(ctx: Any) -> dict[str, Any] | None:
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
        diag = data.get(_KEY)
        return diag if isinstance(diag, dict) and diag.get("questions") else None


def _clear_active(ctx: Any) -> None:
    if ctx.student is None:
        return
    with session_scope(ctx.engine) as db:
        wc = db.get(WorkingContext, ctx.student.id)
        if wc is None:
            return
        try:
            data = json.loads(wc.current_problem or "{}")
        except Exception:
            return
        data.pop(_KEY, None)
        wc.current_problem = json.dumps(data, ensure_ascii=False)
        db.add(wc)


async def start_diagnostic(llm, *, subject: str, stage: str, count: int = 4) -> list[dict[str, Any]]:
    """生成分级诊断题（由易到难）。"""
    count = max(3, min(int(count), 6))
    prompt = _GEN_PROMPT.format(stage=stage, subject=subject, count=count)
    r = await llm.complete([ChatMessage(role="user", content=prompt)], temperature=0.5)
    data = extract_json(r.content or "")
    questions = (data or {}).get("questions") if isinstance(data, dict) else None
    if not questions:
        raise ProviderError("诊断题生成失败", cause=(r.content or "")[:120], fix="稍后重试")
    cleaned = []
    for q in questions[:count]:
        if isinstance(q, dict) and (q.get("text") or "").strip():
            cleaned.append(
                {
                    "text": str(q.get("text") or "").strip(),
                    "answer": str(q.get("answer") or "").strip(),
                    "analysis": str(q.get("analysis") or "").strip(),
                    "knowledge_point": str(q.get("knowledge_point") or f"{subject}/综合").strip(),
                    "difficulty": str(q.get("difficulty") or "medium").strip(),
                }
            )
    if not cleaned:
        raise ProviderError("诊断题生成失败：题目为空", fix="稍后重试")
    return cleaned


async def grade_answers(llm, *, questions: list[dict], answers: list[str]) -> list[dict[str, Any]]:
    """逐题判分。返回 [{index, correct, verdict}]。"""
    out = []
    for i, q in enumerate(questions):
        stu = answers[i] if i < len(answers) else "（未作答）"
        try:
            prompt = _VERDICT_PROMPT.format(
                question=q["text"][:400], answer=q["answer"][:200], student=stu[:400]
            )
            r = await llm.complete([ChatMessage(role="user", content=prompt)], temperature=0.1)
            data = extract_json(r.content or "")
            correct = bool(data.get("correct")) if isinstance(data, dict) else False
            verdict = str(data.get("verdict") or "").strip() if isinstance(data, dict) else ""
        except Exception:
            correct, verdict = False, "判分失败"
        out.append({"index": i, "correct": correct, "verdict": verdict})
    return out


def level_of(ok: int, total: int) -> str:
    """按正确率分层。"""
    if total == 0:
        return "medium"
    ratio = ok / total
    if ratio >= 0.75:
        return "优秀"
    if ratio >= 0.4:
        return "中等"
    return "基础薄弱"


def report_text(*, nickname: str, subject: str, ok: int, total: int, detail_lines: list[str]) -> str:
    """诊断结论文本（LLM 之外的确定性底稿）。"""
    lvl = level_of(ok, total)
    lines = [f"📋 入学诊断完成（{subject}）：答对 {ok}/{total}，总体水平：{lvl}", ""]
    lines += detail_lines
    return "\n".join(lines)
