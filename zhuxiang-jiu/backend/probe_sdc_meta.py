# -*- coding: utf-8 -*-
"""sdc 元数据探测: AI 榜 article→slug 映射 + 详情页 JSON-LD 字段结构"""
import io
import json
import re
import sys
import time

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
import httpx

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36")
RANK_URL = "https://shortdramacast.com/zh-cn/rankings/ai-short-dramas"

with httpx.Client(timeout=20.0, follow_redirects=True,
                  headers={"User-Agent": UA}) as c:
    t = c.get(RANK_URL).text
    print(f"榜单页 {len(t)/1024:.0f}KB")
    pairs = []
    for blk in re.split(r"<article\b", t)[1:]:
        mn = re.search(r'data-analytics-item-name="([^"]{2,40})"', blk)
        mh = re.search(r'href="(/zh-cn/dramas/[^"]+)"', blk)
        if mn and mh:
            pairs.append((mn.group(1), mh.group(1)))
    print(f"article→slug 映射 {len(pairs)} 对:")
    for name, slug in pairs[:5]:
        print(f"  {name} → {slug}")
    # 前 2 个详情页 JSON-LD
    for name, slug in pairs[:2]:
        time.sleep(1.0)
        d = c.get("https://shortdramacast.com" + slug).text
        print(f"\n== {name} {slug} ({len(d)/1024:.0f}KB)")
        for m in re.finditer(
                r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>',
                d, re.S):
            try:
                obj = json.loads(m.group(1).strip())
            except json.JSONDecodeError:
                print("  [ld+json 解析失败]", m.group(1)[:100])
                continue
            objs = obj if isinstance(obj, list) else [obj]
            for o in objs:
                t_ = o.get("@type", "?")
                keys = sorted(o.keys())
                print(f"  JSON-LD @type={t_} keys={keys}")
                for k in ("name", "seasonNumber", "numberOfEpisodes", "genre"):
                    if k in o:
                        print(f"    {k} = {o[k]!r}")
