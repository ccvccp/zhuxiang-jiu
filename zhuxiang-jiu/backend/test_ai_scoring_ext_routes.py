"""AI 语义评分层·第二批测试(Service 层 + HTTP 层, 51 项)

覆盖 8 个评分器:
    MemberProfileScorer(7) / PointsRiskScorer(5) / MessageContentScorer(5)
    WithdrawRiskScorer(5) / GroupbuyQualifyScorer(5) / AdminOperationScorer(4)
    AgreementRiskScorer(4) / FinanceAnomalyScorer(4)
    + HTTP 层(12): 8 端点 200 全通过 / 403 鉴权 / 422 参数校验 / 409 业务冲突

在宿主机运行(需已安装 fastapi + httpx):
    cd D:\\网站架构设计\\zhuxiang-jiu\\backend
    $env:LOCK_MODE="asyncio"; $env:STORE_MODE="asyncio"; $env:AUTH_MODE="compat"
    python test_ai_scoring_ext_routes.py
"""

import asyncio
import os
import sys

os.environ["LOCK_MODE"] = "asyncio"
os.environ["STORE_MODE"] = "asyncio"
os.environ.setdefault("AUTH_MODE", "compat")

from fastapi.testclient import TestClient

from main import app
from services.ai_scoring_ext_service import (
    MemberProfileScorer, PointsRiskScorer, MessageContentScorer,
    WithdrawRiskScorer, GroupbuyQualifyScorer, AdminOperationScorer,
    AgreementRiskScorer, FinanceAnomalyScorer,
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


async def _expect_value_error(name, coro):
    try:
        await coro
        record(name, False, "未抛出 ValueError")
    except ValueError:
        record(name, True)
    except Exception as exc:
        record(name, False, f"异常类型错误: {type(exc).__name__}: {exc}")


async def main():
    profile = MemberProfileScorer()
    points = PointsRiskScorer()
    message = MessageContentScorer()
    withdraw = WithdrawRiskScorer()
    groupbuy = GroupbuyQualifyScorer()
    admin = AdminOperationScorer()
    agreement = AgreementRiskScorer()
    finance = FinanceAnomalyScorer()

    print("=" * 64)
    print("AI 语义评分层·第二批测试(8 评分器: 会员画像/积分风控/内容审核/提现风控/团购资格/后台操作/合同风险/财务异常)")
    print("=" * 64)

    # ========================================================
    # 1. 会员智能画像评分(02 会员管理)
    # ========================================================
    r = await profile.score({
        "profileFieldCount": 8, "profileFieldTotal": 8, "accountAgeDays": 730,
        "monthlyLogins": 20, "monthlyConsumption": 6000, "repurchaseRate": 0.6,
        "refundRate": 0.0, "bambooScore": 850,
    })
    record("01_profile_high_value",
           r["tier"] == "high_value" and r["score"] >= 70 and r["success"],
           f"score={r['score']}, tier={r['tier']}")

    r = await profile.score({
        "profileFieldCount": 1, "profileFieldTotal": 8, "accountAgeDays": 5,
        "monthlyLogins": 1, "monthlyConsumption": 0, "repurchaseRate": 0.0,
        "refundRate": 0.5, "bambooScore": 200,
    })
    record("02_profile_at_risk",
           r["tier"] == "at_risk" and r["score"] < 40 and "挽留" in r["action"],
           f"score={r['score']}, tier={r['tier']}")

    r = await profile.score({
        "profileFieldCount": 4, "profileFieldTotal": 8, "accountAgeDays": 180,
        "monthlyLogins": 8, "monthlyConsumption": 2000, "repurchaseRate": 0.3,
        "refundRate": 0.1, "bambooScore": 600,
    })
    record("03_profile_standard",
           r["tier"] == "standard" and 40 <= r["score"] < 70,
           f"score={r['score']}, tier={r['tier']}")

    r = await profile.score({
        "monthlyLogins": 20, "monthlyConsumption": 6000, "bambooScore": 850,
    })
    total = sum(x["contribution"] for x in r["factors"])
    record("04_profile_factor_consistency",
           abs(total - r["score"]) < 0.5 and len(r["factors"]) == 7,
           f"sum={total}, score={r['score']}, factors={len(r['factors'])}")

    await _expect_value_error(
        "05_profile_negative_consumption",
        profile.score({"monthlyConsumption": -100}))

    r = await profile.score({
        "monthlyLogins": 20, "monthlyConsumption": 6000, "bambooScore": 850,
    })
    record("06_profile_confidence_full", r["confidence"] == 1.0,
           f"confidence={r['confidence']}")

    r = await profile.score({"bambooScore": 700})
    record("07_profile_confidence_missing",
           r["confidence"] < 1.0 and r["confidence"] >= 0.3,
           f"confidence={r['confidence']}")

    # ========================================================
    # 2. 积分防薅羊毛评分(03 会员积分)
    # ========================================================
    r = await points.score({
        "todayEarned": 20, "dailyRedeemCount": 1, "singleChannelRatio": 0.3,
        "sameDeviceAccounts": 1, "violationCount": 0, "nightActionRatio": 0.0,
    })
    record("08_points_low_risk_pass",
           r["level"] == "low" and "正常" in r["action"] and r["score"] < 30,
           f"score={r['score']}, level={r['level']}")

    r = await points.score({
        "todayEarned": 500, "dailyRedeemCount": 6, "singleChannelRatio": 0.9,
        "sameDeviceAccounts": 4, "violationCount": 2, "nightActionRatio": 0.5,
    })
    record("09_points_high_risk_block",
           r["level"] == "high" and "冻结" in r["action"] and r["score"] >= 60,
           f"score={r['score']}, level={r['level']}")

    r = await points.score({
        "todayEarned": 180, "dailyRedeemCount": 2, "singleChannelRatio": 0.5,
        "sameDeviceAccounts": 2, "violationCount": 0, "nightActionRatio": 0.1,
    })
    record("10_points_medium_review",
           r["level"] == "medium" and 30 <= r["score"] < 60,
           f"score={r['score']}, level={r['level']}")

    await _expect_value_error(
        "11_points_negative_earned",
        points.score({"todayEarned": -5, "sameDeviceAccounts": 1}))

    r = await points.score({"todayEarned": 30})
    record("12_points_confidence_missing",
           r["confidence"] < 1.0 and r["confidence"] >= 0.3,
           f"confidence={r['confidence']}")

    # ========================================================
    # 3. 信息内容审核评分(08 信息管理)
    # ========================================================
    r = await message.score({
        "content": "本月会员日活动通知,全场竹香酒满300减50,数量有限先到先得。",
        "hourlySendCount": 1, "sendHour": 10,
    })
    record("13_message_low_risk_pass",
           r["level"] == "low" and "放行" in r["action"] and r["score"] < 30,
           f"score={r['score']}, level={r['level']}")

    r = await message.score({
        "content": "代开发票加微信办理 http://a.com http://b.com http://c.com 兼职日结",
        "duplicateRatio": 0.7, "hourlySendCount": 12, "sendHour": 3,
    })
    sensitive = next(x for x in r["factors"] if x["name"] == "sensitive_words")
    record("14_message_high_risk_autodetect",
           r["level"] == "high" and "拦截" in r["action"] and sensitive["score"] >= 66,
           f"score={r['score']}, level={r['level']}, sensitive={sensitive['score']}")

    r = await message.score({
        "content": "提醒:平台不办理代开发票业务,谨防上当",
        "linkCount": 2, "duplicateRatio": 0.4, "hourlySendCount": 4, "sendHour": 10,
    })
    record("15_message_medium_review",
           r["level"] == "medium" and 30 <= r["score"] < 60,
           f"score={r['score']}, level={r['level']}")

    await _expect_value_error(
        "16_message_empty_content",
        message.score({"content": ""}))

    r = await message.score({"content": "正常内容", "sensitiveHitCount": 3})
    sensitive = next(x for x in r["factors"] if x["name"] == "sensitive_words")
    record("17_message_explicit_hits_override",
           sensitive["score"] == 99.0,
           f"sensitive={sensitive['score']}(显式 3 处 × 33)")

    # ========================================================
    # 4. 提现风控评分(12 钱包盈利)
    # ========================================================
    r = await withdraw.score({
        "amount": 100, "balance": 5000, "monthlyWithdrawCount": 1,
        "accountAgeDays": 365, "abnormalIncomeRatio": 0.0, "rejectedCount": 0,
        "accountFrozen": False, "identityVerified": True,
    })
    record("18_withdraw_low_auto_approve",
           r["level"] == "low" and "自动打款" in r["action"] and r["score"] < 25,
           f"score={r['score']}, level={r['level']}")

    r = await withdraw.score({
        "amount": 5000, "balance": 5000, "monthlyWithdrawCount": 6,
        "accountAgeDays": 5, "abnormalIncomeRatio": 0.6, "rejectedCount": 2,
        "accountFrozen": True,
    })
    record("19_withdraw_high_freeze",
           r["level"] == "high" and "冻结" in r["action"] and r["score"] >= 55,
           f"score={r['score']}, level={r['level']}")

    r = await withdraw.score({
        "amount": 2000, "balance": 5000, "monthlyWithdrawCount": 2,
        "accountAgeDays": 30, "abnormalIncomeRatio": 0.2,
        "identityVerified": False,
    })
    record("20_withdraw_medium_manual_review",
           r["level"] == "medium" and "人工审核" in r["action"],
           f"score={r['score']}, level={r['level']}")

    await _expect_value_error(
        "21_withdraw_over_balance",
        withdraw.score({"amount": 100, "balance": 50}))

    await _expect_value_error(
        "22_withdraw_zero_amount",
        withdraw.score({"amount": 0, "balance": 50}))

    # ========================================================
    # 5. 团购资格评分(14 团购模块)
    # ========================================================
    r = await groupbuy.score({
        "qualificationDocs": 5, "annualPurchaseAmount": 80000,
        "onTimePaymentRatio": 1.0, "violationCount": 0, "targetQuantity": 100,
    })
    record("23_groupbuy_t3_core",
           r["tier"] == "T3" and r["score"] >= 80 and "核心" in r["tierName"],
           f"score={r['score']}, tier={r['tier']}")

    r = await groupbuy.score({
        "qualificationDocs": 0, "annualPurchaseAmount": 0,
        "onTimePaymentRatio": 0.3, "violationCount": 2, "targetQuantity": 5,
    })
    record("24_groupbuy_rejected",
           r["tier"] == "rejected" and r["score"] < 40,
           f"score={r['score']}, tier={r['tier']}")

    r = await groupbuy.score({
        "qualificationDocs": 3, "annualPurchaseAmount": 30000,
        "onTimePaymentRatio": 0.8, "violationCount": 0, "targetQuantity": 30,
    })
    record("25_groupbuy_t2_advanced",
           r["tier"] == "T2" and 60 <= r["score"] < 80,
           f"score={r['score']}, tier={r['tier']}")

    r = await groupbuy.score({
        "qualificationDocs": 2, "annualPurchaseAmount": 10000,
        "onTimePaymentRatio": 0.6, "violationCount": 1, "targetQuantity": 20,
    })
    record("26_groupbuy_t1_basic",
           r["tier"] == "T1" and 40 <= r["score"] < 60,
           f"score={r['score']}, tier={r['tier']}")

    await _expect_value_error(
        "27_groupbuy_invalid_quantity",
        groupbuy.score({"qualificationDocs": 3, "targetQuantity": 0}))

    # ========================================================
    # 6. 后台操作风险评分(17 后台管理)
    # ========================================================
    r = await admin.score({
        "operationType": "read", "operationHour": 14, "isWeekend": False,
        "operationsLast10Min": 2, "operatesOnSelf": False, "hasSecondReviewer": True,
    })
    record("28_admin_low_allow",
           r["level"] == "low" and "直接执行" in r["action"] and r["score"] < 30,
           f"score={r['score']}, level={r['level']}")

    r = await admin.score({
        "operationType": "delete", "operationHour": 3, "isWeekend": False,
        "operationsLast10Min": 12, "operatesOnSelf": True, "hasSecondReviewer": False,
    })
    record("29_admin_high_block",
           r["level"] == "high" and "拦截" in r["action"] and r["score"] >= 60,
           f"score={r['score']}, level={r['level']}")

    r = await admin.score({
        "operationType": "update", "isWeekend": True,
        "operationsLast10Min": 5, "operatesOnSelf": False,
    })
    record("30_admin_medium_confirm_2fa",
           r["level"] == "medium" and "二次确认" in r["action"],
           f"score={r['score']}, level={r['level']}")

    await _expect_value_error(
        "31_admin_unknown_operation",
        admin.score({"operationType": "drop_table"}))

    # ========================================================
    # 7. 合同条款风险评分(18 条款规则合同)
    # ========================================================
    r = await agreement.score({
        "exemptionClauseCount": 0, "penaltyRatio": 0.1, "unilateralClauseCount": 0,
        "jurisdictionType": "court_standard",
        "presentKeyClauses": ["交付条款", "付款条款", "违约责任", "争议解决", "保密条款"],
    })
    record("32_agreement_low_risk",
           r["level"] == "low" and r["score"] < 30 and "标准用印" in r["action"],
           f"score={r['score']}, level={r['level']}")

    r = await agreement.score({
        "exemptionClauseCount": 4, "penaltyRatio": 0.5, "unilateralClauseCount": 3,
        "jurisdictionType": "unilateral_far",
        "missingKeyClauses": ["交付条款", "付款条款"],
    })
    record("33_agreement_high_risk_suggestions",
           r["level"] == "high" and r["score"] >= 60
           and len(r["revisionSuggestions"]) >= 4 and "法务介入" in r["action"],
           f"score={r['score']}, level={r['level']}, suggestions={len(r['revisionSuggestions'])}")

    r = await agreement.score({
        "exemptionClauseCount": 1, "penaltyRatio": 0.2, "unilateralClauseCount": 1,
        "jurisdictionType": "arbitration",
        "presentKeyClauses": ["交付条款", "付款条款"],
    })
    missing = next(x for x in r["factors"] if x["name"] == "missing_clauses")
    record("34_agreement_medium_autodiff_missing",
           r["level"] == "medium" and "缺失 3 项" in missing["detail"],
           f"score={r['score']}, level={r['level']}, missing_detail={missing['detail']}")

    await _expect_value_error(
        "35_agreement_negative_penalty",
        agreement.score({"exemptionClauseCount": 1, "penaltyRatio": -0.1}))

    # ========================================================
    # 8. 财务异常检测评分(19 财务管理)
    # ========================================================
    r = await finance.score({
        "amount": 1000, "accountAverageAmount": 950, "summaryMatchScore": 95,
        "entryHour": 10, "isWeekend": False, "entriesToday": 5,
        "dailyAverageEntries": 10, "unbalanceAmount": 0,
    })
    record("36_finance_normal_auto_post",
           r["level"] == "low" and "自动过账" in r["action"] and r["score"] < 25,
           f"score={r['score']}, level={r['level']}")

    r = await finance.score({
        "amount": 50000, "accountAverageAmount": 1000, "summaryMatchScore": 10,
        "entryHour": 2, "entriesToday": 100, "dailyAverageEntries": 10,
        "unbalanceAmount": 500,
    })
    record("37_finance_alert_freeze",
           r["level"] == "high" and "冻结" in r["action"] and r["score"] >= 50,
           f"score={r['score']}, level={r['level']}")

    r = await finance.score({
        "amount": 2500, "accountAverageAmount": 1000, "entryHour": 14,
        "entriesToday": 20, "dailyAverageEntries": 10, "unbalanceAmount": 0,
    })
    record("38_finance_attention_review",
           r["level"] == "medium" and "复核" in r["action"] and 25 <= r["score"] < 50,
           f"score={r['score']}, level={r['level']}")

    await _expect_value_error(
        "39_finance_negative_amount",
        finance.score({"amount": -100, "accountAverageAmount": 100}))

    # ========================================================
    # 9. HTTP 层(8 端点全栈: 经 JWT 中间件 + 路由层)
    # ========================================================
    client = TestClient(app)
    ADMIN = {"X-Role": "admin"}

    # 403 鉴权(带合法 body, 确保命中权限校验而非参数校验)
    r = client.post("/api/ai-scoring/member-profile",
                    json={"monthlyLogins": 15, "monthlyConsumption": 3000})
    record("40_http_member_profile_403",
           r.status_code == 403, f"status={r.status_code}")

    r = client.post("/api/ai-scoring/finance-anomaly", json={"amount": 800},
                    headers={"X-Role": "member"})
    record("41_http_finance_anomaly_403",
           r.status_code == 403, f"status={r.status_code}")

    # 200 正常调用(8 端点逐一)
    r = client.post("/api/ai-scoring/member-profile", headers=ADMIN,
                    json={"monthlyLogins": 15, "monthlyConsumption": 3000,
                          "bambooScore": 700})
    b = r.json()
    record("42_http_member_profile_200",
           r.status_code == 200 and b["success"] and b["scorer"] == "member_profile"
           and "tier" in b and b["modelVersion"] == "v1-ext",
           f"status={r.status_code}, body={str(b)[:120]}")

    r = client.post("/api/ai-scoring/points-risk", headers=ADMIN,
                    json={"todayEarned": 30, "sameDeviceAccounts": 1})
    b = r.json()
    record("43_http_points_risk_200",
           r.status_code == 200 and b["success"] and b["scorer"] == "points_risk"
           and "level" in b,
           f"status={r.status_code}, body={str(b)[:120]}")

    r = client.post("/api/ai-scoring/message-content", headers=ADMIN,
                    json={"content": "新品上市通知:竹香酒·十年陈酿今日开售"})
    b = r.json()
    record("44_http_message_content_200",
           r.status_code == 200 and b["success"] and b["scorer"] == "message_content"
           and "level" in b,
           f"status={r.status_code}, body={str(b)[:120]}")

    r = client.post("/api/ai-scoring/withdraw-risk", headers=ADMIN,
                    json={"amount": 200, "balance": 1000})
    b = r.json()
    record("45_http_withdraw_risk_200",
           r.status_code == 200 and b["success"] and b["scorer"] == "withdraw_risk"
           and "level" in b,
           f"status={r.status_code}, body={str(b)[:120]}")

    r = client.post("/api/ai-scoring/groupbuy-qualify", headers=ADMIN,
                    json={"targetQuantity": 30, "qualificationDocs": 3,
                          "annualPurchaseAmount": 30000})
    b = r.json()
    record("46_http_groupbuy_qualify_200",
           r.status_code == 200 and b["success"] and b["scorer"] == "groupbuy_qualify"
           and "tier" in b,
           f"status={r.status_code}, body={str(b)[:120]}")

    r = client.post("/api/ai-scoring/admin-operation", headers=ADMIN,
                    json={"operationType": "update", "operationHour": 14})
    b = r.json()
    record("47_http_admin_operation_200",
           r.status_code == 200 and b["success"] and b["scorer"] == "admin_operation"
           and "level" in b,
           f"status={r.status_code}, body={str(b)[:120]}")

    r = client.post("/api/ai-scoring/agreement-risk", headers=ADMIN,
                    json={"exemptionClauseCount": 1, "penaltyRatio": 0.1,
                          "presentKeyClauses": ["交付条款", "付款条款", "违约责任",
                                                "争议解决", "保密条款"]})
    b = r.json()
    record("48_http_agreement_risk_200",
           r.status_code == 200 and b["success"] and b["scorer"] == "agreement_risk"
           and "revisionSuggestions" in b,
           f"status={r.status_code}, body={str(b)[:120]}")

    r = client.post("/api/ai-scoring/finance-anomaly", headers=ADMIN,
                    json={"amount": 800, "accountAverageAmount": 900})
    b = r.json()
    record("49_http_finance_anomaly_200",
           r.status_code == 200 and b["success"] and b["scorer"] == "finance_anomaly"
           and "level" in b,
           f"status={r.status_code}, body={str(b)[:120]}")

    # 422 参数校验(Pydantic ge=1 拦截)
    r = client.post("/api/ai-scoring/groupbuy-qualify", headers=ADMIN,
                    json={"targetQuantity": 0, "qualificationDocs": 3})
    record("50_http_groupbuy_invalid_quantity_422",
           r.status_code == 422, f"status={r.status_code}")

    # 409 业务冲突(提现超余额 → ValueError 映射, 全局异常格式 error 字段)
    r = client.post("/api/ai-scoring/withdraw-risk", headers=ADMIN,
                    json={"amount": 100, "balance": 50})
    record("51_http_withdraw_over_balance_409",
           r.status_code == 409 and "余额" in r.json().get("error", ""),
           f"status={r.status_code}, error={r.json().get('error')}")


if __name__ == "__main__":
    asyncio.run(main())
    print()
    print("-" * 64)
    print("\n".join(RESULTS))
    print("-" * 64)
    print(f"总计: {PASS + FAIL}  通过: {PASS}  失败: {FAIL}")
    sys.exit(1 if FAIL else 0)
