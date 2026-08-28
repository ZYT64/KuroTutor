"""讲义生成（产品规格书 4.4 长内容模式①「写讲义系统讲」）。

把某一主题生成结构化、可打印的 Markdown 讲义，落盘到工作区，返回路径。
供定时课堂备课、长内容系统讲解复用。
"""

from __future__ import annotations

import re
from typing import Any

from kurotutor.agent.context import ToolContext
from kurotutor.core import get_logger, log_event
from kurotutor.core.errors import ToolError
from kurotutor.services.llm import ChatMessage, build_llm_provider

log = get_logger("lecture")

_PROMPT = (
    "你是一位资深老师，请为{stage}学生写一份关于「{topic}」的高质量讲义。\n"
    "要求结构清晰、循序渐进、适合学习：\n"
    "## 知识框架\n## 核心概念（逐条讲透、举例）\n## 典型例题（每题：思路→详解→关键点）\n"
    "## 易错点\n## 自测题（含答案在文末）\n\n用 Markdown 输出，正文从一级标题开始。"
    "只输出讲义本体，不要任何额外说明。"
)

_STAGE_ZH = {"primary": "小学", "junior": "初中", "senior": "高中", "university": "大学/考研"}


def _safe_name(topic: str) -> str:
    return re.sub(r"[\\/:*?\"<>|]", "_", topic.strip())[:40] or "讲义"


async def generate_lecture(ctx: ToolContext, kwargs: dict[str, Any]) -> str:
    """生成讲义并落盘。参数：topic（主题，必填），subject（学科，可选）。"""
    topic = (kwargs.get("topic") or "").strip()
    if not topic:
        return "请提供讲义主题（topic）。"
    if ctx.config.models is None or ctx.config.models.llm is None:
        raise ToolError("未配置文本模型，无法生成讲义", cause="models.llm 缺失", fix="配置 models.llm")
    stage = _STAGE_ZH.get(getattr(ctx.student, "stage", "") or "", "初中")
    prompt = _PROMPT.format(stage=stage, topic=topic)

    provider = build_llm_provider(ctx.config.models.llm)
    try:
        result = await provider.complete([ChatMessage(role="user", content=prompt)], max_tokens=4096)
    except Exception as exc:
        raise ToolError(str(exc), fix="模型调用失败，请稍后重试或检查配置") from exc
    finally:
        await provider.aclose()

    body = (result.content or "").strip()
    if not body:
        return "模型未能生成讲义内容，请稍后重试。"
    body = re.sub(r"\n{3,}", "\n\n", body)  # 压缩多余空行

    out_path = ctx.sandbox.resolve_path(f"lectures/{_safe_name(topic)}.md", for_write=True)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(body, encoding="utf-8")

    sections = sum(1 for line in body.splitlines() if line.strip().startswith("#"))
    log_event(log, "lecture generated", path=str(out_path), topic=topic)
    return (
        f"已生成讲义：『{topic}』\n路径：{out_path}\n章节数：{sections}，字数：{len(body)}。"
        "可直接打印或发给学生。"
    )
