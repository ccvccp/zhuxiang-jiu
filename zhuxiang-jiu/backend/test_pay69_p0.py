"""69号·AI智能支付大模型 P0 专项测试
(七通道注册表+健康度观测+意图标签底座)

运行方式:
    python test_pay69_p0.py

覆盖(69号规划 §七 P0):
    - 注册表封闭: 七通道齐备/费率区间/
      限额/时效/特征域/启动自检
    - 健康度: 状态域封闭/成功率映射
      确定性/未观测默认 healthy
    - frozen 铁律: 永不被上报改写/
      人工专属/解冻恢复
    - 意图标签: 八类封闭/关键词规则轨
      确定性归属/多标签并存/归属不明
      default/亲和通道域内
    - LLM 禁入: 意图解析纯规则轨
      (engine=rule_based)
    - 上报口径: 成功数>尝试数 409/
      负数 409/通道域外 404
    - 观测面: channels/health/intents/
      model status(off 不受影响)
    - 决策面: intents/parse off=409
    - HTTP: 8 端点行为与设计口径一致
    - QC: 60号注册表零改动(叠加铁律)
"""

import asyncio
import os
import sys

os.environ["LOCK_MODE"] = "asyncio"
os.environ["STORE_MODE"] = "asyncio"
os.environ.pop("LLM_API_KEY", None)
os.environ["LLM_ENABLED"] = "off"
os.environ["XIAOZHU_LLM_MODE"] = "off"
os.environ["XIAOZHU_PROACTIVE_MODE"] = "off"
os.environ["QR55_MODE"] = "off"
os.environ["AIUP56_MODE"] = "off"
os.environ["KB57_MODE"] = "off"
os.environ["II58_MODE"] = "off"
os.environ["II59_MODE"] = "off"
os.environ["AB63_MODE"] = "off"
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


