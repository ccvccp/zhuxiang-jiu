# -*- coding: utf-8 -*-
"""归因分析前置核查: 三榜观测覆盖面 + 跨榜同现网络(严格口径)"""
import io
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, ".")

from collections import defaultdict

from db import get_session
from models import DramaRankSnapshot

with get_session() as s:
    rows = (s.query(DramaRankSnapshot)
            .filter(DramaRankSnapshot.source.in_(["dataeye", "xinbang", "kuaishou"]))
            .all())

boards = defaultdict(list)   # (source, rank_type) -> [(no, name)]
for sn in rows:
    boards[(sn.source, sn.rank_type)].append((sn.rank_no, sn.drama_name))

for k in sorted(boards):
    print(k, "n=%d" % len(boards[k]),
          sorted(boards[k], key=lambda x: (x[0] is None, x[0])))

# 剧名 -> 出现的榜集合(交叉网络)
net = defaultdict(set)
for (src, rt), items in boards.items():
    for no, name in items:
        net[name].add(f"{src}/{rt}")

print("\n== multi-board dramas ==")
for name, bs in sorted(net.items(), key=lambda kv: -len(kv[1])):
    if len(bs) > 1:
        print(f"  {name}: {sorted(bs)}")

total = len(net)
multi = sum(1 for bs in net.values() if len(bs) > 1)
print(f"\nboard-dramas={total} multi-board={multi}")
