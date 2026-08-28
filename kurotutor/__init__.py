"""KuroTutor —— QQ 私聊里的 24 小时 AI 私人老师。

对外暴露版本号与顶层配置类型，便于 ``kuro version`` 与外部集成引用。
"""

__version__ = "0.1.0"
__app_name__ = "kurotutor"

from kurotutor.config.schema import AppConfig, ModelSpec  # noqa: E402

__all__ = ["__version__", "__app_name__", "AppConfig", "ModelSpec"]
