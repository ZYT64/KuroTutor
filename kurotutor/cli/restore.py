"""kuro restore —— 从本地备份 zip 或 Gitee 云备份恢复数据。

恢复会覆盖当前 data/ 下的数据库与工作区文件，强制二次确认。
云恢复支持按版本回滚：--from gitee 列出版本，--version 指定版本。
"""

from __future__ import annotations

from pathlib import Path

import typer

from kurotutor.core import get_logger

from . import ui

log = get_logger("restore")


def restore_command(
    source: str = typer.Option("gitee", "--from", help="恢复来源：gitee / 本地 zip 路径"),
    version: str = typer.Option(None, "--version", help="云备份版本（git commit，留空=最新）"),
    list_versions: bool = typer.Option(False, "--list", help="列出云端备份版本"),
    yes: bool = typer.Option(False, "--yes", "-y", help="跳过确认"),
) -> None:
    """恢复数据：从 Gitee 云备份（可按版本回滚）或本地备份 zip。会覆盖当前数据。"""
    from kurotutor.config.loader import load_config
    from kurotutor.services.cloud_backup import (
        CloudBackupError,
        extract_backup,
        fetch_version,
        list_versions,
    )

    cfg = load_config()
    data_dir = Path(cfg.data_dir)

    if list_versions or (source == "gitee" and not version and not yes):
        try:
            versions = list_versions(cfg)
        except Exception as exc:
            ui.err(f"获取云端版本失败：{exc}")
            raise typer.Exit(1) from exc
        ui.info("云端备份版本（新 → 旧，--version 填短 commit）：")
        for v in versions[:20]:
            ui.info(f"{v['date'][:19]}  {v['commit'][:8]}  {v['message']}")
        if list_versions:
            return
        if not versions:
            ui.err("云端没有可用版本。")
            raise typer.Exit(1)
        pick = typer.prompt("输入要恢复的短 commit（回车=最新）", default="")
        version = pick.strip() or versions[0]["commit"]

    if source == "gitee":
        ui.info("从 Gitee 拉取并解密备份……")
        try:
            data = fetch_version(cfg, version or None)
        except CloudBackupError as exc:
            ui.err(str(exc))
            raise typer.Exit(1) from exc
        zip_path = data_dir / "backups" / f"restore_{version[:8] if version else 'latest'}.zip"
        zip_path.parent.mkdir(parents=True, exist_ok=True)
        zip_path.write_bytes(data)
        ui.ok(f"已下载并解密：{zip_path.name}")
    else:
        zip_path = Path(source)
        if not zip_path.exists():
            ui.err(f"备份文件不存在：{zip_path}")
            raise typer.Exit(1)

    if not yes:
        confirm = typer.confirm(
            f"恢复将覆盖当前 {data_dir} 下的数据库与工作区（建议先 kuro backup）。继续？"
        )
        if not confirm:
            ui.info("已取消恢复。")
            return

    count = extract_backup(zip_path, data_dir)
    if count == 0:
        ui.err("备份包里没有可恢复的内容。")
        raise typer.Exit(1)
    ui.ok(f"恢复完成（{count} 个文件，含配置）。重启服务生效：docker compose restart kuro webui")
