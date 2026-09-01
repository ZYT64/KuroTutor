import asyncio
import sys

sys.path.insert(0, "/opt/venv/lib/python3.11/site-packages")


async def main():
    from pathlib import Path

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

    # 预置测试文件
    ws = Path(cfg.data_dir) / "workspaces" / "incoming"
    ws.mkdir(parents=True, exist_ok=True)

    # 测试 1 用的 PDF
    import pymupdf
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 80), "一元二次方程练习", fontsize=18)
    page.insert_text((72, 130), "1. 解方程 x^2 - 5x + 6 = 0", fontsize=13)
    page.insert_text((72, 160), "2. 已知 x^2 + kx + 6 = 0 的一个根是 2，求 k 和另一根。", fontsize=13)
    doc.save(str(ws / "complex_test.pdf"))
    doc.close()

    results = []

    def record(name, ok, detail):
        results.append((name, ok, detail))
        print(f"{'PASS' if ok else 'FAIL'} {name}")
        print(f"  {detail[:150]}")

    # ===== 测试 1：多步文档处理链 =====
    # 读 PDF → 判断是扫描还是有文字层 → 提取题目 → 出一道类似的题
    t0 = __import__("time").time()
    r = await agent.run(
        "我上传了一份PDF（incoming/complex_test.pdf），帮我读取内容，"
        "然后从里面挑一道题出给一个初中生练习，要求：只出题不给答案。"
    )
    dt = __import__("time").time() - t0
    has_tools = len(r.tool_calls) > 0 or "doc" in str(r.tool_calls)
    # Agent 应该调了 doc_read 或类似工具，回复里应该有题目
    record("多步文档处理链", r.ok and len(r.text) > 30, f"[{dt:.0f}s tools={len(r.tool_calls)}] {r.text[:120]}")

    # ===== 测试 2：OCR 识别链 =====
    # 用 OCR 读图片里的文字
    t0 = __import__("time").time()
    r = await agent.run(
        "帮我用 OCR 识别 incoming/complex_test.pdf 里的文字内容，告诉我上面写了什么题。"
    )
    dt = __import__("time").time() - t0
    record("OCR 识别链", r.ok and len(r.text) > 20, f"[{dt:.0f}s tools={len(r.tool_calls)}] {r.text[:120]}")

    # ===== 测试 3：教学全流程（出题→判分→错题入库） =====
    # 先出题
    t0 = __import__("time").time()
    r1 = await agent.run("给我出一道一元二次方程的题，因式分解法的，出完告诉我题目")
    dt1 = __import__("time").time() - t0
    has_quiz = "quiz_generate" in str(r1.tool_calls) or "题" in r1.text
    record("出题", r1.ok and has_quiz, f"[{dt1:.0f}s tools={len(r1.tool_calls)}] {r1.text[:120]}")

    # 然后故意答错，看是否正确判分并记错题
    t0 = __import__("time").time()
    r2 = await agent.run("这题的答案是 x=1 和 x=6")
    dt2 = __import__("time").time() - t0
    has_check = "quiz_check" in str(r2.tool_calls) or "错" in r2.text or "不对" in r2.text
    record("判分+错题入库", r2.ok and has_check, f"[{dt2:.0f}s tools={len(r2.tool_calls)}] {r2.text[:150]}")

    # ===== 测试 4：多工具协作（查时间 + 写文档 + 读回来） =====
    t0 = __import__("time").time()
    r = await agent.run(
        "帮我做三件事："
        "1. 查一下现在几点了；"
        "2. 在工作区写一个文件 study_log.txt，内容是'今天学了因式分解法解一元二次方程'；"
        "3. 读回这个文件确认内容。"
    )
    dt = __import__("time").time() - t0
    tool_count = len(r.tool_calls)
    has_multi = tool_count >= 2 or ("study_log" in r.text and "时间" in r.text)
    record("多工具协作", r.ok and has_multi, f"[{dt:.0f}s tools={tool_count}] {r.text[:120]}")

    # ===== 测试 5：错误恢复（文件不存在 → 优雅处理 → 给出替代方案） =====
    t0 = __import__("time").time()
    r = await agent.run(
        "帮我读取 incoming/这个文件不存在.pdf，如果读不到就告诉我里面可能写了什么类型的题目。"
    )
    dt = __import__("time").time() - t0
    graceful = r.ok and len(r.text) > 20  # 不崩溃且有有意义的回复
    record("错误恢复", graceful, f"[{dt:.0f}s tools={len(r.tool_calls)}] {r.text[:120]}")

    # ===== 测试 6：知识库操作（沉淀+检索） =====
    t0 = __import__("time").time()
    r1 = await agent.run(
        "把以下解题方法沉淀到知识库："
        "题型是『一元二次方程-因式分解法』，方法是『十字相乘法』，"
        "步骤是『1.移项使右边为0 2.左边因式分解 3.每个因式=0 4.求根』，"
        "易错点是『忘记检查判别式』。"
    )
    dt1 = __import__("time").time() - t0
    r2 = await agent.run("搜索一下知识库里关于一元二次方程的解题方法")
    dt2 = __import__("time").time() - t0
    has_deposit = "kb_deposit" in str(r1.tool_calls) or "沉淀" in r1.text or "已" in r1.text
    has_search = "kb_search" in str(r2.tool_calls) or "十字" in r2.text or "因式" in r2.text
    record("知识库沉淀+检索", r1.ok and has_deposit and has_search,
           f"[沉淀 {dt1:.0f}s + 检索 {dt2:.0f}s] 检索结果: {r2.text[:100]}")

    # ===== 汇总 =====
    passed = sum(1 for _, ok, _ in results if ok)
    print(f"\n{'='*60}")
    print(f"复杂场景测试: {passed}/{len(results)} 通过")
    for name, ok, _ in results:
        print(f"  {'✅' if ok else '❌'} {name}")


asyncio.run(main())
