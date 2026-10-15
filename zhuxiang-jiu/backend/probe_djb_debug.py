# -*- coding: utf-8 -*-
"""djb_parse 0 条调试: 分块与正则逐步验证"""
import io
import re
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, ".")

text = open("djb_sample.html", encoding="utf-8").read()
print("len:", len(text))

blocks = re.split(r'<a\s[^>]*class="card"', text)
print("split blocks:", len(blocks))

# a 标签真实形态
for m in list(re.finditer(r'<a[^>]{0,300}?>', text))[:0]:
    pass
a_tags = re.findall(r'<a[^>]*class="card"[^>]*>', text)
print('class="card" a tags:', len(a_tags))
if a_tags:
    print("sample a tag:", a_tags[0][:250])

# href+title 同标签形态
mh = list(re.finditer(
    r'href="/duanju/info-(\d+)\.html"[^>]*title="短剧《([^》]{2,30})》"', text))
print("href+title matches:", len(mh))
if mh:
    print("first:", mh[0].group(1), mh[0].group(2))
    # 该匹配的 a 标签开头(往前找 <a)
    astart = text.rfind("<a", 0, mh[0].start())
    print("tag head:", text[astart:astart + 120])

# 万热度 所在块
blocks2 = re.split(r'class="card"', text)
print("split by class=card:", len(blocks2))
for i, blk in enumerate(blocks2[1:3], 1):
    mh2 = re.search(r'href="/duanju/info-(\d+)\.html"[^>]*title="短剧《([^》]{2,30})》"', blk)
    mv = re.search(r"([\d.]+)万热度", blk)
    print(f"blk{i}: href_title={'Y' if mh2 else 'N'} heat={mv.group(1) if mv else 'N'}")
