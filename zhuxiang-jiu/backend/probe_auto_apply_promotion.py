r"""AI 自学习层·晋升逻辑手动验证脚本(全正反馈 + auto_apply 场景)

场景设计(A/B 对照, 单一变量 = auto_apply):
    A 组 order_risk   : auto_apply=False + 5 条全正反馈 → 学习只产挑战者, 不晋升
    B 组 points_risk  : auto_apply=True  + 5 条全正反馈 → 学习后自动晋升为冠军
    B 组收尾          : 用真实评分器跑一次评分, 确认生产路径已用上新冠军权重

预期输出(晋升逻辑正常时):
    A 组: promoted=False, newStatus=challenger, 冠军版本不变(reset 后为 v2)
    B 组: promoted=True,  newStatus=champion,   冠军版本=新学习版本, 挑战者清空,
          earn_burst 权重上调(全正反馈奖励主贡献因子), 评分器 weightVersion=新版本
    注: get_active_weight_version 仅在评分器实际评分后才有值(未加载过为 v1),
        故 A/B 组打印 v1 属正常, 以 C 组评分输出为准

在宿主机运行(需已安装 fastapi + httpx, 或用项目 .deps):
    cd D:\网站架构设计\zhuxiang-jiu\backend
    (PowerShell) $env:PYTHONPATH="D:\网站架构设计\zhuxiang-jiu\backend\.deps"; python probe_auto_apply_promotion.py
    (cmd)        set PYTHONPATH=D:\网站架构设计\zhuxiang-jiu\backend\.deps && python probe_auto_apply_promotion.py
"""

import asyncio
import os
import sys

# 单进程内存模式(与项目测试约定一致), 必须在导入服务层之前设置
os.environ["LOCK_MODE"] = "asyncio"
os.environ["STORE_MODE"] = "asyncio"
os.environ["AUTH_MODE"] = "compat"

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from services.ai_learning_service import (  # noqa: E402
    default_weights, get_active_weight_version, get_weights_view,
    reset_weights, run_learning_cycle, submit_feedback,
    update_learning_config,
)

DIV = "=" * 62


def _order_factors(credit=80, register=50, amount=30, qty=40,
                   cancel=60, address=0, remark=0, time_p=0):
    """order_risk 因子快照(贡献 = score × 默认权重)"""
    return [
        {"name": "credit", "score": credit, "contribution": round(credit * 0.20, 1)},
        {"name": "register_age", "score": register, "contribution": round(register * 0.15, 1)},
        {"name": "amount", "score": amount, "contribution": round(amount * 0.15, 1)},
        {"name": "quantity", "score": qty, "contribution": round(qty * 0.10, 1)},
        {"name": "cancel_rate", "score": cancel, "contribution": round(cancel * 0.15, 1)},
        {"name": "address", "score": address, "contribution": round(address * 0.10, 1)},
        {"name": "remark", "score": remark, "contribution": round(remark * 0.10, 1)},
        {"name": "time_pattern", "score": time_p, "contribution": round(time_p * 0.10, 1)},
    ]


def _points_factors(earn_burst=100):
    """points_risk 因子快照: earn_burst 独占全部贡献(放大奖励效果)"""
    return [
        {"name": "earn_burst", "score": earn_burst,
         "contribution": round(earn_burst * 0.25, 1)},
        {"name": "redeem_frequency", "score": 0, "contribution": 0},
        {"name": "channel_concentration", "score": 0, "contribution": 0},
        {"name": "device_accounts", "score": 0, "contribution": 0},
        {"name": "violations", "score": 0, "contribution": 0},
        {"name": "night_activity", "score": 0, "contribution": 0},
    ]


def _print_weights(title: str, weights: dict, base: dict | None = None):
    print(f"\n  {title}")
    for name, value in weights.items():
        if base is None:
            print(f"    {name:<22} {value:.4f}")
        else:
            ratio = value / base[name] if base[name] else float("inf")
            arrow = "↑" if ratio > 1.0001 else ("↓" if ratio < 0.9999 else "=")
            print(f"    {name:<22} {value:.4f}  ({arrow} {ratio:.2f}x 默认)")


