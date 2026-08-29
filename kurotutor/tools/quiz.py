"""个性化出题工具：出题（画像/知识点/变式）、函数绘图、答题判分闭环。

判分后的错题走 wrongbook.record_wrong_question（含复习排期），与拍照解题同一闭环。
进行中的题目存学生工作上下文（active_quiz），跨轮可答。
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from pathlib import Path
from typing import Any

from sqlmodel import select

from kurotutor.agent.context import ToolContext
from kurotutor.core.errors import ToolError
from kurotutor.services import quiz as quiz_svc
from kurotutor.services.llm import build_llm_provider
from kurotutor.services.plot import plot_functions
from kurotutor.services.vision import build_vision_provider, extract_json, resolve_vision_spec
from kurotutor.storage import KnowledgePoint, WorkingContext, session_scope
from kurotutor.tools.wrongbook import record_wrong_question

_QUIZ_KEY = "active_quiz"

_STAGE_MAP = {"primary": "小学", "junior": "初中", "senior": "高中", "university": "大学"}


async def find_real_questions_via_ctx(
    ctx: ToolContext,
    llm,
    *,
    topic: str,
    stage: str = "中学",
    difficulty: str = "medium",
    count: int = 3,
    source: str = "auto",
) -> tuple[list[dict[str, Any]], str]:
    """真题链（用户定序）：web 搜真题 → jszkk 免费题库 → 火花 K12 付费兜底。

    出题与入学诊断共用的找题入口；返回 (题目列表, 来源说明)，
    全空表示都没找到，由调用方决定回退策略（如智能生成）。
    ``source`` 语义与 quiz_generate 一致：web 只走①，bank 只走②③，auto 全走。
    """
    questions: list[dict[str, Any]] = []
    source_note = ""
    # ① web 搜真题：搜索 → 抓正文 → LLM 提取结构化原题（Tavily 优先，相关性过滤）
    if source in ("auto", "web") and topic:
        from kurotutor.tools.web import fetch_page_text, make_search_fn

        search_fn = make_search_fn(ctx, prefer_tavily=True)

        def fetch_fn(url: str, limit: int = 2500) -> str:
            return fetch_page_text(url, limit)

        questions, note = await quiz_svc.find_real_questions(
            llm, search_fn=search_fn, fetch_fn=fetch_fn, topic=topic,
            stage=stage, difficulty=difficulty, count=count,
        )
        if questions:
            return questions, "真题（网上找的）· " + note
    if not questions and source in ("auto", "bank") and topic:
        # ② 免费题库 API：jszkk 全能搜题（免鉴权；仅在 web 搜不到时调用）
        keyword = topic.split("/")[-1].strip() or topic
        found = await asyncio.to_thread(quiz_svc.search_jszkk, keyword, count)
        if found:
            return found, "免费题库（jszkk 全能搜题）"
        # ③ 付费题库 API：火花数据 K12（最后兜底，¥5/100 次——省着用，仅 web+jszkk 都无结果时调用）
        qbank_spec = ctx.config.models.qbank if ctx.config.models else None
        qbank_key = (qbank_spec.api_key or "").strip() if qbank_spec else ""
        if qbank_key:
            try:
                found = await asyncio.to_thread(
                    quiz_svc.search_huohua,
                    keyword,
                    count,
                    api_key=qbank_key,
                    subject=topic.split("/")[0].strip(),
                    stage=stage,
                )
            except Exception as exc:
                found = []
                source_note = f"火花题库调用失败（{str(exc)[:60]}），已跳过"
            if found:
                return found, "在线题库（火花数据 K12）"
    return [], source_note or "未找到合适真题"


def _save_active_quiz(ctx: ToolContext, quiz: list[dict[str, Any]]) -> None:
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
        data[_QUIZ_KEY] = quiz
        wc.current_problem = json.dumps(data, ensure_ascii=False)
        db.add(wc)


def _load_active_quiz(ctx: ToolContext) -> list[dict[str, Any]]:
    if ctx.student is None:
        return []
    with session_scope(ctx.engine) as db:
        wc = db.get(WorkingContext, ctx.student.id)
        if wc is None:
            return []
        try:
            data = json.loads(wc.current_problem or "{}")
        except Exception:
            return []
        quiz = data.get(_QUIZ_KEY) or []
        return quiz if isinstance(quiz, list) else []


def _weakest_points(ctx: ToolContext, subject: str | None = None, n: int = 2) -> list[str]:
    """读画像里掌握度最低、置信度足够的知识点（出题优先打薄弱点）。"""
    if ctx.student is None:
        return []
    with session_scope(ctx.engine) as db:
        stmt = (
            select(KnowledgePoint)
            .where(KnowledgePoint.student_id == ctx.student.id, KnowledgePoint.confidence >= 0.2)
            .order_by(KnowledgePoint.mastery.asc())
            .limit(n)
        )
        if subject:
            stmt = stmt.where(KnowledgePoint.subject == subject)
        rows = db.exec(stmt).all()
    return [f"{r.subject}/{r.chapter}/{r.name}".replace("//", "/") for r in rows if r.name]


def _auto_difficulty(ctx: ToolContext) -> tuple[str, str]:
    """按最薄弱点掌握度自动定难度。返回 (difficulty, 依据说明)。"""
    with session_scope(ctx.engine) as db:
        kp = (
            db.exec(
                select(KnowledgePoint)
                .where(KnowledgePoint.student_id == ctx.student.id, KnowledgePoint.confidence >= 0.2)
                .order_by(KnowledgePoint.mastery.asc())
                .limit(1)
            ).first()
            if ctx.student
            else None
        )
    if kp is None:
        return "medium", ""
    if kp.mastery < 0.35:
        return "easy", f"薄弱点「{kp.name}」掌握度 {kp.mastery:.0%}，先出基础题巩固"
    if kp.mastery < 0.6:
        return "medium", f"围绕薄弱点「{kp.name}」（掌握度 {kp.mastery:.0%}）出中等难度"
    return "medium", ""


async def quiz_generate(ctx: ToolContext, kwargs: dict[str, Any]) -> str:
    """个性化出题（真题优先）。

    参数：topic（主题，可选）、knowledge_point（知识点，可选，缺省按画像薄弱点）、count（1-10，默认 3）、
    difficulty（缺省按薄弱点掌握度自动定）、purpose、variants（变式母题）、
    source（auto=默认：先网上找真题，失败回退智能生成 / web=只找真题 / generate=只智能生成）。
    """
    if ctx.config.models is None or ctx.config.models.llm is None:
        raise ToolError("未配置文本模型，无法出题", fix="配置 models.llm")
    topic = str(kwargs.get("topic") or "").strip()
    knowledge_point = str(kwargs.get("knowledge_point") or "").strip()
    count = kwargs.get("count") or 3
    difficulty = str(kwargs.get("difficulty") or "").strip().lower()
    purpose = str(kwargs.get("purpose") or "巩固练习").strip()
    variants = [str(v) for v in (kwargs.get("variants") or []) if str(v).strip()]
    source = str(kwargs.get("source") or "auto").strip().lower()
    if source not in ("auto", "web", "bank", "generate"):
        return "source 只支持 auto（默认）/ web / bank（仅题库 API）/ generate。"

    difficulty_note = ""
    if not difficulty:
        difficulty, difficulty_note = _auto_difficulty(ctx)

    # 方向：优先画像薄弱点
    if not knowledge_point and not topic and not variants:
        weak = _weakest_points(ctx, n=2)
        if weak:
            knowledge_point = weak[0]
            difficulty_note = difficulty_note or f"按画像薄弱点选题：{'、'.join(weak)}"
    # 校本进度：登记了学校章节时，无明确主题则贴校情出题
    if not topic and not variants:
        from kurotutor.services.memory import get_school_progress

        prog = get_school_progress(ctx.engine, ctx.student.id) if ctx.student else None
        if prog and prog.chapter and (not knowledge_point or prog.chapter in knowledge_point):
            knowledge_point = knowledge_point or f"{prog.chapter}"
            school_note = f"贴合校本进度「{prog.chapter}」"
            if prog.exam_date:
                school_note += f"（临近考试：{prog.exam_date}）"
            difficulty_note = difficulty_note or school_note
    if not topic and not knowledge_point and not variants:
        return "请告诉我出题方向：topic（主题）或 knowledge_point（知识点），或提供 variants（变式母题）。"

    stage = _STAGE_MAP.get(getattr(ctx.student, "stage", "") or "", "中学")
    llm = build_llm_provider(ctx.config.models.llm)
    questions: list[dict[str, Any]] = []
    source_note = ""
    try:
        # ①②③ 真题链：web 搜真题 → jszkk 免费题库 → 火花 K12（共享助手，诊断同链）
        if source in ("auto", "web", "bank") and (topic or knowledge_point):
            questions, source_note = await find_real_questions_via_ctx(
                ctx, llm, topic=topic or knowledge_point, stage=stage,
                difficulty=difficulty, count=count, source=source,
            )

        # ④ 兜底/指定：LLM 智能生成
        if not questions and source != "web":
            if variants:
                purpose = purpose if "变式" in purpose else "变式训练"
            questions = await quiz_svc.generate_questions(
                llm, topic=topic, knowledge_point=knowledge_point, count=count,
                difficulty=difficulty, purpose=purpose, variants=variants, stage=stage,
            )
            source_note = "题库与搜索均无结果，已回退智能生成（贴合学生水平）"
        elif not questions:
            return "免费题库与网上搜索都没找到合适的题。可稍后重试，或改用 source=generate 智能生成。"
    finally:
        await llm.aclose()

    # 题图下载：题库返回的配图 URL → 下载落盘（题目完整下载：题干/答案/解析/配图都在工作区）
    if ctx.student is not None:
        img_dir = ctx.student_dir("qbank_images")
        for q in questions:
            url = str(q.get("image_url") or "").strip()
            if not url.startswith(("http://", "https://")):
                continue
            try:
                import hashlib as _hashlib
                from urllib.parse import urlparse as _urlparse

                import httpx as _httpx

                ext = (_urlparse(url).path.rsplit(".", 1)[-1] or "png").lower()[:4]
                suffix = ext if ext in ("png", "jpg", "jpeg") else "png"
                name = "q_" + _hashlib.md5(url.encode()).hexdigest()[:10] + "." + suffix
                dest = Path(img_dir) / name
                if not dest.exists():
                    r = _httpx.get(url, timeout=15, follow_redirects=True)
                    r.raise_for_status()
                    dest.write_bytes(r.content)
                q["image_path"] = str(dest)
            except Exception:
                continue  # 题图下载失败不影响题目本身

    # 题图校验：多模态模型核对「图与题」的对应关系——不匹配的先尝试归位到其他题，仍不行则剔除
    if ctx.config.models is not None and ctx.config.models.vision is not None:
        await _verify_quiz_images(ctx, questions)

    _save_active_quiz(ctx, questions)
    lines = [
        f"已准备 {len(questions)} 道题（{purpose} · {difficulty} · {source_note}）。"
        "把题目发给学生，**先不要透露答案**。"
    ]
    if difficulty_note:
        lines.insert(1, f"（难度依据：{difficulty_note}）")
    n_img = sum(1 for q in questions if q.get("image_path"))
    for i, q in enumerate(questions, 1):
        mark = "【真题】" if q.get("real") else ""
        lines.append(f"第{i}题{mark}：{q['text']}")
        if q.get("image_path"):
            lines.append(f"   题图：{q['image_path']}（已下载）")
        if q.get("source_url"):
            lines.append(f"   来源：{q['source_url']}")
    if n_img:
        lines.append(f"（{n_img} 张题图已下载到工作区）")
    lines.append(
        "学生作答后用 quiz_check 判分；答错会自动记入错题本并排复习。"
        "答案与解析在系统中，不要主动展示，学生要答案或连续答错时再给。"
        "若某题网页未给答案（answer 为空），判分时以你的专业判断为准。"
    )
    return "\n".join(lines)


async def plot_function(ctx: ToolContext, kwargs: dict[str, Any]) -> str:
    """画函数图像。参数：expressions（表达式列表或逗号分隔，如 x^2-2*x-3）、x_min、x_max、title。"""
    raw = kwargs.get("expressions") or []
    if isinstance(raw, str):
        raw = [e.strip() for e in raw.split(",") if e.strip()]
    if not raw:
        return "请提供要绘制的函数表达式（expressions，如 x^2-2*x-3）。"
    x_min = float(kwargs.get("x_min") or -10)
    x_max = float(kwargs.get("x_max") or 10)
    title = str(kwargs.get("title") or "").strip()
    out_dir = ctx.student_dir("plots")
    import time as _time

    out = str(out_dir / f"plot_{int(_time.time())}.png")
    try:
        path = plot_functions(out, [str(e) for e in raw], x_min=x_min, x_max=x_max, title=title)
    except ToolError as exc:
        return f"绘图失败：{exc}"
    return (
        f"函数图像已生成：{path}\n"
        f"包含 {len(raw)} 条曲线（{ '、'.join('y='+str(e) for e in raw)}），"
        f"x 范围 [{x_min}, {x_max}]，已画坐标轴与网格。可以把图发给学生并讲解。"
    )


async def quiz_check(ctx: ToolContext, kwargs: dict[str, Any]) -> str:
    """判分学生作答。

    参数：answers（学生答案；多题用 | 分隔，按出题顺序）、question_index（判第几题，缺省全部）。
    """
    if ctx.config.models is None or ctx.config.models.llm is None:
        raise ToolError("未配置文本模型，无法判分", fix="配置 models.llm")
    quiz = _load_active_quiz(ctx)
    if not quiz:
        return "当前没有进行中的出题。先用 quiz_generate 出题。"
    raw = str(kwargs.get("answers") or "").strip()
    if not raw:
        return "请提供学生的答案（answers）。多题用 | 分隔，按出题顺序。"
    idx_raw = kwargs.get("question_index")
    try:
        index = int(idx_raw) - 1 if idx_raw is not None else None
    except (TypeError, ValueError):
        index = None
    pairs = [a.strip() for a in raw.split("|") if a.strip()]
    if index is not None and 0 <= index < len(quiz):
        targets = [(index, quiz[index])]
    else:
        targets = list(enumerate(quiz))
    if index is not None and (index < 0 or index >= len(quiz)):
        return f"题号超出范围：当前共 {len(quiz)} 题。"

    llm = build_llm_provider(ctx.config.models.llm)
    lines = ["判分结果："]
    wrong: list[dict[str, Any]] = []
    try:
        for order, (qi, q) in enumerate(targets):
            student_answer = pairs[order] if order < len(pairs) else "（未作答）"
            try:
                verdict = await quiz_svc.check_answer(
                    llm, question=q["text"], answer=q["answer"], student_answer=student_answer
                )
            except Exception as exc:
                lines.append(f"· 第{qi + 1}题：判分失败（{exc}），请稍后重试")
                continue
            mark = "✅ 答对" if verdict["correct"] else "❌ 答错"
            lines.append(f"· 第{qi + 1}题：{mark}（{verdict['verdict']}）")
            if not verdict["correct"]:
                wrong.append({**q, "student_answer": student_answer})
    finally:
        await llm.aclose()

    # 错题闭环：记错题本 + 复习排期（与拍照解题同一入口）
    if ctx.student is not None:
        for q in wrong:
            record_wrong_question(
                ctx.engine,
                ctx.student.id,
                {
                    "question": q["text"],
                    "subject": (
                        (q.get("knowledge_point") or "/").split("/")[0]
                        if q.get("knowledge_point")
                        else "数学"
                    ),
                    "knowledge_point": q.get("knowledge_point") or "",
                    "student_answer": q.get("student_answer", ""),
                    "correct_answer": q.get("answer", ""),
                    "analysis": q.get("analysis", ""),
                    "error_type": "conceptual",
                    "source": "quiz",
                },
            )
        if wrong:
            lines.append(f"（{len(wrong)} 道错题已记入错题本并排了复习）")
    lines.append(
        "根据判分继续教学：对的肯定并加深一问；错的讲清错因后再出一道同考点变式（quiz_generate + variants）。"
    )
    return "\n".join(lines)

_VERIFY_IMG_PROMPT = (
    "下面是一道数学题的题干，以及一张图片。请判断：这张图片是否是这道题的配图（公式渲染图、几何图形、"
    "函数图像、统计图表等都算配图；与题目内容完全无关的图才算不匹配）。\n"
    "题干：{question}\n"
    '只输出 JSON：{{"match": true}} 或 {{"match": false}}，不要其他文字。'
)


async def _verify_quiz_images(ctx: ToolContext, questions: list[dict[str, Any]]) -> None:
    """多模态模型核对题图归属：不匹配 → 尝试归位到其他题 → 仍不匹配则剔除图片。

    视觉调用有上限（8 次）；校验调用失败时保留原图（宁滥勿缺，题目本身不受影响）。
    """
    vision = build_vision_provider(resolve_vision_spec(ctx.config))
    checked = 0
    try:
        for q in questions:
            img = q.get("image_path")
            if not img or not Path(img).exists() or checked >= 8:
                continue
            checked += 1
            try:
                raw = await vision.understand(img, _VERIFY_IMG_PROMPT.format(question=q["text"][:300]))
                data = extract_json(raw)
                matched = bool(data.get("match")) if isinstance(data, dict) else True
            except Exception:
                matched = True  # 校验失败按匹配处理，不阻塞出题
            if matched:
                continue
            # 不匹配 → 尝试归位：与其他缺图题的题干逐个比对
            relocated = False
            for other in questions:
                if other is q or other.get("image_path"):
                    continue
                try:
                    raw = await vision.understand(
                        img, _VERIFY_IMG_PROMPT.format(question=other["text"][:300])
                    )
                    data = extract_json(raw)
                except Exception:
                    break
                if isinstance(data, dict) and data.get("match"):
                    other["image_path"] = img
                    q["image_path"] = ""  # 图已归位到其他题
                    relocated = True
                    break
            if not relocated:
                with contextlib.suppress(Exception):
                    Path(img).unlink(missing_ok=True)
                q["image_path"] = ""
                q["image_dropped"] = "题图与题目不匹配，已剔除"
    finally:
        await vision.aclose()
