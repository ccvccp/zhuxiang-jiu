# -*- coding: utf-8 -*-
"""定字段: djgc 完整 tr 行 + sdc 榜单条目行"""
import io
import re
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
import httpx

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36")

with httpx.Client(timeout=20.0, follow_redirects=True,
                  headers={"User-Agent": UA}) as c:
    t3 = c.get("https://www.duanjugongcheng.com/cn/fengxiangbiao").text
    print("== djgc: 完整 tr 行 ==")
    trs = re.findall(r"<tr\b[^>]*>.*?</tr>", t3, re.S)
    print("tr n=", len(trs))
    if trs:
        # 找含《》的 tr
        for tr in trs:
            if "《" in tr:
                print(repr(tr[:1200]))
                break
    # 表头
    th = re.findall(r"<th[^>]*>(.*?)</th>", t3, re.S)
    print("th:", [re.sub(r"<[^>]+>", "", x).strip() for x in th[:12]])
    # 榜单分组标题
    for m in list(re.finditer(r"<h\d[^>]*>([^<]{2,30})</h\d>", t3))[:8]:
        print("h:", m.group(1))

    t2 = c.get("https://shortdramacast.com/zh-cn/rankings/chinese-short-dramas").text
    print("\n== sdc: 榜单条目行 ==")
    # 找名次数字+剧名的行结构
    for pat in (r'class="[^"]*(?:top|entry|list-item|chart)[^"]*"',
                r'<article[^>]*>', r'<ol[^>]*>'):
        ms = re.findall(pat, t2)
        from collections import Counter
        print(f"pat={pat[:34]!r} n={len(ms)} top={Counter(ms).most_common(4)}")
    # updated 时间戳
    i = t2.find("ranking-updated")
    print("updated ctx:", repr(re.sub(r"\s+", " ", t2[i - 50:i + 200])))
    # 表格?
    print("table n=", t2.count("<table"), "tbody n=", t2.count("<tbody"))
    if "<table" in t2:
        tb = re.search(r"<table.*?</table>", t2, re.S)
        print(repr(tb.group(0)[:1000]))
