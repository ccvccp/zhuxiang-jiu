"""69号·AI智能支付大模型 P2 专项测试
(风险熵引擎+认证步进+行为基线)

运行方式:
    python test_pay69_p2.py

覆盖(69号规划 §七 P2):
    - 熵字典: 六轴+权重和=1.0+梯度
      四档+riskTier 语义对齐(60号
      light/standard/strong/enhanced)
    - 六轴语义: 金额分档/信值分档/
      行为分档/环境叠加封顶/通道映射/
      历史线性
    - 熵计算确定性: 同输入同输出+
      权重×轴可复现(LLM 禁入实证)
    - 步进梯度: free/otp/biometric/
      dual 四档语义正确
    - 行为基线: EMA 首样本直建+增量
      更新+偏离度双指标取最大
    - 行为偏离→熵升高→认证增强
      (方向单一——安全侧)
    - fail-soft: 引擎异常→light 档
      +留痕(不阻断业务)
    - 模式矩阵: compute/deviation off
      409 / behavior report 快环
      不受开关影响
    - 留痕: records 全局/会员双口径
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
    from services.pay69_entropy_service import (
        Pay69EntropyService,
    )
    svc = Pay69EntropyService()

    print("[01 熵字典与注册封闭]")

    r = client.get(f"{BASE}/entropy/dict", headers=ADMIN)
    body = r.json()
    record("熵字典 200",
           r.status_code == 200, f"s={r.status_code}")
    record("六轴封闭",
           set(body["axes"]) == {
               "amount", "trust", "behavior",
               "environment", "channel",
               "history"})
    record("熵权重和=1.0",
           abs(sum(body["weights"].values())
               - 1.0) < 1e-9)
    record("梯度四档+60号 riskTier 对齐",
           [(s["step"], s["riskTier"])
            for s in body["ladder"]] == [
               ("free", "light"),
               ("otp", "standard"),
               ("biometric", "strong"),
               ("dual", "enhanced")])
    record("梯度阈值递增",
           [s["cap"] for s in body["ladder"]]
           == [0.30, 0.50, 0.70, 1.01])
    record("铁律公示(fail-soft/LLM 禁入/慢环)",
           len(body["ironRules"]) == 3)
    record("启动自检通过(P2 扩展)",
           reg._validate_registry() is None)

    print("[02 六轴语义(确定性查表)]")

    record("金额轴: ¥50→0.10",
           svc._amount_axis(50) == 0.10)
    record("金额轴: ¥100(阈值含)→0.10",
           svc._amount_axis(100) == 0.10)
    record("金额轴: ¥999→0.30",
           svc._amount_axis(999) == 0.30)
    record("金额轴: ¥5000(阈值含)→0.50",
           svc._amount_axis(5000) == 0.50)
    record("金额轴: ¥20000→0.70",
           svc._amount_axis(20000) == 0.70)
    record("金额轴: ¥30000→0.95",
           svc._amount_axis(30000) == 0.95)

    record("信值轴: S/trusted→0.10",
           svc._trust_axis("S") == 0.10
           and svc._trust_axis("trusted") == 0.10)
    record("信值轴: D/restricted→0.90",
           svc._trust_axis("D") == 0.90
           and svc._trust_axis("restricted")
           == 0.90)
    record("信值轴: 未知档→0.60(中性保守)",
           svc._trust_axis("unknown") == 0.60)

    record("行为轴: 无基线→0.30(中性)",
           svc._behavior_axis(None) == 0.30)
    record("行为轴: 偏离≤1.30→0.10(基线内)",
           svc._behavior_axis(1.30) == 0.10)
    record("行为轴: 偏离≤2.50→0.70(中度)",
           svc._behavior_axis(2.50) == 0.70)
    record("行为轴: 偏离>2.50→0.95(严重)",
           svc._behavior_axis(3.0) == 0.95)

    record("环境轴: 全正常→0.0",
           svc._environment_axis(
               False, False, False) == 0.0)
    record("环境轴: 新设备→0.40",
           svc._environment_axis(
               True, False, False) == 0.40)
    record("环境轴: 三项叠加封顶 0.95",
           svc._environment_axis(
               True, True, True) == 0.95)

    record("通道轴: biometric→0.20",
           svc._channel_axis("biometric") == 0.20)
    record("通道轴: credit_tv→0.60",
           svc._channel_axis("credit_tv") == 0.60)
    record("通道轴: 未指定→0.50(中性)",
           svc._channel_axis("") == 0.50)

    record("历史轴: 0→0.10",
           svc._history_axis(0) == 0.10)
    record("历史轴: 1.0→0.95(线性)",
           svc._history_axis(1.0) == 0.95)
    record("历史轴: 0.5→0.525",
           svc._history_axis(0.5) == 0.525)

    print("[03 熵计算与步进梯度(shadow)]")

    set_mode("shadow")
    try:
        # 低熵场景: 小额+高信值+生物通道
        r1 = client.post(f"{BASE}/entropy/compute", headers=ADMIN, json={
            "memberId": 1, "amount": 50,
            "trustTier": "S",
            "channelId": "biometric"})
        r2 = client.post(f"{BASE}/entropy/compute", headers=ADMIN, json={
            "memberId": 1, "amount": 50,
            "trustTier": "S",
            "channelId": "biometric"})
        b1 = r1.json()
        record("compute shadow 200",
               r1.status_code == 200
               and r2.status_code == 200)
        record("同输入同输出(熵值全等)",
               b1["entropy"] == r2.json()["entropy"])
        w = reg.ENTROPY_WEIGHTS
        record("熵=权重×轴(可复现)",
               abs(b1["entropy"] - round(sum(
                   w[k] * b1["axes"][k]
                   for k in reg.ENTROPY_AXES),
                   4)) < 1e-9)
        record("低熵→free+light",
               b1["entropy"] == 0.135
               and b1["step"] == "free"
               and b1["riskTier"] == "light",
               f"e={b1['entropy']}")

        # 中熵场景
        r = client.post(f"{BASE}/entropy/compute", headers=ADMIN, json={
            "memberId": 2, "amount": 2000,
            "trustTier": "B",
            "channelId": "wechat",
            "newDevice": True,
            "anomalyRate": 0.2})
        b = r.json()
        record("中熵→otp+standard",
               b["entropy"] == 0.437
               and b["step"] == "otp"
               and b["riskTier"] == "standard",
               f"e={b['entropy']}")

        # 高熵场景
        r = client.post(f"{BASE}/entropy/compute", headers=ADMIN, json={
            "memberId": 3, "amount": 8000,
            "trustTier": "B",
            "channelId": "qr",
            "newDevice": True, "oddHour": True,
            "anomalyRate": 0.5})
        b = r.json()
        record("高熵→biometric+strong",
               b["step"] == "biometric"
               and b["riskTier"] == "strong",
               f"e={b['entropy']}")

        # 极高熵场景(先播种基线, 行为轴可极端偏离)
        client.post(f"{BASE}/behavior/report", headers=ADMIN, json={
            "memberId": 4, "intervalMs": 800,
            "typingSpeedMs": 150})
        r = client.post(f"{BASE}/entropy/compute", headers=ADMIN, json={
            "memberId": 4, "amount": 30000,
            "trustTier": "D",
            "channelId": "credit_tv",
            "newDevice": True, "oddHour": True,
            "newLocation": True,
            "anomalyRate": 1.0,
            "intervalMs": 8000,
            "typingSpeedMs": 1500})
        b = r.json()
        record("极高熵→dual+enhanced",
               b["entropy"] == 0.9025
               and b["step"] == "dual"
               and b["riskTier"] == "enhanced",
               f"e={b['entropy']}")
        record("行为轴严重偏离 0.95",
               b["axes"]["behavior"] == 0.95
               and b["deviation"] == 10.0,
               f"d={b['deviation']}")

        r = client.post(f"{BASE}/entropy/compute", headers=ADMIN, json={
            "memberId": 1, "amount": 0})
        record("金额非法 422(请求层 gt=0)",
               r.status_code == 422, f"s={r.status_code}")
    finally:
        set_mode("off")

    print("[04 行为基线 EMA(快环)]")

    # 首样本直建
    r = client.post(f"{BASE}/behavior/report", headers=ADMIN, json={
        "memberId": 10, "intervalMs": 1000,
        "typingSpeedMs": 200})
    b = r.json()
    record("首样本直建基线(不受开关)",
           r.status_code == 200
           and b["avgIntervalMs"] == 1000
           and b["avgTypingMs"] == 200
           and b["samples"] == 1)

    # EMA 增量
    r = client.post(f"{BASE}/behavior/report", headers=ADMIN, json={
        "memberId": 10, "intervalMs": 2000,
        "typingSpeedMs": 300})
    b = r.json()
    record("EMA 增量(α=0.3)",
           b["avgIntervalMs"] == 1300
           and b["avgTypingMs"] == 230
           and b["samples"] == 2,
           f"i={b['avgIntervalMs']} t={b['avgTypingMs']}")

    r = client.get(f"{BASE}/behavior/baseline/10", headers=ADMIN)
    record("基线视图 200+established",
           r.status_code == 200
           and r.json()["established"] is True
           and r.json()["baseline"]["samples"]
           == 2)

    r = client.get(f"{BASE}/behavior/baseline/99", headers=ADMIN)
    record("无基线视图(未建立)",
           r.json()["established"] is False)

    print("[05 偏离度与方向单一性]")

    set_mode("shadow")
    try:
        # 基线内: 与基线一致
        r = client.post(f"{BASE}/behavior/deviation", headers=ADMIN, json={
            "memberId": 10, "intervalMs": 1300,
            "typingSpeedMs": 230})
        b = r.json()
        record("偏离度预览 200+基线内 1.0",
               r.status_code == 200
               and b["deviation"] == 1.0
               and b["axisScore"] == 0.10,
               f"d={b['deviation']}")

        # 100% 偏离 → 2.0 → 0.70 中度
        r = client.post(f"{BASE}/behavior/deviation", headers=ADMIN, json={
            "memberId": 10, "intervalMs": 2600,
            "typingSpeedMs": 230})
        b = r.json()
        record("100% 偏离→偏离比 2.0",
               b["deviation"] == 2.0,
               f"d={b['deviation']}")
        record("偏离→轴熵升高 0.70",
               b["axisScore"] == 0.70)

        # 熵随偏离升高(方向单一——安全侧)
        r_in = client.post(f"{BASE}/entropy/compute", headers=ADMIN, json={
            "memberId": 10, "amount": 100,
            "trustTier": "A",
            "channelId": "wechat",
            "intervalMs": 1300,
            "typingSpeedMs": 230})
        r_out = client.post(f"{BASE}/entropy/compute", headers=ADMIN, json={
            "memberId": 10, "amount": 100,
            "trustTier": "A",
            "channelId": "wechat",
            "intervalMs": 2600,
            "typingSpeedMs": 230})
        e_in = r_in.json()["entropy"]
        e_out = r_out.json()["entropy"]
        record("行为偏离→熵单调升高",
               e_out > e_in,
               f"in={e_in} out={e_out}")
        step_in = r_in.json()["step"]
        step_out = r_out.json()["step"]
        record("熵升高→认证步进增强(方向单一)",
               reg.STEP_LADDER.index(
                   next(s for s in reg.STEP_LADDER
                        if s[0] == step_out))
               >= reg.STEP_LADDER.index(
                   next(s for s in reg.STEP_LADDER
                        if s[0] == step_in)))

        # 基线外会员(无基线)→中性 0.30
        r = client.post(f"{BASE}/entropy/compute", headers=ADMIN, json={
            "memberId": 77, "amount": 100,
            "intervalMs": 5000,
            "typingSpeedMs": 500})
        record("无基线行为轴 0.30(中性)",
               r.json()["axes"]["behavior"]
               == 0.30)

        r = client.post(f"{BASE}/behavior/report", headers=ADMIN, json={
            "memberId": 10, "intervalMs": -1,
            "typingSpeedMs": 200})
        record("行为样本非正 422(请求层)",
               r.status_code == 422, f"s={r.status_code}")
    finally:
        set_mode("off")

    print("[06 fail-soft 铁律]")

    async def _fail_soft():
        """注入引擎故障(amount 轴 monkeypatch
        抛错)→light 档+留痕"""
        orig = svc._amount_axis
        svc._amount_axis = lambda a: (
            exec("raise RuntimeError('injected')"))
        try:
            rec = await svc.compute_entropy(
                1, 100, fail_soft=True)
            return rec
        finally:
            svc._amount_axis = orig

    rec = await _fail_soft()
    record("fail-soft: 故障→light 档",
           rec["step"] == "free"
           and rec["riskTier"] == "light"
           and rec["failSoft"] is True
           and rec["axes"] is None,
           f"r={rec.get('failSoft')}")

    # 非 fail_soft 模式: 异常上抛
    svc2 = Pay69EntropyService()
    svc2._amount_axis = lambda a: (
        exec("raise RuntimeError('injected')"))
    try:
        await svc2.compute_entropy(1, 100)
        record("非 fail-soft: 异常上抛",
               False, "未抛")
    except RuntimeError:
        record("非 fail-soft: 异常上抛", True)

    print("[07 留痕与模式矩阵]")

    set_mode("shadow")
    try:
        client.post(f"{BASE}/entropy/compute", headers=ADMIN, json={
            "memberId": 1, "amount": 100})
        client.post(f"{BASE}/entropy/compute", headers=ADMIN, json={
            "memberId": 1, "amount": 200})
        client.post(f"{BASE}/entropy/compute", headers=ADMIN, json={
            "memberId": 2, "amount": 300})
    finally:
        set_mode("off")

    r = client.get(f"{BASE}/entropy/records", headers=ADMIN)
    b = r.json()
    record("全局留痕视图(含 fail-soft)",
           r.status_code == 200 and b["count"]
           >= 4, f"n={b['count']}")
    r = client.get(
        f"{BASE}/entropy/records?memberId=1",
        headers=ADMIN)
    b = r.json()
    record("会员留痕双口径过滤",
           r.status_code == 200 and b["count"]
           >= 2 and all(
               x["memberId"] == 1
               for x in b["records"]),
           f"n={b['count']}")

    r = client.post(f"{BASE}/entropy/compute", headers=ADMIN, json={
        "memberId": 1, "amount": 100})
    record("compute off 409(决策面)",
           r.status_code == 409, f"s={r.status_code}")
    r = client.post(f"{BASE}/behavior/deviation", headers=ADMIN, json={
        "memberId": 10, "intervalMs": 1000,
        "typingSpeedMs": 200})
    record("deviation off 409(决策面)",
           r.status_code == 409, f"s={r.status_code}")
    r = client.post(f"{BASE}/behavior/report", headers=ADMIN, json={
        "memberId": 10, "intervalMs": 1000,
        "typingSpeedMs": 200})
    record("behavior report off 200(快环)",
           r.status_code == 200, f"s={r.status_code}")
    for ep in ("entropy/dict", "entropy/records",
               "behavior/baseline/10"):
        r = client.get(f"{BASE}/{ep}", headers=ADMIN)
        record(f"观测面 {ep} off 200",
               r.status_code == 200,
               f"s={r.status_code}")
    r = client.get(f"{BASE}/entropy/dict")
    record("熵字典无 admin 403",
           r.status_code == 403, f"s={r.status_code}")

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
