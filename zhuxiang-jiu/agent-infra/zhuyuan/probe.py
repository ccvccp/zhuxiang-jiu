"""zhuyuan probe: 性能探针（80号·T2.1 基座+T2.2 转化探针, 本会话截图实战沉淀）

Playwright 基座: 微信 UA + networkidle 等待 + 关键选择器定位 + 首屏截图。
坑位沉淀: Playwright 浏览器二进制缺失 → channel="msedge" 系统浏览器零下载。
T2.2 转化探针: 购物流程关键元素存在性 + 立即购买按钮首屏可见性。
  - 选择器取证自 product-detail.html 真实 DOM(价格/规格/数量/双购买按钮)
  - 反例验证: service.html 真实内容页无商品卡容器, renderProductCard 不触发
T2.3 信任探针在本基座上加检查器。
"""
import os

DEFAULT_URL = "https://zyjiu.com/product-detail.html?id=1"
_SHOT_DIR = os.path.join(os.environ.get("TEMP", "."), "zhuyuan-probe")
_UA = ("Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) "
       "MicroMessenger/8.0.42")
VIEWPORT_W, VIEWPORT_H = 390, 844

# 购物流程关键元素(T2.2): (key, 名称, 选择器, 缺失分级)
# P0=转化断链(价格/购买按钮缺=无法下单), P1=体验缺口(规格/数量)
CONVERSION_ELEMENTS = (
    ("price_now", "现价", ".price-now", "P0"),
    ("spec_options", "规格选择", ".spec-selector .spec", "P1"),
    ("qty_input", "数量选择", "#qtyInput", "P1"),
    ("add_cart", "加入购物车按钮", "button:has-text('加入购物车')", "P0"),
    ("buy_now", "立即购买按钮", "button:has-text('立即购买')", "P0"),
)


def _new_context(browser):
    """微信 UA 移动视口(390x844, 整改截图实战同款)。"""
    return browser.new_context(
        viewport={"width": VIEWPORT_W, "height": VIEWPORT_H},
        device_scale_factor=2, user_agent=_UA)


def _take_shot(page, shot_name):
    if not shot_name:
        return None
    os.makedirs(_SHOT_DIR, exist_ok=True)
    shot = os.path.join(_SHOT_DIR, shot_name)
    page.screenshot(path=shot)
    return shot


def probe_page(url=DEFAULT_URL, selectors=(), timeout=40000, shot_name=None):
    """单页探针(T2.1 基座): 打开→等渲染→选择器定位→(可选)首屏截图。

    返回 {"url", "ok", "title", "found": {selector: bool}, "shot": path|None}。
    页面加载失败抛出的异常由调用方处理; ok=渲染完成。
    """
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="msedge")
        page = _new_context(browser).new_page()
        page.goto(url, wait_until="networkidle", timeout=timeout)
        page.wait_for_timeout(2000)
        found = {sel: page.locator(sel).count() > 0 for sel in selectors}
        shot = _take_shot(page, shot_name)
        title = page.title()
        browser.close()
    return {"url": url, "ok": True, "title": title, "found": found, "shot": shot}


def probe_conversion(url=DEFAULT_URL, timeout=40000, shot_name=None):
    """转化探针(T2.2): 购物流程关键元素存在性+立即购买首屏可见性。

    返回 {"url", "ok", "pass", "title", "elements", "missing", "findings",
          "buy_first_screen", "buy_box", "shot"}:
      - pass=五关键元素齐备(转化路径不断链)
      - findings: P0=缺价格/购买按钮, P1=缺规格/数量, P2=购买按钮不在首屏(体验)
      - buy_first_screen=立即购买渲染盒完全落在首屏高(844)内
    页面加载失败抛出的异常由调用方处理。
    """
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="msedge")
        page = _new_context(browser).new_page()
        page.goto(url, wait_until="networkidle", timeout=timeout)
        page.wait_for_timeout(2000)
        elements = {
            key: page.locator(sel).count() > 0
            for key, _name, sel, _prio in CONVERSION_ELEMENTS}
        buy_box = None
        buy_first_screen = False
        if elements["buy_now"]:
            box = page.locator(
                "button:has-text('立即购买')").first.bounding_box()
            if box:
                buy_box = box
                buy_first_screen = box["y"] + box["height"] <= VIEWPORT_H
        shot = _take_shot(page, shot_name)
        title = page.title()
        browser.close()
    missing = [key for key, hit in elements.items() if not hit]
    findings = [
        {"level": prio, "msg": f"购物流程缺失{name}({key})"}
        for key, name, _sel, prio in CONVERSION_ELEMENTS if not elements[key]]
    if elements["buy_now"] and not buy_first_screen:
        y = int(buy_box["y"]) if buy_box else -1
        findings.append({
            "level": "P2",
            "msg": (f"立即购买按钮不在首屏(y={y}, 首屏高{VIEWPORT_H})"
                    "——需滚动才可见")})
    return {"url": url, "ok": True, "pass": not missing, "title": title,
            "elements": elements, "missing": missing, "findings": findings,
            "buy_first_screen": buy_first_screen, "buy_box": buy_box,
            "shot": shot}


def run(args=None):
    """zhuyuan probe CLI: 转化探针——购物流程关键元素+购买按钮首屏可见性。"""
    url = getattr(args, "url", None) or DEFAULT_URL
    try:
        r = probe_conversion(url=url, shot_name="t22-conversion.png")
    except Exception as exc:  # noqa: BLE001 探针对环境故障如实报告
        print(f"[zhuyuan probe] 探针失败: {exc}")
        return 1
    print(f"[zhuyuan probe] 转化探针成功: {r['url']}")
    print(f"  页面标题: {r['title']}")
    print("  购物流程关键元素:")
    for key, name, sel, _prio in CONVERSION_ELEMENTS:
        hit = r["elements"][key]
        print(f"    {'✓' if hit else '✗'} {name} {sel}")
    if r["buy_box"]:
        mark = "✓" if r["buy_first_screen"] else "✗"
        print(f"  {mark} 立即购买首屏可见 (y={int(r['buy_box']['y'])}, "
              f"首屏高{VIEWPORT_H})")
    else:
        print("  ✗ 立即购买按钮未渲染")
    for f in r["findings"]:
        print(f"  [{f['level']}] {f['msg']}")
    if r["shot"]:
        print(f"  首屏截图: {r['shot']}")
    verdict = "PASS——购物流程关键元素齐备" if r["pass"] else \
        f"FAIL——缺失 {', '.join(r['missing'])}"
    print(f"  转化结论: {verdict}")
    return 0 if r["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(run())
