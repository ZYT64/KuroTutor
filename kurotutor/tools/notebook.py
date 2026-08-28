"""笔记本工具：记录 / 发笔记图解析入库 / 查询。

产品规格书 4.9 笔记本功能：发笔记图 → 异步解析（文字识别+视觉理解）→ 智能归类 → 确认入库 → 查询。
本模块实现真实的入库、入库后查询，以及「笔记图 → 解析 → 归类入库」闭环（视觉 Provider 可插拔）。
归类用「已有笔记本匹配 + 学科映射 + 主题兜底」三档；向量语义归类待嵌入服务接入后同接口增强。
"""

from __future__ import annotations

from typing import Any

from sqlmodel import select

from kurotutor.agent.context import ToolContext
from kurotutor.core.errors import ToolError
from kurotutor.services.vision import build_vision_provider, extract_json
from kurotutor.storage import NotebookEntry, NotebookSource, session_scope

# 视觉解析提示词：只输出 JSON
_PARSE_PROMPT = (
    "你是一位学生学习笔记整理助手，正在看学生发来的笔记图片。"
    "请读取笔记内容，并按要求输出一个 JSON 对象，不要输出其他文字：\n"
    '{"subject":"学科（如数学）","topic":"主题（如函数）",'
    '"summary":"一句话摘要","content":"笔记正文（尽量完整转写）"}'
    "\n若图片看不清或不是笔记，请如实把相应字段留空，不要编造。"
)


def _guess_notebook(subject: str, topic: str) -> str:
    """从学科/主题推断笔记本名。映射常用学科为固定笔记本。"""
    mapping = {
        "数学": "数学",
        "语文": "语文",
        "英语": "英语",
        "物理": "物理",
        "化学": "化学",
        "生物": "生物",
        "历史": "历史",
        "地理": "地理",
        "政治": "政治",
    }
    for key, name in mapping.items():
        if key in subject or key in topic:
            return name
    return topic or "未分类"


def _categorize(engine: Any, student_id: int, subject: str, topic: str) -> str:
    """智能归类三档：① 命中已有笔记本名 ② 学科映射 ③ 主题兜底。"""
    with session_scope(engine) as db:
        existing = db.exec(
            select(NotebookEntry.notebook).where(NotebookEntry.student_id == student_id).distinct()
        ).all()
    # ① 已有笔记本匹配（主题/学科出现其中一个名字）
    for name in existing:
        if name and (name in subject or name in topic):
            return name
    return _guess_notebook(subject, topic)


async def parse_photo(ctx: ToolContext, kwargs: dict[str, Any]) -> str:
    """发笔记图 → 视觉解析 → 智能归类 → 入库。参数：path（图片路径）。"""
    path = (kwargs.get("path") or "").strip()
    if not path:
        return "请提供笔记图片路径（path）。"
    if not path.startswith(("http://", "https://")):
        path = str(ctx.sandbox.resolve_path(path, for_write=False))

    if ctx.config.models is None or ctx.config.models.vision is None:
        raise ToolError(
            "未配置视觉模型，无法解析笔记图",
            cause="models.vision 缺失",
            fix="在 kuro.json 中配置 models.vision",
        )
    provider = build_vision_provider(ctx.config.models.vision)
    try:
        raw = await provider.understand(path, _PARSE_PROMPT, detail="high")
    except Exception as exc:
        raise ToolError(str(exc), fix="视觉模型调用失败，请稍后重试或检查配置") from exc
    finally:
        await provider.aclose()

    data = extract_json(raw)
    subject = (data.get("subject") or "").strip()
    topic = (data.get("topic") or "").strip()
    summary = (data.get("summary") or "").strip()
    content = (data.get("content") or "").strip()
    if not summary and not content:
        return "未能解析出笔记内容，请换一张更清晰/对齐的笔记图重试。"

    notebook = _categorize(ctx.engine, ctx.student.id, subject, topic)
    with session_scope(ctx.engine) as db:
        entry = NotebookEntry(
            student_id=ctx.student.id,
            notebook=notebook,
            subject=subject,
            topic=topic,
            summary=summary,
            content=content,
            source=NotebookSource.IMAGE,
            source_path=path,
        )
        db.add(entry)
        db.flush()
        entry_id = entry.id
    return f"已解析并存入笔记本「{notebook}」（#{entry_id}）——主题：{topic or '（未识别）'}"


async def add_notebook(ctx: ToolContext, kwargs: dict[str, Any]) -> str:
    """记一条笔记。参数：subject, topic, summary, content, notebook(可选), source。"""
    subject = (kwargs.get("subject") or "").strip()
    topic = (kwargs.get("topic") or "").strip()
    summary = (kwargs.get("summary") or "").strip()
    content = (kwargs.get("content") or "").strip()
    if not summary and not content:
        return "请提供笔记内容（summary 或 content）。"
    notebook = (kwargs.get("notebook") or "").strip() or _guess_notebook(subject, topic)
    source = kwargs.get("source") or NotebookSource.TEXT

    with session_scope(ctx.engine) as db:
        entry = NotebookEntry(
            student_id=ctx.student.id,
            notebook=notebook,
            subject=subject,
            topic=topic,
            summary=summary,
            content=content,
            source=source,
            source_path=(kwargs.get("source_path") or ""),
        )
        db.add(entry)
        db.flush()
        entry_id = entry.id
    return f"已存入笔记本「{notebook}」（#{entry_id}）。主题：{topic or '（无）'}"


async def query_notebook(ctx: ToolContext, kwargs: dict[str, Any]) -> str:
    """查笔记。参数：notebook（可选），keyword（可选），limit。"""
    notebook = (kwargs.get("notebook") or "").strip()
    keyword = (kwargs.get("keyword") or "").strip()
    limit = int(kwargs.get("limit") or 20)
    with session_scope(ctx.engine) as db:
        stmt = select(NotebookEntry).where(NotebookEntry.student_id == ctx.student.id)
        if notebook:
            stmt = stmt.where(NotebookEntry.notebook == notebook)
        if keyword:
            like = f"%{keyword}%"
            stmt = stmt.where(
                (NotebookEntry.summary.like(like))
                | (NotebookEntry.content.like(like))
                | (NotebookEntry.topic.like(like))
            )
        stmt = stmt.order_by(NotebookEntry.created_at.desc()).limit(limit)
        rows = db.exec(stmt).all()
    if not rows:
        return "笔记本里暂无匹配记录。"
    lines = [f"共 {len(rows)} 条笔记："]
    for e in rows:
        lines.append(f"- [{e.notebook}] {e.topic or ''}：{e.summary[:50]}")
    return "\n".join(lines)
