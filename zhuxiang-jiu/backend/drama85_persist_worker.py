# -*- coding: utf-8 -*-
"""85号·落库层(Day2, docs/短剧榜单采集持久化落地方案.md 第三节)

拉取 8510/8511 契约端点(分页取全) → 内联清洗 → upsert 主表 + share_pool 日快照 → 更新 crawl_job。

数据流:
  GET /dramas?offset=&n=500 (循环 hasMore)
    → clean(剧名清洗/AI标记/异常行)
    → upsert drama(itemId 强键优先 → 剧名 exact 跨源合并 → 新建)
    → 快照(source=真实来源域, 日快照 uk_snap 幂等)
    → crawl_job(status/last_run_at/error_msg)

用法:
  python drama85_persist_worker.py --once           # 跑一轮落库
  python drama85_persist_worker.py --verify         # 只做验收查询
  python drama85_persist_worker.py --once --verify  # 落库后立即验收
"""
from __future__ import annotations

import argparse
import re
from datetime import date, datetime

import httpx
from sqlalchemy import func, select

from db import get_session
from models import CrawlJob, Drama, DramaRankSnapshot

ENDPOINTS = [
    # (job_source, url, 默认来源域——item 未带 sourcePlatform 时的兜底)
    ("share_pool_8510", "http://127.0.0.1:8510/dramas", "wukong"),
    ("share_pool_8511", "http://127.0.0.1:8511/dramas", "hongguo"),
]

PAGE_N = 500
TIMEOUT = 900          # 首采全量约 6-8 分钟(1 QPS 限速), 给足
AI_MARKERS = ("ai漫剧", "ai短剧", "ai生成", "ai动画", "漫剧")   # cleaner 依剧名/标签识别


# ---------- 拉取 ----------

def fetch_all(url: str) -> list[dict]:
    """契约分页取全(端点默认 n=50, 必须显式带 n 并循环 hasMore)。"""
    items, offset = [], 0
    with httpx.Client(timeout=TIMEOUT) as c:
        while True:
            r = c.get(url, params={"offset": offset, "n": PAGE_N})
            r.raise_for_status()
            d = r.json()
            page = d.get("dramas", [])
            items += page
            if not d.get("hasMore"):
                return items
            offset += len(page)


# ---------- 清洗 ----------

def clean(item: dict, fallback_source: str) -> tuple[dict | None, str]:
    """返回 (cleaned, skip_reason); cleaned=None 表示异常行跳过。"""
    title = re.sub(r"\s+", " ", str(item.get("title") or "")).strip()
    if not title:
        return None, "empty_title"
    dur = item.get("durationSec")
    if isinstance(dur, int) and dur < 0:
        dur = None                      # 负时长置空(不造数)
    tags = [str(t) for t in (item.get("tags") or []) if str(t).strip()]
    hay = (title + " " + " ".join(tags)).lower()
    is_ai = any(m in hay for m in AI_MARKERS)
    platforms = item.get("platforms") or []
    src = str(item.get("sourcePlatform") or "").strip() or fallback_source
    return {
        "itemId": str(item.get("itemId") or "").strip() or None,
        "title": title,
        "sourcePlatform": src,
        "platforms": [str(p) for p in platforms],
        "episodes": item.get("episodes"),
        "durationSec": dur,
        "tags": tags,
        "isAi": is_ai,
        "subtitle": str(item.get("subtitleText") or "")[:500],
        "metrics": item.get("metrics") or {},
    }, ""


# ---------- upsert + 快照 ----------

def _resolve_drama(s, c: dict) -> tuple[Drama, bool]:
    """归一三级: ①itemId 强键 ②剧名 exact 跨源合并 ③新建。返回 (row, is_new)。"""
    row = None
    if c["itemId"]:
        row = s.execute(select(Drama).where(Drama.item_id == c["itemId"])).scalar_one_or_none()
    if row is None:
        row = s.execute(select(Drama).where(Drama.drama_name == c["title"])).scalar_one_or_none()
    if row is not None:
        # 合并: platforms 并集; 空字段补值; last_seen 刷新(main_platform 不动)
        merged = [p for p in (row.platforms or "").split(",") if p]
        for p in c["platforms"]:
            if p not in merged:
                merged.append(p)
        row.platforms = ",".join(merged) or None
        if row.episodes is None and c["episodes"] is not None:
            row.episodes = c["episodes"]
        if row.duration_sec is None and c["durationSec"] is not None:
            row.duration_sec = c["durationSec"]
        if not row.subtitle_text and c["subtitle"]:
            row.subtitle_text = c["subtitle"]
        if c["isAi"]:
            row.is_ai_drama = True
        row.last_seen_at = datetime.now()
        return row, False
    now = datetime.now()
    row = Drama(
        item_id=c["itemId"],
        drama_name=c["title"],
        main_platform=c["sourcePlatform"],
        platforms=",".join(c["platforms"]) or None,
        episodes=c["episodes"],
        duration_sec=c["durationSec"],
        genre_tags=c["tags"] or None,
        is_ai_drama=c["isAi"],
        subtitle_text=c["subtitle"] or None,
        first_seen_at=now,
        last_seen_at=now,
        status="active",
    )
    s.add(row)
    s.flush()          # 立即拿 drama_id + 让生成列 dedup_key 生效(批内后续可命中)
    return row, True


