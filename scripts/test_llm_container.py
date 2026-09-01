import asyncio
import json
import sys

sys.path.insert(0, "/opt/venv/lib/python3.11/site-packages")


async def main():
    from kurotutor.config.loader import load_config
    from kurotutor.services.llm import build_llm_provider, ChatMessage

    cfg = load_config()
    llm = build_llm_provider(cfg.models.llm)
    try:
        r = await llm.complete([ChatMessage(role="user", content="回复两个字：正常")])
        print("LLM OK:", (r.content or "")[:80])
    except Exception as e:
        print(f"LLM FAIL: {type(e).__name__}: {str(e)[:300]}")
    finally:
        await llm.aclose()


asyncio.run(main())
