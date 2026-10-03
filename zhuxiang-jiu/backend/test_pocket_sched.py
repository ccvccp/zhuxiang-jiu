"""顺手赚钱·巡检调度器与口径修正专项测试(2026-10-04 检查升级)

运行: python test_pocket_sched.py
"""

import asyncio
import os
import sys

os.environ["LOCK_MODE"] = "asyncio"
os.environ["STORE_MODE"] = "asyncio"
os.environ.pop("POCKET_SCAN_AUTO", None)
os.environ.pop("POCKET_SCAN_INTERVAL_SECONDS", None)

PASS = 0
FAIL = 0
RESULTS = []


def record(name, passed, detail=""):
    global PASS, FAIL
    if passed:
        PASS += 1
        RESULTS.append(f"  ✓ {name}")
    else:
        FAIL += 1
        RESULTS.append(f"  ✗ {name} — {detail}")


async def main():
    from repositories.store import reset_store, _mock_store
    from services import pocket_scheduler as sched

    print("=" * 60)
    print("顺手赚钱·巡检调度器测试")
    print("=" * 60)
    print()

    reset_store()

    # ---------- 开关/周期/生命周期 ----------
    record("test_01_default_off", sched.scheduler_enabled() is False)
    os.environ["POCKET_SCAN_AUTO"] = "on"
    record("test_02_env_on", sched.scheduler_enabled() is True)
    record("test_03_default_interval_86400",
           sched.interval_seconds() == 86400)
    os.environ["POCKET_SCAN_INTERVAL_SECONDS"] = "1"
    record("test_04_floor_300", sched.interval_seconds() == 300)
    os.environ.pop("POCKET_SCAN_INTERVAL_SECONDS", None)
    os.environ.pop("POCKET_SCAN_AUTO", None)
    record("test_05_off_start_false", sched.start_scheduler() is False)
    os.environ["POCKET_SCAN_AUTO"] = "on"
    ok = sched.start_scheduler() and sched._task \
        and not sched._task.done()
    record("test_06_start_runs", ok)
    first = sched._task
    sched.start_scheduler()
    record("test_07_idempotent", sched._task is first)
    sched.stop_scheduler()
    record("test_08_stop", sched._task is None)
    os.environ.pop("POCKET_SCAN_AUTO", None)

    # ---------- 真实数据巡检 ----------
    # 构造: 会员 + 三点位(poster 到期未领 / sticker 活跃 /
    # poster 沉默) + 打卡记录
    from datetime import datetime, timedelta, UTC
    _mock_store["pocket_sites"] = _mock_store.get("pocket_sites", {})
    now = datetime.now(UTC)
    _old = (now - timedelta(days=35)).isoformat()
    _sil = (now - timedelta(days=12)).isoformat()
    _mock_store["pocket_sites"][990] = {
        "siteId": 990, "memberId": 1, "scene": "hotel",
        "status": "active", "monthRewardClaimed": False,
        "createdAt": _old, "lastCheckinAt": _sil,
    }
    _mock_store["pocket_sites"][991] = {
        "siteId": 991, "memberId": 1, "scene": "taxi_rear",
        "status": "active", "monthRewardClaimed": False,
        "createdAt": (now - timedelta(days=2)).isoformat(),
        "lastCheckinAt": (now - timedelta(days=1)).isoformat(),
    }
    _mock_store["pocket_sites"][992] = {
        "siteId": 992, "memberId": 2, "scene": "community",
        "status": "active", "monthRewardClaimed": True,
        "createdAt": _old, "lastCheckinAt": _sil,
    }
    _mock_store["pocket_checkins"] = _mock_store.get(
        "pocket_checkins", {})
    _mock_store["pocket_checkins"][1] = {
        "checkinId": 1, "memberId": 1, "siteId": 990,
        "rewardAmount": 2.0,
    }

    report = await sched.run_scan()
    record("test_09_scan_runs", report.get("siteTotal") == 3,
           f"report={report}")
    record("test_10_status_dist_active",
           report["siteStatusDist"]["active"] == 3)
    # 990(hotel poster 35 天未领) 应入到期未领, 金额 20(非 30)
    ready = report.get("readyUnclaimed") or []
    record("test_11_ready_unclaimed_990",
           any(r.get("siteId") == 990 for r in ready))
    record("test_12_ready_amount_poster_price",
           report.get("readyUnclaimedAmount") == 20.0,
           f"amount={report.get('readyUnclaimedAmount')}")
    # 990/992 沉默(≥7 天未打卡), 991 活跃不沉默
    silent_ids = {s.get("siteId")
                  for s in report.get("silentSites") or []}
    record("test_13_silent_detection",
           silent_ids == {990, 992}, f"silent={silent_ids}")
    record("test_14_checkin_reward_total",
           report.get("checkinRewardTotal") == 2.0)
    # 992 已领存续奖(poster 20)
    record("test_15_month_claimed_total",
           report.get("monthRewardClaimedTotal") == 20.0)
    record("test_16_last_scan_cached",
           sched.last_scan().get("siteTotal") == 3)

    # ---------- my_stats 口径修正 ----------
    from services.pocket_service import PocketService
    stats = await PocketService().my_stats(1)
    # 会员1: 990(hotel poster 到期未领 20) + 991(taxi 未到期)
    # 修正前: 1×30=30(统一车贴价); 修正后: 20(poster 实价)
    record("test_17_my_stats_ready_amount",
           stats.get("monthRewardReadyCount") == 1
           and stats.get("monthRewardReadyAmount") == 20.0,
           f"stats={stats}")

    # 汇总
    print()
    print("-" * 60)
    for line in RESULTS:
        print(line)
    print("-" * 60)
    print(f"通过 {PASS} 项, 失败 {FAIL} 项")
    if FAIL:
        sys.exit(1)
    print("ALL PASS")


if __name__ == "__main__":
    asyncio.run(main())
