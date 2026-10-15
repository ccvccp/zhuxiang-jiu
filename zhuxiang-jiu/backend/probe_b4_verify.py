# -*- coding: utf-8 -*-
"""批4 18 新行终验: 构成 + 5 部推荐位激活证据 + contentType"""
import io
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, ".")
from sqlalchemy import select
from db import get_session
from models import Drama, DramaRankSnapshot

ACTIVATE = ["替白月光撕开那张脸", "离婚当天，董事长母亲找到我",
            "全家都盼弟弟成龙，他偏托我成凤", "天降俏弟媳，大姑子们追着宠",
            "一卦显圣1：得到道家传承后我终于发达了"]

with get_session() as s:
    rows = s.execute(select(Drama).order_by(Drama.drama_id.desc()).limit(18)).scalars().all()
    print(f"== 最新 18 行(批4)")
    for d in reversed(rows):
        snaps = s.execute(select(DramaRankSnapshot).where(
            DramaRankSnapshot.drama_name == d.drama_name)).scalars().all()
        srcs = sorted({sn.source for sn in snaps})
        ct = next((((sn.raw_json or {}).get("contract") or {}).get("contentType") or "?")
                  for sn in snaps if sn.source == "hongguo")
        act = " ★激活" if d.drama_name in ACTIVATE else ""
        print(f"  #{d.drama_id} [{ct}] {d.drama_name} ep={d.episodes} "
              f"src={srcs}{act}")
    print("\n== 激活五部互链详情")
    for nm in ACTIVATE:
        d = s.execute(select(Drama).where(Drama.drama_name == nm)).scalar_one_or_none()
        if not d:
            print(f"  {nm}: <未入池>")
            continue
        snaps = s.execute(select(DramaRankSnapshot).where(
            DramaRankSnapshot.drama_name == nm)).scalars().all()
        for sn in snaps:
            print(f"  {nm}: [{sn.source}] rank={sn.rank_no} linked={sn.drama_id} "
                  f"snap={sn.snapshot_time}")
