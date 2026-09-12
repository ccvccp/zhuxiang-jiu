"""71号·AI智能支付端口大模型 P1 专项测试
(端口自愈编排: 三态治理+熔断+半开探测
+影子冷启动+全留痕)

运行方式:
    python test_pay71_p1.py

覆盖(71号规划 §七 P1/§4.1):
    - 注册表 P1 扩展封闭: 熔断阈值>
      预警阈值分级单调/窗口/探测次数/
      影子期/治理生命周期/建议书状态
      机与种类/自愈动作域
    - 影子冷启动: 纳管→shadow/重复纳管
      409/影子期未满提案 409/broken 不可
      转正/转正→active/摘牌→offboarded/
      重纳管/待审建议书互斥
    - 前兆滚动窗口: 多信号类型去重叠加
      确定性/同窗口同分可复现
    - 保护方向编排: 0.70→degrade/0.90→
      fuse/69号 critical→degrade(消费方
      铁律)/窗口清零→recover/未纳管跳过
    - 半开探测: broken 专用(非 broken 409)/
      连续 3 次成功恢复/失败清零/恢复
      证据链 allSuccess
    - 全留痕: 轨迹三要素(触发窗口/动作/
      恢复证据)
    - 保护方向永续: PAY71_KILL=1 编排
      仍可用
    - 观测面: dict/shadows/traces(HTTP)
    - QC: 69号注册表零改动(叠加铁律)
"""

import asyncio
import os
import sys
from datetime import UTC, datetime, timedelta

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


async def backdate_shadow(port_id: str,
                          days: float):
    """测试辅助: 回溯影子期起点
    (直接仓储写入——确定性时间控制)"""
    from repositories.pay71_repository import (
        Pay71Repository,
    )
    repo = Pay71Repository()
    rec = await repo.get_shadow(port_id)
    rec["shadowStart"] = (
        datetime.now(UTC)
        - timedelta(days=days)).isoformat()
    await repo.save_shadow(port_id, rec)


