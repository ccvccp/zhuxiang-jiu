"""zhuyuan probe: 性能探针基座（80号·T2.1, 本会话截图实战沉淀）

Playwright 基座: 微信 UA + networkidle 等待 + 关键选择器定位 + 首屏截图。
坑位沉淀: Playwright 浏览器二进制缺失 → channel="msedge" 系统浏览器零下载。
T2.2 转化探针/T2.3 信任探针在本基座上加检查器。
"""
import os

DEFAULT_URL = "https://zyjiu.com/product-detail.html?id=1"
_SHOT_DIR = os.path.join(os.environ.get("TEMP", "."), "zhuyuan-probe")


def probe_page(url=DEFAULT_URL, selectors=(), timeout=40000, shot_name=None):
    """单页探针: 打开→等渲染→选择器定位→(可选)首屏截图。

    返回 {"url", "ok", "title", "found": {selector: bool}, "shot": path|None}。
    页面加载失败抛出的异常由调用方处理; ok=渲染完成。
    """
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="msedge")
        ctx = browser.new_context(
            viewport={"width": 390, "height": 844}, device_scale_factor=2,
            user_agent=("Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) "
                        "MicroMessenger/8.0.42"))
        page = ctx.new_page()
        page.goto(url, wait_until="networkidle", timeout=timeout)
        page.wait_for_timeout(2000)
        found = {sel: page.locator(sel).count() > 0 for sel in selectors}
        shot = None
        if shot_name:
            os.makedirs(_SHOT_DIR, exist_ok=True)
            shot = os.path.join(_SHOT_DIR, shot_name)
            page.screenshot(path=shot)
        title = page.title()
        browser.close()
    return {"url": url, "ok": True, "title": title, "found": found, "shot": shot}


def run(args=None):
    """zhuyuan probe CLI: 基座演示——线上详情页+关键选择器定位。"""
    url = getattr(args, "url", None) or DEFAULT_URL
    sels = ("[class*='price']", "[class*='buy']", "[class*='btn']")
    try:
        r = probe_page(url=url, selectors=sels, shot_name="t21-probe.png")
    except Exception as exc:  # noqa: BLE001 探针对环境故障如实报告
        print(f"[zhuyuan probe] 探针失败: {exc}")
        return 1
    print(f"[zhuyuan probe] 探针成功: {r['url']}")
    print(f"  页面标题: {r['title']}")
    for sel, hit in r["found"].items():
        print(f"  {'✓' if hit else '✗'} {sel}")
    if r["shot"]:
        print(f"  首屏截图: {r['shot']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
