"""CLI 运行时装载：配置 + 引擎 + 注册表。

所有需要访问数据或启动服务（agent / serve / student / kb / doctor）的命令，
都通过 :func:`load_runtime` 拿到一套就绪的运行时对象；缺配置时给友好提示而非崩溃。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from kurotutor.config import AppConfig, load_config
from kurotutor.core.errors import KuroError
from kurotutor.storage import build_db_url, build_engine, init_db
from kurotutor.tools import build_default_registry

from . import ui


@dataclass
class Runtime:
    """一次 CLI 调用所需的运行时全套。"""

    config: AppConfig
    engine: object
    registry: object


def find_config(config_path: Path | None) -> Path:
    """确定配置路径：CLI 指定 > 环境变量 > 默认（kuro.json）。"""
    if config_path is not None:
        return config_path
    from kurotutor.config.loader import default_config_path

    return default_config_path()


def load_runtime(config_path: Path | None = None, *, require_models: bool = False) -> Runtime:
    """装载配置、初始化数据层、构建工具注册表。"""
    path = find_config(config_path)
    try:
        config = load_config(path)
    except KuroError as exc:
        ui.err(str(exc))
        raise SystemExit(1) from exc

    db_url = build_db_url(config.data_dir)
    engine = build_engine(db_url)
    init_db(engine)
    registry = build_default_registry()

    if require_models and (config.models is None or config.models.llm is None):
        ui.warn(
            "当前配置缺失文本模型，Agent 无法真正驱动。请先运行 `kuro init` 或 `kuro config set` 补全配置。"
        )
        for iss in config.model_validate_extra():
            if iss.field.startswith("models"):
                ui.err(f"  {iss.field}: {iss.message}")
        # echo/mock 为离线联调，无需密钥即可跑通链路
        ui.info("本地联调可用 echo 模型跑通链路：`kuro config set models.llm.provider echo`")
    elif (
        require_models
        and config.models
        and config.models.llm
        and not config.models.llm.api_key
        and config.models.llm.provider.lower() not in ("echo", "mock")
    ):
        ui.warn("当前文本模型已选，但未配置 API 密钥，在线调用会失败。")
        ui.info("用 `kuro config set models.llm.api_key <你的密钥>` 补齐。")
    return Runtime(config=config, engine=engine, registry=registry)
