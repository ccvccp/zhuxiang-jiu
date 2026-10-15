# -*- coding: utf-8 -*-
"""三链 crawl_job 登记(修正版): 先清错位行, 统一 7 字段重插"""
import io
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, ".")

from sqlalchemy import select

from db import get_session
from models import CrawlJob

# (source, channel, url, method, cron, status, error_msg_or_desc)
JOBS = [
    ("duanjubaike", 2, "https://www.duanjubaike.net/paihang/rebo.html",
     "html", "20 1 * * *", "success",
     "短剧百科聚合热播榜: 番茄/蛋花/西瓜/悟空/常读/红果/河马/繁花/点众等平台综合"
     "人气热度前100名每日更新; robots * Allow:/; SSR 卡片; ★info-{sid} 与红果 "
     "video_series_id 同键(2026-10-15 实证)"),
    ("rebang_baidu", 2, "https://www.rebang.vip/baidu/drama",
     "html", "25 1 * * *", "success",
     "今日热榜·百度热门短剧榜聚合: 60条 SSR(rank/title/热度/题材/集数/主演); "
     "robots * Allow:/"),
    ("douyin_search", 2,
     "https://www.douyin.com/search/短剧热度实时榜(2026-10-15投喂1链)",
     "manual", None, "skipped",
     "评估不接入: 抖音搜索 SERP——robots 无 User-agent:* 段(仅搜索引擎爬虫各段, "
     "自建 worker 无适用规则形式上放行), 但页面 72KB JS 壳: render_data/_SSR/"
     "INITIAL_STATE 全 0, 数据全部客户端渲染且有风控/登录墙——技术上不可采且形态"
     "不符(搜索聚合页非剧集数据页, 同 toutiaoapi SERP 类)。抖音数据由 DataEye "
     "抖音端榜+悟空池 v.douyin 分享链覆盖。"),
]

with get_session() as s:
    # 清理上一版错位行(status NULL)
    n = s.query(CrawlJob).filter(CrawlJob.source.in_(
        ["duanjubaike", "rebang_baidu", "douyin_search"])).delete()
    print("cleaned:", n)
    for src, ch, url, method, cron, status, err in JOBS:
        s.add(CrawlJob(source=src, channel=ch, url=url, method=method,
                       cron_expr=cron, status=status, error_msg=err))
        print(f"{src}: inserted ({status})")

with get_session() as s:
    rows = s.execute(select(CrawlJob.source, CrawlJob.status, CrawlJob.cron_expr)
                     .order_by(CrawlJob.id)).all()
    print("\ncrawl_job rows:", len(rows))
    for r in rows:
        print("  -", r[0], "|", r[1], "|", r[2])
