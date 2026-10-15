# -*- coding: utf-8 -*-
"""三站接入 crawl_job 登记: djgc 风向标 / sdc 出海榜 / djtj7 混合榜(每日)"""
import io
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, ".")

from sqlalchemy import select

from db import get_session
from models import CrawlJob

JOBS = [
    ("djgc", 2, "https://www.duanjugongcheng.com/cn/fengxiangbiao",
     "20 2 * * *", "success",
     "短剧工程·短剧风向标: 100名 SSR 表格(排名/剧名/体裁/题材/标签/今日热播指数"
     "/趋势↑↓); 指数=抖音官方数据播放量/完播率/搜索量/互动量综合; robots * Allow:/"
     "(仅禁 api/signup/signin/profile); 封面走 fqnovelpic 字节系图床"),
    ("sdc", 2, "https://shortdramacast.com/zh-cn/rankings/chinese-short-dramas",
     "25 2 * * *", "success",
     "SceneTrace(shortdramacast) 出海视角中文短剧 Top100: SSR article(data-"
     "analytics-rank/name + aria-label 热度 0-100 相对分); SSR 可见 25 条; 页面"
     "周更(2026-10-10 期); robots * Allow:/ + 多 sitemap; 站内另有海外/AI 短剧"
     "Top100+演员榜可扩展"),
    ("djtj7", 2, "https://www.djtj7.com.cn/",
     "30 2 * * *", "success",
     "短剧推荐站首页六平台混合热搜榜: 8条(抖音/红果/腾讯/快手/爱奇艺/小红书 "
     "rank-no/li 结构, 平台·热度); robots 404 无 robots 按允许; 条目少仅作辅助源"),
]

with get_session() as s:
    for src, ch, url, cron, status, desc in JOBS:
        if s.execute(select(CrawlJob.id).where(
                CrawlJob.source == src, CrawlJob.url == url)).first():
            print(f"{src}: exists skip")
            continue
        s.add(CrawlJob(source=src, channel=ch, url=url, method="html",
                       cron_expr=cron, status=status, error_msg=desc))
        print(f"{src}: inserted")

with get_session() as s:
    n = s.query(CrawlJob).count()
    print("crawl_job rows:", n)
