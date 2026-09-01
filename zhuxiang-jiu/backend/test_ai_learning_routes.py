"""AI 自学习层测试(Service 层 + HTTP 层 + 评分器端到端, 40 项)

覆盖:
    - 注册表与默认权重(2): 16 个可学习档案(14 评分器, 物流路由按
      speed/cost/balanced 策略展开为 3 个独立档案)
    - 权重加载回退(1): 无档案时回退默认权重
    - 反馈提交(4): expectedAction 推导 correct / 缺标注 / 未知评分器 / 未知因子
    - 漂移监控(2): 基线建立 / 二次反馈漂移检测
    - Hedge 学习(4): 反馈不足 / 学习产出挑战者 / 护栏约束 / 二轮学习退役旧挑战者
    - 冠军/挑战者(3): 无挑战者晋升 / 晋升生效 / 评分器使用晋升权重(端到端)
    - 人工权重管理(7): 覆盖生效(端到端) / 因子集错误 / 权重和违规 / 护栏违规 /
      重置默认 / 版本历史 / 配置校验
    - 自动晋升(1): 全正反馈 + auto_apply → 挑战者直接晋升
    - 认证评分器(1): auth_risk 权重覆盖端到端
    - 物流策略子键(1): logistics_routing:cost 独立档案
    - HTTP 层(8): 鉴权 403 / 总览 / 404 / 覆盖 / 反馈 / 学习 409+200 /
      晋升+重置+历史+漂移 / 配置 422

在宿主机运行(需已安装 fastapi + httpx):
    cd D:\\网站架构设计\\zhuxiang-jiu\\backend
    $env:LOCK_MODE="asyncio"; $env:STORE_MODE="asyncio"; $env:AUTH_MODE="compat"
    python test_ai_learning_routes.py
"""

import asyncio
import os
import sys

os.environ["LOCK_MODE"] = "asyncio"
os.environ["STORE_MODE"] = "asyncio"
os.environ.setdefault("AUTH_MODE", "compat")

from services.ai_learning_service import (
    SCORER_REGISTRY, default_weights, get_weights_view,
    invalidate_weight_cache, load_effective_weights,
    manual_override_weights, overview, promote_challenger,
    reset_weights, run_learning_cycle, submit_feedback,
    update_learning_config, get_history, get_drift_view,
)

PASS = 0
FAIL = 0
RESULTS = []


def record(name, passed, detail=""):
    global PASS, FAIL
    if passed:
        PASS += 1
        RESULTS.append(f"  [PASS] {name}")
    else:
        FAIL += 1
        RESULTS.append(f"  [FAIL] {name} -- {detail}")


async def _expect_error(name, coro, exc_type):
    try:
        await coro
        record(name, False, f"未抛出 {exc_type.__name__}")
    except exc_type:
        record(name, True)
    except Exception as exc:
        record(name, False, f"异常类型错误: {type(exc).__name__}: {exc}")


def _order_factors(credit=80, register=50, amount=30, qty=40,
                   cancel=60, address=0, remark=0, time_p=0):
    """构造 order_risk 因子快照(贡献 = score × 默认权重)"""
    return [
        {"name": "credit", "score": credit, "contribution": round(credit * 0.20, 1)},
        {"name": "register_age", "score": register, "contribution": round(register * 0.15, 1)},
        {"name": "amount", "score": amount, "contribution": round(amount * 0.15, 1)},
        {"name": "quantity", "score": qty, "contribution": round(qty * 0.10, 1)},
        {"name": "cancel_rate", "score": cancel, "contribution": round(cancel * 0.15, 1)},
        {"name": "address", "score": address, "contribution": round(address * 0.10, 1)},
        {"name": "remark", "score": remark, "contribution": round(remark * 0.10, 1)},
        {"name": "time_pattern", "score": time_p, "contribution": round(time_p * 0.05, 1)},
    ]


