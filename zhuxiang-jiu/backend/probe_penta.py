# -*- coding: utf-8 -*-
"""五链形态判定: robots -> 页面形态 -> 数据价值(纯文本分析, 不执行 JS)"""
import io
import re
import sys
from urllib.parse import urlparse

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
import httpx

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36")

LINKS = [
    "https://www.djtj7.com.cn/",
    "https://www.duanjugongcheng.com/cn/fengxiangbiao",
    "https://shortdramacast.com/zh-cn/rankings/chinese-short-dramas",
    "https://www.hongguoduanjuc.com.cn/",
    "https://www.xcysw0.com.cn/",
]

with httpx.Client(timeout=15.0, follow_redirects=True,
                  headers={"User-Agent": UA}) as c:
    for url in LINKS:
        p = urlparse(url)
        print("=" * 70)
        print("URL:", url)
        try:
            rr = c.get(f"{p.scheme}://{p.netloc}/robots.txt")
            robots = rr.text if rr.status_code == 200 else None
            print(f"robots: {rr.status_code}",
                  ("| " + robots[:200].replace("\n", " ")) if robots else "")
        except Exception as e:
            robots = None
            print("robots EXC:", type(e).__name__, str(e)[:80])
        try:
            r = c.get(url)
            t = r.text
            print("page:", r.status_code, "len:", len(t), "final:", str(r.url)[:70])
            title = re.search(r"<title[^>]*>(.*?)</title>", t, re.S)
            print("title:", title.group(1).strip()[:80] if title else "-")
            marks = []
            for kw in ("短剧", "榜单", "排行", "热度", "rank", "RENDER_DATA",
                       "_SSR", "INITIAL_STATE", "window.open", "下载", "App",
                       "apk", "二维码"):
                n = t.count(kw)
                if n:
                    marks.append(f"{kw}={n}")
            print("marks:", " ".join(marks[:12]))
        except Exception as e:
            print("page EXC:", type(e).__name__, str(e)[:100])
        print()
