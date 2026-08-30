"""云备份（可选）：全量打包 → AES-GCM 加密 → Gitee 私有仓库版本化存储。

设计：
- 全量：数据库 + 工作区 + 知识库 + kuro.json 配置，一次打包。
- 版本化：每天一个独立 git commit（今天不吞昨天），云端 git 历史即版本历史；
  支持按版本一键回滚。
- 加密在本地完成，上云的永远是密文；encrypt_password 是唯一解密凭据。
- 未配置 gitee_repo 时云端链路自动禁用（可选项，零配置兼容）。
- 任何失败都被捕获并返回明确原因，不影响主服务。
"""

from __future__ import annotations

import hashlib
import secrets
import subprocess
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any

from kurotutor.core import get_logger

log = get_logger("cloud_backup")

_MAGIC = b"KUROENC1"
_SALT_LEN = 16
_NONCE_LEN = 12
_SCRYPT_N, _SCRYPT_R, _SCRYPT_P = 2**14, 8, 1
_BACKUP_ITEMS = ("kurotutor.db", "workspaces", "kb", "exports")


class CloudBackupError(Exception):
    """云备份失败（含可操作的修复建议）。"""


def is_configured(cfg: Any) -> bool:
    """云端备份是否已配置（repo + token + 口令齐全）。"""
    b = getattr(cfg, "backup", None)
    return bool(b and b.gitee_repo and b.gitee_user and b.gitee_token and b.encrypt_password)


# ---- 加解密 ------------------------------------------------------------------


def _derive_key(password: str, salt: bytes) -> bytes:
    return hashlib.scrypt(password.encode(), salt=salt, n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P, dklen=32)


def encrypt_bytes(data: bytes, password: str) -> bytes:
    """AES-GCM 加密：magic + salt + nonce + 密文。"""
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    salt = secrets.token_bytes(_SALT_LEN)
    nonce = secrets.token_bytes(_NONCE_LEN)
    key = _derive_key(password, salt)
    ct = AESGCM(key).encrypt(nonce, data, _MAGIC)
    return _MAGIC + salt + nonce + ct


def decrypt_bytes(data: bytes, password: str) -> bytes:
    """解密；口令错误/文件损坏抛 CloudBackupError。"""
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


# ---- 打包与恢复 --------------------------------------------------------------


def make_local_backup(
    data_dir: Path, out_dir: Path | None = None, *, config_path: Path | None = None
) -> Path:
    """全量打包：数据库/工作区/知识库/导出/kuro.json 配置。返回 zip 路径。"""
    target_dir = out_dir or (data_dir / "backups")
    target_dir.mkdir(parents=True, exist_ok=True)
    out = target_dir / f"kuro_backup_{datetime.now():%Y%m%d_%H%M}.zip"
    count = 0
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        for item in _BACKUP_ITEMS:
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
    """把备份 zip 解压覆盖到 data/（白名单顶层条目 + kuro.json，拒绝路径穿越）。返回文件数。"""
    count = 0
    with zipfile.ZipFile(zip_path) as zf:
        for name in zf.namelist():
            top = name.replace("\\", "/").split("/")[0]
            if top not in (*_BACKUP_ITEMS, "kuro.json"):
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
    """clone（或复用临时 clone）仓库执行 fn(repo_dir)。read_only 时浅克隆提速。"""
    url = _repo_url(cfg)
    depth = ["--depth", "50"] if read_only else []
    with tempfile.TemporaryDirectory(prefix="kuro-cloud-") as td:
        repo_dir = Path(td) / "repo"
        clone = _git(["clone", *depth, url, str(repo_dir)])
        if clone.returncode != 0:
            if read_only:
                raise CloudBackupError(f"Gitee 拉取失败：{(clone.stderr or clone.stdout).strip()[:200]}")
            # 空仓库无法 clone → 本地 init（首次备份）
            init = _git(["init", "-b", "main", str(repo_dir)])
            if init.returncode != 0:
                raise CloudBackupError(f"git 初始化失败：{init.stderr.strip()[:120]}")
            remote_add = _git(["remote", "add", "origin", url], cwd=repo_dir)
            if remote_add.returncode != 0:
                raise CloudBackupError(f"git remote 配置失败：{remote_add.stderr.strip()[:120]}")
        return fn(repo_dir)


