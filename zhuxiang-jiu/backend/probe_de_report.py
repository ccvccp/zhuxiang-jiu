# -*- coding: utf-8 -*-
"""DataEye 三榜(热力总榜/抖音端/红果端)快照全量取数 → 对比报告数据"""
import io
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, ".")

from db import get_session
from models import Drama, DramaRankSnapshot

with get_session() as s:
    rows = (s.query(DramaRankSnapshot, Drama)
            .outerjoin(Drama, DramaRankSnapshot.drama_id == Drama.drama_id)
            .filter(DramaRankSnapshot.source == "dataeye")
            .order_by(DramaRankSnapshot.rank_type, DramaRankSnapshot.rank_no)
            .all())
    print("dataeye snapshots:", len(rows))
    for sn, d in rows:
        name = sn.drama_name
        linked = d is not None
        plat = d.main_platform if linked else "-"
        eps = d.episodes if linked else "-"
        tags = ",".join((d.genre_tags or {}).get("tags", [])
                        if isinstance(d.genre_tags, dict) else (d.genre_tags or [])
                        if isinstance(d.genre_tags, list) else []) if linked else "-"
        print(f"[{sn.rank_type}] no={sn.rank_no} {name} "
              f"heat={sn.heat_value}{sn.heat_unit or ''} "
              f"period={sn.period_start}~{sn.period_end} "
              f"linked={'Y' if linked else 'N'}({plat}) ep={eps} tags={tags}")

    # 各榜计数
    from collections import Counter
    c = Counter(sn.rank_type for sn, _ in rows)
    print("\ncounts:", dict(c))

    # 快照总量与时间
    all_sn = s.query(DramaRankSnapshot).count()
    print("total snapshots:", all_sn)
