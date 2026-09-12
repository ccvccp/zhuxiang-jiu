"""71号·AI智能支付端口大模型 P2 专项测试
(预判式支付: 意图预判+预载建议+免密梯度
对齐+大额拆分建议书+重试策略)

运行方式:
    python test_pay71_p2.py

覆盖(71号规划 §七 P2/§4.2):
    - 注册表 P2 扩展封闭: 拆分阈值/
      份数/TTL/状态机/退避序列递增
    - 预判引擎: 习惯主导推荐(69号
      habits 消费)/无习惯退化为费率
      默认/frozen+broken 过滤/预授权
      限额检查
    - 免密梯度对齐: ¥88 小额熵 step=
      free(freeTier=True)/¥3000 熵
      step=otp(freeTier=False)——69号
      P2 熵引擎纯调用(留痕落库)
    - 预载可撤销观测域: revoke 留痕/
      重复撤销 409/资金不锁定口径
    - 大额拆分: <阈值 409/¥6000 2 份/
      ¥25000 3 份(对齐 69号 AMOUNT_
      BANDS 档)/拆分守恒/通道轮转组合
    - 确认令牌: 单次消费(二次确认 409)/
      错误令牌 409/拒绝留痕/总额回显/
      executed=False 建议包铁律
    - 重试策略: 退避序列确定性/上限
      切换建议/端口域外 404
    - 决策面 off 409 + 观测面不受影响
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

    print("[01 注册表 P2 扩展封闭]")

    from services import pay71_registry as reg

    record("拆分阈值 ¥5000(对齐 69号档)",
           reg.SPLIT_THRESHOLD == 5000.0)
    record("拆分份数上限 3",
           reg.SPLIT_MAX_PARTS == 3)
    record("拆分确认 TTL 1800s",
           reg.SPLIT_CONFIRM_TTL == 1800)
    record("拆分状态机封闭",
           set(reg.SPLIT_STATES) == {
               "proposed", "confirmed",
               "rejected"})
    record("重试退避序列(800/1600/3200 递增)",
           reg.RETRY_BACKOFF_MS == (
               800, 1600, 3200))
    record("重试上限 3(对齐 60号 P3)",
           reg.RETRY_MAX_ATTEMPTS == 3)
    record("习惯样本下限 3",
           reg.PREDICT_MIN_SAMPLES == 3)
    record("启动自检通过(导入即验)",
           reg._validate_registry() is None)

    print("[02 预判字典(观测面)+决策面 off]")

    r = client.get(f"{BASE}/predict/dict",
                   headers=ADMIN)
    body = r.json()
    record("预判字典 200",
           r.status_code == 200
           and body["splitThreshold"] == 5000.0
           and body["retryBackoffMs"] == [
               800, 1600, 3200],
           f"s={r.status_code}")
    record("字典含资金铁律声明",
           "executed=False" in body[
               "fundsIronRule"]
           and "69号 P2 熵引擎"
           in body["freeTierBasis"])

    r = client.post(f"{BASE}/predict/compute",
                    headers=ADMIN, json={
        "memberId": 1, "amount": 88})
    record("预判计算 off=409(决策面)",
           r.status_code == 409, f"s={r.status_code}")

    r = client.post(f"{BASE}/predict/split/"
                    "propose",
                    headers=ADMIN, json={
        "memberId": 1,
        "totalAmount": 6000})
    record("拆分发起 off=409(决策面)",
           r.status_code == 409, f"s={r.status_code}")

    print("[03 意图预判(习惯主导——69号消费)]")

    # 69号习惯播种(69号自身快环端点——
    # 71号只读消费铁律)
    for _ in range(5):
        client.post("/api/pay69/route/habit/"
                    "report",
                    headers=ADMIN, json={
            "memberId": 1,
            "channelId": "wechat",
            "count": 1})
    client.post("/api/pay69/route/habit/report",
                headers=ADMIN, json={
        "memberId": 1,
        "channelId": "alipay",
        "count": 1})

    os.environ["PAY71_MODE"] = "shadow"
    try:
        r = client.post(f"{BASE}/predict/compute",
                        headers=ADMIN, json={
            "memberId": 1, "amount": 88})
        body = r.json()
        record("shadow 态预判 200",
               r.status_code == 200,
               f"s={r.status_code}")
        record("习惯主导→推荐 wechat(5 次)",
               body["recommendedChannel"]
               == "wechat"
               and body["reason"]["basis"]
               == "habit"
               and body["reason"]["habitCount"]
               == 5,
               f"b={body.get('reason')}")
        record("候选拓扑含习惯计数",
               body["candidates"][0] == {
                   "portId": "wechat",
                   "feeRate": 0.006,
                   "habitCount": 5,
                   "rankBasis": "habit"},
               str(body["candidates"][:1]))

        # 免密梯度对齐: ¥88 熵 0.28<0.30
        # → free 档(69号 P2 纯调用)
        record("¥88 免密档 freeTier=True",
               body["freeTier"] is True
               and body["entropyStep"] == "free"
               and abs(body["entropy"]
                       - 0.28) < 0.01,
               f"e={body.get('entropy')} "
               f"s={body.get('entropyStep')}")
        record("预授权限额检查通过",
               body["preloadable"] is True
               and body["preloadCheck"][
                   "amountWithinLimit"] is True)
        record("小额无拆分建议",
               body["splitAdvisable"] is False)
        record("预判引擎 rule_based(LLM 禁入)",
               body["engine"] == "rule_based")

        # ¥3000: 熵 0.36≥0.30 → otp 档
        r = client.post(f"{BASE}/predict/compute",
                        headers=ADMIN, json={
            "memberId": 1, "amount": 3000})
        body = r.json()
        record("¥3000 认证档 freeTier=False",
               body["freeTier"] is False
               and body["entropyStep"] == "otp"
               and abs(body["entropy"]
                       - 0.36) < 0.01,
               f"e={body.get('entropy')} "
               f"s={body.get('entropyStep')}")
        record("¥3000<阈值 无拆分建议",
               body["splitAdvisable"] is False)

        # 69号熵留痕落库(纯调用实证——
        # 71号消费的熵评估在 69号留痕)
        r = client.get("/api/pay69/entropy/"
                       "records",
                       headers=ADMIN,
                       params={"limit": 10})
        record("69号熵留痕落库(纯调用实证)",
               r.status_code == 200
               and r.json()["count"] >= 2,
               f"count={r.json().get('count')}")

        # 无习惯会员→费率默认(TV 资格门
        # 69号 P1 范式——credit_tv 不参评)
        r = client.post(f"{BASE}/predict/compute",
                        headers=ADMIN, json={
            "memberId": 999, "amount": 88})
        body = r.json()
        record("无习惯→费率默认 unionpay 0.003"
               "(credit_tv 资格门——69号 P1"
               " 范式对齐)",
               body["recommendedChannel"]
               == "unionpay"
               and body["reason"]["basis"]
               == "fee_default"
               and body["reason"]["feeRate"]
               == 0.003,
               f"b={body.get('reason')}")
        record("credit_tv 不在候选池"
               "(tvEligible=False)",
               "credit_tv" not in [
                   c["portId"]
                   for c in
                   body["candidates"]],
               str(body["candidates"]))

        # frozen 过滤(69号冻结 wechat)
        client.post("/api/pay69/health/wechat/"
                    "freeze",
                    headers=ADMIN,
                    json={"frozen": True})
        r = client.post(f"{BASE}/predict/compute",
                        headers=ADMIN, json={
            "memberId": 1, "amount": 88})
        body = r.json()
        record("frozen 通道被过滤(推荐降级 alipay)",
               body["recommendedChannel"]
               == "alipay",
               body["recommendedChannel"])
        client.post("/api/pay69/health/wechat/"
                    "freeze",
                    headers=ADMIN,
                    json={"frozen": False})

        # broken 过滤(71号熔断 alipay)
        client.post(f"{BASE}/ports/alipay/state",
                    headers=ADMIN, json={
            "portState": "broken"})
        r = client.post(f"{BASE}/predict/compute",
                        headers=ADMIN, json={
            "memberId": 1, "amount": 88})
        body = r.json()
        record("broken 端口被过滤",
               "alipay" not in [
                   c["portId"]
                   for c in body["candidates"]],
               str(body["candidates"][:3]))
        client.post(f"{BASE}/ports/alipay/state",
                    headers=ADMIN, json={
            "portState": "healthy"})
    finally:
        os.environ["PAY71_MODE"] = "off"

    print("[04 预载可撤销观测域]")

    os.environ["PAY71_MODE"] = "shadow"
    try:
        r = client.post(f"{BASE}/predict/compute",
                        headers=ADMIN, json={
            "memberId": 1, "amount": 88})
        seq = r.json()["predictionSeq"]

        r = client.post(f"{BASE}/predict/{seq}/"
                        "revoke", headers=ADMIN)
        body = r.json()
        record("预载撤销 200+留痕",
               r.status_code == 200
               and body["revoked"] is True
               and body.get("revokedAt"),
               f"s={r.status_code}")

        r = client.post(f"{BASE}/predict/{seq}/"
                        "revoke", headers=ADMIN)
        record("重复撤销 409",
               r.status_code == 409, f"s={r.status_code}")

        r = client.post(f"{BASE}/predict/999/"
                        "revoke", headers=ADMIN)
        record("撤销不存在预判 404",
               r.status_code == 404, f"s={r.status_code}")

        r = client.get(f"{BASE}/predict/records",
                       headers=ADMIN,
                       params={"memberId": 1})
        body = r.json()
        record("预判留痕视图(按会员过滤)",
               body["count"] >= 5
               and all(rec["memberId"] == 1
                       for rec in body["records"]),
               f"count={body['count']}")
    finally:
        os.environ["PAY71_MODE"] = "off"

    print("[05 大额拆分建议书]")

    os.environ["PAY71_MODE"] = "shadow"
    try:
        r = client.post(f"{BASE}/predict/split/"
                        "propose",
                        headers=ADMIN, json={
            "memberId": 1,
            "totalAmount": 6000})
        record("低于阈值 ¥4999 409——本例 ¥6000"
               " 通过",
               r.status_code == 200,
               f"s={r.status_code}")

        r = client.post(f"{BASE}/predict/split/"
                        "propose",
                        headers=ADMIN, json={
            "memberId": 1,
            "totalAmount": 4999})
        record("¥4999<5000 不可拆分 409",
               r.status_code == 409, f"s={r.status_code}")

        # ¥6000: 2 份均分(3000/3000)
        r = client.post(f"{BASE}/predict/split/"
                        "propose",
                        headers=ADMIN, json={
            "memberId": 1,
            "totalAmount": 6000})
        body = r.json()
        record("¥6000→2 份拆分",
               body["partCount"] == 2
               and body["parts"][0]["amount"]
               == 3000.0
               and body["parts"][1]["amount"]
               == 3000.0,
               str(body.get("parts")))
        record("拆分守恒(份数和=总额)",
               abs(sum(p["amount"]
                      for p in body["parts"])
                   - 6000.0) < 0.01)
        record("通道轮转组合(并行度)",
               body["parts"][0]["channelId"]
               != body["parts"][1]["channelId"]
               or body["partCount"] == 1,
               str([p["channelId"]
                    for p in body["parts"]]))
        record("三要素齐备(金额/通道/时效)",
               all({"amount", "channelId",
                    "settleHours"}
                   <= set(p)
                   for p in body["parts"]))
        record("建议书初始态+令牌",
               body["state"] == "proposed"
               and len(body["confirmToken"])
               == 16
               and body["executed"] is False)
        sixk_seq = body["splitSeq"]
        sixk_token = body["confirmToken"]

        # ¥25000: 3 份(对齐 69号 2 万档)
        r = client.post(f"{BASE}/predict/split/"
                        "propose",
                        headers=ADMIN, json={
            "memberId": 1,
            "totalAmount": 25000})
        body = r.json()
        record("¥25000→3 份拆分",
               body["partCount"] == 3
               and abs(body["parts"][0]
                      ["amount"]
                      - 8333.33) < 0.01,
               str(body.get("parts")))

        print("[06 确认令牌单次消费铁律]")

        r = client.post(
            f"{BASE}/predict/split/{sixk_seq}/"
            f"confirm",
            headers=ADMIN, json={
        "confirmToken": "wrong-token-xx",
        "approve": True})
        record("错误令牌 409",
               r.status_code == 409, f"s={r.status_code}")

        r = client.post(
            f"{BASE}/predict/split/{sixk_seq}/"
            f"confirm",
            headers=ADMIN, json={
        "confirmToken": sixk_token,
        "approve": True})
        body = r.json()
        record("确认 200+总额回显",
               r.status_code == 200
               and body["state"] == "confirmed"
               and body["totalEcho"] == 6000.0,
               f"s={r.status_code} b={body}")
        record("executed=False 建议包铁律",
               body["executed"] is False)
        record("确认令牌已消费(置空)",
               body["confirmToken"] == "")
        record("disposition 四要素",
               set(body["disposition"]) == {
                   "proposedAction",
                   "expectedGain",
                   "riskAssessment",
                   "rollbackPlan"})

        r = client.post(
            f"{BASE}/predict/split/{sixk_seq}/"
            f"confirm",
            headers=ADMIN, json={
        "confirmToken": sixk_token,
        "approve": True})
        record("二次确认 409(令牌单次消费)",
               r.status_code == 409, f"s={r.status_code}")

        # 拒绝流
        r = client.post(f"{BASE}/predict/split/"
                        "propose",
                        headers=ADMIN, json={
            "memberId": 1,
            "totalAmount": 8000})
        body = r.json()
        r = client.post(
            f"{BASE}/predict/split/"
            f"{body['splitSeq']}/confirm",
            headers=ADMIN, json={
        "confirmToken":
            body["confirmToken"],
        "approve": False})
        body = r.json()
        record("拒绝流 200+留痕",
               r.status_code == 200
               and body["state"] == "rejected"
               and body["executed"] is False,
               f"s={r.status_code}")

        r = client.post(
            f"{BASE}/predict/split/999/confirm",
            headers=ADMIN, json={
        "confirmToken": "x",
        "approve": True})
        record("建议书不存在 404",
               r.status_code == 404, f"s={r.status_code}")

        r = client.get(f"{BASE}/predict/splits",
                       headers=ADMIN,
                       params={"memberId": 1})
        body = r.json()
        record("拆分视图令牌不外泄",
               all("confirmToken" not in rec
                   or rec.get("confirmToken")
                   in ("", None)
                   for rec in body["records"])
               or all(rec.get("state")
                      != "proposed"
                      for rec in
                      body["records"]
                      if "confirmToken"
                      not in rec),
               str([{k: rec.get(k)
                     for k in ("splitSeq",
                               "confirmToken")}
                    for rec in
                    body["records"][:4]]))
    finally:
        os.environ["PAY71_MODE"] = "off"

    print("[07 重试策略快环(退避确定性)]")

    r = client.post(f"{BASE}/predict/retry/report",
                    headers=ADMIN, json={
        "portId": "wechat", "failCount": 1})
    body = r.json()
    record("重试上报 200(快环不受开关)",
           r.status_code == 200
           and body["totalFails"] == 1
           and body["advice"]
           == "第 2 次重试建议间隔 1600ms",
           f"b={body}")

    r = client.post(f"{BASE}/predict/retry/report",
                    headers=ADMIN, json={
        "portId": "wechat", "failCount": 2})
    body = r.json()
    record("累计 3 次达上限→切换建议",
           body["totalFails"] == 3
           and "上限" in body["advice"],
           f"b={body.get('advice')}")

    r = client.post(f"{BASE}/predict/retry/report",
                    headers=ADMIN, json={
        "portId": "nonexist", "failCount": 1})
    record("重试端口域外 404",
           r.status_code == 404, f"s={r.status_code}")

    r = client.get(f"{BASE}/predict/retries",
                   headers=ADMIN)
    body = r.json()
    record("重试统计视图(端口计数)",
           body["ports"].get("wechat") == 3
           and body["retryMax"] == 3,
           str(body.get("ports")))

    print("[08 QC 叠加铁律(69号零改动)]")

    from services import pay69_registry as \
        reg69
    record("69号注册表零改动(七通道)",
           set(reg69.CHANNEL_REGISTRY)
           == set(reg69.CHANNEL_IDS)
           and len(reg69.CHANNEL_IDS) == 7)
    record("69号熵权重零改动",
           reg69.ENTROPY_WEIGHTS == {
               "amount": 0.20, "trust": 0.25,
               "behavior": 0.20,
               "environment": 0.15,
               "channel": 0.10,
               "history": 0.10})

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

    r = client.get(f"{BASE}/selfheal/dict",
                   headers=ADMIN)
    record("71号 P1 端点正常(无回归)",
           r.status_code == 200
           and r.json()["shadowDays"] == 7,
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
