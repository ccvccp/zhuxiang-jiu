"""66号·AI智能工程师大模块 P5a 专项测试
(监控维护自动化)

运行方式:
    python test_xx66_p5a.py

覆盖(P5a 三件):
    - 调度器(开关默认 off/interval 下限/mode 门控/幂等 stop)
    - vitals trust 区对账回填(无基线 no_baseline → clean →
      danger 呈现不改判定)
    - heal 预演信值域对账预检(clean 放行/danger 降级建议书/
      非 trust 域 skipped)
"""

import asyncio
import os
import sys

os.environ["LOCK_MODE"] = "asyncio"
os.environ["STORE_MODE"] = "asyncio"
os.environ.pop("LLM_API_KEY", None)
os.environ["LLM_ENABLED"] = "off"
os.environ["AI_ENFORCE_MODE"] = "observe"
os.environ["XX66_MODE"] = "off"
os.environ["XX66_LLM_MODE"] = "off"
os.environ["XX66_SCAN_AUTO"] = "off"
os.environ["XX66_RECON_AUTO"] = "off"

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


def reset_all():
    from repositories.store import reset_store as _reset
    _reset()


class TestScheduler:
    """01 调度器(开关/周期/mode 门控)"""

    async def run(self):
        print("[01 调度器]")
        from services import xx66_scheduler as sch

        record("巡检开关默认 off", not sch.scan_enabled())
        record("对账开关默认 off", not sch.recon_enabled())

        os.environ["XX66_SCAN_AUTO"] = "on"
        os.environ["XX66_RECON_AUTO"] = "on"
        record("巡检开关 on 生效", sch.scan_enabled())
        record("对账开关 on 生效", sch.recon_enabled())

        record("巡检周期默认 300",
               sch._interval("XX66_SCAN_INTERVAL_SECONDS", 300)
               == 300)
        record("巡检周期下限 60",
               sch._interval("XX66_SCAN_INTERVAL_SECONDS_NOPE",
                             10, floor=60) == 60)
        os.environ["XX66_RECON_INTERVAL_SECONDS"] = "100"
        record("对账周期下限 3600",
               sch._interval("XX66_RECON_INTERVAL_SECONDS",
                             86400, floor=3600) == 3600)
        del os.environ["XX66_RECON_INTERVAL_SECONDS"]

        # mode 门控: 决策面 off 语义同口径跳过
        os.environ["XX66_MODE"] = "off"
        record("off 态跳过执行", not sch._mode_active())
        os.environ["XX66_MODE"] = "shadow"
        record("shadow 态可执行", sch._mode_active())
        os.environ["XX66_MODE"] = "assist"
        record("assist 态可执行", sch._mode_active())
        os.environ["XX66_MODE"] = "off"

        # 幂等启动/停止(off 开关下返回 False 不起任务)
        os.environ["XX66_SCAN_AUTO"] = "off"
        record("off 开关启动返回 False",
               not sch.start_scan_scheduler())
        os.environ["XX66_SCAN_AUTO"] = "on"
        os.environ["XX66_MODE"] = "shadow"
        record("on 开关启动成功", sch.start_scan_scheduler())
        record("重复启动幂等", sch.start_scan_scheduler())
        sch.stop_schedulers()
        record("停止后可再启动", sch.start_scan_scheduler())
        sch.stop_schedulers()
        record("停止后任务清空", sch._SCAN_TASK is None)
        os.environ["XX66_SCAN_AUTO"] = "off"
        os.environ["XX66_RECON_AUTO"] = "off"
        os.environ["XX66_MODE"] = "off"


