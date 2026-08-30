"""配置系统测试。"""

from __future__ import annotations

from kurotutor.config import redact
from kurotutor.config.loader import load_config_from_data


def _data(**overrides) -> dict:
    base = {
        "models": {"llm": {"provider": "openai", "model": "m", "api_key": "sk-test1234"}},
        "channel": {"app_id": "app", "secret": "secret-xyz"},
    }
    base.update(overrides)
    return base


def test_defaults_when_empty(tmp_path):
    cfg = load_config_from_data({}, project_root=tmp_path)
    assert cfg.name == "KuroTutor"
    assert cfg.permissions.shell == "allow"
    assert cfg.permissions.file_access == "workspace_only"
    assert cfg is not None


def test_relative_paths_resolved_to_abs(tmp_path):
    cfg = load_config_from_data(_data(), project_root=tmp_path)
    assert cfg.workspace == str(tmp_path / "data" / "workspaces")
    assert cfg.kb.path == str(tmp_path / "data" / "kb")


def test_redact_masks_secret_and_keys(tmp_path):
    cfg = load_config_from_data(_data(), project_root=tmp_path)
    r = redact(cfg)
    assert r.channel.secret == "secr****"
    assert r.models.llm.api_key == "sk-t****"
    # 原始对象未被修改
    assert cfg.channel.secret == "secret-xyz"


def test_env_override(tmp_path, monkeypatch):
    monkeypatch.setenv("KURO_MODELS__LLM__MODEL", "deepseek-v3")
    monkeypatch.setenv("KURO_CHANNEL__APP_ID", "env-app")
    cfg = load_config_from_data(_data(), project_root=tmp_path)
    assert cfg.models.llm.model == "deepseek-v3"
    assert cfg.channel.app_id == "env-app"


def test_validate_flags_echo_key_not_required(tmp_path):
    # echo 离线模型不需要密钥，不应校验报错
    cfg = load_config_from_data(
        {"models": {"llm": {"provider": "echo", "model": "echo"}}}, project_root=tmp_path
    )
    issues = cfg.model_validate_extra()
    api_keys = [i for i in issues if i.field == "models.llm.api_key"]
    assert api_keys == []


def test_validate_flags_working_provider_without_key(tmp_path):
    cfg = load_config_from_data(
        {"models": {"llm": {"provider": "openai", "model": "m"}}}, project_root=tmp_path
    )
    issues = cfg.model_validate_extra()
    assert any(i.field == "models.llm.api_key" for i in issues)
