# -*- coding: utf-8 -*-
"""三链投喂收尾: 登记两新源 crawl_job(每日) + douyin 搜索页评估留痕"""
import io
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, ".")

from sqlalchemy import select

from db import get_session
from models import CrawlJob

JOBS = [
    ("duanjubaike", 2, "https://www.duanjubaike.net/paihang/rebo.html",
     "html", "20 1 * * *",
     None, "pending",
     "短剧百科聚合热播榜: 番茄/蛋花/西瓜/悟空/常读/红果/河马/繁花/点众等平台"
     "综合人气热度前100名, 每日更新; robots * Allow:/; SSR 卡片结构; "
     "★info-{sid} 与红果 video_series_id 同键(2026-10-15 实证)"),
    ("rebang_baidu", 2, "https://www.rebang.vip/baidu/drama",
     "html", "25 1 * * *",
     None, "pending",
     "今日热榜·百度热门短剧榜聚合: 60条 SSR(rank/title/热度/题材/集数/主演); "
     "robots * Allow:/"),
    ("douyin_search", 2,
     "https://www.douyin.com/search/短剧热度实时榜(2026-10-15投喂1链)",
     "manual", None,
     "skipped",
     "评估不接入: 抖音搜索 SERP——robots 无 User-agent:* 段(仅搜索引擎爬虫各段, "
     "自建 worker 无适用规则形式上放行), 但页面 72KB JS 壳: render_data/_SSR/"
     "INITIAL_STATE 全 0, 数据全部客户端渲染且有风控/登录墙——技术上不可采且"
     "形态不符(搜索聚合页非剧集数据页, 同 toutiaoapi SERP 类)。抖音数据由"
     "DataEye 抖音端榜+悟空池 v.douyin 分享链覆盖。"),
]

with get_session() as s:
    for src, ch, url, method, cron, status, err, _desc in JOBS:
        exists = s.execute(select(CrawlJob.id).where(
            CrawlJob.source == src, CrawlJob.url == url)).first()
        if exists:
            print(f"{src}: exists skip")
            continue
        s.add(CrawlJob(source=src, channel=ch, url=url, method=method,
                       cron_expr=cron, status=status, error_msg=err))
        print(f"{src}: inserted ({status})")

with get_session() as s:
    rows = s.execute(select(CrawlJob.source, CrawlJob.status, CrawlJob.cron_expr)
                     .order_by(CrawlJob.id)).all()
    print("\ncrawl_job rows:", len(rows))
    for r in rows:
        print("  -", r[0], "|", r[1], "|", r[2])
