# -*- coding: utf-8 -*-
"""架构蓝图论断 × 85号池内实测交叉验证:
①集数分布(60-100集规格占比) ②题材词频(高潜赛道对照) ③漫剧/AI占比 ④源分布"""
import io
import sys
from collections import Counter

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, ".")
from sqlalchemy import func, select
from db import get_session
from models import Drama, DramaRankSnapshot

with get_session() as s:
    dramas = s.execute(select(Drama)).scalars().all()
    n = len(dramas)
    print(f"主表 {n} 部")

    # ① 集数分布(蓝图规格 60-100 集)
    eps = [d.episodes for d in dramas if d.episodes]
    in_range = sum(1 for e in eps if 60 <= e <= 100)
    below = sum(1 for e in eps if e < 60)
    above = sum(1 for e in eps if e > 100)
    print(f"\n== ① 集数分布(有 ep 数据 {len(eps)} 部)")
    print(f"  60-100 集(蓝图标准规格): {in_range} ({in_range/len(eps)*100:.0f}%)")
    print(f"  <60 集: {below} ({below/len(eps)*100:.0f}%)")
    print(f"  >100 集: {above} ({above/len(eps)*100:.0f}%)")
    buckets = Counter()
    for e in eps:
        b = "<40" if e < 40 else "40-59" if e < 60 else "60-79" if e < 80 else \
            "80-99" if e < 100 else "100-149" if e < 150 else "150-299" if e < 300 else "300+"
        buckets[b] += 1
    print(f"  分桶: {dict(sorted(buckets.items(), key=lambda x: ['<40','40-59','60-79','80-99','100-149','150-299','300+'].index(x[0])))}")

    # ② 题材词频(genre_tags 展平)
    cnt = Counter()
    for d in dramas:
        for g in (d.genre_tags or []):
            cnt[g] += 1
    print(f"\n== ② 题材词频(genre_tags 非空 {sum(1 for d in dramas if d.genre_tags)} 部)")
    for g, c in cnt.most_common(25):
        print(f"  {g}: {c}")

    # 蓝图高潜赛道关键词命中
    KEY = {"末日废土/末世": ["末世", "废土", "求生"],
           "规则怪谈/无限流": ["规则", "怪谈", "无限流"],
           "玄幻仙侠": ["玄幻", "仙侠", "修仙", "修真", "异界"],
           "虐恋/追妻/火葬场": ["虐恋", "追妻", "火葬场", "虐"],
           "年代": ["年代", "八零", "七零", "1988"],
           "豪门/战神": ["豪门", "战神"],
           "系统/重生/逆袭": ["系统", "重生", "逆袭"]}
    print("  -- 蓝图高潜赛道命中(标签口径, 一部可多标):")
    for label, kws in KEY.items():
        c = sum(v for g, v in cnt.items() if any(k in g for k in kws))
        print(f"  {label}: {c} 部次")

    # ③ AI/漫剧占比
    n_ai = sum(1 for d in dramas if d.is_ai_drama)
    snaps = s.execute(select(DramaRankSnapshot.source,
                             func.count()).group_by(DramaRankSnapshot.source)).all()
    ai_names = {(sn.drama_name, ) for sn in s.execute(
        select(DramaRankSnapshot.drama_name).where(
            DramaRankSnapshot.source == "sdc_ai")).all()}
    print(f"\n== ③ AI 占比: is_ai_drama {n_ai}/{n} ({n_ai/n*100:.1f}%) "
          f"(sdc_ai 观测 {len(ai_names)} 部)")

    # ④ 源分布
    print(f"\n== ④ 快照源分布")
    for src, c in sorted(snaps, key=lambda x: -x[1]):
        print(f"  {src}: {c}")
