"""73号·AI智能会员体验大模型 P1 专项测试
(视野与唤醒: 升级视野引擎+情境引导时机
+响应回流+权益对比告知)

运行方式:
    python test_member73_p1.py

覆盖(73号规划 §四 4.1/4.2/4.3/§十 P1):
    - 注册表 P1 封闭: 模式四档/时刻
      域/入口域/缺口分档单调/三态/
      形式域/静默窗/封顶/影子期/
      权益矩阵键域 L1-L5 闭合
    - 鉴权: 会员面本人访问/越权 403/
      admin 通道
    - 视野引擎: 缺口公式/消费史外推
      预计到达/保级风险窗/冷启动影子位/
      L5 顶级无 next
    - 时机决策: 触发分三因子查表
      复现(order_done 0.72 present/
      points_changed 0.40 defer/
      profile_gap×home 0.144 abandon)/
      hint 文案数字插值/factors 留痕
    - 静默窗: 深夜 23 点 present→defer
    - 打扰封顶: 3 次呈现后熔断 abandon
    - 影子期: 新会员留痕不呈现 shadow
    - L5: abandon top_level
    - 呈现位: MODE off/shadow 留痕
      rendered=False; assist rendered
    - 响应回流: click/重复 409/不存在
      404/类型域外 409
    - 形式学习: 连续忽略 2 次→
      progress_hint 降权→card 接替
    - 权益对比: L1→L2 卡片(新增/持续
      权益+保级要求数字)/L5 预览 409
    - 告知: 决策面 off 409/升级未完成
      409/reveal 即效与需领取分类
    - QC: member 数据零修改/智客端点
      正常(叠加铁律)
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


NOW = "2026-09-13T12:00:00+00:00"
NIGHT = "2026-09-13T23:30:00+00:00"


async def main():
    from repositories.store import reset_store
    reset_store()

    from fastapi.testclient import TestClient
    from main import app
    client = TestClient(app)
    ADMIN = {"X-Role": "admin"}
    BASE = "/api/member73"

    print("[01 注册表 P1 封闭]")

    from services import member73_registry as reg

    record("模式四档封闭",
           set(reg.MODE_VALUES) == {
               "off", "shadow", "assist",
               "full"})
    record("时刻域四类封闭",
           set(reg.MOMENT_WEIGHTS) == {
               "order_done", "achieved",
               "points_changed",
               "profile_gap"})
    record("入口域封闭",
           set(reg.ENTRY_WEIGHTS) == {
               "order_page", "profile_page",
               "points_page", "home"})
    record("缺口分档单调递减",
           reg.gap_factor(0.95) == 1.0
           and reg.gap_factor(0.75) == 0.8
           and reg.gap_factor(0.1) == 0.1)
    record("静默窗判定(跨午夜)",
           reg.in_silence(23) is True
           and reg.in_silence(3) is True
           and reg.in_silence(12) is False)
    record("权益分类确定性",
           reg.classify_benefit("专属折扣 92 折")
           == "instant"
           and reg.classify_benefit(
               "生日礼: ¥100 生日券")
           == "claim")
    record("启动自检通过(导入即验)",
           reg._validate_registry() is None)

    print("[02 造数(三会员+消费史)]")

    from repositories.member_repository import (
        MemberRepository,
    )
    member_repo = MemberRepository()

    # 会员 A: L1 成长值 400(缺口 80%),
    # 注册 10 天前, 周期 340 天前(临期)
    from datetime import datetime, UTC
    m_a = await member_repo.create({
        "phone": "13900000001",
        "password": "test123456",
        "nickname": "视野测试A",
        "level": 1, "growth_value": 400,
        "points": 0, "status": 1,
        "created_at":
            "2026-09-03T12:00:00+00:00",
        "levelUpdatedAt":
            "2025-10-08T12:00:00+00:00",
        "periodConsume": 100.0,
    })
    MID_A = m_a["id"]
    # 会员 B: 注册 2 天(冷启动影子期)
    m_b = await member_repo.create({
        "phone": "13900000002",
        "password": "test123456",
        "nickname": "影子测试B",
        "level": 1, "growth_value": 400,
        "points": 0, "status": 1,
        "created_at":
            "2026-09-11T12:00:00+00:00",
    })
    MID_B = m_b["id"]
    # 会员 C: L5 顶级
    m_c = await member_repo.create({
        "phone": "13900000003",
        "password": "test123456",
        "nickname": "顶级测试C",
        "level": 5, "growth_value": 12000,
        "points": 0, "status": 1,
        "created_at":
            "2026-01-01T12:00:00+00:00",
        "levelUpdatedAt":
            "2026-01-01T12:00:00+00:00",
        "periodConsume": 20000.0,
    })
    MID_C = m_c["id"]
    # 会员 D: L2 保级风险窗(周期临期
    # 且消费未足——requirement 300)
    m_d = await member_repo.create({
        "phone": "13900000004",
        "password": "test123456",
        "nickname": "保级测试D",
        "level": 2, "growth_value": 400,
        "points": 0, "status": 1,
        "created_at":
            "2026-09-03T12:00:00+00:00",
        "levelUpdatedAt":
            "2025-10-08T12:00:00+00:00",
        "periodConsume": 100.0,
    })
    MID_D = m_d["id"]

    # 消费史(视野外推): A 一单实付 500
    from repositories.order_repository import (
        OrderRepository,
    )
    order_repo = OrderRepository()
    await order_repo.create({
        "id": "ORD-M73-1",
        "memberId": MID_A,
        "status": "PAID",
        "priceDetail": {"actualAmount": 500},
        "payment": {"paidAt":
                    "2026-09-10T10:00:00"
                    "+00:00"},
        "createdAt":
            "2026-09-10T10:00:00+00:00",
    })

    print("[03 鉴权(本人/越权/admin)]")

    r = client.get(f"{BASE}/horizon/{MID_A}")
    record("未登录 401", r.status_code == 401,
           f"s={r.status_code}")
    r = client.get(
        f"{BASE}/horizon/{MID_A}",
        headers={"X-Member-Id": "999"})
    record("越权访问 403",
           r.status_code == 403,
           f"s={r.status_code}")
    r = client.get(
        f"{BASE}/horizon/{MID_A}",
        headers={"X-Member-Id": str(MID_A)})
    record("本人访问 200",
           r.status_code == 200,
           f"s={r.status_code}")
    r = client.get(
        f"{BASE}/horizon/{MID_A}",
        headers=ADMIN)
    record("admin 访问 200",
           r.status_code == 200,
           f"s={r.status_code}")

    print("[04 视野引擎]")

    h = r.json()["data"]
    record("等级视野(L1→L2 缺口 100)",
           h["level"] == 1
           and h["next"]["level"] == 2
           and h["next"]["gapGrowth"] == 100
           and h["next"]["gapProgress"] == 0.8,
           f"h={h.get('next')}")
    est = h["estimatedArrival"]
    record("外推预计到达(500÷注册期≈50/天)",
           est is not None
           and 50.0 <= est["dailyRate"] <= 52.0
           and est["daysRemaining"] == 2,
           f"e={est}")
    record("冷启动影子位(A 已出影)",
           h["coldStart"]["inShadow"] is False,
           f"c={h.get('coldStart')}")

    # 保级风险窗(D: L2 周期临期+未足)
    r = client.get(
        f"{BASE}/horizon/{MID_D}",
        headers=ADMIN)
    keep = r.json()["data"]["keepRisk"]
    record("保级风险窗(临期+未足)",
           keep["requirement"] == 300
           and keep["remainingAmount"] == 200.0
           and keep["daysRemaining"] == 20
           and keep["atRisk"] is True,
           f"k={keep}")

    r = client.get(
        f"{BASE}/horizon/{MID_B}",
        headers=ADMIN)
    cold_b = r.json()["data"]["coldStart"]
    record("影子期位(B 注册 2 天)",
           cold_b["inShadow"] is True
           and cold_b["daysRemaining"]
           in (5, 6),
           f"c={cold_b}")

    r = client.get(
        f"{BASE}/horizon/{MID_C}",
        headers=ADMIN)
    record("L5 顶级无 next",
           r.json()["data"]["level"] == 5
           and r.json()["data"]["next"]
           is None,
           f"h={r.json()['data'].get('next')}")

    r = client.get(
        f"{BASE}/horizon/99999",
        headers=ADMIN)
    record("会员不存在 404",
           r.status_code == 404,
           f"s={r.status_code}")

    print("[05 触发分字典+时机决策]")

    r = client.get(f"{BASE}/mentor/dict",
                   headers=ADMIN)
    body = r.json()["data"]
    record("字典公示(四因子+三态+封顶)",
           r.status_code == 200
           and body["presentLine"] == 0.70
           and body["dailyDisturbCap"] == 3
           and body["decisionStates"] == [
               "present", "defer", "abandon"],
           f"d={body}")

    # A: order_done×order_page=0.9×0.8×1.0
    # =0.72 ≥0.70 present
    r = client.post(f"{BASE}/mentor/decide",
                    headers=ADMIN,
                    json={"memberId": MID_A,
                          "momentType":
                              "order_done",
                          "entry": "order_page",
                          "now": NOW})
    m1 = r.json()["data"]
    record("触发分复现(0.72)",
           m1["triggerScore"] == 0.72,
           f"s={m1.get('triggerScore')}")
    record("三态决策 present",
           m1["decision"] == "present"
           and m1["reason"] == "score",
           f"d={m1.get('decision')}")
    record("三因子留痕",
           len(m1["factors"]) == 3
           and m1["factors"][1]["value"]
           == 0.8,
           f"f={m1.get('factors')}")
    hint_text = m1["hintPayload"]["text"]
    record("hint 数字插值(差 ¥100/80%)",
           "仅差 ¥100" in hint_text
           and "进度 80%" in hint_text,
           f"t={hint_text}")
    record("off 模式留痕不呈现",
           m1["rendered"] is False,
           f"r={m1.get('rendered')}")

    # points_changed=0.5×0.8×1.0=0.40
    # → defer(观察线)
    r = client.post(f"{BASE}/mentor/decide",
                    headers=ADMIN,
                    json={"memberId": MID_A,
                          "momentType":
                              "points_changed",
                          "entry": "order_page",
                          "now": NOW})
    m2 = r.json()["data"]
    record("defer 档(0.40 观察线)",
           m2["triggerScore"] == 0.4
           and m2["decision"] == "defer",
           f"d={m2}")

    # profile_gap×home=0.4×0.8×0.6
    # =0.192 → abandon
    r = client.post(f"{BASE}/mentor/decide",
                    headers=ADMIN,
                    json={"memberId": MID_A,
                          "momentType":
                              "profile_gap",
                          "entry": "home",
                          "now": NOW})
    m3 = r.json()["data"]
    record("abandon 档(0.192)",
           abs(m3["triggerScore"]
               - 0.192) < 0.0001
           and m3["decision"] == "abandon"
           and m3["hintPayload"] == {},
           f"d={m3}")

    # 时刻/入口域外
    r = client.post(f"{BASE}/mentor/decide",
                    headers=ADMIN,
                    json={"memberId": MID_A,
                          "momentType": "bad",
                          "now": NOW})
    record("时刻域外 409",
           r.status_code == 409,
           f"s={r.status_code}")
    r = client.post(f"{BASE}/mentor/decide",
                    headers=ADMIN,
                    json={"memberId": MID_A,
                          "momentType":
                              "order_done",
                          "entry": "bad",
                          "now": NOW})
    record("入口域外 409",
           r.status_code == 409,
           f"s={r.status_code}")

    # L5 → abandon top_level
    r = client.post(f"{BASE}/mentor/decide",
                    headers=ADMIN,
                    json={"memberId": MID_C,
                          "momentType":
                              "order_done",
                          "now": NOW})
    m_c1 = r.json()["data"]
    record("L5 顶级 abandon",
           m_c1["decision"] == "abandon"
           and m_c1["reason"] == "top_level",
           f"d={m_c1}")

    print("[06 静默窗+影子期+呈现位]")

    # 深夜 23:30 → present 降级 defer
    r = client.post(f"{BASE}/mentor/decide",
                    headers=ADMIN,
                    json={"memberId": MID_A,
                          "momentType":
                              "order_done",
                          "now": NIGHT})
    m4 = r.json()["data"]
    record("静默窗 defer(silence_window)",
           m4["decision"] == "defer"
           and m4["reason"]
           == "silence_window"
           and m4["hour"] == 23,
           f"d={m4}")

    # 影子期会员 B: present 但不呈现
    r = client.post(f"{BASE}/mentor/decide",
                    headers=ADMIN,
                    json={"memberId": MID_B,
                          "momentType":
                              "order_done",
                          "now": NOW})
    m_b1 = r.json()["data"]
    record("影子期留痕不呈现",
           m_b1["decision"] == "present"
           and m_b1["rendered"] is False
           and m_b1["shadow"] is True,
           f"d={m_b1}")

    # MODE shadow: 留痕不呈现
    os.environ["MEMBER73_MODE"] = "shadow"
    try:
        r = client.post(
            f"{BASE}/mentor/decide",
            headers=ADMIN,
            json={"memberId": MID_A,
                  "momentType": "order_done",
                  "now": NOW})
        m5 = r.json()["data"]
        record("MODE shadow 留痕不呈现",
               m5["decision"] == "present"
               and m5["rendered"] is False,
               f"d={m5}")
    finally:
        os.environ["MEMBER73_MODE"] = "off"

    # MODE assist: 呈现
    os.environ["MEMBER73_MODE"] = "assist"
    try:
        r = client.post(
            f"{BASE}/mentor/decide",
            headers=ADMIN,
            json={"memberId": MID_A,
                  "momentType": "order_done",
                  "now": NOW})
        m6 = r.json()["data"]
        record("MODE assist 呈现",
               m6["decision"] == "present"
               and m6["rendered"] is True,
               f"d={m6}")

        # 打扰封顶: m6 已呈现 1 次, 再补
        # 2 次 order_done 呈现 → 第 4 次
        # present 熔断(achieved 0.64 属
        # defer 不计入——只有呈现件计数)
        for _ in range(2):
            client.post(
                f"{BASE}/mentor/decide",
                headers=ADMIN,
                json={"memberId": MID_A,
                      "momentType":
                          "order_done",
                      "now": NOW})
        r = client.post(
            f"{BASE}/mentor/decide",
            headers=ADMIN,
            json={"memberId": MID_A,
                  "momentType":
                      "order_done",
                  "now": NOW})
        m7 = r.json()["data"]
        record("打扰封顶熔断(daily_cap)",
               m7["decision"] == "abandon"
               and m7["reason"]
               == "daily_cap",
               f"d={m7}")

        print("[07 响应回流+形式学习]")

        # present 留痕: A 4 件(含未呈现)
        # +B 影子 1 件 + m7 前 3 呈现 = 6
        r = client.get(
            f"{BASE}/mentor/moments"
            "?decision=present",
            headers=ADMIN)
        moments = r.json()["data"]
        record("留痕视图(present 6 件"
               "含影子/未呈现)",
               len(moments) == 6,
               f"n={len(moments)}")

        m6_id = m6["momentId"]
        r = client.post(
            f"{BASE}/mentor/{m6_id}/respond",
            headers=ADMIN,
            json={"responseType": "click"})
        record("响应 click 200",
               r.status_code == 200
               and r.json()["data"]
               ["responded"] is True,
               f"s={r.status_code}")
        r = client.post(
            f"{BASE}/mentor/{m6_id}/respond",
            headers=ADMIN,
            json={"responseType": "click"})
        record("重复响应 409",
               r.status_code == 409,
               f"s={r.status_code}")
        r = client.post(
            f"{BASE}/mentor/99999/respond",
            headers=ADMIN,
            json={"responseType": "click"})
        record("时刻不存在 404",
               r.status_code == 404,
               f"s={r.status_code}")
        r = client.post(
            f"{BASE}/mentor/{m6_id}/respond",
            headers=ADMIN,
            json={"responseType": "bad"})
        err = r.json().get(
            "error", r.json().get("detail", ""))
        record("响应类型域外 409",
               r.status_code == 409
               and "响应类型无效" in err,
               f"s={r.status_code} e={err}")

        # 形式学习(双层):
        # ① 全局率淘汰: progress_hint 已
        # 3 件呈现仅 1 click(1/3=0.333)
        # < card 种子 0.5 → mh1 让位 card
        # ② 连续忽略降权: A×card 连续
        # 2 ignore → card 0.5×0.49=0.245
        # < progress_hint 0.333 → mh3
        # 回摆 progress_hint(形式轮替)
        r = client.post(
            f"{BASE}/mentor/decide",
            headers=ADMIN,
            json={"memberId": MID_A,
                  "momentType":
                      "order_done",
                  "now":
                      "2026-09-14T12:00:00"
                      "+00:00"})
        mh1 = r.json()["data"]
        record("全局响应率淘汰"
               "(progress_hint 1/3→card)",
               mh1["form"] == "card",
               f"f={mh1.get('form')}")
        r = client.post(
            f"{BASE}/mentor/"
            f"{mh1['momentId']}/respond",
            headers=ADMIN,
            json={"responseType": "ignore"})
        r = client.post(
            f"{BASE}/mentor/decide",
            headers=ADMIN,
            json={"memberId": MID_A,
                  "momentType":
                      "order_done",
                  "now":
                      "2026-09-15T12:00:00"
                      "+00:00"})
        mh2 = r.json()["data"]
        r = client.post(
            f"{BASE}/mentor/"
            f"{mh2['momentId']}/respond",
            headers=ADMIN,
            json={"responseType": "ignore"})
        r = client.post(
            f"{BASE}/mentor/decide",
            headers=ADMIN,
            json={"memberId": MID_A,
                  "momentType":
                      "order_done",
                  "now":
                      "2026-09-16T12:00:00"
                      "+00:00"})
        mh3 = r.json()["data"]
        record("连续忽略降权→形式回摆"
               "(card 0.245<0.333)",
               mh3["form"]
               == "progress_hint",
               f"f={mh3.get('form')}")
    finally:
        os.environ["MEMBER73_MODE"] = "off"

    print("[08 权益对比卡片]")

    r = client.get(
        f"{BASE}/benefits/preview/{MID_A}",
        headers=ADMIN)
    pv = r.json()["data"]
    record("L1→L2 对比卡片",
           pv["fromLevel"] == 1
           and pv["toLevel"] == 2
           and pv["threshold"] == 500
           and pv["gapGrowth"] == 100,
           f"p={pv}")
    record("新增权益(95 折/免邮 99)",
           "专属折扣 95 折" in pv["newBenefits"]
           and "免邮阈值 ¥99"
           in pv["newBenefits"],
           f"n={pv.get('newBenefits')}")
    record("持续权益+保级要求 300",
           pv["keptBenefits"] == []
           and pv["keepRequirement"] == 300,
           f"k={pv.get('keepRequirement')}")
    r = client.get(
        f"{BASE}/benefits/preview/{MID_C}",
        headers=ADMIN)
    record("L5 预览 409(最高等级)",
           r.status_code == 409,
           f"s={r.status_code}")

    print("[09 升级告知(reveal)]")

    r = client.post(f"{BASE}/benefits/reveal",
                    headers=ADMIN,
                    json={"memberId": MID_A,
                          "fromLevel": 1,
                          "toLevel": 2})
    record("reveal off=409(决策面)",
           r.status_code == 409,
           f"s={r.status_code}")

    os.environ["MEMBER73_MODE"] = "shadow"
    try:
        r = client.post(
            f"{BASE}/benefits/reveal",
            headers=ADMIN,
            json={"memberId": MID_A,
                  "fromLevel": 1,
                  "toLevel": 2})
        err = r.json().get(
            "error", r.json().get("detail", ""))
        record("升级未完成 409",
               r.status_code == 409
               and "升级未完成" in err,
               f"s={r.status_code} e={err}")
        r = client.post(
            f"{BASE}/benefits/reveal",
            headers=ADMIN,
            json={"memberId": MID_A,
                  "fromLevel": 1,
                  "toLevel": 3})
        err = r.json().get(
            "error", r.json().get("detail", ""))
        record("跃迁非法 409(须逐级)",
               r.status_code == 409
               and "逐级" in err,
               f"s={r.status_code} e={err}")

        # A 升级 L2(member 等级底盘变更)
        m_a["level"] = 2
        m_a["growth_value"] = 500
        await member_repo.save(MID_A, m_a)
        r = client.post(
            f"{BASE}/benefits/reveal",
            headers=ADMIN,
            json={"memberId": MID_A,
                  "fromLevel": 1,
                  "toLevel": 2})
        rv = r.json()["data"]
        record("reveal 200(卡片锁定 1→2)",
               r.status_code == 200
               and rv["cardPayload"][
                   "fromLevel"] == 1
               and rv["cardPayload"][
                   "toLevel"] == 2
               and "专属折扣 95 折"
               in rv["instantEffects"]
               and any("生日礼" in b
                       for b in
                       rv["claimRequired"]),
               f"r={rv}")

        r = client.get(
            f"{BASE}/benefits/reveals",
            headers=ADMIN)
        record("告知留痕 1 条",
               len(r.json()["data"]) == 1,
               f"n={len(r.json()['data'])}")
    finally:
        os.environ["MEMBER73_MODE"] = "off"

    print("[10 QC(零破坏·叠加铁律)]")

    a_after = await member_repo.get_by_id(
        MID_A)
    record("member 等级底盘零修改"
           "(A 由测试显式升 L2)",
           a_after["level"] == 2
           and a_after["growth_value"]
           == 500,
           f"m={a_after.get('level')}")
    c_after = await member_repo.get_by_id(
        MID_C)
    record("C 会员零触碰",
           c_after["level"] == 5
           and c_after["growth_value"]
           == 12000,
           f"c={c_after.get('level')}")

    # 智客端点正常(叠加铁律——零改动)
    r = client.get("/api/member-ai/overview",
                   headers=ADMIN)
    record("智客端点正常(无回归)",
           r.status_code == 200,
           f"s={r.status_code}")
    r = client.get(
        f"/api/member-ai/health/{MID_A}",
        headers=ADMIN)
    record("智客健康度正常(消费 A L2)",
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
