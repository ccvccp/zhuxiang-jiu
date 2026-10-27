"""40号·DV博主模块 调度器扩展专项测试(2026-10-03 检查升级)

背景: P7"全网实时价值侦测中枢"设计宣称实时, 落地为手动 API 触发
(生产 36 事件后再无增量)——检查升级补齐 radar2 采集评分轮与
P5a/P6a 引擎学习回流轮; 本测试锁定新增两轮的开关/周期/生命周期/
单轮真实调用语义。

运行: python test_blogger_sched2.py
"""
# ---------------------------------------------------------------------------
# [SCRIPT-STYLE TEST | 脚本式测试]  pytest 收集为 0 属预期（IMP-12 台账文件）。
#   运行方式 : python test_blogger_sched2.py        （cwd 必须是 zhuxiang-jiu/backend）
#   结果判定 : 看脚本尾部打印的 pass/fail 计数，不要看 pytest 输出。
#   勿误判   : pytest --collect-only 对本文件收集 0 条并以退出码 5
#              「no tests collected」结束——这是本文件的正常形态。
#              禁止据此判定测试损坏，禁止把退出码 5 当失败上报。
#   成因     : 用例以 record()/模块级计数函数组织，或 Test* 类内方法未命名
#              为 test_*，不满足 pytest.ini 的 python_functions=test_* 规则。
#   台账     : backend/scriptstyle_tests_manifest.json（机器可读）
#   闸门     : backend/test_scriptstyle_gate.py 校验清单一致性与 banner 存在性
# ---------------------------------------------------------------------------


import asyncio
import os
import sys

os.environ["LOCK_MODE"] = "asyncio"
os.environ["STORE_MODE"] = "asyncio"
os.environ["LLM_ENABLED"] = "off"
os.environ.pop("BLOGGER_RADAR2_AUTO", None)
os.environ.pop("BLOGGER_ENGINE_LEARNING_AUTO", None)
os.environ.pop("BLOGGER_RADAR2_INTERVAL_SECONDS", None)
os.environ.pop("BLOGGER_ENGINE_LEARNING_INTERVAL_SECONDS", None)

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
    from services import blogger_scheduler as sched

    print("=" * 60)
    print("40号 调度器扩展测试(radar2 + engine_learning)")
    print("=" * 60)
    print()

    reset_store()

    # ---------- 开关与周期 ----------
    print("[开关与周期]")

    record("test_01_radar2_default_off",
           sched.radar2_enabled() is False)
    record("test_02_engine_learning_default_off",
           sched.engine_learning_enabled() is False)

    os.environ["BLOGGER_RADAR2_AUTO"] = "on"
    os.environ["BLOGGER_ENGINE_LEARNING_AUTO"] = "on"
    record("test_03_env_on_enables_both",
           sched.radar2_enabled() is True
           and sched.engine_learning_enabled() is True)

    record("test_04_radar2_default_interval_900",
           sched._interval("BLOGGER_RADAR2_INTERVAL_SECONDS", 900) == 900)
    record("test_05_engine_learning_default_interval_3600",
           sched._interval(
               "BLOGGER_ENGINE_LEARNING_INTERVAL_SECONDS", 3600) == 3600)
    os.environ["BLOGGER_RADAR2_INTERVAL_SECONDS"] = "10"
    record("test_06_interval_floor_60",
           sched._interval(
               "BLOGGER_RADAR2_INTERVAL_SECONDS", 900) == 60)
    os.environ.pop("BLOGGER_RADAR2_INTERVAL_SECONDS", None)

    # ---------- 生命周期 ----------
    print("[生命周期]")

    os.environ.pop("BLOGGER_RADAR2_AUTO", None)
    record("test_07_radar2_off_start_false",
           sched.start_radar2_scheduler() is False)
    os.environ.pop("BLOGGER_ENGINE_LEARNING_AUTO", None)
    record("test_08_engine_learning_off_start_false",
           sched.start_engine_learning_scheduler() is False)

    os.environ["BLOGGER_RADAR2_AUTO"] = "on"
    os.environ["BLOGGER_ENGINE_LEARNING_AUTO"] = "on"
    ok = sched.start_radar2_scheduler() is True
    ok = ok and sched._RADAR2_TASK is not None \
        and not sched._RADAR2_TASK.done()
    record("test_09_radar2_start_runs", ok)

    ok = sched.start_engine_learning_scheduler() is True
    ok = ok and sched._ENGINE_LEARNING_TASK is not None \
        and not sched._ENGINE_LEARNING_TASK.done()
    record("test_10_engine_learning_start_runs", ok)

    first = sched._RADAR2_TASK
    sched.start_radar2_scheduler()
    record("test_11_start_idempotent",
           sched._RADAR2_TASK is first)

    sched.stop_schedulers()
    record("test_12_stop_all",
           (sched._RADAR2_TASK is None
            and sched._ENGINE_LEARNING_TASK is None
            and sched._RADAR_TASK is None))
    os.environ.pop("BLOGGER_RADAR2_AUTO", None)
    os.environ.pop("BLOGGER_ENGINE_LEARNING_AUTO", None)

    # ---------- 单轮语义(radar2 真实调用链) ----------
    print("[单轮语义]")

    # P7a 采集: 幂等槽位(mock 源, 全量 active 频道)
    from services.radar_hub_service import RadarHubService
    result = await RadarHubService().collect_events()
    record("test_13_collect_events_runs",
           "collected" in result,
           f"keys={list(result)[:6]}")

    # P7b 评分: 全量事件三维评分
    from services.radar_score_service import RadarScoreService
    scored = await RadarScoreService().score_events()
    record("test_14_score_events_runs",
           isinstance(scored, dict),
           f"keys={list(scored)[:6]}")

    # P5a/P6a 采集方法存在且可调(观测面, 空库不炸)
    from services.blogger_auto_learn_service import (
        BloggerAutoLearnService,
    )
    sig = await BloggerAutoLearnService().collect_signals()
    record("test_15_p5a_collect_signals_runs",
           isinstance(sig, dict), f"keys={list(sig)[:6]}")

    from services.blogger_av_learn_service import (
        BloggerAVLearnService,
    )
    av = await BloggerAVLearnService().collect_av_signals()
    record("test_16_p6a_collect_av_signals_runs",
           isinstance(av, dict), f"keys={list(av)[:6]}")

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
