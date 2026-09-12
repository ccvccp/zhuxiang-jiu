"""69号·AI智能支付大模型 P4 专项测试
(生物特征——FIDO 挑战/活体意图/模板账本)

运行方式:
    python test_pay69_p4.py

覆盖(69号规划 §七 P4):
    - 生物字典: 方式/结果域封闭+胁迫
      线索表+阈值+铁律公示
    - FIDO 挑战: 一次性(GETDEL 防重放)
      +方式域外 409
    - 验证结果四态: verified/degraded/
      failed/challenge_expired
    - 活体意图(胁迫判定): 特征匹配但
      胁迫线索≥0.60→degraded 静默降级
      密码+人工留痕; 特征不匹配→failed
      优先于胁迫; 挑战重放→expired
    - 胁迫计分确定性: 权重叠加封顶/
      单项不足阈值不降级
    - 模板账本: 首登记版本 1/顺序+1
      增量/版本回滚拒绝/超上限拒绝/
      服务端仅版本+哈希(原始特征
      永不上传)
    - 置信度低→补充验证建议(不自动
      降档)
    - 模式矩阵: 决策面 off 409/
      观测面 off 200
"""

import asyncio
import os
import sys

os.environ["LOCK_MODE"] = "asyncio"
os.environ["STORE_MODE"] = "asyncio"
os.environ.pop("LLM_API_KEY", None)
os.environ["LLM_ENABLED"] = "off"
os.environ["QR55_MODE"] = "off"
os.environ["PAY60_MODE"] = "off"
os.environ["PAY69_MODE"] = "off"

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


def set_mode(m: str):
    os.environ["PAY69_MODE"] = m


