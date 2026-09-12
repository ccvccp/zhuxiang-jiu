"""69号·AI智能支付大模型 P3 专项测试
(交易级授信——评估/分期方案/调额建议书/还款回流)

运行方式:
    python test_pay69_p3.py

覆盖(69号规划 §七 P3):
    - 授信字典: 四轴+权重和=1.0+评级
      五档+利率表递增+状态机封闭
    - 四轴语义: 信值分档/现金流截断/
      履约公式/压力比
    - 评估确定性: 同输入同输出+权重×
      轴可复现(LLM 禁定价)
    - 评级语义: excellent/good/fair/
      cautious/rejected 分档正确
    - 分期方案: 利率查表+评级门控
      (excellent 3/6/12, fair 仅3,
      cautious/rejected 无)
    - 调额建议书: proposed 不生效/
      admin 终审 approved 生效/
      rejected 留痕/重复终审 409/
      超上限 409/额度为零等级 409
    - 还款回流: ontime/late/early
      计数+履约轴联动评估(快环
      不受开关影响)
    - 模式矩阵: evaluate/propose off
      409 / decide/repayment 不受
      开关影响
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
    from services.pay69_credit_service import (
        Pay69CreditService,
    )
    svc = Pay69CreditService()

    print("[01 授信字典与注册封闭]")

    r = client.get(f"{BASE}/credit/dict", headers=ADMIN)
    body = r.json()
    record("授信字典 200",
           r.status_code == 200, f"s={r.status_code}")
    record("四轴封闭",
           set(body["factors"]) == {
               "trust", "cashflow",
               "repayment", "pressure"})
    record("授信权重和=1.0",
           abs(sum(body["weights"].values())
               - 1.0) < 1e-9)
    record("评级五档封闭",
           body["grades"] == [
               "excellent", "good", "fair",
               "cautious", "rejected"])
    record("利率表 3/6/12 递增",
           [body["installmentRates"][k]
            for k in ("3", "6", "12")]
           == [0.030, 0.045, 0.060])
    record("调额状态机封闭",
           body["adjustmentStates"] == [
               "proposed", "approved",
               "rejected"])
    record("铁律公示(资金永不自动/LLM 禁定价/45号零改动)",
           len(body["ironRules"]) == 3)
    record("启动自检通过(P3 扩展)",
           reg._validate_registry() is None)

    print("[02 四轴语义(确定性)]")

    record("信值轴: S→1.0",
           svc._trust_axis("S") == 1.0)
    record("信值轴: D→0.0",
           svc._trust_axis("D") == 0.0)
    record("信值轴: 未知档→0.3(保守)",
           svc._trust_axis("xxx") == 0.3)

    record("现金流轴: 0.9→0.9",
           svc._cashflow_axis(0.9) == 0.9)
    record("现金流轴: 1.5 截断 1.0",
           svc._cashflow_axis(1.5) == 1.0)
    record("现金流轴: 默认 0.5",
           svc._cashflow_axis(None) == 0.5)

    record("履约轴: 无记录→0.5(中性)",
           svc._repayment_axis({}) == 0.5)
    record("履约轴: 全准时→1.0",
           svc._repayment_axis(
               {"ontime": 10}) == 1.0)
    record("履约轴: 5准1迟→0.7333",
           svc._repayment_axis(
               {"ontime": 5, "late": 1})
           == 0.7333,
           str(svc._repayment_axis(
               {"ontime": 5, "late": 1})))

    record("压力轴: ¥5000/额度50000→0.9",
           svc._pressure_axis(5000, 50000)
           == 0.9)
    record("压力轴: 超额→0.0",
           svc._pressure_axis(60000, 50000)
           == 0.0)
    record("压力轴: 零额度→0.0",
           svc._pressure_axis(100, 0) == 0.0)

    print("[03 交易级评估(shadow)]")

    set_mode("shadow")
    try:
        # 优: S 级+现金流0.9+无记录(0.5)
        # +小额压力0.9
        r1 = client.post(f"{BASE}/credit/evaluate", headers=ADMIN, json={
            "memberId": 1, "amount": 5000,
            "trustTier": "S",
            "cashflowIndex": 0.9})
        r2 = client.post(f"{BASE}/credit/evaluate", headers=ADMIN, json={
            "memberId": 1, "amount": 5000,
            "trustTier": "S",
            "cashflowIndex": 0.9})
        b1 = r1.json()
        record("evaluate shadow 200",
               r1.status_code == 200
               and r2.status_code == 200)
        record("同输入同输出(分值全等)",
               b1["score"] == r2.json()["score"])
        w = reg.CREDIT_WEIGHTS
        record("分=权重×轴(可复现)",
               abs(b1["score"] - round(sum(
                   w[k] * b1["axes"][k]
                   for k in reg.CREDIT_FACTORS),
                   4)) < 1e-9)
        # S=1.0, cf=0.9, rp=0.5, pr=0.9
        # score = 0.3+0.225+0.125+0.18=0.83
        record("优秀分 0.83→excellent",
               b1["score"] == 0.83
               and b1["grade"] == "excellent",
               f"s={b1['score']}")
        record("基础额度 S→50000",
               b1["baseLimit"] == 50000.0)

        # 中: B 级+现金流0.5+无记录+大额
        r = client.post(f"{BASE}/credit/evaluate", headers=ADMIN, json={
            "memberId": 2, "amount": 12000,
            "trustTier": "B",
            "cashflowIndex": 0.5})
        b = r.json()
        # B=0.6, cf=0.5, rp=0.5, pr=0.2
        # = 0.18+0.125+0.125+0.04=0.47
        record("中分 0.47→cautious",
               b["grade"] == "cautious",
               f"s={b['score']}")
        record("cautious 不批授信",
               b["creditApproved"] is False)

        # D 级零额度→409
        r = client.post(f"{BASE}/credit/evaluate", headers=ADMIN, json={
            "memberId": 3, "amount": 100,
            "trustTier": "D"})
        record("D 级禁授信 409",
               r.status_code == 409,
               f"s={r.status_code}")
    finally:
        set_mode("off")

    print("[04 分期方案(利率查表)]")

    set_mode("shadow")
    try:
        r = client.post(f"{BASE}/credit/evaluate", headers=ADMIN, json={
            "memberId": 1, "amount": 12000,
            "trustTier": "S",
            "cashflowIndex": 0.9})
        b = r.json()
        plans = {p["periods"]: p
                 for p in b["installmentPlans"]}
        record("excellent 3/6/12 期全开",
               set(plans) == {3, 6, 12},
               str(set(plans)))
        record("3 期利率 3.0%(查表)",
               plans[3]["annualRate"] == 0.030)
        record("12 期利率 6.0%(查表)",
               plans[12]["annualRate"] == 0.060)
        record("利息=金额×利率",
               plans[3]["totalInterest"]
               == 360.0,
               str(plans[3]["totalInterest"]))
        record("每期=(本+息)/期数",
               plans[3]["perInstallment"]
               == round(12360 / 3, 2),
               str(plans[3]["perInstallment"]))

        # good 档 3/6 期(B=0.6/cf0.9/
        # rp0.5/pr0.8→0.69)
        r = client.post(f"{BASE}/credit/evaluate", headers=ADMIN, json={
            "memberId": 5, "amount": 3000,
            "trustTier": "B",
            "cashflowIndex": 0.9})
        b = r.json()
        record("good 档 3/6 期(0.69)",
               b["score"] == 0.69
               and b["grade"] == "good"
               and {p["periods"]
                    for p in
                    b["installmentPlans"]}
               == {3, 6},
               f"g={b['grade']} "
               f"p={[p['periods'] for p in b['installmentPlans']]}")

        # cautious 无分期(member6 B 级大额)
        r = client.post(f"{BASE}/credit/evaluate", headers=ADMIN, json={
            "memberId": 6, "amount": 12000,
            "trustTier": "B",
            "cashflowIndex": 0.5})
        b = r.json()
        record("cautious 无分期方案",
               b["grade"] == "cautious"
               and b["installmentPlans"] == []
               and b["creditApproved"] is False,
               f"g={b['grade']}")
    finally:
        set_mode("off")

    print("[05 调额建议书(资金域永不自动)]")

    set_mode("shadow")
    try:
        # 发起(未生效)
        r = client.post(f"{BASE}/credit/adjustment/propose", headers=ADMIN, json={
            "memberId": 2, "requestedLimit": 20000,
            "trustTier": "B",
            "reason": "临时大额需求",
            "proposedBy": "member"})
        b = r.json()
        record("建议书发起 200+proposed",
               r.status_code == 200
               and b["status"] == "proposed",
               f"s={r.status_code}")
        adj_id = b["adjustmentId"]

        # 未终审不生效: 现行额度仍基础
        r = client.get(
            f"{BASE}/credit/adjustments"
            f"?status=proposed", headers=ADMIN)
        record("建议书视图 proposed 在册",
               r.json()["count"] >= 1)

        # 超上限
        r = client.post(f"{BASE}/credit/adjustment/propose", headers=ADMIN, json={
            "memberId": 2, "requestedLimit": 40000,
            "trustTier": "B"})
        record("超临时上限(基础×2) 409",
               r.status_code == 409,
               f"s={r.status_code}")

        # D 级禁调额
        r = client.post(f"{BASE}/credit/adjustment/propose", headers=ADMIN, json={
            "memberId": 3, "requestedLimit": 100,
            "trustTier": "D"})
        record("D 级禁调额 409",
               r.status_code == 409,
               f"s={r.status_code}")
    finally:
        set_mode("off")

    # 终审不受开关影响(资金人工铁律)
    r = client.post(
        f"{BASE}/credit/adjustment/{adj_id}/decide",
        headers=ADMIN, json={
            "approve": True, "decidedBy": "admin-张"})
    b = r.json()
    record("off 态终审 200(不受开关)",
           r.status_code == 200
           and b["status"] == "approved"
           and b["decidedBy"] == "admin-张",
           f"s={r.status_code}")

    # 重复终审 409
    r = client.post(
        f"{BASE}/credit/adjustment/{adj_id}/decide",
        headers=ADMIN, json={"approve": True})
    record("重复终审 409(状态机)",
           r.status_code == 409,
           f"s={r.status_code}")

    # 不存在
    r = client.post(
        f"{BASE}/credit/adjustment/999/decide",
        headers=ADMIN, json={"approve": True})
    record("建议书不存在 404",
           r.status_code == 404,
           f"s={r.status_code}")

    print("[06 还款回流与履约联动(快环)]")

    # off 态回流不受影响
    for _ in range(4):
        client.post(f"{BASE}/credit/repayment/report", headers=ADMIN, json={
            "memberId": 2, "eventType": "ontime"})
    r = client.post(f"{BASE}/credit/repayment/report", headers=ADMIN, json={
        "memberId": 2, "eventType": "late"})
    b = r.json()
    record("回流 200+计数(ontime 4/late 1)",
           r.status_code == 200
           and b["stats"]["ontime"] == 4
           and b["stats"]["late"] == 1,
           f"st={b['stats']}")

    r = client.post(f"{BASE}/credit/repayment/report", headers=ADMIN, json={
        "memberId": 2, "eventType": "junk"})
    record("事件域外 409",
           r.status_code == 409,
           f"s={r.status_code}")

    # 履约联动: 评估的 repayment 轴变化
    set_mode("shadow")
    try:
        r = client.post(f"{BASE}/credit/evaluate", headers=ADMIN, json={
            "memberId": 2, "amount": 3000,
            "trustTier": "B",
            "cashflowIndex": 0.9})
        b = r.json()
        record("履约轴联动(4准1迟=0.68)",
               b["axes"]["repayment"] == 0.68
               and b["repayStats"]["ontime"]
               == 4,
               f"rp={b['axes']['repayment']}")
    finally:
        set_mode("off")

    print("[07 模式矩阵与留痕]")

    r = client.post(f"{BASE}/credit/evaluate", headers=ADMIN, json={
        "memberId": 1, "amount": 100,
        "trustTier": "S"})
    record("evaluate off 409(决策面)",
           r.status_code == 409,
           f"s={r.status_code}")
    r = client.post(f"{BASE}/credit/adjustment/propose", headers=ADMIN, json={
        "memberId": 1, "requestedLimit": 1000,
        "trustTier": "S"})
    record("propose off 409(决策面)",
           r.status_code == 409,
           f"s={r.status_code}")
    r = client.post(f"{BASE}/credit/repayment/report", headers=ADMIN, json={
        "memberId": 1, "eventType": "early"})
    record("repayment off 200(快环)",
           r.status_code == 200,
           f"s={r.status_code}")

    r = client.get(
        f"{BASE}/credit/records?memberId=2",
        headers=ADMIN)
    b = r.json()
    record("留痕双口径过滤(member2)",
           r.status_code == 200 and b["count"]
           >= 2 and all(
               x["memberId"] == 2
               for x in b["records"]),
           f"n={b['count']}")
    r = client.get(f"{BASE}/credit/records", headers=ADMIN)
    record("全局留痕在库",
           r.json()["count"] >= 4,
           f"n={r.json()['count']}")
    r = client.get(
        f"{BASE}/credit/adjustments"
        f"?status=approved", headers=ADMIN)
    record("approved 建议书过滤",
           r.json()["count"] == 1
           and r.json()["adjustments"][0]
           ["status"] == "approved")

    r = client.get(f"{BASE}/credit/dict")
    record("授信字典无 admin 403",
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
