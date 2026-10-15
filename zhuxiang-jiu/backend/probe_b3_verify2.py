# -*- coding: utf-8 -*-
"""批3 快照 contentType 补验(不依赖时间窗: 按今日 snapshot_time + 新剧名)"""
import io
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, ".")
from sqlalchemy import select
from db import get_session
from models import Drama, DramaRankSnapshot

NEW = ["谁说没灵根不能修仙的？之无灵证道", "重回八一从打猎开始发家",
       "谁让这个悍匪修仙的！", "山海藏墟，无眼窥天", "这一次，女婿他绝不低头",
       "掌生2：嫡女国师", "正在搬砖，被宋小姐拉去闪婚了",
       "重生八五，靠购物返现系统赢麻了", "装穷参加同学会，合照把我给裁了",
       "我李长生重回八零，赶山狩猎养妻女", "重回1988，我从倒货开始养家致富"]

with get_session() as s:
    print("== 新剧主行")
    for nm in NEW:
        d = s.execute(select(Drama).where(Drama.drama_name == nm)).scalar_one_or_none()
        print(f"  {'✓' if d else '✗缺'} {nm}: platform={d.main_platform if d else '-'} "
              f"ep={d.episodes if d else '-'} itemId={d.item_id if d else '-'}")
    print("\n== 今日快照 contentType(按剧名)")
    import datetime as _dt
    t0 = _dt.datetime.combine(_dt.date.today(), _dt.time.min) - _dt.timedelta(hours=8)
    for nm in NEW[:6]:
        sn = s.execute(select(DramaRankSnapshot).where(
            DramaRankSnapshot.drama_name == nm,
            DramaRankSnapshot.source == "hongguo"
        ).order_by(DramaRankSnapshot.snapshot_time.desc())).scalars().first()
        if sn:
            ct = ((sn.raw_json or {}).get("contract") or {}).get("contentType")
            print(f"  {nm}: snap={sn.snapshot_time} contentType={ct} linked={sn.drama_id}")
        else:
            print(f"  {nm}: <无快照>")
