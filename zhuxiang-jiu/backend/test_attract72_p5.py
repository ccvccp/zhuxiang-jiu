"""72号·AI智能自动引流大模型 P5 专项测试
(热点卡位: 雷达联动四维势能+卡位决策
三段留痕+结果回流)

运行方式:
    python test_attract72_p5.py

覆盖(72号规划 §四 4.6/§七 P5/§十 P5):
    - 注册表 P5 扩展封闭: 势能权重和=1/
      裁决域/状态机/安全下限
    - 四维势能公式复现: 时效(黄金窗 48h
      线性衰减)/相关(词表命中密度)/安全
      (L1=1.0·L2=0.8)/转化(valueScore/100
      封顶)/加权综合——确定性·两次同值
    - 三裁决: 高风险自动拒追(安全硬编码
      优先于势能)/chase/observe
    - 机会清单: 雷达只读消费+价值下限
      过滤+按综合势能排序
    - 决策面门控: off=409/KILL 制动/
      事件不存在 404/价值不足 409/
      同事件幂等拒绝
    - shadow: 落档不执行(status=pending
      +executed=False)/不可人工确认
    - assist: 方案产出→须先确认才执行→
      确认→执行(情境参考注入)→结果回流
      闭环(closed+expectedPotential)
    - QC: 40号雷达数据零修改(只读铁律)
      /attract v1.0 零触碰
"""

import asyncio
import copy
import os
import sys
from datetime import (UTC, datetime as dt,
                      timedelta)

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
os.environ.pop("ATTRACT72_KILL", None)

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


def iso_now(offset_hours: float = 0.0) -> str:
    return (dt.now(UTC)
            + timedelta(hours=offset_hours)
            ).isoformat()


