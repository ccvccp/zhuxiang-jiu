"""zhuyuan probe: 性能探针（80号·T2.1 基座+T2.2 转化探针+T2.3 信任探针）

Playwright 基座: 微信 UA + networkidle 等待 + 关键选择器定位 + 首屏截图。
坑位沉淀: Playwright 浏览器二进制缺失 → channel="msedge" 系统浏览器零下载。
T2.2 转化探针: 购物流程关键元素存在性 + 立即购买按钮首屏可见性。
  - 选择器取证自 product-detail.html 真实 DOM(价格/规格/数量/双购买按钮)
  - 反例验证: service.html 真实内容页无商品卡容器, renderProductCard 不触发
T2.3 信任探针: 资质三件套(ICP/许可证/警示语)+产地+评分——渲染态 innerText 取证
  (页脚共享资质块=main.js renderFooter+首页 hero 警示语)。
  评分★误报防坑: ai-hub-widget 有客服满意度★按钮, 不等于商品评分——
  匹配口径收窄为 好评率/用户评分类短语 + .rating/.stars 精确类。
"""
import os
import re

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

# 信任要素(T2.3): (key, 名称, 渲染态 innerText 正则, 缺失分级)
# P0=资质三件套(合规底线), P1=产地信任信息, P2=评分体验增强
TRUST_TEXT_CHECKS = (
    ("icp", "ICP备案号", r"ICP备\d+", "P0"),
    ("license", "食品经营许可证", r"SC\d{14}", "P0"),
    ("warning", "健康警示语", r"过量饮酒|未成年|孕妇", "P0"),
    ("origin", "产地信息", r"产地", "P1"),
    ("rating", "评分展示", r"好评率|用户评分|商品评分|综合评分", "P2"),
)
# 评分类名兜底: 精确类匹配, 不用 [class*=] 子串(防 start/operating 类误报)
TRUST_RATING_SELECTORS = (".rating", ".stars")
_TRUST_P0 = ("icp", "license", "warning")

# T2.1 基座演示选择器(详情页关键 UI 类; buy 未命中=真实结构反映非故障)
_BASE_SELECTORS = ("[class*='price']", "[class*='buy']", "[class*='btn']")

# 归档目录注入点(测试隔离用; None=默认 REPO_ROOT/docs/registry/reports, 同 comply)
REPORTS_DIR = None

_LEVEL_ORDER = {"P0": 0, "P1": 1, "P2": 2}


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


def probe_trust(url=DEFAULT_URL, timeout=40000, shot_name=None):
    """信任探针(T2.3): 资质三件套(ICP/许可证/警示语)+产地+评分展示检查。

    取证口径: 页面渲染后 document.body.innerText 正则匹配——
    资质三件套来自页脚共享资质块(main.js renderFooter), 首页另有 hero 警示语;
    评分另查 .rating/.stars 精确类兜底(满意度★不算商品评分)。
    pass=P0 资质三件套齐备(合规底线); P1 产地/P2 评分缺失记 findings 不阻断。
    返回 {"url", "ok", "pass", "title", "elements", "missing", "findings", "shot"}。
    页面加载失败抛出的异常由调用方处理。
    """
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="msedge")
        page = _new_context(browser).new_page()
        page.goto(url, wait_until="networkidle", timeout=timeout)
        page.wait_for_timeout(2000)
        body_text = page.evaluate("() => document.body.innerText") or ""
        elements = {
            key: re.search(pat, body_text) is not None
            for key, _name, pat, _prio in TRUST_TEXT_CHECKS}
        if not elements["rating"]:
            elements["rating"] = any(
                page.locator(sel).count() > 0
                for sel in TRUST_RATING_SELECTORS)
        shot = _take_shot(page, shot_name)
        title = page.title()
        browser.close()
    missing = [key for key, hit in elements.items() if not hit]
    findings = [
        {"level": prio, "msg": f"信任要素缺失{name}({key})"}
        for key, name, _pat, prio in TRUST_TEXT_CHECKS if not elements[key]]
    passed = all(elements[k] for k in _TRUST_P0)
    return {"url": url, "ok": True, "pass": passed, "title": title,
            "elements": elements, "missing": missing, "findings": findings,
            "shot": shot}


