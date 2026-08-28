"""统一错误类型体系。

遵守项目宪法「工程防御规范」：禁止直接抛出原生异常。
所有意料内的失败都抛出 :class:`KuroError` 及其子类，错误信息包含
「现象 + 原因 + 可操作的修复建议」，便于 Agent 与 CLI 直接呈现给用户。
"""

from __future__ import annotations


class KuroError(Exception):
    """KuroTutor 所有错误的基类。"""

    code = "kuro_error"

    def __init__(self, message: str, *, cause: str = "", fix: str = ""):
        super().__init__(message)
        self.message = message
        self.cause = cause
        self.fix = fix

    def __str__(self) -> str:  # pragma: no cover - 仅在渲染时用
        text = self.message
        if self.cause:
            text += f"（原因：{self.cause}）"
        if self.fix:
            text += f"（建议：{self.fix}）"
        return text


class ConfigError(KuroError):
    code = "config_error"


class ProviderError(KuroError):
    """模型/服务调用失败（网络、鉴权、配额、超时）。"""

    code = "provider_error"


class SandboxError(KuroError):
    """违反沙箱硬约束（越界文件访问 / 企图改系统设置）。"""

    code = "sandbox_error"


class ToolError(KuroError):
    """工具执行失败。Agent 循环捕获后回填给模型，不中断会话。"""

    code = "tool_error"


class ChannelError(KuroError):
    """渠道接入/心跳/收发失败。"""

    code = "channel_error"


class StorageError(KuroError):
    """数据库读写失败。"""

    code = "storage_error"
