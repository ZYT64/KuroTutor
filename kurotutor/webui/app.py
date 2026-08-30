"""KuroTutor WebUI 管理面板。

FastAPI 应用：口令认证 → 学情/错题/排课只读 API + 配置查看与修改（校验后写盘）
+ 备份触发 + 前端构建产物静态托管。教学写操作仍走 QQ 对话，面板不碰学生数据。
"""

from __future__ import annotations

import zipfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlmodel import select

from kurotutor.config.loader import load_config
from kurotutor.core import get_logger
from kurotutor.services.stats import effect_summary, take_daily_snapshot
from kurotutor.storage import (
    CheckIn,
    CorpusEntry,
    CourseInstance,
    KnowledgeCard,
    KnowledgePoint,
    MasterySnapshot,
    ScheduleTask,
    Student,
    TaskStatus,
    WrongQuestion,
    WrongStatus,
    session_scope,
)

log = get_logger("webui")

_COOKIE = "kuro_webui"


# 懒加载：模块导入不触发配置读取与数据库连接（测试可 monkeypatch 覆盖）
_CFG: Any = None
_ENGINE: Any = None


def _get_cfg():
    global _CFG
    if _CFG is None:
        _CFG = load_config()
    return _CFG


def _get_engine():
    global _ENGINE
    if _ENGINE is None:
        from kurotutor.storage import build_engine, init_db

        cfg = _get_cfg()
        _ENGINE = build_engine("sqlite:///" + cfg.data_dir.replace("\\", "/") + "/kurotutor.db")
        init_db(_ENGINE)
    return _ENGINE


def _authed(request) -> bool:
    token = (_get_cfg().webui.token or "").strip()
    if not token:
        return False  # 未配置口令 = 面板禁用
    return request.cookies.get(_COOKIE) == token


def _require(request) -> None:
    if not _authed(request):
        raise HTTPException(status_code=401, detail="未登录或口令已变更")


class LoginBody(BaseModel):
    token: str


class SetBody(BaseModel):
    key: str
    value: str


class RestoreBody(BaseModel):
    version: str = ""


