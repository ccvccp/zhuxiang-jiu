"""73号·AI智能会员体验大模型 P3 专项测试
(预判代办: 下一步预判+授权白名单三档
+grant/revoke 台账+代办执行四态)

运行方式:
    python test_member73_p3.py

覆盖(73号规划 §四 4.4/§十 P3):
    - 注册表 P3 封闭: 动作五域/
      风险三档/映射闭合/资金类
      不渗白名单(铁律自检)/授权源/
      结果四态
    - 预判: 信号确定性采集
      (RECEIVED 订单→review_order
      top1/资料缺口→profile_
      completion/积分>0→claim/
      无信号→None)/频率排序
    - 授权: 用户面 grant(域外 409/
      资金类 409/重复 409)/
      revoke 即时生效(未授权 404/
      重复 409)/台账位图
    - 执行: 决策面 off 409/
      白名单外 rejected(留痕)/
      未授权 rejected/low+granted
      →executed/medium 授权后仍
      prefilled(资金铁律)/
      KILL 拒绝
    - 隐性容忍度: revoke 后 execute
      立即 rejected(授权位图=边界)
    - QC: P1/P2 端点无回归/
      member 零修改
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
os.environ["ATTRACT72_MODE"] = "off"
os.environ["MEMBER73_MODE"] = "off"
os.environ.pop("MEMBER73_KILL", None)

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
    BASE = "/api/member73"

    print("[01 注册表 P3 封闭]")

    from services import member73_registry as reg

    record("代办动作五域封闭",
           set(reg.DELEGATE_ACTIONS) == {
               "profile_completion",
               "benefit_claim",
               "renewal_prefill",
               "review_order",
               "address_confirm"})
    record("风险映射闭合(五动作全覆盖)",
           set(reg.DELEGATE_RISK)
           == set(reg.DELEGATE_ACTIONS))
    record("资金类不渗白名单(铁律)",
           not (set(reg.CONFIRM_ONLY_ACTIONS)
                & set(reg.DELEGATE_ACTIONS)))
    record("中风险=续费预填(唯一)",
           [a for a, (t, _) in
            reg.DELEGATE_RISK.items()
            if t == "medium"]
           == ["renewal_prefill"])
    record("结果四态封闭",
           set(reg.DELEGATE_RESULTS) == {
               "executed", "prefilled",
               "confirmed", "rejected"})
    record("启动自检通过(导入即验)",
           reg._validate_registry() is None)

    print("[02 造数(预判信号)]")

    from repositories.member_repository import (
        MemberRepository,
    )
    member_repo = MemberRepository()

    # A: 资料全+5 单 RECEIVED(未评价)
    # +积分 300(3 批次<5)→ review_order
    # 频率 top1
    m_a = await member_repo.create({
        "phone": "13900000021",
        "password": "test123456",
        "nickname": "预判A",
        "avatar": "a.png",
        "gender": 1,
        "birthdate": "1990-01-01",
        "level": 1, "growth_value": 400,
        "points": 300, "status": 1,
        "created_at":
            "2026-09-03T12:00:00+00:00",
    })
    MID_A = m_a["id"]
    # B: 资料全+无单+积分 0(无信号)
    m_b = await member_repo.create({
        "phone": "13900000022",
        "password": "test123456",
        "nickname": "全量B",
        "avatar": "a.png",
        "gender": 1,
        "birthdate": "1990-01-01",
        "level": 1, "growth_value": 0,
        "points": 0, "status": 1,
        "created_at":
            "2026-09-03T12:00:00+00:00",
    })
    MID_B = m_b["id"]
    # C: L5 资料全+无单(仅续费预填信号)
    m_c = await member_repo.create({
        "phone": "13900000023",
        "password": "test123456",
        "nickname": "顶级C",
        "avatar": "c.png",
        "gender": 1,
        "birthdate": "1990-01-01",
        "level": 5, "growth_value": 12000,
        "points": 0, "status": 1,
        "created_at":
            "2026-09-03T12:00:00+00:00",
    })
    MID_C = m_c["id"]

    from repositories.order_repository import (
        OrderRepository,
    )
    order_repo = OrderRepository()
    for i in range(5):
        await order_repo.create({
            "orderId": f"ORD-M73-P3-{i}",
            "memberId": MID_A,
            "status": "RECEIVED",
            "priceDetail": {
                "actualAmount": 200},
            "createdAt":
                "2026-09-10T10:00:00"
                "+00:00",
        })

    print("[03 下一步预判(确定性排序)]")

    r = client.post(f"{BASE}/delegate/predict",
                    headers={
                        "X-Member-Id":
                            str(MID_A)})
    pr = r.json()["data"]
    record("A 预判 review_order top1"
           "(5 单未评价)",
           r.status_code == 200
           and pr["topAction"]
           == "review_order"
           and pr["topReason"]
           == "已收货未评价订单 5 单",
           f"p={pr}")
    record("候选含 claim(积分 300→3 批次)",
           {c["action"] for c in
            pr["candidates"]}
           == {"review_order",
               "benefit_claim"}
           and pr["candidates"][1]
           ["signal"] == 3,
           f"c={pr.get('candidates')}")
    record("未授权→granted False",
           pr["granted"] is False
           and pr["riskTier"] == "low",
           f"p={pr}")

    r = client.post(f"{BASE}/delegate/predict",
                    headers={
                        "X-Member-Id":
                            str(MID_B)})
    pr_b = r.json()["data"]
    record("B 无信号→top None(诚实)",
           pr_b["topAction"] is None
           and pr_b["candidates"] == [],
           f"p={pr_b}")

    r = client.post(f"{BASE}/delegate/predict",
                    headers={
                        "X-Member-Id":
                            str(MID_C)})
    pr_c = r.json()["data"]
    record("C L5→renewal_prefill"
           "(medium 风险档)",
           pr_c["topAction"]
           == "renewal_prefill"
           and pr_c["riskTier"]
           == "medium",
           f"p={pr_c}")

    r = client.post(f"{BASE}/delegate/predict",
                    headers={})
    record("未登录 predict 401",
           r.status_code == 401,
           f"s={r.status_code}")

    print("[04 授权 grant/revoke(用户面)]")

    r = client.post(f"{BASE}/delegate/grant",
                    headers={
                        "X-Member-Id":
                            str(MID_A)},
                    json={"memberId": 0,
                          "action":
                              "review_order"})
    g1 = r.json()["data"]
    record("授权 review_order 200",
           r.status_code == 200
           and g1["granted"] is True
           and g1["source"] == "user",
           f"g={g1}")

    r = client.post(f"{BASE}/delegate/grant",
                    headers={
                        "X-Member-Id":
                            str(MID_A)},
                    json={"memberId": 0,
                          "action":
                              "review_order"})
    record("重复授权 409",
           r.status_code == 409,
           f"s={r.status_code}")

    r = client.post(f"{BASE}/delegate/grant",
                    headers={
                        "X-Member-Id":
                            str(MID_A)},
                    json={"memberId": 0,
                          "action": "payment"})
    err = r.json().get(
        "error", r.json().get("detail", ""))
    record("资金类授权 409(铁律)",
           r.status_code == 409
           and "永不授权" in err,
           f"s={r.status_code} e={err}")

    r = client.post(f"{BASE}/delegate/grant",
                    headers={
                        "X-Member-Id":
                            str(MID_A)},
                    json={"memberId": 0,
                          "action": "bad"})
    record("动作域外 409",
           r.status_code == 409
           and "白名单" in r.json()
           .get("error", ""),
           f"s={r.status_code}")

    # 台账
    r = client.get(
        f"{BASE}/delegate/grants/{MID_A}",
        headers=ADMIN)
    record("台账 1 条有效位图",
           len(r.json()["data"]) == 1
           and r.json()["data"][0]
           ["action"] == "review_order",
           f"g={r.json()['data']}")

    # revoke
    r = client.post(f"{BASE}/delegate/revoke",
                    headers={
                        "X-Member-Id":
                            str(MID_A)},
                    json={"memberId": 0,
                          "action":
                              "review_order"})
    rv = r.json()["data"]
    record("revoke 即时生效",
           r.status_code == 200
           and rv["granted"] is False
           and rv["revokedAt"] != "",
           f"r={rv}")
    r = client.post(f"{BASE}/delegate/revoke",
                    headers={
                        "X-Member-Id":
                            str(MID_A)},
                    json={"memberId": 0,
                          "action":
                              "review_order"})
    record("重复 revoke 409",
           r.status_code == 409,
           f"s={r.status_code}")
    r = client.post(f"{BASE}/delegate/revoke",
                    headers={
                        "X-Member-Id":
                            str(MID_B)},
                    json={"memberId": 0,
                          "action":
                              "review_order"})
    record("未授权记录 revoke 404",
           r.status_code == 404,
           f"s={r.status_code}")

    r = client.get(
        f"{BASE}/delegate/grants/{MID_A}",
        headers=ADMIN)
    record("revoke 后台账清空"
           "(granted 位图口径)",
           len(r.json()["data"]) == 0,
           f"g={r.json()['data']}")

    print("[05 代办执行(四态)]")

    r = client.post(f"{BASE}/delegate/execute",
                    headers={
                        "X-Member-Id":
                            str(MID_A)},
                    json={"memberId": 0,
                          "action":
                              "review_order"})
    record("执行 off=409(决策面)",
           r.status_code == 409,
           f"s={r.status_code}")

    os.environ["MEMBER73_MODE"] = "shadow"
    try:
        # 白名单外 rejected(留痕)
        r = client.post(
            f"{BASE}/delegate/execute",
            headers={
                "X-Member-Id":
                    str(MID_A)},
            json={"memberId": 0,
                  "action": "payment"})
        ex1 = r.json()["data"]
        record("白名单外 rejected 留痕",
               r.status_code == 200
               and ex1["executeResult"]
               == "rejected"
               and "RT-03" in ex1["note"],
               f"e={ex1}")

        # 未授权 rejected
        r = client.post(
            f"{BASE}/delegate/execute",
            headers={
                "X-Member-Id":
                    str(MID_A)},
            json={"memberId": 0,
                  "action":
                      "profile_completion"})
        ex2 = r.json()["data"]
        record("未授权 rejected",
               ex2["executeResult"]
               == "rejected"
               and "未授权"
               in ex2["note"],
               f"e={ex2}")

        # 重新授权→low executed
        client.post(
            f"{BASE}/delegate/grant",
            headers={
                "X-Member-Id":
                    str(MID_A)},
            json={"memberId": 0,
                  "action":
                      "profile_completion"})
        r = client.post(
            f"{BASE}/delegate/execute",
            headers={
                "X-Member-Id":
                    str(MID_A)},
            json={"memberId": 0,
                  "action":
                      "profile_completion"})
        ex3 = r.json()["data"]
        record("low+授权→executed",
               ex3["executeResult"]
               == "executed"
               and ex3["riskTier"]
               == "low",
               f"e={ex3}")

        # revoke 后立即执行→rejected
        # (隐性容忍度=授权位图)
        client.post(
            f"{BASE}/delegate/revoke",
            headers={
                "X-Member-Id":
                    str(MID_A)},
            json={"memberId": 0,
                  "action":
                      "profile_completion"})
        r = client.post(
            f"{BASE}/delegate/execute",
            headers={
                "X-Member-Id":
                    str(MID_A)},
            json={"memberId": 0,
                  "action":
                      "profile_completion"})
        ex4 = r.json()["data"]
        record("revoke 后立即 rejected"
               "(容忍度=位图)",
               ex4["executeResult"]
               == "rejected",
               f"e={ex4}")

        # medium: 授权后仍只预填
        client.post(
            f"{BASE}/delegate/grant",
            headers={
                "X-Member-Id":
                    str(MID_C)},
            json={"memberId": 0,
                  "action":
                      "renewal_prefill"})
        r = client.post(
            f"{BASE}/delegate/execute",
            headers={
                "X-Member-Id":
                    str(MID_C)},
            json={"memberId": 0,
                  "action":
                      "renewal_prefill"})
        ex5 = r.json()["data"]
        record("medium 授权后仍 prefilled"
               "(预填不代付铁律)",
               ex5["executeResult"]
               == "prefilled"
               and ex5["riskTier"]
               == "medium"
               and "不代付"
               in ex5["note"],
               f"e={ex5}")

        # KILL 制动
        os.environ[
            "MEMBER73_KILL"] = "1"
        try:
            r = client.post(
                f"{BASE}/delegate/execute",
                headers={
                    "X-Member-Id":
                        str(MID_C)},
                json={"memberId": 0,
                      "action":
                          "renewal_prefill"})
            record("KILL 执行 409",
                   r.status_code == 409,
                   f"s={r.status_code}")
        finally:
            os.environ.pop(
                "MEMBER73_KILL", None)

        # 日志留痕(观测面)
        r = client.get(
            f"{BASE}/delegate/logs",
            headers=ADMIN)
        logs = r.json()["data"]
        results = [l["executeResult"]
                   for l in logs]
        record("五条日志四态齐"
               "(rejected×3/executed/"
               "prefilled)",
               len(logs) == 5
               and results.count(
                   "rejected") == 3
               and "executed"
               in results
               and "prefilled"
               in results,
               f"r={results}")

        # 预判 granted 联动(C 已授权
        # renewal_prefill)
        r = client.post(
            f"{BASE}/delegate/predict",
            headers={
                "X-Member-Id":
                    str(MID_C)})
        record("预判 granted 联动",
               r.json()["data"]
               ["granted"] is True,
               f"p={r.json()['data']}")
    finally:
        os.environ["MEMBER73_MODE"] = "off"

    print("[06 QC(零破坏·无回归)]")

    r = client.get(f"{BASE}/horizon/{MID_A}",
                   headers=ADMIN)
    record("P1 视野正常",
           r.status_code == 200,
           f"s={r.status_code}")
    r = client.get(
        f"{BASE}/effortless/{MID_A}",
        headers=ADMIN)
    record("P2 无感度正常",
           r.status_code == 200,
           f"s={r.status_code}")
    a_after = await member_repo.get_by_id(
        MID_A)
    record("member 零修改",
           a_after["level"] == 1
           and a_after["points"] == 300,
           f"m={a_after.get('level')}"
           f"/{a_after.get('points')}")

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
