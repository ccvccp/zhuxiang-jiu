# -*- coding: utf-8 -*-
"""疑点核查: monthly_hongguo 双 no=1(二嫁有喜/莞东风云) 的原文上下文"""
import io
import json
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, ".")

from db import get_session
from models import DramaRankSnapshot

with get_session() as s:
    rows = (s.query(DramaRankSnapshot)
            .filter(DramaRankSnapshot.source == "dataeye")
            .all())
    for sn in rows:
        if sn.drama_name in ("二嫁有喜", "莞东风云，从野草到商业女",
                             "意外中奖，傻弟竟成姐姐靠山",
                             "五十岁爷们闪婚了", "那个男人从仙界归来了"):
            rj = sn.raw_json or {}
            print(f"== [{sn.rank_type}] {sn.drama_name} no={sn.rank_no}")
            print("   raw keys:", list(rj.keys()))
            print("   raw:", json.dumps(rj, ensure_ascii=False)[:400])
