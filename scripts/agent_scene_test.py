# -*- coding: utf-8 -*-
"""Agent 真实场景测试 v2：包装 registry.execute 记录每一轮真实工具调用。

上一版用 resp.tool_calls 断言，但它只含 Agent 最后一轮——多轮循环中的工具调用
全部漏记（S6 实际生成了 PPT 却被误判失败）。本版以「记录到的工具调用 + 落盘副作用」为准。
"""

import asyncio
import io
import sys
import time
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from kurotutor.agent.entry import MessageEntry
from kurotutor.config.loader import load_config
from kurotutor.storage import Student, QuestionItem, session_scope
from kurotutor.storage.engine import build_engine, init_db
from kurotutor.tools.registry import build_default_registry
from sqlmodel import select

WORKSPACE = Path("data/workspaces")
TOOL_LOG: list[str] = []


async def main():
    cfg = load_config()
    engine = build_engine("sqlite:///" + cfg.data_dir.replace(chr(92), "/") + "/kurotutor.db")
    init_db(engine)
    reg = build_default_registry()

    # 包装：记录每次真实工具执行
    original_execute = reg.execute

    async def recording_execute(ctx, name, kwargs=None):
        TOOL_LOG.append(name)
        return await original_execute(ctx, name, kwargs)

    reg.execute = recording_execute

    entry = MessageEntry(cfg, reg, engine)
    with session_scope(engine) as db:
        st = db.exec(select(Student).where(Student.external_id == "demo1")).first()
        sid = st.id

    session_id = None
    results = []

    async def scene(name, expect_any, check=None, **kw):
        """expect_any：本场景应至少调用其中任意一个工具（列表可空）。"""
        nonlocal session_id
        mark = len(TOOL_LOG)
        t0 = time.time()
        resp = await entry.handle(student_id=sid, session_id=session_id, **kw)
        called = TOOL_LOG[mark:]
        ok, note = True, ""
        if expect_any and not (set(called) & set(expect_any)):
            ok = False
            note = f"未调用预期工具（预期任一：{expect_any}，实际：{called or '无'}）"
        if not resp.text.strip():
            ok = False
            note += " 回复为空"
        if not resp.ok:
            ok = False
            note += f" error={resp.error}"
        if check:
            extra = check(resp.text)
            if extra:
                ok = False
                note = (note + "；" if note else "") + extra
        results.append((name, ok, called, round(time.time() - t0, 1), resp.text))
        print(f"\n{'✅' if ok else '❌'} {name}  [{round(time.time() - t0, 1)}s]  工具链：{' → '.join(called) or '无'}")
        print(f"   回复：{resp.text[:150].replace(chr(10), ' ')}")
        if note:
            print(f"   ⚠️ {note}")

    # S1 讲题（纯文本，允许直接回答）
    await scene("S1 讲题（文本·引导式）", [], text="老师，勾股定理是什么？给我讲讲，先别给太难的内容")

    # S2 拍照解题
    img_math = str(WORKSPACE / "incoming" / "test_math.png")
    await scene("S2 拍照解题", ["solve_photo"],
                text="老师这道题我不会，帮我看看", images=[img_math])

    # S3 题集录入（真实整卷图）
    img_paper = str(WORKSPACE / "incoming" / "real_physics.png")
    await scene("S3 题集录入（整卷切题）", ["split_photo", "split_document"],
                text="老师，帮我把这张卷子录入题集", images=[img_paper])

    # S4 提取组卷（Word）
    exports = WORKSPACE / "exports"
    n_docx = len(list(exports.glob("*.docx"))) if exports.exists() else 0

    def _docx_added(_reply: str = ""):
        n = len(list(exports.glob("*.docx"))) if exports.exists() else 0
        return "" if n > n_docx else f"exports 未新增 docx（{n_docx}→{n}）"

    await scene("S4 提取组卷（Word）", ["bank_extract"], _docx_added,
                text="把我题集里的题整理成一份 Word 卷子发我")

    # S5 网络搜索
    await scene("S5 网络搜索", ["web_search"],
                text="上网搜一下勾股定理有哪几种证明方法，挑两种给我讲讲")

    # S6 文档生成（PPT）
    docs_dir = WORKSPACE / "docs"
    n_ppt = len(list(docs_dir.glob("*.pptx"))) if docs_dir.exists() else 0

    def _ppt_added(_reply: str = ""):
        n = len(list(docs_dir.glob("*.pptx"))) if docs_dir.exists() else 0
        return "" if n > n_ppt else f"docs 未新增 pptx（{n_ppt}→{n}）"

    await scene("S6 文档生成（PPT）", ["doc_write"], _ppt_added,
                text="帮我生成一份介绍二次函数的 PPT 课件，存到 docs 目录")

    # S7 完全没懂 → 自动录错题
    # 判定：题集条目增加，或 Agent 调用了 bank_add 且被去重护栏拦截（重复场景下的正确行为）
    with session_scope(engine) as db:
        n0 = len(db.exec(select(QuestionItem)).all())
    mark7 = len(TOOL_LOG)

    def _auto_added(reply: str = ""):
        with session_scope(engine) as db:
            n1 = len(db.exec(select(QuestionItem)).all())
        if n1 > n0:
            return ""
        if "bank_add" in TOOL_LOG[mark7:] and any(k in reply for k in ("收进题集", "已在题集", "不重复")):
            return ""  # 策略动作已发生，去重护栏拦截重复录入（重跑场景的正确行为）
        return f"题集未自动增加（{n0}→{n1}）且未见录入动作"

    await scene("S7 完全没懂→自动录错题", ["bank_add"], _auto_added,
                text="老师，刚才那道题我完全听不懂，太难受了，我是不是没救了…")

    print("\n" + "=" * 60)
    passed = sum(1 for _, ok, *_ in results if ok)
    print(f"Agent 真实场景测试：{passed}/{len(results)} 通过")
    for name, ok, called, dt, _ in results:
        print(f"  {'✅' if ok else '❌'} {name}  工具链：{' → '.join(called) or '无'}  [{dt}s]")


asyncio.run(main())
