"""入学诊断工具：摸底出题 → 判分 → 画像基线 → 学情报告。"""

from __future__ import annotations

from typing import Any

from kurotutor.agent.context import ToolContext
from kurotutor.core import get_logger, log_event
from kurotutor.core.errors import ToolError
from kurotutor.services import diagnostic
from kurotutor.services.llm import build_llm_provider
from kurotutor.services.profile import ProfileService

log = get_logger("diagnostic")

_STAGE_MAP = {"primary": "小学", "junior": "初中", "senior": "高中", "university": "大学"}


async def diagnostic_start(ctx: ToolContext, kwargs: dict[str, Any]) -> str:
    """开始入学诊断。参数：subject（默认数学）、count（3-6，默认 4）。"""
    if ctx.config.models is None or ctx.config.models.llm is None:
        raise ToolError("未配置文本模型，无法诊断", fix="配置 models.llm")
    if ctx.student is None:
        return "当前没有学生上下文。"
    subject = str(kwargs.get("subject") or "数学").strip()
    count = kwargs.get("count") or 4
    stage = _STAGE_MAP.get(getattr(ctx.student, "stage", "") or "", "初中")

    llm = build_llm_provider(ctx.config.models.llm)
    try:
        # 优先真题链（web→jszkk→火花，与出题同链；用户定序）：诊断也是出新题
        questions: list[dict[str, Any]] = []
        source_note = ""
        try:
            from kurotutor.tools.quiz import find_real_questions_via_ctx

            found, source_note = await find_real_questions_via_ctx(
                ctx, llm, topic=f"{stage}{subject}摸底", stage=stage, count=count,
            )
            questions = diagnostic.adapt_real_questions(found, subject=subject, count=count)
        except Exception as exc:  # 真题链任何故障都不阻塞诊断（回退生成）
            log_event(log, f"诊断真题链失败，回退生成：{exc}", level="warning")
            questions = []
            source_note = ""
        if questions and len(questions) < int(count):
            # 真题不够：AI 生成补足到 count（真题优先、生成兜底，混排后按难度递进）
            missing = int(count) - len(questions)
            try:
                extra = await diagnostic.start_diagnostic(llm, subject=subject, stage=stage, count=missing)
            except Exception as exc:
                log_event(log, f"诊断补题生成失败（已有 {len(questions)} 道真题）：{exc}", level="warning")
                extra = []
            questions = diagnostic.sort_by_difficulty(questions + extra)
        if not questions:
            questions = await diagnostic.start_diagnostic(llm, subject=subject, stage=stage, count=count)
    finally:
        await llm.aclose()

    diagnostic._save_active(
        ctx,
        {"subject": subject, "stage": stage, "questions": questions},
    )
    real_head = f"题目来源：{source_note}。" if questions and questions[0].get("real") else ""
    lines = [
        f"📖 入学诊断开始（{subject} · {len(questions)} 题，由易到难）。{real_head}"
        "别有压力，这是为了摸清你的起点，好给你定制学习计划。把每题答案发给我（可以一起发）。"
    ]
    for i, q in enumerate(questions, 1):
        lines.append(f"第{i}题（{q['difficulty']}）：{q['text']}")
    lines.append("答完我来判分并给你一份学情报告。")
    return "\n".join(lines)


async def diagnostic_submit(ctx: ToolContext, kwargs: dict[str, Any]) -> str:
    """提交诊断答案：判分 → 画像基线 → 诊断报告。参数：answers（多题用 | 分隔）。"""
    if ctx.config.models is None or ctx.config.models.llm is None:
        raise ToolError("未配置文本模型，无法判分", fix="配置 models.llm")
    diag = diagnostic._load_active(ctx)
    if not diag:
        return "当前没有进行中的诊断。对我说『测测我的水平』开始入学诊断。"
    raw = str(kwargs.get("answers") or "").strip()
    if not raw:
        return "请提供你的答案（answers），多题用 | 分隔。"
    questions = diag["questions"]
    answers = [a.strip() for a in raw.split("|") if a.strip()]

    llm = build_llm_provider(ctx.config.models.llm)
    try:
        results = await diagnostic.grade_answers(llm, questions=questions, answers=answers)
    finally:
        await llm.aclose()

    # 画像基线：诊断结果以高置信写入掌握度（诊断可信度高）
    if ctx.student is not None:
        profile = ProfileService(ctx.engine)
        for r in results:
            q = questions[r["index"]]
            kp_raw = q.get("knowledge_point") or "数学/综合"
            parts = kp_raw.split("/")
            subject = parts[0] if parts[0] else diag["subject"]
            chapter = parts[1] if len(parts) > 1 else "综合"
            name = parts[2] if len(parts) > 2 else (parts[1] if len(parts) > 1 else "综合")
            profile.update_after_answer(
                student_id=ctx.student.id,
                subject=subject,
                chapter=chapter,
                name=name,
                is_correct=r["correct"],
                error_type="conceptual" if not r["correct"] else "unknown",
            )

    ok = sum(1 for r in results if r["correct"])
    detail_lines = []
    for r in results:
        q = questions[r["index"]]
        mark = "✅" if r["correct"] else "❌"
        detail_lines.append(f"{mark} 第{r['index'] + 1}题（{q['difficulty']}）：{r['verdict']}")
    text = diagnostic.report_text(
        nickname=(ctx.student.nickname if ctx.student else "") or "同学",
        subject=diag["subject"],
        ok=ok,
        total=len(results),
        detail_lines=detail_lines,
    )
    diagnostic._clear_active(ctx)
    lines = [text, "", "你的画像已经建好，之后的出题、课程都会按这个起点定制。"]
    if ok < len(results):
        lines.append("建议先从薄弱考点开始：对我说『从xx开始教我』或直接『给我出几道题练练』。")
    return "\n".join(lines)
