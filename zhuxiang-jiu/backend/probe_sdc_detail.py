# -*- coding: utf-8 -*-
"""验证 sdc AI 分类定义: 聚宝仙盆第十三季详情页的分类标签"""
import io
import re
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
import httpx

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36")

with httpx.Client(timeout=15.0, follow_redirects=True,
                  headers={"User-Agent": UA}) as c:
    t = c.get("https://shortdramacast.com/zh-cn/rankings/ai-short-dramas").text
    m = re.search(r'href="(/zh-cn/drama/ju-bao-xian-pen[^"]*)"', t)
    if not m:
        m = re.search(r'<a[^>]*href="(/zh-cn/drama/[^"]+)"', t)
    print("detail link:", m.group(1) if m else "-")
    if m:
        d = c.get("https://shortdramacast.com" + m.group(1)).text
        title = re.search(r"<title>(.*?)</title>", d, re.S)
        print("detail title:", title.group(1).strip()[:80] if title else "-")
        for kw in ("AI", "人工智能", "动画", "漫剧", "分类", "标签", "genre"):
            n = d.count(kw)
            if n:
                mm = re.search(kw, d)
                ctx = re.sub(r"\s+", " ",
                             d[max(0, mm.start() - 80):mm.start() + 120])
                print(f"kw={kw!r} n={n} ctx={ctx[:180]!r}")
