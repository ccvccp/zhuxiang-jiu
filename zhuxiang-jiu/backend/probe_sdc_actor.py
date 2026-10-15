# -*- coding: utf-8 -*-
"""演员榜卡片结构解剖"""
import io
import re
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, ".")

from drama85_media_crawler import fetch_html

t = fetch_html("https://shortdramacast.com/zh-cn/rankings/hot-short-drama-actors")
m = re.search(r'<article class="actor-discovery-card is-featured".*?</article>', t, re.S)
print("featured card:")
print(repr(m.group(0)[:1400]) if m else "NA")
print()
m2 = re.search(r'<article class="actor-discovery-card(?! is-featured)[^"]*".*?</article>', t, re.S)
print("normal card:")
print(repr(m2.group(0)[:900]) if m2 else "NA")