def generate_probe_report(base, conv, trust, out_dir=None):
    """T2.4 探针报告(四节: 概况/通过率/失败明细+P0-P2 分级/处置建议) → 归档路径。

    base/conv/trust 为三探针返回 dict(基座 probe_page/转化 probe_conversion/
    信任 probe_trust); 基座未命中选择器记信息级 P2(真实结构反映, 非故障)。
    """
    import datetime
    try:
        from zhuyuan import audit as _a
    except ImportError:
        import audit as _a
    out_dir = out_dir or REPORTS_DIR or os.path.join(
        _a._REPO_ROOT, "docs", "registry", "reports")
    os.makedirs(out_dir, exist_ok=True)
    now = datetime.datetime.now(tz=datetime.timezone.utc).astimezone()
    path = os.path.join(out_dir, now.strftime("探针报告-%Y%m%d-%H%M%S.md"))
    # 聚合 findings: 基座未命中(信息级) + 转化 + 信任, 按 P0>P1>P2 排序
    findings = list(conv["findings"]) + list(trust["findings"])
    for sel, hit in base["found"].items():
        if not hit:
            findings.append({"level": "P2", "msg": (
                f"信息级: 基座选择器未命中 {sel}——如实反映页面真实结构, 非故障")})
    findings.sort(key=lambda f: _LEVEL_ORDER.get(f["level"], 9))
    base_hits = sum(1 for h in base["found"].values() if h)
    base_n = len(base["found"])
    conv_hits = sum(1 for h in conv["elements"].values() if h)
    conv_n = len(conv["elements"])
    trust_hits = sum(1 for h in trust["elements"].values() if h)
    trust_n = len(trust["elements"])

    def _pct(a, b):
        return f"{a}/{b} ({int(a / b * 100) if b else 100}%)"

    lines = ["# 探针报告 · 竹园 zhuyuan probe（80号·性能探针）", ""]
    lines.append(f"> 生成: {now.strftime('%Y-%m-%d %H:%M:%S')} · 目标: "
                 f"{base['url']} · 视口 {VIEWPORT_W}x{VIEWPORT_H} 微信 UA"
                 " · 三探针: 基座/转化/信任")
    lines += ["", "## 一、探针概况", ""]
    lines.append(f"- 基座探针: 渲染{'完成' if base['ok'] else '异常'} · "
                 f"标题「{base['title']}」 · 选择器命中 {_pct(base_hits, base_n)}")
    lines.append("- 转化探针: "
                 + ("PASS——购物流程关键元素齐备" if conv["pass"]
                    else f"FAIL——缺失 {', '.join(conv['missing'])}"))
    lines.append("- 信任探针: "
                 + ("PASS——资质三件套齐备(合规底线)" if trust["pass"]
                    else "FAIL——资质三件套缺失"))
    lines += ["", "## 二、各探针通过率", ""]
    lines.append("- 基座(选择器命中): " + _pct(base_hits, base_n) + " —— "
                 + " ".join(f"{'✓' if h else '✗'} {s}"
                            for s, h in base["found"].items()))
    _cname = {k: n for k, n, *_ in CONVERSION_ELEMENTS}
    _tname = {k: n for k, n, *_ in TRUST_TEXT_CHECKS}
    lines.append("- 转化(关键要素齐备): " + _pct(conv_hits, conv_n)
                 + f" · 立即购买首屏可见: {'✓' if conv['buy_first_screen'] else '✗'}")
    lines.append("  - " + " ".join(f"{'✓' if h else '✗'}{_cname[k]}"
                                    for k, h in conv["elements"].items()))
    lines.append("- 信任(要素通过): " + _pct(trust_hits, trust_n) + " —— "
                 + " ".join(f"{'✓' if h else '✗'}{_tname[k]}"
                            for k, h in trust["elements"].items()))
    lines += ["", "## 三、失败明细+P0-P2 分级", ""]
    if findings:
        for f in findings:
            lines.append(f"- [{f['level']}] {f['msg']}")
    else:
        lines.append("- 无缺失项——三探针全要素通过")
    lines += ["", "## 四、处置建议(只出不施)", ""]
    _SUGGEST = {"P0": "上线前必修——转化/合规断链",
                "P1": "排期修复——信任信息补齐",
                "P2": "人工裁量——体验/增强项"}
    grouped = {}
    for f in findings:
        grouped.setdefault(f["level"], []).append(f["msg"])
    for lv in ("P0", "P1", "P2"):
        if lv in grouped:
            lines.append(f"- {lv}({_SUGGEST[lv]}): {len(grouped[lv])} 项")
    if not findings:
        lines.append("- 无待处置项")
    lines += ["", "> **只出不施**: 本报告仅输出处置建议, 不改代码不动资产。", ""]
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))
    return path