async def scene_a_no_auto_apply():
    """A 组: auto_apply=False → 学习只产挑战者, 不晋升"""
    print(f"\n{DIV}\n场景 A: order_risk · auto_apply=False · 5 条全正反馈\n{DIV}")

    await reset_weights("order_risk")  # 保证可重复运行
    await update_learning_config(
        "order_risk", {"min_feedback": 5, "auto_apply": False, "eta": 0.5})

    for i in range(5):
        r = await submit_feedback({
            "scorerId": "order_risk", "scoreAtDecision": 55.0,
            "actualAction": "review", "expectedAction": "review",
            "note": f"正反馈#{i + 1}", "factors": _order_factors()})
        assert r["correct"] is True, "正反馈的 correct 派生应为 True"

    learned = await run_learning_cycle("order_risk")
    view = await get_weights_view("order_risk")
    defaults = default_weights("order_risk")

    print(f"\n  学习结果: newVersion={learned['newVersion']}, "
          f"newStatus={learned['newStatus']}, promoted={learned['promoted']}")
    print(f"  冠军版本: {view['champion']['version']}  "
          f"(activeVersion={get_active_weight_version('order_risk')}, "
          f"本组未评分故为 v1 属正常)")
    _print_weights("挑战者权重 vs 默认:", learned["weights"], defaults)

    ok = (learned["promoted"] is False
          and learned["newStatus"] == "challenger"
          and view["champion"]["version"] == "v2"  # reset 产生 v2, 未被挑战者取代
          and view["challenger"] is not None)
    print(f"\n  [A 组结论] 挑战者已生成且未晋升: "
          f"{'PASS' if ok else 'FAIL'}")
    return ok


async def scene_b_auto_apply_promotes():
    """B 组: auto_apply=True → 全正反馈学习后自动晋升为冠军"""
    print(f"\n{DIV}\n场景 B: points_risk · auto_apply=True · 5 条全正反馈\n{DIV}")

    await reset_weights("points_risk")
    await update_learning_config(
        "points_risk", {"min_feedback": 5, "auto_apply": True, "eta": 0.5})

    for i in range(5):
        r = await submit_feedback({
            "scorerId": "points_risk", "scoreAtDecision": 25.0,
            "actualAction": "low", "expectedAction": "low",
            "note": f"正反馈#{i + 1}", "factors": _points_factors()})
        assert r["correct"] is True

    learned = await run_learning_cycle("points_risk")
    view = await get_weights_view("points_risk")
    defaults = default_weights("points_risk")

    print(f"\n  学习结果: newVersion={learned['newVersion']}, "
          f"newStatus={learned['newStatus']}, promoted={learned['promoted']}")
    print(f"  冠军版本: {view['champion']['version']}  "
          f"(activeVersion={get_active_weight_version('points_risk')})")
    _print_weights("新冠军权重 vs 默认:", learned["weights"], defaults)

    guardrail_ok = all(defaults[k] / 2 - 1e-3 <= learned["weights"][k]
                       <= defaults[k] * 2 + 1e-3 for k in defaults)
    ok = (learned["promoted"] is True
          and learned["newStatus"] == "champion"
          and view["champion"]["version"] == learned["newVersion"]
          and view["challenger"] is None
          and learned["weights"]["earn_burst"] > defaults["earn_burst"]
          and guardrail_ok)
    print(f"\n  [B 组结论] 自动晋升生效 + 主贡献因子上调 + 护栏内: "
          f"{'PASS' if ok else 'FAIL'}")
    return ok, learned["newVersion"]


async def scene_c_scorer_uses_new_champion(expected_version: str):
    """C 组收尾: 真实评分器验证生产路径用上新冠军权重"""
    print(f"\n{DIV}\n场景 C: 评分器端到端 · 确认生效权重 = 新冠军\n{DIV}")

    from services.ai_scoring_ext_service import PointsRiskScorer
    r = await PointsRiskScorer().score({
        "todayEarned": 300, "dailyEarnCap": 200, "dailyRedeemCount": 1,
        "singleChannelRatio": 0.2, "sameDeviceAccounts": 1,
        "violationCount": 0, "nightActionRatio": 0.05})
    earn = next(f for f in r["factors"] if f["name"] == "earn_burst")
    print(f"  评分输出: weightVersion={r['weightVersion']}, "
          f"earn_burst weight={earn['weight']:.4f}, "
          f"contribution={earn['contribution']:.1f}")
    ok = (r["weightVersion"] == expected_version
          and earn["weight"] > default_weights("points_risk")["earn_burst"])
    print(f"\n  [C 组结论] 评分器已使用新冠军权重: {'PASS' if ok else 'FAIL'}")
    return ok


async def main():
    print(f"{DIV}\nAI 自学习层 · 晋升逻辑验证(全正反馈 + auto_apply)\n{DIV}")
    ok_a = await scene_a_no_auto_apply()
    ok_b, new_version = await scene_b_auto_apply_promotes()
    ok_c = await scene_c_scorer_uses_new_champion(new_version)
    results = [ok_a, ok_b, ok_c]
    print(f"\n{DIV}\n总结: A={results[0]} B={results[1]} C={results[2]} → "
          f"{'全部 PASS' if all(results) else '存在 FAIL'}\n{DIV}")
    sys.exit(0 if all(results) else 1)


if __name__ == "__main__":
    asyncio.run(main())
