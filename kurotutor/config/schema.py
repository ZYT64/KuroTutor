"""KuroTutor 配置模型（Schema）。

本模块只定义配置的数据结构与校验规则，不负责读取文件。
读取与默认值合并逻辑见 :mod:`kurotutor.config.loader`。

设计要点：
- Provider 全部可插拔：每个模型块由 ``provider`` + ``model`` 标识，密钥可选。
- 未知字段宽松：``model_config = ConfigDict(extra="allow")`` ，
  便于各 Provider 携带自身特有的参数而无需改动本 Schema。
- 所有敏感字段集中在一个地方，方便统一脱敏（见 loader.redact）。
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

# 允许的沙箱 shell 权限级别
ShellAccess = Literal["deny", "whitelist", "allow"]
# 允许的文件访问范围
FileAccess = Literal["workspace_only", "readonly", "allow"]
# 日志级别
LogLevel = Literal["debug", "info", "warning", "error"]


class ModelSpec(BaseModel):
    """单个模型/服务的 Provider 配置块。

    ``provider`` 决定走哪个适配器（如 openai、dashscope、echo、mock）；
    ``model`` 是具体模型名；``base_url`` 覆盖默认接入地址（如火山/阿里的 OpenAI 兼容端点）。
    """

    model_config = ConfigDict(extra="allow")

    provider: str
    model: str
    api_key: str | None = None
    base_url: str | None = Field(default=None, description="OpenAI 兼容端点；缺省用 Provider 默认")


class ModelsConfig(BaseModel):
    """所有可插拔模型的聚合配置块。"""

    model_config = ConfigDict(extra="allow")

    llm: ModelSpec
    vision: ModelSpec | None = None
    transcriber: ModelSpec | None = None
    embedding: ModelSpec | None = None
    reranker: ModelSpec | None = None
    layout: ModelSpec | None = None  # 版面分析（题集切题用），默认 RapidOCR 本地
    search: ModelSpec | None = None  # 网络搜索：provider=bing（默认，免密钥）/ tavily（需 api_key）


class ChannelConfig(BaseModel):
    """渠道接入配置（当前仅 QQ 私聊）。"""

    model_config = ConfigDict(extra="allow")

    app_id: str = ""
    secret: str = ""


class PermissionsConfig(BaseModel):
    """Agent 沙箱权限配置（自动单模式）。

    默认即最保守值：shell 拒绝，文件仅限工作区。
    """

    model_config = ConfigDict(extra="allow")

    shell: ShellAccess = "deny"
    file_access: FileAccess = "workspace_only"
    model_endpoints: list[str] = Field(default_factory=list)
    # shell=whitelist 时允许执行的命令（首 token 精确匹配）
    allowed_commands: list[str] = Field(default_factory=list)


class KbConfig(BaseModel):
    """知识库底层配置。"""

    model_config = ConfigDict(extra="allow")

    vector_store: str = "milvus"  # 占位，默认值会在实现向量库时落地为可选实现
    path: str = "data/kb"


class PathsConfig(BaseModel):
    """技能与插件目录。默认相对项目根目录解析。"""

    model_config = ConfigDict(extra="allow")

    skills_dir: str = "skills"
    plugins_dir: str = "plugins"


class ServerConfig(BaseModel):
    """内部服务（FastAPI）配置，供渠道长连接与健康检查使用。"""

    model_config = ConfigDict(extra="allow")

    host: str = "0.0.0.0"
    port: int = 8000


class AppConfig(BaseModel):
    """顶层配置。"""

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    name: str = "kurotutor"
    version: str = "0.1.0"
    # 工作区根目录：Agent 一切文件操作的唯一允许范围（硬约束①）
    workspace: str = "data/workspaces"
    data_dir: str = "data"

    channel: ChannelConfig = Field(default_factory=ChannelConfig)
    models: ModelsConfig | None = None
    permissions: PermissionsConfig = Field(default_factory=PermissionsConfig)
    kb: KbConfig = Field(default_factory=KbConfig)
    paths: PathsConfig = Field(default_factory=PathsConfig)
    server: ServerConfig = Field(default_factory=ServerConfig)

    log_level: LogLevel = "info"

    def model_validate_extra(self) -> list[ValidationIssue]:
        """返回配置中的缺失/异常项列表，供 ``kuro config validate`` 使用。"""
        issues: list[ValidationIssue] = []
        if self.models is None:
            issues.append(ValidationIssue(field="models", message="缺失 models 配置块，无法驱动 Agent"))
            return issues
        if self.models.llm is None:
            issues.append(ValidationIssue(field="models.llm", message="缺失文本模型配置"))
        elif self.models.llm.provider.lower() not in ("echo", "mock") and not self.models.llm.api_key:
            # 真实在线模型才需要密钥；echo/mock 为离线联调，无需密钥
            issues.append(
                ValidationIssue(field="models.llm.api_key", message="未配置文本模型 API 密钥（BYOK）")
            )
        if self.models.vision is None:
            issues.append(
                ValidationIssue(field="models.vision", message="未配置视觉模型（拍照解题/批改将不可用）")
            )
        if self.channel and (not self.channel.app_id or not self.channel.secret):
            issues.append(ValidationIssue(field="channel", message="未配置 QQ 渠道 app_id / secret"))
        return issues


class ValidationIssue(BaseModel):
    """配置校验的单项问题。"""

    field: str
    message: str
