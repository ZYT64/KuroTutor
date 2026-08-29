# -*- coding: utf-8 -*-
"""诊断闭环复测：修复 LLM 流式后，场景 7（开始）→ 场景 8（提交答卷）。"""
import asyncio
import io
import sys
import time
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from sqlmodel import select

from kurotutor.agent.entry import MessageEntry
from kurotutor.config.loader import load_config
from kurotutor.storage import Session, Student, session_scope
from kurotutor.storage.engine import build_engine, init_db
from kurotutor.tools.registry import build_default_registry

TOOL_LOG: list[str] = []


async def main():
    cfg = load_config()
    engine = build_engine("sqlite:///" + cfg.data_dir.replace(chr(92), "/") + "/kurotutor.db")
    init_db(engine)
    reg = build_default_registry()
    original = reg.execute

    async def rec(ctx, name, kwargs=None):
        TOOL_LOG.append(name)
        return await original(ctx, name, kwargs)

    reg.execute = rec
    entry = MessageEntry(cfg, reg, engine)

    with session_scope(engine) as db:
        st = db.exec(select(Student).where(Student.external_id == "final-demo")).first()
        sid = st.id
        sess = db.exec(select(Session).where(Session.student_id == sid).order_by(Session.id.desc())).first()
        session_id = sess.id if sess else None

    async def scene(name, expect_any, **kw):
        mark = len(TOOL_LOG)
        t0 = time.time()
        resp = await entry.handle(student_id=sid, session_id=session_id, **kw)
        called = TOOL_LOG[mark:]
        ok = bool(set(called) & set(expect_any)) and bool(resp.text.strip())
        print(f"\n{'✅' if ok else '❌'} {name}  [{time.time()-t0:.0f}s]  工具链：{' → '.join(called) or '无'}", flush=True)
        print(f"   回复：{resp.text[:300].replace(chr(10), ' ')}", flush=True)
        return ok

    ok7 = await scene("7R. 入学诊断·开始", ["diagnostic_start"], text="来，正式开始摸底测试吧")
    ok8 = await scene("8R. 入学诊断·提交答卷", ["diagnostic_submit"],
                      text="第1题：-1；第2题：x=5或x=-1；第3题：化简得1/(x-1)，代入x=2得1；第4题：k=2，交点(1,4)；第5题：A(−1,0)，B(3,0)，P(1,−4)")
    print("\n" + "=" * 50, flush=True)
    print(f"诊断闭环复测：{'✅ 全部通过' if ok7 and ok8 else '❌ 仍存在问题'}", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