async def main():
    from repositories.store import reset_store
    reset_store()

    from fastapi.testclient import TestClient
    from main import app
    client = TestClient(app)
    ADMIN = {"X-Role": "admin"}
    BASE = "/api/pay69"

    from services import pay69_registry as reg
    from services.pay69_biometric_service import (
        Pay69BiometricService,
    )
    svc = Pay69BiometricService()

    print("[01 生物字典与注册封闭]")

    r = client.get(f"{BASE}/biometric/dict", headers=ADMIN)
    body = r.json()
    record("生物字典 200",
           r.status_code == 200, f"s={r.status_code}")
    record("方式域封闭(face/fingerprint)",
           body["methods"] == [
               "face", "fingerprint"])
    record("结果域四态封闭",
           body["results"] == [
               "verified", "degraded",
               "failed",
               "challenge_expired"])
    record("胁迫线索表五项",
           len(body["coercionSigns"]) == 5)
    record("胁迫阈值 0.60",
           body["coercionThreshold"] == 0.60)
    record("挑战 TTL 120 秒",
           body["challengeTtl"] == 120)
    record("铁律公示(端侧存储/安全自动留痕/不做资金操作)",
           len(body["ironRules"]) == 3)
    record("启动自检通过(P4 扩展)",
           reg._validate_registry() is None)

    print("[02 胁迫计分确定性]")

    record("单线索: 面部僵硬 0.45",
           svc.coerce_score(
               ["facial_stiffness"]) == 0.45)
    record("双线索叠加: 0.45+0.40=0.85",
           svc.coerce_score(
               ["facial_stiffness",
                "voice_tremor"]) == 0.85)
    record("未知线索不计分",
           svc.coerce_score(["junk"]) == 0.0)
    record("全线索封顶 1.0",
           svc.coerce_score(
               list(reg.COERCION_SIGNS))
           == 1.0)
    record("单线索 0.45<0.60 不降级阈值",
           svc.coerce_score(
               ["facial_stiffness"])
           < reg.COERCION_THRESHOLD)
    record("双线索 0.85≥0.60 触发降级",
           svc.coerce_score(
               ["facial_stiffness",
                "voice_tremor"])
           >= reg.COERCION_THRESHOLD)

    print("[03 FIDO 挑战与验证四态(shadow)]")

    set_mode("shadow")
    try:
        # 正常链: 挑战→验证通过
        r = client.post(f"{BASE}/biometric/challenge", headers=ADMIN, json={
            "memberId": 1, "method": "face"})
        b = r.json()
        record("挑战发起 200+hex32",
               r.status_code == 200
               and len(b["challenge"]) == 32
               and b["ttlSeconds"] == 120)
        ch1 = b["challenge"]

        r = client.post(f"{BASE}/biometric/verify", headers=ADMIN, json={
            "memberId": 1, "challenge": ch1,
            "match": True})
        b = r.json()
        record("验证通过 verified",
               b["result"] == "verified"
               and b["match"] is True
               and b["coerceScore"] == 0.0,
               f"r={b['result']}")

        # 重放: 同一挑战二次消费→expired
        r = client.post(f"{BASE}/biometric/verify", headers=ADMIN, json={
            "memberId": 1, "challenge": ch1,
            "match": True})
        record("挑战重放→challenge_expired",
               r.json()["result"]
               == "challenge_expired"
               and r.json()[
                   "challengeValid"] is False)

        # 特征不匹配→failed(优先于胁迫)
        r = client.post(f"{BASE}/biometric/challenge", headers=ADMIN, json={
            "memberId": 1, "method": "face"})
        ch2 = r.json()["challenge"]
        r = client.post(f"{BASE}/biometric/verify", headers=ADMIN, json={
            "memberId": 1, "challenge": ch2,
            "match": False,
            "signs": ["facial_stiffness",
                       "voice_tremor"]})
        b = r.json()
        record("特征不匹配→failed(优先胁迫)",
               b["result"] == "failed"
               and b["match"] is False
               and b["coerceScore"] == 0.85)

        # 胁迫: 匹配但双线索≥阈值→degraded
        r = client.post(f"{BASE}/biometric/challenge", headers=ADMIN, json={
            "memberId": 2, "method": "face"})
        ch3 = r.json()["challenge"]
        r = client.post(f"{BASE}/biometric/verify", headers=ADMIN, json={
            "memberId": 2, "challenge": ch3,
            "match": True,
            "signs": ["facial_stiffness",
                       "voice_tremor"]})
        b = r.json()
        record("胁迫→degraded 静默降级",
               b["result"] == "degraded"
               and b["match"] is True
               and b["coerceScore"] == 0.85,
               f"r={b['result']}")
        record("降级指向 password+manual",
               b["degradedTo"]
               == "password+manual")

        # 单线索不足阈值→仍 verified
        r = client.post(f"{BASE}/biometric/challenge", headers=ADMIN, json={
            "memberId": 2, "method": "face"})
        ch4 = r.json()["challenge"]
        r = client.post(f"{BASE}/biometric/verify", headers=ADMIN, json={
            "memberId": 2, "challenge": ch4,
            "match": True,
            "signs": ["delayed_response"]})
        record("单线索不足阈值→verified",
               r.json()["result"] == "verified"
               and r.json()["coerceScore"]
               == 0.25)

        # 不存在挑战→expired
        r = client.post(f"{BASE}/biometric/verify", headers=ADMIN, json={
            "memberId": 1,
            "challenge": "nonexistent",
            "match": True})
        record("不存在挑战→expired",
               r.json()["result"]
               == "challenge_expired")

        # 指纹方式
        r = client.post(f"{BASE}/biometric/challenge", headers=ADMIN, json={
            "memberId": 3, "method": "fingerprint"})
        ch5 = r.json()["challenge"]
        r = client.post(f"{BASE}/biometric/verify", headers=ADMIN, json={
            "memberId": 3, "challenge": ch5,
            "match": True})
        record("指纹方式验证通过",
               r.json()["method"]
               == "fingerprint"
               and r.json()["result"]
               == "verified")

        # 方式域外
        r = client.post(f"{BASE}/biometric/challenge", headers=ADMIN, json={
            "memberId": 1, "method": "iris"})
        record("方式域外 409",
               r.status_code == 409,
               f"s={r.status_code}")
    finally:
        set_mode("off")

    print("[04 端侧模板版本账本(shadow)]")

    set_mode("shadow")
    try:
        # 首登记
        r = client.post(f"{BASE}/biometric/template/register", headers=ADMIN, json={
            "memberId": 1, "method": "face",
            "confidenceHash":
                "abc123def456",
            "confidence": 0.95})
        b = r.json()
        record("首登记版本 1",
               r.status_code == 200
               and b["version"] == 1
               and b["confidenceHash"]
               == "abc123def456")

        # 顺序增量
        r = client.post(f"{BASE}/biometric/template/register", headers=ADMIN, json={
            "memberId": 1, "method": "face",
            "templateVersion": 2,
            "confidence": 0.96})
        record("顺序增量版本 2",
               r.status_code == 200
               and r.json()["version"] == 2)

        # 版本回滚→拒绝
        r = client.post(f"{BASE}/biometric/template/register", headers=ADMIN, json={
            "memberId": 1, "method": "face",
            "templateVersion": 2})
        record("版本重复(回滚) 409",
               r.status_code == 409,
               f"s={r.status_code}")
        r = client.post(f"{BASE}/biometric/template/register", headers=ADMIN, json={
            "memberId": 1, "method": "face",
            "templateVersion": 5})
        record("跳版 409(须顺序+1)",
               r.status_code == 409,
               f"s={r.status_code}")

        # 首登记非 1
        r = client.post(f"{BASE}/biometric/template/register", headers=ADMIN, json={
            "memberId": 9, "method": "face",
            "templateVersion": 3})
        record("首登记非 1 409",
               r.status_code == 409,
               f"s={r.status_code}")

        # 缺省版本=自动+1
        r = client.post(f"{BASE}/biometric/template/register", headers=ADMIN, json={
            "memberId": 1, "method": "face"})
        record("缺省版本自动+1(=3)",
               r.status_code == 200
               and r.json()["version"] == 3)

        # 置信度低→建议事件(不自动降档)
        r = client.post(f"{BASE}/biometric/template/register", headers=ADMIN, json={
            "memberId": 1, "method": "face",
            "confidence": 0.50})
        record("低置信登记 200(建议不拦截)",
               r.status_code == 200
               and r.json()["confidence"]
               == 0.50)

        # 指纹独立账本
        r = client.post(f"{BASE}/biometric/template/register", headers=ADMIN, json={
            "memberId": 1, "method": "fingerprint"})
        record("指纹独立账本(版本 1)",
               r.json()["version"] == 1)

        # 方式域外
        r = client.post(f"{BASE}/biometric/template/register", headers=ADMIN, json={
            "memberId": 1, "method": "iris"})
        record("模板方式域外 409",
               r.status_code == 409,
               f"s={r.status_code}")
    finally:
        set_mode("off")

    print("[05 模板视图(原始特征永不上传)]")

    r = client.get(f"{BASE}/biometric/template/1", headers=ADMIN)
    b = r.json()
    record("模板视图 200+已登记",
           r.status_code == 200
           and b["registered"] is True)
    record("rawDataStored=False 铁律",
           b["rawDataStored"] is False)
    record("账本仅版本+哈希(无特征字段)",
           set(b["template"])
           <= {"memberId", "method",
               "version",
               "confidenceHash",
               "confidence",
               "registeredAt"},
           str(set(b["template"])))

    r = client.get(f"{BASE}/biometric/template/99", headers=ADMIN)
    record("未登记视图(registered False)",
           r.json()["registered"] is False)

    print("[06 事件留痕与模式矩阵]")

    r = client.get(
        f"{BASE}/biometric/events?memberId=2",
        headers=ADMIN)
    b = r.json()
    record("事件双口径过滤(member2)",
           r.status_code == 200
           and b["count"] == 2
           and all(
               x["memberId"] == 2
               for x in b["events"]),
           f"n={b['count']}")
    degraded_events = [
        e for e in b["events"]
        if e["result"] == "degraded"]
    record("degraded 事件留痕含降级指向",
           degraded_events
           and degraded_events[0]
           ["degradedTo"]
           == "password+manual")

    r = client.get(f"{BASE}/biometric/events", headers=ADMIN)
    record("全局事件在库(≥7)",
           r.json()["count"] >= 7,
           f"n={r.json()['count']}")

    r = client.post(f"{BASE}/biometric/challenge", headers=ADMIN, json={
        "memberId": 1})
    record("挑战 off 409(决策面)",
           r.status_code == 409,
           f"s={r.status_code}")
    r = client.post(f"{BASE}/biometric/verify", headers=ADMIN, json={
        "memberId": 1, "challenge": "x",
        "match": True})
    record("验证 off 409(决策面)",
           r.status_code == 409,
           f"s={r.status_code}")
    r = client.post(f"{BASE}/biometric/template/register", headers=ADMIN, json={
        "memberId": 1})
    record("模板登记 off 409(决策面)",
           r.status_code == 409,
           f"s={r.status_code}")

    for ep in ("biometric/dict",
               "biometric/events",
               "biometric/template/1"):
        r = client.get(f"{BASE}/{ep}", headers=ADMIN)
        record(f"观测面 {ep} off 200",
               r.status_code == 200,
               f"s={r.status_code}")

    r = client.get(f"{BASE}/biometric/dict")
    record("生物字典无 admin 403",
           r.status_code == 403,
           f"s={r.status_code}")

    total = PASS + FAIL
    print("-" * 62)
    print(f"总计: {PASS} 通过 / {FAIL} 失败"
          f" (共 {total})")
    print("-" * 62)
    if FAIL:
        for line in RESULTS:
            if "✗" in line:
                print(line)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
