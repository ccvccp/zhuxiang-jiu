"""AI 语义评分层·第三批测试(认证风控评分, Service 层 + HTTP 层, 25 项)

覆盖 AuthRiskScorer:
    决策分级(allow/step_up/challenge/block) / 硬约束拦截(黑名单IP/泄露密码)
    / 地理速度(不可能行程) / 因子明细一致性 / 置信度 / ValueError 边界
    + HTTP 层(4): 403 鉴权 / 200 正常 / 422 参数校验 / 409 业务冲突

在宿主机运行(需已安装 fastapi + httpx):
    cd D:\\网站架构设计\\zhuxiang-jiu\\backend
    $env:LOCK_MODE="asyncio"; $env:STORE_MODE="asyncio"; $env:AUTH_MODE="compat"
    python test_ai_scoring_auth_routes.py
"""

import asyncio
import os
import sys

os.environ["LOCK_MODE"] = "asyncio"
os.environ["STORE_MODE"] = "asyncio"
os.environ.setdefault("AUTH_MODE", "compat")

from fastapi.testclient import TestClient

from main import app
from services.ai_scoring_auth_service import AuthRiskScorer

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
    except Exception as exc:  # noqa: BLE001
        record(name, False, f"异常类型错误: {type(exc).__name__}: {exc}")


def _factor(result, name):
    return next(x for x in result["factors"] if x["name"] == name)


