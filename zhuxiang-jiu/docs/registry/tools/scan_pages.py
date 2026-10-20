# -*- coding: utf-8 -*-
"""竹盾 T0.4 页面清单生成器（临时工具——P1 起由 zhudun index 替代并移除）

扫描前端资产面, 产出 docs/registry/L0-页面清单.md:
  1) 仓库根页面 html(59)——title 提取+分类(核心九页/模型看板/功能页)
  2) js/ 模块族(62)——分类(看板配套/测试/挂件/工具箱/业务模块) + css/images 普查
  3) taro-app/public 手工资产(部署铁律载体)
  4) 三向对照: inject_beian PAGES ↔ 根 html ↔ ECS dist 实际(2026-10-20 云助手取证快照)
  5) nginx 路由资产: 设计规则 vs ECS 实时状态(漂移警示)
用法: python docs/registry/tools/scan_pages.py
"""
import io
import os
import re
from collections import OrderedDict, defaultdict

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
SITE = REPO  # zhuxiang-jiu 根即站点 dist 源
TARO_PUBLIC = os.path.join(os.path.dirname(REPO), "taro-app", "public")
OUT = os.path.join(REPO, "docs", "registry", "L0-页面清单.md")

# ECS dist 实际清单取证快照(2026-10-20 云助手 InvokeId t-wl070lv9cr1xts0, 只读 Get-ChildItem)
# 刷新方式: 经 deploy/aliyun-ecs-rpc.ps1 Send 发送 dist 列表 payload 后更新本常量
DIST_HTML = [
    "activity-dashboard.html", "activity-detail.html", "activity.html", "ai-alliance-dashboard.html",
    "ai-api-dashboard.html", "ai-asset-dashboard.html", "ai-blogger-dashboard.html", "ai-entry-dashboard.html",
    "ai-governance-dashboard.html", "ai-hub-dashboard.html", "ai-invoice-dashboard.html",
    "ai-learning-dashboard.html", "ai-learning-detail.html", "ai-learning-report.html",
    "ai-payport-dashboard.html", "ai-pdm-dashboard.html", "ai-promo-radar.html", "ai-promo-studio.html",
    "ai-ride-dashboard.html", "ai-security-dashboard.html", "alliance-mall.html", "app.html",
    "arch-dashboard.html", "attract72-dashboard.html", "beian-placeholder.html", "cart.html", "cert.html",
    "challenge.html", "checkout.html", "drama.html", "h2-watch.html", "index.html",
    "knowledge-dashboard.html", "knowledge-sources.html", "legal-dashboard.html", "login.html",
    "logistics.html", "margin-dashboard.html", "margin.html", "module-test.html", "my-card.html",
    "news-detail.html", "news.html", "official.html", "product-detail.html", "products.html",
    "qr70-dashboard.html", "scene.html", "search.html", "service.html", "store-map.html",
    "synapse-dashboard.html", "synapse.html", "test-app-local.html", "trust-dashboard.html",
    "trust-risk-dashboard.html", "video.html", "visual.html", "wine-advisor.html", "xiaozhu-dashboard.html",
    "xiaozhu-fc-redteam.html", "xiaozhu-voice.html", "xx64-delivery-dashboard.html", "zhidan-dashboard.html",
    "zhike-dashboard.html", "zhisou-dashboard.html", "zhiyun-dashboard.html", "zjian-dashboard.html",
    "zjian.html", "zy-dashboard.html", "zyh-dashboard.html", "zyh.html",
]
DIST_JS_COUNT = 142
DIST_DIRS = [".well-known", "css", "docs", "images", "js", "videos"]


def read_pages_list():
    """从 inject_beian.py 动态解析 PAGES(备案注入面)"""
    src = io.open(os.path.join(REPO, "deploy", "inject_beian.py"), encoding="utf-8-sig", errors="replace").read()
    m = re.search(r'PAGES\s*=\s*\[(.*?)\]', src, re.S)
    return re.findall(r'"([^"]+)"', m.group(1)) if m else []


def html_title(path):
    """取 <title>"""
    try:
        src = io.open(path, encoding="utf-8-sig", errors="replace").read()
    except OSError:
        return ""
    m = re.search(r'<title[^>]*>(.*?)</title>', src, re.S | re.I)
    return re.sub(r'\s+', ' ', m.group(1)).strip()[:40] if m else ""


