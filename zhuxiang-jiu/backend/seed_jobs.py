# -*- coding: utf-8 -*-
"""85号·采集任务种子(Day1)

写入 crawl_job 初始记录(幂等: uk_job source+url 冲突即跳过)。
用法: python seed_jobs.py [--dry-run]
"""
from __future__ import annotations

import argparse
import sys

from sqlalchemy import select

from db import get_session, init_schema
from models import CrawlJob

# 通道②契约端点(8510/8511, 已在运行) + 三条待接文章页源 + 通道①灰豚(待充值)
SEED_JOBS = [
    # source, channel, url, method, cron
    ("share_pool_8510", 2, "http://127.0.0.1:8510/dramas", "contract", "30 0 * * *"),        # 每日 00:30
    ("share_pool_8511", 2, "http://127.0.0.1:8511/dramas", "contract", "35 0 * * *"),        # 每日 00:35
    ("xinbang",         2, "TBD:新榜抖快在播短剧周榜文章页URL(当期确定后回填)", "html", "0 9 * * 1"),  # 每周一 09:00
    ("dataeye",         2, "TBD:DataEye公开图文/月榜摘要页URL", "html", "0 10 1 * *"),       # 每月 1 日
    ("kuaishou",        2, "TBD:快手星芒公开片单/盘点文章URL", "html", "0 20 * * 0"),        # 每周日 20:00
]


def seed(dry_run: bool = False) -> int:
    inserted = 0
    with get_session() as s:
        for source, channel, url, method, cron in SEED_JOBS:
            exists = s.execute(
                select(CrawlJob.id).where(CrawlJob.source == source, CrawlJob.url == url)
            ).first()
            if exists:
                continue
            s.add(CrawlJob(source=source, channel=channel, url=url, method=method, cron_expr=cron))
            inserted += 1
            print("add job source=%s url=%s cron=%s" % (source, url[:60], cron))
    if dry_run:
        print("dry_run: %d rows NOT committed" % inserted)
    return inserted


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="85号 crawl_job 种子写入")
    ap.add_argument("--dry-run", action="store_true", help="只打印不提交")
    args = ap.parse_args()

    n = init_schema()  # 先保证库表在(幂等)
    print("schema stmts=%d" % n)
    if not args.dry_run:
        inserted = seed()
        print("seeded=%d" % inserted)
        # 验收查询
        with get_session() as s:
            rows = s.execute(select(CrawlJob.source, CrawlJob.url, CrawlJob.cron_expr)).all()
        print("crawl_job rows=%d" % len(rows))
        for r in rows:
            print("  - %s | %s | %s" % (r[0], r[1][:60], r[2]))
        assert len(rows) >= 5, "Day1 验收: crawl_job 至少 5 条"
        print("DAY1_ACCEPT_OK")
    else:
        seed(dry_run=True)