class TestVitalsReconBackfill:
    """02 vitals trust 区对账回填"""

    async def run(self):
        print("[02 vitals 对账回填]")
        from services.xx66_service import Xx66Service
        from services.xx66_recon_service import (
            Xx66ReconService,
        )
        svc = Xx66Service()

        reset_all()
        v = await svc.vitals()
        trust = v["zones"]["trust"]
        record("无基线 no_baseline",
               trust.get("reconStatus") == "no_baseline",
               str(trust.get("reconStatus")))

        # 跑一轮对账(内存空态: 四不变式全过 → clean)
        os.environ["XX66_MODE"] = "shadow"
        await Xx66ReconService().run_recon()
        v2 = await svc.vitals()
        trust2 = v2["zones"]["trust"]
        record("空态对账 clean",
               trust2.get("reconStatus") == "clean",
               str(trust2.get("reconStatus")))
        record("回填轮次号", trust2.get("reconRunId") == 1,
               str(trust2.get("reconRunId")))

        # 直插 danger 轮次(repo 层种子)——呈现 danger 不改判定
        from repositories.xx66_repository import (
            Xx66Repository,
        )
        from core.helpers import ts as _ts
        await Xx66Repository().save_recon_run({
            "runId": 2, "invariants": {}, "ledgerCount": 0,
            "profileCount": 0, "diffCount": 1, "dangerCount": 1,
            "dangerList": ["I1"], "status": "completed",
            "ranAt": _ts(),
        })
        v3 = await svc.vitals()
        trust3 = v3["zones"]["trust"]
        record("danger 轮次呈现 danger",
               trust3.get("reconStatus") == "danger",
               str(trust3.get("reconStatus")))
        record("danger 不改 zone 判定(呈现最小变更)",
               trust3.get("score") == trust2.get("score"))
        os.environ["XX66_MODE"] = "off"


class TestHealTrustDryRun:
    """03 heal 预演信值域对账预检"""

    async def run(self):
        print("[03 预演信值域预检]")
        from services.xx66_heal_service import Xx66HealService
        heal = Xx66HealService()

        reset_all()
        # 非 trust 域: skipped
        p0 = await heal._dry_run(
            ["cache_purge"], "monitor:disk_full")
        record("非信值域 skipped",
               p0["details"][0]["trustDomainDryRun"]
               == "skipped"
               and p0["passable"])

        # trust 域 + 无基线: no_baseline 放行
        p1 = await heal._dry_run(
            ["cache_purge"], "trust:hub_degraded")
        record("信值域无基线放行",
               p1["details"][0]["trustDomainDryRun"]
               == "no_baseline" and p1["passable"])

        # 直插 clean 轮次 → clean 放行
        from repositories.xx66_repository import (
            Xx66Repository,
        )
        from core.helpers import ts as _ts
        repo = Xx66Repository()
        await repo.save_recon_run({
            "runId": 1, "invariants": {}, "ledgerCount": 0,
            "profileCount": 0, "diffCount": 0, "dangerCount": 0,
            "dangerList": [], "status": "completed",
            "ranAt": _ts(),
        })
        p2 = await heal._dry_run(
            ["cache_purge"], "trust:hub_degraded")
        record("clean 轮次放行",
               p2["details"][0]["trustDomainDryRun"]
               == "clean" and p2["passable"])

        # danger 轮次 → 预检不过(降级建议书)
        await repo.save_recon_run({
            "runId": 2, "invariants": {}, "ledgerCount": 0,
            "profileCount": 0, "diffCount": 1, "dangerCount": 1,
            "dangerList": ["I1"], "status": "completed",
            "ranAt": _ts(),
        })
        p3 = await heal._dry_run(
            ["cache_purge"], "trust:hub_degraded")
        record("danger 轮次预检不过",
               not p3["passable"])
        record("danger 附降级原因",
               "差异态" in p3["details"][0].get("reason", ""))
        record("白名单动作仍白名单(danger 不改白名单事实)",
               p3["details"][0]["whitelisted"])


class TestP0Regression:
    """04 P0 回归抽样(reconStatus 断言同步)"""

    async def run(self):
        print("[04 P0 回归抽样]")
        reset_all()
        from services.xx66_service import Xx66Service
        v = await Xx66Service().vitals()
        trust = v["zones"]["trust"]
        record("P0 断言同步: 空态不再 not_implemented",
               trust.get("reconStatus") != "not_implemented")
        record("快照 zoneDetails 携带回填字段",
               "reconStatus" in trust and "reconRunId" in trust)


async def main():
    print("=" * 60)
    print("66号·AI智能工程师 P5a 监控维护自动化 专项测试")
    print("=" * 60)
    for cls in (TestScheduler, TestVitalsReconBackfill,
                TestHealTrustDryRun, TestP0Regression):
        await cls().run()
    print("-" * 60)
    for line in RESULTS:
        print(line)
    print("=" * 60)
    print(f"总计: {PASS} 通过 / {FAIL} 失败")
    print("=" * 60)
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
