import asyncio
from kurotutor.agent.context import ToolContext
from kurotutor.agent.sandbox import Sandbox
from kurotutor.tools.registry import build_default_registry
from kurotutor.config.loader import load_config
from kurotutor.storage.engine import build_engine, init_db
from kurotutor.storage import Student, session_scope
from sqlmodel import select

async def main():
    cfg = load_config()
    engine = build_engine('sqlite:///' + cfg.data_dir.replace(chr(92),'/') + '/kurotutor.db')
    init_db(engine)
    with session_scope(engine) as db:
        st = db.exec(select(Student).where(Student.external_id=='demo1')).first()
        sid = st.id
    with session_scope(engine) as db:
        student = db.get(Student, sid)
    ctx = ToolContext(config=cfg, engine=engine, sandbox=Sandbox(cfg), logger=None, student=student)
    reg = build_default_registry()
    out = await reg.execute(ctx, 'split_document', {'path': 'incoming/physics_set.pdf'})
    lines = out.split('\n')
    print(lines[0]); print(lines[-1])

asyncio.run(main())
