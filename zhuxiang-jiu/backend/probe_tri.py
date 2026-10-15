# -*- coding: utf-8 -*-
"""三链形态判定: duanjubaike 热播榜 / douyin 搜索页 / rebang.vip 百度短剧榜
每链: robots → GET 页面 → 结构化数据线索(剧名容器/榜单条目/热度字段)"""
import io
import re
import sys
from urllib.parse import urlparse

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
import httpx

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36")

LINKS = [
    "https://www.duanjubaike.net/paihang/rebo.html",
    "https://www.douyin.com/search/%E7%9F%AD%E5%89%A7%E7%83%AD%E5%BA%A6%E5%AE%9E%E6%97%B6%E6%A6%9C",
    "https://www.rebang.vip/baidu/drama",
]

with httpx.Client(timeout=15.0, follow_redirects=True,
                  headers={"User-Agent": UA}) as c:
    for url in LINKS:
        p = urlparse(url)
        print("=" * 70)
        print("URL:", url)
        # robots
        try:
            rr = c.get(f"{p.scheme}://{p.netloc}/robots.txt")
            robots = rr.text if rr.status_code == 200 else None
            print(f"robots: {rr.status_code}")
            if robots:
                print("robots head:", robots[:400].replace("\n", " | "))
        except Exception as e:
            robots = None
            print("robots EXC:", e)
        # 页面
        try:
            r = c.get(url)
            t = r.text
            print("page:", r.status_code, "len:", len(t), "final:", str(r.url)[:80])
            title = re.search(r"<title[^>]*>(.*?)</title>", t, re.S)
            print("title:", title.group(1).strip()[:80] if title else "-")
            for kw in ("render_data", "RENDER_DATA", "_SSR", "INITIAL_STATE",
                       "router", "热力", "热度", "播放", "rank", "榜单",
                       "短剧", "class=\"rank", "data-"):
                n = t.count(kw)
                if n:
                    print(f"  kw={kw!r} count={n}")
        except Exception as e:
            print("page EXC:", type(e).__name__, e)
        print()
