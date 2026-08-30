"""kuro upgrade —— 一条命令更新到最新版本。

自动识别运行环境：
- 容器内（docker compose run --rm cli kuro upgrade）：拉代码 → 自带 venv 重建 wheel →
  通过挂载的 docker 套接字重建镜像并滚动重启；
- 宿主机（源码 + 虚拟环境）：拉代码 → 项目 venv 重建 wheel → docker compose 重建。
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import typer

from kurotutor import __version__

from . import ui

_PIP_MIRROR = "https://mirrors.tuna.tsinghua.edu.cn/pypi/web/simple"


def _run(cmd: list[str], cwd: Path | None = None) -> tuple[int, str]:
    r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    return r.returncode, (r.stdout + r.stderr).strip()


def _repo_root() -> Path | None:
    """定位项目根（含 .git 与 compose.yaml）。容器内固定在 /app/project。"""
    candidates = [Path("/app/project")] if Path("/app/project/.git").exists() else []
    host_roots = [
        p
        for p in [Path.cwd(), *Path.cwd().parents]
        if (p / ".git").exists() and (p / "compose.yaml").exists()
    ]
    candidates += host_roots
    return candidates[0] if candidates else None


def _in_container() -> bool:
    return Path("/app/project/.git").exists() and sys.prefix.startswith("/")


def _rebuild_wheel(root: Path) -> None:
    """重建 wheel：清掉旧包，容器内用自带 venv，宿主机用项目 venv。"""
    dist = root / "dist"
    dist.mkdir(exist_ok=True)
    for old in dist.glob("*.whl"):
        old.unlink()
    # 清掉旧 egg-info 与 build/：本地安装/历史构建残留会让容器内 setuptools 报权限/时间戳错误
    import shutil as _shutil

    for stale in ("build", *(str(p) for p in root.glob("*.egg-info"))):
        _shutil.rmtree(root / stale, ignore_errors=True)
    if _in_container():
        cmd = [sys.executable, "-m", "pip", "wheel", "--no-deps", "-w", "dist", ".", "-i", _PIP_MIRROR]
        code, out = _run(cmd, cwd=root)
    else:
        py = root / ".venv" / "Scripts" / "python.exe"
        if not py.exists():
            py = root / ".venv" / "bin" / "python"
        if not py.exists():
            raise RuntimeError(
                "未找到 .venv 虚拟环境（宿主机部署请先 python -m venv .venv && pip install -e .）"
            )
        cmd = [str(py), "-m", "pip", "wheel", "--no-deps", "-w", "dist", "."]
        code, out = _run(cmd, cwd=root)
    if code != 0:
        raise RuntimeError(f"wheel 构建失败：{out[-300:]}")


def upgrade_command(
    yes: bool = typer.Option(False, "--yes", "-y", help="跳过确认直接更新"),
    check_only: bool = typer.Option(False, "--check", help="只检查远端是否有新版本，不更新"),
) -> None:
    """更新 KuroTutor 到最新版本（拉代码 → 重建 wheel 与镜像 → 滚动重启）。"""
    root = _repo_root()
    if root is None:
        ui.err("未找到项目根目录（需要 .git 与 compose.yaml）。请在部署目录或 cli 容器内执行 kuro upgrade。")
        raise typer.Exit(1)

    ui.heading("KuroTutor 更新检查")
    # 容器内 root 操作宿主挂载的仓库时 git 会拒绝（dubious ownership），加白名单
    _run(["git", "config", "--global", "--add", "safe.directory", str(root)])
    code, out = _run(["git", "fetch", "origin"], cwd=root)
    if code != 0:
        ui.err(f"拉取远端信息失败：{out[:200]}")
        raise typer.Exit(1)
    code, behind_out = _run(["git", "rev-list", "--count", "HEAD..origin/main"], cwd=root)
    behind = int(behind_out.strip() or "0") if code == 0 else 0

    if behind == 0:
        ui.ok(f"已是最新版本（v{__version__}）。")
        return
    ui.info(f"当前 v{__version__}，远端有 {behind} 个新提交。")

    if check_only:
        ui.info("仅检查模式，未执行更新。去掉 --check 或运行 kuro upgrade 执行更新。")
        return
    if not yes and not typer.confirm(f"拉取 {behind} 个新提交并重建容器？"):
        ui.info("已取消。")
        return

    ui.info("拉取最新代码……")
    code, out = _run(["git", "pull", "--ff-only", "origin", "main"], cwd=root)
    if code != 0:
        ui.err(f"代码拉取失败（本地有改动时请先处理）：{out[:300]}")
        raise typer.Exit(1)
    ui.ok("代码已更新。")

    ui.info("重建 wheel……")
    try:
        _rebuild_wheel(root)
    except RuntimeError as exc:
        ui.err(str(exc))
        raise typer.Exit(1) from exc
    ui.ok("wheel 已重建。")

    code, docker_check = _run(["docker", "--version"])
    if code != 0:
        ui.warn(
            "未检测到 Docker，代码与 wheel 已更新；请在宿主机执行 docker compose up -d --build 完成部署。"
        )
        return

    ui.info("重建镜像并滚动重启（可能需要几分钟）……")
    code, out = _run(["docker", "compose", "up", "-d", "--build"], cwd=root)
    if code != 0:
        ui.err(f"容器重建失败：{out[-400:]}")
        raise typer.Exit(1)
    ui.ok("服务已用新版本重启。可运行 kuro doctor 做健康检查。")
