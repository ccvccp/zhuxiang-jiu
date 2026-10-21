"""zhuyuan audit: 资产盘点基线（80号·T0.3）

按《资产标准.md》七项口径扫描主站资产(html+js) → 资产台账:
占位符/SEO(title+description)/断链图/ALT 缺/空正文/关键模块(资质行+健康警示语)。
口径细则:
- 豁免清单命中 → exempt(不检);
- SPA 壳(引用 js/main.js)正文由渲染生成 → 跳过空正文判定;
- 资质行/警示语在页面源码或共享渲染资产(js/main.js)任一出现即达标(渲染后可达);
- B2 基准: 真 index(noscript 资质块)healthy; 挖空副本(无资质无 js)必检出 P0。
"""
import os
import re

try:
    from zhuyuan import cli as _cli
except ImportError:
    _REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
else:
    _REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(_cli.__file__))))

EXEMPT_PREFIXES = ("module-test", "ai-promo-radar", "ai-pdm-dashboard",
                   "ai-promo-studio", "payport-dashboard", "activity-dashboard")
_PLACEHOLDER_RE = re.compile(r"TODO|FIXME|占位|敬请期待|lorem", re.IGNORECASE)
_IMG_RE = re.compile(r"<img\s[^>]*>")
_SRC_RE = re.compile(r'src="([^"]+)"')
_ALT_RE = re.compile(r'alt="([^"]*)"')
_MIN_TEXT = 200


def _is_exempt(name):
    return name.startswith(EXEMPT_PREFIXES) or "-dashboard" in name


def _visible_text(html):
    t = re.sub(r"<script.*?</script>|<style.*?</style>|<[^>]+>", " ", html,
               flags=re.DOTALL)
    return re.sub(r"\s+", " ", t).strip()


def audit_page(html_text, asset_id, repo_root, shared_text=""):
    """单页七项检查 → [finding...](资产标准 schema 行)。"""
    findings = []
    text = html_text
    vis = _visible_text(text)
    m = _PLACEHOLDER_RE.search(vis)
    if m:
        findings.append({"asset_id": asset_id, "asset_type": "page",
                         "status": "missing", "gap_type": "content_missing",
                         "priority": "P0", "evidence": f"占位符「{m.group(0)}」"})
    head = text[:text.find("</head>")] if "</head>" in text else text
    if not re.search(r"<title>[^<]{2,}</title>", head):
        findings.append({"asset_id": asset_id, "asset_type": "page",
                         "status": "missing", "gap_type": "seo_missing",
                         "priority": "P1", "evidence": "title 为空/缺失"})
    if 'name="description"' not in head:
        findings.append({"asset_id": asset_id, "asset_type": "page",
                         "status": "missing", "gap_type": "seo_missing",
                         "priority": "P1", "evidence": "无 meta description"})
    for img_m in _IMG_RE.finditer(text):
        tag = img_m.group(0)
        src = _SRC_RE.search(tag)
        alt = _ALT_RE.search(tag)
        if src and not alt:
            findings.append({"asset_id": asset_id, "asset_type": "page",
                             "status": "missing", "gap_type": "alt_missing",
                             "priority": "P2", "evidence": f"img 无 alt: {src.group(1)}"})
        if src:
            ref = src.group(1).split("?")[0]
            if ref.startswith(("http", "data:", "/api", "${")):
                continue  # 外链/内联/接口/JS 模板占位(${p.image})不做文件存在性判定
            base = ref.lstrip("/") if ref.startswith("/") else ref
            if not os.path.exists(os.path.join(repo_root, base)):
                findings.append({"asset_id": asset_id, "asset_type": "page",
                                 "status": "missing", "gap_type": "image_broken",
                                 "priority": "P2", "evidence": f"断链图: {ref}"})
    dynamic_shell = "js/main.js" in text
    if not dynamic_shell and len(vis) < _MIN_TEXT:
        findings.append({"asset_id": asset_id, "asset_type": "page",
                         "status": "missing", "gap_type": "content_missing",
                         "priority": "P2", "evidence": f"可见正文仅 {len(vis)} 字符(<{_MIN_TEXT})"})
    combined = text + shared_text
    for must, label in ((r"鲁ICP备\d+号", "ICP 备案号"),
                        (r"食品经营许可证", "食品经营许可证"),
                        (r"未成年人禁止", "健康警示语")):
        if not re.search(must, combined):
            findings.append({"asset_id": asset_id, "asset_type": "page",
                             "status": "missing", "gap_type": "content_missing",
                             "priority": "P0", "evidence": f"关键模块缺失: {label}"})
    return findings


def scan_assets(repo_root=None):
    """主站资产盘点 → (assets 台账, summary)。豁免页输出 exempt 单行。"""
    repo_root = repo_root or _REPO_ROOT
    main_js = os.path.join(repo_root, "js", "main.js")
    shared_text = ""
    if os.path.exists(main_js):
        with open(main_js, encoding="utf-8") as fh:
            shared_text = fh.read()
    assets, summary = [], {"healthy": 0, "missing": 0, "exempt": 0}
    for name in sorted(os.listdir(repo_root)):
        if not name.endswith(".html"):
            continue
        with open(os.path.join(repo_root, name), encoding="utf-8") as fh:
            html_text = fh.read()
        if _is_exempt(name):
            assets.append({"asset_id": name, "asset_type": "page",
                          "status": "exempt", "gap_type": None,
                          "priority": None, "evidence": "内部工具页豁免"})
            summary["exempt"] += 1
            continue
        findings = audit_page(html_text, name, repo_root, shared_text)
        if findings:
            assets.extend(findings)
            summary["missing"] += 1
        else:
            assets.append({"asset_id": name, "asset_type": "page",
                           "status": "healthy", "gap_type": None,
                           "priority": None, "evidence": None})
            summary["healthy"] += 1
    return assets, summary


def run(args=None):
    """zhuyuan audit CLI: 资产台账输出 + T0.4 度量上报。"""
    assets, summary = scan_assets()
    print(f"[zhuyuan audit] 资产台账: healthy={summary['healthy']} "
          f"missing={summary['missing']} exempt={summary['exempt']}")
    for a in assets:
        if a["status"] == "exempt":
            print(f"  [exempt] {a['asset_id']}")
            continue
        if a["status"] == "healthy":
            print(f"  [healthy] {a['asset_id']}")
        else:
            print(f"  [{a['priority']}:{a['gap_type']}] {a['asset_id']} —— {a['evidence']}")
    try:
        from zhuyuan import stats as stats_mod
    except ImportError:
        import stats as stats_mod
    pri = {"p0": 0, "p1": 0, "p2": 0}
    for a in assets:
        p = (a.get("priority") or "").lower()
        if p in pri:
            pri[p] += 1
    ok = stats_mod.report_stats(metrics={**summary, **pri})
    print(f"[zhuyuan audit] 度量上报 zhuyuan:stats {'成功' if ok else '降级(仅打印)'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
