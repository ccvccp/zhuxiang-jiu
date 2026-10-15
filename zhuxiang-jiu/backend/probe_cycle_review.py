# -*- coding: utf-8 -*-
"""10-16 凌晨首个多源自动采集周期复盘:
① crawl_job 执行状态 ② 各源快照增量(vs 711 基线) ③ contentType 落快照
④ matcher 互链/is_ai_drama 增量(vs 297/16 基线)"""
import io
import json
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, ".")
from sqlalchemy import func, select
from db import get_session
from models import CrawlJob, Drama, DramaRankSnapshot

with get_session() as s:
    # ① crawl_job 执行状态
    jobs = s.execute(select(CrawlJob).order_by(CrawlJob.source)).scalars().all()
    print(f"== ① crawl_job {len(jobs)} 行")
    for j in jobs:
        err = (j.error_msg or "")[:60]
        print(f"  {j.source:<16} cron={j.cron_expr or '-':<12} status={j.status:<8} "
              f"last={j.last_run_at} next={j.next_run_at} {err}")

    # ② 快照增量: 各源×快照日 分布
    print("\n== ② 快照分布(source × 日期, 行数)")
    rows = s.execute(select(DramaRankSnapshot.source,
                            func.date(DramaRankSnapshot.snapshot_time),
                            func.count())
                     .group_by(DramaRankSnapshot.source,
                               func.date(DramaRankSnapshot.snapshot_time))
                     .order_by(func.date(DramaRankSnapshot.snapshot_time),
                               DramaRankSnapshot.source)).all()
    total = 0
    for src, day, n in rows:
        total += n
        print(f"  {day} {src:<16} {n}")
    print(f"  快照总数: {total} (基线 711)")

    # ③ 当日快照 contentType 落库检查(share_pool 识别键=rank_type)
    print("\n== ③ 当日快照 contentType")
    import datetime as _dt
    today0 = _dt.datetime.combine(_dt.date.today(), _dt.time.min)
    snaps = s.execute(select(DramaRankSnapshot).where(
        DramaRankSnapshot.snapshot_time >= today0)).scalars().all()
    by_src: dict[str, list] = {}
    for sn in snaps:
        by_src.setdefault(f"{sn.source}/{sn.rank_type}", []).append(sn)
    for key in sorted(by_src):
        lst = by_src[key]
        n_ct = sum(1 for sn in lst if (sn.raw_json or {}).get("contentType"))
        cts: dict = {}
        for sn in lst:
            ct = (sn.raw_json or {}).get("contentType")
            cts[ct] = cts.get(ct, 0) + 1
        print(f"  {key:<28} {len(lst):>3} 行: 带 contentType {n_ct}, 分布 {cts}")
    if snaps:
        print(f"    样例 {snaps[0].drama_name}: "
              f"raw_json_keys={sorted((snaps[0].raw_json or {}).keys())}")

    # ④ 主表与互链
    print("\n== ④ 主表")
    n_drama = s.execute(select(func.count()).select_from(Drama)).scalar()
    n_ai = s.execute(select(func.count()).select_from(Drama)
                     .where(Drama.is_ai_drama.is_(True))).scalar()
    n_unlinked = s.execute(select(func.count()).select_from(DramaRankSnapshot)
                           .where(DramaRankSnapshot.drama_id.is_(None))).scalar()
    print(f"  主表 {n_drama} 部 (基线 297) / is_ai_drama {n_ai} 行 (基线 16) / "
          f"unlinked 快照 {n_unlinked}")
