"""个性化出题服务：按画像/知识点/题集出题，LLM 结构化生成，答题判分闭环。

分工：本服务只负责「生成题目 + 判分」（结构化 JSON），教学呈现由 Agent 完成；
判分后的错题走 wrongbook.record_wrong_question（含复习排期），与拍照解题同一闭环。
"""

from __future__ import annotations

import asyncio
from typing import Any

from kurotutor.core.errors import ProviderError
from kurotutor.services.llm import ChatMessage
from kurotutor.services.vision import extract_json

_GEN_PROMPT = (
    "你是一位经验丰富的{stage}老师，正在给学生出{purpose}。要求：\n"
    "1. 题目紧扣知识点：{focus}；\n"
    "2. 难度：{difficulty}；数量：{count} 道；\n"
    "{variant_line}"
    "3. 每道题给出：text（题目全文，含小问）、answer（标准答案）、"
    "analysis（解法要点，2-3 句）、knowledge_point（『学科/章节/名称』）、difficulty（easy/medium/hard）；\n"
    "4. 题目表述清晰自洽、答案确定无歧义；计算题给具体数值，不给出没法验算的开放题。\n"
    "只输出 JSON：{{\"questions\":[{{...}}]}}，不要其他文字。"
)


async def generate_questions(
    llm,  # LLM Provider（complete 接口）
    *,
    topic: str = "",
    knowledge_point: str = "",
    count: int = 3,
    difficulty: str = "medium",
    purpose: str = "巩固练习",
    variants: list[str] | None = None,
    stage: str = "中学",
) -> list[dict[str, Any]]:
    """生成题目列表。返回 [{"text","answer","analysis","knowledge_point","difficulty"}]。"""
    count = max(1, min(int(count), 10))
    focus = knowledge_point or topic or "学生近期薄弱点综合"
    variant_line = ""
    if variants:
        joined = "\n".join(f"- 原题：{v[:200]}" for v in variants[:3])
        variant_line = (
            f"3. 以下面的原题为母本出**变式题**（换数字/换情境/换问法，不换核心考点）：\n{joined}\n"
        )
    prompt = _GEN_PROMPT.format(
        stage=stage,
        purpose=purpose,
        focus=focus,
        difficulty=difficulty,
        count=count,
        variant_line=variant_line,
    )
    result = await llm.complete([ChatMessage(role="user", content=prompt)], temperature=0.6)
    data = extract_json(result.content or "")
    questions = (data or {}).get("questions") if isinstance(data, dict) else None
    if not questions:
        raise ProviderError(
            "出题失败：模型未返回有效题目",
            cause=(result.content or "")[:120],
            fix="稍后重试，或换一个知识点描述",
        )
    cleaned: list[dict[str, Any]] = []
    for q in questions[:count]:
        if not isinstance(q, dict) or not (q.get("text") or "").strip():
            continue
        cleaned.append(
            {
                "text": str(q.get("text") or "").strip(),
                "answer": str(q.get("answer") or "").strip(),
                "analysis": str(q.get("analysis") or "").strip(),
                "knowledge_point": str(q.get("knowledge_point") or knowledge_point or "").strip(),
                "difficulty": str(q.get("difficulty") or difficulty).strip(),
            }
        )
    if not cleaned:
        raise ProviderError("出题失败：题目内容为空", fix="稍后重试")
    return cleaned


_CHECK_PROMPT = (
    "你是严谨的阅卷老师。题目：{question}\n标准答案：{answer}\n学生作答：{student}\n"
    "请判分。只输出 JSON："
    '{{"correct": true 或 false, "verdict": "一句话判定（对就说对，错指出关键错误）"}}'
)


