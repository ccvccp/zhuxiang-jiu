# -*- coding: utf-8 -*-
"""sdc 三扩展榜 crawl_job 登记(每日)"""
import io
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, ".")

from sqlalchemy import select

from db import get_session
from models import CrawlJob

JOBS = [
    ("sdc_global", 2, "https://shortdramacast.com/zh-cn/rankings/global-short-dramas",
     "35 2 * * *",
     "SceneTrace 海外短剧 Top100: SSR 可见 25(2026-10-10 期 98 部); 海外分发译制剧"
     "与国内池零重叠(分发集不同), 独立海外维度观测"),
    ("sdc_ai", 2, "https://shortdramacast.com/zh-cn/rankings/ai-short-dramas",
     "40 2 * * *",
     "SceneTrace AI 短剧 Top100: SSR 可见 25; ★与池内高度关联(linked=8)——榜首"
     "《聚宝仙盆之杂灵根才是真BOSS第十三季》《万妖图录传第十三季》均为池内红果"
     "漫剧续季; is_ai_drama 判别模型增量源"),
    ("sdc_actors", 2, "https://shortdramacast.com/zh-cn/rankings/hot-short-drama-actors",
     "45 2 * * *",
     "SceneTrace 短剧演员热度榜: person 维度(185 卡 SSR 可见 25), rank_type=actor, "
     "drama_name 列存演员名留档(如 刘润铭/Rosa Singerman——海外演员含英文); "
     "matcher 按剧名匹配天然不误链; 制作方/演员情报维度"),
]

with get_session() as s:
    for src, ch, url, cron, desc in JOBS:
        if s.execute(select(CrawlJob.id).where(
                CrawlJob.source == src, CrawlJob.url == url)).first():
            print(f"{src}: exists skip")
            continue
        s.add(CrawlJob(source=src, channel=ch, url=url, method="html",
                       cron_expr=cron, status="success", error_msg=desc))
        print(f"{src}: inserted")

with get_session() as s:
    print("crawl_job rows:", s.query(CrawlJob).count())
