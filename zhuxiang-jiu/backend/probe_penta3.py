# -*- coding: utf-8 -*-
"""三轮: djtj7/duanjugongcheng/shortdramacast 条目块解剖"""
import io
import re
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

# 1) djtj7: rank-list 块解剖
t = open("djtj7_sample.html", encoding="utf-8").read()
print("== djtj7 ==")
i = t.find('class="rank-list"')
blk = t[i:i + 2500]
# 条目切分
entries = re.split(r'class="rank-no"', t)
print("rank-no blocks:", len(entries) - 1)
if len(entries) > 2:
    print("entry sample:", repr(entries[1][:600]))
# 全部条目的剧名与数值
for m in list(re.finditer(r'class="rank-no"[^>]*>(\d+)</(\w+)>', t))[:3]:
    ctx = t[m.start():m.start() + 500]
    print("ctx:", repr(ctx[:400]))

# 2) duanjugongcheng: 100 序号单元块
t2 = open("djgc_sample.html", encoding="utf-8").read()
print("\n== duanjugongcheng ==")
i2 = t2.find('class="absolute inset-0 flex items-center justify-center')
print("unit ctx:", repr(t2[max(0, i2 - 700):i2 + 500])[:1200])

# 3) shortdramacast: ranking 条目
t3 = open("sdc_sample.html", encoding="utf-8").read()
print("\n== shortdramacast ==")
# 找第一个剧条目: 含 /zh-cn/ 链接的形态
hrefs = re.findall(r'href="(/[^"]{5,60})"', t3)
from collections import Counter
paths = Counter(h.split("/")[2] if h.count("/") >= 2 else h for h in hrefs)
print("href top sections:", paths.most_common(8))
# ranking-item 类
for pat in (r'class="ranking-(?:item|row|card|list)[^"]*"',
            r'<a[^>]*class="[^"]*group[^"]*"[^>]*>'):
    ms = re.findall(pat, t3)
    print(f"pat={pat[:40]!r} n={len(ms)}")
i3 = t3.find("ranking-updated")
print("near updated:", repr(t3[i3:i3 + 700])[:800])