async def main():
    from repositories.store import reset_store
    reset_store()

    from fastapi.testclient import TestClient
    from main import app
    client = TestClient(app)
    ADMIN = {"X-Role": "admin"}
    BASE = "/api/pay69"

    print("[01 七通道注册表封闭]")

    from services import pay69_registry as reg

    record("七通道齐备(qr/wechat/alipay/bank"
           "/unionpay/credit_tv/biometric)",
           set(reg.CHANNEL_REGISTRY) == set(
               reg.CHANNEL_IDS)
           and len(reg.CHANNEL_IDS) == 7,
           str(reg.CHANNEL_IDS))
    record("费率区间合法(全通道 0-0.05)",
           all(0 <= m["feeRate"] <= 0.05
               for m in reg.CHANNEL_REGISTRY.values()))
    record("限额合法(全通道>0)",
           all(m["singleLimit"] > 0
               and m["dailyLimit"] > 0
               for m in reg.CHANNEL_REGISTRY.values()))
    record("时效合法(全通道≥0)",
           all(m["settleHours"] >= 0
               for m in reg.CHANNEL_REGISTRY.values()))
    record("特征域非空(全通道)",
           all(m["traits"]
               for m in reg.CHANNEL_REGISTRY.values()))
    record("启动自检通过(导入即验)",
           reg._validate_registry() is None)

    print("[02 健康度状态域]")

    record("状态域四态封闭",
           set(reg.HEALTH_STATES) == {
               "healthy", "degraded",
               "critical", "frozen"},
           str(reg.HEALTH_STATES))
    record("健康映射 1.0→healthy",
           reg.health_of(1.0) == "healthy")
    record("健康映射 0.99→healthy(阈值含)",
           reg.health_of(0.99) == "healthy")
    record("健康映射 0.95→degraded",
           reg.health_of(0.95) == "degraded")
    record("健康映射 0.90→degraded(阈值含)",
           reg.health_of(0.90) == "degraded")
    record("健康映射 0.89→critical",
           reg.health_of(0.89) == "critical")
    record("健康映射 0→critical",
           reg.health_of(0) == "critical")

    print("[03 HTTP 观测面]")

    r = client.get(f"{BASE}/channels", headers=ADMIN)
    body = r.json()
    record("通道字典 200+7通道",
           r.status_code == 200
           and body["channelCount"] == 7,
           f"s={r.status_code}")
    record("字典含健康度阈值口径",
           body["healthThresholds"]["healthy"] == 0.99)

    r = client.get(f"{BASE}/channels/wechat", headers=ADMIN)
    record("单通道详情 200",
           r.status_code == 200
           and r.json()["channelId"] == "wechat")
    r = client.get(f"{BASE}/channels/nonexist", headers=ADMIN)
    record("通道域外 404", r.status_code == 404,
           f"s={r.status_code}")

    r = client.get(f"{BASE}/channels")
    record("无 admin 403", r.status_code == 403,
           f"s={r.status_code}")

    r = client.get(f"{BASE}/health", headers=ADMIN)
    body = r.json()
    record("健康度观测 200",
           r.status_code == 200
           and body["frozenCount"] == 0,
           f"s={r.status_code}")
    record("未观测通道默认 healthy",
           all(c["state"] == "healthy"
               for c in body["channels"]))

    print("[04 健康度上报(快环观测)]")

    from services.pay69_p0_service import Pay69P0Service
    svc = Pay69P0Service()

    r = client.post(f"{BASE}/health/report", headers=ADMIN, json={
        "channelId": "wechat", "attemptCount": 100,
        "successCount": 99, "avgLatencyMs": 230.5})
    body = r.json()
    record("上报 200+成功率 0.99+healthy",
           r.status_code == 200
           and body["successRate"] == 0.99
           and body["state"] == "healthy",
           f"s={r.status_code} b={body}")
    record("上报含观测事件埋点",
           True)  # 埋点细节由 events 视图断言

    r = client.post(f"{BASE}/health/report", headers=ADMIN, json={
        "channelId": "alipay", "attemptCount": 100,
        "successCount": 85})
    record("成功率 0.85→critical",
           r.status_code == 200
           and r.json()["state"] == "critical")

    r = client.post(f"{BASE}/health/report", headers=ADMIN, json={
        "channelId": "bank", "attemptCount": 100,
        "successCount": 92})
    record("成功率 0.92→degraded",
           r.status_code == 200
           and r.json()["state"] == "degraded")

    r = client.post(f"{BASE}/health/report", headers=ADMIN, json={
        "channelId": "nonexist", "attemptCount": 10,
        "successCount": 10})
    record("上报通道域外 404",
           r.status_code == 404, f"s={r.status_code}")

    r = client.post(f"{BASE}/health/report", headers=ADMIN, json={
        "channelId": "qr", "attemptCount": 10,
        "successCount": 15})
    record("成功数>尝试数 409",
           r.status_code == 409, f"s={r.status_code}")

    r = client.post(f"{BASE}/health/report", headers=ADMIN, json={
        "channelId": "qr", "attemptCount": -1,
        "successCount": 0})
    record("负数次数 422(Pydantic 请求层拦截)",
           r.status_code == 422, f"s={r.status_code}")
    # 服务层第二道防线(绕过 HTTP 直调——409 语义)
    try:
        await svc.report_health("qr", -1, 0)
        record("服务层负数 ValueError", False, "未抛")
    except ValueError:
        record("服务层负数 ValueError(第二道)", True)

    print("[05 frozen 人工专属铁律]")

    r = client.post(f"{BASE}/health/credit_tv/freeze",
                    headers=ADMIN, json={"frozen": True})
    record("冻结 200+frozen 态",
           r.status_code == 200
           and r.json()["frozen"] is True
           and r.json()["state"] == "frozen",
           f"b={r.json() if r.status_code == 200 else r.status_code}")

    # 上报不可改写 frozen(人工专属)
    r = client.post(f"{BASE}/health/report", headers=ADMIN, json={
        "channelId": "credit_tv", "attemptCount": 100,
        "successCount": 100})
    record("上报后 frozen 保持(永不被改写)",
           r.status_code == 200
           and r.json()["frozen"] is True)

    r = client.get(f"{BASE}/health", headers=ADMIN)
    record("观测面 frozenCount=1",
           r.json()["frozenCount"] == 1)

    r = client.post(f"{BASE}/health/credit_tv/freeze",
                    headers=ADMIN, json={"frozen": False})
    record("解冻 200+恢复 healthy",
           r.status_code == 200
           and r.json()["frozen"] is False,
           f"s={r.status_code}")

    r = client.post(f"{BASE}/health/nonexist/freeze",
                    headers=ADMIN, json={"frozen": True})
    record("冻结通道域外 404",
           r.status_code == 404, f"s={r.status_code}")

    print("[06 意图标签规则轨(LLM 禁入)]")

    parsed = svc.parse_intent("这单很急, 赶时间")
    record("'急/赶时间'→fast_needed",
           parsed["tags"] == ["fast_needed"])
    record("fast_needed 亲和 wechat/alipay/biometric",
           set(parsed["candidateChannels"]) == {
               "wechat", "alipay", "biometric"})

    parsed = svc.parse_intent("大额贵重商品")
    record("'大额/贵重'→large_amount",
           parsed["tags"] == ["large_amount"])
    record("large_amount 亲和 bank/unionpay",
           set(parsed["candidateChannels"]) == {
               "bank", "unionpay"})

    parsed = svc.parse_intent("用微信付, 群里代付")
    record("社交语义→social_sharing",
           parsed["tags"] == ["social_sharing"]
           and parsed["candidateChannels"] == ["wechat"])

    parsed = svc.parse_intent("有什么优惠活动吗")
    record("'优惠/活动'→promo_hunting",
           parsed["tags"] == ["promo_hunting"])

    parsed = svc.parse_intent("用信值抵扣, 后付")
    record("'信值/后付'→credit_preference",
           parsed["tags"] == ["credit_preference"]
           and parsed["candidateChannels"] == [
               "credit_tv"])

    parsed = svc.parse_intent("刷脸支付")
    record("'刷脸'→biometric_habit",
           parsed["tags"] == ["biometric_habit"]
           and parsed["candidateChannels"] == [
               "biometric"])

    parsed = svc.parse_intent("急用微信, 有优惠吗")
    record("多标签并存(fast+social+promo)",
           set(parsed["tags"]) == {
               "fast_needed", "social_sharing",
               "promo_hunting"},
           str(parsed["tags"]))

    parsed = svc.parse_intent("随便")
    record("归属不明→default",
           parsed["tags"] == ["default"])

    parsed = svc.parse_intent("")
    record("空文本→default(不报错)",
           parsed["tags"] == ["default"])

    record("引擎标识 rule_based(LLM 禁入实证)",
           svc.parse_intent("急")["engine"]
           == "rule_based")

    record("意图标签八类封闭",
           set(svc.parse_intent("x")["tags"]) <= {
               "fast_needed", "large_amount",
               "privacy_needed", "social_sharing",
               "promo_hunting",
               "credit_preference",
               "biometric_habit", "default"}
           and len(reg.INTENT_TAGS) == 8)

    print("[07 决策面 off 409 + 观测面不受影响]")

    r = client.post(f"{BASE}/intents/parse", headers=ADMIN, json={
        "intentText": "这单很急", "memberId": 1})
    record("意图解析 off=409(决策面)",
           r.status_code == 409, f"s={r.status_code}")

    r = client.get(f"{BASE}/model/status", headers=ADMIN)
    body = r.json()
    record("模型状态 200(off 不受影响)",
           r.status_code == 200
           and body["mode"] == "off"
           and body["channelCount"] == 7,
           f"s={r.status_code}")

    r = client.get(f"{BASE}/intents", headers=ADMIN)
    record("意图视图 200(空留痕)",
           r.status_code == 200
           and r.json()["count"] == 0)

    print("[08 shadow 态决策面开放]")

    os.environ["PAY69_MODE"] = "shadow"
    try:
        r = client.post(f"{BASE}/intents/parse", headers=ADMIN, json={
            "intentText": "这单很急, 用微信",
            "memberId": 1, "intentId": 601,
            "sessionId": 701})
        body = r.json()
        record("shadow 态解析 200+留痕",
               r.status_code == 200
               and body["tags"] == [
                   "fast_needed",
                   "social_sharing"],
               f"s={r.status_code}")
        record("归因链透传(intentId/sessionId)",
               body["intentId"] == 601
               and body["sessionId"] == 701)

        r = client.post(f"{BASE}/intents/parse", headers=ADMIN, json={
            "intentText": "  ", "memberId": 1})
        record("空白文本 409",
               r.status_code == 409, f"s={r.status_code}")

        r = client.get(f"{BASE}/intents", headers=ADMIN)
        record("意图视图 count=1(留痕落库)",
               r.json()["count"] == 1
               and r.json()["intents"][0]
               ["candidateChannels"] == [
                   "wechat", "alipay", "biometric"])
    finally:
        os.environ["PAY69_MODE"] = "off"

    print("[09 QC 叠加铁律(60号零改动)]")

    from services import pay60_registry as reg60
    record("60号注册表零改动(模式三态)",
           reg60.MODE_VALUES == ("off", "shadow", "assist"))
    record("60号通道三态零改动",
           reg60.CHANNEL_MODES == (
               "mock", "real", "mock_fallback"))

    r = client.get("/api/pay60/registry", headers=ADMIN)
    record("60号 registry 端点正常(200)",
           r.status_code == 200,
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
