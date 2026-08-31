"""云备份（可选）：逐文件加密 → Gitee 私有仓库版本化存储。

设计：
- 逐文件推送：不做 zip 打包，每个文件单独加密后按原始目录结构推到 Gitee，
  天然绕过单文件大小限制（只要单文件 < 100MB 即可）。
- 版本化：每次备份一个 git commit，云端 git 历史即版本历史。
- 增量：git 只传输变更文件，未修改的文件不重复上传。
- 加密在本地完成，上云的永远是密文。
- 超过 100MB 的单个文件跳过并记录（罕见，如用户上传的超大模型文件）。
"""

from __future__ import annotations

import hashlib
import secrets
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

from kurotutor.core import get_logger

log = get_logger("cloud_backup")

_MAGIC = b"KUROENC1"
_SALT_LEN = 16
_NONCE_LEN = 12
_SCRYPT_N, _SCRYPT_R, _SCRYPT_P = 2**14, 8, 1
_BACKUP_DIRS = ("workspaces", "kb", "exports")
_BACKUP_FILES = ("kurotutor.db", "kuro.json")
_MAX_FILE_MB = 90  # Gitee 单文件限制 100MB，留余量


class CloudBackupError(Exception):
    """云备份失败（含可操作的修复建议）。"""


def is_configured(cfg: Any) -> bool:
    b = getattr(cfg, "backup", None)
    return bool(b and b.gitee_repo and b.gitee_user and b.gitee_token and b.encrypt_password)


# ---- 加解密 ------------------------------------------------------------------


def _derive_key(password: str, salt: bytes) -> bytes:
    return hashlib.scrypt(password.encode(), salt=salt, n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P, dklen=32)


def encrypt_bytes(data: bytes, password: str) -> bytes:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    salt = secrets.token_bytes(_SALT_LEN)
    nonce = secrets.token_bytes(_NONCE_LEN)
    key = _derive_key(password, salt)
    ct = AESGCM(key).encrypt(nonce, data, _MAGIC)
    return _MAGIC + salt + nonce + ct


def decrypt_bytes(data: bytes, password: str) -> bytes:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    header_len = len(_MAGIC) + _SALT_LEN + _NONCE_LEN
    if len(data) < header_len or not data.startswith(_MAGIC):
        raise CloudBackupError("备份文件格式不对（不是 KuroTutor 加密备份）")
    salt = data[len(_MAGIC) : len(_MAGIC) + _SALT_LEN]
    nonce = data[len(_MAGIC) + _SALT_LEN : header_len]
    ct = data[header_len:]
    key = _derive_key(password, salt)
    try:
        return AESGCM(key).decrypt(nonce, ct, _MAGIC)
    except Exception as exc:
        raise CloudBackupError("解密失败：加密口令不对，或备份文件已损坏") from exc


# ---- 本地打包与恢复（CLI backup/restore 用） ----------------------------------


def make_local_backup(
    data_dir: Path, out_dir: Path | None = None, *, config_path: Path | None = None
) -> Path:
    """全量打包为 zip：数据库/工作区/知识库/导出/kuro.json。返回 zip 路径。"""
    import zipfile

    target_dir = out_dir or (data_dir / "backups")
    target_dir.mkdir(parents=True, exist_ok=True)
    out = target_dir / f"kuro_backup_{datetime.now():%Y%m%d_%H%M}.zip"
    count = 0
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        for item in (*_BACKUP_DIRS, "kurotutor.db"):
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
        if config_path and config_path.exists():
            zf.write(config_path, arcname="kuro.json")
            count += 1
    if count == 0:
        out.unlink(missing_ok=True)
        raise CloudBackupError("数据目录为空，没有可备份的内容")
    return out