def html_category(name, pages):
    if name in pages:
        return "核心页·备案注入"
    if name.endswith("-dashboard.html"):
        return "模型看板"
    return "功能页"


def js_category(fname):
    if fname.endswith("-dashboard.js"):
        return "看板配套"
    if "-test" in fname or fname.startswith("test-"):
        return "测试"
    if "widget" in fname:
        return "挂件"
    if fname == "main.js" or fname in ("data.js", "modules.js", "auth.js"):
        return "核心"
    return "业务模块"


def main():
    pages = read_pages_list()
    root_html = sorted(f for f in os.listdir(SITE) if f.endswith(".html") and os.path.isfile(os.path.join(SITE, f)))
    js_files = sorted(f for f in os.listdir(os.path.join(SITE, "js")) if f.endswith(".js"))
    js_toolkit = sorted(os.listdir(os.path.join(SITE, "js", "toolkit")))
    css_files = sorted(f for f in os.listdir(os.path.join(SITE, "css")) if f.endswith(".css")) \
        if os.path.isdir(os.path.join(SITE, "css")) else []
    img_stat = defaultdict(int)
    img_root = os.path.join(SITE, "images")
    if os.path.isdir(img_root):
        for root, dirs, files in os.walk(img_root):
            rel = os.path.relpath(root, img_root)
            img_stat["<根>" if rel == "." else rel] += sum(1 for f in files)

    # taro-app/public 手工资产
    taro_stat = defaultdict(int)
    taro_html = []
    taro_total = 0
    if os.path.isdir(TARO_PUBLIC):
        for root, dirs, files in os.walk(TARO_PUBLIC):
            for f in files:
                ext = os.path.splitext(f)[1].lstrip(".").lower()
                k = {"html": "html", "js": "js", "css": "css", "md": "docs",
                     "png": "images", "jpg": "images", "jpeg": "images", "webp": "images",
                     "svg": "images", "mp4": "videos", "webm": "videos"}.get(ext, "其他:" + ext)
                taro_stat[k] += 1
                taro_total += 1
                if ext == "html":
                    taro_html.append(f)
    # deploy/ 下经脚本直部署的页面源(排除 .work.html 工作快照)
    deploy_dir = os.path.join(SITE, "deploy")
    deploy_html = sorted(f for f in os.listdir(deploy_dir)
                         if f.endswith(".html") and not f.endswith(".work.html")
                         and os.path.isfile(os.path.join(deploy_dir, f))) \
        if os.path.isdir(deploy_dir) else []

    # 三向对照(本地三源: 仓库根 + taro-app/public + deploy/)
    root_set, dist_set, pages_set = set(root_html), set(DIST_HTML), set(pages)
    taro_set, deploy_set = set(taro_html), set(deploy_html)
    local_all = root_set | taro_set | deploy_set

    def src_of(p):
        if p in root_set:
            return "仓库根"
        if p in taro_set:
            return "taro-app/public"
        if p in deploy_set:
            return "deploy/"
        return "⚠ 仅 dist 无本地源"

    pages_missing_local = [p for p in pages if p not in local_all]
    pages_missing_dist = [p for p in pages if p not in dist_set]
    dist_only = sorted(dist_set - local_all)
    root_only = sorted(root_set - dist_set)
    dash_cnt = sum(1 for f in root_html if f.endswith("-dashboard.html"))

    buf = []
    buf.append("# L0-页面清单（竹盾资产图谱 · 前端资产层）")
    buf.append("")
    buf.append("> 生成: 2026-10-20 · 任务 T0.4 · 扫描面: 仓库根 html/js/css/images + taro-app/public 手工资产 + nginx 路由")
    buf.append("> dist 实际对照: 2026-10-20 ECS 云助手取证快照（InvokeId t-wl070lv9cr1xts0, C:\\www\\dist 实测）——刷新经 [deploy/aliyun-ecs-rpc.ps1](../../deploy/aliyun-ecs-rpc.ps1) Send 列表 payload")
    buf.append("> 维护: 新增/修改页面后重跑扫描器+人工校对（P1 起由 `zhudun index` 自动重建）· 上游: [78号_竹盾增量管理Agent_创新规划方案.md](../78号_竹盾增量管理Agent_创新规划方案.md)")
    buf.append("")
    buf.append("## 总览")
    buf.append("")
    buf.append("| 指标 | 数值 |")
    buf.append("| --- | --- |")
    buf.append("| 仓库根页面 html | %d（核心九页 / 模型看板 %d / 功能页 %d） |" % (
        len(root_html), dash_cnt, len(root_html) - dash_cnt - len([p for p in pages if p in root_set])))
    buf.append("| js/ 模块 | %d + toolkit/ %d |" % (len(js_files), len(js_toolkit)))
    buf.append("| css/ | %d | images/ | %d 文件 |" % (len(css_files), sum(img_stat.values())))
    buf.append("| taro-app/public 手工资产 | %d 文件（html %d / js %d / css %d / images %d / videos %d / docs %d） |" % (
        taro_total, taro_stat["html"], taro_stat["js"], taro_stat["css"],
        taro_stat["images"], taro_stat["videos"], taro_stat["docs"]))
    buf.append("| ECS dist 实际（取证快照） | html %d · js %d · 子目录 %s |" % (len(DIST_HTML), DIST_JS_COUNT, "/".join(DIST_DIRS)))
    buf.append("| 备案注入面 PAGES | %d（inject_beian.py） |" % len(pages))
    buf.append("")
    buf.append("## 根页面明细（zhuxiang-jiu/*.html）")
    buf.append("")
    buf.append("| 文件 | 分类 | title |")
    buf.append("| --- | --- | --- |")
    for f in root_html:
        buf.append("| %s | %s | %s |" % (f, html_category(f, pages), html_title(os.path.join(SITE, f))))
    buf.append("")
    buf.append("## js/ 模块族")
    buf.append("")
    buf.append("| 文件 | 分类 |")
    buf.append("| --- | --- |")
    for f in js_files:
        buf.append("| js/%s | %s |" % (f, js_category(f)))
    buf.append("")
    if js_toolkit:
        buf.append("### js/toolkit/（现成工程工具箱）")
        buf.append("")
        buf.append("| 文件 |")
        buf.append("| --- |")
        for f in js_toolkit:
            buf.append("| %s |" % f)
        buf.append("")
    if img_stat:
        buf.append("### images/ 分布")
        buf.append("")
        buf.append("| 子目录 | 文件数 |")
        buf.append("| --- | --- |")
        for k in sorted(img_stat):
            buf.append("| %s | %d |" % (k, img_stat[k]))
        buf.append("")
    if taro_stat:
        buf.append("### taro-app/public 手工资产（部署铁律载体: 构建出包即完整站点）")
        buf.append("")
        buf.append("| 类型 | 数量 |")
        buf.append("| --- | --- |")
        for k in sorted(taro_stat):
            buf.append("| %s | %d |" % (k, taro_stat[k]))
        buf.append("")

    # 三向对照
    buf.append("## 三向对照（验收: PAGES ↔ 本地三源 ↔ dist 实际）")
    buf.append("")
    buf.append("> 本地页面源有三处（部署铁律）: **仓库根** zhuxiang-jiu/*.html + **taro-app/public**（手工资产, 构建随包出 dist）+ **deploy/**（脚本直部署, 如 visual.html）——勿假设仓库根即全量。")
    buf.append("")
    buf.append("| 检查 | 结果 |")
    buf.append("| --- | --- |")
    buf.append("| PAGES 九页本地源齐备（三源合并） | %s |" % ("✅ 全在" if not pages_missing_local else "⚠ 缺: " + ", ".join(pages_missing_local)))
    buf.append("| PAGES 九页全在 ECS dist | %s |" % ("✅ 全在" if not pages_missing_dist else "⚠ 缺: " + ", ".join(pages_missing_dist)))
    buf.append("| dist 有而本地三源皆无（真孤儿页, 源码仅存服务器） | %d 页: %s |" % (len(dist_only), ", ".join(dist_only) if dist_only else "无"))
    buf.append("| 仓库根有而 dist 无（未部署/已下线） | %d 页: %s |" % (len(root_only), ", ".join(root_only) if root_only else "无"))
    buf.append("")
    buf.append("**核心九页来源对照（备案注入面 PAGES）:**")
    buf.append("")
    buf.append("| 页面 | 本地源位置 | dist |")
    buf.append("| --- | --- | --- |")
    for p in pages:
        buf.append("| %s | %s | %s |" % (p, src_of(p), "✅" if p in dist_set else "⚠ 无"))
    buf.append("")
    if dist_only:
        buf.append("> ⚠ dist-only 页面源码仅存服务器（C:\\www\\dist）——版本管理盲区, 建议拉回本地入库, 防服务器侧丢失后无法重建。")
        buf.append("")

    # nginx 路由资产
    buf.append("## nginx 路由资产（设计 vs ECS 实时, 2026-10-20 取证）")
    buf.append("")
    buf.append("**设计规则**（[deploy/zxjiu.conf.work](../../deploy/zxjiu.conf.work), 2026-10-02 官网首页路由）:")
    buf.append("")
    buf.append("- `map $http_user_agent $zxjiu_is_mobile`（移动 UA 判定）→ `map \"$zxjiu_is_mobile$args\" $zxjiu_home`：PC 无参裸根→`/official.html`（官网首页）; 移动端/带参（72号引流落地 `/?clickId...`）→`/index.html`（SPA）")
    buf.append("- WS 通道: `/api/entry/qr/`（39号扫码推送, 200s）, `/api/xiaozhu/ws/`（48号流式语音, 120s）, `/api/chat/ws`（聊天 15 事件, 200s）")
    buf.append("- `/r/` 短链: PetalBot|Scrapy|SERanking UA 封禁 403（72号爬虫污染防线）")
    buf.append("- html 全量 no-cache; js/css 哈希名 immutable 30d; index.html 每次回源")
    buf.append("")
    buf.append("**ECS 实时状态**（取证快照, C:\\nginx\\conf）:")
    buf.append("")
    buf.append("| 项 | 状态 |")
    buf.append("| --- | --- |")
    buf.append("| UA 分流 map（PC 裸根→official.html） | ✅ **2026-10-20 竹盾已补齐**（双层 map + `location = /` try_files; 服务器本机+公网双端三路验证: PC→official / 移动→SPA / 带参→SPA 内容全等; 备份 nginx.conf.bak-zhudun-20261020-200047, 取证见 [reports/影响报告](reports/)） |")
    buf.append("| /api/ 反代 | ✅ `127.0.0.1:8000`（backend8147 nssm 本地后端, 全切完成） |")
    buf.append("| /api/visual/ | `127.0.0.1:8200`（图形分析 Agent visual 服务, 16m body） |")
    buf.append("| WS 通道 | ✅ entry/qr 200s + xiaozhu/ws 120s（site-proxy.conf, 已指本地 8000） |")
    buf.append("| html no-cache | ✅ `location ~ \\.html$`（2026-10-19 补, 防 http 缓存残缺 CSS 类事故） |")
    buf.append("| common.js 热修 | `common.0ad2fc19.js`/`common.de40aaca.js` 定向 no-cache（2026-10-19 结算 mock 热修） |")
    buf.append("| sitemap/robots | ✅ 指本地 8000 |")
    buf.append("")
    buf.append("⚠ **repo 快照纪律**: ecs8147-nginx.conf / ecs8147-site-proxy.conf 已于 2026-10-20 刷新为生产语义等价重建（nginx.conf 含生产指纹头: len=3465/sha256=2E73...7BD）——但快照永远可能滞后, **改 nginx 前仍必须先经 [deploy/aliyun-ecs-rpc.ps1](../../deploy/aliyun-ecs-rpc.ps1) 取服务器实时 conf**（竹盾红线: 部署面变更以生产侧取证为准）。")
    buf.append("")

    io.open(OUT, "w", encoding="utf-8").write("\n".join(buf))
    print("OK root_html=%d js=%d toolkit=%d css=%d images=%d taro=%d dist_html=%d pages=%d dist_only=%d root_only=%d" % (
        len(root_html), len(js_files), len(js_toolkit), len(css_files), sum(img_stat.values()),
        taro_total, len(DIST_HTML), len(pages), len(dist_only), len(root_only)))


if __name__ == "__main__":
    main()
