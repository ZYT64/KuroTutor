"""知识库·语料库工具（规格 4.3 双库结构的「语料库」）。

- ``corpus_add``：入库大段教学资料（讲义/教材/百科，含标题/学科/来源/标签）。
- ``corpus_search``：按学科/关键词检索；配置了嵌入模型则用语义重排，否则关键词回退。
"""

from __future__ import annotations

import json
from typing import Any

from sqlmodel import select

from kurotutor.agent.context import ToolContext
from kurotutor.storage import CorpusEntry, session_scope


def _as_list(v: Any) -> list[str]:
    if isinstance(v, list):
        return [str(x) for x in v]
    return [x for x in str(v or "").replace("，", ",").split(",") if x.strip()]


async def corpus_add(ctx: ToolContext, kwargs: dict[str, Any]) -> str:
    """入库一段语料。参数：subject, title, content, source, tags。"""
    subject = (kwargs.get("subject") or "").strip() or "综合"
    title = (kwargs.get("title") or "").strip()
    content = (kwargs.get("content") or "").strip()
    if not content:
        return "请提供语料内容（content）。"
    source = (kwargs.get("source") or "").strip()
    tags = json.dumps(_as_list(kwargs.get("tags")), ensure_ascii=False)
    with session_scope(ctx.engine) as db:
        entry = CorpusEntry(
            student_id=ctx.student.id,
            subject=subject,
            title=title,
            content=content,
            source=source or "import",
            tags=tags,
        )
        db.add(entry)
        db.flush()
        entry_id = entry.id
    return f"语料已入库 #{entry_id}（{subject} · {title or '无标题'}，{len(content)} 字）。"


async def corpus_search(ctx: ToolContext, kwargs: dict[str, Any]) -> str:
    """检索语料。参数：subject（可选），query（关键词），limit。"""
    from kurotutor.kb.embeddings import build_embedding_provider, cosine

    subject = (kwargs.get("subject") or "").strip()
    query = (kwargs.get("query") or "").strip()
    limit = int(kwargs.get("limit") or 5)
    with session_scope(ctx.engine) as db:
        stmt = select(CorpusEntry).where(
            (CorpusEntry.student_id == ctx.student.id) | (CorpusEntry.student_id.is_(None))
        )
        if subject:
            stmt = stmt.where(CorpusEntry.subject == subject)
        rows = list(db.exec(stmt).all())

    if query:
        spec = ctx.config.models.embedding if ctx.config.models else None
        if spec is not None and spec.api_key:
            try:
                provider = build_embedding_provider(spec)
                texts = [query] + [f"{e.title} {e.content}" for e in rows]
                try:
                    embs = await provider.embed(texts)
                finally:
                    await provider.aclose()
                q_emb = embs[0]
                scored = sorted(
                    ((cosine(q_emb, embs[i + 1]), i) for i in range(len(rows))), key=lambda x: -x[0]
                )
                rows = [rows[i] for score, i in scored if score > 0][:limit] or rows[:limit]
            except Exception:
                rows = _keyword(rows, query, limit)
        else:
            rows = _keyword(rows, query, limit)
    else:
        rows = rows[:limit]

    if not rows:
        return "语料库里暂无匹配内容。"
    lines = [f"语料库找到 {len(rows)} 条："]
    for e in rows:
        lines.append(f"- [{e.subject}] {e.title or '无标题'}：{e.content[:60]}")
    return "\n".join(lines)


def _keyword(rows: list[CorpusEntry], query: str, limit: int) -> list[CorpusEntry]:
    q = query.lower()
    scored = []
    for e in rows:
        blob = f"{e.subject} {e.title} {e.content}".lower()
        if q in blob:
            scored.append((blob.count(q), e))
    scored.sort(key=lambda x: -x[0])
    return [e for _, e in scored][:limit]
