# -*- coding: utf-8 -*-
"""批5 25 新剧匹配矩阵: 全源快照命中 + L1 互链状态 + 口径分野验证"""
import io
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, ".")
from sqlalchemy import select
from db import get_session
from models import Drama, DramaRankSnapshot

B5_NEW = list(range(333, 358))  # #333-#357

with get_session() as s:
    dramas = s.execute(select(Drama).where(
        Drama.drama_id.in_(B5_NEW))).scalars().all()
    # 全源快照: 剧名 -> [(source, drama_id, rank_no, date)]
    snaps = {}
    for sn in s.execute(select(DramaRankSnapshot)).scalars().all():
        snaps.setdefault(sn.drama_name, []).append(
            (sn.source, sn.drama_id, sn.rank_no, str(sn.snapshot_time.date())))

    n_hit = n_pure = 0
    print(f"批5 25 新行全源匹配矩阵:")
    for d in sorted(dramas, key=lambda x: x.drama_id):
        ss = snaps.get(d.drama_name, [])
        linked = [x for x in ss if x[1] == d.drama_id]
        dangle = [x for x in ss if x[1] is None]
        other = [x for x in ss if x[1] not in (None, d.drama_id)]
        if linked or other:
            n_hit += 1
            tag = "L1互链" if linked else ("他剧链" if other else "")
            det = []
            for x in linked[:3]:
                det.append(f"{x[0]}#{x[2]}({x[3]})")
            for x in other[:2]:
                det.append(f"{x[0]}#他剧")
            print(f"  #{d.drama_id} {d.drama_name} ep={d.episodes} "
                  f"[{tag}] {' '.join(det)}")
        else:
            n_pure += 1
            dz = f" 悬空:{','.join(sorted(set(x[0] for x in dangle))[:3])}" if dangle else ""
            print(f"  #{d.drama_id} {d.drama_name} ep={d.episodes} [纯新]{dz}")
    print(f"\n汇总: 匹配命中 {n_hit} / 纯新 {n_pure}")

    # 模糊关联(L3 候选): 剧名 4 字前缀在快照层
    print("\n模糊关联(L3 季数/改名候选):")
    snap_names = set(snaps)
    for d in sorted(dramas, key=lambda x: x.drama_id):
        pre = d.drama_name[:4]
        fz = [n for n in snap_names if n != d.drama_name and pre in n]
        if fz:
            print(f"  {d.drama_name} <- {fz[:4]}")
