"""AI 决策阻断引擎测试(v7.8: 三级模式/决策门/四重保护/审计统计, 22 项)

覆盖:
    - 模式解析(6): 默认 observe / 全局 shadow / 全局 enforce /
      scopes 域内 enforce+域外降 shadow / scopes=shadow 域外降 observe /
      非法模式值容错
    - observe 模式(2): 决策门放行不落审计 / 快照仍产生(反馈闭环保持)
    - shadow 模式(3): 决策门运行落审计 / block 动作不真实阻断 /
      审计记录含影子标记
    - enforce 集成(3): 高分 withdraw → 真实阻断 / 低分 → 放行 /
      review 档 → 强制人工
    - 四重保护(5): 冷启动降级(反馈<50) / 低正确率降级 /
      熔断降级(窗口阻断率>30%) / fail-open(评分异常放行) /
      未知评分器容错
    - 审计与统计(3): stats 累计正确 / 审计新→旧 / 熔断窗口计数
    - 概览与 HTTP(2): enforcement_overview 结构 /
      HTTP audit 端点 + 未知评分器 404

在宿主机运行(需已安装 fastapi + httpx):
    cd D:\\网站架构设计\\zhuxiang-jiu\\backend
    python test_ai_enforcement.py
"""

import asyncio
import os
import sys

os.environ["LOCK_MODE"] = "asyncio"
os.environ["STORE_MODE"] = "asyncio"
os.environ.setdefault("AUTH_MODE", "compat")
# 测试默认 observe, 各用例按需覆盖(记得恢复)
os.environ.pop("AI_ENFORCE_MODE", None)
os.environ.pop("AI_ENFORCE_SCOPES", None)

from repositories.ai_learning_repository import AiLearningRepository
from services import ai_enforcement as enf

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


def _fake_score(score: float, version: str = "vT") -> dict:
    """构造阈值类评分结果(patch _invoke_scorer 用, score 决定 action)"""
    return {
        "score": score,
        "weightVersion": version,
        "factors": [
            {"name": "amount_ratio", "score": score,
             "weight": 0.2, "contribution": round(score * 0.2, 1)},
        ],
    }


def _patch_score(score: float):
    """生成返回固定评分结果的 _invoke_scorer 替身"""
    async def _fake(sid, ctx):
        return _fake_score(score)
    return _fake


async def _seed_feedback(scorer_id: str, total: int, correct: int) -> None:
    """直接塞反馈记录(绕过服务层校验, 构造保护检查的前置状态)"""
    repo = AiLearningRepository()
    for i in range(total):
        await repo.add_feedback({
            "scorerId": scorer_id,
            "factors": [{"name": "amount_ratio", "score": 50,
                         "weight": 0.2, "contribution": 10}],
            "scoreAtDecision": 50,
            "actualAction": "ok",
            "expectedAction": "ok",
            "correct": i < correct,
            "source": "auto", "note": f"seed:{i}",
        })


