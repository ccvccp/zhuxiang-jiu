# -*- coding: utf-8 -*-
"""从 AI 榜 article 抽详情链接 + 验证分类定义"""
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
    links = re.findall(r'href="(/zh-cn/[^"]*(?:drama|ju-bao)[^"]*)"', t)
    print("links:", links[:6])
    # 榜首块内的任意 href
    blk = re.split(r"<article\b", t)[1] if "<article" in t else ""
    h = re.findall(r'href="([^"]+)"', blk)[:6]
    print("first-article hrefs:", h)
    target = next((l for l in links), None) or (h[0] if h else None)
    if target:
        d = c.get("https://shortdramacast.com" + target).text
        title = re.search(r"<title>(.*?)</title>", d, re.S)
        print("detail:", target, "|", title.group(1).strip()[:70] if title else "-")
        for kw in ("AI", "人工智能", "动画", "漫剧", "动态漫画", "genre"):
            n = d.count(kw)
            if n:
                mm = re.search(kw, d)
                ctx = re.sub(r"\s+", " ",
                             d[max(0, mm.start() - 70):mm.start() + 110])
                print(f"kw={kw!r} n={n} ctx={ctx[:170]!r}")
