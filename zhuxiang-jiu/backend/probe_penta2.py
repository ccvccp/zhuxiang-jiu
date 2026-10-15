# -*- coding: utf-8 -*-
"""二轮: 三候选站条目结构解剖"""
import io
import re
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
import httpx

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36")

with httpx.Client(timeout=20.0, follow_redirects=True,
                  headers={"User-Agent": UA}) as c:
    # 1) 短剧工程 风向标(702KB)
    t = c.get("https://www.duanjugongcheng.com/cn/fengxiangbiao").text
    open("djgc_sample.html", "w", encoding="utf-8").write(t)
    print("== duanjugongcheng fengxiangbiao len:", len(t))
    # 找榜单条目容器
    for pat in (r'class="[^"]*rank[^"]*"', r'class="[^"]*item[^"]*"',
                r'<h[23][^>]*>[^<]{2,40}</h[23]>',
                r'href="/cn/drama[^"]*"'):
        ms = re.findall(pat, t)
        from collections import Counter
        top = Counter(ms).most_common(5)
        print(f"pat={pat[:36]!r} n={len(ms)} top={top}")
    # 热度值形态
    hv = re.findall(r"热度[：:}\s<]*([\d.,]+\s*[万亿kwKWK]?)", t)
    print("heat values n=", len(hv), hv[:12])

    # 2) shortdramacast Top100
    t2 = c.get("https://shortdramacast.com/zh-cn/rankings/chinese-short-dramas").text
    open("sdc_sample.html", "w", encoding="utf-8").write(t2)
    print("\n== shortdramacast len:", len(t2))
    for pat in (r'href="(/zh-cn/drama/[^"]*)"', r'class="[^"]*rank[^"]*"',
                r'<h[23][^>]*>([^<]{2,50})</h[23]>'):
        ms = re.findall(pat, t2)
        print(f"pat={pat[:36]!r} n={len(ms)} sample={ms[:6]}")
    hv2 = re.findall(r"[>\s]([\d.]+[万亿KkWw]?)[\s<]", t2)
    print("numeric tokens n=", len(hv2), hv2[:15])

    # 3) djtj7
    t3 = c.get("https://www.djtj7.com.cn/").text
    open("djtj7_sample.html", "w", encoding="utf-8").write(t3)
    print("\n== djtj7 len:", len(t3))
    for pat in (r'href="(/[^"]*\.html?)"', r'class="[^"]*(?:rank|item|list)[^"]*"',
                r'《([^》]{2,25})》'):
        ms = re.findall(pat, t3)
        print(f"pat={pat[:36]!r} n={len(ms)} sample={ms[:8]}")
