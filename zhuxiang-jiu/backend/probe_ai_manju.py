# -*- coding: utf-8 -*-
"""AI 榜×红果漫剧池关联数据取数"""
import io
import re
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, ".")

from db import get_session
from models import Drama, DramaRankSnapshot

# 批1 漫剧 9 部(红果 video-animation-share 形态入池)
MANJU = ["游戏入侵", "归墟", "反相之地", "持械入宋", "垫底漫画家",
         "困兽之战", "万妖图录传", "聚宝仙盆", "网游：毒帝无双"]

with get_session() as s:
    print("== sdc_ai 25 条(关联状态) ==")
    rows = (s.query(DramaRankSnapshot, Drama)
            .outerjoin(Drama, DramaRankSnapshot.drama_id == Drama.drama_id)
            .filter(DramaRankSnapshot.source == "sdc_ai")
            .order_by(DramaRankSnapshot.rank_no).all())
    for sn, d in rows:
        plat = d.main_platform if d else "-"
        ep = d.episodes if d else "-"
        hit = next((m for m in MANJU if m in sn.drama_name), "")
        print(f"  no={sn.rank_no} {sn.drama_name} linked={'Y' if d else 'N'}"
              f"({plat}) ep={ep} 漫剧系列={'★' + hit if hit else ''}")

    print("\n== linked 8 部的主行快照全景(各自身在哪些榜) ==")
    for sn, d in rows:
        if d is None:
            continue
        snaps = (s.query(DramaRankSnapshot)
                 .filter(DramaRankSnapshot.drama_id == d.drama_id)
                 .order_by(DramaRankSnapshot.source).all())
        print(f"  {d.drama_name} (main={d.main_platform} ep={d.episodes}):")
        for x in snaps:
            print(f"    [{x.source}/{x.rank_type}] no={x.rank_no}")

    print("\n== 池内主表 hongguo 剧总数与漫剧 9 部在库状态 ==")
    n_hg = s.query(Drama).filter(Drama.main_platform == "hongguo").count()
    print("  hongguo main_platform 行:", n_hg)
    for m in MANJU:
        d = s.query(Drama).filter(Drama.drama_name.like(f"%{m}%")).first()
        print(f"  {m}: {'在池 ' + str(d.episodes) + '集' if d else '不在池'}")
