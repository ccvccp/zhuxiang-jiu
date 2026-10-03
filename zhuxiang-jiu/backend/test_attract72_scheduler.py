"""72号·AI智能自动引流大模型 健康度日度调度器专项测试
(attract72_scheduler, 2026-09-29 立项)

背景: 健康度检查此前无专属调度器——上次检查 2026-09-14,
15 天零活动(生产检查发现) → 日度调度化(kg51 范式平移)。

运行方式:
    python test_attract72_scheduler.py

覆盖:
    - 开关: 默认 off(off=零 task 零影响)/env on 开启
    - 周期: 默认 86400s/下限 300s 防忙循环/非法值回退
    - 单轮: run_scheduled_health 真实调用 P6
      check_health(meta_health 台账落痕)且 verdict 域内
    - 生命周期: 未启用 start False/启用 True/幂等
      重复 start True/stop 后可再启
    - QC: 开关默认 off 下 start 不创建 task(零影响)
"""

import asyncio
import os
import sys

os.environ["LOCK_MODE"] = "asyncio"
os.environ["STORE_MODE"] = "asyncio"
os.environ.pop("LLM_API_KEY", None)
os.environ["LLM_ENABLED"] = "off"
os.environ["XIAOZHU_LLM_MODE"] = "off"
os.environ["XIAOZHU_PROACTIVE_MODE"] = "off"
os.environ["QR55_MODE"] = "off"
os.environ["AIUP56_MODE"] = "off"
os.environ["KB57_MODE"] = "off"
os.environ["II58_MODE"] = "off"
os.environ["II59_MODE"] = "off"
os.environ["AB63_MODE"] = "off"
os.environ["PAY60_MODE"] = "off"
os.environ["PAY69_MODE"] = "off"
os.environ["PAY71_MODE"] = "off"
os.environ["ATTRACT72_MODE"] = "off"
os.environ.pop("ATTRACT72_KILL", None)
os.environ.pop("ATTRACT72_IMMUNITY", None)
os.environ.pop("ATTRACT72_HEALTH_AUTO", None)
os.environ.pop("ATTRACT72_HEALTH_INTERVAL", None)

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
    from repositories.store import reset_store
    from services import attract72_scheduler as sched

    print("=" * 60)
    print("72号 健康度日度调度器测试(attract72_scheduler)")
    print("=" * 60)
    print()

    reset_store()

    # ---------- 配置 ----------
    print("[配置]")

    # test 1: 默认 off(零 task 零影响)
    record("test_01_default_off",
           sched.scheduler_enabled() is False)

    # test 2: env on 开启
    os.environ["ATTRACT72_HEALTH_AUTO"] = "on"
    record("test_02_env_on_enables",
           sched.scheduler_enabled() is True)

    # test 3: 周期默认 86400
    os.environ.pop("ATTRACT72_HEALTH_INTERVAL", None)
    record("test_03_default_interval_86400",
           sched.scheduler_interval_seconds() == 86400)

    # test 4: 周期下限 300(防忙循环)
    os.environ["ATTRACT72_HEALTH_INTERVAL"] = "1"
    record("test_04_interval_floor_300",
           sched.scheduler_interval_seconds() == 300)

    # test 5: 非法周期回退默认
    os.environ["ATTRACT72_HEALTH_INTERVAL"] = "abc"
    record("test_05_invalid_interval_fallback",
           sched.scheduler_interval_seconds() == 86400)
    os.environ.pop("ATTRACT72_HEALTH_INTERVAL", None)

    # ---------- 生命周期 ----------
    print("[生命周期]")

    # test 6: 默认 off 下 start 不创建 task
    os.environ.pop("ATTRACT72_HEALTH_AUTO", None)
    record("test_06_start_disabled_returns_false",
           sched.start_scheduler() is False
           and sched.scheduler_running() is False)

    # test 7: 启用后 start True 且 running
    os.environ["ATTRACT72_HEALTH_AUTO"] = "on"
    ok = sched.start_scheduler() is True and \
        sched.scheduler_running() is True
    record("test_07_start_enabled_runs", ok)

    # test 8: 幂等——重复 start 不再创建 task
    first = sched._scheduler_task
    sched.start_scheduler()
    record("test_08_start_idempotent",
           sched._scheduler_task is first)

    # test 9: stop 后可再启
    sched.stop_scheduler()
    ok = sched.scheduler_running() is False
    sched.start_scheduler()
    ok = ok and sched.scheduler_running() is True
    record("test_09_stop_then_restart", ok)
    sched.stop_scheduler()
    os.environ.pop("ATTRACT72_HEALTH_AUTO", None)

    # ---------- 单轮健康检查 ----------
    print("[单轮健康检查]")

    # test 10: run_scheduled_health 调用 P6 check_health
    # 且 verdict 在封闭域内(meta_health 台账落痕)
    health = await sched.run_scheduled_health()
    record("test_10_run_health_checkId",
           bool(health.get("checkId")),
           f"health={health}")
    record("test_11_run_health_verdict_domain",
           health.get("verdict") in
           ("healthy", "degraded", "frozen"))

    # ---------- 启动即首轮(2026-10-03 修正回归) ----------
    print("[启动即首轮]")

    # test 12: start 后无需等一轮——task 启动立即执行首轮
    # 并落 meta_health 台账(原"先 sleep 后执行"在频繁重建下
    # 永不执行, 生产 meta_health=0 佐证)
    from repositories.attract72_repository import (
        Attract72Repository,
    )
    repo = Attract72Repository()
    before = len(await repo.list_health())
    os.environ["ATTRACT72_HEALTH_AUTO"] = "on"
    sched.start_scheduler()
    await asyncio.sleep(0.5)  # 让 task 首轮跑完
    after = len(await repo.list_health())
    record("test_12_start_runs_first_round_immediately",
           after > before,
           f"before={before} after={after}")
    sched.stop_scheduler()
    os.environ.pop("ATTRACT72_HEALTH_AUTO", None)

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
