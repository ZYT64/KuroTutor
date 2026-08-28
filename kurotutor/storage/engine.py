"""数据层：引擎工厂。

默认 SQLite（零配置即可运行），通过配置/环境变量可切换到 PostgreSQL。
用法::

    engine = build_engine(build_db_url(data_dir))
    init_db(engine)
    with session_scope(engine) as session:
        ...
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from sqlmodel import Session, SQLModel, create_engine

# 允许通过环境变量覆盖数据库连接串（优先级最高）
_DB_URL_ENV = "KURO_DB_URL"


def default_db_url(data_dir: str | Path) -> str:
    """生成默认 SQLite 连接串，库文件落在数据目录下。"""
    data_path = Path(data_dir)
    data_path.mkdir(parents=True, exist_ok=True)
    db_path = data_path / "kurotutor.db"
    return f"sqlite+pysqlite:///{db_path.as_posix()}"


def build_db_url(data_dir: str | Path, override: str | None = None) -> str:
    """按优先级决定数据库 URL：显式 > 环境变量 > 默认 SQLite。"""
    return override or os.environ.get(_DB_URL_ENV) or default_db_url(data_dir)


def build_engine(db_url: str) -> object:
    """按连接串构造引擎。SQLite 需要关闭跨线程检查（渠道为长连接/多线程）。"""
    kwargs: dict = {"echo": False, "future": True}
    if db_url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False}
    return create_engine(db_url, **kwargs)


def init_db(engine: object) -> None:
    """建表（幂等）。首次启动时调用。"""
    SQLModel.metadata.create_all(engine)


@contextmanager
def session_scope(engine: object) -> Iterator[Session]:
    """事务性会话上下文：正常提交，异常回滚，用完即关。

    ``expire_on_commit=False`` 让 commit 后已加载的属性仍可读，
    避免对象跨层的（如 router → entry）访问因属性过期而抛
    ``DetachedInstanceError``。需要重新查询时再显式 refresh。
    """
    session = Session(engine, expire_on_commit=False)
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
