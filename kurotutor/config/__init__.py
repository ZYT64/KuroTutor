"""配置系统：Schema + 加载器。"""

from kurotutor.config.loader import (
    default_config_path,
    load_config,
    load_config_from_file,
    redact,
)
from kurotutor.config.schema import (
    AppConfig,
    ChannelConfig,
    KbConfig,
    ModelsConfig,
    ModelSpec,
    PermissionsConfig,
    ValidationIssue,
)

__all__ = [
    "AppConfig",
    "ChannelConfig",
    "KbConfig",
    "ModelSpec",
    "ModelsConfig",
    "PermissionsConfig",
    "ValidationIssue",
    "default_config_path",
    "load_config",
    "load_config_from_file",
    "redact",
]
