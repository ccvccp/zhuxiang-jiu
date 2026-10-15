# -*- coding: utf-8 -*-
"""三轮: duanjubaike rebo.html 条目结构解剖(剧名/热度/集数容器, 100名分布)"""
import io
import re
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
import httpx

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36")

with httpx.Client(timeout=15.0, follow_redirects=True,
                  headers={"User-Agent": UA}) as c:
    t = c.get("https://www.duanjubaike.net/paihang/rebo.html").text

open("djb_sample.html", "w", encoding="utf-8").write(t)
print("len:", len(t))

# 万热度 角标出现次数 = 条目热度数
heats = re.findall(r"([\d.]+)万热度", t)
print("万热度 entries:", len(heats), "head:", heats[:10])

# 卡片容器: 找一个完整卡片(含剧名+集数+热度)的 HTML 块
i = t.find("万热度")
if i > 0:
    # 向前找卡片开头(前 800 字), 打印整个卡片结构
    print("\ncard ctx:", repr(t[max(0, i - 900):i + 200]))

# 剧名链接形态
for pat in (r'<a[^>]*href="([^"]*)"[^>]*>\s*<[^>]*>\s*[^<]*[^<]*</a>',
            r'href="(/[^"]*)"[^>]*title="([^"]*)"',
            r'title="([^"]{2,30})"'):
    ms = re.findall(pat, t)
    print(f"\npat={pat[:40]!r} n={len(ms)} sample={ms[:8]}")

# 集数形态
eps = re.findall(r"共?(\d+)集", t)
print("\n集数 n=", len(eps), eps[:10])

# 分页
pages = re.findall(r'href="[^"]*paihang[^"]*page[^"]*"', t)
print("pagination:", pages[:5])