def create_app() -> FastAPI:
    app = FastAPI(title="KuroTutor Panel", docs_url=None, redoc_url=None)

    @app.post("/api/login")
    def login(body: LoginBody, response: Response):
        token = (_get_cfg().webui.token or "").strip()
        if not token or body.token != token:
            raise HTTPException(status_code=401, detail="口令错误")
        response.set_cookie(_COOKIE, token, httponly=True, samesite="lax", max_age=7 * 86400)
        return {"ok": True}

    @app.post("/api/logout")
    def logout(response: Response):
        response.delete_cookie(_COOKIE)
        return {"ok": True}

    @app.get("/api/overview")
    def overview(request: Request):
        _require(request)
        engine = _get_engine()
        today = datetime.now(UTC)
        with session_scope(engine) as db:
            students = db.exec(select(Student)).all()
            sids = [s.id for s in students]
            wrongs = db.exec(select(WrongQuestion)).all()
            open_wrong = sum(1 for w in wrongs if w.status != WrongStatus.MASTERED)
            mastered = sum(1 for w in wrongs if w.status == WrongStatus.MASTERED)
            tasks = db.exec(
                select(ScheduleTask).where(
                    ScheduleTask.status == TaskStatus.PENDING,
                    ScheduleTask.fire_at >= today - timedelta(days=1),
                )
            ).all()
            checkins = db.exec(select(CheckIn)).all()
            today_str = today.astimezone().strftime("%Y-%m-%d")
            checkin_today = sum(1 for c in checkins if c.date == today_str)
            snaps = {
                sid: db.exec(
                    select(MasterySnapshot)
                    .where(MasterySnapshot.student_id == sid)
                    .order_by(MasterySnapshot.date.desc())
                    .limit(1)
                ).first()
                for sid in sids
            }
        by_student = []
        for s in students:
            snap = snaps.get(s.id)
            by_student.append(
                {
                    "id": s.id,
                    "nickname": s.nickname or s.external_id[:8],
                    "stage": s.stage,
                    "avg_mastery": snap.avg_mastery if snap else None,
                    "due_count": snap.due_count if snap else None,
                }
            )
        return {
            "students_total": len(students),
            "wrong_open": open_wrong,
            "wrong_mastered": mastered,
            "pending_tasks": len(tasks),
            "checkin_today": checkin_today,
            "by_student": by_student,
        }

    @app.get("/api/students/{sid}")
    def student_detail(sid: int, request: Request):
        _require(request)
        engine = _get_engine()
        with session_scope(engine) as db:
            st = db.get(Student, sid)
            if st is None:
                raise HTTPException(status_code=404, detail="学生不存在")
            kps = db.exec(
                select(KnowledgePoint)
                .where(KnowledgePoint.student_id == sid)
                .order_by(KnowledgePoint.mastery.asc())
            ).all()
            wrongs = db.exec(
                select(WrongQuestion)
                .where(WrongQuestion.student_id == sid)
                .order_by(WrongQuestion.id.desc())
                .limit(100)
            ).all()
            courses = db.exec(
                select(CourseInstance)
                .where(CourseInstance.student_id == sid)
                .order_by(CourseInstance.start_at.desc())
                .limit(20)
            ).all()
        try:
            take_daily_snapshot(engine, sid)
            effect = effect_summary(engine, sid)
        except Exception as exc:
            log.warning("snapshot failed in panel", error=str(exc))
            effect = {}
        return {
            "id": st.id,
            "nickname": st.nickname or st.external_id[:8],
            "stage": st.stage,
            "note": st.note,
            "mastery": [
                {"name": k.name, "subject": k.subject, "mastery": k.mastery, "confidence": k.confidence}
                for k in kps[:20]
            ],
            "wrongs": [
                {
                    "id": w.id,
                    "subject": w.subject,
                    "question": (w.question_text or "（图片题）")[:80],
                    "error_type": w.error_type,
                    "status": w.status,
                    "times_wrong": w.times_wrong,
                }
                for w in wrongs
            ],
            "courses": [
                {
                    "id": c.id,
                    "title": c.title,
                    "start": c.start_at.isoformat() if c.start_at else "",
                    "status": c.status,
                    "classroom_url": c.classroom_url,
                }
                for c in courses
            ],
            "effect": effect,
        }

    @app.get("/api/mistakes")
    def mistakes(request: Request, student_id: int | None = None):
        _require(request)
        engine = _get_engine()
        with session_scope(engine) as db:
            stmt = select(WrongQuestion).order_by(WrongQuestion.id.desc()).limit(200)
            if student_id:
                stmt = select(WrongQuestion).where(
                    WrongQuestion.student_id == student_id
                ).order_by(WrongQuestion.id.desc()).limit(200)
            rows = db.exec(stmt).all()
        return [
            {
                "id": w.id,
                "student_id": w.student_id,
                "subject": w.subject,
                "question": (w.question_text or "（图片题）")[:100],
                "error_type": w.error_type,
                "status": w.status,
            }
            for w in rows
        ]

    @app.get("/api/kb")
    def kb_list(request: Request, student_id: int | None = None):
        """知识库列表：方法卡片 + 语料，可按学生筛选。"""
        _require(request)
        with session_scope(_get_engine()) as db:
            cards_stmt = select(KnowledgeCard).order_by(KnowledgeCard.id.desc()).limit(200)
            corpus_stmt = select(CorpusEntry).order_by(CorpusEntry.id.desc()).limit(100)
            if student_id:
                from sqlmodel import or_

                cards_stmt = cards_stmt.where(
                    or_(KnowledgeCard.student_id == student_id, KnowledgeCard.student_id.is_(None))
                )
                corpus_stmt = corpus_stmt.where(CorpusEntry.student_id == student_id)
            cards = db.exec(cards_stmt).all()
            corpus = db.exec(corpus_stmt).all()
            names = {s.id: (s.nickname or s.external_id[:8]) for s in db.exec(select(Student)).all()}
        return {
            "cards": [
                {
                    "id": c.id,
                    "student": names.get(c.student_id, "公共") if c.student_id else "公共",
                    "subject": c.subject,
                    "question_type": c.question_type,
                    "method": (c.method or "")[:100],
                    "steps": (c.steps or "")[:150],
                }
                for c in cards
            ],
            "corpus": [
                {
                    "id": e.id,
                    "student": names.get(e.student_id, "未知") if e.student_id else "公共",
                    "title": e.title,
                    "subject": e.subject,
                    "content": (e.content or "")[:120],
                }
                for e in corpus
            ],
        }

    @app.get("/api/schedule")
    def schedule(request: Request):
        _require(request)
        engine = _get_engine()
        with session_scope(engine) as db:
            tasks = db.exec(
                select(ScheduleTask)
                .where(ScheduleTask.status == TaskStatus.PENDING)
                .order_by(ScheduleTask.fire_at.asc())
                .limit(50)
            ).all()
            names = {s.id: (s.nickname or s.external_id[:8]) for s in db.exec(select(Student)).all()}
        return [
            {
                "id": t.id,
                "student": names.get(t.student_id, "未知"),
                "kind": t.kind,
                "fire_at": t.fire_at.astimezone().isoformat() if t.fire_at else "",
            }
            for t in tasks
        ]

    @app.get("/api/config")
    def config_masked(request: Request):
        _require(request)
        from kurotutor.config.loader import redact

        redacted = redact(_get_cfg())
        return redacted.model_dump()

    # 允许面板修改的配置前缀白名单（防止越权改 permissions 等）
    _EDITABLE_PREFIXES = (
        "channel.", "models.llm.", "models.vision.", "models.embedding.",
        "models.search.", "models.qbank.", "openmaic.", "webui.token", "backup.", "ocr.",
        "retention.", "models.layout.",
    )
    _SECRET_SUFFIXES = ("api_key", "secret", "access_code", "token")

    @app.post("/api/config/set")
    def config_set(body: SetBody, request: Request):
        _require(request)
        key = body.key.strip()
        if not any(key == p.rstrip(".") or key.startswith(p) for p in _EDITABLE_PREFIXES):
            raise HTTPException(status_code=400, detail=f"面板不允许修改配置项：{key}")
        from kurotutor.cli.config import _coerce, _load_or_init, _set_nested, _write
        from kurotutor.config.loader import default_config_path

        path = default_config_path()
        raw = _load_or_init(path)
        value = body.value.strip()
        # 密钥类字段留空 = 保持不变
        if not value and any(key.endswith(sfx) for sfx in _SECRET_SUFFIXES):
            return {"ok": True, "unchanged": True}
        _set_nested(raw, key, _coerce(value))
        try:
            from kurotutor.config.loader import load_config_from_data, project_root_from_config_path

            root = project_root_from_config_path(path)
            load_config_from_data(raw, project_root=root)
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"配置校验未通过：{exc}") from exc
        _write(path, raw)
        log.info(f"panel config set: {key}")
        return {"ok": True}

    @app.post("/api/backup")
    def backup(request: Request, cloud: bool = False):
        """本地备份（cloud=false）/ 云端备份（cloud=true）。返回 {ok, detail}，错误不抛 5xx。"""
        _require(request)
        cfg = _get_cfg()
        data_dir = Path(cfg.data_dir)
        if cloud:
            from kurotutor.config.loader import default_config_path
            from kurotutor.services.cloud_backup import run_cloud_backup

            return run_cloud_backup(cfg, data_dir, config_path=default_config_path())
        if not data_dir.exists():
            return {"ok": False, "detail": "数据目录不存在"}
        target_dir = data_dir / "backups"
        target_dir.mkdir(parents=True, exist_ok=True)
        out = target_dir / f"kuro_backup_{datetime.now():%Y%m%d_%H%M}.zip"
        count = 0
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
            for item in ("kurotutor.db", "workspaces", "kb", "exports"):
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
            return {"ok": False, "detail": "数据目录为空，没有可备份的内容"}
        log.info(f"panel backup created: {out} ({count} files)")
        return {
            "ok": True,
            "file": out.name,
            "size_mb": round(out.stat().st_size / 1048576, 1),
            "detail": f"本地备份完成：{out.name}（{round(out.stat().st_size / 1048576, 1)} MB）",
        }

    @app.get("/api/backup/versions")
    def backup_versions(request: Request):
        _require(request)
        from kurotutor.services.cloud_backup import list_versions

        try:
            return {"ok": True, "versions": list_versions(_get_cfg())}
        except Exception as exc:
            return {"ok": False, "detail": str(exc), "versions": []}

    @app.post("/api/backup/restore")
    def backup_restore(body: RestoreBody, request: Request):
        """一键回滚：按版本拉取云端备份并覆盖本地数据。"""
        _require(request)
        from kurotutor.services.cloud_backup import extract_backup, fetch_version

        data_dir = Path(_get_cfg().data_dir)
        try:
            data = fetch_version(_get_cfg(), body.version or None)
        except Exception as exc:
            return {"ok": False, "detail": str(exc)}
        zip_path = data_dir / "backups" / f"restore_{(body.version or 'latest')[:8]}.zip"
        zip_path.parent.mkdir(parents=True, exist_ok=True)
        zip_path.write_bytes(data)
        count = extract_backup(zip_path, data_dir)
        if count == 0:
            return {"ok": False, "detail": "备份包里没有可恢复的内容"}
        log.info(f"panel restore done: version={body.version} files={count}")
        short = (body.version or "latest")[:8]
        return {
            "ok": True,
            "detail": f"已回滚到版本 {short}（{count} 个文件）。重启服务后完全生效。",
        }

    # 前端静态托管（构建产物存在时）：容器内 COPY 到 /app/kurotutor/webui/dist，
    # 源码运行时在包目录旁。两个位置都探测。
    dist_candidates = [
        Path("/app/kurotutor/webui/dist"),
        Path(__file__).parent / "dist",
    ]
    dist = next((d for d in dist_candidates if d.is_dir()), None)
    if dist is not None:

        @app.get("/{path:path}")
        def spa(path: str):
            target = dist / path
            if target.is_file():
                return FileResponse(target)
            return FileResponse(dist / "index.html")  # SPA 路由兜底

    return app


app = create_app()
