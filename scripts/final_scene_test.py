# -*- coding: utf-8 -*-
"""最终全场景真机测试：M1/M2/M3 全清单，走真实 Agent 循环（真实模型 API），代码入口模拟学生。"""
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

    # 独立测试学生，不污染真实数据
    with session_scope(engine) as db:
        st = db.exec(select(Student).where(Student.external_id == "final-demo")).first()
        if st is None:
            st = Student(external_id="final-demo", nickname="验收同学", grade="初三")
            db.add(st)
            db.flush()
        sid = st.id

    session_id = None
    results = []

    async def scene(name, expect_any=None, check=None, **kw):
        nonlocal session_id
        if session_id is None:
            # 取该学生最新会话，保证跨场景上下文连续（模拟同一个学生连续聊天）
            with session_scope(engine) as db:
                sess = db.exec(
                    select(Session).where(Session.student_id == sid).order_by(Session.id.desc())
                ).first()
                session_id = sess.id if sess else None
        mark = len(TOOL_LOG)
        t0 = time.time()
        resp = await entry.handle(student_id=sid, session_id=session_id, **kw)
        called = TOOL_LOG[mark:]
        ok, note = True, ""
        if expect_any and not (set(called) & set(expect_any)):
            ok = False
            note = f"未调用预期工具（{expect_any}，实际 {called or '无'}）"
        if not resp.text.strip():
            ok, note = False, (note + "；" if note else "") + "回复为空"
        if check:
            extra = check(resp.text)
            if extra:
                ok = False
                note = (note + "；" if note else "") + extra
        results.append((name, ok, called, round(time.time() - t0, 1), resp.text))
        print(f"\n{'✅' if ok else '❌'} {name}  [{time.time()-t0:.0f}s]  工具链：{' → '.join(called) or '无'}", flush=True)
        print(f"   回复：{resp.text[:170].replace(chr(10), ' ')}", flush=True)
        if note:
            print(f"   ⚠️ {note}", flush=True)
        return resp

    img_dir = Path("data/workspaces/incoming")
    math_img = str(img_dir / "test_math.png")

    # ── M1 基础链路 ────────────────────────────────────────────
    await scene("1. 打招呼对话（私聊收发）", text="老师我在，今天想学点数学")
    await scene("2. 拍照解题（视觉读题→引导讲解）", expect_any=["solve_photo"], images=[math_img])

    # ── M2 闭环能力 ────────────────────────────────────────────
    await scene("3. 真题出题（web→jszkk→火花→生成 四级链）",
                text="给我出 2 道一元二次方程的题，最好是真题",
                expect_any=["quiz_generate"],
                check=lambda t: None if ("题" in t) else "回复无题目")
    await scene("4. 排课（明天晚八点讲函数）", text="帮我约一节数学课，讲函数，明天晚上八点",
                expect_any=["schedule_class"])
    await scene("5. 学习周报", text="帮我生成这周的学习周报", expect_any=["weekly_report"])
    await scene("6. 校本同步", text="我们学校用人教版教材，现在讲到二次函数", expect_any=["school_sync"])

    # ── M3 产品化 ──────────────────────────────────────────────
    r7 = await scene("7. 入学诊断·开始", text="测测我的数学水平", expect_any=["diagnostic_start"])
    await scene("8. 入学诊断·提交答卷", expect_any=["diagnostic_submit"],
                text="第1题选B，第2题选C，第3题选A，第4题选B，第5题选A")
    await scene("9. 代码沙箱（2 的 20 次方）", text="用代码帮我算一下 2 的 20 次方是多少",
                expect_any=["code_run"],
                check=lambda t: None if "1048576" in t else "回复未包含正确结果 1048576")
    await scene("10. 目标设定", text="我的目标是期末数学考到 95 分以上", expect_any=["goal_set"])
    await scene("11. 每日打卡", text="老师我来打个卡", expect_any=["daily_checkin"])

    # ── 汇总 ───────────────────────────────────────────────────
    print("\n" + "=" * 64, flush=True)
    passed = sum(1 for _, ok, *_ in results if ok)
    print(f"全场景真机测试：{passed}/{len(results)} 通过", flush=True)
    for name, ok, called, secs, _ in results:
        print(f"  {'✅' if ok else '❌'} {name}  [{secs}s]")


if __name__ == "__main__":
    asyncio.run(main())
