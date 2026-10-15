# -*- coding: utf-8 -*-
"""二轮: douyin robots * 段 + 两榜单页数据容器解剖"""
import io
import re
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
import httpx

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36")

with httpx.Client(timeout=15.0, follow_redirects=True,
                  headers={"User-Agent": UA}) as c:
    # 1) douyin robots 全文 * 段
    rr = c.get("https://www.douyin.com/robots.txt")
    t = rr.text
    star = re.search(r"User-agent:\s*\*(.*?)(?=User-agent:|\Z)", t, re.S)
    print("== douyin robots * section ==")
    print(star.group(1).strip()[:500] if star else "(no * section)")

    # 2) duanjubaike 容器解剖
    t1 = c.get("https://www.duanjubaike.net/paihang/rebo.html").text
    print("\n== duanjubaike rebo.html ==")
    print("len:", len(t1))
    for m in list(re.finditer("热度", t1))[:3]:
        print("ctx:", repr(t1[max(0, m.start() - 200):m.start() + 100]))
    for pat in (r'<li[^>]*class="[^"]*rank', r'<div[^>]*class="[^"]*item',
                r'<a[^>]*href="/(?:detail|v)/[^"]*"'):
        n = len(re.findall(pat, t1))
        print(f"pat={pat!r} n={n}")

    # 3) rebang.vip 容器解剖
    t2 = c.get("https://www.rebang.vip/baidu/drama").text
    print("\n== rebang.vip /baidu/drama ==")
    print("len:", len(t2))
    for m in list(re.finditer(r'class="rank', t2))[:2]:
        print("ctx:", repr(t2[m.start():m.start() + 350]))
    m = re.search(r"热度[^0-9]{0,10}[\d.]+", t2)
    if m:
        print("heat ctx:", repr(t2[max(0, m.start() - 250):m.start() + 60]))
