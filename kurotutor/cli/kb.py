"""kuro kb —— 知识库管理。"""

from __future__ import annotations

from pathlib import Path

import typer
from sqlmodel import select

from kurotutor.storage import KnowledgeCard, NotebookEntry, session_scope

from . import ui

app = typer.Typer(help="知识库管理", add_completion=False)


@app.command("status")
def status(config: Path | None = typer.Option(None, "--config", "-c")) -> None:
    """知识库统计。"""
    from kurotutor.cli.common import load_runtime

    rt = load_runtime(config)
    with session_scope(rt.engine) as db:
        cards = db.exec(select(KnowledgeCard)).all()
        notes = db.exec(select(NotebookEntry)).all()
        subjects = db.exec(select(KnowledgeCard.subject).distinct()).all()

    ui.heading("知识库状态")
    ui.kv_table(
        "方法库 / 方法卡片",
        [
            ("卡片总数", str(len(cards))),
            ("覆盖学科", "、".join([s for s in subjects if s]) or "[dim]无[/dim]"),
            ("存储路径", rt.config.kb.path),
        ],
    )
    ui.kv_table(
        "笔记本",
        [
            ("笔记总数", str(len(notes))),
            ("笔记本数", str(len({n.notebook for n in notes}))),
        ],
    )
    if not cards and not notes:
        ui.warn("知识库为空。解题后会主动沉淀方法卡片（`kuro kb status` 可随时查看）。")
