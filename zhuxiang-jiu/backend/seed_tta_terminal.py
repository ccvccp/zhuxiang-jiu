# -*- coding: utf-8 -*-
"""toutiaoapi(悟空搜索 SERP) 评估结论留痕 crawl_job
形态: 搜索聚合结果页(1.26MB 壳, 结果卡片为资讯/短视频混合流);
robots.txt 404 不存在(按允许) —— 非 robots 终局, 是价值判定不接入:
无结构化剧集数据(无集数/播放/itemId/分享链), 与既有源零增量。"""
import io
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, ".")

from sqlalchemy import select

from db import get_session
from models import CrawlJob

URL = ("https://search5-search-hl.toutiaoapi.com/search"
       "?keyword=好雨知时节短剧&aid=777821(2026-10-15批2投喂1条)")
ERR = ("评估后不接入: 悟空搜索 SERP(标题『好雨知时节 短剧-悟空搜索』), "
       "robots.txt 404 不存在按允许; 页面 1.26MB 前端壳, 关键词出现 181 次但均为"
       "搜索结果卡片(资讯/短视频混合流), 无 _SSR/INITIAL_STATE 结构化 JSON、"
       "无集数/播放/item_id/分享链字段——非剧集数据页, 解析成本高且与 hongguo "
       "分享页源零增量。若未来需要搜索引擎发现通道, 走正规 web search API 而非 "
       "SERP 壳页。")

with get_session() as s:
    if s.execute(select(CrawlJob.id).where(CrawlJob.source == "toutiaoapi_serp",
                                           CrawlJob.url == URL)).first():
        print("already exists, skip")
    else:
        s.add(CrawlJob(source="toutiaoapi_serp", channel=2, url=URL,
                       method="manual", cron_expr=None,
                       status="skipped", error_msg=ERR))
        print("inserted toutiaoapi_serp record")
    row = s.execute(select(CrawlJob.source, CrawlJob.status)
                    .where(CrawlJob.source == "toutiaoapi_serp")).first()
    print("verify:", row)
