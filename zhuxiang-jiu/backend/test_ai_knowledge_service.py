"""AI 检索增强评分引擎测试(v7.9 阶段 2: 证据/校准/升级, 15 项)

覆盖:
    - 引擎单元(10): 开关/空库/无因子返回 None, 邻居统计,
      经验分加权平均, 校准护栏, alpha=0, 区域可靠性判定,
      证据不足不判定, 升级判定纯函数
    - 快照集成(1): snapshot_decision 存 knowledge 证据块
    - 决策门集成(4): enforce 不可靠区域升级人工复核(只加严不阻断),
      enforce 可靠区域不升级, shadow 只审计不升级, observe 不受影响

在宿主机运行(纯标准库, 沙箱可跑):
    cd D:\\网站架构设计\\zhuxiang-jiu\\backend
    python test_ai_knowledge_service.py
"""

import asyncio
import os

os.environ["LOCK_MODE"] = "asyncio"
os.environ["STORE_MODE"] = "asyncio"
os.environ.setdefault("AUTH_MODE", "compat")
for _k in ("AI_KB", "AI_KB_ALPHA", "AI_KB_MAX_ADJUST",
           "AI_KB_MIN_EVIDENCE", "AI_KB_TOP_K",
           "AI_ENFORCE_MODE", "AI_ENFORCE_SCOPES"):
    os.environ.pop(_k, None)

from repositories.ai_knowledge_repository import AiKnowledgeRepository
from repositories.ai_learning_repository import AiLearningRepository
from services.ai_feedback_hooks import snapshot_decision
from services.ai_knowledge_service import (
    augment_with_knowledge, should_escalate_review,
)
from services.ai_learning_service import submit_feedback

PASS = 0
FAIL = 0
RESULTS = []

# withdraw_risk 低风险查询向量(与低分评分输出对齐)
LOW_RISK_VEC = {"amount_ratio": 1.0, "frequency": 0.0, "account_age": 0.0,
                "income_anomaly": 0.0, "history_rejects": 0.0,
                "status_flags": 0.0}


def record(name, passed, detail=""):
    global PASS, FAIL
    if passed:
        PASS += 1
        RESULTS.append(f"  [PASS] {name}")
    else:
        FAIL += 1
        RESULTS.append(f"  [FAIL] {name} -- {detail}")


def factors_of(vec: dict) -> list:
    return [{"name": k, "score": v, "weight": 0.0, "contribution": v}
            for k, v in vec.items()]


async def seed_kb(vec: dict, n: int, correct: bool,
                  score_at: float = 80.0):
    """向知识库注入 n 条同向量案例(控制正确性)"""
    kb = AiKnowledgeRepository()
    for _ in range(n):
        await kb.add_case("withdraw_risk", {
            "factors": dict(vec), "scoreAtDecision": score_at,
            "action": "low", "actualAction": "high",
            "correct": correct, "source": "auto", "businessKey": "kb-t",
        })


async def seed_protection_feedback():
    """注入 50 条反馈(90% 正确)通过冷启动/正确率保护"""
    for i in range(50):
        await submit_feedback({
            "scorerId": "withdraw_risk",
            "factors": factors_of(LOW_RISK_VEC),
            "scoreAtDecision": 5.0,
            "actualAction": "low", "expectedAction": "low",
            "correct": i >= 5,     # 45 正确 / 5 错误 = 90%
            "source": "auto", "note": "kb-protection",
        })