def run(args=None):
    """zhuyuan probe CLI: 基座+转化+信任三探针(T2.1-T2.3)+T2.4 报告归档。"""
    url = getattr(args, "url", None) or DEFAULT_URL
    try:
        base = probe_page(url=url, selectors=_BASE_SELECTORS)
        conv = probe_conversion(url=url, shot_name="t22-conversion.png")
        trust = probe_trust(url=url, shot_name="t23-trust.png")
    except Exception as exc:  # noqa: BLE001 探针对环境故障如实报告
        print(f"[zhuyuan probe] 探针失败: {exc}")
        return 1
    # ---- 基座探针段(T2.1) ----
    print(f"[zhuyuan probe] 基座探针成功: {base['url']}")
    print(f"  页面标题: {base['title']}")
    for sel, hit in base["found"].items():
        print(f"    {'✓' if hit else '✗'} {sel}")
    # ---- 转化探针段(T2.2) ----
    print(f"[zhuyuan probe] 转化探针成功: {conv['url']}")
    print(f"  页面标题: {conv['title']}")
    print("  购物流程关键元素:")
    for key, name, sel, _prio in CONVERSION_ELEMENTS:
        hit = conv["elements"][key]
        print(f"    {'✓' if hit else '✗'} {name} {sel}")
    if conv["buy_box"]:
        mark = "✓" if conv["buy_first_screen"] else "✗"
        print(f"  {mark} 立即购买首屏可见 (y={int(conv['buy_box']['y'])}, "
              f"首屏高{VIEWPORT_H})")
    else:
        print("  ✗ 立即购买按钮未渲染")
    for f in conv["findings"]:
        print(f"  [{f['level']}] {f['msg']}")
    verdict = "PASS——购物流程关键元素齐备" if conv["pass"] else \
        f"FAIL——缺失 {', '.join(conv['missing'])}"
    print(f"  转化结论: {verdict}")
    # ---- 信任探针段(T2.3) ----
    print(f"[zhuyuan probe] 信任探针成功: {trust['url']}")
    print(f"  页面标题: {trust['title']}")
    print("  信任要素(资质三件套+产地+评分):")
    for key, name, _pat, prio in TRUST_TEXT_CHECKS:
        hit = trust["elements"][key]
        print(f"    {'✓' if hit else '✗'} {name} [缺失分级{prio}]")
    for f in trust["findings"]:
        print(f"  [{f['level']}] {f['msg']}")
    if trust["shot"]:
        print(f"  首屏截图: {trust['shot']}")
    p0_missing = [k for k in _TRUST_P0 if not trust["elements"][k]]
    verdict = "PASS——资质三件套齐备(合规底线)" if trust["pass"] else \
        f"FAIL——资质三件套缺失: {', '.join(p0_missing)}"
    print(f"  信任结论: {verdict}")
    # ---- 综合+报告归档(T2.4) ----
    report_path = generate_probe_report(base, conv, trust)
    print(f"[zhuyuan probe] T2.4 探针报告已归档: {report_path}")
    ok = conv["pass"] and trust["pass"]
    print(f"  综合结论: {'PASS——三探针齐跑通过' if ok else 'FAIL(见上明细)'}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(run())
