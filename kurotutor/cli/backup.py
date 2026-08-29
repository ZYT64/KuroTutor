"""kuro backup —— 打包学生数据（数据库 + 工作区 + 知识库）到带时间戳的压缩包。

数据无价：所有学生数据就是 data/ 下的一个 SQLite 文件与若干目录。
本命令把它们打包为单个 zip（保留最近 N 份），可配合系统 cron 定期执行。
"""

from __future__ import annotations

import zipfile
from pathlib import Path

import typer

from . import ui

# 打包内容（相对 data/ 目录）；不存在自动跳过
_INCLUDE = ["kurotutor.db", "workspaces", "kb", "exports"]
# 默认保留份数（超出自动删最旧）
_KEEP = 7


def _prune_old(backup_dir: Path, keep: int) -> int:
    backups = sorted(backup_dir.glob("kuro_backup_*.zip"))
    removed = 0
    for old in backups[: max(len(backups) - keep, 0)]:
        old.unlink()
        removed += 1
    return removed


def backup_command(
    out_dir: Path | None = typer.Option(None, "--out", help="备份输出目录（默认 data/backups）"),
    keep: int = typer.Option(_KEEP, "--keep", help="保留最近几份备份"),
) -> None:
    """备份数据目录（数据库/工作区/知识库/导出）为单个压缩包。"""
    root = Path.cwd()
    data_dir = root / "data"
    if not data_dir.exists():
        ui.fail("未找到 data/ 目录——请在部署目录内执行 kuro backup。")
        raise typer.Exit(1)
    from datetime import datetime

    target_dir = out_dir or (data_dir / "backups")
    target_dir.mkdir(parents=True, exist_ok=True)
    out = target_dir / f"kuro_backup_{datetime.now():%Y%m%d_%H%M}.zip"

    ui.info("打包中……")
    count = 0
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        for item in _INCLUDE:
            src = data_dir / item
            if not src.exists():
                continue
            if src.is_file():
                zf.write(src, arcname=item)
                count += 1
            else:
                for f in src.rglob("*"):
                    if f.is_file():
                        zf.write(f, arcname=str(f.relative_to(data_dir)))
                        count += 1
    if count == 0:
        out.unlink()
        ui.fail("data/ 下没有可备份的内容。")
        raise typer.Exit(1)

    removed = _prune_old(target_dir, max(int(keep), 1))
    size_mb = out.stat().st_size / 1024 / 1024
    ui.ok(f"备份完成：{out}（{size_mb:.1f} MB，{count} 个文件）")
    if removed:
        ui.info(f"已清理 {removed} 份旧备份（保留最近 {keep} 份）。")
    ui.info("恢复方式：解压覆盖 data/ 目录后重启服务（先停服务再覆盖）。")