def extract_backup(zip_path: Path, data_dir: Path) -> int:
    """把备份 zip 解压覆盖到 data/。返回文件数。"""
    import zipfile

    count = 0
    with zipfile.ZipFile(zip_path) as zf:
        for name in zf.namelist():
            top = name.replace("\\", "/").split("/")[0]
            if top not in (*_BACKUP_DIRS, "kurotutor.db", "kuro.json"):
                continue
            if ".." in name:
                continue
            target = data_dir / name
            if name.endswith("/"):
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(name) as src, open(target, "wb") as dst:
                dst.write(src.read())
            count += 1
    return count


# ---- Gitee 传输 --------------------------------------------------------------


def _git(args: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace"
    )


def _repo_url(cfg: Any) -> str:
    b = cfg.backup
    return f"https://{b.gitee_user}:{b.gitee_token}@gitee.com/{b.gitee_repo}.git"


def _with_cloned_repo(cfg: Any, read_only: bool, fn) -> Any:
    url = _repo_url(cfg)
    depth = ["--depth", "50"] if read_only else []
    with tempfile.TemporaryDirectory(prefix="kuro-cloud-") as td:
        repo_dir = Path(td) / "repo"
        clone = _git(["clone", *depth, url, str(repo_dir)])
        if clone.returncode != 0:
            if read_only:
                raise CloudBackupError(f"Gitee 拉取失败：{(clone.stderr or clone.stdout).strip()[:200]}")
            init = _git(["init", "-b", "main", str(repo_dir)])
            if init.returncode != 0:
                raise CloudBackupError(f"git 初始化失败：{init.stderr.strip()[:120]}")
            remote_add = _git(["remote", "add", "origin", url], cwd=repo_dir)
            if remote_add.returncode != 0:
                raise CloudBackupError(f"git remote 配置失败：{remote_add.stderr.strip()[:120]}")
        return fn(repo_dir)


def list_versions(cfg: Any) -> list[dict[str, str]]:
    if not is_configured(cfg):
        raise CloudBackupError("云备份未配置（backup.gitee_repo 为空）")

    def _run(repo_dir: Path) -> list[dict[str, str]]:
        glog = _git(["log", "--date=iso-local", "--pretty=format:%H|%cI|%s"], cwd=repo_dir)
        if glog.returncode != 0:
            raise CloudBackupError("云端仓库还没有任何备份版本")
        out = []
        for line in glog.stdout.strip().splitlines():
            parts = line.split("|", 2)
            if len(parts) == 3:
                out.append({"commit": parts[0], "date": parts[1], "message": parts[2]})
        return out

    return _with_cloned_repo(cfg, read_only=True, fn=_run)


# ---- 备份 --------------------------------------------------------------------


def _collect_files(data_dir: Path, config_path: Path | None) -> list[Path]:
    """收集所有要备份的文件路径（全量：数据库 + 工作区 + 知识库 + 导出 + 配置）。"""
    files: list[Path] = []
    for item in _BACKUP_DIRS:
        d = data_dir / item
        if d.is_dir():
            files.extend(f for f in d.rglob("*") if f.is_file())
    for item in _BACKUP_FILES:
        f = data_dir / item
        if f.is_file():
            files.append(f)
    if config_path and config_path.exists():
        files.append(config_path)
    return files


