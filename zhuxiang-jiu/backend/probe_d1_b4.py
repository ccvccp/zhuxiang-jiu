# -*- coding: utf-8 -*-
"""批4 23 链投喂对 djb 互链的完整贡献: 关键词对照 10-16 名册(已链/悬空/不在榜)"""
import io
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, ".")
from sqlalchemy import select
from db import get_session
from models import DramaRankSnapshot

B4_KW = ["偏要偷尝半口甜", "替白月光", "老婆出国旅游", "奔头儿", "妹妹博士毕业宴",
         "保姆真千金", "离婚当天", "全家都盼弟弟", "嫁进沈家", "给流浪汉",
         "天降俏弟媳", "年薪八百万", "盛律师", "准儿媳", "继承者游戏",
         "东北老太", "合租凶宅", "假千金", "开局百年内力", "半生托举",
         "落魄书生", "退休老爸", "一卦显圣"]

with get_session() as s:
    rows = s.execute(select(DramaRankSnapshot).where(
        DramaRankSnapshot.source == "duanjubaike",
    )).scalars().all()
    d16 = {r.drama_name: r for r in rows if str(r.snapshot_time.date()) == "2026-10-16"}
    n_hit = n_dangle = n_off = 0
    print("== 批4 23 链在 djb 10-16 名册状态 ==")
    for kw in B4_KW:
        m = [n for n in d16 if kw in n]
        if not m:
            n_off += 1
            print(f"  不在榜: {kw}")
            continue
        for n in m:
            r = d16[n]
            if r.drama_id:
                n_hit += 1
                print(f"  #{r.rank_no} 已链: {n}")
            else:
                n_dangle += 1
                print(f"  #{r.rank_no} 悬空: {n}")
    print(f"\n已链 {n_hit} / 悬空 {n_dangle} / 不在榜关键词 {n_off}")
