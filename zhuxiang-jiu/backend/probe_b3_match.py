# -*- coding: utf-8 -*-
"""批3 12 新行画像: 构成确认(drama_id 最大 12) + 逐条与 13 源快照匹配状态"""
import io
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, ".")
from sqlalchemy import func, select
from db import get_session
from models import Drama, DramaRankSnapshot

with get_session() as s:
    rows = s.execute(select(Drama).order_by(Drama.drama_id.desc()).limit(15)).scalars().all()
    print(f"主表最大 drama_id 前 15(批3 新行区间):")
    snap_names: dict[str, list[tuple[str, int]]] = {}
    for sn in s.execute(select(DramaRankSnapshot.drama_name, DramaRankSnapshot.source,
                               DramaRankSnapshot.drama_id)).all():
        snap_names.setdefault(sn[0], []).append((sn[1], sn[2] or 0))
    total_snap = s.execute(select(func.count()).select_from(DramaRankSnapshot)).scalar()
    for d in rows:
        # 同名快照(全源)
        same = snap_names.get(d.drama_name, [])
        # 模糊: 含子串关系的快照名
        fuzzy = [(nm, srcs) for nm, srcs in snap_names.items()
                 if nm != d.drama_name and (nm in d.drama_name or d.drama_name in nm)
                 and abs(len(nm) - len(d.drama_name)) <= 8]
        src_list = ",".join(f"{x[0]}{'#L' if x[1] else ''}" for x in same)
        fz = "; ".join(f"{nm}[{','.join(x[0] for x in srcs[:3])}]" for nm, srcs in fuzzy[:4])
        print(f"  #{d.drama_id} {d.drama_name!r}")
        print(f"     platform={d.main_platform} ep={d.episodes} itemId={d.item_id} "
              f"ai={bool(d.is_ai_drama)} tags={d.genre_tags}")
        print(f"     同名快照: {src_list or '无(纯新)'}")
        if fz:
            print(f"     模糊相关: {fz}")
    print(f"\n快照总数 {total_snap}")
