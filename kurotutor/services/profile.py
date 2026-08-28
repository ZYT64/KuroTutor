"""学生实力画像服务（产品规格书 4.1）。

表示：学段 + 学科→章节→知识点，每点带 掌握度(0-1)、置信度、最近练习时间、错误类型分布。
更新：难度加权更新（每次解题/批改后异步调用）。
驱动：错题询问策略（简单题已熟练→默认不存；中难题+薄弱→主动问；同类连续错→直接存+推加练）。

画像驱动的两个动作在 :meth:`ProfileService.update_after_answer`（更新掌握度）
与 :meth:`ProfileService.wrongbook_policy`（返回应「 skip / ask / record 」）里落地。
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from sqlmodel import select

from kurotutor.core import get_logger
from kurotutor.storage import KnowledgePoint, session_scope
from kurotutor.storage.models import WrongStatus

log = get_logger("profile")

# 掌握度 EMA 更新系数：越大越偏向最近表现
_ALPHA = 0.3
# 置信度单次增量
_CONF_DELTA = 0.05
# 掌握度低于该值视为薄弱点
_WEAK_THRESHOLD = 0.6
# 同类连续错 ≥ 该次数 → 直接记录
_RECENT_WRONG_THRESHOLD = 2


class WrongbookPolicy(StrEnum):
    """错题询问策略结论。"""

    SKIP = "skip"  # 已熟练 / 无信息：不打扰，默认不存
    ASK = "ask"  # 中难题+薄弱：主动询问是否记入
    RECORD = "record"  # 同类连续错 / 明显薄弱：直接记入 + 建议加练


def _utcnow() -> datetime:
    return datetime.now(UTC)


class ProfileService:
    """学生画像的读写与决策。"""

    def __init__(self, engine: Any):
        self._engine = engine

    # ---- 读取 ---------------------------------------------------------------

    def get_or_create_kp(
        self, db: Any, *, student_id: int, subject: str, chapter: str, name: str
    ) -> KnowledgePoint:
        kp = db.exec(
            select(KnowledgePoint).where(
                KnowledgePoint.student_id == student_id,
                KnowledgePoint.subject == subject,
                KnowledgePoint.name == name,
            )
        ).first()
        if kp is None:
            kp = KnowledgePoint(
                student_id=student_id, subject=subject, chapter=chapter, name=name, mastery=0.5
            )
            db.add(kp)
            db.flush()
        return kp

    def _recent_wrong_count(self, db: Any, *, student_id: int, subject: str, name: str) -> int:
        from kurotutor.storage import WrongQuestion

        # 该知识点在待复习状态的错题累计错误次数
        rows = db.exec(
            select(WrongQuestion).where(
                WrongQuestion.student_id == student_id,
                WrongQuestion.subject == subject,
                WrongQuestion.status == WrongStatus.TO_REVIEW,
            )
        ).all()
        total = 0
        for wq in rows:
            kp = db.get(KnowledgePoint, wq.knowledge_point_id)
            if kp and kp.name == name:
                total += wq.times_wrong
        return total

    # ---- 更新 ---------------------------------------------------------------

    def update_after_answer(
        self,
        *,
        student_id: int,
        subject: str,
        chapter: str,
        name: str,
        is_correct: bool | None,
        error_type: str = "unknown",
    ) -> float:
        """每次判分后更新掌握度/置信度/错误分布。返回更新后的掌握度。"""
        if is_correct is None:
            return 0.5  # 无判分信息不更新
        with session_scope(self._engine) as db:
            kp = self.get_or_create_kp(db, student_id=student_id, subject=subject, chapter=chapter, name=name)
            score = 1.0 if is_correct else 0.0
            kp.mastery = round(kp.mastery * (1 - _ALPHA) + score * _ALPHA, 4)
            kp.confidence = round(min(1.0, kp.confidence + _CONF_DELTA), 4)
            kp.last_practice_at = _utcnow()
            if not is_correct:
                dist = _parse_dist(kp.error_distribution)
                dist[error_type] = dist.get(error_type, 0) + 1
                kp.error_distribution = _dump_dist(dist)
            db.add(kp)
            return kp.mastery

    # ---- 错题策略 -----------------------------------------------------------

    def wrongbook_policy(
        self,
        *,
        student_id: int,
        subject: str,
        chapter: str,
        name: str,
        is_correct: bool | None,
        difficulty: str = "medium",
        error_category: str = "unknown",
    ) -> WrongbookPolicy:
        """根据画像与本次判分，决定错题该 skip / ask / record（少打扰）。

        - 做对 / 无判分 → 默认不存。
        - 同类连续错 → 直接记（无需问）。
        - 波动薄弱点（有历史且掌握度低）→ 直接记。
        - 概念性中难题做错 → 询问（结合外部「最多一问」护栏，不会反复打搅）。
        - 简单 / 粗心 / 未知 → 默认不打扰。
        """
        if is_correct is None:
            return WrongbookPolicy.SKIP
        if is_correct is True:
            return WrongbookPolicy.SKIP  # 做对了：不记错题（可能仍沉淀方法卡）
        with session_scope(self._engine) as db:
            kp = db.exec(
                select(KnowledgePoint).where(
                    KnowledgePoint.student_id == student_id,
                    KnowledgePoint.subject == subject,
                    KnowledgePoint.name == name,
                )
            ).first()
            confidence = kp.confidence if kp else 0.0
            mastery = kp.mastery if kp else 0.5
            recent_wrong = self._recent_wrong_count(db, student_id=student_id, subject=subject, name=name)
        if recent_wrong >= _RECENT_WRONG_THRESHOLD:
            return WrongbookPolicy.RECORD  # 同类连续错：直接存
        if kp is not None and confidence >= 0.2 and mastery < _WEAK_THRESHOLD:
            return WrongbookPolicy.RECORD  # 有力历史且确认薄弱：直接存
        # 概念性中难题做错 → 主动问；其余（粗心/简单/未知）→ 不打扰
        if difficulty in ("medium", "hard") and error_category == "conceptual":
            return WrongbookPolicy.ASK
        return WrongbookPolicy.SKIP


def _parse_dist(raw: str) -> dict[str, int]:
    import json

    try:
        return json.loads(raw) if raw else {}
    except (ValueError, TypeError):
        return {}


def _dump_dist(dist: dict[str, int]) -> str:
    import json

    return json.dumps(dist, ensure_ascii=False)
