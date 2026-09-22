# -*- coding: utf-8 -*-
"""生产 nginx: 根级静态 HTML 加 no-cache(幂等)

背景: activity-detail.html 等根级入口页由 location / try_files 直
serve, 无 Cache-Control 头 -> 浏览器启发式缓存(Last-Modified 10%),
手机用户拿不到部署更新(详情页返回按钮上线后用户仍见旧版)。

方案: ① /js/ /css/ /api/ /r/ 前缀 location 升级 ^~(阻止正则抢占,
语义不变); ② 新增正则 location ~ \.html$ 对全部静态 HTML 输出
Cache-Control: no-cache(每次回源验证, ETag 304 秒回, 部署即生效);
③ = /index.html 精确匹配原有 no-store 优先级更高, 不受影响。
"""
import io

CONF = "/etc/nginx/conf.d/zxjiu.conf"

REPL = [
    # 前缀 location 升级 ^~(防正则 \.html$ 抢占; 匹配语义不变)
    ("    location /js/  {", "    location ^~ /js/  {"),
    ("    location /css/ {", "    location ^~ /css/ {"),
    ("    location /api/xiaozhu/ws/ {", "    location ^~ /api/xiaozhu/ws/ {"),
    ("location /api/ {", "location ^~ /api/ {"),
    ("    location /r/ {", "    location ^~ /r/ {"),
]

HTML_BLOCK = """    # 根级静态 HTML(活动中心/详情页/语音页/zyh 等入口): no-cache 每次回源
    # 验证(ETag 304 秒回, 部署即生效)——曾无 Cache-Control 走启发式缓存,
    # 手机用户拿不到更新(详情页返回按钮上线后仍见旧版即此因)
    location ~ \\.html$ {
        add_header Cache-Control "no-cache";
        try_files $uri =404;
    }

    # SPA 前端路由回退(Taro H5 前端路由, 刷新非根路径不 404)
    location / {"""

SPA_ANCHOR = """    # SPA 前端路由回退(Taro H5 前端路由, 刷新非根路径不 404)
    location / {"""


def main():
    s = io.open(CONF, encoding="utf-8").read()
    if 'location ~ \\.html$' in s:
        print("already-patched")
        return
    for old, new in REPL:
        if new in s:
            continue
        assert old in s, "anchor missing: %r" % old[:50]
        s = s.replace(old, new, 1)
    assert SPA_ANCHOR in s, "spa anchor missing"
    s = s.replace(SPA_ANCHOR, HTML_BLOCK, 1)
    io.open(CONF, "w", encoding="utf-8").write(s)
    print("patched: %d prefix ^~ + html no-cache block" % len(REPL))


if __name__ == "__main__":
    main()
