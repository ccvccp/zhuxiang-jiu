"""69号·AI智能支付大模型 P5 专项测试
(情境智能码——生成/核销/对账/统计)

运行方式:
    python test_pay69_p5.py

覆盖(69号规划 §七 P5):
    - 情境码字典: 风险因素表+挑战阈值
      +状态域+TTL+55号口径
    - 情境风险确定性: 夜间单因 0.35 不
      挑战/双因 0.65 挑战/三因封顶/
      正常午间 0.0
    - 码生成: ZXBJ-QR55:pay69-smart
      前缀+水印 6 位+推荐方式亲和+
      优惠组合金额门控+55号签名链
    - 水印确定性: 同输入同水印/异
      nonce 异水印
    - 核销: 正常核销/重放拒绝/非归属
      商户拒绝/篡改码/过期码/非69号
      域码 404
    - 商户对账摘要: 聚合正确+nonce
      去重(状态更新不重复计数)
    - 情境统计: 因素命中率×挑战率
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
    from services.pay69_smartcode_service import (
        Pay69SmartcodeService,
    )
    svc = Pay69SmartcodeService()

    print("[01 情境码字典与注册封闭]")

    r = client.get(f"{BASE}/smartcode/dict", headers=ADMIN)
    body = r.json()
    record("情境码字典 200",
           r.status_code == 200, f"s={r.status_code}")
    record("风险因素三项",
           set(body["contextRisks"]) == {
               "night_hours", "new_device",
               "remote_location"})
    record("挑战阈值 0.50",
           body["challengeThreshold"] == 0.50)
    record("状态域三态封闭",
           body["statuses"] == [
               "generated", "redeemed",
               "expired"])
    record("TTL 300 秒+55号 serviceId",
           body["ttlSeconds"] == 300
           and body["serviceId"]
           == "pay69-smart")
    record("水印长度 6",
           body["watermarkLength"] == 6)
    record("铁律公示(主动触发/确定性"
           "水印/55号零改动)",
           len(body["ironRules"]) == 3)
    record("启动自检通过(P5 扩展)",
           reg._validate_registry() is None)

    print("[02 情境风险确定性]")

    risk = svc.context_risk(3, False, False)
    record("夜间单因 0.35 不挑战",
           risk["score"] == 0.35
           and risk["factors"]
           == ["night_hours"]
           and risk["challengeRequired"]
           is False)
    risk = svc.context_risk(3, True, False)
    record("夜+新设备 0.65 挑战",
           risk["score"] == 0.65
           and risk["challengeRequired"]
           is True)
    risk = svc.context_risk(3, True, True)
    record("三因叠加封顶 0.90 挑战",
           risk["score"] == 0.90
           and risk["challengeRequired"]
           is True)
    risk = svc.context_risk(12, False, False)
    record("正常午间 0.0 无挑战",
           risk["score"] == 0.0
           and risk["factors"] == []
           and risk["challengeRequired"]
           is False)
    risk = svc.context_risk(23, True, False)
    record("23 点非夜间(仅新设备 0.30)",
           risk["score"] == 0.30
           and "night_hours"
           not in risk["factors"])

    print("[03 码生成(shadow)]")

    set_mode("shadow")
    codes = []
    try:
        r = client.post(f"{BASE}/smartcode/generate", headers=ADMIN, json={
            "memberId": 1, "merchantId": 10,
            "amount": 299.0,
            "productType": "竹香经典",
            "hour": 12,
            "tags": ["fast_needed"]})
        b = r.json()
        record("正常生成 200",
               r.status_code == 200
               and b["status"] == "generated",
               f"s={r.status_code}")
        record("55号码前缀",
               b["fullCode"].startswith(
                   "ZXBJ-QR55:pay69-smart:"))
        record("水印 6 位大写十六进制",
               len(b["watermark"]) == 6
               and all(
                   c in "0123456789ABCDEF"
                   for c in b["watermark"]))
        record("推荐方式 fast→wechat",
               b["recommendedChannel"]
               == "wechat")
        # 优惠组合在码内参数(载荷解码验证)
        from services.qr55_crypto import (
            decode_payload,
        )
        payload_seg = b["fullCode"]\
            .split(":")[2].split(".")[0]
        params = decode_payload(
            payload_seg)["params"]
        record("金额≥100 优惠组合 random5(码内)",
               params.get("promoCombo")
               == "random5")
        record("码内推荐方式参数",
               params.get("recommendChannel")
               == "wechat")
        record("正常情境无挑战",
               b["challengeRequired"] is False
               and b["contextRisk"] == 0.0)
        codes.append(b)

        # 异常情境→挑战嵌入
        r = client.post(f"{BASE}/smartcode/generate", headers=ADMIN, json={
            "memberId": 2, "merchantId": 10,
            "amount": 88.0, "hour": 3,
            "newDevice": True,
            "remoteLocation": True})
        b2 = r.json()
        record("异常情境挑战嵌入",
               b2["challengeRequired"] is True
               and b2["contextRisk"] == 0.90
               and "challenge:1"
               not in str(b2))
        record("金额<100 无优惠组合",
               b2["recommendedChannel"]
               == "qr")
        codes.append(b2)

        # 金额非法
        r = client.post(f"{BASE}/smartcode/generate", headers=ADMIN, json={
            "memberId": 1, "merchantId": 10,
            "amount": 0})
        record("金额非法 422(请求层)",
               r.status_code == 422,
               f"s={r.status_code}")
    finally:
        set_mode("off")

    print("[04 水印确定性]")

    record("同输入同水印(确定性)",
           svc.watermark_of(1, 100.0, "abc")
           == svc.watermark_of(
               1, 100.0, "abc"))
    record("异 nonce 异水印",
           svc.watermark_of(1, 100.0, "abc")
           != svc.watermark_of(
               1, 100.0, "xyz"))
    record("异会员异水印",
           svc.watermark_of(1, 100.0, "abc")
           != svc.watermark_of(
               2, 100.0, "abc"))

    print("[05 核销全链(shadow)]")

    set_mode("shadow")
    try:
        # 正常核销
        full_code = codes[0]["fullCode"]
        r = client.post(f"{BASE}/smartcode/redeem", headers=ADMIN, json={
            "merchantId": 10, "code": full_code})
        b = r.json()
        record("正常核销 200+redeemed",
               r.status_code == 200
               and b["redeemed"] is True
               and b["verifyStatus"] == "ok",
               f"s={r.status_code} b={b}")
        record("核销回执含金额+水印",
               b["amount"] == 299.0
               and len(b["watermark"])
               == 6)

        # 重放核销(同码二次)→409
        r = client.post(f"{BASE}/smartcode/redeem", headers=ADMIN, json={
            "merchantId": 10, "code": full_code})
        record("重放核销 409(仅 generated)",
               r.status_code == 409,
               f"s={r.status_code}")

        # 非归属商户→409
        r = client.post(f"{BASE}/smartcode/redeem", headers=ADMIN, json={
            "merchantId": 99,
            "code": codes[1]["fullCode"]})
        record("非归属商户核销 409",
               r.status_code == 409,
               f"s={r.status_code}")

        # 篡改码
        tampered = codes[1]["fullCode"][:-2] \
            + ("ff" if codes[1]["fullCode"]
               [-2:] != "ff" else "00")
        r = client.post(f"{BASE}/smartcode/redeem", headers=ADMIN, json={
            "merchantId": 10, "code": tampered})
        b = r.json()
        record("篡改码→tampered 不核销",
               r.status_code == 200
               and b["redeemed"] is False
               and b["verifyStatus"]
               == "tampered",
               f"b={b}")

        # 非69号域码(55号直生——事件不存在)
        from services.qr55_crypto import (
            generate_code,
        )
        foreign = generate_code(
            "pay69-smart", {"amount": "1"},
            1, ttl_seconds=300)
        r = client.post(f"{BASE}/smartcode/redeem", headers=ADMIN, json={
            "merchantId": 10,
            "code": foreign["code"]})
        record("非69号域码 404(事件不存在)",
               r.status_code == 404,
               f"s={r.status_code}")

        # 过期码
        expired = generate_code(
            "pay69-smart", {"amount": "1"},
            1, ttl_seconds=-1)
        r = client.post(f"{BASE}/smartcode/redeem", headers=ADMIN, json={
            "merchantId": 10,
            "code": expired["code"]})
        b = r.json()
        record("过期码→expired 不核销",
               r.status_code == 200
               and b["redeemed"] is False
               and b["verifyStatus"]
               == "expired")

        # 格式非法
        r = client.post(f"{BASE}/smartcode/redeem", headers=ADMIN, json={
            "merchantId": 10, "code": "junk"})
        record("码格式非法 409",
               r.status_code == 409,
               f"s={r.status_code}")
    finally:
        set_mode("off")

    print("[06 商户对账摘要(nonce 去重)]")

    r = client.get(
        f"{BASE}/smartcode/merchant/10/summary",
        headers=ADMIN)
    b = r.json()
    record("对账摘要 200",
           r.status_code == 200,
           f"s={r.status_code}")
    record("总码数 2(nonce 去重——状态"
           "更新不重复计数)",
           b["totalCodes"] == 2,
           f"n={b['totalCodes']}")
    record("已核销 1+核销金额 299",
           b["redeemed"] == 1
           and b["redeemedAmount"] == 299.0,
           f"r={b['redeemed']} "
           f"a={b['redeemedAmount']}")
    record("待核销 1(挑战码未核)",
           b["pendingRedeem"] == 1)
    record("挑战码计数 1",
           b["challengeCount"] == 1)

    print("[07 情境统计与事件视图]")

    r = client.get(f"{BASE}/smartcode/stats", headers=ADMIN)
    b = r.json()
    record("统计 200+总码 2",
           r.status_code == 200
           and b["totalCodes"] == 2)
    record("挑战率 0.5(1/2)",
           b["challengeRate"] == 0.5,
           f"c={b['challengeRate']}")
    record("夜间因素命中 1(码2 午间无夜间)",
           b["factorHits"]["night_hours"]
           == 1
           and b["factorRates"]
           ["night_hours"] == 0.5)
    record("新设备因素命中 1",
           b["factorHits"]["new_device"]
           == 1)

    r = client.get(
        f"{BASE}/smartcode/events?merchantId=10",
        headers=ADMIN)
    b = r.json()
    record("事件视图 200+双口径过滤",
           r.status_code == 200
           and b["count"] == 2
           and all(
               x["merchantId"] == 10
               for x in b["records"]),
           f"n={b['count']}")
    record("列表脱敏(无完整码值)",
           all("fullCode" not in x
               and "code" not in x
               for x in b["records"]))
    record("核销态事件在册",
           any(x["status"] == "redeemed"
               for x in b["records"]))

    print("[08 模式矩阵]")

    r = client.post(f"{BASE}/smartcode/generate", headers=ADMIN, json={
        "memberId": 1, "merchantId": 10,
        "amount": 100})
    record("生成 off 409(决策面)",
           r.status_code == 409,
           f"s={r.status_code}")
    r = client.post(f"{BASE}/smartcode/redeem", headers=ADMIN, json={
        "merchantId": 10, "code": "x"})
    record("核销 off 409(决策面)",
           r.status_code == 409,
           f"s={r.status_code}")
    for ep in ("smartcode/dict",
               "smartcode/events",
               "smartcode/stats",
               "smartcode/merchant/10/summary"):
        r = client.get(f"{BASE}/{ep}", headers=ADMIN)
        record(f"观测面 {ep} off 200",
               r.status_code == 200,
               f"s={r.status_code}")
    r = client.get(f"{BASE}/smartcode/dict")
    record("情境码字典无 admin 403",
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
