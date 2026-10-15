# -*- coding: utf-8 -*-
"""百度AI投喂 5 新链终局留痕 + djtj7 源回退(同族内容农场):
① 5 新链 crawl_job skipped ② djtj7 改终局 skipped ③ 删 djtj7 编造快照 8 条"""
import io
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, ".")
from sqlalchemy import delete, select
from db import get_session
from models import CrawlJob, DramaRankSnapshot

NEW_LINKS = [
    ("aayow", "https://aayow.cn/",
     "评估不接入·内容农场: robots 放行但无 ICP 备案; 假 UGC 评论(模板生成); "
     "《》剧名 3 个池内 0 命中(总裁的甜蜜陷阱等编造名); title SEO 堆叠"),
    ("bkdjtj", "https://www.bkdjtj.com.cn/",
     "评估不接入·内容农场: 无 ICP 备案; ticker 假快讯(编造剧名配编造数据"
     "「上线72小时播放破3.2亿」); 《》剧名 10 个池内 0 命中; 与 djtj7 系/aijee 交叉复用剧名"),
    ("aijee", "https://aijee.cn/",
     "评估不接入·内容农场: 无 ICP 备案; news-item 静态停在 2026-08-12(模板不更新); "
     "《》剧名 4 个池内 0 命中; 与 bkdjtj 共享「战神归来」"),
    ("dsdjtj", "https://www.dsdjtj.com.cn/",
     "评估不接入·内容农场: 无 ICP 备案; ticker 模板与 bkdjtj/djtj7cn 同族; "
     "《》剧名 5 个池内 0 命中; 锚点导流(#platform-rank)结构"),
    ("djtj7cn", "https://www.djtj7.com.cn/",
     "评估不接入·内容农场(djtj7.com 同族双域): 与已接入 djtj7.com 剧名重合 4/8"
     "(午夜十二点的电梯/穿书成炮灰后我火了/重生后我把仇人写进剧本/闪婚老公竟是首富); "
     "剧名池内 0 命中; 无 ICP 备案"),
]

with get_session() as s:
    # ① 5 新链留痕
    for src, url, note in NEW_LINKS:
        exists = s.execute(select(CrawlJob).where(
            CrawlJob.source == src)).scalar_one_or_none()
        if exists is None:
            s.add(CrawlJob(source=src, channel=2, url=url, method="html",
                           status="skipped", error_msg=note))
            print(f"[新增] {src}: skipped 留痕")
        else:
            exists.status = "skipped"
            exists.error_msg = note
            print(f"[更新] {src}: skipped")

    # ② djtj7 终局改判(已接入→不采回退)
    job = s.execute(select(CrawlJob).where(
        CrawlJob.source == "djtj7")).scalar_one()
    job.status = "skipped"
    job.cron_expr = None
    job.error_msg = (
        "终局不采·数据回退(2026-10-15 复判): 本批 djtj7.com.cn 双域对照证实同族内容农场"
        "——双域剧名重合 4/8 且全部池内 0 命中(编造剧名: 闪婚老公竟是首富/重生后我把仇人"
        "写进剧本等); .com 域现已 DNS 失效(getaddrinfo failed); 已入库 8 条快照同判删除; "
        "原「六平台混合热搜榜」口径不可信。调度器 execute 对 skipped 行自动跳过, 无需重启")
    print("[改判] djtj7: success → skipped(终局) + cron 摘除")

    # ③ 删 djtj7 编造快照
    names = [r for r in s.execute(select(DramaRankSnapshot.drama_name).where(
        DramaRankSnapshot.source == "djtj7")).scalars()]
    linked = s.execute(select(DramaRankSnapshot).where(
        DramaRankSnapshot.source == "djtj7",
        DramaRankSnapshot.drama_id.isnot(None))).scalars().all()
    print(f"[删除] djtj7 快照 {len(names)} 条(剧名: {names}); "
          f"互链污染检查: {len(linked)} 条带 drama_id")
    s.execute(delete(DramaRankSnapshot).where(DramaRankSnapshot.source == "djtj7"))
    s.commit()
    print("commit 完成")

# 复核
with get_session() as s:
    n_job = s.execute(select(CrawlJob)).scalars().all()
    n_djtj7 = s.execute(select(DramaRankSnapshot).where(
        DramaRankSnapshot.source == "djtj7")).scalars().all()
    from sqlalchemy import func
    n_snap = s.execute(select(func.count()).select_from(DramaRankSnapshot)).scalar()
    skipped = sum(1 for j in n_job if j.status == "skipped")
    print(f"复核: crawl_job {len(n_job)} 行(skipped {skipped}) / "
          f"djtj7 快照残留 {len(n_djtj7)} / 快照总数 {n_snap} (711-8=703 预期)")
