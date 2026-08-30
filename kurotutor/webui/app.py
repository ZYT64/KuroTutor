"""KuroTutor WebUI 管理面板（只读）。

FastAPI 应用：口令认证 → 学情/错题/排课/配置(脱敏)/备份 只读 API +
前端构建产物静态托管。写操作仍走 CLI 与 QQ 对话，面板与 CLI 不双写。

启动：uvicorn kurotutor.webui.app:app --host 0.0.0.0 --port 8001
"""

from __future__ import annotations

import zipfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlmodel import select

from kurotutor.config.loader import load_config
from kurotutor.core import get_logger
from kurotutor.services.stats import effect_summary, take_daily_snapshot
from kurotutor.storage import (
    CheckIn,
    CourseInstance,
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


def _load_engine():
    from kurotutor.storage import build_engine, init_db

    cfg = load_config()
    engine = build_engine("sqlite:///" + cfg.data_dir.replace("\\", "/") + "/kurotutor.db")
    init_db(engine)
    return cfg, engine


_CFG, _ENGINE = _load_engine()


def _authed(request) -> bool:
    token = (_CFG.webui.token or "").strip()
    if not token:
        return False  # 未配置口令 = 面板禁用
    return request.cookies.get(_COOKIE) == token


def _require(request) -> None:
    if not _authed(request):
        raise HTTPException(status_code=401, detail="未登录或口令已变更")


class LoginBody(BaseModel):
    token: str


def create_app() -> FastAPI:
    app = FastAPI(title="KuroTutor Panel", docs_url=None, redoc_url=None)

    @app.post("/api/login")
    def login(body: LoginBody, response: Response):
        token = (_CFG.webui.token or "").strip()
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
        today = datetime.now(UTC)
        with session_scope(_ENGINE) as db:
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
        with session_scope(_ENGINE) as db:
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
            take_daily_snapshot(_ENGINE, sid)
            effect = effect_summary(_ENGINE, sid)
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
        with session_scope(_ENGINE) as db:
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

    @app.get("/api/schedule")
    def schedule(request: Request):
        _require(request)
        with session_scope(_ENGINE) as db:
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

        redacted = redact(_CFG)
        return redacted.model_dump()

    @app.post("/api/backup")
    def backup(request: Request):
        _require(request)
        data_dir = Path(_CFG.data_dir)
        if not data_dir.exists():
            raise HTTPException(status_code=400, detail="数据目录不存在")
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
        log.info("panel backup created", file=str(out), files=count)
        return {"ok": True, "file": out.name, "size_mb": round(out.stat().st_size / 1048576, 1)}

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
