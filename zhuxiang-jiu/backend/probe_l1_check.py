# -*- coding: utf-8 -*-
"""验证 L1 补链: 三部 DataEye 榜单剧主行 + 其跨源快照互链"""
import io
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, ".")

from db import get_session
from models import Drama, DramaRankSnapshot

NAMES = ["我隐藏身份，找到了新妈妈", "傅总别装了，你老婆要掀桌", "东北话事人"]

with get_session() as s:
    for name in NAMES:
        d = s.query(Drama).filter(Drama.drama_name == name).first()
        if not d:
            print(f"!! 未找到主行: {name}")
            continue
        snaps = (s.query(DramaRankSnapshot)
                 .filter(DramaRankSnapshot.drama_id == d.drama_id)
                 .order_by(DramaRankSnapshot.source).all())
        print(f"== {name} (id={d.drama_id} main_platform={d.main_platform} "
              f"episodes={d.episodes} item_id={d.item_id})")
        for sn in snaps:
            print(f"   [{sn.source}/{sn.rank_type}] no={sn.rank_no} "
                  f"heat={sn.heat_value}{sn.heat_unit or ''} "
                  f"period={sn.period_start}~{sn.period_end}")
