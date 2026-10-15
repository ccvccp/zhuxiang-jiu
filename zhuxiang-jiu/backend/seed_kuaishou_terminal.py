# -*- coding: utf-8 -*-
"""快手 v.kuaishou.com 分享链 7 条终局留痕(crawl_job)
robots 终局: 302 落地域 v.m.chenzhongtech.com robots.txt
`User-agent: * | Disallow: /` —— 全站禁采一票否决(猫眼同款)。
幂等: uk_job(source,url) 冲突即跳过。"""
import io
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, ".")

from sqlalchemy import select

from db import get_session
from models import CrawlJob

URL = ("https://v.kuaishou.com/KG1bc4JF,+KnViGyzY,+K0ArVE0H,+nMyXXPRw,"
       "+KtnUvitJ,+KTpUyN4c,+J9jot8ex(2026-10-15投喂7链)")
ERR = ("终局不可采: 分享链 302 → 落地域 v.m.chenzhongtech.com, 其 robots.txt "
       "User-agent:* 段 Disallow:/ 全站禁采 —— 合规红线一票否决(与猫眼同款终局)。"
       "页面虽为 180KB SSR(caption/剧名在页), 但落地域禁止抓取, 永不启用。"
       "剧名清单: 女儿要自由我彻底放手她却后悔/活捉间谍一枚能上重点大学吗/逆风成长/"
       "重生八三携全家闯出发财路/除尽恶霸还乡荡乾坤/被开除后我接管了全球公司/"
       "青榕小剧场(未名剧)。快手数据已由 DataEye 快手侧+星芒榜单源覆盖。")

with get_session() as s:
    exists = s.execute(select(CrawlJob.id).where(
        CrawlJob.source == "kuaishou_share",
        CrawlJob.url == URL)).first()
    if exists:
        print("already exists, skip")
    else:
        s.add(CrawlJob(source="kuaishou_share", channel=2, url=URL,
                       method="manual", cron_expr=None,
                       status="skipped", error_msg=ERR))
        print("inserted kuaishou_share terminal record")

with get_session() as s:
    row = s.execute(select(CrawlJob.source, CrawlJob.status, CrawlJob.url)
                    .where(CrawlJob.source == "kuaishou_share")).first()
    print("verify:", row[0], row[1], row[2][:50])
