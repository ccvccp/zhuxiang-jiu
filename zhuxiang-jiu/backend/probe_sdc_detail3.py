# -*- coding: utf-8 -*-
"""聚宝仙盆第十三季 sdc 详情页: 分类标签验证(AI 分类=漫剧?)"""
import io
import re
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
import httpx

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36")

url = ("https://shortdramacast.com/zh-cn/dramas/"
       "ju-bao-xian-pen-zhi-za-ling-gen-cai-shi-zhen-b-o-s-s-season-13")
with httpx.Client(timeout=15.0, follow_redirects=True,
                  headers={"User-Agent": UA}) as c:
    d = c.get(url).text
title = re.search(r"<title>(.*?)</title>", d, re.S)
print("title:", title.group(1).strip()[:90] if title else "-")
print("len:", len(d))
for kw in ("AI", "人工智能", "动画", "漫剧", "动态漫画", "animated", "anime",
           "genre", "题材", "类型", "标签"):
    n = d.count(kw)
    if n:
        mm = list(re.finditer(kw, d))[:2]
        for m in mm:
            ctx = re.sub(r"\s+", " ", d[max(0, m.start() - 70):m.start() + 110])
            print(f"kw={kw!r} ctx={ctx[:170]!r}")
        break_ = None