async def main():
    print("=" * 64)
    print("AI 决策阻断引擎测试(三级模式 / 决策门 / 四重保护 / 审计统计)")
    print("=" * 64)
    repo = AiLearningRepository()

    # ========================================================
    # 1. 模式解析
    # ========================================================
    record("01_mode_defaults_observe", enf.enforcement_mode("order_risk")
           == "observe")

    os.environ["AI_ENFORCE_MODE"] = "shadow"
    record("02_mode_global_shadow", enf.enforcement_mode("order_risk")
           == "shadow")

    os.environ["AI_ENFORCE_MODE"] = "enforce"
    record("03_mode_global_enforce", enf.enforcement_mode("order_risk")
           == "enforce")

    os.environ["AI_ENFORCE_SCOPES"] = "withdraw_risk"
    record("04_mode_scopes_in_enforce_out_shadow",
           enf.enforcement_mode("withdraw_risk") == "enforce"
           and enf.enforcement_mode("order_risk") == "shadow")

    os.environ["AI_ENFORCE_MODE"] = "shadow"
    record("05_mode_scopes_shadow_out_observe",
           enf.enforcement_mode("withdraw_risk") == "shadow"
           and enf.enforcement_mode("order_risk") == "observe")

    os.environ["AI_ENFORCE_MODE"] = "bogus"
    os.environ.pop("AI_ENFORCE_SCOPES")
    record("06_mode_invalid_falls_back", enf.enforcement_mode("order_risk")
           == "observe")
    os.environ.pop("AI_ENFORCE_MODE")

    # ========================================================
    # 2. observe 模式(决策门 = v7.6 现状)
    # ========================================================
    res = await enf.enforce_decision(
        "order_risk", "order:OBS-1",
        {"bambooScore": 750, "registerHours": 720,
         "orderAmount": 199.0, "historyOrders": 10})
    audits = await repo.list_enforcement_audit("order_risk")
    record("07_observe_never_blocks_never_audits",
           res["mode"] == "observe" and not res["blocked"]
           and not res["reviewRequired"] and len(audits) == 0,
           f"res={res.get('mode')}, audits={len(audits)}")

    snap = await repo.get_decision_snapshot("order_risk", "order:OBS-1")
    record("08_observe_still_snapshots", snap is not None
           and snap.get("decision") in ("pass", "review", "block"),
           f"snap={snap and snap.get('decision')}")

    # ========================================================
    # 3. shadow 模式(patch 评分: score=70 → withdraw_risk high)
    # ========================================================
    os.environ["AI_ENFORCE_MODE"] = "shadow"
    from services import ai_feedback_hooks as hooks
    orig_invoke = hooks._invoke_scorer
    hooks._invoke_scorer = _patch_score(70)
    try:
        res = await enf.enforce_decision(
            "withdraw_risk", "withdraw:SH-1", {"amount": 100, "balance": 100})
        audits = await repo.list_enforcement_audit("withdraw_risk")
    finally:
        hooks._invoke_scorer = orig_invoke
    record("09_shadow_runs_gate_and_audits",
           res["mode"] == "shadow" and res["action"] == "high"
           and not res["blocked"] and len(audits) >= 1,
           f"mode={res.get('mode')}, action={res.get('action')}, "
           f"blocked={res.get('blocked')}")

    record("10_shadow_audit_marks_effective_mode",
           audits[0].get("effectiveMode") == "shadow"
           and audits[0].get("score") == 70
           and audits[0].get("weightVersion") == "vT"
           and bool(audits[0].get("businessKey")),
           f"audit0={audits[0] if audits else None}")

    # ========================================================
    # 4. enforce 集成(真实评分器, 先塞 50 条高正确率反馈过保护)
    # ========================================================
    await _seed_feedback("withdraw_risk", 50, 45)   # 正确率 90%
    enf._accuracy_cache.clear()
    os.environ["AI_ENFORCE_MODE"] = "enforce"
    os.environ["AI_ENFORCE_SCOPES"] = "withdraw_risk"

    # 高风险: 全额提现+高频+新账户+异常收益+历史驳回 → high → block
    res = await enf.enforce_decision(
        "withdraw_risk", "withdraw:ENF-HI",
        {"amount": 1000, "balance": 1000, "monthlyWithdrawCount": 10,
         "accountAgeDays": 1, "abnormalIncomeRatio": 0.9,
         "rejectedCount": 5, "accountFrozen": False,
         "identityVerified": False})
    record("11_enforce_high_risk_blocks",
           res["mode"] == "enforce" and res["effectiveMode"] == "enforce"
           and res["action"] == "high" and res["blocked"] is True,
           f"action={res.get('action')}, blocked={res.get('blocked')}, "
           f"effective={res.get('effectiveMode')}")

    # 低风险: 小额+低频+老账户 → low → 放行
    res = await enf.enforce_decision(
        "withdraw_risk", "withdraw:ENF-LO",
        {"amount": 100, "balance": 10000, "monthlyWithdrawCount": 0,
         "accountAgeDays": 365, "abnormalIncomeRatio": 0.0,
         "rejectedCount": 0, "accountFrozen": False,
         "identityVerified": True})
    record("12_enforce_low_risk_passes",
           res["action"] == "low" and not res["blocked"]
           and not res["reviewRequired"],
           f"action={res.get('action')}")

    # review 档(patch score=40 → medium → 强制人工)
    hooks._invoke_scorer = _patch_score(40)
    try:
        res = await enf.enforce_decision(
            "withdraw_risk", "withdraw:ENF-MID",
            {"amount": 100, "balance": 100})
    finally:
        hooks._invoke_scorer = orig_invoke
    record("13_enforce_medium_requires_review",
           res["action"] == "medium" and res["reviewRequired"] is True
           and res["blocked"] is False,
           f"action={res.get('action')}, review={res.get('reviewRequired')}")

    # ========================================================
    # 5. 四重保护
    # ========================================================
    # 5.1 冷启动: points_risk 无反馈 → enforce 降级 shadow
    os.environ["AI_ENFORCE_SCOPES"] = "withdraw_risk,points_risk"
    hooks._invoke_scorer = _patch_score(70)
    try:
        res = await enf.enforce_decision(
            "points_risk", "points:COLD-1", {"userId": "u1"})
    finally:
        hooks._invoke_scorer = orig_invoke
    record("14_cold_start_degrades",
           res["mode"] == "enforce" and res["effectiveMode"] == "shadow"
           and not res["blocked"] and res["degraded"] is True
           and str(res.get("degradeReason") or "").startswith("cold_start"),
           f"effective={res.get('effectiveMode')}, "
           f"reason={res.get('degradeReason')}")

    # 5.2 低正确率: order_risk 塞 50 条但只有 20 条正确(40%) → 降级
    await _seed_feedback("order_risk", 50, 20)
    enf._accuracy_cache.clear()
    os.environ["AI_ENFORCE_SCOPES"] = "withdraw_risk,order_risk"
    hooks._invoke_scorer = _patch_score(70)
    try:
        res = await enf.enforce_decision(
            "order_risk", "order:LOWACC-1",
            {"bambooScore": 750, "orderAmount": 100})
    finally:
        hooks._invoke_scorer = orig_invoke
    record("15_low_accuracy_degrades",
           res["effectiveMode"] == "shadow" and res["degraded"] is True
           and str(res.get("degradeReason") or "").startswith("low_accuracy"),
           f"reason={res.get('degradeReason')}")

    # 5.3 熔断: 窗口塞 10 total / 4 blocked(40%) → 降级
    for _ in range(4):
        await repo.incr_burst_window("withdraw_risk", "blocked")
    for _ in range(6):
        await repo.incr_burst_window("withdraw_risk", "total")
    hooks._invoke_scorer = _patch_score(70)
    try:
        res = await enf.enforce_decision(
            "withdraw_risk", "withdraw:BURST-1",
            {"amount": 100, "balance": 100})
    finally:
        hooks._invoke_scorer = orig_invoke
    record("16_burst_ratio_degrades",
           res["effectiveMode"] == "shadow"
           and str(res.get("degradeReason") or "").startswith("burst"),
           f"reason={res.get('degradeReason')}")

    # 5.4 fail-open: 评分器抛异常 → 放行+降级审计
    async def _boom(sid, ctx):
        raise RuntimeError("scorer exploded")
    hooks._invoke_scorer = _boom
    try:
        res = await enf.enforce_decision(
            "withdraw_risk", "withdraw:FAIL-1",
            {"amount": 100, "balance": 100})
    finally:
        hooks._invoke_scorer = orig_invoke
    record("17_fail_open_on_scorer_error",
           res["blocked"] is False and not res["reviewRequired"]
           and res["degraded"] is True
           and str(res.get("degradeReason") or "").startswith("fail_open"),
           f"reason={res.get('degradeReason')}")

    # 5.5 未知评分器: 返回 None → 放行(不抛异常)
    async def _none(sid, ctx):
        return None
    hooks._invoke_scorer = _none
    try:
        res = await enf.enforce_decision(
            "withdraw_risk", "withdraw:UNREG-1",
            {"amount": 100, "balance": 100})
    finally:
        hooks._invoke_scorer = orig_invoke
    record("18_unregistered_scorer_fails_open",
           res["blocked"] is False and res["degraded"] is True
           and res.get("degradeReason") == "scorer_unavailable",
           f"reason={res.get('degradeReason')}")

    # ========================================================
    # 6. 审计与统计
    # ========================================================
    stats = await repo.get_enforcement_stats("withdraw_risk")
    record("19_stats_accumulate",
           stats.get("total", 0) >= 6 and stats.get("blocked", 0) >= 1
           and stats.get("reviews", 0) >= 1
           and stats.get("degraded", 0) >= 3,
           f"stats={stats}")

    audits = await repo.list_enforcement_audit("withdraw_risk", limit=5)
    keys = {a.get("businessKey") for a in audits}
    record("20_audit_newest_first",
           len(audits) >= 5 and "withdraw:UNREG-1" in keys
           and audits[0].get("businessKey") == "withdraw:UNREG-1",
           f"first={audits[0].get('businessKey') if audits else None}")

    w_total = await repo.get_burst_window("withdraw_risk", "total")
    record("21_burst_window_counts_decisions",
           w_total >= 11,   # 预塞 10 + ENF 决策累计 ≥1
           f"window_total={w_total}")

    # ========================================================
    # 7. 概览与 HTTP
    # ========================================================
    ov = await enf.enforcement_overview("withdraw_risk")
    record("22_enforcement_overview_structure",
           ov.get("success") and ov.get("mode") == "enforce"
           and isinstance(ov.get("stats"), dict)
           and ov["stats"].get("blocked", 0) >= 1
           and isinstance(ov.get("burstWindow"), dict)
           and ov.get("protections", {}).get("coldStartMinFeedback") == 50,
           f"ov={ {k: ov.get(k) for k in ('mode', 'stats')} }")

    os.environ.pop("AI_ENFORCE_MODE")
    os.environ.pop("AI_ENFORCE_SCOPES")
    try:
        from fastapi.testclient import TestClient
        from main import app
    except ImportError:
        print("  [SKIP] 23_http_audit_endpoint -- 沙箱无 fastapi, 宿主机可跑")
    else:
        client = TestClient(app)
        resp = client.get("/api/ai-learning/enforcement/withdraw_risk/audit",
                          headers={"X-Role": "admin"})
        body = resp.json() if resp.status_code == 200 else {}
        resp404 = client.get(
            "/api/ai-learning/enforcement/no_such_scorer/audit",
            headers={"X-Role": "admin"})
        record("23_http_audit_endpoint",
               resp.status_code == 200 and body.get("success")
               and body.get("count", 0) >= 1
               and isinstance(body.get("records"), list)
               and resp404.status_code == 404,
               f"code={resp.status_code}, n={body.get('count')}, "
               f"404={resp404.status_code}")

    # ========================================================
    # 汇总
    # ========================================================
    print("\n".join(RESULTS))
    print("=" * 64)
    print(f"总计: {PASS} 通过, {FAIL} 失败")
    print("=" * 64)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys_exit = asyncio.run(main())
    raise SystemExit(sys_exit)