async def check_answer(llm, *, question: str, answer: str, student_answer: str) -> dict[str, Any]:
    """判一道题。返回 {"correct": bool, "verdict": str}。"""
    prompt = _CHECK_PROMPT.format(
        question=question[:500], answer=answer[:300], student=student_answer[:500]
    )
    result = await llm.complete([ChatMessage(role="user", content=prompt)], temperature=0.1)
    data = extract_json(result.content or "")
    if not isinstance(data, dict) or not isinstance(data.get("correct"), bool):
        raise ProviderError(
            "判分失败：模型未返回有效判定", cause=(result.content or "")[:120], fix="稍后重试"
        )
    return {"correct": data["correct"], "verdict": str(data.get("verdict") or "").strip()}


# ---- 免费题库 API ----------------------------------------------------------------


def search_jszkk(keyword: str, count: int = 3, *, timeout: float = 12.0) -> list[dict[str, Any]]:
    """jszkk「全能搜题」免费开放接口（免鉴权）：GET /api/open/seek?q=关键词。

    题库为开源社区贡献（大学网课题为主，K12 覆盖有限），作为免费题库第一优先源：
    有结果直接用，无结果自动落 web 搜题。返回 [{"text","answer",...}]（题目为空的不收）。
    """
    import httpx as _httpx

    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    variants = [keyword, f"{keyword} 答案"] if " " not in keyword else [keyword]
    for q in variants[:2]:
        if len(out) >= count:
            break
        try:
            resp = _httpx.get(
                "https://study.jszkk.com/api/open/seek",
                params={"q": q},
                timeout=timeout,
                headers={"Content-Type": "application/json; charset=utf-8"},
            )
            resp.raise_for_status()
            data = (resp.json() or {}).get("data") or {}
        except Exception:
            continue
        question = str(data.get("question") or "").strip()
        answer = str(data.get("answer") or "").strip()
        if len(question) >= 8 and question[:40] not in seen:
            seen.add(question[:40])
            out.append(
                {
                    "text": question,
                    "answer": answer.replace(" #", "；"),
                    "analysis": "来源：jszkk 全能搜题（社区题库）",
                    "knowledge_point": keyword,
                    "difficulty": "medium",
                    "source_url": "https://study.jszkk.com/",
                    "real": True,
                }
            )
    return out


def search_huohua(
    keyword: str,
    count: int = 3,
    *,
    api_key: str,
    subject: str = "",
    stage: str = "",
    timeout: float = 20.0,
) -> list[dict[str, Any]]:
    """火花数据 K12 题库 API（¥5/100 次，空结果不扣费）：小初高 1656 万题 + 1085 万题图。

    POST /v1/search（source=k12_questions），返回题干/题图/答案/解析。api_key 到
    huohuaapi.com 控制台注册开通（含 5 元即可用百次）。
    """
    import httpx as _httpx

    filters: dict[str, Any] = {}
    if subject:
        filters["subject"] = subject
    filters["knowledge"] = keyword
    resp = _httpx.post(
        "https://api.huohuaapi.cn/v1/search",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json={
            "source": "k12_questions",
            "purpose": f"找{stage or 'K12'}{subject or ''}{keyword}相关题目、答案与解析",
            "query": f"{keyword} 的题目、答案和解析",
            "filters": filters,
            "return": {"format": "json"},
        },
        timeout=timeout,
    )
    resp.raise_for_status()
    data = resp.json()
    results = data.get("results") or data.get("data") or []
    out: list[dict[str, Any]] = []
    for item in (results if isinstance(results, list) else [])[:count]:
        if isinstance(item, dict):
            text = str(item.get("question") or item.get("text") or item.get("content") or "").strip()
        else:
            text = str(item).strip()
        if len(text) < 10:
            continue
        isdict = isinstance(item, dict)
        answer = str(item.get("answer") or "").strip() if isdict else ""
        analysis = str(item.get("analysis") or item.get("explanation") or "").strip() if isdict else ""
        image = str(item.get("image") or item.get("image_url") or "").strip() if isdict else ""
        out.append(
            {
                "text": text,
                "answer": answer,
                "analysis": analysis,
                "knowledge_point": keyword,
                "difficulty": str(item.get("difficulty") or "medium").strip() if isdict else "medium",
                "image_url": image,
                "source_url": "火花数据 K12 题库",
                "real": True,
            }
        )
    return out


