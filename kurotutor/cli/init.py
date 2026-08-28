"""kuro init —— 交互式初始化配置。

引导用户填写渠道、LLM、视觉模型等关键项，写入 kuro.json。
提供「本地联调」默认值（echo 模型），保证无密钥也能先把整条链跑通，
之后再 `kuro config set` 换成真实模型。
"""

from __future__ import annotations

import json
from pathlib import Path

import typer
from rich.prompt import Prompt

from kurotutor.cli import ui
from kurotutor.config.loader import default_config_path

app = typer.Typer(help="交互式初始化配置", add_completion=False)


def _write_json(path: Path, data: dict) -> None:
    data.setdefault("name", "kurotutor")
    data.setdefault("version", "0.1.0")
    data.setdefault("workspace", "data/workspaces")
    data.setdefault("data_dir", "data")
    data.setdefault(
        "permissions",
        {
            "shell": "deny",
            "file_access": "workspace_only",
            "model_endpoints": [],
        },
    )
    data.setdefault("kb", {"vector_store": "sqlite-vec", "path": "data/kb"})
    data.setdefault("paths", {"skills_dir": "skills", "plugins_dir": "plugins"})
    data.setdefault("server", {"host": "0.0.0.0", "port": 8000})
    data.setdefault("log_level", "info")
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def run_init(config_path: Path | None = None) -> None:
    """执行初始化流程（可被 `kuro config init` 复用）。"""
    path = config_path or default_config_path()
    ui.heading("KuroTutor 初始化")

    if path.exists():
        ui.info(f"检测到已有配置：{path}")
        if not typer.confirm("要继续覆盖吗？", default=False):
            ui.info("已取消。")
            raise typer.Exit(0)

    ui.subheading("1/3 渠道 —— QQ 机器人（可留空跳过，用控制台渠道联调）")
    app_id = Prompt.ask("QQ 机器人 app_id（留空跳过）", default="")
    secret = Prompt.ask("QQ 机器人 secret（留空跳过）", default="")

    ui.subheading("2/3 文本模型（LLM）—— 老师的大脑")
    provider = Prompt.ask("provider（openai / echo，echo 为离线联调）", default="openai")
    model = Prompt.ask("model（如 deepseek-v3）", default="deepseek-v3")
    base_url = Prompt.ask("base_url（OpenAI 兼容端点，留空用默认）", default="")
    api_key = Prompt.ask("api_key（BYOK，留空表示稍后配置）", default="", password=True)

    ui.subheading("3/3 可选 —— 视觉模型（拍照解题/批改需要）")
    has_vision = typer.confirm("配置视觉模型？", default=False)
    vision = None
    if has_vision:
        vision = {
            "provider": Prompt.ask("vision provider", default="openai"),
            "model": Prompt.ask("vision model", default="qwen-vl-plus"),
            "base_url": Prompt.ask("vision base_url", default=""),
            "api_key": Prompt.ask("vision api_key", default="", password=True),
        }

    data: dict = {"channel": {"app_id": app_id or "", "secret": secret or ""}}
    models: dict = {
        "llm": {
            "provider": provider,
            "model": model,
            "api_key": api_key,
        }
    }
    if base_url:
        models["llm"]["base_url"] = base_url
    if has_vision and vision:
        models["vision"] = vision
    # 其余模型块保留示例占位（可后续按需配置）
    data["models"] = models

    _write_json(path, data)

    ui.ok(f"已写入配置：{path}")
    ui.info("下一步：`kuro config validate` 检查配置，`kuro doctor` 做健康检查。")
    if not api_key or provider.lower() == "echo":
        ui.info("当前为离线联调配置。要用真实模型，请 `kuro config set models.llm.api_key <你的密钥>`。")


@app.command()
def run(config: Path | None = typer.Option(None, "--config", "-c")) -> None:
    """执行初始化。"""
    run_init(config)
