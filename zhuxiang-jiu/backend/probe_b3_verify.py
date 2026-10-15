# -*- coding: utf-8 -*-
"""批3 入池终验: ①今日新增 12 部清单+PUGC ②我李长生/重回1988 itemId 填充
③新快照 contentType ④谁说没灵根 IP 季跨度"""
import io
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, ".")
from sqlalchemy import func, select
from db import get_session
from models import Drama, DramaRankSnapshot

with get_session() as s:
    n = s.execute(select(func.count()).select_from(Drama)).scalar()
    print(f"主表 {n} 部")
    print("\n== 我李长生/重回1988/持械入宋/聚宝仙盆(联动与季系)")
    for nm in ("我李长生重回八零，赶山狩猎养妻女", "重回1988，我从倒货开始养家致富",
               "持械入宋：第一季", "聚宝仙盆之杂灵根才是真BOSS",
               "谁说没灵根不能修仙的？之无灵证道"):
        d = s.execute(select(Drama).where(Drama.drama_name == nm)).scalar_one_or_none()
        if d:
            print(f"  {d.drama_name}: platform={d.main_platform} itemId={d.item_id} "
                  f"ep={d.episodes} ai={bool(d.is_ai_drama)}")
        else:
            print(f"  {nm}: <未入池>")

    print("\n== 今日新增主行(created_at 今天, 批3)")
    import datetime as _dt
    today = _dt.date.today()
    rows = s.execute(select(Drama).where(
        func.date(Drama.created_at) == today,
        Drama.main_platform == "hongguo",
        Drama.created_at >= _dt.datetime.now() - _dt.timedelta(hours=1)
    )).scalars().all()
    for d in rows:
        print(f"  {d.drama_name}: ep={d.episodes} itemId={d.item_id} tags={d.genre_tags}")

    print("\n== 今日 hongguo share_pool 快照 contentType(批3新落)")
    snaps = s.execute(select(DramaRankSnapshot).where(
        DramaRankSnapshot.source == "hongguo",
        DramaRankSnapshot.rank_type == "share_pool",
        DramaRankSnapshot.created_at >= _dt.datetime.now() - _dt.timedelta(hours=1)
    )).scalars().all()
    cts = {}
    for sn in snaps:
        ct = ((sn.raw_json or {}).get("contract") or {}).get("contentType")
        cts[ct] = cts.get(ct, 0) + 1
    print(f"  近1h 新落快照 {len(snaps)} 条 contentType 分布 {cts}")
    for sn in snaps[:3]:
        ct = ((sn.raw_json or {}).get("contract") or {}).get("contentType")
        print(f"    {sn.drama_name}: {ct}")

    print("\n== 谁说没灵根 IP 季跨度(djgc 快照)")
    for sn in s.execute(select(DramaRankSnapshot.drama_name, DramaRankSnapshot.source)
                        .where(DramaRankSnapshot.drama_name.like("%谁说没灵根%"))
                        .distinct()).all():
        print(f"  [{sn.source}] {sn.drama_name}")
