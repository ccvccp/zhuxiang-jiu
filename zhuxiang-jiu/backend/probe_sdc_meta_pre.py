# -*- coding: utf-8 -*-
"""sdc_ai 快照 + 主表 genre_tags 现状盘点(元数据采集前置)"""
import io
import json
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, ".")

from sqlalchemy import select
from db import get_session
from models import Drama, DramaRankSnapshot

with get_session() as s:
    snaps = s.execute(select(DramaRankSnapshot)
                      .where(DramaRankSnapshot.source == "sdc_ai")
                      .order_by(DramaRankSnapshot.rank_no)).scalars().all()
    print(f"sdc_ai 快照 {len(snaps)} 行")
    for sn in snaps[:3]:
        print(f"  rank={sn.rank_no} name={sn.drama_name} drama_id={sn.drama_id} "
              f"raw_json_keys={list((sn.raw_json or {}).keys())}")
        print(f"    raw_json={json.dumps(sn.raw_json, ensure_ascii=False)[:220]}")
    # 主表 genre_tags 形态样例
    ids = [sn.drama_id for sn in snaps if sn.drama_id]
    if ids:
        rows = s.execute(select(Drama).where(Drama.drama_id.in_(ids))).scalars().all()
        print(f"\n互链主表 {len(rows)} 行 genre_tags 形态:")
        for d in rows[:5]:
            print(f"  {d.drama_name}: {json.dumps(d.genre_tags, ensure_ascii=False)[:160]}")
        # 全表 genre_tags 非空形态分布
        allrows = s.execute(select(Drama.genre_tags).where(Drama.genre_tags.isnot(None))).all()
        kinds = {}
        for (g,) in allrows:
            k = type(g).__name__ + ("/" + (type(list(g.values())[0]).__name__ if isinstance(g, dict) and g else ""))
            kinds[k] = kinds.get(k, 0) + 1
        print(f"\n全表 genre_tags 非空 {len(allrows)} 行形态分布: {kinds}")
