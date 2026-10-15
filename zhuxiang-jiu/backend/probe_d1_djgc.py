# -*- coding: utf-8 -*-
"""D+1 补充查证: djgc/sdc 10-16 新期 vs 批3 8 部收录; djgc 两期名册变动与 linked 构成"""
import io
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, ".")
from sqlalchemy import select
from db import get_session
from models import DramaRankSnapshot

B3_NEW = ["装穷参加同学会，合照把我给裁了", "重生八五，靠购物返现系统赢麻了",
          "正在搬砖，被宋小姐拉去闪婚了", "掌生2：嫡女国师", "山海藏墟，无眼窥天",
          "谁让这个悍匪修仙的！", "重回八一从打猎开始发家", "谁说没灵根不能修仙的？之无灵证道",
          "这一次，女婿他绝不低头"]

with get_session() as s:
    for src in ("djgc", "sdc"):
        rows = s.execute(select(DramaRankSnapshot).where(
            DramaRankSnapshot.source == src)).scalars().all()
        d15 = {r.drama_name: r for r in rows if str(r.snapshot_time.date()) == "2026-10-15"}
        d16 = {r.drama_name: r for r in rows if str(r.snapshot_time.date()) == "2026-10-16"}
        up = set(d16) - set(d15)
        down = set(d15) - set(d16)
        print(f"== {src} 两期名册: 新上榜(+{len(up)}) 落榜(-{len(down)}) ==")
        for n in sorted(up)[:15]:
            r = d16[n]
            print(f"  +#{r.rank_no} {'已链' if r.drama_id else '悬空'} {n}")
        for n in sorted(down)[:15]:
            print(f"  -#{d15[n].rank_no} {n}")
        print(f"== {src} 10-16 期: 批3 剧收录状态 ==")
        for name in B3_NEW:
            hit = [n for n in d16 if name[:6] in n or n[:6] in name]
            if not hit:
                print(f"  不在榜: {name}")
            for n in hit:
                r = d16[n]
                print(f"  #{r.rank_no} {'已链' if r.drama_id else '悬空'}: {n} <- {name}")
        print()
