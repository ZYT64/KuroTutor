"""kuro upgrade —— 检查并更新到最新版本。

流程：拉取最新代码 → 显示版本变化 → （--yes 时）重建镜像并滚动重启 → 健康检查。
容器环境外仅做 git 拉取与提示；镜像重建走 compose。
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import typer

from kurotutor import __version__

from . import ui


def _run(cmd: list[str], cwd: Path | None = None) -> tuple[int, str]:
    r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    return r.returncode, (r.stdout + r.stderr).strip()


def _repo_root() -> Path | None:
    """从当前目录向上找含 .git 与 compose.yaml 的项目根。"""
    for p in [Path.cwd(), *Path.cwd().parents]:
        if (p / ".git").exists() and (p / "compose.yaml").exists():
            return p
    return None


def upgrade_command(
    yes: bool = typer.Option(False, "--yes", "-y", help="跳过确认直接更新"),
    check_only: bool = typer.Option(False, "--check", help="只检查远端是否有新版本，不更新"),
) -> None:
    """更新 KuroTutor 到最新版本（git 拉取 + 容器重建重启）。"""
    root = _repo_root()
    if root is None:
        ui.fail("未找到项目根目录（需要 .git 与 compose.yaml）。请在部署目录内执行 kuro upgrade。")
        raise typer.Exit(1)

    ui.heading("KuroTutor 更新检查")
    code, out = _run(["git", "fetch", "origin"], cwd=root)
    if code != 0:
        ui.fail(f"拉取远端信息失败：{out[:200]}")
        raise typer.Exit(1)
    code, behind_out = _run(["git", "rev-list", "--count", "HEAD..origin/main"], cwd=root)
    behind = int(behind_out.strip() or "0") if code == 0 else 0

    if behind == 0:
        ui.ok(f"已是最新版本（v{__version__}）。")
        return
    ui.info(f"当前 v{__version__}，远端有 {behind} 个新提交。")

    if check_only:
        ui.info("仅检查模式，未执行更新。运行 kuro upgrade 执行更新。")
        return
    if not yes:
        confirm = typer.confirm(f"拉取 {behind} 个新提交并重建容器？")
        if not confirm:
            ui.info("已取消。")
            return

    code, out = _run(["git", "pull", "--ff-only", "origin", "main"], cwd=root)
    if code != 0:
        ui.fail(f"代码拉取失败（本地有改动时请先处理）：{out[:300]}")
        raise typer.Exit(1)
    ui.ok("代码已更新。")

    docker = _run(["docker", "--version"])[0] == 0
    if not docker:
        ui.warn("未检测到 Docker，代码已更新；请手动重启服务。")
        return

    ui.info("重建镜像并重启服务（可能需要几分钟）……")
    code, out = _run(["docker", "compose", "up", "-d", "--build"], cwd=root)
    if code != 0:
        ui.fail(f"容器重建失败：{out[-400:]}")
        raise typer.Exit(1)
    ui.ok("服务已用新版本重启。运行 kuro doctor 做健康检查。")
