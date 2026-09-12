"""71号·AI智能支付端口大模型 P3 专项测试
(帕累托调配: 四维向量+支配分析+情境
选解+外部信号建议书)

运行方式:
    python test_pay71_p3.py

覆盖(71号规划 §七 P3/§4.3):
    - 注册表 P3 扩展封闭: 情境权重表
      四档各和=1.0/合规分覆盖全端口/
      外部信号映射闭合/自检
    - 四维评分确定性: cost=1-费率/
      MAX/success=端口态×健康度/
      experience=1-时效/24/compliance
      =规则表
    - 帕累托前沿: 被支配端口(wechat/
      biometric 被 alipay 支配)不在
      前沿/前沿成员两两互不支配/
      同输入同前沿可复现
    - 情境选解: balanced→unionpay/
      large_amount→bank/price_
      sensitive+tvEligible→credit_tv/
      情境域外 409
    - 限额硬过滤: ¥60000 仅 bank 可用
    - 降级影响: unionpay degraded→
      balanced 切换 bank
    - advisoryOnly 铁律: 建议注入
      69号 P1(永不直接执行路由)
    - 外部信号: 登记→建议书 proposed/
      确定性偏移(目标维+0.05, 最大
      其他维-0.05, 和守恒)/批准→
      覆盖激活→weightsUsed=override/
      拒绝→保持出厂/二次终审 409/
      种类域外 409
    - 决策面 off 409 + 快环摄取不受
      开关影响
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

    print("[01 注册表 P3 扩展封闭]")

    from services import pay71_registry as reg
    from services import pay69_registry as \
        reg69

    record("情境权重表四档闭合",
           set(reg.ALLOCATION_CONTEXT_WEIGHTS)
           == set(reg.ALLOCATION_CONTEXTS)
           and len(reg.ALLOCATION_CONTEXTS)
           == 4)
    record("每档权重和=1.0",
           all(abs(sum(w.values()) - 1.0)
               < 1e-9 for w in
               reg.ALLOCATION_CONTEXT_WEIGHTS
               .values()))
    record("large_amount 合规维主导",
           reg.ALLOCATION_CONTEXT_WEIGHTS[
               "large_amount"]["compliance"]
           == 0.50)
    record("price_sensitive 成本维主导",
           reg.ALLOCATION_CONTEXT_WEIGHTS[
               "price_sensitive"]["cost"]
           == 0.45)
    record("合规分覆盖全端口(七)",
           set(reg.ALLOCATION_COMPLIANCE)
           == set(reg69.CHANNEL_IDS))
    record("外部信号种类域封闭",
           set(reg.EXTERNAL_SIGNAL_KINDS) == {
               "fee_change",
               "fx_fluctuation",
               "policy_change"})
    record("外部信号映射与种类闭合",
           set(reg.EXTERNAL_SIGNAL_SHIFTS)
           == set(
               reg.EXTERNAL_SIGNAL_KINDS))
    record("启动自检通过(导入即验)",
           reg._validate_registry() is None)

    print("[02 调配字典+决策面 off]")

    r = client.get(f"{BASE}/allocation/dict",
                   headers=ADMIN)
    body = r.json()
    record("调配字典 200",
           r.status_code == 200
           and body["dimensions"] == [
               "cost", "success",
               "experience", "compliance"]
           and "balanced" in body["contexts"],
           f"s={r.status_code}")
    record("字典含支配定义+资金铁律",
           "支配" in body["dominance"]
           and "69号 P1" in body[
               "fundsIronRule"])

    r = client.post(f"{BASE}/allocation/compute",
                    headers=ADMIN, json={
        "memberId": 1, "amount": 500})
    record("调配计算 off=409(决策面)",
           r.status_code == 409, f"s={r.status_code}")

    print("[03 四维评分+帕累托前沿(确定性)]")

    os.environ["PAY71_MODE"] = "shadow"
    try:
        r = client.post(
            f"{BASE}/allocation/compute",
            headers=ADMIN, json={
        "memberId": 1, "amount": 500,
        "context": "balanced"})
        body = r.json()
        record("shadow 态调配 200",
               r.status_code == 200,
               f"s={r.status_code}")
        record("¥5000 内六端口参评"
               "(credit_tv 资格门)",
               set(body["scores"]) == {
                   "qr", "wechat", "alipay",
                   "bank", "unionpay",
                   "biometric"},
               str(set(body["scores"])))
        record("四维分数值齐备(全端口)",
               all(set(s) == {
                   "cost", "success",
                   "experience", "compliance"}
                   for s in
                   body["scores"].values()))
        record("unionpay cost=0.5"
               "(1-0.003/0.006)",
               body["scores"]["unionpay"][
                   "cost"] == 0.5)
        record("qr experience=0(24h 封顶)",
               body["scores"]["qr"][
                   "experience"] == 0.0)
        record("bank compliance=0.95",
               body["scores"]["bank"][
                   "compliance"] == 0.95)

        front = set(body["paretoFront"])
        record("被支配端口不在前沿"
               "(wechat/biometric 被 alipay"
               " 支配; qr 被 unionpay"
               " 全维支配)",
               "wechat" not in front
               and "biometric" not in front
               and "qr" not in front,
               str(front))
        record("前沿=alipay/unionpay/bank"
               "(两两互不支配)",
               front == {"alipay", "unionpay",
                         "bank"},
               str(front))

        # 前沿成员两两互不支配(程序化验证)
        dims = ("cost", "success",
                "experience", "compliance")

        def dominates(a, b):
            return (all(a[d] >= b[d]
                        for d in dims)
                    and any(a[d] > b[d]
                            for d in dims))

        front_ports = [body["scores"][p]
                       for p in front]
        non_dom = not any(
            dominates(a, b)
            for a in front_ports
            for b in front_ports
            if a is not b)
        record("前沿成员两两互不支配",
               non_dom)

        record("balanced→unionpay"
               "(加权 0.83 最大)",
               body["recommended"]
               == "unionpay",
               body["recommended"])
        record("advisoryOnly 建议包铁律",
               body["advisoryOnly"] is True
               and body["injection"][
                   "target"]
               == "pay69 P1 route context")
        record("weightsSource=factory(初始)",
               body["weightsSource"]
               == "factory")

        # 确定性可复现(同输入同前沿同推荐)
        r2 = client.post(
            f"{BASE}/allocation/compute",
            headers=ADMIN, json={
        "memberId": 1, "amount": 500,
        "context": "balanced"})
        body2 = r2.json()
        record("同输入同前沿同推荐(可复现)",
               body2["paretoFront"]
               == body["paretoFront"]
               and body2["recommended"]
               == body["recommended"]
               and body2["weighted"]
               == body["weighted"])
    finally:
        os.environ["PAY71_MODE"] = "off"

    print("[04 情境选解(规则表档位)]")

    os.environ["PAY71_MODE"] = "shadow"
    try:
        r = client.post(
            f"{BASE}/allocation/compute",
            headers=ADMIN, json={
        "memberId": 1, "amount": 3000,
        "context": "large_amount"})
        record("large_amount→bank"
               "(合规维主导)",
               r.json()["recommended"]
               == "bank",
               r.json()["recommended"])

        r = client.post(
            f"{BASE}/allocation/compute",
            headers=ADMIN, json={
        "memberId": 1, "amount": 500,
        "context": "price_sensitive",
        "tvEligible": True})
        record("price_sensitive+TV 资格"
               "→credit_tv(成本维极致)",
               r.json()["recommended"]
               == "credit_tv",
               r.json()["recommended"])
        record("TV 参评(资格门开启)",
               "credit_tv" in r.json()[
                   "paretoFront"]
               or "credit_tv" in r.json()[
                   "scores"],
               str(r.json()["scores"].keys()))

        r = client.post(
            f"{BASE}/allocation/compute",
            headers=ADMIN, json={
        "memberId": 1, "amount": 500,
        "context": "high_value",
        "tvEligible": True})
        record("high_value+TV→credit_tv"
               "(体验维 1.0)",
               r.json()["recommended"]
               == "credit_tv",
               r.json()["recommended"])

        r = client.post(
            f"{BASE}/allocation/compute",
            headers=ADMIN, json={
        "memberId": 1, "amount": 500,
        "context": "nonsense"})
        record("情境域外 409",
               r.status_code == 409, f"s={r.status_code}")

        # 限额硬过滤: ¥60000 仅 bank
        # (singleLimit 10 万)可用
        r = client.post(
            f"{BASE}/allocation/compute",
            headers=ADMIN, json={
        "memberId": 1, "amount": 60000,
        "context": "balanced"})
        body = r.json()
        record("¥60000 限额过滤→仅 bank",
               set(body["scores"]) == {
                   "bank"}
               and body["recommended"]
               == "bank",
               str(set(body["scores"])))
    finally:
        os.environ["PAY71_MODE"] = "off"

    print("[05 降级影响(健康度联动)]")

    # unionpay 手动降级(P0 人工轨)
    client.post(f"{BASE}/ports/unionpay/"
                "state",
                headers=ADMIN, json={
        "portState": "degraded"})
    os.environ["PAY71_MODE"] = "shadow"
    try:
        r = client.post(
            f"{BASE}/allocation/compute",
            headers=ADMIN, json={
        "memberId": 1, "amount": 500,
        "context": "balanced"})
        body = r.json()
        record("unionpay 降级 success=0.5"
               "(端口态折减)",
               body["scores"]["unionpay"][
                   "success"] == 0.5,
               str(body["scores"][
                   "unionpay"]))
        record("balanced 切换 bank"
               "(降权后反超)",
               body["recommended"] == "bank",
               body["recommended"])
    finally:
        os.environ["PAY71_MODE"] = "off"
    # 恢复 unionpay
    client.post(f"{BASE}/ports/unionpay/"
                "state",
                headers=ADMIN, json={
        "portState": "healthy"})

    print("[06 外部信号(快环摄取——不受开关)]")

    r = client.post(
        f"{BASE}/allocation/external/report",
        headers=ADMIN, json={
        "kind": "fee_change",
        "note": "央行费率调整",
        "affectedPorts": ["wechat",
                          "alipay"]})
    body = r.json()
    record("外部信号登记 200(off 不受影响)",
           r.status_code == 200
           and body["state"] == "proposed"
           and body["kind"] == "fee_change",
           f"s={r.status_code}")
    record("受影响端口留痕",
           body["affectedPorts"] == [
               "wechat", "alipay"])
    record("确定性偏移(price_sensitive"
           " cost 0.45→0.50)",
           body["suggestedWeights"] == {
               "cost": 0.50,
               "success": 0.25,
               "experience": 0.10,
               "compliance": 0.15},
           str(body["suggestedWeights"]))
    record("偏移和守恒(=1.0)",
           abs(sum(body[
               "suggestedWeights"].values())
               - 1.0) < 1e-6)
    record("影响分析(维度+扣减源+delta)",
           body["impact"]["dimension"]
           == "cost"
           and body["impact"][
               "reducedFrom"] == "success"
           and body["impact"]["delta"]
           == 0.05)
    record("disposition 四要素",
           set(body["disposition"]) == {
               "proposedAction",
               "expectedGain",
               "riskAssessment",
               "rollbackPlan"})
    fee_seq = body["externalSeq"]

    r = client.post(
        f"{BASE}/allocation/external/report",
        headers=ADMIN, json={
        "kind": "explode"})
    record("种类域外 409",
           r.status_code == 409, f"s={r.status_code}")

    r = client.post(
        f"{BASE}/allocation/external/report",
        headers=ADMIN, json={
        "kind": "policy_change",
        "affectedPorts": ["nonexist"]})
    record("受影响端口域外 404",
           r.status_code == 404, f"s={r.status_code}")

    print("[07 外部信号终审(覆盖激活唯一入口)]")

    r = client.post(
        f"{BASE}/allocation/external/"
        f"{fee_seq}/decide",
        headers=ADMIN,
        json={"approve": True})
    body = r.json()
    record("批准→approved+覆盖激活",
           r.status_code == 200
           and body["state"] == "approved"
           and body["overrideActivated"]
           is True,
           f"s={r.status_code}")

    r = client.post(
        f"{BASE}/allocation/external/"
        f"{fee_seq}/decide",
        headers=ADMIN,
        json={"approve": True})
    record("二次终审 409",
           r.status_code == 409, f"s={r.status_code}")

    r = client.post(
        f"{BASE}/allocation/external/999/"
        "decide",
        headers=ADMIN,
        json={"approve": True})
    record("建议书不存在 404",
           r.status_code == 404, f"s={r.status_code}")

    # 权重视图: price_sensitive 覆盖生效
    r = client.get(f"{BASE}/allocation/"
                  "weights",
                  headers=ADMIN)
    body = r.json()
    ps = next(
        c for c in body["contexts"]
        if c["context"]
        == "price_sensitive")
    record("权重视图 override 生效",
           ps["source"] == "override"
           and ps["weights"]["cost"] == 0.50
           and ps["overrideFrom"]
           == fee_seq,
           str(ps))
    record("其余情境保持出厂",
           next(
               c for c in
               body["contexts"]
               if c["context"]
               == "balanced")["source"]
           == "factory")

    # 调配消费覆盖权重
    os.environ["PAY71_MODE"] = "shadow"
    try:
        r = client.post(
            f"{BASE}/allocation/compute",
            headers=ADMIN, json={
        "memberId": 1, "amount": 500,
        "context": "price_sensitive"})
        body = r.json()
        record("调配消费覆盖权重"
               "(weightsUsed=override)",
               body["weightsSource"]
               == "override"
               and body["weightsUsed"][
                   "cost"] == 0.50,
               str(body.get("weightsUsed")))
    finally:
        os.environ["PAY71_MODE"] = "off"

    # 拒绝流: policy_change→reject
    r = client.post(
        f"{BASE}/allocation/external/report",
        headers=ADMIN, json={
        "kind": "policy_change",
        "note": "渠道政策收紧"})
    pol_seq = r.json()["externalSeq"]
    r = client.post(
        f"{BASE}/allocation/external/"
        f"{pol_seq}/decide",
        headers=ADMIN,
        json={"approve": False})
    body = r.json()
    record("拒绝→rejected+覆盖未激活",
           r.status_code == 200
           and body["state"] == "rejected"
           and body["overrideActivated"]
           is False,
           f"s={r.status_code}")
    r = client.get(f"{BASE}/allocation/"
                  "weights",
                  headers=ADMIN)
    la = next(
        c for c in r.json()["contexts"]
        if c["context"] == "large_amount")
    record("拒绝后 large_amount 保持出厂",
           la["source"] == "factory")

    print("[08 观测视图(records/external)]")

    r = client.get(f"{BASE}/allocation/"
                  "records",
                  headers=ADMIN)
    body = r.json()
    record("调配留痕视图(全量)",
           body["count"] >= 7,
           f"count={body['count']}")

    r = client.get(f"{BASE}/allocation/"
                  "records",
                  headers=ADMIN,
                  params={"context":
                          "balanced"})
    record("留痕按情境过滤",
           all(rec["context"] == "balanced"
               for rec in
               r.json()["records"])
           and r.json()["count"] >= 3)

    r = client.get(f"{BASE}/allocation/"
                  "external",
                  headers=ADMIN)
    body = r.json()
    record("外部信号视图(建议书落库)",
           body["count"] == 2
           and body["signalKinds"] == [
               "fee_change",
               "fx_fluctuation",
               "policy_change"],
           f"count={body['count']}")
    states = {rec["state"]
              for rec in
              body["records"]}
    record("状态域(approved/rejected 各一)",
           states == {"approved",
                      "rejected"},
           str(states))

    print("[09 QC 叠加铁律(69号零改动)]")

    record("69号注册表零改动(七通道)",
           set(reg69.CHANNEL_REGISTRY)
           == set(reg69.CHANNEL_IDS)
           and len(reg69.CHANNEL_IDS) == 7)
    record("69号路由权重零改动",
           reg69.ROUTE_WEIGHTS == {
               "fee": 0.15, "health": 0.35,
               "affinity": 0.35,
               "habit": 0.15})

    r = client.get("/api/pay69/channels",
                   headers=ADMIN)
    record("69号端点正常(200——无回归)",
           r.status_code == 200
           and r.json()["channelCount"] == 7,
           f"s={r.status_code}")

    r = client.get(f"{BASE}/ports",
                   headers=ADMIN)
    record("71号 P0 端点正常(无回归)",
           r.status_code == 200
           and r.json()["portCount"] == 7,
           f"s={r.status_code}")

    r = client.get(f"{BASE}/selfheal/dict",
                   headers=ADMIN)
    record("71号 P1 端点正常(无回归)",
           r.status_code == 200
           and r.json()["shadowDays"] == 7,
           f"s={r.status_code}")

    r = client.get(f"{BASE}/predict/dict",
                   headers=ADMIN)
    record("71号 P2 端点正常(无回归)",
           r.status_code == 200
           and r.json()["splitThreshold"]
           == 5000.0,
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
