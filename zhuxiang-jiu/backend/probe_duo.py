# -*- coding: utf-8 -*-
"""hongguoduanjuc(仿红果?) 与 xcysw0(星辰影视聚合) 定性解剖:
榜单条目结构 / 外链指向 / 备案标识 / 盗版播放入口"""
import io
import re
import sys
from collections import Counter

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
import httpx

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36")

with httpx.Client(timeout=15.0, follow_redirects=True,
                  headers={"User-Agent": UA}) as c:
    for name, url in (("hongguoduanjuc", "https://www.hongguoduanjuc.com.cn/"),
                      ("xcysw0", "https://www.xcysw0.com.cn/")):
        t = c.get(url).text
        print("=" * 70)
        print(f"== {name} len={len(t)}")
        # 1) 外链域名分布
        hrefs = re.findall(r'href="(https?://[^"/]+)', t)
        print("外链域名 top:", Counter(h.replace("https://", "")
                                       for h in hrefs).most_common(8))
        # 2) 条目结构
        for pat in (r'class="[^"]*(?:rank|item|list|card)[^"]*"',
                    r'《([^》]{2,25})》'):
            ms = re.findall(pat, t)
            print(f"pat={pat[:34]!r} n={len(ms)} sample={ms[:6]}")
        # 3) 备案/版权标识
        for kw in ("ICP", "备案", "版权", "Copyright", "©", "京公网",
                   "fanqienovel", "novelquickapp", "byte", "字节", "抖音"):
            n = t.count(kw)
            if n:
                m = re.search(kw, t)
                ctx = t[max(0, m.start() - 60):m.start() + 80].replace("\n", " ")
                print(f"  kw={kw!r} n={n} ctx={ctx!r}")
        # 4) 播放/下载入口
        for pat in (r'href="([^"]*(?:play|video|watch|m3u8)[^"]*)"',
                    r'(?:播放|在线看|立即观看|全集)'):
            ms = re.findall(pat, t)
            if ms:
                print(f"play pat={pat[:30]!r} n={len(ms)} sample={str(ms[:5])[:120]}")
        print()