async def main():
    print("=" * 64)
    print("AI 自学习层测试(Hedge 在线学习 + 冠军/挑战者 + 漂移监控 + 端到端)")
    print("=" * 64)

    # ========================================================
    # 1. 注册表与默认权重
    # ========================================================
    record("01_registry_covers_19_profiles",
           len(SCORER_REGISTRY) == 19
           and "logistics_routing:cost" in SCORER_REGISTRY
           and "promo_hotspot" in SCORER_REGISTRY
           and "alliance_onboarding" in SCORER_REGISTRY
           and "alliance_review" in SCORER_REGISTRY,
           f"count={len(SCORER_REGISTRY)}")

    sums_ok = True
    for sid in SCORER_REGISTRY:
        dw = default_weights(sid)
        target = 0.5 if sid.startswith("logistics_routing:") else 1.0
        if abs(sum(dw.values()) - target) > 0.01 or not dw:
            sums_ok = False
            break
    record("02_default_weights_valid_for_all", sums_ok,
           f"scorer={sid if not sums_ok else '-'}, sum={sum(dw.values()):.4f}")

    w = await load_effective_weights("member_profile",
                                     default_weights("member_profile"))
    record("03_load_falls_back_to_defaults",
           w == default_weights("member_profile"),
           f"weights={w}")

    # ========================================================
    # 2. 反馈提交(auth_risk 组)
    # ========================================================
    r = await submit_feedback({
        "scorerId": "auth_risk", "scoreAtDecision": 18.0,
        "actualAction": "allow", "expectedAction": "step_up",
        "factors": [
            {"name": "failed_attempts", "score": 10, "contribution": 2.0},
            {"name": "geo_velocity", "score": 0, "contribution": 0},
            {"name": "device_match", "score": 50, "contribution": 10.0},
            {"name": "ip_reputation", "score": 0, "contribution": 0},
            {"name": "time_pattern", "score": 0, "contribution": 0},
            {"name": "account_age", "score": 0, "contribution": 0},
            {"name": "password_strength", "score": 20, "contribution": 2.0},
            {"name": "behavior_deviation", "score": 40, "contribution": 4.0},
        ]})
    record("04_feedback_derives_correct_from_expected",
           r["success"] is True and r["correct"] is False,
           f"correct={r.get('correct')}")

    await _expect_error(
        "05_feedback_requires_outcome_label",
        submit_feedback({"scorerId": "auth_risk", "scoreAtDecision": 10.0,
                         "actualAction": "allow",
                         "factors": [{"name": "geo_velocity", "score": 0}]}),
        ValueError)

    await _expect_error(
        "06_feedback_unknown_scorer_rejected",
        submit_feedback({"scorerId": "no_such_scorer", "scoreAtDecision": 10.0,
                         "actualAction": "x", "correct": True,
                         "factors": [{"name": "a", "score": 1}]}),
        KeyError)

    await _expect_error(
        "07_feedback_unknown_factor_rejected",
        submit_feedback({"scorerId": "auth_risk", "scoreAtDecision": 10.0,
                         "actualAction": "allow", "correct": True,
                         "factors": [{"name": "no_such_factor", "score": 1}]}),
        ValueError)

    # ========================================================
    # 3. 漂移监控(auth_risk 组)
    # ========================================================
    drift = await get_drift_view("auth_risk")
    record("08_drift_baseline_initialized",
           drift["drift"].get("count") == 1 and drift["drift"]["driftScore"] == 0.0,
           f"drift={drift['drift']}")

    await submit_feedback({
        "scorerId": "auth_risk", "scoreAtDecision": 90.0,
        "actualAction": "block", "expectedAction": "block",
        "factors": [
            {"name": "failed_attempts", "score": 100, "contribution": 20.0},
            {"name": "geo_velocity", "score": 100, "contribution": 15.0},
            {"name": "device_match", "score": 100, "contribution": 20.0},
            {"name": "ip_reputation", "score": 100, "contribution": 15.0},
            {"name": "time_pattern", "score": 100, "contribution": 5.0},
            {"name": "account_age", "score": 100, "contribution": 5.0},
            {"name": "password_strength", "score": 100, "contribution": 10.0},
            {"name": "behavior_deviation", "score": 100, "contribution": 10.0},
        ]})
    drift = await get_drift_view("auth_risk")
    record("09_drift_detects_shift",
           drift["drift"]["count"] == 2 and drift["drift"]["driftScore"] > 0
           and drift["drift"]["driftLevel"] in ("medium", "high"),
           f"drift={drift['drift']}")

    # ========================================================
    # 4. Hedge 学习(order_risk 组)
    # ========================================================
    await update_learning_config("order_risk", {"min_feedback": 3})
    await _expect_error(
        "10_learn_insufficient_feedback",
        run_learning_cycle("order_risk"), ValueError)

    for i in range(3):
        await submit_feedback({
            "scorerId": "order_risk", "scoreAtDecision": 40.0,
            "actualAction": "review", "expectedAction": "review",
            "factors": _order_factors(credit=80 if i < 2 else 20,
                                      cancel=60 if i < 2 else 10)})

    learned = await run_learning_cycle("order_risk")
    champion_w = learned["weights"]
    dw = default_weights("order_risk")
    record("11_learn_cycle_creates_challenger",
           learned["promoted"] is False and learned["newStatus"] == "challenger"
           and learned["learnedFrom"] == 3
           and champion_w != dw and abs(sum(champion_w.values()) - 1.0) < 0.001,
           f"result={ {k: learned[k] for k in ('newVersion', 'newStatus', 'promoted')} }")

    guardrail_ok = all(dw[k] / 2 - 0.001 <= champion_w[k] <= dw[k] * 2 + 0.001
                       for k in dw)
    record("12_learn_respects_guardrail", guardrail_ok,
           f"weights={champion_w}")

    # 再补 3 条反馈 → 二轮学习, 旧挑战者退役入历史
    for _ in range(3):
        await submit_feedback({
            "scorerId": "order_risk", "scoreAtDecision": 55.0,
            "actualAction": "review", "correct": False,
            "factors": _order_factors(credit=10, cancel=10, amount=90)})
    learned2 = await run_learning_cycle("order_risk")
    hist = await get_history("order_risk")
    record("13_second_cycle_retires_old_challenger",
           learned2["newVersion"] != learned["newVersion"]
           and any(h["version"] == learned["newVersion"] for h in hist["history"]),
           f"v2={learned['newVersion']}, v3={learned2['newVersion']}, "
           f"history={[h['version'] for h in hist['history']]}")

    await _expect_error(
        "14_promote_without_challenger_fails",
        promote_challenger("member_profile"), ValueError)

    promotion = await promote_challenger("order_risk")
    view = await get_weights_view("order_risk")
    record("15_promote_challenger_to_champion",
           promotion["promotedVersion"] == learned2["newVersion"]
           and view["champion"]["version"] == learned2["newVersion"]
           and view["challenger"] is None,
           f"promotion={promotion.get('promotedVersion')}, "
           f"champion={view['champion']['version']}")

    # 端到端: 评分器使用晋升后的权重
    from services.ai_scoring_service import OrderRiskScorer
    r = await OrderRiskScorer().score({
        "bambooScore": 400, "registerHours": 100, "orderAmount": 2000.0,
        "totalQuantity": 2, "historyOrders": 6, "historyCancels": 2,
        "addressComplete": True, "remark": "", "orderHour": 14})
    credit_factor = next(f for f in r["factors"] if f["name"] == "credit")
    record("16_scorer_uses_promoted_weights",
           r["weightVersion"] == learned2["newVersion"]
           and abs(credit_factor["weight"] - learned2["weights"]["credit"]) < 0.001,
           f"version={r['weightVersion']}, "
           f"credit_weight={credit_factor['weight']}")

    # ========================================================
    # 5. 人工权重管理(traffic_antifraud 组)
    # ========================================================
    override = {"burst": 0.30, "new_account": 0.15, "promoter_history": 0.10,
                "conversion": 0.15, "source": 0.15, "night": 0.10,
                "effective_rate": 0.05}
    applied = await manual_override_weights("traffic_antifraud", override,
                                            reason="测试覆盖")
    from services.ai_scoring_service import TrafficAntiFraudScorer
    r = await TrafficAntiFraudScorer().score({
        "promoterId": 1, "recentCount": 10, "avgIntervalSeconds": 600,
        "newAccountRatio": 0.2, "nightRatio": 0.1, "conversionRate": 0.3,
        "uniqueSources": 3, "totalRecords": 100, "effectiveRecords": 80,
        "fraudCount": 0})
    burst = next(f for f in r["factors"] if f["name"] == "burst")
    record("17_manual_override_applies_immediately",
           applied["newVersion"] == "v2" and r["weightVersion"] == "v2"
           and abs(burst["weight"] - 0.30) < 0.001,
           f"applied={applied.get('newVersion')}, output={r['weightVersion']}, "
           f"burst_weight={burst['weight']}")

    await _expect_error(
        "18_override_wrong_factor_set_rejected",
        manual_override_weights("traffic_antifraud",
                                {"burst": 0.5, "new_account": 0.5}),
        ValueError)

    await _expect_error(
        "19_override_sum_violation_rejected",
        manual_override_weights("traffic_antifraud",
                                {**override, "burst": 0.90}),
        ValueError)

    await _expect_error(
        "20_override_guardrail_violation_rejected",
        manual_override_weights("traffic_antifraud",
                                {**override, "burst": 0.45,
                                 "promoter_history": 0.05}),
        ValueError)

    reset = await reset_weights("traffic_antifraud")
    view = await get_weights_view("traffic_antifraud")
    record("21_reset_restores_defaults",
           reset["success"] is True
           and view["champion"]["weights"] == default_weights("traffic_antifraud")
           and view["challenger"] is None,
           f"champion={view['champion']['weights']}")

    hist = await get_history("traffic_antifraud")
    record("22_history_preserves_retired_versions",
           hist["historyCount"] >= 2
           and any(h["source"] == "manual" for h in hist["history"]),
           f"history={[h['version'] for h in hist['history']]}")

    await update_learning_config("traffic_antifraud", {"eta": 0.8})
    await _expect_error(
        "23_config_validation_rejects_bad_range",
        update_learning_config("traffic_antifraud", {"guardrail": 20}),
        ValueError)
    await _expect_error(
        "24_config_rejects_unknown_key",
        update_learning_config("traffic_antifraud", {"unknown_key": 1}),
        ValueError)
    cfg = await update_learning_config("traffic_antifraud", {"eta": 0.5})
    record("25_config_update_roundtrip",
           cfg["config"]["eta"] == 0.5 and cfg["config"]["guardrail"] == 2.0,
           f"config={cfg['config']}")

    # ========================================================
    # 6. 自动晋升(points_risk 组: 全正反馈 + auto_apply)
    # ========================================================
    await update_learning_config("points_risk",
                                 {"min_feedback": 2, "auto_apply": True})
    points_factors = [
        {"name": "earn_burst", "score": 100, "contribution": 25.0},
        {"name": "redeem_frequency", "score": 0, "contribution": 0},
        {"name": "channel_concentration", "score": 0, "contribution": 0},
        {"name": "device_accounts", "score": 0, "contribution": 0},
        {"name": "violations", "score": 0, "contribution": 0},
        {"name": "night_activity", "score": 0, "contribution": 0},
    ]
    for _ in range(2):
        await submit_feedback({"scorerId": "points_risk",
                               "scoreAtDecision": 25.0,
                               "actualAction": "low", "expectedAction": "low",
                               "factors": points_factors})
    auto = await run_learning_cycle("points_risk")
    record("26_auto_apply_promotes_when_better",
           auto["promoted"] is True and auto["newStatus"] == "champion"
           and auto["weights"]["earn_burst"] > 0.25,
           f"promoted={auto['promoted']}, "
           f"earn_burst={auto['weights'].get('earn_burst')}")

    # ========================================================
    # 7. 认证评分器 + 物流策略子键端到端
    # ========================================================
    auth_override = {"failed_attempts": 0.30, "geo_velocity": 0.10,
                     "device_match": 0.15, "ip_reputation": 0.15,
                     "time_pattern": 0.05, "account_age": 0.05,
                     "password_strength": 0.10, "behavior_deviation": 0.10}
    await manual_override_weights("auth_risk", auth_override, reason="认证调权")
    from services.ai_scoring_auth_service import AuthRiskScorer
    r = await AuthRiskScorer().score({
        "failedAttempts": 5, "newDevice": False, "ipRiskType": "clean",
        "loginHour": 14, "passwordStatus": "medium"})
    failed = next(f for f in r["factors"] if f["name"] == "failed_attempts")
    record("27_auth_scorer_uses_overridden_weights",
           r["weightVersion"] == "v2" and abs(failed["weight"] - 0.30) < 0.001
           and abs(failed["contribution"] - 15.0) < 0.1,
           f"version={r['weightVersion']}, weight={failed['weight']}, "
           f"contribution={failed['contribution']}")

    await manual_override_weights("logistics_routing:cost",
                                  {"speed": 0.05, "cost": 0.45},
                                  reason="成本策略调权")
    from services.ai_scoring_service import LogisticsRoutingScorer
    r = await LogisticsRoutingScorer().score({"weight": 2.0, "budget": "cost"})
    r_balanced = await LogisticsRoutingScorer().score(
        {"weight": 2.0, "budget": "balanced"})
    record("28_logistics_budget_variant_isolated",
           r["weightVersion"] == "v2" and r_balanced["weightVersion"] == "v1",
           f"cost={r['weightVersion']}, balanced={r_balanced['weightVersion']}")

    # ========================================================
    # 8. HTTP 层(TestClient 全栈, member_profile 组)
    # ========================================================
    from fastapi.testclient import TestClient
    from main import app
    client = TestClient(app)

    resp = client.get("/api/ai-learning/overview")
    record("29_http_overview_requires_admin",
           resp.status_code == 403, f"status={resp.status_code}")

    resp = client.get("/api/ai-learning/overview", headers={"X-Role": "admin"})
    body = resp.json()
    record("30_http_overview_lists_all_scorers",
           resp.status_code == 200 and body.get("scorerCount") == 19
           and len(body.get("scorers", [])) == 19,
           f"status={resp.status_code}, count={body.get('scorerCount')}")

    resp = client.get("/api/ai-learning/weights/no_such_scorer",
                      headers={"X-Role": "admin"})
    record("31_http_unknown_scorer_404",
           resp.status_code == 404, f"status={resp.status_code}")

    resp = client.put("/api/ai-learning/weights/member_profile",
                      json={"weights": {"profile": 1.0}})
    record("32_http_override_requires_admin",
           resp.status_code == 403, f"status={resp.status_code}")

    resp = client.put("/api/ai-learning/weights/member_profile",
                      headers={"X-Role": "admin"},
                      json={"weights": {"profile": 0.10, "account_age": 0.10,
                                        "activity": 0.10, "consumption": 0.30,
                                        "repurchase": 0.10, "refund": 0.15,
                                        "credit": 0.15},
                            "reason": "HTTP 覆盖测试"})
    body = resp.json()
    record("33_http_override_success",
           resp.status_code == 200 and body.get("newVersion") == "v2"
           and abs(body["weights"]["consumption"] - 0.30) < 0.001,
           f"status={resp.status_code}, body={body}")

    member_factors = [
        {"name": "profile", "score": 50, "contribution": 5.0},
        {"name": "account_age", "score": 50, "contribution": 5.0},
        {"name": "activity", "score": 50, "contribution": 7.5},
        {"name": "consumption", "score": 50, "contribution": 10.0},
        {"name": "repurchase", "score": 50, "contribution": 7.5},
        {"name": "refund", "score": 50, "contribution": 7.5},
        {"name": "credit", "score": 50, "contribution": 7.5},
    ]
    resp = client.post("/api/ai-learning/feedback", headers={"X-Role": "admin"},
                       json={"scorerId": "member_profile",
                             "scoreAtDecision": 50.0,
                             "actualAction": "standard",
                             "expectedAction": "standard",
                             "factors": member_factors})
    record("34_http_feedback_success",
           resp.status_code == 200 and resp.json().get("correct") is True,
           f"status={resp.status_code}, body={resp.json()}")

    resp = client.post("/api/ai-learning/feedback", headers={"X-Role": "admin"},
                       json={"scorerId": "member_profile",
                             "scoreAtDecision": 50.0,
                             "actualAction": "standard",
                             "factors": member_factors})
    record("35_http_feedback_without_outcome_409",
           resp.status_code == 409, f"status={resp.status_code}")

    resp = client.post("/api/ai-learning/learn/member_profile",
                       headers={"X-Role": "admin"})
    record("36_http_learn_insufficient_409",
           resp.status_code == 409, f"status={resp.status_code}")

    resp = client.put("/api/ai-learning/config/member_profile",
                      headers={"X-Role": "admin"},
                      json={"min_feedback": 1})
    record("37_http_config_update_success",
           resp.status_code == 200
           and resp.json()["config"]["min_feedback"] == 1,
           f"status={resp.status_code}")

    resp = client.post("/api/ai-learning/learn/member_profile",
                       headers={"X-Role": "admin"})
    body = resp.json()
    record("38_http_learn_success",
           resp.status_code == 200 and body.get("newStatus") == "challenger"
           and body.get("learnedFrom") == 1,
           f"status={resp.status_code}, body={ {k: body.get(k) for k in ('newVersion', 'newStatus')} }")

    resp = client.post("/api/ai-learning/promote/member_profile",
                       headers={"X-Role": "admin"})
    promoted_ok = resp.status_code == 200
    resp = client.post("/api/ai-learning/reset/member_profile",
                       headers={"X-Role": "admin"})
    reset_ok = resp.status_code == 200
    resp = client.get("/api/ai-learning/history/member_profile",
                      headers={"X-Role": "admin"})
    history_ok = resp.status_code == 200 and resp.json()["historyCount"] >= 2
    resp = client.get("/api/ai-learning/drift/member_profile",
                      headers={"X-Role": "admin"})
    drift_ok = resp.status_code == 200 and resp.json()["drift"]["count"] >= 1
    record("39_http_promote_reset_history_drift",
           promoted_ok and reset_ok and history_ok and drift_ok,
           f"promote={promoted_ok}, reset={reset_ok}, "
           f"history={history_ok}, drift={drift_ok}")

    resp = client.put("/api/ai-learning/config/member_profile",
                      headers={"X-Role": "admin"}, json={"eta": 10})
    record("40_http_config_invalid_eta_422",
           resp.status_code == 422, f"status={resp.status_code}")

    # ========================================================
    # 汇总
    # ========================================================
    print("-" * 64)
    for line in RESULTS:
        print(line)
    print("-" * 64)
    print(f"总计: {PASS} 通过, {FAIL} 失败")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
