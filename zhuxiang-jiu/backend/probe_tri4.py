# -*- coding: utf-8 -*-
"""补查: sdc 实际条目(子串定位) + djgc 数据形态(__NEXT_DATA__?)"""
import io
import re
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
import httpx

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36")

with httpx.Client(timeout=20.0, follow_redirects=True,
                  headers={"User-Agent": UA}) as c:
    t2 = c.get("https://shortdramacast.com/zh-cn/rankings/chinese-short-dramas").text
    print("== sdc: ranking-updated / 条目定位 ==")
    for kw in ("ranking-updated", "ranking-tabs", "ranking-item", "drama"):
        i = t2.find(kw)
        print(f"kw={kw!r} at={i}")
    # 第一个剧名条目上下文
    m = re.search(r'class="[^"]*(?:row|entry|drama)[^"]*"[^>]*>.{0,400}', t2, re.S)
    if m:
        print("row ctx:", repr(m.group(0)[:500]))
    # title 链接
    ms = re.findall(r'<a[^>]*href="([^"]*)"[^>]*>\s*([^<]{2,40})\s*</a>', t2)
    print("a-with-text n=", len(ms), ms[:8])

    t3 = c.get("https://www.duanjugongcheng.com/cn/fengxiangbiao").text
    print("\n== djgc: SSR 数据形态 ==")
    for kw in ("__NEXT_DATA__", "window.__", "application/json", "self.__next_f",
               "loaderData", "_SSR"):
        print(f"kw={kw!r} n={t3.count(kw)}")
    # 找 script json 大块
    scripts = re.findall(r'<script[^>]*type="application/json"[^>]*>(.*?)</script>',
                         t3, re.S)
    print("json scripts:", len(scripts),
          [len(s) for s in scripts[:6]])
    # 热度上下文(非 0 值)
    for m in re.finditer(r"热度[^0-9]{0,8}([1-9][\d.,]*)", t3):
        print("heat ctx:", repr(t3[max(0, m.start() - 100):m.end() + 30]))
        break
    # 剧名《》上下文
    for m in list(re.finditer(r"《([^》]{2,25})》", t3))[:2]:
        print("name ctx:", repr(t3[max(0, m.start() - 120):m.start() + 80]))
