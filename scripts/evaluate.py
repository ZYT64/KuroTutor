"""KuroTutor 评测集：固定用例驱动真实 Agent 循环，逐条打分输出报告。

用法：
  python scripts/evaluate.py                    # 全部用例（真实模型调用，注意消耗 token）
  python scripts/evaluate.py --filter 诊断       # 只跑名称含关键字的用例
评测集在 evals/eval_cases.json（开源后社区可补充用例）。
判定：预期工具被调用（任一）+ 回复包含预期关键词（任一）。
"""
import asyncio
import io
import json
import sys
import time
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from sqlmodel import select  # noqa: E402

from kurotutor.agent.entry import MessageEntry  # noqa: E402
from kurotutor.config.loader import load_config  # noqa: E402
from kurotutor.storage import Student, session_scope  # noqa: E402
from kurotutor.storage.engine import build_engine, init_db  # noqa: E402
from kurotutor.tools.registry import build_default_registry  # noqa: E402

CASES_FILE = ROOT / "evals" / "eval_cases.json"
EVAL_STUDENT = "eval-student"


async def run(filter_kw: str | None) -> int:
    cases = json.loads(CASES_FILE.read_text(encoding="utf-8"))
    if filter_kw:
        cases = [c for c in cases if filter_kw in c["name"]]
    if not cases:
        print("没有匹配的评测用例。")
        return 1

    cfg = load_config()
    engine = build_engine("sqlite:///" + cfg.data_dir.replace("\\", "/") + "/kurotutor.db")
    init_db(engine)
    reg = build_default_registry()
    tool_log: list[str] = []
    original = reg.execute

    async def rec(ctx, name, kwargs=None):
        tool_log.append(name)
        return await original(ctx, name, kwargs)

    reg.execute = rec
    entry = MessageEntry(cfg, reg, engine)

    with session_scope(engine) as db:
        st = db.exec(select(Student).where(Student.external_id == EVAL_STUDENT)).first()
        if st is None:
            st = Student(external_id=EVAL_STUDENT, nickname="评测同学")
            db.add(st)
            db.flush()
        sid = st.id

    session_id = None
    passed = 0
    report = []
    for case in cases:
        tool_log.clear()
        t0 = time.time()
        resp = await entry.handle(student_id=sid, session_id=session_id, text=case["message"])
        called = list(tool_log)
        # session 延续：评测用例各自独立话题，不强制新会话（分段逻辑自行处理）
        expect_tools = case.get("expect_tools") or []
        expect_kw = case.get("expect_keywords") or []
        tool_ok = (not expect_tools) or bool(set(called) & set(expect_tools))
        kw_ok = (not expect_kw) or any(k in resp.text for k in expect_kw)
        ok = tool_ok and kw_ok and bool(resp.text.strip())
        passed += ok
        entry_rec = {
            "name": case["name"],
            "ok": ok,
            "tools": called,
            "seconds": round(time.time() - t0, 1),
            "reply_head": resp.text[:150],
        }
        report.append(entry_rec)
        mark = '✅' if ok else '❌'
        tool_str = ' → '.join(called) or '无'
        print(f"{mark} {case['name']} [{entry_rec['seconds']}s] 工具：{tool_str}")
        if not ok:
            print(f"   ⚠️ tools_ok={tool_ok} keywords_ok={kw_ok}（{expect_kw}）")
        print(f"   {resp.text[:110].replace(chr(10), ' ')}")

    print("\n" + "=" * 60)
    print(f"评测结果：{passed}/{len(cases)} 通过")
    out = ROOT / "evals" / "last_report.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps({"passed": passed, "total": len(cases), "cases": report}, ensure_ascii=False, indent=2)
    out.write_text(payload, encoding="utf-8")
    print(f"报告：{out}")
    return 0 if passed == len(cases) else 1


def main():
    kw = None
    if "--filter" in sys.argv:
        kw = sys.argv[sys.argv.index("--filter") + 1]
    sys.exit(asyncio.run(run(kw)))


if __name__ == "__main__":
    main()
