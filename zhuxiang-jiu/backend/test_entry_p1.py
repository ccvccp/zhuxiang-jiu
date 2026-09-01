"""39号·AI智能网站入口管理模块 P1 专项测试(生物凭证+决策回流+落地页)

运行:
    $env:LOCK_MODE="asyncio"; $env:STORE_MODE="asyncio"
    python test_entry_p1.py

覆盖(P1, 设计文档 §7):
    1. 生物凭证两段协议(9): enroll挑战/bind摘要红线/凭证上限/
        challenge一次性/verify断言派生/令牌签发/吊销失效/
        非本人吊销404/strict拒mock
    2. 决策回流(5):  confirm回流/误拦false_block/幂等409/
        未知决策404/HTTP admin复核
    3. 角色落地页(6): hub chips/连登计数幂等/断签归一/
        里程碑口径/问候分档/landing端点
    4. HTTP路由(5):  bio端点鉴权矩阵/enroll须登录/挑战公开/
        凭证清单/吊销
"""

import asyncio
import hashlib
import os

os.environ["LOCK_MODE"] = "asyncio"
os.environ["STORE_MODE"] = "asyncio"
os.environ.pop("LLM_API_KEY", None)
os.environ["LLM_ENABLED"] = "off"
os.environ["ENTRY_BIO_MODE"] = "mock"

from services.entry_service import EntryService
from repositories.entry_repository import EntryRepository
from repositories.ai_learning_repository import AiLearningRepository
from repositories.store import reset_store as _reset_store_impl

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


def reset_store():
    _reset_store_impl()


async def _expect(exc_type, coro, keyword=""):
    try:
        await coro
        return False, ""
    except exc_type as exc:
        return (not keyword or keyword in str(exc)), str(exc)
    except Exception as exc:
        return False, f"非预期异常 {type(exc).__name__}: {exc}"


_phone_seq = [500]


async def _add_member() -> tuple[int, str]:
    _phone_seq[0] += 1
    from services.auth_service import AuthService
    result = await AuthService().register(
        phone=f"135{_phone_seq[0]:08d}", password="Test1234!",
        nickname="P1测试", age_confirmed=True)
    return int(result["memberId"]), result["phone"]


