# -*- coding: utf-8 -*-
"""D+1 互链稳态构成查证: duanjubaike 两期快照(UTC 10-15/10-16) linked 清单分类,
两期榜单名册变动, 批3 剧收录状态"""
import io
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, ".")
from sqlalchemy import select
from db import get_session
from models import DramaRankSnapshot

B4 = ["替白月光撕开那张脸", "离婚当天，董事长母亲找到我", "全家都盼弟弟成龙，他偏托我成凤",
      "天降俏弟媳，大姑子们追着宠", "一卦显圣1：得到道家传承后我终于发达了",
      "偏要偷尝半口甜", "老婆出国旅游不带我，我笑了", "保姆真千金，心声漏了只想跑",
      "嫁进沈家第一天，我把规矩掀了", "给流浪汉带了份盒饭，他让我明天别上班",
      "年薪八百万，我妈以为我月薪三千八", "准儿媳她不对劲", "继承者游戏：双鱼对局",
      "东北老太嫁台北，两手叉腰爱谁谁", "假千金的优秀全面曝光了", "半生托举，换来七分答卷",
      "落魄书生：开局迎娶俏佳人", "退休老爸不好惹"]
B3_NEW = ["装穷参加同学会，合照把我给裁了", "重生八五，靠购物返现系统赢麻了",
          "正在搬砖，被宋小姐拉去闪婚了", "掌生2：嫡女国师", "山海藏墟，无眼窥天",
          "谁让这个悍匪修仙的！", "重回八一从打猎开始发家", "谁说没灵根不能修仙的？之无灵证道"]

with get_session() as s:
    rows = s.execute(select(DramaRankSnapshot).where(
        DramaRankSnapshot.source == "duanjubaike")).scalars().all()
    d15 = {r.drama_name: r for r in rows if str(r.snapshot_time.date()) == "2026-10-15"}
    d16 = {r.drama_name: r for r in rows if str(r.snapshot_time.date()) == "2026-10-16"}
    print(f"djb UTC10-15(北京10-15早): {len(d15)} 条 linked {sum(1 for r in d15.values() if r.drama_id)}")
    print(f"djb UTC10-16(北京10-16早): {len(d16)} 条 linked {sum(1 for r in d16.values() if r.drama_id)}")

    # 两期榜单名册变动
    up = set(d16) - set(d15)
    down = set(d15) - set(d16)
    print(f"\n两期名册: 新上榜(+{len(up)}) {sorted(up)}")
    print(f"两期名册: 落榜(-{len(down)}) {sorted(down)}")

    # linked 稳态构成 (以 10-16 期为准)
    linked16 = {n: r for n, r in d16.items() if r.drama_id}
    in_b4 = {n: r for n, r in linked16.items() if n in B4}
    in_b3 = {n: r for n, r in linked16.items() if n in B3_NEW}
    other = {n: r for n, r in linked16.items() if n not in B4 and n not in B3_NEW}
    print(f"\n== linked {len(linked16)} 构成(10-16期) ==")
    print(f"批4 短剧: {len(in_b4)}")
    for n, r in sorted(in_b4.items(), key=lambda x: x[1].rank_no or 999):
        print(f"  #{r.rank_no} {n}")
    print(f"批3 新剧: {len(in_b3)}")
    for n, r in sorted(in_b3.items(), key=lambda x: x[1].rank_no or 999):
        print(f"  #{r.rank_no} {n}")
    print(f"池内旧剧: {len(other)}")
    for n, r in sorted(other.items(), key=lambda x: x[1].rank_no or 999):
        print(f"  #{r.rank_no} {n}")

    # 批3 8 部在 djb 的收录状态
    print(f"\n== 批3 新剧在 djb 收录状态 ==")
    for n in B3_NEW:
        r = d16.get(n)
        if r is None:
            print(f"  不在榜: {n}")
        else:
            print(f"  #{r.rank_no} {'已链' if r.drama_id else '悬空'}: {n}")
