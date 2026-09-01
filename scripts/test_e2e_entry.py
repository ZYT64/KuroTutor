import asyncio
import sys

sys.path.insert(0, "/opt/venv/lib/python3.11/site-packages")

EXT_ID = "960FB34CF6AF1597959D918A5D90A79E"


async def main():
    import time

    from kurotutor.config.loader import load_config
    from kurotutor.storage import Student, session_scope
    from kurotutor.storage.engine import build_engine, init_db
    from kurotutor.tools.registry import build_default_registry
    from kurotutor.agent.entry import MessageEntry
    from pathlib import Path
    from sqlmodel import select

    cfg = load_config()
    engine = build_engine("sqlite:///" + cfg.data_dir.replace("\\", "/") + "/kurotutor.db")
    init_db(engine)
    registry = build_default_registry()
    entry = MessageEntry(cfg, registry, engine)

    # 找到真实学生
    with session_scope(engine) as db:
        st = db.exec(select(Student).where(Student.external_id == EXT_ID)).first()
        if st is None:
            st = db.exec(select(Student)).first()
        sid = st.id
    print(f"学生 ID: {sid}")

    # 造测试 PDF
    import pymupdf
    ws = Path(cfg.data_dir) / "workspaces" / "incoming"
    ws.mkdir(parents=True, exist_ok=True)
    test_pdf = ws / "e2e_test.pdf"
    if not test_pdf.exists():
        doc = pymupdf.open()
        page = doc.new_page()
        page.insert_text((72, 80), "Solve: 2x + 3 = 11", fontsize=16)
        doc.save(str(test_pdf))
        doc.close()

    results = []

    async def test(name, msg, expect_kw=None):
        t0 = time.time()
        try:
            resp = await entry.handle(student_id=sid, text=msg)
            dt = time.time() - t0
            ok = resp.ok and len(resp.text) > 5
            if expect_kw:
                ok = ok and any(kw in resp.text for kw in expect_kw)
            results.append((name, ok))
            print(f"{'PASS' if ok else 'FAIL'} {name} [{dt:.0f}s]")
            print(f"  {resp.text[:150]}")
        except Exception as e:
            results.append((name, False))
            print(f"FAIL {name}: {type(e).__name__}: {str(e)[:200]}")

    await test("1.日常对话", "老师好，我今天想练习一下数学", ["数学", "好", "同学", "练"])
    await test("2.OCR识别", "帮我用 ocr_read 识别 incoming/e2e_test.pdf", ["x", "方程", "11"])
    await test("3.出题", "给我出一道一元一次方程的题", ["题"])
    await test("4.答题判分", "这道题的答案是 x = 4", ["对", "错", "正确", "不"])
    await test("5.知识库", "帮我搜一下知识库里有没有关于方程的解题方法")
    await test("6.目标设置", "我这学期的目标是数学期末考到 90 分以上", ["目标"])
    await test("7.打卡", "老师我来了，打个卡", ["打卡"])
    await test("8.查时间", "现在几点了", ["2026"])
    await test("9.代码计算", "用代码帮我算 123 * 456 等于多少", ["56088"])
    await test("10.追问衔接", "上面那道题 123 * 456 的结果帮我验算一遍", ["56088", "验算", "123"])

    passed = sum(1 for _, ok in results if ok)
    print(f"\n{'='*60}")
    print(f"真实场景测试（MessageEntry 统一入口）: {passed}/{len(results)} 通过")
    for name, ok in results:
        print(f"  {'✅' if ok else '❌'} {name}")


asyncio.run(main())
