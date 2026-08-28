"""kuro agent —— 查看已注册工具与技能。"""

from __future__ import annotations

from pathlib import Path

import typer

from kurotutor.cli.common import load_runtime

from . import ui

app = typer.Typer(help="Agent 工具/技能查看", add_completion=False)


@app.command("tools")
def tools(config: Path | None = typer.Option(None, "--config", "-c")) -> None:
    """列出全部已注册工具。"""
    rt = load_runtime(config)
    reg = rt.registry
    if reg.count() == 0:
        ui.warn("当前没有任何工具注册。")
        return
    rows = []
    for t in reg.list():
        rows.append([t.name, t.category, t.description])
    ui.list_table(f"已注册工具（共 {reg.count()} 个）", ["名称", "分类", "描述"], rows)
    ui.info("Agent 在教学中按需调用这些工具；新增工具见 kurotutor/tools/register.py。")


@app.command("skills")
def skills(config: Path | None = typer.Option(None, "--config", "-c")) -> None:
    """列出已安装的技能。

    技能系统（元数据渐进式披露 + 自动技能生成）规划于 M2，当前未落地，
    此处如实上报状态，不给假数据。
    """
    rt = load_runtime(config)
    skills_dir = rt.config.paths.skills_dir
    ui.heading("技能系统")
    ui.info(f"技能目录：{skills_dir}")
    ui.warn("技能系统尚在 M2 规划中，当前未启用，本命令暂列 0 个技能。")
    ui.info("落地后这里会支持 `kuro skill list / add / remove`。")
