"""重排器：对候选结果按与查询的相似度重新排序。

提供两种策略：
- :func:`rerank_by_embedding`：用余弦相似度（有嵌入时）。
- :func:`rerank_by_keyword`：按字段命中次数（无嵌入时的回退）。

保持接口稳定，方便后续换模型/策略。
"""

from __future__ import annotations

from typing import Any

from kurotutor.kb.embeddings import cosine


def searchable_text(item: dict[str, Any]) -> str:
    """把一张方法卡片/记录拼成可检索文本。"""
    parts = [
        item.get("question_type", ""),
        item.get("method", ""),
        item.get("steps", ""),
        item.get("pitfalls", ""),
    ]
    return " ".join(p for p in parts if p)


def rerank_by_keyword(query: str, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """关键词命中越多越靠前（无嵌入时的回退）。"""
    q = query.lower()
    if not q:
        return items
    scored: list[tuple[int, dict[str, Any]]] = []
    for it in items:
        text = searchable_text(it).lower()
        score = sum(
            1
            for field in (
                it.get("question_type", ""),
                it.get("method", ""),
                it.get("steps", ""),
                it.get("pitfalls", ""),
            )
            if q in field.lower()
        )
        if q in text:
            scored.append((max(score, 1), it))
    scored.sort(key=lambda x: -x[0])
    return [it for _, it in scored]


def rerank_by_embedding(
    query_emb: list[float], items: list[dict[str, Any]], item_embs: list[list[float]]
) -> list[dict[str, Any]]:
    """按查询向量与各候选向量的余弦相似度降序重排。"""
    pairs = list(zip(items, item_embs, strict=False))
    pairs.sort(key=lambda p: cosine(query_emb, p[1]), reverse=True)
    return [it for it, _ in pairs]
