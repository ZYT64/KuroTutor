"""沙箱安全测试：路径穿越、软链逃逸、命令黑名单、端点白名单。"""

from __future__ import annotations

import pytest
from sqlmodel import select

from kurotutor.agent.sandbox import Sandbox
from kurotutor.core import SandboxError


def test_resolve_path_inside_workspace(config):
    sandbox = Sandbox(config)
    p = sandbox.resolve_path("notes/abc.md", for_write=True)
    assert str(p).startswith(str(sandbox.workspace))
    assert p.name == "abc.md"


def test_resolve_path_traversal_rejected(config, tmp_path):
    sandbox = Sandbox(config)
    with pytest.raises(SandboxError):
        # 用相对穿越逃出工作区
        sandbox.resolve_path("../../secret.txt", for_write=True)


def test_resolve_path_absolute_outside_rejected(config, tmp_path):
    sandbox = Sandbox(config)
    outside = tmp_path.parent / "secret.txt"
    with pytest.raises(SandboxError):
        sandbox.resolve_path(str(outside), for_write=True)


def test_resolve_path_symlink_escape(config, tmp_path):
    sandbox = Sandbox(config)
    sandbox.workspace.mkdir(parents=True, exist_ok=True)
    outside = tmp_path.parent / "outside_dir"
    outside.mkdir(parents=True, exist_ok=True)
    link = sandbox.workspace / "link"
    try:
        link.symlink_to(outside)
    except (OSError, NotImplementedError):
        pytest.skip("当前环境不支持创建符号链接")
    with pytest.raises(SandboxError):
        sandbox.resolve_path("link/evil.txt", for_write=True)


def test_command_system_blacklist(config):
    sandbox = Sandbox(config)
    allowed, reason = sandbox.check_command("rm -rf /")
    assert allowed is False
    assert "黑名单" in reason


def test_command_deny_default(config):
    sandbox = Sandbox(config)
    allowed, reason = sandbox.check_command("echo hi")
    assert allowed is False
    assert "deny" in reason


def test_command_whitelist(tmp_path):
    from kurotutor.config.loader import load_config_from_data

    cfg = load_config_from_data(
        {
            "permissions": {"shell": "whitelist", "allowed_commands": ["ls", "echo"]},
        },
        project_root=tmp_path,
    )
    sandbox = Sandbox(cfg)
    ok, _ = sandbox.check_command("echo hi")
    assert ok is True
    ok2, _ = sandbox.check_command("curl http://x")
    assert ok2 is False


def test_endpoint_whitelist(tmp_path):
    from kurotutor.config.loader import load_config_from_data

    cfg = load_config_from_data(
        {"permissions": {"model_endpoints": ["https://api.deepseek.com"]}},
        project_root=tmp_path,
    )
    sandbox = Sandbox(cfg)
    assert sandbox.check_endpoint("https://api.deepseek.com/v1/chat") is True
    assert sandbox.check_endpoint("https://evil.com") is False


def test_student_scoped_paths(tmp_path):
    """学生子目录隔离：不同学生的同名校验互不越界，产物落在各自 u<id>/ 下。"""
    from kurotutor.config.loader import load_config_from_data

    cfg = load_config_from_data({"workspace": str(tmp_path / "ws")}, project_root=tmp_path)
    sa = Sandbox(cfg, student_id=1)
    sb = Sandbox(cfg, student_id=2)
    pa = sa.student_path("lessons/二次函数.docx", for_write=True)
    pb = sb.student_path("lessons/二次函数.docx", for_write=True)
    assert "u1" in str(pa) and "u2" in str(pb)
    assert pa != pb  # 同名文件不再互相覆盖
    # 越界仍然被拦：学生目录之外不可写
    import pytest

    from kurotutor.core.errors import SandboxError

    with pytest.raises(SandboxError):
        sa.student_path("../u2/lessons/x.docx", for_write=True)
    # 无学生上下文（CLI 运维）退化为全局工作区
    sg = Sandbox(cfg)
    assert str(sg.student_path("x")) == str(sg.workspace / "x")


def test_retention_cleanup(tmp_path):
    """遗忘机制：超期消息与已完成任务被清理，近期数据保留。"""
    from datetime import UTC, datetime, timedelta

    from kurotutor.services.retention import run_retention
    from kurotutor.storage import (
        Message,
        ScheduleTask,
        Session,
        Student,
        TaskStatus,
        init_db,
        session_scope,
    )
    from kurotutor.storage.engine import build_engine

    engine = build_engine(f"sqlite:///{tmp_path / 'ret.db'}")
    init_db(engine)
    old = datetime.now(UTC) - timedelta(days=400)
    recent = datetime.now(UTC) - timedelta(days=1)
    with session_scope(engine) as db:
        st = Student(external_id="ret-test")
        db.add(st)
        db.flush()
        sess = Session(student_id=st.id, title="t")
        db.add(sess)
        db.flush()
        db.add(Message(session_id=sess.id, role="user", content="老消息", created_at=old))
        db.add(Message(session_id=sess.id, role="user", content="新消息", created_at=recent))
        db.add(ScheduleTask(kind="review", fire_at=old, status=TaskStatus.DONE))
        db.add(ScheduleTask(kind="review", fire_at=old, status=TaskStatus.PENDING))  # 未完成不清理
    out = run_retention(engine, message_days=180, task_days=90)
    assert out["messages_deleted"] == 1 and out["tasks_deleted"] == 1
    with session_scope(engine) as db:
        texts = [m.content for m in db.exec(select(Message)).all()]
        statuses = [t.status for t in db.exec(select(ScheduleTask)).all()]
    assert texts == ["新消息"]
    assert statuses == ["pending"]
