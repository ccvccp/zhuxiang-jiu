"""官网 PC 站过渡部署准备(37号同盟臻选区块上线首发)

背景:
    生产 zxjiu.com 根路径被 Taro H5 SPA(dist/index.html)占用, 官网
    PC 首页以 official.html 过渡命名部署(nginx location ~ \\.html$
    no-cache 直 serve, 零 nginx 改动); zyjiu.com 备案下发独立官网
    上线时回归 index.html 命名(届时改 main.js 导航+logo 与 9 页
    面包屑内链两处即可, 本脚本同步退役)。

动作(幂等, 全量从正源重生成):
    1) 产出 staging 镜像 deploy/staging/official-site/(scp 到生产
       /var/www/zxjiu/dist/ 的完整文件集)
    2) 同步 taro-app/public/(构建链持久化——L31 裸 dist 事故铁律:
       构建出包即完整站点; index.html 例外不进 public, 防 Taro
       copy patterns 覆盖 SPA 模板)
    3) 加工规则(相对 zhuxiang-jiu 正源):
       - index.html → official.html, 9 页内链 index.html 同步替换
       - style.css / main.js 引用加 ?v=20261002(绕 nginx
         ^~ /css/ /js/ 30d immutable 老缓存——带 v 的新 URL 无
         旧缓存键必回源; data.js/auth.js/checkout 业务 js 族
         未变更不加, dist 既有版本与正源一致已批量 diff 实证)

前置断言:
    - taro-app/public/ 不得存在 index.html(防覆盖 Taro SPA 入口)
"""

import re
import shutil
import sys
from pathlib import Path

SITE = Path(__file__).resolve().parent.parent          # zhuxiang-jiu 正源
PUBLIC = SITE.parent / "taro-app" / "public"            # 构建链镜像
STAGING = SITE / "deploy" / "staging" / "official-site"  # scp 文件集

V = "20261003"  # 缓存版本号(首页创新布局批次: 公告条/金刚区/会员卡/榜单/信任条)

# 部署页面(核心购物/资讯闭环 + 同盟商城入口)
PAGES = [
    "index.html",              # → official.html(SPA 占用根路径)
    "products.html",
    "product-detail.html",
    "cart.html",
    "checkout.html",
    "service.html",
    "news.html",
    "news-detail.html",
    "login.html",              # dist 已上线, 仅内链替换(最小改动)
    "ai-alliance-dashboard.html",  # 37号同盟管理看板(B 端后台)
    "alliance-mall.html",      # 37号同盟商城 C 端购物页(2026-10-02 新增)
]

# 资产(三件套 + 同盟页专属 js)
ASSETS = [
    "css/style.css",
    "js/main.js",
    "js/alliance-dashboard.js",
    "js/chat-widget.js",
]


def load(rel: str) -> str:
    path = SITE / rel
    if not path.is_file():
        sys.exit(f"[FATAL] 正源缺失: {path}")
    return path.read_text(encoding="utf-8")


def cache_v(text: str, ref: str) -> tuple[str, bool]:
    """给静态引用加 ?v=(幂等: 先剥旧 v 再注入)"""
    pattern = re.compile(re.escape(ref) + r"(\?v=\d+)?")
    hit = pattern.search(text) is not None
    return pattern.sub(lambda m: f"{ref}?v={V}", text), hit


def replace_index_link(text: str) -> tuple[str, int]:
    """内链 index.html → official.html(面包屑/返回/支付完成跳转)"""
    return re.subn(r"index\.html", "official.html", text)


def main():
    # ---- 前置断言: public 不得有 index.html(Taro SPA 保护) ----
    if (PUBLIC / "index.html").exists():
        sys.exit("[FATAL] taro-app/public/index.html 存在——会覆盖 Taro SPA!")

    if STAGING.exists():
        shutil.rmtree(STAGING)
    (STAGING / "css").mkdir(parents=True)
    (STAGING / "js").mkdir(parents=True)

    print(f"官网过渡部署准备(official.html 批次 v={V})")
    print("=" * 60)

    # ---- 页面加工 ----
    for page in PAGES:
        text = load(page)
        target = "official.html" if page == "index.html" else page

        # 1) 内链替换(login 已上线页亦同规则——返回官网首页指向新入口)
        text, n = replace_index_link(text)

        # 2) 缓存版本注入(style.css 必加; main.js 引用页加)
        notes = [f"内链x{n}"] if n else []
        text, hit = cache_v(text, "css/style.css")
        if hit:
            notes.append("css?v")
        if 'src="js/main.js"' in text:
            text = text.replace('src="js/main.js"', f'src="js/main.js?v={V}"')
            notes.append("main.js?v")

        for dest in (STAGING / target, PUBLIC / target):
            dest.write_text(text, encoding="utf-8", newline="\n")
        print(f"  [页] {page} -> {target}  ({', '.join(notes) or '原样'})")

    # ---- 资产同步 ----
    for rel in ASSETS:
        text = load(rel)
        if rel == "js/main.js":
            # 导航「首页」+ logo 指向 official.html
            text, n = replace_index_link(text)
            print(f"  [产] main.js 内链替换 x{n}")
        for dest in (STAGING / rel, PUBLIC / rel):
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(text, encoding="utf-8", newline="\n")
        print(f"  [产] {rel}  -> staging + public")

    print("=" * 60)
    print(f"完成: staging={STAGING}")
    print(f"      public 镜像同步 {len(PAGES)} 页 + {len(ASSETS)} 资产")
    print("部署: scp -r deploy/staging/official-site/* root@47.236.61.117:/var/www/zxjiu/dist/")


if __name__ == "__main__":
    main()