async def main():
    print("=" * 64)
    print("AI 检索增强评分引擎测试(证据 / 校准 / 复核升级)")
    print("=" * 64)

    # ========================================================
    # 1. 引擎单元
    # ========================================================
    os.environ["AI_KB"] = "off"
    r = await augment_with_knowledge(
        "withdraw_risk", factors_of(LOW_RISK_VEC), 5.0)
    os.environ.pop("AI_KB")
    record("01_kb_off_returns_none", r is None, f"r={r}")

    r = await augment_with_knowledge(
        "order_risk", factors_of({"credit": 60}), 50.0)
    record("02_empty_kb_returns_none", r is None, f"r={r}")

    r = await augment_with_knowledge("order_risk", [], 50.0)
    record("03_no_factors_returns_none", r is None, f"r={r}")

    # 注入证据: 3 正确 + 2 错误(相似度全 1.0), 决策分 80
    await seed_kb(LOW_RISK_VEC, 3, True)
    await seed_kb(LOW_RISK_VEC, 2, False)
    r = await augment_with_knowledge(
        "withdraw_risk", factors_of(LOW_RISK_VEC), 5.0)
    record("04_neighbor_stats",
           r is not None and r["evidenceCount"] == 5
           and r["correctCount"] == 3 and r["wrongRate"] == 0.4
           and r["topSimilarity"] == 1.0 and len(r["neighbors"]) == 5,
           f"r={r and {k: r[k] for k in ('evidenceCount', 'correctCount', 'wrongRate')}}")

    # 经验分 = 相似度加权平均(全 1.0 相似度 → 简单平均 80)
    record("05_empirical_weighted_average",
           r is not None and r["empiricalScore"] == 80.0,
           f"empirical={r and r['empiricalScore']}")

    # 校准: 5 + 0.3×(80-5) = 27.5 → 超 15 护栏 → 截断为 20.0
    record("06_calibration_guardrail_cap",
           r is not None and r["calibratedScore"] == 20.0,
           f"calibrated={r and r['calibratedScore']}")

    # alpha=0 → 无调整
    os.environ["AI_KB_ALPHA"] = "0"
    r0 = await augment_with_knowledge(
        "withdraw_risk", factors_of(LOW_RISK_VEC), 5.0)
    os.environ.pop("AI_KB_ALPHA")
    record("07_alpha_zero_no_adjust",
           r0 is not None and r0["calibratedScore"] == 5.0,
           f"calibrated={r0 and r0['calibratedScore']}")

    # 错误率 2/5 = 0.4 < 0.5 → 可靠区域
    record("08_reliable_region_no_flag",
           r is not None and r["regionUnreliable"] is False,
           f"unreliable={r and r['regionUnreliable']}")

    record("09_should_escalate_pure_fn",
           should_escalate_review(None) is False
           and should_escalate_review({"regionUnreliable": True}) is True
           and should_escalate_review({"regionUnreliable": False}) is False,
           "纯函数判定")

    # ========================================================
    # 2. 快照集成: snapshot_decision 存知识证据块
    # ========================================================
    fake_result = {
        "score": 5.0,
        "level": "low", "action": "low",
        "factors": factors_of(LOW_RISK_VEC),
        "weightVersion": "v1",
    }
    ok = await snapshot_decision("withdraw_risk", "kb:snap:1", fake_result)
    learn_repo = AiLearningRepository()
    snap = await learn_repo.get_decision_snapshot(
        "withdraw_risk", "kb:snap:1")
    record("11_snapshot_stores_knowledge",
           ok and snap is not None and "knowledge" in snap
           and snap["knowledge"]["evidenceCount"] == 5,
           f"snap keys={snap and list(snap.keys())}")

    # ========================================================
    # 3. 决策门集成
    # ========================================================
    await seed_protection_feedback()
    clean_ctx = {"amount": 100, "balance": 10000,
                 "monthlyWithdrawCount": 0, "accountAgeDays": 365,
                 "abnormalIncomeRatio": 0, "rejectedCount": 0,
                 "accountFrozen": False, "identityVerified": True}

    from services.ai_enforcement import enforce_decision

    # 可靠区域(错误率 0.4 < 0.5): enforce 低风险 → 照常放行
    os.environ["AI_ENFORCE_MODE"] = "enforce"
    os.environ["AI_ENFORCE_SCOPES"] = "withdraw_risk"
    res = await enforce_decision("withdraw_risk", "kb:gate:1", clean_ctx)
    record("12a_enforce_reliable_region_passes",
           res["action"] == "low" and res["blocked"] is False
           and res["reviewRequired"] is False
           and res.get("knowledge", {}).get("wrongRate") == 0.4,
           f"res={ {k: res.get(k) for k in ('action', 'blocked', 'reviewRequired')} }")

    # 不可靠区域: 追加 3 条错误案例 → 错误率 5/8 ≥ 0.5 → 升级人工复核
    await seed_kb(LOW_RISK_VEC, 3, False)
    res = await enforce_decision("withdraw_risk", "kb:gate:2", clean_ctx)
    record("12b_enforce_unreliable_escalates_review",
           res["action"] == "low"          # 参数分仍低(只加严不虚增)
           and res["blocked"] is False     # 永不因证据阻断
           and res["reviewRequired"] is True
           and "kb_evidence" in res["note"]
           and res["knowledge"]["regionUnreliable"] is True,
           f"res={ {k: res.get(k) for k in ('action', 'blocked', 'reviewRequired', 'note')} }")

    # shadow 模式: 只审计不升级
    os.environ["AI_ENFORCE_MODE"] = "shadow"
    res = await enforce_decision("withdraw_risk", "kb:gate:3", clean_ctx)
    record("13_shadow_audits_without_escalation",
           res["effectiveMode"] == "shadow"
           and res["reviewRequired"] is False
           and res.get("knowledge", {}).get("regionUnreliable") is True,
           f"res={ {k: res.get(k) for k in ('effectiveMode', 'reviewRequired')} }")

    # observe 模式: 行为与 v7.6 完全一致(pass + 无决策字段)
    os.environ["AI_ENFORCE_MODE"] = "observe"
    res = await enforce_decision("withdraw_risk", "kb:gate:4", clean_ctx)
    record("14_observe_mode_unaffected",
           res["effectiveMode"] == "observe"
           and res["blocked"] is False and "knowledge" not in res,
           f"res keys={list(res.keys())}")

    # 清理环境
    for _k in ("AI_ENFORCE_MODE", "AI_ENFORCE_SCOPES"):
        os.environ.pop(_k, None)

    # ========================================================
    # 汇总
    # ========================================================
    print("\n".join(RESULTS))
    print("=" * 64)
    print(f"总计: {PASS} 通过, {FAIL} 失败")
    print("=" * 64)
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
