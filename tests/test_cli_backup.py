"""kuro backup 命令测试。"""

import zipfile

from typer.testing import CliRunner

from kurotutor.cli.main import app

runner = CliRunner()


def test_backup_creates_zip_and_prunes(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    data = tmp_path / "data"
    data.mkdir()
    (data / "kurotutor.db").write_bytes(b"sqlite-data")
    ws = data / "workspaces" / "u1"
    ws.mkdir(parents=True)
    (ws / "a.txt").write_text("hello", encoding="utf-8")
    backups = data / "backups"
    backups.mkdir()
    # 预置 3 份旧备份，keep=2 应清掉 1 份
    for i in range(3):
        (backups / f"kuro_backup_2026010{i}_0000.zip").write_bytes(b"old")

    res = runner.invoke(app, ["backup", "--keep", "2"])
    assert res.exit_code == 0, res.output
    zips = list(backups.glob("kuro_backup_*.zip"))
    assert len(zips) == 2  # 3 旧 + 1 新 - 2 清理（keep=2）
    newest = max(zips, key=lambda p: p.stat().st_mtime)
    with zipfile.ZipFile(newest) as zf:
        names = zf.namelist()
    assert "kurotutor.db" in names and any("u1/a.txt" in n for n in names)


def test_backup_empty_data_fails(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "data").mkdir()
    res = runner.invoke(app, ["backup"])
    assert res.exit_code == 1
