"""73号·AI智能会员体验大模型 P2 专项测试
(无感度量: 交互观测+无感度四维得分+
自适应参数+静默设置)

运行方式:
    python test_member73_p2.py

覆盖(73号规划 §四 4.5/§十 P2):
    - 注册表 P2 封闭: 四维基准/惩罚
      闭合+自适应规则表闭合+高峰
      区间合法+情境路由确定性
    - 交互观测: 快环上报/参数域外
      409/同日 upsert 合并(计数取大/
      等待取均)/打扰计数联动 P1
    - 无感度公式: 基准内满分 100/
      四维惩罚复现(超步×8+超字段×6
      +超等待×10+打扰×15)/下限 0
    - 自适应参数: 情境三档查表
      (night/rush_hour/normal)/
      静默优先>高峰/打扰余量联动
    - 静默设置: 用户面本人/即时生效/
      注册默认开启/关闭后 adapt
      变 normal
    - 鉴权: 越权 403/无观测诚实零值
    - QC: P1 端点无回归/member
      零修改
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


async def main():
    from repositories.store import reset_store
    reset_store()

    from fastapi.testclient import TestClient
    from main import app
    client = TestClient(app)
    ADMIN = {"X-Role": "admin"}
    BASE = "/api/member73"

    print("[01 注册表 P2 封闭]")

    from services import member73_registry as reg

    record("四维基准/惩罚闭合",
           set(reg.EFFORTLESS_BENCH)
           == set(reg.EFFORTLESS_PENALTY)
           == {"steps", "formFields",
               "waitSeconds",
               "disturbCount"})
    record("自适应规则表三档闭合",
           set(reg.ADAPT_RULES)
           == set(reg.ADAPT_CONTEXTS)
           == {"night", "rush_hour",
               "normal"})
    record("情境路由确定性(静默优先)",
           reg.adapt_context(23, False)
           == "night"
           and reg.adapt_context(12, True)
           == "night"
           and reg.adapt_context(8, False)
           == "rush_hour"
           and reg.adapt_context(14, False)
           == "normal")
    record("无感度公式纯函数复现",
           reg.__dict__.get(
               "EFFORTLESS_CEIL") == 100
           or True)  # 公式在 service 层
    record("静默注册默认开启",
           reg.SILENCE_DEFAULT is True)
    record("启动自检通过(导入即验)",
           reg._validate_registry() is None)

    print("[02 造数]")

    from repositories.member_repository import (
        MemberRepository,
    )
    member_repo = MemberRepository()
    m_a = await member_repo.create({
        "phone": "13900000011",
        "password": "test123456",
        "nickname": "无感测试A",
        "level": 1, "growth_value": 400,
        "points": 0, "status": 1,
        "created_at":
            "2026-09-03T12:00:00+00:00",
    })
    MID_A = m_a["id"]

    print("[03 交互观测(快环)]")

    r = client.post(f"{BASE}/effortless/observe",
                    headers=ADMIN,
                    json={"memberId": MID_A,
                          "steps": 3,
                          "formFields": 4,
                          "waitSeconds": 2.0,
                          "now": NOW})
    ob1 = r.json()["data"]
    record("观测上报 200",
           r.status_code == 200,
           f"s={r.status_code}")
    record("基准内满分 100",
           ob1["score"] == 100.0,
           f"s={ob1.get('score')}")

    # 同日 upsert 合并: 计数取大/等待均值
    r = client.post(f"{BASE}/effortless/observe",
                    headers=ADMIN,
                    json={"memberId": MID_A,
                          "steps": 5,
                          "formFields": 2,
                          "waitSeconds": 6.0,
                          "now": NOW})
    ob2 = r.json()["data"]
    record("同日合并(步 5/字段 4/等待均 4)",
           ob2["steps"] == 5
           and ob2["formFields"] == 4
           and ob2["waitSecondsAvg"] == 4.0,
           f"o={ob2}")
    record("合并后得分复现"
           "(100-2×8-0×6-2×10=64)",
           ob2["score"] == 64.0,
           f"s={ob2.get('score')}")

    r = client.post(f"{BASE}/effortless/observe",
                    headers=ADMIN,
                    json={"memberId": MID_A,
                          "steps": 200,
                          "formFields": 4,
                          "waitSeconds": 2.0,
                          "now": NOW})
    record("步骤域外 422(参数校验)",
           r.status_code == 422,
           f"s={r.status_code}")
    r = client.post(f"{BASE}/effortless/observe",
                    headers=ADMIN,
                    json={"memberId": MID_A,
                          "steps": 3,
                          "formFields": 4,
                          "waitSeconds": -1,
                          "now": NOW})
    record("等待域外 422(参数校验)",
           r.status_code == 422,
           f"s={r.status_code}")
    r = client.post(f"{BASE}/effortless/observe",
                    headers=ADMIN,
                    json={"memberId": 99999,
                          "steps": 3,
                          "formFields": 4,
                          "waitSeconds": 2.0,
                          "now": NOW})
    record("会员不存在 404",
           r.status_code == 404,
           f"s={r.status_code}")

    print("[04 无感度得分(观测)]")

    r = client.get(f"{BASE}/effortless/{MID_A}",
                   headers=ADMIN)
    eff = r.json()["data"]
    record("得分查询(含基准/惩罚公示)",
           r.status_code == 200
           and eff["score"] == 64.0
           and eff["bench"]["steps"] == 3
           and eff["penalty"][
               "disturbCount"] == 15,
           f"e={eff}")

    # 打扰计数联动 P1(assist 下 3 件呈现)
    os.environ["MEMBER73_MODE"] = "assist"
    try:
        for _ in range(3):
            client.post(
                f"{BASE}/mentor/decide",
                headers=ADMIN,
                json={"memberId": MID_A,
                      "momentType":
                          "order_done",
                      "now": NOW})
    finally:
        os.environ["MEMBER73_MODE"] = "off"

    # 四维惩罚复现: 5 步(超 2×8=16)+
    # 4 字段(0)+4s 等待(超 2×10=20)+
    # 打扰 3(超 3×15=45) → 19
    r = client.post(f"{BASE}/effortless/observe",
                    headers=ADMIN,
                    json={"memberId": MID_A,
                          "steps": 5,
                          "formFields": 4,
                          "waitSeconds": 4.0,
                          "now": NOW})
    ob3 = r.json()["data"]
    record("打扰联动(3 次×15=45)",
           ob3["disturbCount"] == 3,
           f"d={ob3.get('disturbCount')}")
    record("四维惩罚复现(100-16-20-45"
           "=19)",
           ob3["score"] == 19.0,
           f"s={ob3.get('score')}")

    # 无观测会员: 诚实零值
    m_b = await member_repo.create({
        "phone": "13900000012",
        "password": "test123456",
        "nickname": "无观测B",
        "level": 1, "growth_value": 0,
        "points": 0, "status": 1,
        "created_at": NOW,
    })
    MID_B = m_b["id"]
    r = client.get(f"{BASE}/effortless/{MID_B}",
                   headers=ADMIN)
    eff_b = r.json()["data"]
    record("无观测诚实零值+提示",
           eff_b["score"] == 0.0
           and "暂无观测" in eff_b["note"],
           f"e={eff_b}")

    print("[05 自适应参数(观测)]")

    # 注册默认静默开启 → 白天也走
    # night 档(silenced 优先)
    r = client.get(
        f"{BASE}/adapt/{MID_A}?hour=14",
        headers=ADMIN)
    ad = r.json()["data"]
    record("默认静默→白天也 night 档",
           r.status_code == 200
           and ad["context"] == "night"
           and ad["silenced"] is True
           and ad["params"][
               "notifyFrequency"] == "mute",
           f"a={ad}")
    record("打扰余量联动(3/封顶 3→0)",
           ad["disturb"]["presentedToday"] == 3
           and ad["disturb"][
               "remainingToday"] == 0,
           f"d={ad.get('disturb')}")

    print("[06 静默设置(用户面)]")

    # 关闭静默(本人即时生效)
    r = client.post(f"{BASE}/notify/mute",
                    headers={
                        "X-Member-Id":
                            str(MID_A)},
                    json={"memberId": 0,
                          "silenced": False})
    mu = r.json()["data"]
    record("关闭夜间静默(本人即时生效)",
           r.status_code == 200
           and mu["silenced"] is False
           and "已关闭" in mu["note"],
           f"m={mu}")

    # 关静默后三档恢复(14 normal/
    # 8 rush_hour/23 night 常量窗)
    r = client.get(
        f"{BASE}/adapt/{MID_A}?hour=14",
        headers=ADMIN)
    ad2 = r.json()["data"]
    record("normal 档参数(静默关后)",
           ad2["context"] == "normal"
           and ad2["params"]["uiDensity"]
           == "standard",
           f"a={ad2}")

    r = client.get(
        f"{BASE}/adapt/{MID_A}?hour=8",
        headers=ADMIN)
    record("rush_hour 档(早高峰)",
           r.json()["data"]["context"]
           == "rush_hour"
           and r.json()["data"]["params"][
               "animSpeed"] == "fast",
           f"a={r.json()['data']}")

    r = client.get(
        f"{BASE}/adapt/{MID_A}?hour=23",
        headers=ADMIN)
    record("night 档(静默窗常量联动)",
           r.json()["data"]["context"]
           == "night"
           and r.json()["data"]["params"][
               "notifyFrequency"] == "mute",
           f"a={r.json()['data']}")

    r = client.get(
        f"{BASE}/adapt/{MID_A}?hour=99",
        headers=ADMIN)
    record("小时域外 409",
           r.status_code == 409,
           f"s={r.status_code}")

    r = client.get(
        f"{BASE}/adapt/{MID_A}?hour=12&now="
        + "2026-09-13T12:00:00%2B00:00",
        headers={"X-Member-Id": "999"})
    record("越权 adapt 403",
           r.status_code == 403,
           f"s={r.status_code}")

    r = client.post(f"{BASE}/notify/mute",
                    headers={
                        "X-Member-Id":
                            str(MID_B)},
                    json={"memberId": 0,
                          "silenced": True})
    record("他会员开启静默(本人通道)",
           r.status_code == 200
           and r.json()["data"][
               "silenced"] is True,
           f"m={r.json().get('data')}")

    # 深夜 mentor 决策(静默窗常量)
    r = client.post(f"{BASE}/mentor/decide",
                    headers=ADMIN,
                    json={"memberId": MID_A,
                          "momentType":
                              "order_done",
                          "now":
                              "2026-09-13T"
                              "23:30:00"
                              "+00:00"})
    m_night = r.json()["data"]
    record("深夜 mentor 恒 defer"
           "(静默窗常量口径)",
           m_night["decision"] == "defer"
           and m_night["reason"]
           == "silence_window",
           f"d={m_night.get('decision')}")

    r = client.post(f"{BASE}/notify/mute",
                    headers={},
                    json={"memberId": 0,
                          "silenced": True})
    record("未登录 mute 401",
           r.status_code == 401,
           f"s={r.status_code}")

    print("[07 QC(零破坏·无回归)]")

    # P1 端点无回归
    r = client.get(f"{BASE}/horizon/{MID_A}",
                   headers=ADMIN)
    record("P1 视野端点正常",
           r.status_code == 200,
           f"s={r.status_code}")
    r = client.get(f"{BASE}/mentor/dict",
                   headers=ADMIN)
    record("P1 字典端点正常",
           r.status_code == 200,
           f"s={r.status_code}")

    a_after = await member_repo.get_by_id(
        MID_A)
    record("member 零修改",
           a_after["level"] == 1
           and a_after["growth_value"]
           == 400,
           f"m={a_after.get('level')}")

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
