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
    qbank: ModelSpec | None = None  # 在线题库：huohua（火花 K12 题库 token，¥5/100 次）


class ChannelConfig(BaseModel):
    """渠道接入配置（当前仅 QQ 私聊）。"""

    model_config = ConfigDict(extra="allow")

    app_id: str = ""
    secret: str = ""


class OCRConfig(BaseModel):
    """文档 OCR 识别链（文档工具用）：按顺序尝试，任一成功即返回。

    chain 默认「百度 → 腾讯 → 本地」，支持配置调整顺序或裁剪。
    baidu/tencent 凭据未填时自动跳过该引擎；local 免费无限。
    """

    model_config = ConfigDict(extra="allow")

    chain: list[str] = Field(default_factory=lambda: ["baidu", "tencent", "local"])
    baidu_api_key: str = ""
    baidu_secret_key: str = ""
    tencent_secret_id: str = ""
    tencent_secret_key: str = ""
    mineru_token: str = ""  # MinerU 官方 API 令牌（专用解析工具用）


class BackupConfig(BaseModel):
    """云端备份（可选）：加密后推送 Gitee 私有仓库。

    gitee_repo 填『用户名/仓库名』；token 为带 repo 权限的私人令牌。
    encrypt_password 是唯一解密凭据——丢失后云端备份永久无法恢复。
    不配置 gitee_repo 时云端备份禁用，仅保留本地备份。
    """

    model_config = ConfigDict(extra="allow")

    gitee_repo: str = ""  # 如 zyt/kurotutor-backup
    gitee_user: str = ""  # Gitee 用户名
    gitee_token: str = ""  # 私人令牌（repo 权限）
    encrypt_password: str = ""  # 加密口令（恢复时必需）
    auto_enabled: bool = True  # 自动备份开关（需先配置 Gitee）
    auto_interval_days: int = 1  # 自动备份频率：每 N 天一次（1=每天）


class WebUIConfig(BaseModel):
    """WebUI 管理面板：只读学情查看（口令认证，默认仅局域网）。"""

    model_config = ConfigDict(extra="allow")

    enabled: bool = True
    token: str = ""  # 登录口令；为空表示禁用面板
    host: str = "0.0.0.0"
    port: int = 8001


class RetentionConfig(BaseModel):
    """数据保留策略（遗忘机制）：超期消息/已完成任务自动清理。"""

    model_config = ConfigDict(extra="allow")

    message_days: int = 180  # 会话消息保留天数（下限 30）
    task_days: int = 90  # 已完成调度任务保留天数（下限 14）
    enabled: bool = True


class OpenMAICConfig(BaseModel):
    """OpenMAIC 互动课堂（THU-MAIC/OpenMAIC）接入配置。

    托管模式：base_url 固定 open.maic.chat，access_code 为官网生成的 sk- 访问码。
    """

    model_config = ConfigDict(extra="allow")

    base_url: str = "https://open.maic.chat"
    access_code: str = ""


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

    name: str = "KuroTutor"
    version: str = "0.1.0"
    # 工作区根目录：Agent 一切文件操作的唯一允许范围（硬约束①）
    workspace: str = "data/workspaces"
    data_dir: str = "data"

    channel: ChannelConfig = Field(default_factory=ChannelConfig)
    models: ModelsConfig | None = None
    openmaic: OpenMAICConfig = Field(default_factory=OpenMAICConfig)
    retention: RetentionConfig = Field(default_factory=RetentionConfig)
    webui: WebUIConfig = Field(default_factory=WebUIConfig)
    backup: BackupConfig = Field(default_factory=BackupConfig)
    ocr: OCRConfig = Field(default_factory=OCRConfig)
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