def run_cloud_backup(cfg: Any, data_dir: Path, *, config_path: Path | None = None) -> dict[str, Any]:
    """云备份：逐文件加密 → 按目录结构推送到 Gitee → git commit 版本化。

    不做 zip 打包——每个文件独立加密，天然绕过单文件大小限制。
    返回 {ok, detail, skipped}，失败不抛出。
    """
    try:
        if not is_configured(cfg):
            return {"ok": False, "detail": "云备份未配置（backup.gitee_repo 为空），已跳过"}

        password = cfg.backup.encrypt_password
        all_files = _collect_files(Path(data_dir), config_path)
        if not all_files:
            return {"ok": False, "detail": "数据目录为空，没有可备份的内容"}

        stamp = f"backup {datetime.now():%Y-%m-%d %H:%M}"

        def _push(repo_dir: Path) -> str:
            pushed, skipped = 0, 0
            for src in all_files:
                # 计算在仓库中的相对路径（保持 data/ 下的目录结构）
                try:
                    rel = src.relative_to(data_dir)
                except ValueError:
                    rel = Path("kuro.json")  # config_path 不在 data_dir 下
                # 跳过备份目录自身和 git 目录
                if rel.parts[0] in ("backups", ".git"):
                    continue
                size_mb = src.stat().st_size / 1048576
                if size_mb > _MAX_FILE_MB:
                    skipped += 1
                    log.warning(f"cloud backup skip (too large): {rel} {size_mb:.0f}MB")
                    continue
                # 加密
                enc_data = encrypt_bytes(src.read_bytes(), password)
                # 写入仓库（保持目录结构 + .enc 后缀）
                dest = repo_dir / str(rel) + ".enc"
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(enc_data)
                pushed += 1

            if pushed == 0:
                raise CloudBackupError("没有文件被推送（全部为空或超大）")

            for args in (
                ["config", "user.email", "kurotutor@backup.local"],
                ["config", "user.name", "KuroTutor Backup"],
                ["add", "-A"],
            ):
                _git(args, cwd=repo_dir)
            commit = _git(["commit", "-m", stamp], cwd=repo_dir)
            if commit.returncode != 0:
                # 没有变更 = 数据没变，正常
                if "nothing to commit" in (commit.stderr or ""):
                    return f"{stamp}（无变更）"
                raise CloudBackupError(f"git 提交失败：{commit.stderr.strip()[:160]}")
            branch = _git(["branch", "-M", "main"], cwd=repo_dir)
            if branch.returncode != 0:
                raise CloudBackupError(f"git 分支重命名失败：{branch.stderr.strip()[:120]}")
            push = _git(["push", "origin", "main"], cwd=repo_dir)
            if push.returncode != 0:
                err = push.stderr.strip() or push.stdout.strip()
                if "Authentication" in err or "403" in err:
                    raise CloudBackupError("Gitee 认证失败：检查 gitee_user / gitee_token")
                raise CloudBackupError(f"Gitee 推送失败：{err[:200]}")
            detail = f"{stamp}（{pushed} 个文件"
            if skipped:
                detail += f"，跳过 {skipped} 个超大文件"
            detail += "）"
            return detail

        detail = _with_cloned_repo(cfg, read_only=False, fn=_push)
        log.info(f"cloud backup pushed: {detail}")
        return {"ok": True, "detail": f"云端备份完成（{detail}）"}
    except CloudBackupError as exc:
        log.warning(f"cloud backup failed: {exc}")
        return {"ok": False, "detail": str(exc)}
    except Exception as exc:
        log.warning(f"cloud backup unexpected error: {exc!r}")
        return {"ok": False, "detail": f"云备份出现意外错误：{exc!r}"}


# ---- 恢复 --------------------------------------------------------------------


def restore_from_cloud(cfg: Any, version: str | None, data_dir: Path) -> dict[str, Any]:
    """从云端恢复：拉取指定版本 → 逐文件解密 → 写回 data/。返回 {count, detail}。"""
    if not is_configured(cfg):
        raise CloudBackupError("云备份未配置，无法从云端恢复")
    password = cfg.backup.encrypt_password

    def _restore(repo_dir: Path) -> int:
        if version:
            checkout = _git(["checkout", version], cwd=repo_dir)
            if checkout.returncode != 0:
                raise CloudBackupError(f"版本 {version[:8]} 不存在")
        count = 0
        for enc_file in sorted(repo_dir.rglob("*.enc")):
            rel = enc_file.relative_to(repo_dir)
            # 去掉 .enc 后缀得到原始路径
            orig_rel = rel.with_suffix("")
            if orig_rel.parts[0] in (".git",):
                continue
            data = decrypt_bytes(enc_file.read_bytes(), password)
            target = data_dir / orig_rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            count += 1
        return count

    count = _with_cloned_repo(cfg, read_only=True, fn=_restore)
    if count == 0:
        raise CloudBackupError("云端没有可恢复的文件")
    return {"count": count, "detail": f"已恢复 {count} 个文件到 {data_dir}"}
