"""配置管理子命令：show / get / set / validate。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import typer

from kurotutor.config import ValidationIssue, load_config
from kurotutor.config.loader import default_config_path

from . import ui

app = typer.Typer(help="配置管理", add_completion=False)


def _as_bool(v: str) -> bool:
    return v.lower() in ("1", "true", "yes", "on", "是")


def _coerce(raw: str) -> Any:
    """把命令行字符串尽量转成结构化类型（bool/int/float），否则保留字符串。"""
    s = raw.strip()
    if s.lower() in ("true", "1", "yes", "on", "是"):
        return True
    if s.lower() in ("false", "0", "no", "off", "否"):
        return False
    try:
        return int(s)
    except ValueError:
        pass
    try:
        return float(s)
    except ValueError:
        return s


def _get_nested(data: dict[str, Any], dotted: str) -> tuple[bool, Any, str]:
    """按 dot 路径取子对象。返回 (是否存在, 值, 上报错误说明)。"""
    cur: Any = data
    for part in dotted.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            return False, None, f"配置项 {dotted} 不存在"
    return True, cur, ""


def _set_nested(data: dict[str, Any], dotted: str, value: Any) -> list[str]:
    """按 dot 路径写入值，沿途创建嵌套字典。返回缺失的祖先链（供报错）。"""
    parts = dotted.split(".")
    cur = data
    for part in parts[:-1]:
        nxt = cur.get(part)
        if not isinstance(nxt, dict):
            nxt = {}
            cur[part] = nxt
        cur = nxt
    cur[parts[-1]] = value
    return []


def _load_or_init(path: Path) -> dict[str, Any]:
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {}


def _write(path: Path, data: dict[str, Any]) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


@app.command("show")
def show(
    config: Path | None = typer.Option(None, "--config", "-c", help="配置文件路径"),
) -> None:
    """查看当前配置（密钥自动打码）。"""
    cfg = load_config(config or default_config_path())
    ui.heading("当前配置")
    models = cfg.models
    if models is None:
        ui.warn("尚未配置 models 块。请运行 `kuro init`。")
    rows = [
        ("名称", cfg.name),
        ("版本", cfg.version),
        ("工作区", cfg.workspace),
        ("日志级别", cfg.log_level),
        ("内部服务", f"{cfg.server.host}:{cfg.server.port}"),
    ]
    ui.kv_table("基本信息", rows)
    if models is not None:
        spec_rows = []
        for field in ("llm", "vision", "embedding", "reranker", "layout"):
            spec = getattr(models, field)
            if spec is None:
                spec_rows.append((field, "[dim]未配置[/dim]"))
            else:
                key = f"{spec.api_key[:4]}****" if spec.api_key else "[dim]无密钥[/dim]"
                spec_rows.append((field, f"{spec.provider} / {spec.model}  [dim]{key}[/dim]"))
        ui.kv_table("模型 Provider", spec_rows)
    ui.kv_table(
        "权限",
        [
            ("shell", cfg.permissions.shell),
            ("file_access", cfg.permissions.file_access),
            ("端点白名单", ", ".join(cfg.permissions.model_endpoints) or "[dim]空[/dim]"),
        ],
    )
    ui.info(f"配置文件：{default_config_path()}")


@app.command("get")
def get(
    key: str = typer.Argument(..., help="配置项，用点分路径，如 models.llm.provider"),
    config: Path | None = typer.Option(None, "--config", "-c"),
) -> None:
    """读取单个配置值。"""
    raw = _load_or_init(config or default_config_path())
    found, value, msg = _get_nested(raw, key)
    if not found:
        ui.err(msg)
        ui.info("可用 `kuro config show` 查看配置结构。")
        raise typer.Exit(1)
    if isinstance(value, (dict, list)):
        ui.info(json.dumps(value, ensure_ascii=False, indent=2))
    else:
        ui.info(f"{key} = [bold]{value}[/bold]")


@app.command("set")
def set_(
    key: str = typer.Argument(..., help="配置项，点分路径，如 models.llm.api_key"),
    value: str = typer.Argument(..., help="配置值（会自动识别 bool/int/float/字符串，支持中文）"),
    config: Path | None = typer.Option(None, "--config", "-c"),
) -> None:
    """修改配置（校验后写盘）。"""
    path = config or default_config_path()
    raw = _load_or_init(path)
    _set_nested(raw, key, _coerce(value))
    # 校验：能否构造出合法配置
    try:
        from kurotutor.config.loader import load_config_from_data, project_root_from_config_path

        root = project_root_from_config_path(path)
        load_config_from_data(raw, project_root=root)
    except Exception as exc:
        ui.err(f"配置写入被拒绝：{exc}")
        ui.info("请检查 key 路径与取值类型是否合法。")
        raise typer.Exit(1) from exc
    _write(path, raw)
    ui.ok(f"已更新 {key} = {value}  →  {path}")


@app.command("validate")
def validate(config: Path | None = typer.Option(None, "--config", "-c")) -> None:
    """校验配置完整性。"""
    path = config or default_config_path()
    if not path.exists():
        ui.warn("未找到配置文件，当前按默认值校验。")
    ui.heading("配置校验")
    cfg = load_config(path)
    issues = cfg.model_validate_extra()
    # 检测占位符（示例配置文件里的「请填写」）
    for field in ("llm", "vision", "embedding", "reranker", "layout"):
        spec = getattr(cfg.models, field, None) if cfg.models else None
        if spec is None:
            continue
        for attr in ("api_key", "model"):
            val = getattr(spec, attr, "")
            if isinstance(val, str) and ("请填写" in val or "请填写密钥" in val):
                issues.append(
                    ValidationIssue(field=f"models.{field}.{attr}", message=f"{attr} 仍是占位符示例")
                )
    if not issues:
        ui.ok("配置完整，可启动。")
        ui.info("提示：`kuro config validate` 只校验配置；`kuro doctor` 还会检查数据库与渠道连接。")
        return
    for iss in issues:
        ui.err(f"  {iss.field}: {iss.message}")
    ui.warn(f"共 {len(issues)} 项待处理。运行 `kuro init` 或 `kuro config set` 补全。")


@app.command("init")
def config_init(config: Path | None = typer.Option(None, "--config", "-c")) -> None:
    """（别名）交互式初始化配置。"""
    from .init import run_init

    run_init(config)
