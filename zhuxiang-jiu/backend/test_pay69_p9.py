"""69号·AI智能支付大模型 P9 专项测试
(跨境预研沙盘——规则库/预演/追踪)

运行方式:
    python test_pay69_p9.py

覆盖(69号规划 §4.7/§七 P9):
    - 规则库字典: 币种六域/管辖区六
      域闭合/沙盘汇率/对冲档/铁律
    - 合规预演: 管辖区查表(外汇管制
      级/单证要求/数字货币口径)+
      汇率换算确定性+对冲档位
    - 域校验: 币种/管辖区域外 404/
      CNY 本位 409/金额非法
    - 沙盘铁律: sandboxOnly 恒 True+
      免责声明+事件留痕
    - 政策追踪: 预演计数+变更通道
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

    print("[01 规则库字典与注册封闭]")

    r = client.get(f"{BASE}/crossborder/dict", headers=ADMIN)
    body = r.json()
    record("规则库字典 200",
           r.status_code == 200, f"s={r.status_code}")
    record("沙盘标志恒 True",
           body["sandboxOnly"] is True)
    record("币种六域(CNY/USD/EUR/JPY/GBP/HKD)",
           body["currencies"] == [
               "CNY", "USD", "EUR",
               "JPY", "GBP", "HKD"])
    record("管辖区六域闭合",
           set(body["jurisdictions"]) == {
               "CN", "US", "EU",
               "JP", "UK", "HK"})
    record("CN 外汇严格管制+报关单",
           body["jurisdictions"]["CN"][
               "fxControl"] == "strict")
    record("CN 数字货币仅试点",
           body["jurisdictions"]["CN"][
               "digitalCurrency"]
           == "pilot_only")
    record("沙盘汇率五对",
           len(body["sandboxFxRates"]) == 5
           and body["sandboxFxRates"][
               "CNY/USD"] == 0.14)
    record("对冲档三级",
           [t["action"] for t in
            body["hedgeTiers"]] == [
               "spot_only",
               "forward_30d",
               "forward_90d_ndp"])
    record("铁律公示(不实接/合成汇率/"
           "慢环变更)",
           len(body["ironRules"]) == 3)
    record("启动自检通过(P9 扩展)",
           reg._validate_registry() is None)

    print("[02 合规预演(shadow)]")

    set_mode("shadow")
    try:
        r = client.post(f"{BASE}/crossborder/preview", headers=ADMIN, json={
            "currency": "USD",
            "jurisdiction": "US",
            "amountCny": 5000})
        b = r.json()
        record("USD 预演 200",
               r.status_code == 200,
               f"s={r.status_code}")
        record("沙盘标志+免责声明",
               b["sandboxOnly"] is True
               and "永不实接"
               in b["disclaimer"])
        record("汇率换算确定性"
               "(¥5000×0.14=$700)",
               b["sandboxRate"] == 0.14
               and b["convertedAmount"]
               == 700.0)
        record("US 管制级+单证",
               b["fxControl"]
               == "moderate"
               and b["requiredDocs"]
               == ["trade_invoice"])
        record("小额对冲档 1 即期",
               b["hedge"]["tier"] == 1
               and b["hedge"]["action"]
               == "spot_only")

        # 中额: 30 天远期
        r = client.post(f"{BASE}/crossborder/preview", headers=ADMIN, json={
            "currency": "EUR",
            "jurisdiction": "EU",
            "amountCny": 50000})
        b = r.json()
        record("中额对冲档 2 远期30天",
               b["hedge"]["tier"] == 2
               and b["hedge"]["action"]
               == "forward_30d")
        record("EUR 换算(×0.13)",
               b["convertedAmount"]
               == 6500.0)
        record("EU 单证含 VAT",
               "vat_number"
               in b["requiredDocs"])

        # 大额: 90 天+自然对冲
        r = client.post(f"{BASE}/crossborder/preview", headers=ADMIN, json={
            "currency": "JPY",
            "jurisdiction": "JP",
            "amountCny": 500000})
        b = r.json()
        record("大额对冲档 3 远期90天",
               b["hedge"]["tier"] == 3
               and b["hedge"]["action"]
               == "forward_90d_ndp")
        record("JPY 换算(×21)",
               b["convertedAmount"]
               == 10500000.0)

        # CN 严格管制: 双单证
        r = client.post(f"{BASE}/crossborder/preview", headers=ADMIN, json={
            "currency": "HKD",
            "jurisdiction": "CN",
            "amountCny": 1000})
        b = r.json()
        record("CN 双单证(发票+报关)",
               set(b["requiredDocs"]) == {
                   "trade_invoice",
                   "customs_declaration"})

        # 域校验
        r = client.post(f"{BASE}/crossborder/preview", headers=ADMIN, json={
            "currency": "XXX",
            "jurisdiction": "US",
            "amountCny": 100})
        record("币种域外 404",
               r.status_code == 404,
               f"s={r.status_code}")
        r = client.post(f"{BASE}/crossborder/preview", headers=ADMIN, json={
            "currency": "USD",
            "jurisdiction": "XX",
            "amountCny": 100})
        record("管辖区域外 404",
               r.status_code == 404,
               f"s={r.status_code}")
        r = client.post(f"{BASE}/crossborder/preview", headers=ADMIN, json={
            "currency": "CNY",
            "jurisdiction": "US",
            "amountCny": 100})
        record("CNY 本位无跨境语义 409",
               r.status_code == 409,
               f"s={r.status_code}")
        r = client.post(f"{BASE}/crossborder/preview", headers=ADMIN, json={
            "currency": "USD",
            "jurisdiction": "US",
            "amountCny": 0})
        record("金额非法 422(请求层)",
               r.status_code == 422,
               f"s={r.status_code}")
    finally:
        set_mode("off")

    print("[03 政策追踪与模式矩阵]")

    r = client.get(f"{BASE}/crossborder/track", headers=ADMIN)
    b = r.json()
    record("追踪观测 200+预演计数",
           r.status_code == 200
           and b["sandboxOnly"] is True
           and b["previewCount"] >= 4,
           f"n={b['previewCount']}")
    record("变更通道说明(慢环 46号)",
           "46号" in b["policyChangesQueue"])

    r = client.post(f"{BASE}/crossborder/preview", headers=ADMIN, json={
        "currency": "USD",
        "jurisdiction": "US",
        "amountCny": 100})
    record("预演 off 409(决策面)",
           r.status_code == 409,
           f"s={r.status_code}")
    for ep in ("crossborder/dict",
               "crossborder/track"):
        r = client.get(f"{BASE}/{ep}", headers=ADMIN)
        record(f"观测面 {ep} off 200",
               r.status_code == 200,
               f"s={r.status_code}")
    r = client.get(f"{BASE}/crossborder/dict")
    record("规则库无 admin 403",
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
