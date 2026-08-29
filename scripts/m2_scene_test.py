# -*- coding: utf-8 -*-
"""M2 真实场景测试：课堂（排课/备课/闭环/改期）、周报、校本同步，走真实 Agent 循环（GLM）。"""
import asyncio
import io
import sys
import time
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from kurotutor.agent.entry import MessageEntry
from kurotutor.config.loader import load_config
from kurotutor.storage import Student, session_scope
from kurotutor.storage.engine import build_engine, init_db
from kurotutor.tools.registry import build_default_registry
from sqlmodel import select

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
        st = db.exec(select(Student).where(Student.external_id == "demo1")).first()
        sid = st.id

    session_id = None
    results = []

    async def scene(name, expect_any, check=None, **kw):
        nonlocal session_id
        mark = len(TOOL_LOG)
        t0 = time.time()
        resp = await entry.handle(student_id=sid, session_id=session_id, **kw)
        called = TOOL_LOG[mark:]
        ok, note = True, ""
        if expect_any and not (set(called) & set(expect_any)):
            ok = False
            note = f"未调用预期工具（{expect_any}，实际 {called or '无'}）"
        if not resp.text.strip():
            ok, note = False, note + " 回复为空"
        if check:
            extra = check(resp.text)
            if extra:
                ok = False
                note = (note + "；" if note else "") + extra
        results.append((name, ok, called, round(time.time() - t0, 1), resp.text))
        print(f"\n{'✅' if ok else '❌'} {name}  [{time.time()-t0:.0f}s]  工具链：{' → '.join(called) or '无'}")
        print(f"   回复：{resp.text[:160].replace(chr(10), ' ')}")
        if note:
            print(f"   ⚠️ {note}")

    # M2-1 排课（单堂）
    await scene(
        "M2-1 排课（单堂）", ["schedule_class"],
        text="老师，帮我约一节数学课，讲二次函数图像，这周日晚上七点",
    )
    # M2-2 查课
    await scene("M2-2 查课表", ["course_list"], text="我现在有什么课来着？")

    # M2-3 备课（手动触发，验证备课→讲义落盘）
    def _lesson_file(_reply: str = ""):
        p = Path(cfg.workspace) / "lessons"
        return "" if p.exists() and list(p.glob("*.md")) else "lessons 目录没有讲义"

    await scene("M2-3 备课（讲义落盘）", ["prepare_class"], _lesson_file,
                text="帮我提前把那节课备好，我想先看看讲义")

    # M2-4 校本同步
    await scene("M2-4 校本同步", ["school_sync"],
                text="对了老师，我们学校用北师大版教材，现在讲到全等三角形，月底有期中考")

    # M2-5 周报
    def _docx(_reply: str = ""):
        p = Path(cfg.workspace) / "exports"
        docs = list(p.glob("学习周报*.docx")) if p.exists() else []
        return "" if docs else "exports 没有周报 docx"

    await scene("M2-5 学习周报（Word 导出）", ["weekly_report"], _docx,
                text="老师，把我的周报生成一份吧，我想看看这周学得怎么样")

    # M2-6 系列课（大纲设计）
    await scene("M2-6 系列课（大纲设计）", ["schedule_class"],
                text="老师，我想报一个 3 节课的物理系列课，目标是期中考上 85 分，从下周六开始每周六下午两点")

    # M2-7 应急改期
    await scene("M2-7 应急改期", ["reschedule_class", "course_list"],
                text="那节数学课时间不合适，帮我改到下周一晚上八点")

    print("\n" + "=" * 60)
    passed = sum(1 for _, ok, *_ in results if ok)
    print(f"M2 真实场景测试：{passed}/{len(results)} 通过")
    for name, ok, called, dt, _ in results:
        print(f"  {'✅' if ok else '❌'} {name}  工具链：{' → '.join(called) or '无'}  [{dt}s]")


asyncio.run(main())
