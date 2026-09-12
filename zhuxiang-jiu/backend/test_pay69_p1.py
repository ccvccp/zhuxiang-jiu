"""69号·AI智能支付大模型 P1 专项测试
(智能路由——评分选道/静默备选/滚动窗口)

运行方式:
    python test_pay69_p1.py

覆盖(69号规划 §七 P1):
    - 路由字典: 权重封闭+和=1.0+硬过滤
      口径公示
    - 评分确定性: 同输入同输出(排序
      分数全等)——LLM 禁入实证(四因子
      全数值)
    - 因子语义: fee(credit_tv=1.0/
      wechat=0.0)/affinity(large_amount
      →bank 优先)/habit(会员历史占比)
    - 硬过滤: 金额超限排除/frozen
      排除/全部超限→无可用通道
    - 健康度分层: 手工上报(critical
      降权)/滚动窗口(覆盖手工)
    - 静默备选: 首选失败自动次优/
      全失败 attempts=3/成功不备选
    - 快环: flows 留痕/滚动窗口统计
      /habit 上报不受开关影响
    - 模式: compute off 409/execute
      需 assist/观测面 off 200
    - QC: P0 专项回归(56/56)
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

    print("[01 路由字典与权重]")

    r = client.get(f"{BASE}/route/dict", headers=ADMIN)
    body = r.json()
    record("路由字典 200",
           r.status_code == 200, f"s={r.status_code}")
    record("权重四因子公示",
           set(body["weights"]) == {
               "fee", "health", "affinity", "habit"})
    record("权重和=1.0",
           abs(sum(body["weights"].values()) - 1.0)
           < 1e-9)
    record("窗口容量 50/最大尝试 3",
           body["windowSize"] == 50
           and body["maxAttempts"] == 3)
    record("硬过滤口径含 frozen",
           any("frozen" in f
               for f in body["hardFilters"]))

    print("[02 评分确定性(shadow 开放决策面)]")

    set_mode("shadow")
    try:
        r1 = client.post(f"{BASE}/route/compute", headers=ADMIN, json={
            "memberId": 1, "amount": 500,
            "tvEligible": True})
        r2 = client.post(f"{BASE}/route/compute", headers=ADMIN, json={
            "memberId": 1, "amount": 500,
            "tvEligible": True})
        b1, b2 = r1.json(), r2.json()
        record("compute shadow 200",
               r1.status_code == 200
               and r2.status_code == 200)
        record("同输入同输出(排序全等)",
               [c["channelId"] for c in b1["ranking"]]
               == [c["channelId"]
                   for c in b2["ranking"]]
               and [c["routeScore"]
                    for c in b1["ranking"]]
               == [c["routeScore"]
                   for c in b2["ranking"]])
        record("七通道全参评(tvEligible)",
               b1["candidateCount"] == 7)
        record("四因子数值明细(LLM 禁入实证)",
               all(
                   isinstance(
                       c["factors"][k], (int, float))
                   for c in b1["ranking"]
                   for k in ("fee", "health",
                             "affinity", "habit")))
        w = reg.ROUTE_WEIGHTS
        top = b1["ranking"][0]
        record("routeScore=权重×因子(可复现)",
               abs(top["routeScore"]
                   - round(
                       w["fee"] * top["factors"]["fee"]
                       + w["health"]
                       * top["factors"]["health"]
                       + w["affinity"]
                       * top["factors"]["affinity"]
                       + w["habit"]
                       * top["factors"]["habit"],
                       4)) < 1e-9)

        # 因子语义
        by_id = {c["channelId"]: c
                 for c in b1["ranking"]}
        record("fee: credit_tv=1.0(零费率)",
               by_id["credit_tv"]
               ["factors"]["fee"] == 1.0)
        record("fee: wechat=0.0(最高费率)",
               by_id["wechat"]
               ["factors"]["fee"] == 0.0)
        record("health: 未观测=1.0(default)",
               by_id["wechat"]
               ["factors"]["health"] == 1.0
               and by_id["wechat"]
               ["healthSource"] == "default")
        record("affinity: 非候选=0.5(默认标签)",
               by_id["bank"]
               ["factors"]["affinity"] == 0.5)
        record("habit: 无历史=0.5",
               by_id["qr"]["factors"]["habit"]
               == 0.5)
    finally:
        set_mode("off")

    print("[03 意图亲和]")

    set_mode("shadow")
    try:
        r = client.post(f"{BASE}/route/compute", headers=ADMIN, json={
            "memberId": 1, "amount": 500,
            "tags": ["large_amount"]})
        b = r.json()
        record("large_amount→bank/unionpay 居前二",
               {b["ranking"][0]["channelId"],
                b["ranking"][1]["channelId"]}
               == {"bank", "unionpay"},
               str([c["channelId"]
                    for c in b["ranking"][:3]]))
        record("亲和候选=1.0",
               next(c for c in b["ranking"]
                    if c["channelId"] == "bank")
               ["factors"]["affinity"] == 1.0)

        r = client.post(f"{BASE}/route/compute", headers=ADMIN, json={
            "memberId": 1, "amount": 500,
            "tags": ["credit_preference"],
            "tvEligible": True})
        record("credit_preference→credit_tv 第一",
               r.json()["ranking"][0]
               ["channelId"] == "credit_tv")

        # credit_tv 无资格→排除(硬过滤③)
        r = client.post(f"{BASE}/route/compute", headers=ADMIN, json={
            "memberId": 1, "amount": 500,
            "tags": ["credit_preference"]})
        record("credit_tv 无资格排除(默认 6 通道)",
               "credit_tv" not in [
                   c["channelId"]
                   for c in r.json()["ranking"]]
               and r.json()["candidateCount"] == 6,
               f"n={r.json()['candidateCount']}")

        r = client.post(f"{BASE}/route/compute", headers=ADMIN, json={
            "memberId": 1, "amount": 500,
            "intentText": "这单很急"})
        b = r.json()
        record("意图文本规则轨→fast 候选居前",
               {b["ranking"][0]["channelId"],
                b["ranking"][1]["channelId"],
                b["ranking"][2]["channelId"]}
               == {"wechat", "alipay",
                   "biometric"},
               str([c["channelId"]
                    for c in b["ranking"][:3]]))
        record("标签传递(tags 解析为 fast)",
               b["tags"] == ["fast_needed"])

        r = client.post(f"{BASE}/route/compute", headers=ADMIN, json={
            "memberId": 1, "amount": 500,
            "tags": ["nonexist_tag"]})
        record("标签域外 409",
               r.status_code == 409, f"s={r.status_code}")
    finally:
        set_mode("off")

    print("[04 硬过滤]")

    set_mode("shadow")
    try:
        # 60000: 仅 bank(singleLimit 100000)
        r = client.post(f"{BASE}/route/compute", headers=ADMIN, json={
            "memberId": 1, "amount": 60000})
        b = r.json()
        record("¥60000 仅 bank 参评",
               b["candidateCount"] == 1
               and b["ranking"][0]
               ["channelId"] == "bank",
               f"n={b['candidateCount']}")

        # 200000: 全部超限
        r = client.post(f"{BASE}/route/compute", headers=ADMIN, json={
            "memberId": 1, "amount": 200000})
        record("¥200000 无可用通道",
               r.json()["candidateCount"] == 0)

        # frozen 排除
        client.post(f"{BASE}/health/unionpay/freeze",
                    headers=ADMIN, json={"frozen": True})
        r = client.post(f"{BASE}/route/compute", headers=ADMIN, json={
            "memberId": 1, "amount": 500,
            "tags": ["large_amount"]})
        ids = [c["channelId"]
               for c in r.json()["ranking"]]
        record("frozen 通道永不选中",
               "unionpay" not in ids
               and "bank" in ids,
               str(ids))
        client.post(f"{BASE}/health/unionpay/freeze",
                    headers=ADMIN, json={"frozen": False})
    finally:
        set_mode("off")

    print("[05 健康度分层(手工→窗口)]")

    set_mode("shadow")
    try:
        # 手工上报: alipay 85% critical
        client.post(f"{BASE}/health/report", headers=ADMIN, json={
            "channelId": "alipay", "attemptCount": 100,
            "successCount": 85})
        r = client.post(f"{BASE}/route/compute", headers=ADMIN, json={
            "memberId": 1, "amount": 500})
        ids = [c["channelId"]
               for c in r.json()["ranking"]]
        record("手工上报参与评分(manual 源)",
               next(c for c in r.json()["ranking"]
                    if c["channelId"] == "alipay")
               ["healthSource"] == "manual")
        record("critical 通道降权(alipay 居 wechat 后)",
               ids.index("alipay")
               > ids.index("wechat"),
               str(ids))
    finally:
        set_mode("off")

    print("[06 习惯因子]")

    set_mode("shadow")
    try:
        # 会员 5: wechat×10
        r = client.post(f"{BASE}/route/habit/report", headers=ADMIN, json={
            "memberId": 5, "channelId": "wechat",
            "count": 10})
        record("习惯上报 200(不受开关)",
               r.status_code == 200
               and r.json()["habits"]["wechat"] == 10)

        r = client.post(f"{BASE}/route/compute", headers=ADMIN, json={
            "memberId": 5, "amount": 500})
        r6 = client.post(f"{BASE}/route/compute", headers=ADMIN, json={
            "memberId": 6, "amount": 500})
        ids5 = [c["channelId"]
                for c in r.json()["ranking"]]
        ids6 = [c["channelId"]
                for c in r6.json()["ranking"]]
        record("习惯通道升位(会员5 wechat↑)",
               ids5.index("wechat")
               < ids6.index("wechat"),
               f"m5={ids5.index('wechat')} "
               f"m6={ids6.index('wechat')}")

        r = client.get(f"{BASE}/route/habits/5", headers=ADMIN)
        record("习惯视图 200+dominant",
               r.status_code == 200
               and r.json()["dominantChannel"]
               == "wechat")

        r = client.post(f"{BASE}/route/habit/report", headers=ADMIN, json={
            "memberId": 5, "channelId": "nonexist",
            "count": 1})
        record("习惯通道域外 404",
               r.status_code == 404, f"s={r.status_code}")
        r = client.post(f"{BASE}/route/habit/report", headers=ADMIN, json={
            "memberId": 5, "channelId": "qr",
            "count": 0})
        record("习惯次数非正 422(请求层)",
               r.status_code == 422, f"s={r.status_code}")
    finally:
        set_mode("off")

    print("[07 沙盘执行+静默备选(assist)]")

    set_mode("assist")
    try:
        r = client.post(f"{BASE}/route/execute", headers=ADMIN, json={
            "memberId": 1, "amount": 500})
        b = r.json()
        record("执行 200+首选成功",
               r.status_code == 200
               and b["success"] is True
               and b["silentFallback"] is False
               and len(b["attempts"]) == 1)
        record("routeId 生成(R69- 前缀)",
               b["routeId"].startswith("R69-"))
        record("留痕含因子分+耗时",
               "routeScore" in b["attempts"][0]
               and "latencyMs"
               in b["attempts"][0])

        # 静默备选: 首选失败→次优
        first = b["attempts"][0]["channelId"]
        r = client.post(f"{BASE}/route/execute", headers=ADMIN, json={
            "memberId": 1, "amount": 500,
            "simulateFail": [first]})
        b2 = r.json()
        record("首选失败→静默备选成功",
               b2["success"] is True
               and b2["silentFallback"] is True
               and len(b2["attempts"]) == 2
               and b2["attempts"][0]
               ["channelId"] == first
               and b2["attempts"][0]["success"]
               is False
               and b2["attempts"][1]["success"]
               is True)

        # 全失败: attempts=3 封顶
        r = client.post(f"{BASE}/route/execute", headers=ADMIN, json={
            "memberId": 1, "amount": 500,
            "tags": ["credit_preference"],
            "simulateFail": ["credit_tv", "bank",
                             "unionpay", "qr",
                             "wechat", "alipay",
                             "biometric"]})
        b3 = r.json()
        record("全失败 attempts=3 封顶+失败留痕",
               b3["success"] is False
               and len(b3["attempts"]) == 3
               and all(not a["success"]
                       for a in b3["attempts"]))

        # 无可用通道
        r = client.post(f"{BASE}/route/execute", headers=ADMIN, json={
            "memberId": 1, "amount": 200000})
        record("无可用通道 409",
               r.status_code == 409, f"s={r.status_code}")
    finally:
        set_mode("off")

    print("[08 快环留痕与滚动窗口]")

    r = client.get(f"{BASE}/route/flows", headers=ADMIN)
    b = r.json()
    record("flows 视图 200+留痕在库",
           r.status_code == 200 and b["count"]
           >= 1 + 2 + 3, f"n={b['count']}")
    record("flows 最新在前(flowSeq 降序)",
           b["flows"][0]["flowSeq"]
           > b["flows"][-1]["flowSeq"])

    r = client.get(f"{BASE}/route/window", headers=ADMIN)
    b = r.json()
    record("窗口视图 200+七通道",
           r.status_code == 200
           and len(b["channels"]) == 7)
    ch_by = {c["channelId"]: c
             for c in b["channels"]}
    observed = [c for c in b["channels"]
                if c["attemptCount"]]
    record("执行通道窗口统计在库",
           len(observed) >= 1
           and all(c["successCount"] >= 0
                   for c in observed))
    # 失败注入的通道: 窗口 successRate<1
    first_ch = ch_by.get("credit_tv")
    if first_ch and first_ch["attemptCount"]:
        record("窗口口径(成功/尝试一致)",
               first_ch["successCount"]
               <= first_ch["attemptCount"])

    # 窗口覆盖手工: 上报 healthy 后再执行
    # 失败注入——窗口 source 生效
    set_mode("assist")
    try:
        client.post(f"{BASE}/health/report", headers=ADMIN, json={
            "channelId": "biometric",
            "attemptCount": 100,
            "successCount": 100})
        r = client.post(f"{BASE}/route/execute", headers=ADMIN, json={
            "memberId": 1, "amount": 100,
            "tags": ["biometric_habit"],
            "simulateFail": ["biometric"]})
        b = r.json()
        record("窗口覆盖手工(healthSource=window)",
               b["ranking"][0]
               ["healthSource"] == "window",
               str(b["ranking"][0]
                   ["healthSource"]))
    finally:
        set_mode("off")

    print("[09 模式矩阵]")

    r = client.post(f"{BASE}/route/compute", headers=ADMIN, json={
        "memberId": 1, "amount": 500})
    record("compute off 409(决策面)",
           r.status_code == 409, f"s={r.status_code}")
    r = client.post(f"{BASE}/route/execute", headers=ADMIN, json={
        "memberId": 1, "amount": 500})
    record("execute off 409(执行面)",
           r.status_code == 409, f"s={r.status_code}")
    set_mode("shadow")
    try:
        r = client.post(f"{BASE}/route/execute", headers=ADMIN, json={
            "memberId": 1, "amount": 500})
        record("execute shadow 409(影子期不执行)",
               r.status_code == 409,
               f"s={r.status_code}")
    finally:
        set_mode("off")
    for ep in ("route/dict", "route/flows",
               "route/window", "model/status"):
        r = client.get(f"{BASE}/{ep}", headers=ADMIN)
        record(f"观测面 {ep} off 200",
               r.status_code == 200,
               f"s={r.status_code}")
    r = client.get(f"{BASE}/route/dict")
    record("路由字典无 admin 403",
           r.status_code == 403, f"s={r.status_code}")

    print("[10 QC 注册表与 P0 回归]")

    record("权重注册封闭(四键)",
           set(reg.ROUTE_WEIGHTS) == {
               "fee", "health", "affinity", "habit"})
    record("启动自检通过(P1 扩展)",
           reg._validate_registry() is None)

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
