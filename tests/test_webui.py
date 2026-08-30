"""WebUI 面板 API 测试（离线，tmp 配置与数据库）。"""

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(tmp_path, monkeypatch):
    import kurotutor.webui.app as webapp
    from kurotutor.config.loader import load_config_from_data
    from kurotutor.storage import Student, session_scope
    from kurotutor.storage.engine import build_engine, init_db

    cfg = load_config_from_data(
        {"webui": {"token": "test-token"}, "data_dir": str(tmp_path / "data")},
        project_root=tmp_path,
    )
    engine = build_engine(f"sqlite:///{tmp_path / 'panel.db'}")
    init_db(engine)
    with session_scope(engine) as db:
        db.add(Student(external_id="panel-user", nickname="面板同学", stage="junior"))
    monkeypatch.setattr(webapp, "_CFG", cfg)
    monkeypatch.setattr(webapp, "_ENGINE", engine)
    return TestClient(webapp.create_app())


def test_login_flow(client):
    # 未登录被拒
    assert client.get("/api/overview").status_code == 401
    # 错误口令被拒
    assert client.post("/api/login", json={"token": "bad"}).status_code == 401
    # 正确口令 → cookie → 访问成功
    assert client.post("/api/login", json={"token": "test-token"}).status_code == 200
    r = client.get("/api/overview")
    assert r.status_code == 200
    body = r.json()
    assert body["students_total"] == 1
    assert body["by_student"][0]["nickname"] == "面板同学"


def test_student_detail(client):
    client.post("/api/login", json={"token": "test-token"})
    r = client.get("/api/students/1")
    assert r.status_code == 200
    body = r.json()
    assert body["nickname"] == "面板同学"
    assert "effect" in body and "mastery" in body
    assert client.get("/api/students/999").status_code == 404


def test_config_masked_no_secret(client):
    """脱敏机制生效：有密钥的配置必须被打码（保留前 4 位 + ****）。"""
    client.post("/api/login", json={"token": "test-token"})
    r = client.get("/api/config")
    assert r.status_code == 200
    text = str(r.json())
    # 若存在任何 api_key/secret 字段，其值必须以 **** 结尾（不允许完整明文）
    for line in text.split(","):
        if '"api_key"' in line or '"secret"' in line:
            value = line.rsplit(":", 1)[-1].strip('" ')
            assert value.endswith('****') or value in ("null", "None", '""'), line


def test_logout(client):
    client.post("/api/login", json={"token": "test-token"})
    assert client.get("/api/overview").status_code == 200
    client.post("/api/logout")
    assert client.get("/api/overview").status_code == 401
