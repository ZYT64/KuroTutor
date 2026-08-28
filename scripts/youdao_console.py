# -*- coding: utf-8 -*-
"""有道智云控制台自动化：连接已打开的 Edge（CDP 9222），检查登录态并找「精品题库」开通入口。

用法：
  python scripts/youdao_console.py check    # 打印页面状态与登录态
  python scripts/youdao_console.py open     # 导航到精品题库/服务列表页并截图
  python scripts/youdao_console.py click <文本>  # 点含指定文本的按钮/链接
"""
import asyncio
import sys
import io

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

CDP = "http://localhost:9222"


async def get_page():
    from playwright.async_api import async_playwright

    pw = await async_playwright().start()
    browser = await pw.chromium.connect_over_cdp(CDP)
    ctx = browser.contexts[0]
    page = ctx.pages[0] if ctx.pages else await ctx.new_page()
    return pw, browser, page


async def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "check"
    pw, browser, page = await get_page()
    try:
        if cmd == "check":
            print("当前 URL:", page.url)
            print("标题:", await page.title())
            body = await page.inner_text("body")
            low = body.lower()
            login_hints = ["登录", "注册", "扫码"]
            logged_hints = ["控制台", "我的应用", "退出", "账户"]
            print("疑似未登录标记:", [h for h in login_hints if h in body][:4])
            print("疑似已登录标记:", [h for h in logged_hints if h in body][:4])
            print("--- 页面关键文本（前 600 字）---")
            print(body[:600])
        elif cmd == "goto":
            url = sys.argv[2]
            await page.goto(url, wait_until="domcontentloaded", timeout=30000)
            await page.wait_for_timeout(2500)
            print("已导航:", page.url)
            print((await page.inner_text("body"))[:600])
        elif cmd == "click":
            text = sys.argv[2]
            locator = page.get_by_text(text, exact=False).first
            await locator.click(timeout=8000)
            await page.wait_for_timeout(2500)
            print(f"已点击「{text}」；当前 URL:", page.url)
            print((await page.inner_text("body"))[:600])
        elif cmd == "shot":
            await page.screenshot(path=sys.argv[2] if len(sys.argv) > 2 else "data/edge_shot.png", full_page=False)
            print("截图完成")
        elif cmd == "text":
            # 找页面上所有含指定文本的元素（调试用）
            t = sys.argv[2]
            els = await page.get_by_text(t, exact=False).all()
            for e in els[:8]:
                print(" 元素:", (await e.inner_text())[:50].replace(chr(10), " "), "|", await e.evaluate("e=>e.tagName"))
    finally:
        await pw.stop()


asyncio.run(main())
