"""Agent 沙箱（自动单模式）。

架构红线第 2 条：权限单一自动放行模式，仅两道硬约束——
1. 所有文件操作限定工作区（路径校验防穿越 + 软链逃逸）。
2. 禁止修改系统设置（系统文件/配置/服务/包管理）。

本模块提供两类校验：
- :meth:`Sandbox.resolve_path` —— 把相对路径安全解析到工作区绝对路径，
  拦截 ``..`` 穿越与软链逃逸。
- :meth:`Sandbox.check_command` / :meth:`Sandbox.check_endpoint` —— 命令白名单 +
  系统级命令黑名单、API 端点白名单。

安全原则：默认最保守；规则收口到一处；任何歧义宁可拒绝。
"""

from __future__ import annotations

from pathlib import Path

from kurotutor.config.schema import AppConfig
from kurotutor.core.errors import SandboxError

# 无论 shell 级别为何，一律禁止的命令（系统级破坏/改配置/装包/管理服务）
_SYSTEM_BLACKLIST = {
    "shutdown",
    "reboot",
    "restart",
    "halt",
    "poweroff",
    "format",
    "mkfs",
    "regedit",
    "reg",
    "taskkill",
    "kill",
    "pkill",
    "systemctl",
    "service",
    "dnf",
    "yum",
    "apt",
    "apt-get",
    "brew",
    "pip",
    "pip3",
    "pipx",
    "npm",
    "gem",
    "chmod",
    "chown",
    "chgrp",
    "mount",
    "umount",
    "swapon",
    "swapoff",
    "dd",
    "mkfs.ext4",
    "fsck",
    "parted",
    "fdisk",
    "del",
    "rmdir",
    "rm",
    "mv",
    "net",
    "netsh",
    "sc",
    "bcdedit",
    "diskpart",
    "vssadmin",
    "wmic",
}
# 系统敏感路径（禁写），即便 file_access=allow 也拒之门外
_SYSTEM_PATHS = (
    "C:\\Windows",
    "C:\\Program Files",
    "C:\\ProgramData",
    "/etc",
    "/usr",
    "/bin",
    "/sbin",
    "/boot",
    "/root",
    "/var",
    "/sys",
    "/proc",
)

# Windows 下常见 shell 别名 → 归一化后的真实命令名
_ALIAS_MAP = {"cmd": "cmd.exe", "copy": "cp", "move": "mv", "erase": "del", "rd": "rmdir"}


def _norm_command_name(token: str) -> str:
    """把命令 token 归一化为可匹配黑名单的名字（去路径、去后缀）。"""
    name = token.replace("\\", "/").split("/")[-1].lower()
    name = _ALIAS_MAP.get(name, name)
    for suffix in (".exe", ".bat", ".cmd", ".ps1", ".sh"):
        if name.endswith(suffix):
            name = name[: -len(suffix)]
            break
    return name


