"""kuro doctor —— 健康检查：配置、数据库、模型、渠道逐项诊断。"""

from __future__ import annotations

from pathlib import Path

import typer
from rich import box
from rich.table import Table

from kurotutor.config.loader import default_config_path
from kurotutor.storage import build_db_url, build_engine, init_db, session_scope

from . import ui


def _check_db(config) -> tuple[str, str]:
    try:
        db_url = build_db_url(config.data_dir)
        engine = build_engine(db_url)
        init_db(engine)
        with session_scope(engine) as db:
            from sqlalchemy import text

            db.exec(text("SELECT 1")).all()
        return "通过", "SQLite 建表与读写正常"
    except Exception as exc:
        return "失败", str(exc)


def _check_llm(config) -> tuple[str, str]:
    if config.models is None or config.models.llm is None:
        return "警告", "未配置文本模型"
    spec = config.models.llm
    if spec.provider.lower() in ("echo", "mock"):
        return "通过", "echo 离线模型（仅联调，非真实教学）"
    if not spec.api_key:
        return "警告", f"provider={spec.provider} 但 api_key 为空"
    return "待验证", f"provider={spec.provider} model={spec.model}（需联网+密钥）"


def _check_config(config) -> tuple[str, str]:
    issues = config.model_validate_extra()
    if not issues:
        return "通过", "配置完整"
    return "警告", f"{len(issues)} 项待补全（如视觉模型/渠道密钥）"


def _check_vision(config) -> tuple[str, str]:
    if config.models is None or config.models.vision is None:
        return "警告", "未配置视觉模型（拍照解题/批改不可用）"
    spec = config.models.vision
    if not spec.api_key:
        return "警告", f"provider={spec.provider} 但 api_key 为空"
    return "待验证", f"provider={spec.provider} model={spec.model}（需联网+密钥）"


def _check_channel(config) -> tuple[str, str]:
    onebot = getattr(config.channel, "onebot", None) or {}
    ws_url = onebot.get("ws_url") or ""
    if ws_url:
        return "待验证", f"OneBot 后端 {ws_url}（需本机运行 napcat 等后端）"
    if config.channel.app_id:
        return "待验证", "QQ 渠道 app_id 已配置（需接入后端）"
    return "联调", "未配置 QQ 后端，可用控制台渠道（`kuro serve --channel console`）联调"


def doctor_command(config: Path | None = typer.Option(None, "--config", "-c")) -> None:
    """检查配置 / 数据库 / 模型 / 渠道健康状态。"""
    path = config or default_config_path()
    ui.heading("KuroTutor 健康检查")

    from kurotutor.config import load_config

    try:
        cfg = load_config(path)
    except Exception as exc:
        ui.err(f"配置加载失败：{exc}")
        raise typer.Exit(1) from exc

    checks = [
        ("配置文件", "通过" if path.exists() else "警告", str(path) if path.exists() else "未找到 kuro.json"),
        ("配置校验", *_check_config(cfg)),
        ("数据库", *_check_db(cfg)),
        ("文本模型", *_check_llm(cfg)),
        ("视觉模型", *_check_vision(cfg)),
        ("渠道", *_check_channel(cfg)),
    ]

    table = Table(box=box.ROUNDED, title="诊断结果", header_style="bold cyan")
    table.add_column("项目", style="bold")
    table.add_column("状态", justify="center")
    table.add_column("说明")
    for name, status, detail in checks:
        styled = status
        if status == "通过":
            styled = "[green]✓ 通过[/green]"
        elif status == "警告":
            styled = "[yellow]! 警告[/yellow]"
        elif status == "失败":
            styled = "[red]✗ 失败[/red]"
        else:
            styled = f"[dim]{status}[/dim]"
        table.add_row(name, styled, detail)
    ui.console.print(table)

    if any(s == "失败" for _, s, _ in checks):
        ui.err("发现失败项，请修复后重试。")
        raise typer.Exit(1)
    ui.ok("未发现致命问题。可 `kuro serve` 启动（或 `kuro init` 补全配置）。")
