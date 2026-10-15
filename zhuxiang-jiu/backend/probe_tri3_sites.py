# -*- coding: utf-8 -*-
"""三站接入前置: duanjugongcheng 完整 robots + 三站条目细解剖"""
import io
import re
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
import httpx

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36")

with httpx.Client(timeout=20.0, follow_redirects=True,
                  headers={"User-Agent": UA}) as c:
    # 1) duanjugongcheng robots 完整
    rr = c.get("https://www.duanjugongcheng.com/robots.txt")
    print("== duanjugongcheng robots FULL ==")
    print(rr.text)

    # 2) djtj7 条目解剖
    t = c.get("https://www.djtj7.com.cn/").text
    print("\n== djtj7 rank-list 条目 ==")
    i = t.find('class="rank-list"')
    print(repr(t[i:i + 900]))
    print("\nlinks:", re.findall(r'<a[^>]*href="([^"]+)"[^>]*>', t[i:i + 2000])[:5])

    # 3) shortdramacast 条目解剖
    t2 = c.get("https://shortdramacast.com/zh-cn/rankings/chinese-short-dramas").text
    print("\n== sdc ranking 条目 ==")
    i2 = t2.find('class="ranking-shell"')
    seg = t2[i2:i2 + 1500]
    print(repr(seg[:1200]))
    print("\nsdc links:", re.findall(r'href="([^"]+)"', seg)[:8])