def persist_once() -> dict:
    """跑一轮: 双端点拉取 → 清洗 → 主表 upsert → 日快照 → crawl_job 状态。"""
    stat = {"items": 0, "skipped": 0, "new": 0, "merged": 0,
            "snapshots": 0, "snap_dup": 0, "sources": {}}
    today = date.today()
    snap_time = datetime(today.year, today.month, today.day)   # 日快照键(uk_snap 幂等)

    for job_source, url, fallback in ENDPOINTS:
        try:
            items = fetch_all(url)
        except Exception as e:
            _mark_job(job_source, url, "failed", str(e)[:500])
            stat["sources"][job_source] = "failed:%s" % type(e).__name__
            continue
        with get_session() as s:
            for raw in items:
                stat["items"] += 1
                c, why = clean(raw, fallback)
                if c is None:
                    stat["skipped"] += 1
                    continue
                row, is_new = _resolve_drama(s, c)
                stat["new" if is_new else "merged"] += 1
                # 日快照(source=真实来源域; metrics 区间值原样入 raw_json)
                exists = s.execute(
                    select(DramaRankSnapshot.id).where(
                        DramaRankSnapshot.source == c["sourcePlatform"],
                        DramaRankSnapshot.rank_type == "share_pool",
                        DramaRankSnapshot.drama_name == c["title"],
                        DramaRankSnapshot.snapshot_time == snap_time,
                    )
                ).first()
                if exists:
                    stat["snap_dup"] += 1
                    continue
                s.add(DramaRankSnapshot(
                    source=c["sourcePlatform"],
                    rank_type="share_pool",
                    drama_id=row.drama_id,
                    drama_name=c["title"],
                    period_start=today,
                    period_end=today,
                    snapshot_time=snap_time,
                    raw_url=url,
                    raw_json={"contract": raw},
                ))
                stat["snapshots"] += 1
        note = "items=%d skip=%d" % (len(items), stat["skipped"])
        _mark_job(job_source, url, "success", note)
        stat["sources"][job_source] = "items=%d" % len(items)
    return stat


def _mark_job(source: str, url: str, status: str, msg: str) -> None:
    with get_session() as s:
        job = s.execute(select(CrawlJob).where(
            CrawlJob.source == source, CrawlJob.url == url)).scalar_one_or_none()
        if job is None:
            return
        job.status = status
        job.last_run_at = datetime.now()
        job.error_msg = None if status == "success" else msg


# ---------- 验收 ----------

def verify() -> bool:
    ok = True
    today = date.today()
    with get_session() as s:
        total = s.execute(select(func.count()).select_from(Drama)).scalar()
        by_plat = dict(s.execute(
            select(Drama.main_platform, func.count()).group_by(Drama.main_platform)).all())
        dups = s.execute(
            select(Drama.dedup_key, func.count()).group_by(Drama.dedup_key)
            .having(func.count() > 1)).all()
        snaps = s.execute(select(
            DramaRankSnapshot.source, func.count()).where(
            DramaRankSnapshot.rank_type == "share_pool",
            DramaRankSnapshot.snapshot_time >= datetime(today.year, today.month, today.day),
        ).group_by(DramaRankSnapshot.source)).all()
        ai_cnt = s.execute(select(func.count()).select_from(Drama)
                           .where(Drama.is_ai_drama.is_(True))).scalar()
        no_id = s.execute(select(func.count()).select_from(Drama)
                          .where(Drama.item_id.is_(None))).scalar()

    print("drama_total=%d (验收≥200: %s)" % (total, total >= 200)); ok &= total >= 200
    print("by_platform=%s" % by_plat)
    print("dup_dedup_keys=%d (验收=0: %s)" % (len(dups), not dups)); ok &= not dups
    print("snapshots_today=%s" % dict(snaps))
    print("ai_drama=%d  no_item_id=%d(剧名兜底行)" % (ai_cnt, no_id))
    print("DAY2_ACCEPT_%s" % ("OK" if ok else "FAIL"))
    return ok


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="85号 落库层(8510/8511 → drama85 库)")
    ap.add_argument("--once", action="store_true", help="跑一轮落库")
    ap.add_argument("--verify", action="store_true", help="验收查询")
    args = ap.parse_args()

    if not (args.once or args.verify):
        ap.print_help()
        raise SystemExit(2)
    if args.once:
        st = persist_once()
        print("persist: items=%d new=%d merged=%d skip=%d snap_new=%d snap_dup=%d" % (
            st["items"], st["new"], st["merged"], st["skipped"],
            st["snapshots"], st["snap_dup"]))
        print("sources:", st["sources"])
    if args.verify:
        raise SystemExit(0 if verify() else 1)
