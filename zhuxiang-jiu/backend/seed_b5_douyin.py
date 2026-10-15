# -*- coding: utf-8 -*-
"""批5配套: 抖音16链 crawl_job 留痕(技术不可采) + 主表8000拉起提示"""
import io
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, ".")
from sqlalchemy import select
from db import get_session
from models import CrawlJob

DOUYIN = [
    ("OxIe-exUy9Q", "以爱之名(衍生短剧)"),
    ("kkQsaifQG-Y", "顶流情感女主播拯救小三爸爸(小辣李嘉琦)"),
    ("E-uYCwi9_es", "阳光灿烂时(李溪芮/王皓轩)"),
    ("AO8LURPv8Wg", "看不见的爱人(美妆博主穿书)"),
    ("y2WjTPGydbA", "夏芷晴人贩子遇铁路工人(无剧名文案)"),
    ("Ze-rt-NWJ1M", "三根葱男团(西安年)"),
    ("qyCabNP-ObE", "我隐藏身份，找到了新妈妈"),
    ("LeZLHCyoKp0", "督军府大小姐，这枪我不封了"),
    ("drg6tdINto0", "(无文案)"),
    ("A1zOJAdAaJg", "五十岁爷们闪婚了"),
    ("1PE6ZicnphA", "将军将我贬妻为妾，圣旨赐下后他慌了"),
    ("exH92r63U5A", "穷酸老爹是装穷"),
    ("Txnq4qG7nlE", "(无文案)"),
    ("OqmE8_9EvSo", "东北婆婆，笑护全家"),
    ("fYr8I7K20a8", "(无文案)"),
    ("HFJcy__Ai4o", "公公甩资让我离婚，前夫婚礼上我惊艳全场"),
]

with get_session() as s:
    for code, note in DOUYIN:
        url = f"https://v.douyin.com/{code}/"
        exist = s.execute(select(CrawlJob).where(
            CrawlJob.source == "douyin_video",
            CrawlJob.url == url)).scalars().first()
        if exist:
            print(f"已留痕: {code}")
            continue
        s.add(CrawlJob(
            source="douyin_video", channel=2, url=url, method="html",
            status="skipped",
            error_msg=(f"评估不接入: 抖音视频分享链——302→douyin.com/video 页 72KB 纯 JS 壳"
                       f"(无title/RENDER_DATA/_ROUTER_DATA/data-fn-args), 无SSR结构化数据, "
                       f"需JS执行+签名接口; 与 douyin_search SERP 同判定; 投喂文案剧名: {note} "
                       f"(2026-10-16批5投喂16链, 其中4部池内已有)"),
        ))
    s.commit()
    n = len(s.execute(select(CrawlJob).where(
        CrawlJob.source == "douyin_video")).scalars().all())
    print(f"douyin_video 留痕共 {n} 条")
