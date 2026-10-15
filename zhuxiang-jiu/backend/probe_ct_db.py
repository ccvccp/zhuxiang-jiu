# -*- coding: utf-8 -*-
"""contentType 落库重查: share_pool 快照 raw_json.contract.contentType 层级"""
import io
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, ".")
from sqlalchemy import select
from db import get_session
from models import DramaRankSnapshot

with get_session() as s:
    for src in ("hongguo", "wukong", "xigua", "baidu"):
        snaps = s.execute(select(DramaRankSnapshot).where(
            DramaRankSnapshot.source == src,
            DramaRankSnapshot.rank_type == "share_pool"
        ).order_by(DramaRankSnapshot.snapshot_time.desc())).scalars().all()
        if not snaps:
            continue
        latest_day = snaps[0].snapshot_time.date()
        day_snaps = [sn for sn in snaps if sn.snapshot_time.date() == latest_day]
        cts: dict = {}
        for sn in day_snaps:
            ct = ((sn.raw_json or {}).get("contract") or {}).get("contentType")
            cts[ct] = cts.get(ct, 0) + 1
        print(f"{src}/share_pool {latest_day}: {len(day_snaps)} 行 "
              f"contract.contentType 分布 {cts}")
