"""KuroTutor —— QQ 私聊里的 24 小时 AI 私人老师。

对外暴露版本号与顶层配置类型，便于 ``kuro version`` 与外部集成引用。
版本号单一来源是 pyproject.toml，此处从已安装包的元数据读取。
"""

from importlib import metadata

try:
    __version__ = metadata.version("KuroTutor")
except metadata.PackageNotFoundError:  # 未安装（源码直接引用）时的兜底
    __version__ = "0.0.0+dev"

__app_name__ = "KuroTutor"

from kurotutor.config.schema import AppConfig, ModelSpec  # noqa: E402

__all__ = ["__version__", "__app_name__", "AppConfig", "ModelSpec"]