async def main():
    from repositories.store import reset_store
    reset_store()

    from fastapi.testclient import TestClient
    from main import app
    client = TestClient(app)
    ADMIN = {"X-Role": "admin"}
    BASE = "/api/attract72"

    print("[01 注册表 P5 扩展封闭]")

    from services import attract72_registry as reg
    from services.attract72_p5_service import (
        Attract72P5Service,
    )

    record("势能权重和=1",
           reg.POTENTIAL_WEIGHT_TIMELINESS
           + reg.POTENTIAL_WEIGHT_RELEVANCE
           + reg.POTENTIAL_WEIGHT_SAFETY
           + reg.POTENTIAL_WEIGHT_CONVERSION
           == 1.0)
    record("裁决域+状态机封闭",
           set(reg.HOTSPOT_VERDICTS) == {
               "chase", "observe", "reject"}
           and set(reg.HOTSPOT_DECISION_STATUSES)
           == {"pending", "confirmed",
               "executed", "closed"})
    record("安全下限+追投阈值域",
           0 <= reg.HOTSPOT_SAFETY_FLOOR <= 1
           and 0 <= reg.HOTSPOT_CHASE_THRESHOLD
           <= 1)
    record("雷达消费档位(L4 屏蔽)",
           set(reg.RADAR_CONSUME_GRADES)
           <= {"L1", "L2", "L3"})
    record("启动自检通过(导入即验)",
           reg._validate_registry() is None)

    print("[02 四维势能公式复现]")

    P = Attract72P5Service._potential

    fresh = {"grade": "L1",
             "title": "中秋送礼竹香白酒礼盒",
             "valueScore": 90.0,
             "detectedAt": iso_now()}
    pot = P(fresh)
    record("时效=黄金窗内满分",
           pot["timeliness"] == 1.0,
           f"p={pot}")
    record("相关=词表命中密度",
           pot["relevance"] == 1.0,
           f"p={pot}")
    record("安全=L1 满分",
           pot["safety"] == 1.0,
           f"p={pot}")
    record("转化=valueScore/100",
           pot["conversion"] == 0.9,
           f"p={pot}")
    record("综合=四维加权和(0.98)",
           abs(pot["total"] - 0.98) < 0.001,
           f"p={pot}")

    half = {"grade": "L1",
            "title": "中秋送礼竹香白酒礼盒",
            "valueScore": 90.0,
            "detectedAt": iso_now(-12.0)}
    pot12 = P(half)
    record("时效 12h→线性衰减 0.75",
           pot12["timeliness"] == 0.75,
           f"p={pot12}")
    record("综合衰减复现(0.9175)",
           abs(pot12["total"] - 0.9175)
           < 0.001,
           f"p={pot12}")

    stale = {"grade": "L1",
             "title": "中秋送礼竹香白酒礼盒",
             "valueScore": 90.0,
             "detectedAt": iso_now(-49.0)}
    record("时效 49h→黄金窗外归零",
           P(stale)["timeliness"] == 0.0,
           f"p={P(stale)}")

    l2 = {"grade": "L2", "title": "宠物美容",
          "valueScore": 40.0,
          "detectedAt": iso_now()}
    record("安全=L2 折算 0.8",
           P(l2)["safety"] == 0.8,
           f"p={P(l2)}")
    record("相关=单命中 1/3",
           P({"grade": "L1", "title": "酒文化",
              "valueScore": 40.0,
              "detectedAt": iso_now()}
             )["relevance"]
           == round(1 / 3, 4))
    record("转化=valueScore 封顶 1.0",
           P({"grade": "L1",
              "title": "中秋送礼竹香",
              "valueScore": 150.0,
              "detectedAt": iso_now()}
             )["conversion"] == 1.0)
    record("确定性(同输入同输出)",
           P(fresh) == P(fresh))

    print("[03 三裁决(安全硬编码优先)]")

    V = Attract72P5Service._verdict
    record("高风险自动拒追(safety 优先)",
           V({"safety": 0.3, "total": 0.9,
              "relevance": 1.0, "timeliness": 1.0,
              "conversion": 1.0}) == "reject")
    record("chase(综合>0.6)",
           V({"safety": 0.8, "total": 0.61,
              "relevance": 1.0, "timeliness": 1.0,
              "conversion": 1.0}) == "chase")
    record("observe(中间带)",
           V({"safety": 1.0, "total": 0.5,
              "relevance": 1.0, "timeliness": 1.0,
              "conversion": 1.0}) == "observe")

    print("[04 造数(40号雷达事件)]")

    from repositories.radar_repository import (
        RadarRepository,
    )
    radar_repo = RadarRepository()

    async def make_event(grade, title, category,
                        value, hours_ago,
                        heat=500):
        eid = await radar_repo.next_id("event")
        await radar_repo.save_event({
            "eventId": eid,
            "fingerprint": f"fp-p5-{eid}",
            "channelId": 1,
            "channelName": "热点频道",
            "platform": "douyin",
            "title": title,
            "summary": "热点摘要",
            "category": category,
            "heatBase": heat,
            "lifecycle": "new", "totalSlots": 1,
            "firstSeenAt": iso_now(-hours_ago),
            "lastSeenAt": iso_now(-hours_ago),
            "detectedAt": iso_now(-hours_ago),
            "grade": grade,
            "valueScore": value,
        })
        return eid

    # 只读快照(QC 用)
    radar_before = copy.deepcopy(
        await radar_repo.list_events(limit=2000))

    # ev1: L1·新鲜·高相关·高值 → chase(0.98)
    ev1 = await make_event(
        "L1", "中秋送礼新趋势竹香白酒礼盒",
        "finance", 90.0, 0.0, heat=900)
    # ev2: L2·新鲜·零相关·中值 → observe(0.57)
    ev2 = await make_event(
        "L2", "宠物美容教程新手指南",
        "current", 40.0, 0.0, heat=400)
    # ev3: L2·过期(96h)·零相关 → observe(0.32)
    ev3 = await make_event(
        "L2", "宠物美容教程进阶篇",
        "current", 40.0, 96.0, heat=300)
    # ev4: 价值分不足(<30) → 不入清单
    ev4 = await make_event(
        "L1", "中秋送礼竹香白酒礼盒",
        "history", 20.0, 0.0, heat=200)
    record("雷达 4 事件造数",
           len(radar_before) + 4
           == len(await radar_repo.list_events(
               limit=2000)))

    print("[05 机会清单(观测面)]")

    r = client.get(f"{BASE}/hotspot/opportunities",
                   headers=ADMIN)
    ops = r.json()["data"]
    ids = [o["radarEventId"] for o in ops]
    record("清单 200+价值下限过滤",
           r.status_code == 200
           and len(ops) == 3
           and ev4 not in ids,
           f"s={r.status_code} ids={ids}")
    record("按综合势能降序",
           ids == [ev1, ev2, ev3],
           f"ids={ids}")
    op1 = ops[0]
    record("ev1 势能+chase 裁决",
           op1["verdict"] == "chase"
           and abs(op1["potential"]["total"]
                   - 0.98) < 0.005,
           f"o={op1}")
    record("势能四维字段齐备",
           set(op1["potential"]) == {
               "timeliness", "relevance",
               "safety", "conversion", "total"},
           f"p={op1.get('potential')}")
    r = client.get(
        f"{BASE}/hotspot/opportunities",
        headers={"X-Role": "user"})
    record("非 admin 403",
           r.status_code == 403,
           f"s={r.status_code}")

    print("[06 决策面门控(off=409)]")

    r = client.post(f"{BASE}/hotspot/decide",
                    headers=ADMIN,
                    json={"radarEventId": ev1})
    err = r.json().get("error",
                       r.json().get("detail", ""))
    record("off 决策 409(决策面)",
           r.status_code == 409
           and "off" in err,
           f"s={r.status_code} e={err}")

    os.environ["ATTRACT72_MODE"] = "shadow"

    r = client.post(f"{BASE}/hotspot/decide",
                    headers=ADMIN,
                    json={"radarEventId": 99999})
    record("事件不存在 404",
           r.status_code == 404,
           f"s={r.status_code}")
    r = client.post(f"{BASE}/hotspot/decide",
                    headers=ADMIN,
                    json={"radarEventId": ev4})
    err = r.json().get("error",
                       r.json().get("detail", ""))
    record("价值分不足 409",
           r.status_code == 409
           and "下限" in err,
           f"s={r.status_code} e={err}")

    print("[07 shadow(落档不执行)]")

    r = client.post(f"{BASE}/hotspot/decide",
                    headers=ADMIN,
                    json={"radarEventId": ev1})
    d1 = r.json()["data"]
    record("shadow 决策 200",
           r.status_code == 200
           and d1["mode"] == "shadow",
           f"s={r.status_code}")
    record("落档不执行(pending+未执行)",
           d1["status"] == "pending"
           and d1["executed"] is False,
           f"d={d1}")
    record("裁决+势能留痕",
           d1["verdict"] == "chase"
           and abs(d1["potential"]["total"]
                   - 0.98) < 0.005,
           f"d={d1.get('verdict')}")
    plan = d1["plan"]
    record("方案·渠道(类别映射)",
           plan["channels"] == ["douyin",
                                "xiaohongshu"],
           f"p={plan.get('channels')}")
    record("方案·短链承接变体",
           plan["landingVariant"]
           == "benefit_first",
           f"p={plan.get('landingVariant')}")
    record("方案·预算建议(100×势能)",
           abs(plan["budgetSuggestion"]
               - 98.0) < 1.0,
           f"p={plan.get('budgetSuggestion')}")
    tw = plan["timeWindow"]
    record("方案·发布时机窗(小时对齐)",
           len(tw) == 2
           and tw[0] % 3600 == 0
           and tw[1] > tw[0],
           f"t={tw}")

    r = client.post(f"{BASE}/hotspot/decide",
                    headers=ADMIN,
                    json={"radarEventId": ev1})
    err = r.json().get("error",
                       r.json().get("detail", ""))
    record("同事件重复决策 409(幂等)",
           r.status_code == 409
           and "已决策" in err,
           f"s={r.status_code} e={err}")
    r = client.post(
        f"{BASE}/hotspot/{d1['decisionId']}"
        "/confirm", headers=ADMIN)
    err = r.json().get("error",
                       r.json().get("detail", ""))
    record("shadow 期不可人工确认",
           r.status_code == 409
           and "shadow" in err,
           f"s={r.status_code} e={err}")

    print("[08 assist(确认→执行→回流)]")

    os.environ["ATTRACT72_MODE"] = "assist"

    r = client.post(f"{BASE}/hotspot/decide",
                    headers=ADMIN,
                    json={"radarEventId": ev2})
    d2 = r.json()["data"]
    record("assist 决策 200(pending)",
           r.status_code == 200
           and d2["mode"] == "assist"
           and d2["status"] == "pending",
           f"s={r.status_code}")
    record("observe 裁决无方案",
           d2["verdict"] == "observe"
           and d2["plan"] is None,
           f"d={d2.get('verdict')}"
           f"/{d2.get('plan')}")

    r = client.post(
        f"{BASE}/hotspot/{d2['decisionId']}"
        "/execute", headers=ADMIN)
    err = r.json().get("error",
                       r.json().get("detail", ""))
    record("未确认执行 409",
           r.status_code == 409
           and "确认" in err,
           f"s={r.status_code} e={err}")

    r = client.post(
        f"{BASE}/hotspot/{d2['decisionId']}"
        "/confirm", headers=ADMIN)
    record("人工确认 200(confirmed)",
           r.status_code == 200
           and r.json()["data"]["status"]
           == "confirmed",
           f"s={r.status_code}")
    r = client.post(
        f"{BASE}/hotspot/{d2['decisionId']}"
        "/confirm", headers=ADMIN)
    record("重复确认 409",
           r.status_code == 409,
           f"s={r.status_code}")

    r = client.post(
        f"{BASE}/hotspot/{d2['decisionId']}"
        "/execute", headers=ADMIN)
    d2e = r.json()["data"]
    record("执行 200(情境参考注入)",
           r.status_code == 200
           and d2e["executed"] is True
           and d2e["executedAt"] != "",
           f"s={r.status_code} d={d2e}")

    r = client.post(
        f"{BASE}/hotspot/{d2['decisionId']}"
        "/outcome", headers=ADMIN,
        json={"impressions": 12000,
              "conversions": 86,
              "actualRoi": 2.4})
    d2o = r.json()["data"]
    record("结果回流 200(closed)",
           r.status_code == 200
           and d2o["status"] == "closed",
           f"s={r.status_code}")
    record("回流=预期 vs 实际闭环",
           d2o["outcome"]["impressions"]
           == 12000
           and d2o["outcome"]["actualRoi"] == 2.4
           and abs(d2o["outcome"]
                   ["expectedPotential"]
                   - 0.57) < 0.005,
           f"o={d2o.get('outcome')}")

    r = client.get(f"{BASE}/hotspot/decisions",
                    headers=ADMIN)
    decisions = r.json()["data"]
    record("决策台账(2 条留痕)",
           r.status_code == 200
           and len(decisions) == 2,
           f"d={len(decisions)}")
    r = client.get(
        f"{BASE}/hotspot/decisions"
        "?verdict=chase", headers=ADMIN)
    record("台账筛选(verdict)",
           len(r.json()["data"]) == 1
           and r.json()["data"][0]["verdict"]
           == "chase",
           f"d={r.json()['data']}")
    r = client.get(
        f"{BASE}/hotspot/decisions"
        "?status=closed", headers=ADMIN)
    record("台账筛选(status)",
           len(r.json()["data"]) == 1
           and r.json()["data"][0]["status"]
           == "closed",
           f"d={r.json()['data']}")

    print("[09 KILL 制动]")

    os.environ["ATTRACT72_KILL"] = "1"
    try:
        r = client.post(
            f"{BASE}/hotspot/decide",
            headers=ADMIN,
            json={"radarEventId": ev3})
        err = r.json().get(
            "error", r.json().get("detail", ""))
        record("KILL 决策 409",
               r.status_code == 409
               and "KILL" in err,
               f"s={r.status_code} e={err}")
        r = client.get(
            f"{BASE}/hotspot/opportunities",
            headers=ADMIN)
        record("KILL 观测面常开",
               r.status_code == 200,
               f"s={r.status_code}")
    finally:
        os.environ.pop("ATTRACT72_KILL", None)

    print("[10 QC(只读铁律·零触碰)]")

    radar_after = await radar_repo.list_events(
        limit=2000)
    before_ids = {e["eventId"]
                  for e in radar_before}
    record("40号雷达数据零修改(只读)",
           radar_before == [
               r for r in radar_after
               if r["eventId"] in before_ids],
           "雷达事件被修改或删除")

    from repositories.attract_repository import (
        AttractRepository,
    )
    attract_repo = AttractRepository()
    clicks = await attract_repo.list_clicks(
        limit=10000)
    record("attract v1.0 零触碰",
           len(clicks) == 0,
           f"c={len(clicks)}")

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