async def main():
    scorer = AuthRiskScorer()

    print("=" * 64)
    print("AI 语义评分层·第三批测试(认证风控评分: 8因子 + 4级决策 + 硬约束拦截)")
    print("=" * 64)

    # ========================================================
    # 1. 决策分级
    # ========================================================
    r = await scorer.score({
        "failedAttempts": 0, "distanceKm": 5, "hoursSinceLastLogin": 24,
        "newDevice": False, "ipRiskType": "clean", "loginHour": 14,
        "accountAgeDays": 730, "passwordStatus": "strong",
        "behaviorDeviationScore": 5,
    })
    record("01_low_risk_allow",
           r["action"] == "allow" and r["score"] < 25 and not r["hardBlocked"],
           f"score={r['score']}, action={r['action']}")

    r = await scorer.score({
        "failedAttempts": 12, "distanceKm": 1500, "hoursSinceLastLogin": 1,
        "newDevice": True, "ipRiskType": "tor", "loginHour": 3,
        "accountAgeDays": 2, "passwordStatus": "weak",
        "behaviorDeviationScore": 95,
    })
    record("02_high_risk_block",
           r["action"] == "block" and r["score"] >= 70,
           f"score={r['score']}, action={r['action']}")

    r = await scorer.score({
        "failedAttempts": 0, "newDevice": True, "ipRiskType": "proxy",
        "loginHour": 10, "accountAgeDays": 365, "passwordStatus": "weak",
    })
    record("03_medium_step_up",
           r["action"] == "step_up" and 25 <= r["score"] < 50,
           f"score={r['score']}, action={r['action']}")

    r = await scorer.score({
        "failedAttempts": 2, "distanceKm": 100, "hoursSinceLastLogin": 10,
        "newDevice": True, "ipRiskType": "tor", "loginHour": 3,
        "accountAgeDays": 30, "passwordStatus": "medium",
        "behaviorDeviationScore": 60,
    })
    record("04_high_medium_challenge",
           r["action"] == "challenge" and 50 <= r["score"] < 70,
           f"score={r['score']}, action={r['action']}")

    # ========================================================
    # 2. 硬约束拦截(规则引擎兜底)
    # ========================================================
    r = await scorer.score({
        "failedAttempts": 0, "newDevice": False, "ipRiskType": "blacklist",
        "loginHour": 14, "accountAgeDays": 730, "passwordStatus": "strong",
    })
    record("05_hard_block_blacklist_ip",
           r["hardBlocked"] and r["action"] == "block" and r["score"] < 25
           and "黑名单" in "、".join(r["hardBlockReasons"]),
           f"score={r['score']}, action={r['action']}, reasons={r['hardBlockReasons']}")

    r = await scorer.score({
        "failedAttempts": 0, "newDevice": False, "ipRiskType": "clean",
        "loginHour": 14, "accountAgeDays": 730, "passwordStatus": "breached",
    })
    record("06_hard_block_breached_password",
           r["hardBlocked"] and r["action"] == "block" and r["score"] < 25
           and "泄露" in "、".join(r["hardBlockReasons"]),
           f"score={r['score']}, action={r['action']}, reasons={r['hardBlockReasons']}")

    # ========================================================
    # 3. 因子行为
    # ========================================================
    r = await scorer.score({
        "failedAttempts": 0, "distanceKm": 1200, "hoursSinceLastLogin": 1,
        "newDevice": False, "ipRiskType": "clean", "loginHour": 14,
        "accountAgeDays": 365, "passwordStatus": "strong",
    })
    geo = _factor(r, "geo_velocity")
    record("07_geo_impossible_travel",
           geo["score"] == 100.0 and "km/h" in geo["detail"],
           f"geo={geo['score']}, detail={geo['detail']}")

    r = await scorer.score({
        "failedAttempts": 0, "distanceKm": 5, "hoursSinceLastLogin": 24,
        "newDevice": False, "ipRiskType": "clean", "loginHour": 14,
        "accountAgeDays": 365, "passwordStatus": "strong",
    })
    geo = _factor(r, "geo_velocity")
    record("08_geo_normal_same_city",
           geo["score"] == 0.0, f"geo={geo['score']}, detail={geo['detail']}")

    r = await scorer.score({
        "failedAttempts": 0, "newDevice": True, "ipRiskType": "clean",
        "loginHour": 14, "accountAgeDays": 3, "passwordStatus": "strong",
    })
    age = _factor(r, "account_age")
    record("09_new_account_factor",
           age["score"] >= 96 and "账龄 3" in age["detail"],
           f"age={age['score']}, detail={age['detail']}")

    r = await scorer.score({
        "failedAttempts": 0, "newDevice": True, "ipRiskType": "clean",
        "loginHour": 14, "accountAgeDays": 365, "passwordStatus": "strong",
        "behaviorDeviationScore": 80,
    })
    beh = _factor(r, "behavior_deviation")
    record("10_behavior_deviation_direct",
           beh["score"] == 80.0, f"behavior={beh['score']}")

    r = await scorer.score({
        "failedAttempts": 0, "ipRiskType": "clean", "loginHour": 14,
        "accountAgeDays": 365, "passwordStatus": "strong",
    })
    dev = _factor(r, "device_match")
    record("11_device_unknown_neutral",
           dev["score"] == 50.0 and "未知" in dev["detail"],
           f"device={dev['score']}, detail={dev['detail']}")

    # ========================================================
    # 4. 一致性与置信度
    # ========================================================
    r = await scorer.score({
        "failedAttempts": 3, "distanceKm": 300, "hoursSinceLastLogin": 2,
        "newDevice": True, "ipRiskType": "vpn", "loginHour": 23,
        "accountAgeDays": 45, "passwordStatus": "medium",
        "behaviorDeviationScore": 40,
    })
    total = sum(x["contribution"] for x in r["factors"])
    record("12_factor_consistency",
           abs(total - r["score"]) < 0.5 and len(r["factors"]) == 8,
           f"sum={total}, score={r['score']}, factors={len(r['factors'])}")

    r = await scorer.score({
        "failedAttempts": 0, "newDevice": False, "ipRiskType": "clean",
        "loginHour": 14,
    })
    record("13_confidence_full_required",
           r["confidence"] == 1.0, f"confidence={r['confidence']}")

    r = await scorer.score({"loginHour": 14})
    record("14_confidence_missing",
           r["confidence"] < 1.0 and r["confidence"] >= 0.3,
           f"confidence={r['confidence']}")

    r = await scorer.score({
        "failedAttempts": 0, "newDevice": False, "ipRiskType": "clean",
    })
    record("15_sparse_input_still_allow",
           r["action"] == "allow" and r["score"] < 25,
           f"score={r['score']}, action={r['action']}(无风险信号时放行, 置信度低)")

    r = await scorer.score({
        "failedAttempts": 0, "newDevice": False, "ipRiskType": "clean",
        "loginHour": 10,
    })
    record("16_action_detail_mapping",
           r["action"] == "allow" and "双令牌" in r["actionDetail"],
           f"action={r['action']}, detail={r['actionDetail']}")

    r = await scorer.score({
        "failedAttempts": 0, "newDevice": True, "ipRiskType": "proxy",
        "loginHour": 10, "accountAgeDays": 365, "passwordStatus": "weak",
    })
    record("17_step_up_action_detail",
           r["action"] == "step_up" and "验证码" in r["actionDetail"],
           f"action={r['action']}, detail={r['actionDetail']}")

    # ========================================================
    # 5. ValueError 边界
    # ========================================================
    await _expect_value_error(
        "18_negative_failed_attempts",
        scorer.score({"failedAttempts": -1}))

    await _expect_value_error(
        "19_negative_distance",
        scorer.score({"distanceKm": -10, "hoursSinceLastLogin": 1}))

    await _expect_value_error(
        "20_unknown_ip_risk_type",
        scorer.score({"ipRiskType": "zombie"}))

    await _expect_value_error(
        "21_unknown_password_status",
        scorer.score({"passwordStatus": "super_strong"}))

    # ========================================================
    # 6. HTTP 层
    # ========================================================
    client = TestClient(app)
    ADMIN = {"X-Role": "admin"}

    r = client.post("/api/ai-scoring/auth-risk",
                    json={"failedAttempts": 0, "newDevice": False,
                          "ipRiskType": "clean"})
    record("22_http_requires_admin_role",
           r.status_code == 403, f"status={r.status_code}")

    r = client.post("/api/ai-scoring/auth-risk", headers=ADMIN,
                    json={"failedAttempts": 1, "newDevice": True,
                          "ipRiskType": "proxy", "loginHour": 10,
                          "accountAgeDays": 365, "passwordStatus": "weak"})
    b = r.json()
    record("23_http_normal_200",
           r.status_code == 200 and b["success"] and b["scorer"] == "auth_risk"
           and b["action"] in ("allow", "step_up", "challenge", "block")
           and b["modelVersion"] == "v1-auth",
           f"status={r.status_code}, body={str(b)[:120]}")

    r = client.post("/api/ai-scoring/auth-risk", headers=ADMIN,
                    json={"failedAttempts": -1})
    record("24_http_negative_attempts_422",
           r.status_code == 422, f"status={r.status_code}")

    r = client.post("/api/ai-scoring/auth-risk", headers=ADMIN,
                    json={"ipRiskType": "zombie"})
    record("25_http_unknown_ip_type_409",
           r.status_code == 409 and "IP" in r.json().get("error", ""),
           f"status={r.status_code}, error={r.json().get('error')}")


if __name__ == "__main__":
    asyncio.run(main())
    print()
    print("-" * 64)
    print("\n".join(RESULTS))
    print("-" * 64)
    print(f"总计: {PASS + FAIL}  通过: {PASS}  失败: {FAIL}")
    sys.exit(1 if FAIL else 0)
