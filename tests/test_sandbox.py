"""沙箱安全测试：路径穿越、软链逃逸、命令黑名单、端点白名单。"""

from __future__ import annotations

import pytest

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