# ---- 网上找真题（免费来源：搜索 + 网页提取，替代付费题库 API） ------------------

_FIND_PROMPT = (
    "你是一位{stage}老师，正在从网页内容里挑选真实考题给学生练习。要求：\n"
    "1. **只挑网页里真实出现的题目**（考试真题/教辅题），严禁自己编造或改写数字；\n"
    "2. 知识点方向：{focus}；难度偏好：{difficulty}；最多 {count} 道；\n"
    "3. 只挑**纯文字能完整表述**的题（网页里的带图题无法把图给学生，跳过并说明）；\n"
    "4. 每道题给：text（题目原文全文）、answer（网页给出的答案；网页没给就填空字符串并标注）、"
    "analysis（解析，网页没给就给出你的解法要点）、knowledge_point（『学科/章节/名称』）、"
    "difficulty（easy/medium/hard）、source_url（来自哪个网页）；\n"
    "5. 如果网页内容里没有合适的题，返回空列表，不要硬凑。\n"
    "只输出 JSON：{{\"questions\":[{{...}}]}}。\n\n网页内容：\n{pages}"
)


async def find_real_questions(
    llm,
    *,
    search_fn,  # (query, limit) -> list[str] 行
    fetch_fn,  # (url, limit) -> str 正文
    topic: str,
    stage: str = "中学",
    difficulty: str = "medium",
    count: int = 3,
) -> tuple[list[dict[str, Any]], str]:
    """网上找真题：搜索 → 抓正文 → LLM 提取结构化原题。

    返回 (题目列表, 来源说明)。找不到合适的题返回 ([], 原因)，由调用方回退生成。
    """
    query = f"{topic} 真题 答案 解析 {stage}"
    hits = await asyncio.to_thread(search_fn, query, 6)
    if not hits:
        hits = await asyncio.to_thread(search_fn, f"{topic} 中考真题", 6)
    if not hits:
        return [], "搜索无结果"
    # 相关性过滤：标题/链接完全不含主题关键词（前 2 字）的结果视为搜索垃圾
    key = topic[:2]
    hits = [h for h in hits if key in h or topic in h]
    if not hits:
        return [], f"搜索结果与「{topic}」无关（搜索引擎反爬返回垃圾），可稍后重试或改用智能生成"
    pages = []
    used_urls: list[str] = []
    for line in hits[:3]:
        url = line.splitlines()[1].strip() if len(line.splitlines()) > 1 else ""
        if not url.startswith("http"):
            continue
        try:
            body = await asyncio.to_thread(fetch_fn, url, 2500)
        except Exception:
            continue
        if len(body) < 100:
            continue
        pages.append(f"【来源 {len(pages) + 1}】{url}\n{body[:2500]}")
        used_urls.append(url)
    if not pages:
        return [], "候选网页正文都不可用"
    prompt = _FIND_PROMPT.format(
        stage=stage, focus=topic or "学生近期学习内容", difficulty=difficulty, count=count,
        pages="\n\n".join(pages),
    )
    result = await llm.complete([ChatMessage(role="user", content=prompt)], temperature=0.2)
    data = extract_json(result.content or "")
    questions = (data or {}).get("questions") if isinstance(data, dict) else None
    cleaned: list[dict[str, Any]] = []
    for q in (questions or [])[:count]:
        if not isinstance(q, dict) or len(str(q.get("text") or "").strip()) < 10:
            continue
        cleaned.append(
            {
                "text": str(q.get("text") or "").strip(),
                "answer": str(q.get("answer") or "").strip(),
                "analysis": str(q.get("analysis") or "").strip(),
                "knowledge_point": str(q.get("knowledge_point") or topic).strip(),
                "difficulty": str(q.get("difficulty") or difficulty).strip(),
                "source_url": str(q.get("source_url") or (used_urls[0] if used_urls else "")).strip(),
                "real": True,
            }
        )
    return cleaned, f"来自网页：{', '.join(used_urls[:2])}"
