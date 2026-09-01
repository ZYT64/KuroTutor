import asyncio
import sys

sys.path.insert(0, "/opt/venv/lib/python3.11/site-packages")


async def main():
    from kurotutor.config.loader import load_config
    from kurotutor.storage import Student, session_scope
    from kurotutor.storage.engine import build_engine, init_db
    from kurotutor.tools.registry import build_default_registry
    from kurotutor.agent.core import Agent
    from sqlmodel import select

    cfg = load_config()
    engine = build_engine("sqlite:///" + cfg.data_dir.replace("\\", "/") + "/kurotutor.db")
    init_db(engine)

    with session_scope(engine) as db:
        st = db.exec(select(Student).where(Student.external_id == "960FB34CF6AF1597959D918A5D90A79E")).first()
        if st is None:
            st = db.exec(select(Student)).first()
    with session_scope(engine) as db:
        student = db.get(Student, st.id)

    registry = build_default_registry()
    agent = Agent(cfg, registry, engine, student=student)

    tests = [
        ("纯对话", "你好"),
        ("代码工具", "用代码算 2 的 10 次方"),
        ("查时间", "现在几点了"),
    ]
    for name, msg in tests:
        try:
            r = await agent.run(msg)
            ok = r.ok and len(r.text) > 3
            print(f"{'PASS' if ok else 'FAIL'} {name}: {r.text[:80]}")
        except Exception as e:
            print(f"FAIL {name}: {type(e).__name__}: {str(e)[:150]}")


asyncio.run(main())
