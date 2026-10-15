# -*- coding: utf-8 -*-
"""toutiaoapi SERP 壳页形态定性: 有无 SSR 数据/剧卡片价值"""
import io
import re
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
import httpx

url = ("https://search5-search-hl.toutiaoapi.com/search"
       "?keyword=%E5%A5%BD%E9%9B%A8%E7%9F%A5%E6%97%B6%E8%8A%82%20%E7%9F%AD%E5%89%A7&aid=777821")
with httpx.Client(timeout=15.0, follow_redirects=True,
                  headers={"User-Agent": "Mozilla/5.0 (Linux; Android 13; Pixel 7) "
                          "AppleWebKit/537.36 Chrome/120.0 Mobile Safari/537.36"}) as c:
    r = c.get(url)
t = r.text
print("len:", len(t))
for kw in ("_SSR", "SSR_HYDRATED", "window.__INITIAL_STATE__", "RENDER_DATA",
           "好雨知时节", "novelquickapp", "红果", "series", "video_id"):
    n = t.count(kw)
    print(f"kw={kw!r} count={n}")
# 好雨知时节 出现处上下文
for m in list(re.finditer("好雨知时节", t))[:3]:
    print("\nctx:", repr(t[max(0, m.start() - 120):m.start() + 180]))
