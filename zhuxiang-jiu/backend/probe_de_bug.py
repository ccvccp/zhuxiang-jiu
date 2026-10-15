# -*- coding: utf-8 -*-
"""定位: de_parse 对《东北话事人》两条目的实际输出与 best 去重选择"""
import io
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, ".")

import re

from drama85_media_crawler import DE_BOARDS, RANK_AFTER, fetch_text, _rank_from_after

text = fetch_text("https://m.thepaper.cn/newsDetail_forward_34234356")

marks = []
for pat, rtype, label in DE_BOARDS:
    m = pat.search(text)
    if m:
        marks.append((m.start(), rtype, label))
marks.sort()

# 手工重演 de_parse 内核(红果段)并打印每个《》的 after/rank/heat
pos, rtype, label = marks[-1]
end = len(text)
seg = text[pos:end]
print(f"seg [{label}] span={pos}..{end}")
for m in re.finditer(r"《\s*([^《》]{2,25})\s*》", seg):
    name = re.sub(r"\s+", "", m.group(1))
    tail_m = re.search(r"[。；]", seg[m.end():])
    after = seg[m.end():m.end() + (tail_m.start() if tail_m else 120)]
    before = seg[max(0, m.start() - 12):m.start()]
    rank = _rank_from_after(after)
    # 前置倒装
    if rank is None:
        mb = re.search(r"(?:榜首|第([一二三四五六七八九十\d]{1,3})名?)\s*$", before)
        if mb:
            rank = 1 if not mb.group(1) else int(mb.group(1)) if mb.group(1).isdigit() else None
    hv = re.search(r"(?:热力值|日均热度|热度)[达近约超]*\s*([\d.]+)\s*(万|亿)", after)
    print(f"\n{name!r}\n  after={after[:60]!r}\n  rank={rank} "
          f"heat={hv.group(1) + hv.group(2) if hv else None}\n  before={before!r}")

# RANK_AFTER 对「第二，」单测
print("\nRANK_AFTER test:", RANK_AFTER.search("第二，日均热度达5968.8万"))