async def main():
    reset_store()
    svc = EntryService()
    repo = EntryRepository()
    learning_repo = AiLearningRepository()

    mid, phone = await _add_member()
    dv = "DV_P1_TEST_DEVICE_01"

    # ========================================================
    # 1. 生物凭证两段协议
    # ========================================================
    print("\n========== 1. 生物凭证两段协议 ==========")

    # 非法类型
    ok, msg = await _expect(
        ValueError, svc.bio_enroll(mid, "iris", dv))
    record("非法生物类型409", ok, msg)

    enroll = await svc.bio_enroll(mid, "fingerprint", dv)
    record("enroll生成设备挑战",
           enroll["enrollChallenge"].startswith("BC")
           and enroll["challengeTtl"] == 60
           and "不上送" in enroll["hint"], f"实际{enroll}")

    # bind 摘要红线: 只存 publicKeyHash, 无原始生物数据
    pkh = hashlib.sha256(b"device-public-key-p1").hexdigest()[:32]
    cred = await svc.bio_bind(mid, "fingerprint", dv,
                              enroll["enrollChallenge"], pkh,
                              "我的指纹")
    record("bind登记摘要凭证",
           cred["credentialId"].startswith("BIO")
           and cred["publicKeyHash"] == pkh
           and cred["status"] == "active"
           and cred["mode"] == "mock", f"实际{cred}")

    ok, msg = await _expect(
        ValueError, svc.bio_bind(mid, "fingerprint", dv, "",
                                 pkh))
    record("重复凭证绑定409", ok, msg)

    # 凭证数上限(再绑4个到5, 第6个拒)
    for i in range(4):
        e = await svc.bio_enroll(mid, "face", f"{dv}{i}")
        await svc.bio_bind(mid, "face", f"{dv}{i}",
                           e["enrollChallenge"],
                           hashlib.sha256(f"pk{i}".encode())
                           .hexdigest()[:32])
    ok, msg = await _expect(
        ValueError, svc.bio_enroll(mid, "fingerprint", dv))
    record("凭证上限5个409", ok, msg)

    # challenge → verify(Mock 确定性派生)
    ch = await svc.bio_challenge(cred["credentialId"])
    assertion = hashlib.sha256(
        (ch["assertionChallenge"] + dv).encode()).hexdigest()[:32]
    result = await svc.bio_verify(cred["credentialId"], assertion,
                                  ip="127.0.0.1")
    record("verify断言通过签发令牌",
           result["status"] in ("authenticated",
                                "step_up_required")
           and (result.get("tokens") or {}).get("accessToken"),
           f"实际{result.get('status')}")

    # 挑战一次性(已消费)
    ok, msg = await _expect(
        ValueError, svc.bio_verify(cred["credentialId"], assertion))
    record("挑战一次性消费", ok, msg)

    # 错误断言
    ch2 = await svc.bio_challenge(cred["credentialId"])
    ok, msg = await _expect(
        ValueError, svc.bio_verify(
            cred["credentialId"], "WRONG_ASSERTION_HASH_XXXX"))
    record("错误断言409", ok, msg)

    # 吊销后失效
    revoked = await svc.bio_revoke(mid, cred["credentialId"])
    record("吊销幂等留痕",
           revoked["status"] == "revoked")
    ok, msg = await _expect(
        ValueError, svc.bio_challenge(cred["credentialId"]))
    record("吊销凭证挑战409", ok, msg)

    # 非本人吊销
    mid_other, _ = await _add_member()
    ok, msg = await _expect(
        KeyError, svc.bio_revoke(mid_other, cred["credentialId"]))
    record("非本人吊销404", ok, msg)

    # strict 模式拒 mock 凭证
    os.environ["ENTRY_BIO_MODE"] = "strict"
    try:
        cred2 = None
        # 新绑一个 mock 凭证在 strict 下验证
        e = await svc.bio_enroll(mid, "face", f"{dv}s")
        # strict 只影响 verify, enroll/bind 不拦
        cred2 = await svc.bio_bind(
            mid, "face", f"{dv}s", e["enrollChallenge"],
            hashlib.sha256(b"strict-pk").hexdigest()[:32])
        ch3 = await svc.bio_challenge(cred2["credentialId"])
        a3 = hashlib.sha256(
            (ch3["assertionChallenge"] + f"{dv}s").encode()
        ).hexdigest()[:32]
        ok, msg = await _expect(
            ValueError, svc.bio_verify(
                cred2["credentialId"], a3))
        record("strict模式拒Mock凭证409", ok, msg)
    finally:
        os.environ["ENTRY_BIO_MODE"] = "mock"

    # ========================================================
    # 2. 决策回流(第 21 档案 auth_risk)
    # ========================================================
    print("\n========== 2. 决策回流 ==========")

    d = await svc.guard(mid, "password", fingerprint="", ip="127.0.0.1")
    did = d["decisionId"]
    r = await svc.review_decision(did, "confirm")
    record("confirm回流correct=True",
           r.get("correct") is True, f"实际{r.get('correct')}")
    feedbacks = await learning_repo.list_feedback("auth_risk",
                                                  limit=10)
    record("auth_risk反馈落库",
           len(feedbacks) >= 1
           and feedbacks[0].get("source") == "entry",
           f"实际{len(feedbacks)}条")

    ok, msg = await _expect(
        ValueError, svc.review_decision(did, "confirm"))
    record("重复复核409幂等", ok, msg)

    d2 = await svc.guard(mid, "password", ip="10.0.0.9")
    r2 = await svc.review_decision(d2["decisionId"], "false_block")
    record("误拦false_block回流correct=False",
           r2.get("correct") is False, f"实际{r2.get('correct')}")

    ok, msg = await _expect(
        KeyError, svc.review_decision(99999, "confirm"))
    record("未知决策404", ok, msg)

    ok, msg = await _expect(
        ValueError, svc.review_decision(
            (await svc.guard(mid, "password",
                             ip="10.0.0.8"))["decisionId"],
            "乱裁"))
    record("非法裁决409", ok, msg)

    # ========================================================
    # 3. 角色落地页
    # ========================================================
    print("\n========== 3. 角色落地页 ==========")

    landing = await svc.landing("member", mid)
    record("member落地页hub chips",
           len(landing["chips"]) >= 4
           and any(c.get("id") == "alliance.scene"
                   for c in landing["chips"]),
           f"实际{[c.get('id') for c in landing['chips']]}")

    n1 = await svc.touch_login_streak(mid)
    n2 = await svc.touch_login_streak(mid)
    record("连登计数当日幂等", n1 == n2 == 1,
           f"实际{n1}/{n2}")

    # 断签归一(改 lastSeenAt 为 3 天前)
    key = f"streak:{mid}"
    rec = await repo.get_fingerprint(key)
    from datetime import datetime, UTC, timedelta
    await repo.save_fingerprint(key, {
        **rec, "lastSeenAt": (datetime.now(UTC)
                              - timedelta(days=3)).isoformat(),
        "seenCount": 10})
    n3 = await svc.touch_login_streak(mid)
    record("断签归一重计", n3 == 1, f"实际{n3}")

    # 昨日连续 → +1
    await repo.save_fingerprint(key, {
        **rec, "lastSeenAt": (datetime.now(UTC)
                              - timedelta(days=1)).isoformat(),
        "seenCount": 6})
    n4 = await svc.touch_login_streak(mid)
    record("昨日连续加一", n4 == 7, f"实际{n4}")

    ms = svc._streak_reward(7)
    record("7天里程碑50分",
           ms["points"] == 50 and ms["day"] == 7, f"实际{ms}")
    ms30 = svc._streak_reward(35)
    record("30天里程碑300分", ms30["points"] == 300,
           f"实际{ms30}")

    landing = await svc.landing("member", mid)
    record("落地页问候含连登天数",
           str(landing["loginStreak"]) in landing["greeting"],
           f"实际{landing['greeting']}")

    landing_admin = await svc.landing("admin")
    record("admin落地页有chips",
           len(landing_admin["chips"]) >= 1,
           f"实际{len(landing_admin['chips'])}")

    # ========================================================
    # 4. HTTP 路由
    # ========================================================
    print("\n========== 4. HTTP路由 ==========")

    from fastapi.testclient import TestClient
    from main import app
    client = TestClient(app)
    headers = {"X-Member-Id": str(mid)}
    admin_h = {"X-Role": "admin"}

    r = client.post("/api/entry/bio/enroll",
                    json={"bioType": "fingerprint",
                          "deviceId": "DV_HTTP"})
    record("HTTP enroll未登录401", r.status_code == 401,
           f"实际{r.status_code}")

    # 新会员(凭证未满)验证 enroll 登录态链路
    mid_fresh, _ = await _add_member()
    r = client.post("/api/entry/bio/enroll",
                    json={"bioType": "fingerprint",
                          "deviceId": "DV_HTTP"},
                    headers={"X-Member-Id": str(mid_fresh)})
    record("HTTP enroll登录态200", r.status_code == 200,
           f"实际{r.status_code}")

    r = client.post("/api/entry/bio/challenge",
                    json={"credentialId": cred["credentialId"]})
    record("HTTP challenge白名单公开",
           r.status_code in (200, 409),
           f"实际{r.status_code}")  # 已吊销凭证 → 409 也算协议通

    r = client.get("/api/entry/bio/credentials", headers=headers)
    record("HTTP 凭证清单200",
           r.status_code == 200
           and r.json()["count"] >= 5, f"实际{r.status_code}")

    r = client.post("/api/entry/decisions/99999/review",
                    json={"verdict": "confirm"}, headers=admin_h)
    record("HTTP复核未知决策404", r.status_code == 404,
           f"实际{r.status_code}")

    r = client.get("/api/entry/landing?role=member&memberId="
                   + str(mid))
    record("HTTP landing公开200",
           r.status_code == 200
           and r.json()["data"]["chips"], f"实际{r.status_code}")

    print("\n" + "=" * 62)
    for line in RESULTS:
        print(line)
    print("=" * 62)
    print(f"总计: {PASS} 通过 / {FAIL} 失败")
    return FAIL


if __name__ == "__main__":
    import sys
    sys.exit(asyncio.run(main()) and 1 or 0)
