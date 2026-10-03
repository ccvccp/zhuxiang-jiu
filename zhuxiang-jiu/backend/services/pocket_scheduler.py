"""顺手赚钱模块·日度巡检调度器(pocket_scheduler, 2026-10-04 检查升级)

背景:
    全项目 40+ 调度器惯例下 pocket 为纯请求驱动例外——存续奖
    (满月 ¥20/30)依赖用户手动 claim 且"撤销未领视为放弃", 无到期
    扫描/无沉默点位观测/无发放对账; 生产 3 点位近 7 天零活动。

边界(资金红线):
    本轮为**纯观测面**——只统计落痕不改任何资金规则; 存续奖
    自动发放(改"手动领取"业务规则)列拍板项, 调度器不越权。

巡检内容(单轮):
    - 点位状态分布(active/invalid/removed)
    - 到期可领未领(monthRewardReady)与沉默点位(active 且
      连续 ≥7 天未打卡)
    - 累计发放对账(打卡奖总额 + 已领存续奖)
    - 留痕键 pocket:daily_scan:last(供管理端/日报消费)

范式: 全站调度器平移:
    - POCKET_SCAN_AUTO 默认 off; POCKET_SCAN_INTERVAL_SECONDS
      默认 86400s(下限 300s); 启动即首轮; fail-soft
"""

import asyncio
import logging
import os

logger = logging.getLogger("pocket_scheduler")

DEFAULT_INTERVAL = 86400
MIN_INTERVAL = 300

_task: asyncio.Task | None = None
_last_scan: dict = {}


def scheduler_enabled() -> bool:
    return os.environ.get(
        "POCKET_SCAN_AUTO", "off").strip().lower() == "on"


def interval_seconds() -> int:
    try:
        value = int(os.environ.get(
            "POCKET_SCAN_INTERVAL_SECONDS",
            str(DEFAULT_INTERVAL)))
        return max(MIN_INTERVAL, value)
    except ValueError:
        return DEFAULT_INTERVAL


async def run_scan() -> dict:
    """单轮巡检(观测面; 调度/手动共用)"""
    global _last_scan
    from datetime import datetime, timedelta, UTC
    from repositories.pocket_repository import PocketRepository
    repo = PocketRepository()
    settings = await repo.get_settings()
    duration_days = int(settings.get("durationDays", 30))
    poster_price = float(settings.get("monthRewardPoster", 20.0))
    sticker_price = float(settings.get("monthRewardSticker", 30.0))

    sites = await repo.list_sites(limit=5000)
    from repositories.pocket_repository import SCENES
    now = datetime.now(UTC)
    status_dist = {"active": 0, "invalid": 0, "removed": 0,
                   "other": 0}
    ready_unclaimed = []          # 到期可领未领(奖励沉默风险)
    silent_sites = []             # 沉默点位(active 且 ≥7 天未打卡)
    month_claimed_amount = 0.0
    for s in sites:
        st = str(s.get("status") or "other")
        status_dist[st if st in status_dist else "other"] += 1
        created = str(s.get("createdAt") or "")
        try:
            days = (now - datetime.fromisoformat(
                created)).days if created else 0
        except ValueError:
            days = 0
        price = (sticker_price
                 if SCENES.get(s.get("scene", "")) == "sticker"
                 else poster_price)
        if s.get("monthRewardClaimed"):
            month_claimed_amount += price
        elif st == "active" and days >= duration_days:
            ready_unclaimed.append({
                "siteId": s.get("siteId"),
                "memberId": s.get("memberId"),
                "days": days,
                "amount": price,
            })
        if st == "active":
            last = str(s.get("lastCheckinAt")
                       or s.get("updatedAt") or created)
            try:
                silent_days = (now - datetime.fromisoformat(
                    last)).days if last else 999
            except ValueError:
                silent_days = 999
            if silent_days >= 7:
                silent_sites.append({
                    "siteId": s.get("siteId"),
                    "memberId": s.get("memberId"),
                    "silentDays": silent_days,
                })
    checkins = await repo.list_checkins(limit=5000)
    checkin_reward_total = sum(
        float(c.get("rewardAmount", 0) or 0) for c in checkins)
    report = {
        "scannedAt": datetime.now(UTC).isoformat(),
        "siteTotal": len(sites),
        "siteStatusDist": status_dist,
        "readyUnclaimed": ready_unclaimed,
        "readyUnclaimedAmount": round(sum(
            r["amount"] for r in ready_unclaimed), 2),
        "silentSites": silent_sites,
        "checkinTotal": len(checkins),
        "checkinRewardTotal": round(checkin_reward_total, 2),
        "monthRewardClaimedTotal": round(month_claimed_amount, 2),
    }
    _last_scan = report
    # 留痕(供管理端/日报消费; 观测面键; 失败不影响巡检)
    try:
        import json
        from repositories.backend import get_redis_client, _k
        client = await get_redis_client()
        await client.set(_k("pocket", "daily_scan", "last"),
                         json.dumps(report, ensure_ascii=False))
    except Exception:
        pass
    logger.info(
        "pocket_scan sites=%s ready_unclaimed=%s(¥%s) silent=%s "
        "checkin_reward=¥%s month_claimed=¥%s",
        report["siteTotal"], len(ready_unclaimed),
        report["readyUnclaimedAmount"], len(silent_sites),
        report["checkinRewardTotal"],
        report["monthRewardClaimedTotal"])
    return report


def last_scan() -> dict:
    """最近一轮巡检结果(内存缓存; 空=未跑过)"""
    return _last_scan


async def _loop() -> None:
    interval = interval_seconds()
    logger.info("pocket_scan_scheduler started interval=%ss", interval)
    while True:
        try:
            await run_scan()
        except Exception as exc:  # noqa: BLE001
            logger.warning("顺手赚钱巡检异常(继续运行): %s", exc)
        await asyncio.sleep(interval)


def start_scheduler() -> bool:
    """启动巡检调度(幂等; POCKET_SCAN_AUTO=off 返回 False)"""
    global _task
    if not scheduler_enabled():
        logger.info("pocket_scan_scheduler disabled "
                    "(POCKET_SCAN_AUTO=off)")
        return False
    if _task is not None and not _task.done():
        return True
    try:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = asyncio.get_event_loop()
        _task = loop.create_task(_loop())
        return True
    except RuntimeError as exc:
        logger.warning("调度器启动失败(无事件循环): %s", exc)
        return False


def stop_scheduler() -> None:
    global _task
    if _task is not None:
        _task.cancel()
    _task = None
