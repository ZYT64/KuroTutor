"""知识库工具：方法卡片沉淀与检索。

产品规格书 4.3：双库结构（语料库 + 方法库），随解题主动沉淀方法卡片。
本模块实现方法库的真实写入与检索。检索策略：

- 本期：字段级关键词匹配（subject/question_type + method/steps/pitfalls），
  配合置信度排序，结果可复用。
- 向量检索（嵌入 + 重排）在嵌入服务接入后，以同接口增强替换排序逻辑，不改变工具签名。

语义去重合并：命中已有同 subject+question_type 卡片时，更新而非新建（越用越强）。
"""

from __future__ import annotations

from typing import Any

from sqlmodel import select

from kurotutor.agent.context import ToolContext
from kurotutor.storage import KnowledgeCard, session_scope


async def deposit_card(ctx: ToolContext, kwargs: dict[str, Any]) -> str:
    """沉淀一张方法卡片。参数：subject, question_type, method, steps, pitfalls, source。"""
    subject = (kwargs.get("subject") or "").strip()
    question_type = (kwargs.get("question_type") or "").strip()
    method = (kwargs.get("method") or "").strip()
    if not subject or not question_type or not method:
        return "方法卡片至少需要 subject / question_type / method 三要素。"
    steps = (kwargs.get("steps") or "").strip()
    pitfalls = (kwargs.get("pitfalls") or "").strip()
    source = (kwargs.get("source") or "").strip()

    with session_scope(ctx.engine) as db:
        # 语义去重合并：同学生 + 同 subject + 同题型 → 更新已有卡片
        card = db.exec(
            select(KnowledgeCard).where(
                KnowledgeCard.student_id == ctx.student.id,
                KnowledgeCard.subject == subject,
                KnowledgeCard.question_type == question_type,
            )
        ).first()
        if card is None:
            card = KnowledgeCard(
                student_id=ctx.student.id,
                subject=subject,
                question_type=question_type,
            )
            db.add(card)
            db.flush()
            updated = False
        else:
            updated = True
        card.method = method
        if steps:
            card.steps = steps
        if pitfalls:
            card.pitfalls = pitfalls
        if source:
            card.source = source
        card_id = card.id
    note = "（已合并更新）" if updated else ""
    return f"已沉淀方法卡片 #{card_id}「{subject} · {question_type}」{note}"


async def search_cards(ctx: ToolContext, kwargs: dict[str, Any]) -> str:
    """检索方法卡片。参数：subject（可选），query（题型/方法关键词），limit。"""
    subject = (kwargs.get("subject") or "").strip()
    query = (kwargs.get("query") or "").strip()
    limit = int(kwargs.get("limit") or 5)
    with session_scope(ctx.engine) as db:
        stmt = select(KnowledgeCard).where(
            (KnowledgeCard.student_id == ctx.student.id) | (KnowledgeCard.student_id.is_(None))
        )
        if subject:
            stmt = stmt.where(KnowledgeCard.subject == subject)
        rows = list(db.exec(stmt).all())
    if not query:
        rows = rows[:limit]
    else:
        rows = await _rank_cards(ctx, query, rows, limit)

    if not rows:
        return "知识库里暂无匹配的方法卡片。"
    lines = [f"找到 {len(rows)} 张方法卡片："]
    for c in rows:
        lines.append(f"- [{c.subject}·{c.question_type}] {c.method}")
        if c.steps:
            lines.append(f"    步骤：{c.steps[:60]}")
        if c.pitfalls:
            lines.append(f"    易错：{c.pitfalls[:60]}")
    return "\n".join(lines)


async def _rank_cards(ctx: ToolContext, query: str, rows: list[Any], limit: int) -> list[Any]:
    """语义重排：配置了嵌入模型则用余弦，否则回退关键词。全程用卡片对象。"""
    from kurotutor.kb.embeddings import build_embedding_provider, cosine
    from kurotutor.kb.reranker import searchable_text

    cards = list(rows)
    spec = ctx.config.models.embedding if ctx.config.models else None
    if spec is not None and spec.api_key and query:
        try:
            provider = build_embedding_provider(spec)
            texts = [query] + [
                searchable_text(
                    {
                        "question_type": c.question_type,
                        "method": c.method,
                        "steps": c.steps,
                        "pitfalls": c.pitfalls,
                    }
                )
                for c in cards
            ]
            try:
                embs = await provider.embed(texts)
            finally:
                await provider.aclose()
            query_emb = embs[0]
            scored = sorted(
                ((cosine(query_emb, embs[i + 1]), i) for i in range(len(cards))),
                key=lambda x: -x[0],
            )
            ranked = [cards[i] for score, i in scored if score > 0]
            return (ranked or cards)[:limit]
        except Exception:
            pass  # 嵌入失败 → 回退关键词
    # 关键词回退
    q = query.lower()
    scored = []
    for c in cards:
        blob = f"{c.question_type} {c.method} {c.steps} {c.pitfalls}".lower()
        if q in blob:
            score = sum(1 for f in (c.question_type, c.method, c.steps, c.pitfalls) if q in f.lower())
            scored.append((score, c))
    scored.sort(key=lambda x: -x[0])
    return [c for _, c in scored][:limit] or cards[:limit]