def list_versions(cfg: Any) -> list[dict[str, str]]:
    """列出云端备份版本（git 提交历史，新 → 旧）。"""
    if not is_configured(cfg):
        raise CloudBackupError("云备份未配置（backup.gitee_repo 为空）")

    def _run(repo_dir: Path) -> list[dict[str, str]]:
        glog = _git(
            ["log", "--date=iso-local", "--pretty=format:%H|%cI|%s"],
            cwd=repo_dir,
        )
        if glog.returncode != 0:
            raise CloudBackupError("云端仓库还没有任何备份版本")
        out = []
        for line in glog.stdout.strip().splitlines():
            parts = line.split("|", 2)
            if len(parts) == 3:
                out.append({"commit": parts[0], "date": parts[1], "message": parts[2]})
        return out

    return _with_cloned_repo(cfg, read_only=True, fn=_run)


def push_backup(cfg: Any, enc_path: Path) -> str:
    """把加密备份作为新 commit 推送（版本化，不覆盖旧版本）。返回版本说明。"""
    stamp = f"backup {datetime.now():%Y-%m-%d %H:%M}"

    def _run(repo_dir: Path) -> str:
        (repo_dir / "backup.enc").write_bytes(enc_path.read_bytes())
        for args in (
            ["config", "user.email", "kurotutor@backup.local"],
            ["config", "user.name", "KuroTutor Backup"],
            ["add", "-A"],
        ):
            _git(args, cwd=repo_dir)
        commit = _git(["commit", "-m", stamp], cwd=repo_dir)
        if commit.returncode != 0:
            raise CloudBackupError(f"git 提交失败：{commit.stderr.strip()[:160]}")
        branch = _git(["branch", "-M", "main"], cwd=repo_dir)  # 空仓库克隆默认 master，统一为 main
        if branch.returncode != 0:
            raise CloudBackupError(f"git 分支重命名失败：{branch.stderr.strip()[:120]}")
        push = _git(["push", "origin", "main"], cwd=repo_dir)
        if push.returncode != 0:
            err = push.stderr.strip() or push.stdout.strip()
            if "Authentication" in err or "403" in err:
                raise CloudBackupError("Gitee 认证失败：检查 gitee_user / gitee_token（需 repo 权限）")
            raise CloudBackupError(f"Gitee 推送失败：{err[:200]}")
        return stamp

    return _with_cloned_repo(cfg, read_only=False, fn=_run)


def fetch_version(cfg: Any, version: str | None) -> bytes:
    """拉取指定版本（None=最新）的备份并解密。返回 zip 字节。"""
    if not is_configured(cfg):
        raise CloudBackupError("云备份未配置，无法从云端恢复")

    def _run(repo_dir: Path) -> bytes:
        if version:
            checkout = _git(["checkout", version, "--", "backup.enc"], cwd=repo_dir)
            if checkout.returncode != 0:
                raise CloudBackupError(f"切换到版本 {version[:8]} 失败（版本不存在？）")
        enc = repo_dir / "backup.enc"
        if not enc.exists():
            raise CloudBackupError("该版本里没有备份文件（backup.enc）")
        return decrypt_bytes(enc.read_bytes(), cfg.backup.encrypt_password)

    return _with_cloned_repo(cfg, read_only=True, fn=_run)


def run_cloud_backup(cfg: Any, data_dir: Path, *, config_path: Path | None = None) -> dict[str, Any]:
    """完整云备份流程：全量打包 → 加密 → 推送。返回 {ok, detail}，失败不抛出。"""
    try:
        if not is_configured(cfg):
            return {"ok": False, "detail": "云备份未配置（backup.gitee_repo 为空），已跳过"}
        zip_path = make_local_backup(Path(data_dir), config_path=config_path)
        enc_path = zip_path.with_suffix(".zip.enc")
        enc_path.write_bytes(encrypt_bytes(zip_path.read_bytes(), cfg.backup.encrypt_password))
        stamp = push_backup(cfg, enc_path)
        size_mb = round(enc_path.stat().st_size / 1048576, 1)
        enc_path.unlink(missing_ok=True)
        log.info(f"cloud backup pushed: {stamp} ({size_mb} MB enc)")
        return {"ok": True, "detail": f"云端备份完成（{stamp}，加密包 {size_mb} MB）"}
    except CloudBackupError as exc:
        log.warning(f"cloud backup failed: {exc}")
        return {"ok": False, "detail": str(exc)}
    except Exception as exc:  # 兜底：任何异常不崩溃
        log.warning(f"cloud backup unexpected error: {exc!r}")
        return {"ok": False, "detail": f"云备份出现意外错误：{exc!r}（详见服务日志）"}
