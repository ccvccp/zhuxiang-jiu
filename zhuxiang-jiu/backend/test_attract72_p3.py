"""72号·AI智能自动引流大模型 P3 专项测试
(预算预判: 72h 预分配+偏差重博弈+探索
基金+三层时间尺度+系数 46号链)

运行方式:
    python test_attract72_p3.py

覆盖(72号规划 §四 4.5/§七 P3):
    - 注册表 P3 扩展封闭: 状态机/
      阈值/基金域/时间尺度/档案口径
    - 决策面门控: 生成/建议/执行 off=409;
      重博弈快环不受 MODE(无方案 409 语义)
    - 72h 预分配: 月池切片/探索基金
      15%(零GMV+低样本双上浮)/core
      配额(雷达提升+anti 定律压低)/
      幂等 superseded 留痕
    - 偏差监控: 无消耗→0/近似消耗
      →10% 不触发/超速消耗→35% 触发
    - 重博弈: 旧方案 rebalanced+新方案
      active+diff 留痕+消耗学习
    - 系数 46号链: propose(pending 建议书)
      →apply(v1.0 currentRate 显式变更
      +46号留痕清理)→再建议 409
    - KILL 制动: 生成/重博弈/执行全拒绝
    - QC: v1.0 数据零破坏(仅 currentRate
      变更)/池总量守恒
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


ANCHOR = "2026-09-11T11:00:00+00:00"
NOW = "2026-09-13T11:00:00+00:00"
NOW_Q = __import__("urllib.parse",
                   fromlist=["quote"]).quote(
    NOW, safe="")


async def main():
    from repositories.store import reset_store
    reset_store()

    from fastapi.testclient import TestClient
    from main import app
    client = TestClient(app)
    ADMIN = {"X-Role": "admin"}
    BASE = "/api/attract72"

    print("[01 注册表 P3 扩展封闭]")

    from services import attract72_registry as reg

    record("预分配状态机封闭",
           set(reg.FORECAST_STATUSES) == {
               "active", "superseded",
               "rebalanced"})
    record("三层时间尺度封闭",
           set(reg.TIME_SCALES) == {
               "minute", "day", "month"})
    record("偏差阈值/基金浮动域",
           reg.DEVIATION_THRESHOLD == 0.15
           and reg.EXPLORATION_MIN == 0.05
           and reg.EXPLORATION_MAX == 0.15)
    record("月窗占比(72h/720h)",
           reg.POOL_WINDOW_SHARE == 0.1)
    record("预算治理档案口径",
           reg.BUDGET_SCORER_ID
           == "growth_budget")
    record("启动自检通过(导入即验)",
           reg._validate_registry() is None)

    print("[02 造数(历史+雷达+anti 定律)]")

    from repositories.member_repository import (
        MemberRepository,
    )
    member_repo = MemberRepository()
    m = await member_repo.create({
        "id": 7002, "nickname": "预算P3测试",
        "phone": "13900007002",
        "password": "test123456"})
    await member_repo.save(7002, m)

    from services.promotion_service import (
        PromotionService,
    )
    zxbj = (await PromotionService()
            .claim_promo_code(
                member_id=7002,
                channel="wechat_miniprogram"))["code"]

    from services.attract_service import AttractService
    from repositories.attract_repository import (
        AttractRepository,
    )
    svc = AttractService()
    attract_repo = AttractRepository()

    async def make_click(channel, at):
        r = await svc.resolve_click(
            zxbj, utm_source=channel)
        click = await attract_repo.get_click(
            r["clickId"])
        click["at"] = at
        await attract_repo.save_click(click)
        return r["clickId"]

    # 历史(锚点前): xiaohongshu 高 GMV /
    # douyin 低 GMV
    xhs_clicks = []
    for _ in range(10):
        xhs_clicks.append(
            await make_click(
                "xiaohongshu",
                "2026-09-10T10:00:00+00:00"))
    for i in range(8):
        await svc.attach_registration(
            xhs_clicks[i], 7800 + i)
    for i in range(8):
        await svc.attach_order(
            xhs_clicks[i], f"ORD-X{i}",
            400.0, commission=20.0)
    dy_clicks = []
    for _ in range(6):
        dy_clicks.append(
            await make_click(
                "douyin",
                "2026-09-10T11:00:00+00:00"))
    await svc.attach_registration(
        dy_clicks[0], 7900)
    await svc.attach_order(
        dy_clicks[0], "ORD-D0", 50.0,
        commission=5.0)

    # 雷达 L1 finance 事件 → 信号(影响
    # douyin+xiaohongshu)
    from repositories.radar_repository import (
        RadarRepository,
    )
    radar_repo = RadarRepository()
    ev_id = await radar_repo.next_id("event")
    await radar_repo.save_event({
        "eventId": ev_id,
        "fingerprint": f"fp-p3-{ev_id}",
        "channelId": 1, "channelName": "财富故事会",
        "platform": "douyin",
        "title": "年轻人理财新趋势",
        "summary": "工资到手先存三成",
        "category": "finance",
        "heatBase": 800,
        "lifecycle": "new", "totalSlots": 1,
        "firstSeenAt": "2026-09-13T00:00:00Z",
        "lastSeenAt": "2026-09-13T00:00:00Z",
        "grade": "L1", "valueScore": 80.0,
    })
    from services.attract72_p1_service import (
        Attract72P1Service,
    )
    ingest = await Attract72P1Service() \
        .ingest_signals(today="2026-09-13")
    record("信号摄取(雷达1+节日2)",
           ingest["radar"] == 1
           and ingest["festival"] == 2,
           f"i={ingest}")

    # anti 定律种子(douyin 渠道压低)
    from repositories.attract72_repository import (
        Attract72Repository,
    )
    repo72 = Attract72Repository()
    law_id = await repo72.next_id("law")
    await repo72.save_law({
        "lawId": law_id, "kind": "anti",
        "dimension": "channel_feature",
        "factor": "channel:douyin",
        "statement": "douyin 渠道拉低转化",
        "conditions": {
            "dimension": "channel_feature",
            "factor": "channel:douyin"},
        "confidence": 0.8,
        "boundary": "样本 6/10·置信度 0.8",
        "validatedCount": 1, "decayCount": 0,
        "sourceInsightId": 0, "status": "active",
        "changeId": 0, "kbEntryId": 0,
        "syncedToKB": False,
        "createdAt": "2026-09-13T00:00:00+00:00",
        "publishedAt": "2026-09-13T00:00:00+00:00",
        "lastValidatedAt": "",
    })

    print("[03 决策面门控(off=409) ]")

    r = client.post(f"{BASE}/budget/forecast/"
                   "generate", headers=ADMIN,
                   json={"anchor": ANCHOR})
    record("生成 off=409(决策面)",
           r.status_code == 409,
           f"s={r.status_code}")
    r = client.post(f"{BASE}/budget/rates/"
                   "propose", headers=ADMIN)
    record("建议 off=409(决策面)",
           r.status_code == 409,
           f"s={r.status_code}")
    r = client.post(f"{BASE}/budget/rates/"
                   "apply", headers=ADMIN)
    record("执行 off=409(决策面)",
           r.status_code == 409,
           f"s={r.status_code}")
    # 重博弈=快环(不受 MODE)——无方案语义
    r = client.post(f"{BASE}/budget/rebalance/"
                   "auto", headers=ADMIN,
                   json={"now": NOW})
    err = r.json().get("error",
                       r.json().get("detail", ""))
    record("重博弈快环不受 MODE(无方案 409)",
           r.status_code == 409
           and "无活动方案" in err,
           f"s={r.status_code} e={err}")

    os.environ["ATTRACT72_MODE"] = "shadow"

    print("[04 72h 预分配生成]")

    r = client.post(f"{BASE}/budget/forecast/"
                   "generate", headers=ADMIN,
                   json={"anchor": ANCHOR})
    record("生成 200", r.status_code == 200,
           f"s={r.status_code}")
    # 幂等再生成 → 旧 superseded
    r = client.post(f"{BASE}/budget/forecast/"
                   "generate", headers=ADMIN,
                   json={"anchor": ANCHOR})
    f1 = r.json()["data"]

    record("月池切片(10000×10%)",
           f1["poolTotal"] == 10000.0
           and f1["windowBudget"] == 1000.0,
           f"p={f1.get('poolTotal')} "
           f"w={f1.get('windowBudget')}")
    record("探索基金 15%(零GMV+低样本双上浮)",
           f1["explorationRatio"] == 0.15
           and f1["explorationAmount"] == 150.0,
           f"r={f1.get('explorationRatio')} "
           f"a={f1.get('explorationAmount')}")
    alloc = {a["channel"]: a
             for a in f1["allocations"]}
    record("allocations 8 渠道全覆盖",
           len(f1["allocations"]) == 8
           and set(alloc) == {
               "douyin", "kuaishou", "wechat",
               "xiaohongshu", "bilibili",
               "taobao", "direct", "seo"},
           f"a={list(alloc)}")
    # core: xhs 3200×1.5=4800, douyin
    # 50×1.0=50 → 850×4800/4850=841.24
    record("core 配额(GMV×雷达提升归一)",
           abs(alloc["xiaohongshu"]["amount"]
               - 841.24) < 0.01
           and abs(alloc["douyin"]["amount"]
                   - 8.76) < 0.01,
           f"x={alloc['xiaohongshu']['amount']}"
           f" d={alloc['douyin']['amount']}")
    record("anti 定律压低 douyin 系数(0.9)",
           alloc["douyin"]["suggestedRate"] == 0.9
           and alloc["douyin"]["lawBoost"] == -0.5,
           f"d={alloc['douyin']}")
    record("雷达提升 xhs 系数(1.1)+hotspot",
           alloc["xiaohongshu"]
           ["suggestedRate"] == 1.1
           and alloc["xiaohongshu"]["hotspot"]
           is True
           and alloc["xiaohongshu"]["lift"]
           == 0.5,
           f"x={alloc['xiaohongshu']}")
    record("探索候选 6 渠道均分 25",
           alloc["wechat"]["exploration"]
           is True
           and alloc["wechat"]["amount"] == 25.0
           and alloc["wechat"]["suggestedRate"]
           == 1.0,
           f"w={alloc['wechat']}")
    record("三层时间尺度+月储备 9000",
           f1["timeScales"]["month"]
           == "战略储备 ¥9000.0"
           and set(f1["timeScales"]) == {
               "minute", "day", "month"},
           f"t={f1.get('timeScales')}")
    record("需求信号元数据(雷达1+节日2)",
           len(f1["demandSignals"]["radar"]) == 1
           and len(f1["demandSignals"]
                   ["festival"]) == 2,
           f"d={f1.get('demandSignals')}")
    record("期望 ROI(3250/165=19.7)",
           f1["expectedRoi"] == 19.7,
           f"r={f1.get('expectedRoi')}")
    record("置信度(样本驱动)",
           alloc["xiaohongshu"]["confidence"]
           == 0.5
           and alloc["douyin"]["confidence"]
           == 0.3,
           f"c={alloc['xiaohongshu'].get('confidence')}"
           f"/{alloc['douyin'].get('confidence')}")

    # 历史(再生成 → superseded 留痕)
    status = client.get(
        f"{BASE}/budget/forecast",
        headers=ADMIN).json()["data"]
    record("再生成旧方案 superseded",
           len(status["history"]) == 2
           and {h["status"] for h in
                status["history"]}
           == {"active", "superseded"},
           f"h={status['history']}")

    print("[05 探索基金状态]")

    exp = client.get(f"{BASE}/budget/exploration",
                     headers=ADMIN).json()["data"]
    record("基金候选 6 渠道",
           len(exp["candidates"]) == 6
           and {c["channel"] for c in
                exp["candidates"]}
           == {"kuaishou", "wechat",
               "bilibili", "taobao",
               "direct", "seo"},
           f"c={exp['candidates']}")
    record("月级战略储备",
           exp["monthlyReserve"] == 9000.0
           and exp["ratio"] == 0.15,
           f"e={exp.get('monthlyReserve')}")

    print("[06 偏差监控(阈值 15%) ]")

    # 无窗口内消耗 → 偏差 0
    status = client.get(
        f"{BASE}/budget/forecast?now={NOW_Q}",
        headers=ADMIN).json()["data"]
    record("无消耗→偏差 0",
           status["active"]["deviation"] == 0.0
           and status["active"]
           ["actualConsumed"] == 0.0,
           f"a={status['active']}")
    # 窗口内 5 单×佣金 120=600 vs 期望
    # 1000×0.6667=666.67 → 偏差 10%
    wc = []
    for _ in range(5):
        wc.append(await make_click(
            "wechat",
            "2026-09-12T10:00:00+00:00"))
    for i, cid in enumerate(wc):
        await svc.attach_registration(
            cid, 7910 + i)
        await svc.attach_order(
            cid, f"ORD-W{i}", 150.0,
            commission=120.0)
    status = client.get(
        f"{BASE}/budget/forecast?now={NOW_Q}",
        headers=ADMIN).json()["data"]
    act = status["active"]
    record("近似消耗→偏差 10% 不触发",
           abs(act["deviation"] - 0.10) < 0.001
           and act["expectedSofar"] == 666.67
           and act["elapsedRatio"] == 0.6667,
           f"a={act}")
    r = client.post(f"{BASE}/budget/rebalance/"
                   "auto", headers=ADMIN,
                   json={"now": NOW})
    err = r.json().get("error",
                       r.json().get("detail", ""))
    record("重博弈 409(偏差未达阈值)",
           r.status_code == 409
           and "偏差未达阈值" in err,
           f"s={r.status_code} e={err}")

    print("[07 超速消耗→偏差重博弈]")

    # 追加 3 单×佣金 100 → 900 vs
    # 666.67 → 偏差 35%
    for i in range(3):
        cid = await make_click(
            "wechat",
            "2026-09-12T11:00:00+00:00")
        await svc.attach_registration(
            cid, 7920 + i)
        await svc.attach_order(
            cid, f"ORD-V{i}", 100.0,
            commission=100.0)
    status = client.get(
        f"{BASE}/budget/forecast?now={NOW_Q}",
        headers=ADMIN).json()["data"]
    record("超速消耗→偏差 35%",
           abs(status["active"]["deviation"]
               - 0.35) < 0.001
           and status["active"]
           ["actualConsumed"] == 900.0,
           f"a={status['active']}")

    r = client.post(f"{BASE}/budget/rebalance/"
                   "auto", headers=ADMIN,
                   json={"now": NOW})
    rb = r.json()["data"]
    record("重博弈 200(触发)",
           r.status_code == 200
           and rb["newForecastId"]
           > rb["rebalancedId"],
           f"s={r.status_code} "
           f"rb={rb.get('newForecastId')}")
    record("偏差留痕(0.35)",
           rb["deviation"] == 0.35,
           f"d={rb.get('deviation')}")
    diff_map = {d["channel"]: d
                for d in rb["diff"]}
    # 新方案: wechat 入 core(1050 GMV)
    # → 850×1050/5900=151.27
    record("diff 留痕(wechat 探索→core)",
           "wechat" in diff_map
           and diff_map["wechat"]
           ["fromAmount"] == 25.0
           and abs(diff_map["wechat"]
                   ["toAmount"] - 151.27)
           < 0.01,
           f"d={diff_map.get('wechat')}")
    record("消耗学习(xhs 配额下调)",
           diff_map["xiaohongshu"]
           ["fromAmount"] == 841.24
           and diff_map["xiaohongshu"]
           ["toAmount"] < 841.24,
           f"d={diff_map.get('xiaohongshu')}")

    status = client.get(
        f"{BASE}/budget/forecast",
        headers=ADMIN).json()["data"]
    hist_status = {h["status"]
                   for h in status["history"]}
    record("旧方案 rebalanced+新方案 active",
           status["active"]["forecastId"]
           == rb["newForecastId"]
           and "rebalanced" in hist_status,
           f"h={status['history']}")

    print("[08 系数 46号链]")

    r = client.post(f"{BASE}/budget/rates/"
                   "propose", headers=ADMIN)
    prop = r.json()["data"]
    ch_map = {c["channel"]: c
              for c in prop["changes"]}
    record("建议书(2 渠道系数变更)",
           r.status_code == 200
           and prop["changeId"] > 0
           and len(prop["changes"]) == 2
           and ch_map["xiaohongshu"]["to"] == 1.1
           and ch_map["douyin"]["to"] == 0.9,
           f"p={prop}")

    # 46号 pending 留痕
    from repositories.ai_governance_repository \
        import AiGovernance46Repository
    change = await AiGovernance46Repository() \
        .get_change(prop["changeId"])
    record("46号建议书 pending 留痕",
           change is not None
           and change["status"] == "pending"
           and change["scorerId"]
           == "growth_budget",
           f"c={change}")

    # 未留痕执行拒绝: 新方案未 propose?
    # (本方案已 propose → 直接执行)
    r = client.post(f"{BASE}/budget/rates/"
                   "apply", headers=ADMIN)
    applied = r.json()["data"]
    record("执行 200(v1.0 currentRate)",
           r.status_code == 200
           and len(applied["applied"]) == 2,
           f"a={applied}")
    budgets = {b["channel"]: b
               for b in await
               attract_repo.list_budgets()}
    rate_view = {
        c: budgets[c]["currentRate"]
        for c in ("xiaohongshu", "douyin",
                  "wechat")}
    record("v1.0 系数双轨变更(1.1/0.9)",
           rate_view["xiaohongshu"] == 1.1
           and rate_view["douyin"] == 0.9
           and rate_view["wechat"] == 1.0,
           f"b={rate_view}")
    change = await AiGovernance46Repository() \
        .get_change(prop["changeId"])
    record("46号留痕清理(人工执行)",
           change["status"] == "rejected"
           and "人工执行" in change.get(
               "reviewNote", ""),
           f"c={change}")

    # 重复执行拒绝
    r = client.post(f"{BASE}/budget/rates/"
                   "apply", headers=ADMIN)
    err = r.json().get("error",
                       r.json().get("detail", ""))
    record("重复执行 409",
           r.status_code == 409
           and "已执行" in err,
           f"s={r.status_code} e={err}")
    # 已执行后再建议 → 无变更 409
    r = client.post(f"{BASE}/budget/rates/"
                   "propose", headers=ADMIN)
    err = r.json().get("error",
                       r.json().get("detail", ""))
    record("已执行后再建议 409(无变更)",
           r.status_code == 409
           and "无系数变更建议" in err,
           f"s={r.status_code} e={err}")

    print("[09 KILL 制动]")

    os.environ["ATTRACT72_KILL"] = "1"
    try:
        r = client.post(
            f"{BASE}/budget/forecast/"
            "generate", headers=ADMIN,
            json={"anchor": ANCHOR})
        record("KILL 生成 409",
               r.status_code == 409,
               f"s={r.status_code}")
        r = client.post(
            f"{BASE}/budget/rebalance/"
            "auto", headers=ADMIN,
            json={"now": NOW})
        err = r.json().get(
            "error", r.json().get("detail", ""))
        record("KILL 重博弈 409",
               r.status_code == 409
               and "KILL" in err,
               f"s={r.status_code} e={err}")
        r = client.post(f"{BASE}/budget/rates/"
                       "apply", headers=ADMIN)
        record("KILL 执行 409",
               r.status_code == 409,
               f"s={r.status_code}")
        # 观测面不受 KILL
        r = client.get(f"{BASE}/budget/"
                      "forecast",
                      headers=ADMIN)
        record("KILL 观测面常开",
               r.status_code == 200,
               f"s={r.status_code}")
    finally:
        os.environ.pop("ATTRACT72_KILL", None)

    print("[10 QC(零破坏·池守恒) ]")

    clicks = await attract_repo.list_clicks(
        limit=10000)
    attrs = await svc.list_attributions()
    record("v1.0 点击/归因零破坏",
           len(clicks) == 24
           and len(attrs) == 17,
           f"c={len(clicks)} "
           f"a={len(attrs)}")
    budgets = await attract_repo.list_budgets()
    pool_view = [
        (b["channel"], b["monthlyPool"])
        for b in budgets]
    rate_all = [
        (b["channel"], b["currentRate"])
        for b in budgets]
    record("池总量守恒(8 渠道×10000)",
           len(budgets) == 8
           and all(b["monthlyPool"] == 10000.0
                   for b in budgets),
           f"b={pool_view}")
    record("仅 currentRate 变更(6 渠道 1.0)",
           sum(1 for b in budgets
               if b["currentRate"] == 1.0) == 6,
           f"r={rate_all}")

    changes = await AiGovernance46Repository() \
        .list_changes(
            scorer_id="growth_budget",
            limit=10)
    record("46号 growth_budget 档案 1 建议",
           len(changes) == 1,
           f"c={len(changes)}")

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