class Sandbox:
    """沙箱策略对象。持有配置与工作区根目录。"""

    def __init__(self, config: AppConfig, student_id: int | None = None):
        self._config = config
        self.workspace = Path(config.workspace).resolve()
        self.workspace.mkdir(parents=True, exist_ok=True)
        # 学生子作用域：多用户文件隔离（每个学生的产物互相不可见/不覆盖）
        # student=None 时（无学生上下文，如 CLI 运维）退化为全局工作区
        self.student_scope = f"u{student_id}" if student_id else ""

    def student_path(self, rel: str | Path, *, for_write: bool = False) -> Path:
        """解析到当前学生的子目录（workspace/u<id>/...）。

        边界收紧到学生目录本身：学生 A 无法通过 ``../`` 写到学生 B 的目录。
        无学生上下文时退化为全局工作区路径。
        """
        root = (self.workspace / self.student_scope) if self.student_scope else self.workspace
        resolved = self.resolve_path(root / Path(rel), for_write=for_write)
        if self.student_scope and not self._is_within(resolved, root):
            raise SandboxError(
                "文件操作越出学生工作区",
                cause=f"路径 {resolved} 超出本学生的目录 {root}",
                fix="所有文件操作限定在自己学生的目录内",
            )
        return resolved

    # ---- 文件路径校验 -------------------------------------------------------

    def resolve_path(self, raw_path: str | Path, *, for_write: bool = False) -> Path:
        """把相对/绝对路径解析为受限的绝对路径。

        默认（workspace_only）限定在 :attr:`workspace` 内；``for_write`` 且
        只读权限时拒绝写入。任何越界/软链逃逸抛 :class:`SandboxError`。
        """
        p = Path(raw_path).expanduser()
        if not p.is_absolute():
            p = self.workspace / p
        # 先做词法穿越检查（快速失败）
        try:
            resolved = p.resolve(strict=False)
        except OSError as exc:
            raise SandboxError("路径解析失败", cause=str(exc), fix="检查路径是否合法") from exc

        mode = self._config.permissions.file_access
        if mode == "allow":
            # 允许任意路径，但仍禁止系统敏感目录
            if self._hits_system_path(resolved):
                raise SandboxError(
                    "禁止修改系统设置",
                    cause=f"目标路径位于系统目录：{resolved}",
                    fix="仅允许在工作区或数据目录内操作文件",
                )
            return resolved

        # workspace_only / readonly：必须落在工作区内（拦截软链逃逸）
        if not self._is_within(resolved, self.workspace):
            raise SandboxError(
                "文件操作越出工作区",
                cause=f"路径 {resolved} 不在工作区 {self.workspace} 内",
                fix="所有文件操作限定在配置的 workspace 目录内",
            )
        if for_write and mode == "readonly":
            raise SandboxError(
                "当前为只读模式，禁止写入",
                cause=f"写入路径 {resolved}",
                fix="在 kuro.json 中把 permissions.file_access 改为 workspace_only 后再写",
            )
        return resolved

    # ---- 命令校验 -----------------------------------------------------------

    def check_command(self, command: str) -> tuple[bool, str]:
        """校验一条命令是否允许执行。返回 (允许, 原因)。"""
        shell = self._config.permissions.shell
        stripped = command.strip()
        if not stripped:
            return True, "空命令，忽略"
        first_token = stripped.split()[0]
        name = _norm_command_name(first_token)

        # 系统级命令：任何 shell 级别都拒绝
        if name in _SYSTEM_BLACKLIST:
            return False, f"命令 {name} 属系统级操作，已列入黑名单"

        if shell == "deny":
            return False, "shell 权限为 deny，默认拒绝一切命令执行"
        if shell == "allow":
            return True, "shell 权限为 allow，系统级黑名单之外放行"
        if shell == "whitelist":
            allowed = {_norm_command_name(c) for c in self._config.permissions.allowed_commands}
            if name in allowed:
                return True, f"命令 {name} 在白名单内"
            return False, f"命令 {name} 不在白名单（allowed_commands）内"
        return False, f"未知的 shell 权限：{shell}"

    # ---- API 端点校验 -------------------------------------------------------

    def check_endpoint(self, url: str) -> bool:
        """URL 是否命中配置的 model_endpoints 白名单（允许子域名/子路径）。"""
        allowed = self._config.permissions.model_endpoints
        if not allowed:
            return False
        for base in allowed:
            base = base.rstrip("/")
            if url == base or url.startswith(base + "/"):
                return True
        return False

    # ---- 辅助 ---------------------------------------------------------------

    @staticmethod
    def _is_within(path: Path, root: Path) -> bool:
        try:
            path.relative_to(root)
            return True
        except ValueError:
            return False

    @staticmethod
    def _hits_system_path(path: Path) -> bool:
        text = str(path).lower()
        return any(text.startswith(sp.lower()) for sp in _SYSTEM_PATHS)
