"""pytest 共享 fixture。

所有测试用临时目录 + 独立 DB，避免污染项目 data/。
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from kurotutor.config.loader import load_config_from_data
from kurotutor.storage import build_db_url, build_engine, init_db
from kurotutor.tools import build_default_registry

# 一份最小可用配置（echo 离线模型，无需密钥）
_CONFIG = {
    "name": "kurotutor",
    "version": "0.1.0",
    "workspace": "data/workspaces",
    "data_dir": "data",
    "models": {"llm": {"provider": "echo", "model": "echo", "api_key": ""}},
    "permissions": {"shell": "deny", "file_access": "workspace_only", "model_endpoints": []},
}


@pytest.fixture
def config(tmp_path: Path):
    return load_config_from_data(json.loads(json.dumps(_CONFIG)), project_root=tmp_path)


@pytest.fixture
def engine(config):
    eng = build_engine(build_db_url(config.data_dir))
    init_db(eng)
    return eng


@pytest.fixture
def registry():
    return build_default_registry()


@pytest.fixture
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


def run_async(coro):
    loop = asyncio.new_event_loop()
    try:
        coro_result = loop.run_until_complete(coro)
        return coro_result
    finally:
        loop.close()
