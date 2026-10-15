# -*- coding: utf-8 -*-
"""hongguoduanjuc(仿红果导流站) + xcysw0(星辰影视聚合导航) 留痕 crawl_job"""
import io
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, ".")

from sqlalchemy import select

from db import get_session
from models import CrawlJob

JOBS = [
    ("hongguoduanjuc", 2, "https://www.hongguoduanjuc.com.cn/(2026-10-15投喂)",
     "skipped",
     "评估不接入·仿冒站: 自称「红果短剧官网」但红果真域名为字节系 "
     "fanqienovel.com/novelquickapp.com——本站为商标仿冒导流站。证据: robots 404;"
     " 页面无 ICP 备案号、无字节/fanqienovel 关联标识、零外链域名; 结构为 73 张"
     " func-card「功能卡片」+ 26 处「立即观看/播放」导流入口而非数据榜单; footer "
     "「本站不存储任何短剧文件仅提供片源与集数索引」。不接入三重理由: ①自述榜单"
     "无数据口径可信度不可验证; ②商标仿冒+无备案=法律与安全风险; ③导流入口与"
     "盗版/诈骗站特征一致。红果数据一律走 novelquickapp.com 官方分享链(8511 已接)。"),
    ("xcysw0", 2, "https://www.xcysw0.com.cn/(2026-10-15投喂)",
     "skipped",
     "评估不接入·二手聚合导航: 「星辰影视网」聚合腾讯/爱奇艺/红果/小红书/快手等"
     "六平台热搜榜, footer 自称「榜单数据每 30 分钟同步·评分与简介均为编辑部原创」"
     "但同步来源不明。证据: robots 404; 无 ICP 备案号; plat-card/rank-no 结构含"
     " 12 部剧名但口径为二手转载。不接入理由: ①数据二手来源不可溯, 与已接入的一手"
     "源(dataeye/新榜/duanjubaike/rebang)零增量; ②无备案信誉背书; ③「立即观看」"
     "导流入口模式与盗版站特征一致。若需平台热搜聚合, rebang.vip(robots 明示+"
     "结构化 SSR)已覆盖同类需求。"),
]

with get_session() as s:
    for src, ch, url, status, err in JOBS:
        if s.execute(select(CrawlJob.id).where(
                CrawlJob.source == src, CrawlJob.url == url)).first():
            print(f"{src}: exists skip")
            continue
        s.add(CrawlJob(source=src, channel=ch, url=url, method="manual",
                       cron_expr=None, status=status, error_msg=err))
        print(f"{src}: inserted ({status})")

with get_session() as s:
    rows = s.execute(select(CrawlJob.source, CrawlJob.status)
                     .where(CrawlJob.source.in_(["hongguoduanjuc", "xcysw0"]))).all()
    print("verify:", rows)
