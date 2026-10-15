# -*- coding: utf-8 -*-
"""sdc 三扩展榜结构探针: global/ai 短剧榜 + 演员榜(演员维度形态确认)"""
import io
import re
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, ".")

from drama85_media_crawler import fetch_html, sdc_parse

BOARDS = [
    ("global", "https://shortdramacast.com/zh-cn/rankings/global-short-dramas"),
    ("ai", "https://shortdramacast.com/zh-cn/rankings/ai-short-dramas"),
    ("actors", "https://shortdramacast.com/zh-cn/rankings/hot-short-drama-actors"),
]

with __import__("httpx").Client(timeout=20.0, follow_redirects=True, headers={
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 Chrome/120.0 Safari/537.36"}) as c:
    for name, url in BOARDS:
        t = c.get(url).text
        title = re.search(r"<title[^>]*>(.*?)</title>", t, re.S)
        upd = re.search(r'ranking-updated">\s*([^<]+)<', t)
        print(f"== {name}: {title.group(1).strip()[:50] if title else '-'}")
        print("   updated:", upd.group(1).strip() if upd else "-",
              "len:", len(t), "articles:", t.count("<article"))
        if name != "actors":
            items = sdc_parse(t)
            print("   parse n=", len(items))
            for it in items[:3]:
                print("   ", it["rank"], it["name"], it["heat"])
        else:
            # 演员榜形态: article 属性样本
            m = re.search(r"<article\b[^>]*>", t)
            print("   article attrs:", m.group(0)[:400] if m else "-")
            m2 = re.search(r'aria-label="([^"]{2,50})"', t)
            print("   aria sample:", m2.group(1) if m2 else "-")
