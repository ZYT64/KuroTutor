"""配置加载器：文件读取 → 默认值合并 → 环境变量覆盖 → 校验。

优先级（从高到低，符合项目宪法「配置分层」）：环境变量 > 配置文件 > 代码默认值。
- 代码默认值：由 :class:`kurotutor.config.schema.AppConfig` 各字段的默认值提供。
- 配置文件：默认找 ``./kuro.json``（可用环境变量 ``KURO_CONFIG`` 或 CLI ``--config`` 覆盖）。
- 环境变量：以 ``KURO_`` 开头、用 ``__`` 表示嵌套路径，例如
  ``KURO_MODELS__LLM__API_KEY`` → ``models.llm.api_key``。

相对路径（workspace / kb.path / paths.*）统一相对「项目根目录」解析。
项目根目录 = 配置文件所在目录；找不到配置文件时 = 当前工作目录。
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from kurotutor.config.schema import AppConfig

# 允许从环境变量覆盖的叶子字段（安全白名单，点分 + 大写形式）。
# 其他字段一律以配置文件为准，避免任意环境变量影响配置结构。
_ENV_ALLOWED = {
    "CHANNEL.APP_ID",
    "CHANNEL.SECRET",
    "MODELS.LLM.API_KEY",
    "MODELS.LLM.MODEL",
    "MODELS.LLM.PROVIDER",
    "MODELS.LLM.BASE_URL",
    "MODELS.VISION.API_KEY",
    "MODELS.VISION.MODEL",
    "MODELS.EMBEDDING.API_KEY",
    "MODELS.EMBEDDING.MODEL",
    "MODELS.ASR.API_KEY",
    "MODELS.ASR.MODEL",
    "MODELS.TTS.API_KEY",
    "MODELS.TTS.MODEL",
    "MODELS.RERANKER.API_KEY",
    "MODELS.RERANKER.MODEL",
    "LOG.LEVEL",
    "LOG_LEVEL",
}


def default_config_path(cwd: Path | None = None) -> Path:
    """返回默认配置文件路径（不存在也返回，供调用方判断）。"""
    cwd = cwd or Path.cwd()
    env_path = os.environ.get("KURO_CONFIG")
    if env_path:
        return Path(env_path).expanduser()
    for name in ("kuro.json", "config.json"):
        candidate = cwd / name
        if candidate.exists():
            return candidate
    return cwd / "kuro.json"


def _resolve_env_overrides(env: dict[str, str]) -> dict[str, Any]:
    """把 ``KURO_*`` 环境变量映射为嵌套字典。仅接受白名单内的叶子字段。"""
    out: dict[str, Any] = {}
    for key, value in env.items():
        if not key.startswith("KURO_"):
            continue
        dotted = key[len("KURO_") :].replace("__", ".").lower()
        if dotted.upper() not in _ENV_ALLOWED:
            continue
        cursor = out
        parts = dotted.split(".")
        for part in parts[:-1]:
            cursor = cursor.setdefault(part, {})
        cursor[parts[-1]] = value  # 环境变量一律当作字符串
    return out


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """把 override 递归合并进 base，返回新字典（不修改入参）。"""
    result = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def _resolve_data_paths(project_root: Path, cfg: AppConfig) -> AppConfig:
    """把配置里相对路径解析为绝对路径，便于沙箱与存储层直接使用。"""

    def resolve(p: str) -> str:
        path = Path(p)
        if not path.is_absolute():
            path = project_root / path
        return str(path)

    cfg.workspace = resolve(cfg.workspace)
    cfg.data_dir = resolve(cfg.data_dir)
    cfg.kb.path = resolve(cfg.kb.path)
    cfg.paths.skills_dir = resolve(cfg.paths.skills_dir)
    cfg.paths.plugins_dir = resolve(cfg.paths.plugins_dir)
    return cfg


def load_config_from_data(data: dict[str, Any], *, project_root: Path | None = None) -> AppConfig:
    """从已解析的字典构造配置（含默认值合并、环境变量覆盖、相对路径解析）。"""
    root = project_root or Path.cwd()
    merged = _deep_merge(AppConfig().model_dump(), data)
    merged = _deep_merge(merged, _resolve_env_overrides(dict(os.environ)))
    cfg = AppConfig.model_validate(merged)
    return _resolve_data_paths(root, cfg)


def load_config_from_file(path: str | Path, *, project_root: Path | None = None) -> AppConfig:
    """从指定 JSON 文件读取配置。"""
    config_path = Path(path).expanduser()
    raw = json.loads(config_path.read_text(encoding="utf-8")) if config_path.exists() else {}
    root = project_root or config_path.parent
    return load_config_from_data(raw, project_root=root)


def load_config(path: str | Path | None = None, *, project_root: Path | None = None) -> AppConfig:
    """按常规渠道加载配置。

    ``path`` 缺省时走 :func:`default_config_path`；文件不存在则用默认值构造。
    """
    config_path = Path(path) if path else default_config_path()
    if not config_path.exists():
        # 无配置文件时：用环境变量 + 默认值构造（保证任何命令都不因缺配置而崩溃）
        return load_config_from_data({}, project_root=project_root)
    return load_config_from_file(config_path, project_root=project_root)


def redact(cfg: AppConfig) -> AppConfig:
    """返回脱敏后的配置副本，用于 ``kuro config show``。

    脱敏规则：api_key / secret 只保留前 4 位，其余打码。
    """
    redacted = cfg.model_copy(deep=True)

    def mask(value: str | None) -> str | None:
        if not value:
            return value
        if len(value) <= 4:
            return "****"
        return f"{value[:4]}****"

    redacted.channel.secret = mask(redacted.channel.secret)
    if redacted.models is not None:
        for field_name in ("llm", "vision", "transcriber", "embedding", "reranker", "layout"):
            spec = getattr(redacted.models, field_name)
            if spec is not None and spec.api_key:
                spec.api_key = mask(spec.api_key)
    return redacted


def project_root_from_config_path(path: str | Path | None) -> Path:
    """根据配置文件路径推出项目根目录（配置文件所在目录）。"""
    if path:
        return Path(path).expanduser().parent
    config_path = default_config_path()
    return config_path.parent
