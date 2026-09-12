"""71号·AI智能支付端口大模型 P0 专项测试
(端口池注册表+健康度全景+前兆信号底座)

运行方式:
    python test_pay71_p0.py

覆盖(71号规划 §七 P0):
    - 注册表封闭: 端口域与 69号通道域
      完全一致(消费方铁律)/端口态三态/
      折减系数单调递减/前兆信号域/
      四维调配权重和=1.0/启动自检
    - 前兆信号: 权重确定性映射/预警
      阈值触发(≥0.50)/低权重不预警/
      上报联动观测标记(不改端口态)
    - 端口态人工登记: 三态合法/域外
      409/预警不改写端口态铁律
    - 全景视图: 聚合 69号 P0 只读
      (pay69State/pay69Frozen 字段)/
      计数口径
    - LLM 禁入: 前兆评分纯权重查表
      (确定性)
    - 上报口径: 负增量 409/端口域外
      404/信号域外 404
    - 观测面: ports/panorama/signals/
      events/model status(off 不受影响)
    - HTTP: 8 端点行为与设计口径一致
    - QC: 69号注册表零改动(叠加铁律)
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
os.environ["PAY71_MODE"] = "off"
os.environ.pop("PAY71_KILL", None)

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
    BASE = "/api/pay71"

    print("[01 端口池注册表封闭(消费 69号铁律)]")

    from services import pay71_registry as reg
    from services import pay69_registry as reg69

    record("端口域与 69号通道域完全一致",
           set(reg._port_ids())
           == set(reg69.CHANNEL_IDS)
           and len(reg._port_ids()) == 7,
           str(reg._port_ids()))
    record("端口注册表同源只读(费率一致)",
           reg._port_registry()["wechat"]["feeRate"]
           == reg69.CHANNEL_REGISTRY[
               "wechat"]["feeRate"])
    record("端口态三态封闭",
           set(reg.PORT_STATES) == {
               "healthy", "degraded",
               "broken"},
           str(reg.PORT_STATES))
    record("折减系数单调递减(1.0>0.5>0.0)",
           reg.PORT_STATE_FACTORS == {
               "healthy": 1.0,
               "degraded": 0.5,
               "broken": 0.0})
    record("前兆信号四类封闭",
           set(reg.PRECURSOR_SIGNALS) == {
               "latency_rising",
               "errorcode_climb",
               "success_decline",
               "callback_delay"},
           str(reg.PRECURSOR_SIGNALS))
    record("前兆权重和=1.0(域封闭)",
           abs(sum(
               reg.PRECURSOR_WEIGHTS.values())
               - 1.0) < 1e-9)
    record("预警阈值合法(0.50)",
           reg.PRECURSOR_ALERT_THRESHOLD == 0.50)
    record("四维调配域封闭",
           set(reg.ALLOCATION_DIMENSIONS) == {
               "cost", "success",
               "experience", "compliance"})
    record("四维基准权重和=1.0",
           abs(sum(
               reg.ALLOCATION_BASE_WEIGHTS
               .values()) - 1.0) < 1e-9)
    record("情境档位含 balanced 默认档",
           "balanced" in
           reg.ALLOCATION_CONTEXTS)
    record("启动自检通过(导入即验)",
           reg._validate_registry() is None)
    record("模式默认 off",
           reg.current_mode() == "off")
    record("PAY71_KILL 默认关",
           reg.is_kill() is False)

    print("[02 HTTP 观测面]")

    r = client.get(f"{BASE}/ports", headers=ADMIN)
    body = r.json()
    record("端口池字典 200+7端口",
           r.status_code == 200
           and body["portCount"] == 7,
           f"s={r.status_code}")
    record("字典含端口态/前兆/调配口径",
           body["portStates"] == [
               "healthy", "degraded",
               "broken"]
           and body["precursorSignals"]
           and body["allocationDimensions"])

    r = client.get(f"{BASE}/ports/wechat", headers=ADMIN)
    record("单端口详情 200",
           r.status_code == 200
           and r.json()["portId"] == "wechat")
    r = client.get(f"{BASE}/ports/nonexist", headers=ADMIN)
    record("端口域外 404", r.status_code == 404,
           f"s={r.status_code}")

    r = client.get(f"{BASE}/ports")
    record("无 admin 403", r.status_code == 403,
           f"s={r.status_code}")

    r = client.get(f"{BASE}/panorama", headers=ADMIN)
    body = r.json()
    record("全景 200+7端口聚合",
           r.status_code == 200
           and len(body["ports"]) == 7,
           f"s={r.status_code}")
    record("全景只读源声明(69号 P0)",
           body["source"]
           == "pay69_p0_health_view(readonly)")
    record("未观测端口默认 healthy",
           all(p["portState"] == "healthy"
               for p in body["ports"]))
    record("初始计数全零",
           body["frozenCount"] == 0
           and body["degradedCount"] == 0
           and body["brokenCount"] == 0
           and body["alertedCount"] == 0)

    print("[03 前兆信号上报(快环观测)]")

    from services.pay71_p0_service import (
        Pay71P0Service,
    )
    svc = Pay71P0Service()

    r = client.post(f"{BASE}/signals/report",
                    headers=ADMIN, json={
        "portId": "wechat",
        "signal": "latency_rising",
        "latencyDelta": 180.5,
        "errorRateDelta": 0.02,
        "callbackMs": 300})
    body = r.json()
    record("前兆上报 200+权重 0.35",
           r.status_code == 200
           and body["signalWeight"] == 0.35,
           f"s={r.status_code} b={body}")
    record("0.35<0.50 不触发预警",
           body["alerted"] is False)
    record("信号留痕字段齐备",
           body["latencyDelta"] == 180.5
           and body["errorRateDelta"] == 0.02)

    r = client.post(f"{BASE}/signals/report",
                    headers=ADMIN, json={
        "portId": "alipay",
        "signal": "errorcode_climb",
        "errorRateDelta": 0.05})
    body = r.json()
    record("errorcode_climb 权重 0.35",
           body["signalWeight"] == 0.35
           and body["alerted"] is False)

    r = client.post(f"{BASE}/signals/report",
                    headers=ADMIN, json={
        "portId": "bank",
        "signal": "nonexist_signal"})
    record("信号域外 404",
           r.status_code == 404, f"s={r.status_code}")

    r = client.post(f"{BASE}/signals/report",
                    headers=ADMIN, json={
        "portId": "nonexist",
        "signal": "latency_rising"})
    record("上报端口域外 404",
           r.status_code == 404, f"s={r.status_code}")

    r = client.post(f"{BASE}/signals/report",
                    headers=ADMIN, json={
        "portId": "wechat",
        "signal": "latency_rising",
        "latencyDelta": -1})
    record("负增量 409",
           r.status_code == 409, f"s={r.status_code}")

    print("[04 前兆评分确定性(LLM 禁入)]")

    # 同输入同输出(确定性——可复现断言)
    s1 = await svc.report_signal(
        "unionpay", "callback_delay", 0, 0, 250)
    s2 = await svc.report_signal(
        "unionpay", "callback_delay", 0, 0, 250)
    record("同输入同权重(确定性可复现)",
           s1["signalWeight"]
           == s2["signalWeight"] == 0.10)

    # 四类信号权重各就各位(查表)
    record("权重表查表口径",
           reg.PRECURSOR_WEIGHTS == {
               "latency_rising": 0.35,
               "errorcode_climb": 0.35,
               "success_decline": 0.20,
               "callback_delay": 0.10})

    print("[05 端口态人工登记]")

    r = client.post(f"{BASE}/ports/bank/state",
                    headers=ADMIN, json={
        "portState": "degraded"})
    body = r.json()
    record("端口态登记 degraded 200",
           r.status_code == 200
           and body["portState"] == "degraded"
           and body["stateFactor"] == 0.5,
           f"s={r.status_code} b={body}")

    r = client.post(f"{BASE}/ports/nonexist/state",
                    headers=ADMIN, json={
        "portState": "degraded"})
    record("登记端口域外 404",
           r.status_code == 404, f"s={r.status_code}")

    r = client.post(f"{BASE}/ports/bank/state",
                    headers=ADMIN, json={
        "portState": "exploded"})
    record("端口态域外 409",
           r.status_code == 409, f"s={r.status_code}")

    r = client.get(f"{BASE}/panorama", headers=ADMIN)
    record("全景 degradedCount=1",
           r.json()["degradedCount"] == 1)

    print("[06 预警不改写端口态铁律]")

    # 高权重信号→预警观测标记, 但端口态
    # 保持 healthy(端口态治理是 P1 域+
    # 人工铁律——预警仅观测留痕)
    r = client.post(f"{BASE}/signals/report",
                    headers=ADMIN, json={
        "portId": "unionpay",
        "signal": "latency_rising",
        "latencyDelta": 500})
    record("0.35<0.50 仍不预警(单一信号)",
           r.json()["alerted"] is False)

    # 前兆叠加预警走多信号滚动窗口(P1)
    # ——P0 单信号最高 0.35 不达阈值,
    # 验证预警仅由阈值决定(确定性)
    record("单信号最高权重<阈值(无预警铁律)",
           max(reg.PRECURSOR_WEIGHTS.values())
           < reg.PRECURSOR_ALERT_THRESHOLD)

    r = client.get(f"{BASE}/panorama", headers=ADMIN)
    body = r.json()
    up = next(p for p in body["ports"]
              if p["portId"] == "unionpay")
    record("信号上报后端口态仍 healthy",
           up["portState"] == "healthy"
           and up["alerted"] is False,
           str(up))

    print("[07 69号聚合只读(叠加铁律)]")

    # 通过 69号 API 制造健康度变化, 验证
    # 71号全景只读聚合(不写 69号表)
    r = client.post("/api/pay69/health/report",
                    headers=ADMIN, json={
        "channelId": "qr",
        "attemptCount": 100,
        "successCount": 85})
    record("69号上报(制造 critical 态)",
           r.status_code == 200
           and r.json()["state"] == "critical")

    r = client.get(f"{BASE}/panorama", headers=ADMIN)
    body = r.json()
    qr = next(p for p in body["ports"]
              if p["portId"] == "qr")
    record("全景聚合 69号 pay69State=critical",
           qr["pay69State"] == "critical",
           str(qr))
    record("71号端口态不被 69号改写",
           qr["portState"] == "healthy")

    r = client.post("/api/pay69/health/qr/freeze",
                    headers=ADMIN, json={
        "frozen": True})
    record("69号冻结 qr(制造 frozen)",
           r.status_code == 200)

    r = client.get(f"{BASE}/panorama", headers=ADMIN)
    record("全景 frozenCount=1(只读聚合)",
           r.json()["frozenCount"] == 1)

    print("[08 观测面/事件/模型状态]")

    r = client.get(f"{BASE}/signals", headers=ADMIN)
    body = r.json()
    record("信号视图 200+留痕落库",
           r.status_code == 200
           and body["count"] >= 4,
           f"count={body['count']}")
    record("信号视图含阈值口径",
           body["alertThreshold"] == 0.50)

    r = client.get(f"{BASE}/events", headers=ADMIN)
    body = r.json()
    record("事件视图 200+埋点落库",
           r.status_code == 200
           and body["count"] >= 6,
           f"count={body['count']}")
    types = {e["type"] for e in body["events"]}
    record("事件类型域(signal_report/"
           "port_state_set)",
           types == {"signal_report",
                     "port_state_set"},
           str(types))

    r = client.get(f"{BASE}/model/status",
                   headers=ADMIN)
    body = r.json()
    record("模型状态 200(off 不受影响)",
           r.status_code == 200
           and body["mode"] == "off"
           and body["portCount"] == 7,
           f"s={r.status_code}")
    record("模型状态含 KILL/口径计数",
           body["kill"] is False
           and body["signalTypeCount"] == 4
           and body[
               "allocationDimensionCount"] == 4)

    print("[09 QC 叠加铁律(69号零改动)]")

    from services import pay69_registry as reg69b
    record("69号注册表零改动(七通道)",
           set(reg69b.CHANNEL_REGISTRY)
           == set(reg69b.CHANNEL_IDS)
           and len(reg69b.CHANNEL_IDS) == 7)
    record("69号模式三态零改动",
           reg69b.MODE_VALUES == (
               "off", "shadow", "assist"))
    record("69号路由权重零改动",
           reg69b.ROUTE_WEIGHTS == {
               "fee": 0.15, "health": 0.35,
               "affinity": 0.35, "habit": 0.15})

    r = client.get("/api/pay69/channels",
                   headers=ADMIN)
    record("69号端点正常(200——无回归)",
           r.status_code == 200
           and r.json()["channelCount"] == 7,
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
