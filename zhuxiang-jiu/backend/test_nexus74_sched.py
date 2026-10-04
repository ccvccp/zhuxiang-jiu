"""74号 NexusFlow 发布工作台·巡检调度器专项测试
(2026-10-04 检查升级)

运行: python test_nexus74_sched.py
"""

import asyncio
import os
import sys

os.environ["LOCK_MODE"] = "asyncio"
os.environ["STORE_MODE"] = "asyncio"
os.environ.pop("NEXUS74_SCAN_AUTO", None)
os.environ.pop("NEXUS74_SCAN_INTERVAL_SECONDS", None)

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
    from services import nexus74_scheduler as sched

    print("=" * 60)
    print("74号 NexusFlow 发布工作台·巡检调度器测试")
    print("=" * 60)
    print()

    reset_store()

    # ---------- 开关/周期/生命周期 ----------
    record("test_01_default_off",
           sched.scheduler_enabled() is False)
    os.environ["NEXUS74_SCAN_AUTO"] = "on"
    record("test_02_env_on",
           sched.scheduler_enabled() is True)
    record("test_03_default_interval_86400",
           sched.interval_seconds() == 86400)
    os.environ["NEXUS74_SCAN_INTERVAL_SECONDS"] = "1"
    record("test_04_floor_300",
           sched.interval_seconds() == 300)
    os.environ.pop("NEXUS74_SCAN_INTERVAL_SECONDS")
    os.environ.pop("NEXUS74_SCAN_AUTO")
    record("test_05_off_start_false",
           sched.start_scheduler() is False)
    os.environ["NEXUS74_SCAN_AUTO"] = "on"
    ok = sched.start_scheduler() and sched._task \
        and not sched._task.done()
    record("test_06_start_runs", ok)
    first = sched._task
    sched.start_scheduler()
    record("test_07_idempotent", sched._task is first)
    sched.stop_scheduler()
    record("test_08_stop", sched._task is None)
    os.environ.pop("NEXUS74_SCAN_AUTO")

    # ---------- 真实数据巡检 ----------
    from datetime import datetime, timedelta, UTC
    os.environ["NEXUSFLOW74_MODE"] = "full"
    now = datetime.now(UTC)
    _old = (now - timedelta(days=21)).isoformat()   # 停摆 3 周
    _backlog = (now - timedelta(days=3)).isoformat()

    _mock_store["nexus_sources"] = {
        1: {"sourceId": 1, "title": "旧供给",
            "createdAt": _old},
        2: {"sourceId": 2, "title": "新供给",
            "createdAt": (now - timedelta(
                days=1)).isoformat()},
    }
    _mock_store["nexus_publications"] = {
        10: {"publicationId": 10, "sourceId": 1,
             "platform": "zhihu", "status": "published",
             "mode": "assist", "createdAt": _old,
             "receipt": "{\"result\":\"published\"}"},
        11: {"publicationId": 11, "sourceId": 2,
             "platform": "xiaohongshu",
             "status": "awaiting_manual",
             "mode": "full", "createdAt": _backlog,
             "receipt": "{}"},
        12: {"publicationId": 12, "sourceId": 2,
             "platform": "wechat_mp", "status": "failed",
             "mode": "full", "createdAt": _backlog,
             "error": "{\"kind\":\"auth_expired\"}"},
    }
    _mock_store["nexus_personas"] = {
        1: {"personaId": 1}}
    _mock_store["nexus_retrospects"] = {
        1: {"retroId": 1, "publicationId": 10}}

    report = await sched.run_scan()

    # ① 表分布
    record("test_10_table_counts",
           report["tableCounts"]["sources"] == 2
           and report["tableCounts"]["publications"] == 3
           and report["tableCounts"]["personas"] == 1,
           str(report["tableCounts"]))
    # ② 状态分布(六态)
    dist = report["publicationStatusDist"]
    record("test_11_status_dist",
           dist.get("published") == 1
           and dist.get("awaiting_manual") == 1
           and dist.get("failed") == 1, str(dist))
    # ③ 回执积压(>48h awaiting_manual 检出)
    bl = report["receiptBacklog"]
    record("test_12_backlog",
           len(bl) == 1 and bl[0]["publicationId"] == 11
           and bl[0]["ageDays"] == 3, str(bl))
    # ④ 模式公示(env full)
    record("test_13_mode_full",
           report["mode"] == "full", report["mode"])
    # ⑤ 数据新鲜度(最新 source 1 天前)
    record("test_14_source_freshness",
           report["latestSourceAgeDays"] == 1,
           str(report["latestSourceAgeDays"]))
    # ⑥ 适配器健康(A 档凭证状态在报)
    ah = report["adapterHealth"]
    record("test_15_adapter_health",
           "wechat_mp" in ah, str(ah))
    # ⑦ 巡检结果缓存(last_scan)
    record("test_16_last_scan_cache",
           sched.last_scan().get(
               "publicationTotal") == 3)
    # ⑧ 空库巡检不炸
    reset_store()
    empty = await sched.run_scan()
    record("test_17_empty_store_ok",
           empty["publicationTotal"] == 0
           and empty["tableCounts"]["sources"] == 0)

    os.environ.pop("NEXUSFLOW74_MODE")

    # ---------- 汇总 ----------
    print("\n".join(RESULTS))
    print("=" * 60)
    print(f"通过 {PASS} / 失败 {FAIL} / 共 {PASS + FAIL}")
    print("=" * 60)
    return FAIL == 0


if __name__ == "__main__":
    sys.exit(0 if asyncio.run(main()) else 1)
