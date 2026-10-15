# -*- coding: utf-8 -*-
"""批3 12 部匹配状态增量复查(10-16 聚合榜新快照后):
验证「share 池领先聚合榜 N 天」假设——昨日纯新的 7 部今晨是否被收录"""
import io
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, ".")
from sqlalchemy import select
from db import get_session
from models import Drama, DramaRankSnapshot

B3 = ["装穷参加同学会，合照把我给裁了", "重生八五，靠购物返现系统赢麻了",
      "正在搬砖，被宋小姐拉去闪婚了", "掌生2：嫡女国师",
      "这一次，女婿他绝不低头", "山海藏墟，无眼窥天",
      "谁让这个悍匪修仙的！", "重回八一从打猎开始发家",
      "谁说没灵根不能修仙的？之无灵证道", "新娘逃婚，我换新娘了",
      "星落庭深万物生", "《哪吒传奇3》-7"]
# 10-15 时点状态(批3 分析文档基线): 纯新=7(重生八五/正在搬砖/掌生2/山海藏墟/谁让悍匪/重回八一/谁说没灵根) + 新娘逃婚/星落庭深(推荐位发现纯新) = 9 纯新
# 跨源激活=2(装穷同学会 djgc / 这一次女婿 duanjubaike) + PUGC=1(哪吒传奇)

with get_session() as s:
    snap15, snap16 = {}, {}
    for sn in s.execute(select(DramaRankSnapshot.source, DramaRankSnapshot.drama_name,
                               DramaRankSnapshot.rank_no,
                               DramaRankSnapshot.snapshot_time)).all():
        day = str(sn[3].date())
        box = snap15 if day == "2026-10-15" else (snap16 if day == "2026-10-16" else None)
        if box is not None:
            box.setdefault(sn[1], []).append((sn[0], sn[2]))

    print(f"快照 10-15 剧名 {len(snap15)} / 10-16 剧名 {len(snap16)}")
    print("\n== 批3 12 部 × 10-16 聚合榜收录状态")
    for nm in B3:
        d = s.execute(select(Drama).where(Drama.drama_name == nm)).scalar_one_or_none()
        s15 = [x for x in snap15.get(nm, []) if x[0] != "hongguo"]
        s16 = [x for x in snap16.get(nm, []) if x[0] != "hongguo"]
        tags = []
        if s15:
            tags.append("10-15已链:" + ",".join(f"{a}#{r}" for a, r in s15))
        if s16:
            tags.append("★10-16新收录:" +
                        ",".join(f"{a}#{r}" for a, r in s16))
        lead = ""
        if not s15 and s16:
            d0 = d.first_seen_at if d else None
            lead = f" 领先≈1天" if d0 else ""
        print(f"  {'✓' if d else '?'} {nm}")
        print(f"     {'; '.join(tags) if tags else '两日均无聚合榜观测(纯新)'}{lead}")
