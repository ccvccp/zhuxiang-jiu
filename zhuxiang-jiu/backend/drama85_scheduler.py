# -*- coding: utf-8 -*-
"""85号·采集调度器(Day7, docs/短剧榜单采集持久化落地方案.md)

以 crawl_job 表为单一事实源: 按 cron_expr 注册 APScheduler 任务, 执行时回写
status / last_run_at / error_msg / next_run_at; 连续失败 ≥2 次打印告警。

任务映射:
  share_pool_8510 → persist_once()(内部同时拉 8510+8511 双端点, 一次覆盖两行
                    crawl_job——8511 行为伴随记录, 不单独注册防双跑)
  xinbang         → 文章页采集(周榜, URL 从 crawl_job 读取, 换期只需改表)
  dataeye/kuaishou→ 媒体图文榜(月/按期, URL 从 crawl_job 读取)
  maoyan(skipped 终局) / huitun(pending 阻塞) → 不注册

用法:
  python drama85_scheduler.py --list              # 查看调度表(不启动)
  python drama85_scheduler.py --trigger kuaishou  # 手动触发一次(验收: 任务可手动触发)
  python drama85_scheduler.py --daemon            # 守护运行(阻塞)
"""
from __future__ import annotations

import argparse
import io
import sys
import traceback
from datetime import datetime

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy import select

from db import get_session
from models import CrawlJob

FAIL_STREAK: dict[str, int] = {}          # source → 连续失败次数
ALARM_THRESHOLD = 2

# 结构化列表页源(SSR 表格/卡片, 需原始 HTML 解析); 叙事文章页(dataeye/kuaishou)用纯文本
HTML_BOARD_SOURCES = frozenset({
    "duanjubaike", "rebang_baidu", "djtj7", "djgc",
    "sdc", "sdc_global", "sdc_ai", "sdc_actors",
})
MEDIA_SOURCES = HTML_BOARD_SOURCES | {"dataeye", "kuaishou"}


# ---------- 任务执行体 ----------

def _run_share_pool() -> str:
    from drama85_persist_worker import persist_once
    st = persist_once()
    return "items=%d new=%d merged=%d snap_new=%d" % (
        st["items"], st["new"], st["merged"], st["snapshots"])


def _run_xinbang(url: str) -> str:
    from drama85_xinbang_crawler import fetch_article, persist
    page = fetch_article(url)
    st = persist(url, page)
    return "period=%s snap_new=%d" % (st["period"], st["snap_new"])


def _run_media(source: str, url: str) -> str:
    from drama85_media_crawler import fetch_html, fetch_text, persist
    # 结构化列表页(聚合榜)用原始 HTML; 叙事文章页用纯文本(与 CLI --once 约定一致)
    text = (fetch_html if source in HTML_BOARD_SOURCES else fetch_text)(url)
    st = persist(source, url, text)
    return "snap_new=%d linked=%d unlinked=%d" % (
        st["snap_new"], st["linked"], st["unlinked"])


def execute(source: str) -> None:
    """执行一个任务并回写 crawl_job; 连续失败告警。"""
    with get_session() as s:
        job = s.execute(select(CrawlJob).where(CrawlJob.source == source)).scalar_one_or_none()
        if job is None or job.status == "skipped" or "TBD" in job.url:
            print("[%s] skip (no job / terminal / TBD url)" % source)
            return
        url = job.url
    try:
        if source == "share_pool_8510":
            msg = _run_share_pool()
            _mark(source, "success", msg)
            _mark("share_pool_8511", "success", "伴随 8510 同轮执行")
        elif source == "xinbang":
            msg = _run_xinbang(url)
            _mark(source, "success", msg)
        elif source in MEDIA_SOURCES:
            msg = _run_media(source, url)
            _mark(source, "success", msg)
        else:
            _mark(source, "skipped", "无注册处理器")
            return
        FAIL_STREAK[source] = 0
        print("[%s] OK %s" % (source, msg))
    except Exception as e:
        FAIL_STREAK[source] = FAIL_STREAK.get(source, 0) + 1
        err = "%s: %s" % (type(e).__name__, str(e)[:200])
        _mark(source, "failed", err)
        print("[%s] FAIL(%d) %s" % (source, FAIL_STREAK[source], err))
        if FAIL_STREAK[source] >= ALARM_THRESHOLD:
            print("!!!! 告警: %s 连续失败 %d 次 !!!!" % (source, FAIL_STREAK[source]))


def _mark(source: str, status: str, msg: str) -> None:
    with get_session() as s:
        job = s.execute(select(CrawlJob).where(CrawlJob.source == source)).scalar_one_or_none()
        if job is None:
            return
        job.status = status
        job.last_run_at = datetime.now()
        job.error_msg = None if status == "success" else msg


# ---------- 调度表 ----------

def load_jobs(sched: BlockingScheduler | None) -> list[tuple]:
    """读 crawl_job 可调度行; sched 非空则注册。返回 (source, cron, next_run) 列表。"""
    rows = []
    with get_session() as s:
        jobs = s.execute(select(CrawlJob)).scalars().all()
    for job in jobs:
        if job.status == "skipped" or not job.cron_expr or "TBD" in job.url \
                or job.source == "share_pool_8511":      # 8511 伴随 8510 同轮, 不单注册
            continue
        if sched is not None:
            sched.add_job(execute, CronTrigger.from_crontab(job.cron_expr),
                          id=job.source, args=[job.source], name=job.source,
                          max_instances=1, coalesce=True, misfire_grace_time=3600)
        rows.append((job.source, job.cron_expr))
    return rows


def main() -> None:
    ap = argparse.ArgumentParser(description="85号 采集调度器(crawl_job 驱动)")
    ap.add_argument("--list", action="store_true", help="查看调度表")
    ap.add_argument("--trigger", help="手动触发一次指定 source")
    ap.add_argument("--daemon", action="store_true", help="守护运行")
    args = ap.parse_args()

    if args.list:
        for source, cron in load_jobs(None):
            print("  %-16s %s" % (source, cron))
        return
    if args.trigger:
        execute(args.trigger)
        return
    if args.daemon:
        sched = BlockingScheduler(timezone="Asia/Shanghai")
        rows = load_jobs(sched)
        now = datetime.now()
        nxt: dict[str, datetime] = {}
        for source, cron in rows:
            nft = sched.get_job(source).trigger.get_next_fire_time(None, now)
            nxt[source] = nft
            print("scheduled %-16s %-12s next=%s" % (
                source, cron, nft.strftime("%m-%d %H:%M") if nft else "?"))
        with get_session() as s:
            for job_src, _ in rows:
                j = s.execute(select(CrawlJob).where(CrawlJob.source == job_src)).scalar_one_or_none()
                if j is not None and nxt.get(job_src) is not None:
                    j.next_run_at = nxt[job_src]
        print("scheduler_daemon_up jobs=%d" % len(rows))
        try:
            sched.start()
        except (KeyboardInterrupt, SystemExit):
            print("scheduler_down")


if __name__ == "__main__":
    main()
