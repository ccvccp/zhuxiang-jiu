"""activity.html v72 落地页变体注入(72号 P4 前端渲染)

三步法第一步: python 锚点式替换(带备份 .bak-v72)
注入: CSS v72-strip 样式 + JS v72 变体渲染(trust_first/
benefit_first 差异化横幅; default/无参数零影响)
自包含单文件 + html no-cache——改完即生效, 无需 chunk 改名
"""
import shutil
import sys

PATH = "/var/www/zxjiu/dist/activity.html"

CSS_ANCHOR = "    /* 空态/加载态 */"
CSS_BLOCK = """    /* v72 落地页变体条(72号 P4: trust_first 新客强化信任 /
       benefit_first 老客直显权益; 2026-09-26 注入) */
    .v72-strip { margin-top: 12px; border-radius: 8px;
        padding: 10px 12px; border: 1px dashed rgba(201,169,97,.55);
        background: rgba(201,169,97,.08); }
    .v72-strip.trust { border-color: rgba(74,124,89,.5);
        background: rgba(74,124,89,.07); }
    .v72-head { font-size: 13.5px; color: #8a6d2f;
        display: flex; align-items: center; gap: 6px; }
    .v72-strip.trust .v72-head { color: #355c44; }
    .v72-sub { font-size: 11.5px; color: #6b6b6b; margin-top: 3px; }

"""

JS_ANCHOR = "    /* ---------- 启动 ---------- */"
JS_BLOCK = """    /* ---------- v72 落地页变体渲染(72号 P4 决策留痕
       ——302 URL 携 v72 参数; default/无参数零影响) ---------- */
    (function () {
        var m = location.search.match(/[?&]v72=([a-z_]+)/);
        var variant = m ? m[1] : '';
        if (!variant || variant === 'default') { return; }
        var CONF = {
            trust_first: {
                icon: '🛡', label: '品牌直供 · 官方正品',
                sub: '一物一码防伪查验 · 县区网点联保 · 放心选购',
            },
            benefit_first: {
                icon: '🎁', label: '会员专享 · 下单有礼',
                sub: '会员价直降 · 积分抵现 · 活动抽奖赢好酒',
            },
        };
        var c = CONF[variant];
        if (!c) { return; }
        var hero = document.querySelector('.hero');
        if (!hero) { return; }
        var strip = document.createElement('div');
        strip.className = 'v72-strip '
            + (variant === 'trust_first' ? 'trust' : 'benefit');
        strip.innerHTML = '<div class="v72-head"><span>' + c.icon
            + '</span><b>' + c.label + '</b></div>'
            + '<div class="v72-sub">' + c.sub + '</div>';
        hero.appendChild(strip);
    })();

"""

with open(PATH, encoding="utf-8") as f:
    html = f.read()

if "v72-strip" in html:
    print("ALREADY INJECTED — skip")
    sys.exit(0)

for anchor, block, name in (
        (CSS_ANCHOR, CSS_BLOCK, "CSS"),
        (JS_ANCHOR, JS_BLOCK, "JS")):
    if anchor not in html:
        print(f"ANCHOR MISS [{name}]: {anchor!r}")
        sys.exit(1)
    if html.count(anchor) != 1:
        print(f"ANCHOR NOT UNIQUE [{name}]: "
              f"x{html.count(anchor)}")
        sys.exit(1)
    html = html.replace(anchor, block + anchor)

shutil.copyfile(PATH, PATH + ".bak-v72")
with open(PATH, "w", encoding="utf-8") as f:
    f.write(html)
print("INJECTED OK:",
      "v72-strip" in html,
      "| backup:", PATH + ".bak-v72")