async def main():
    from repositories.store import reset_store
    reset_store()

    from fastapi.testclient import TestClient
    from main import app
    client = TestClient(app)
    ADMIN = {"X-Role": "admin"}
    BASE = "/api/pay71"

    print("[01 注册表 P1 扩展封闭]")

    from services import pay71_registry as reg

    record("熔断阈值 0.80>预警 0.50(分级单调)",
           reg.PRECURSOR_FUSE_THRESHOLD == 0.80
           and reg.PRECURSOR_ALERT_THRESHOLD
           < reg.PRECURSOR_FUSE_THRESHOLD)
    record("滚动窗口容量 10",
           reg.SELFHEAL_WINDOW_SIZE == 10)
    record("半开探测恢复次数 3",
           reg.PROBE_REQUIRED_SUCCESSES == 3)
    record("影子冷启动期 7 天",
           reg.SHADOW_DAYS == 7)
    record("治理生命周期四态封闭",
           set(reg.GOVERNANCE_STATES) == {
               "ungoverned", "shadow",
               "active", "offboarded"})
    record("建议书状态机封闭",
           set(reg.PROPOSAL_STATES) == {
               "proposed", "approved",
               "rejected"})
    record("建议书种类域封闭(启停对称)",
           set(reg.PROPOSAL_KINDS) == {
               "promote", "offboard"})
    record("自愈动作域封闭(六动作)",
           set(reg.SELFHEAL_ACTIONS) == {
               "degrade", "fuse", "recover",
               "probe_pass", "probe_fail",
               "no_change"})
    record("启动自检通过(导入即验)",
           reg._validate_registry() is None)

    print("[02 自愈字典公示(观测面)]")

    r = client.get(f"{BASE}/selfheal/dict",
                   headers=ADMIN)
    body = r.json()
    record("自愈字典 200",
           r.status_code == 200
           and body["fuseThreshold"] == 0.80
           and body["probeRequired"] == 3
           and body["shadowDays"] == 7,
           f"s={r.status_code}")
    record("字典含状态机口径说明",
           "fuse" in body["stateMachine"]
           and "recover" in body["stateMachine"])

    print("[03 影子冷启动(纳管)]")

    r = client.post(f"{BASE}/selfheal/onboard",
                    headers=ADMIN,
                    json={"portId": "wechat"})
    body = r.json()
    record("纳管 wechat 200→shadow",
           r.status_code == 200
           and body["governance"] == "shadow"
           and body["onboardCount"] == 1,
           f"s={r.status_code} b={body}")

    r = client.get(f"{BASE}/selfheal/shadows",
                   headers=ADMIN)
    body = r.json()
    record("影子视图 count=1",
           body["count"] == 1
           and body["shadows"][0]["portId"]
           == "wechat")
    record("影子期未满 daysMet=False",
           body["shadows"][0]["daysMet"]
           is False
           and body["shadows"][0]["eligible"]
           is False)

    r = client.post(f"{BASE}/selfheal/onboard",
                    headers=ADMIN,
                    json={"portId": "wechat"})
    record("重复纳管 409",
           r.status_code == 409, f"s={r.status_code}")

    r = client.post(f"{BASE}/selfheal/onboard",
                    headers=ADMIN,
                    json={"portId": "nonexist"})
    record("纳管端口域外 404",
           r.status_code == 404, f"s={r.status_code}")

    r = client.post(f"{BASE}/selfheal/propose",
                    headers=ADMIN,
                    json={"portId": "wechat",
                          "kind": "promote"})
    record("影子期未满提案 409",
           r.status_code == 409, f"s={r.status_code}")

    print("[04 前兆滚动窗口(确定性)]")

    from services.pay71_p1_service import (
        Pay71P1Service,
    )
    svc = Pay71P1Service()

    # wechat 上报两类信号(0.35+0.35=0.70)
    for sig in ("latency_rising",
                "errorcode_climb"):
        client.post(f"{BASE}/signals/report",
                   headers=ADMIN, json={
            "portId": "wechat",
            "signal": sig})

    w = await svc.precursor_window("wechat")
    record("双信号去重叠加 0.70",
           w["windowScore"] == 0.70
           and w["signalTypes"] == [
               "errorcode_climb",
               "latency_rising"],
           str(w))
    record("同窗口同分可复现(确定性)",
           (await svc.precursor_window(
               "wechat"))["windowScore"]
           == 0.70)
    record("窗口引擎 rule_based(LLM 禁入)",
           w["engine"] == "rule_based")

    try:
        await svc.precursor_window("nonexist")
        record("窗口端口域外 KeyError", False,
               "未抛")
    except KeyError:
        record("窗口端口域外 KeyError", True)

    print("[05 保护方向编排(降级/熔断)]")

    r = client.post(f"{BASE}/selfheal/"
                    "orchestrate",
                    headers=ADMIN,
                    json={"portId": "wechat"})
    body = r.json()
    rec = body["results"][0] if body[
        "results"] else {}
    record("0.70→degrade(降权保护)",
           r.status_code == 200
           and rec.get("action") == "degrade"
           and rec.get("fromState") == "healthy"
           and rec.get("toState")
           == "degraded",
           f"s={r.status_code} b={body}")

    r = client.get(f"{BASE}/panorama",
                   headers=ADMIN)
    wechat = next(
        p for p in r.json()["ports"]
        if p["portId"] == "wechat")
    record("全景 degradedCount=1(降级生效)",
           r.json()["degradedCount"] == 1
           and wechat["portState"]
           == "degraded")

    # 未纳管端口跳过(alipay/credit_tv 等)
    r = client.post(f"{BASE}/selfheal/"
                    "orchestrate",
                    headers=ADMIN, json={})
    body = r.json()
    skipped_ids = {
        s["portId"] for s in
        body["skippedPorts"]}
    record("未纳管端口跳过(6/7)",
           body["evaluated"] == 1
           and len(skipped_ids) == 6
           and "alipay" in skipped_ids,
           f"eval={body['evaluated']} "
           f"skip={len(skipped_ids)}")

    # 熔断: bank 三类信号 0.90≥0.80
    client.post(f"{BASE}/selfheal/onboard",
                headers=ADMIN,
                json={"portId": "bank"})
    for sig in ("latency_rising",
                 "errorcode_climb",
                 "success_decline"):
        client.post(f"{BASE}/signals/report",
                    headers=ADMIN, json={
            "portId": "bank",
            "signal": sig})
    r = client.post(f"{BASE}/selfheal/"
                    "orchestrate",
                    headers=ADMIN,
                    json={"portId": "bank"})
    rec = r.json()["results"][0]
    record("0.90→fuse(熔断摘除)",
           rec["action"] == "fuse"
           and rec["toState"] == "broken",
           str(rec))

    # 69号 critical 触发(消费方铁律)
    client.post("/api/pay69/health/report",
                headers=ADMIN, json={
        "channelId": "unionpay",
        "attemptCount": 100,
        "successCount": 85})
    client.post(f"{BASE}/selfheal/onboard",
                headers=ADMIN,
                json={"portId": "unionpay"})
    r = client.post(f"{BASE}/selfheal/"
                    "orchestrate",
                    headers=ADMIN,
                    json={"portId": "unionpay"})
    rec = r.json()["results"][0]
    record("69号 critical→degrade(窗口 0 分)",
           rec["action"] == "degrade"
           and rec["windowScore"] == 0
           and rec["pay69State"]
           == "critical",
           str(rec))

    print("[06 全留痕(轨迹三要素)]")

    r = client.get(f"{BASE}/selfheal/traces",
                   headers=ADMIN,
                   params={"portId": "wechat"})
    body = r.json()
    record("轨迹视图 200+留痕落库",
           r.status_code == 200
           and body["count"] >= 1,
           f"s={r.status_code}")
    trace = body["traces"][0]
    record("轨迹三要素(触发窗口/动作/证据)",
           trace["action"] == "degrade"
           and trace["trigger"][
               "windowScore"] == 0.70
           and trace["evidence"][
               "fuseThreshold"] == 0.80
           and trace["fromState"] == "healthy"
           and trace["toState"] == "degraded",
           str(trace))

    print("[07 半开探测(broken 恢复证据链)]")

    r = client.post(f"{BASE}/selfheal/probe",
                    headers=ADMIN,
                    json={"portId": "bank",
                          "success": True})
    body = r.json()
    record("探针 1/3 未恢复",
           r.status_code == 200
           and body["probeStreak"] == 1
           and body["recovered"] is False
           and body["portState"] == "broken",
           f"b={body}")

    r = client.post(f"{BASE}/selfheal/probe",
                    headers=ADMIN,
                    json={"portId": "wechat",
                          "success": True})
    record("非 broken 探测 409",
           r.status_code == 409, f"s={r.status_code}")

    client.post(f"{BASE}/selfheal/probe",
                headers=ADMIN,
                json={"portId": "bank",
                      "success": True})
    r = client.post(f"{BASE}/selfheal/probe",
                    headers=ADMIN,
                    json={"portId": "bank",
                          "success": True})
    body = r.json()
    record("连续 3 次成功→恢复 healthy",
           body["recovered"] is True
           and body["portState"] == "healthy"
           and body["action"] == "recover",
           f"b={body}")

    # bank 恢复证据链: 最近 3 探针全成功
    r = client.get(f"{BASE}/selfheal/traces",
                   headers=ADMIN,
                   params={"portId": "bank"})
    recover_traces = [
        t for t in r.json()["traces"]
        if t["action"] == "recover"]
    record("恢复证据链 allSuccess",
           recover_traces
           and recover_traces[0]["evidence"][
               "allSuccess"] is True
           and len(recover_traces[0][
               "evidence"]["probeSeqs"]) == 3,
           str(recover_traces[:1]))

    print("[08 探针失败清零(半开保护)]")

    # bank 再熔断(窗口信号仍在)→探针失败
    client.post(f"{BASE}/selfheal/"
                "orchestrate",
                headers=ADMIN,
                json={"portId": "bank"})
    r = client.post(f"{BASE}/selfheal/probe",
                    headers=ADMIN,
                    json={"portId": "bank",
                          "success": False})
    body = r.json()
    record("探针失败 streak 清零",
           body["action"] == "probe_fail"
           and body["probeStreak"] == 0
           and body["portState"] == "broken",
           f"b={body}")

    # qr 四类信号 1.00→熔断; 供影子期
    # 转正阻断测试
    client.post(f"{BASE}/selfheal/onboard",
                headers=ADMIN,
                json={"portId": "qr"})
    for sig in ("latency_rising",
                "errorcode_climb",
                "success_decline",
                "callback_delay"):
        client.post(f"{BASE}/signals/report",
                    headers=ADMIN, json={
            "portId": "qr",
            "signal": sig})
    r = client.post(f"{BASE}/selfheal/"
                    "orchestrate",
                    headers=ADMIN,
                    json={"portId": "qr"})
    record("1.00→fuse(四信号满额)",
           r.json()["results"][0][
               "action"] == "fuse"
           and r.json()["results"][0][
               "windowScore"] == 1.0)

    print("[09 影子期转正(broken 阻断+建议书)]")

    await backdate_shadow("qr", days=8)
    r = client.get(f"{BASE}/selfheal/shadows",
                   headers=ADMIN)
    qr_view = next(
        s for s in r.json()["shadows"]
        if s["portId"] == "qr")
    record("回溯 8 天 daysMet=True",
           qr_view["daysMet"] is True
           and qr_view["daysElapsed"] >= 7,
           str(qr_view))
    record("broken 态 eligible=False",
           qr_view["portState"] == "broken"
           and qr_view["eligible"] is False)

    r = client.post(f"{BASE}/selfheal/propose",
                    headers=ADMIN,
                    json={"portId": "qr",
                          "kind": "promote"})
    record("熔断中端口不可转正 409",
           r.status_code == 409, f"s={r.status_code}")

    # 恢复 qr(3 探针)→可转正
    for _ in range(3):
        client.post(f"{BASE}/selfheal/probe",
                    headers=ADMIN,
                    json={"portId": "qr",
                          "success": True})
    r = client.post(f"{BASE}/selfheal/propose",
                    headers=ADMIN,
                    json={"portId": "qr",
                          "kind": "promote"})
    body = r.json()
    record("转正建议书发起 200(待审)",
           r.status_code == 200
           and body["state"] == "proposed"
           and body["evidence"]["daysElapsed"]
           >= 7,
           f"s={r.status_code} b={body}")
    record("建议书 disposition 四要素",
           set(body["disposition"]) == {
               "proposedAction",
               "expectedGain",
               "riskAssessment",
               "rollbackPlan"})

    r = client.post(f"{BASE}/selfheal/propose",
                    headers=ADMIN,
                    json={"portId": "qr",
                          "kind": "promote"})
    record("待审建议书互斥 409",
           r.status_code == 409, f"s={r.status_code}")

    proposal_seq = body["proposalSeq"]
    r = client.post(
        f"{BASE}/selfheal/proposals/"
        f"{proposal_seq}/decide",
        headers=ADMIN,
        json={"approve": True})
    body = r.json()
    record("终审批准→active",
           r.status_code == 200
           and body["state"] == "approved"
           and body["governanceAfter"]
           == "active",
           f"b={body}")

    r = client.post(
        f"{BASE}/selfheal/proposals/"
        f"{proposal_seq}/decide",
        headers=ADMIN,
        json={"approve": True})
    record("已终审再决定 409",
           r.status_code == 409, f"s={r.status_code}")

    print("[10 摘牌对称(启停闭环)]")

    r = client.post(f"{BASE}/selfheal/propose",
                    headers=ADMIN,
                    json={"portId": "qr",
                          "kind": "promote"})
    record("active 态不可再转正 409",
           r.status_code == 409, f"s={r.status_code}")

    r = client.post(f"{BASE}/selfheal/propose",
                    headers=ADMIN,
                    json={"portId": "qr",
                          "kind": "offboard"})
    body = r.json()
    offboard_seq = body["proposalSeq"]
    record("摘牌建议书发起 200",
           r.status_code == 200
           and body["kind"] == "offboard")

    r = client.post(
        f"{BASE}/selfheal/proposals/999/decide",
        headers=ADMIN,
        json={"approve": True})
    record("建议书不存在 404",
           r.status_code == 404, f"s={r.status_code}")

    r = client.post(
        f"{BASE}/selfheal/proposals/"
        f"{offboard_seq}/decide",
        headers=ADMIN,
        json={"approve": False})
    body = r.json()
    record("摘牌驳回→保持 active",
           r.status_code == 200
           and body["state"] == "rejected"
           and body["governanceAfter"]
           == "active",
           f"b={body}")

    r = client.post(f"{BASE}/selfheal/propose",
                    headers=ADMIN,
                    json={"portId": "qr",
                          "kind": "offboard"})
    body = r.json()
    r = client.post(
        f"{BASE}/selfheal/proposals/"
        f"{body['proposalSeq']}/decide",
        headers=ADMIN,
        json={"approve": True})
    record("摘牌批准→offboarded",
           r.json()["governanceAfter"]
           == "offboarded"
           and r.json()["state"] == "approved",
           str(r.json()))

    r = client.post(f"{BASE}/selfheal/onboard",
                    headers=ADMIN,
                    json={"portId": "qr"})
    body = r.json()
    record("重纳管→shadow(闭环)",
           r.status_code == 200
           and body["governance"] == "shadow"
           and body["onboardCount"] == 2,
           f"b={body}")

    r = client.post(f"{BASE}/selfheal/propose",
                    headers=ADMIN,
                    json={"portId": "biometric",
                          "kind": "offboard"})
    record("未纳管端口摘牌 409",
           r.status_code == 409, f"s={r.status_code}")

    r = client.post(f"{BASE}/selfheal/propose",
                    headers=ADMIN,
                    json={"portId": "wechat",
                          "kind": "explode"})
    record("建议书种类域外 409",
           r.status_code == 409, f"s={r.status_code}")

    print("[11 窗口清零恢复(degraded→healthy)]")

    # wechat 窗口被 10 条轻信号推出→0.10
    # <0.50→恢复(滚动窗口语义)
    for _ in range(10):
        client.post(f"{BASE}/signals/report",
                    headers=ADMIN, json={
            "portId": "wechat",
            "signal": "callback_delay"})
    w = await svc.precursor_window("wechat")
    record("10 条轻信号推出旧类型(0.10)",
           w["windowScore"] == 0.10,
           str(w))
    r = client.post(f"{BASE}/selfheal/"
                    "orchestrate",
                    headers=ADMIN,
                    json={"portId": "wechat"})
    rec = r.json()["results"][0]
    record("窗口清零→recover healthy",
           rec["action"] == "recover"
           and rec["toState"] == "healthy"
           and rec["fromState"] == "degraded",
           str(rec))

    print("[12 保护方向永续(KILL 不阻断)]")

    os.environ["PAY71_KILL"] = "1"
    try:
        r = client.post(
            f"{BASE}/selfheal/orchestrate",
            headers=ADMIN,
            json={"portId": "wechat"})
        record("PAY71_KILL=1 编排仍可用",
               r.status_code == 200
               and r.json()["results"][0][
                   "action"] == "no_change",
               f"s={r.status_code}")
    finally:
        os.environ.pop("PAY71_KILL", None)

    print("[13 QC 叠加铁律(69号零改动)]")

    from services import pay69_registry as \
        reg69
    record("69号注册表零改动(七通道)",
           set(reg69.CHANNEL_REGISTRY)
           == set(reg69.CHANNEL_IDS)
           and len(reg69.CHANNEL_IDS) == 7)
    record("69号路由权重零改动",
           reg69.ROUTE_WEIGHTS == {
               "fee": 0.15, "health": 0.35,
               "affinity": 0.35, "habit": 0.15})

    r = client.get("/api/pay69/channels",
                   headers=ADMIN)
    record("69号端点正常(200——无回归)",
           r.status_code == 200
           and r.json()["channelCount"] == 7,
           f"s={r.status_code}")

    r = client.get(f"{BASE}/ports", headers=ADMIN)
    record("71号 P0 端点正常(无回归)",
           r.status_code == 200
           and r.json()["portCount"] == 7,
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
