# -*- coding: utf-8 -*-
"""零成本复核后的数据更正(2026-10-15):
1. 删 2 条 Day4 旧版解析器坏快照:
   - 东北话事人 monthly_hongguo no=10/heat=6715wan
     (原文实证: 红果百强榜第二, 日均热度 5968.8万; 6715万是峰值热度)
   - 二嫁有喜 monthly_hongguo no=1 (「登顶红果真人剧榜」分榜语境, 新规则 rank=None)
2. fetch_text 重采 + persist(新版解析器) 重插
3. matcher 补链(新行 drama_id 为 NULL, L1 按 norm 名自动补)
"""
import io
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, ".")

from db import get_session
from drama85_media_crawler import fetch_text, persist
from models import DramaRankSnapshot

URL = "https://m.thepaper.cn/newsDetail_forward_34234356"
BAD = [
    ("dataeye", "monthly_hongguo", "东北话事人", 10),
    ("dataeye", "monthly_hongguo", "二嫁有喜", 1),
]

with get_session() as s:
    for src, rt, name, no in BAD:
        n = (s.query(DramaRankSnapshot)
             .filter(DramaRankSnapshot.source == src,
                     DramaRankSnapshot.rank_type == rt,
                     DramaRankSnapshot.drama_name == name,
                     DramaRankSnapshot.rank_no == no)
             .delete())
        print(f"delete {name} [{rt}] no={no}: rows={n}")

text = fetch_text(URL)
stats = persist("dataeye", URL, text)
print("persist:", stats)

# 验证
with get_session() as s:
    rows = (s.query(DramaRankSnapshot)
            .filter(DramaRankSnapshot.source == "dataeye",
                    DramaRankSnapshot.rank_type == "monthly_hongguo")
            .order_by(DramaRankSnapshot.rank_no.is_(None),
                      DramaRankSnapshot.rank_no)
            .all())
    print("\n== monthly_hongguo after fix ==")
    for sn in rows:
        print(f"  no={sn.rank_no} {sn.drama_name} heat={sn.heat_value}"
              f"{sn.heat_unit or ''} drama_id={sn.drama_id}")
