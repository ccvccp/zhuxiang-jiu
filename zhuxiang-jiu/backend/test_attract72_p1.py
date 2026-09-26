"""72号·AI智能自动引流大模型 P1 专项测试
(感知跃迁: 渠道人格画像+意图快照引擎+外部
信号总线)

运行方式:
    python test_attract72_p1.py

覆盖(72号规划 §四 4.1/§七 P1):
    - 注册表 P1 封闭: 模式四档/
      人格四型/层级查表/情绪词表
      正负互斥/启动自检
    - 鉴权: 无 admin 403
    - 画像同步(博主): traffic 只读消费
      → 品鉴型(高客单)/T1 层级/信任分
      100/置信度 0.12/stats 六数
    - 画像同步(会员): promotion 只读消费
      → 优惠敏感型(流量型)/M0 层级
    - 新晋型: 样本不足(<5 点击)
    - 人格/层级跃迁 → history 留痕
    - 幂等: 二次同步 updated 不 created
    - 意图快照引擎: 词表密度确定性
      (场景/意图/犹豫/情绪基线/停留
      信号/指纹脱敏/覆盖 upsert)
    - 信号总线: 雷达 L1 只读消费/
      L4 风险永不消费/低价值不入流/
      节日日历窗口/ref 幂等
    - QC: 40号雷达数据零修改(叠加铁律)
      /attract v1.0 数据零破坏
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


async def main():
    from repositories.store import reset_store
    reset_store()

    from fastapi.testclient import TestClient
    from main import app
    client = TestClient(app)
    ADMIN = {"X-Role": "admin"}
    BASE = "/api/attract72"

    print("[01 注册表 P1 封闭]")

    from services import attract72_registry as reg

    record("模式四档封闭(引流域首创 full)",
           set(reg.MODE_VALUES) == {
               "off", "shadow", "assist", "full"})
    record("人格类型域封闭(四型)",
           set(reg.PERSONA_TYPES) == {
               "connoisseur", "sharer",
               "bargain_hunter", "newcomer"})
    record("层级查表确定性",
           reg.tier_for_followers(1_500_000) == "T0"
           and reg.tier_for_followers(150_000) == "T1"
           and reg.tier_for_followers(5_000) == "T3"
           and reg.tier_for_members(60) == "M2"
           and reg.tier_for_members(7) == "M0")
    record("情绪词表正负互斥",
           not (set(reg.EMOTION_POSITIVE_WORDS)
                & set(reg.EMOTION_NEGATIVE_WORDS)))
    record("启动自检通过(导入即验)",
           reg._validate_registry() is None)

    print("[02 鉴权与观测面常开]")

    r = client.get(f"{BASE}/personas")
    record("无 admin 403", r.status_code == 403,
           f"s={r.status_code}")
    r = client.get(f"{BASE}/personas",
                   headers={"X-Role": "user"})
    record("非 admin 403", r.status_code == 403,
           f"s={r.status_code}")

    # 空库同步(观测面 off 档常开; 无节日窗口)
    r = client.post(f"{BASE}/personas/sync",
                   headers=ADMIN,
                   json={"today": "2026-03-15"})
    body = r.json()["data"]
    record("空库同步 200(观测面 off 常开)",
           r.status_code == 200
           and body["synced"] == 0
           and body["created"] == 0
           and body["signalsIngested"] == 0,
           f"s={r.status_code} b={body}")

    print("[03 博主画像(品鉴型·traffic 只读消费)]")

    from services.traffic_service import TrafficService
    traffic = TrafficService()
    inf1 = await traffic.create_influencer(
        user_id=9001, name="老陈说酒",
        level="C")
    inf1_id = inf1["id"]
    await traffic.add_influencer_platform(
        influencer_id=inf1_id, platform="douyin",
        platform_uid="dy-laochen",
        follower_count=150_000, verified=True)
    kol_code = (await traffic
                .create_influencer_promo_code(
                    influencer_id=inf1_id,
                    platform="douyin"))["promoCode"]

    from services.attract_service import AttractService
    svc = AttractService()
    # 6 点击(含 2 匿名)→ 4 注册 → 3 下单 400 元
    kol_clicks = []
    for _ in range(6):
        result = await svc.resolve_click(kol_code)
        kol_clicks.append(result["clickId"])
    for i in range(4):
        await svc.attach_registration(
            kol_clicks[i], 9100 + i)
    for i in range(3):
        await svc.attach_order(
            kol_clicks[i], f"ORD-K{i}",
            400.0, commission=20.0)

    r = client.post(f"{BASE}/personas/sync",
                   headers=ADMIN,
                   json={"today": "2026-03-15"})
    body = r.json()["data"]
    record("同步 created=1",
           body["created"] == 1
           and body["synced"] == 1,
           f"b={body}")
    record("品鉴型(高客单≥300)",
           body["personas"][0]["personaType"]
           == "connoisseur",
           f"p={body['personas']}")

    r = client.get(f"{BASE}/personas",
                   headers=ADMIN)
    personas = r.json()["data"]
    p_inf = personas[0]
    record("T1 层级+平台+认证",
           p_inf["followerTier"] == "T1",
           f"p={p_inf}")

    pid = p_inf["personaId"]
    r = client.get(f"{BASE}/personas/{pid}",
                   headers=ADMIN)
    detail = r.json()["data"]
    record("画像详情(stats 六数)",
           detail["stats"]["clickCount"] == 6
           and detail["stats"]["registeredCount"] == 4
           and detail["stats"]["orderCount"] == 3
           and detail["stats"]["gmv"] == 1200.0
           and detail["stats"]["avgOrderAmount"]
           == 400.0,
           f"s={detail.get('stats')}")
    record("信任分公式=100(30+40+30)",
           detail["trustScore"] == 100,
           f"t={detail.get('trustScore')}")
    record("置信度=6/50=0.12",
           detail["confidence"] == 0.12,
           f"c={detail.get('confidence')}")
    record("平台与认证(traffic 只读)",
           detail["platforms"] == ["douyin"]
           and detail["verified"] is True,
           f"p={detail.get('platforms')} "
           f"v={detail.get('verified')}")
    record("首次无跃迁(history 空)",
           detail["history"] == [],
           f"h={detail.get('history')}")

    # 幂等: 二次同步
    r = client.post(f"{BASE}/personas/sync",
                   headers=ADMIN,
                   json={"today": "2026-03-15"})
    body = r.json()["data"]
    record("幂等(更新非新建)",
           body["created"] == 0
           and body["updated"] == 1
           and body["synced"] == 1,
           f"b={body}")

    print("[04 会员画像+新晋型]")

    from repositories.member_repository import (
        MemberRepository,
    )
    member_repo = MemberRepository()
    for uid in (7002, 7003):
        m = await member_repo.create({
            "id": uid, "nickname": f"引流P1测试{uid}",
            "phone": f"1390000{uid:04d}",
            "password": "test123456"})
        await member_repo.save(uid, m)

    from services.promotion_service import (
        PromotionService,
    )
    zxbj = (await PromotionService()
            .claim_promo_code(
                member_id=7002,
                channel="wechat_miniprogram"))["code"]

    # 会员码: 7 点击全注册, 1 单 50 元
    mem_clicks = []
    for _ in range(7):
        result = await svc.resolve_click(zxbj)
        mem_clicks.append(result["clickId"])
    for i in range(7):
        await svc.attach_registration(
            mem_clicks[i], 7200 + i)
    await svc.attach_order(
        mem_clicks[0], "ORD-M0", 50.0,
        commission=2.5)

    # 新晋博主: 仅 2 点击(样本不足)
    inf2 = await traffic.create_influencer(
        user_id=9002, name="小雅微醺", level="C")
    inf2_id = inf2["id"]
    await traffic.add_influencer_platform(
        influencer_id=inf2_id, platform="xiaohongshu",
        platform_uid="xhs-xiaoya",
        follower_count=8_000, verified=False)
    kol2 = (await traffic
            .create_influencer_promo_code(
                influencer_id=inf2_id,
                platform="xiaohongshu"))["promoCode"]
    for _ in range(2):
        await svc.resolve_click(kol2)

    r = client.post(f"{BASE}/personas/sync",
                   headers=ADMIN,
                   json={"today": "2026-03-15"})
    body = r.json()["data"]
    record("三类主体同步(共 3)",
           body["synced"] == 3
           and body["created"] == 2,
           f"b={body}")

    r = client.get(f"{BASE}/personas"
                   "?subjectType=member",
                   headers=ADMIN)
    members = r.json()["data"]
    record("会员=优惠敏感型(流量型)",
           len(members) == 1
           and members[0]["personaType"]
           == "bargain_hunter",
           f"m={members}")
    record("会员层级 M0(7 注册<10)",
           members[0]["followerTier"] == "M0",
           f"m={members}")

    r = client.get(f"{BASE}/personas"
                   "?personaType=newcomer",
                   headers=ADMIN)
    newcomers = r.json()["data"]
    record("新晋型(样本<5)+低置信度",
           len(newcomers) == 1
           and newcomers[0]["confidence"] == 0.04,
           f"n={newcomers}")

    r = client.get(f"{BASE}/personas"
                   "?subjectType=bad",
                   headers=ADMIN)
    record("筛选域外 409", r.status_code == 409,
           f"s={r.status_code}")

    print("[05 人格/层级跃迁留痕]")

    # 会员再 5 注册 → 12 ≥10 → M1 跃迁
    extra = []
    for _ in range(5):
        result = await svc.resolve_click(zxbj)
        extra.append(result["clickId"])
    for i, cid in enumerate(extra):
        await svc.attach_registration(cid, 7300 + i)

    client.post(f"{BASE}/personas/sync",
                 headers=ADMIN,
                 json={"today": "2026-03-15"})
    r = client.get(f"{BASE}/personas"
                   "?subjectType=member",
                   headers=ADMIN)
    m_detail = r.json()["data"][0]
    record("层级跃迁 M0→M1",
           m_detail["followerTier"] == "M1",
           f"m={m_detail}")

    r = client.get(
        f"{BASE}/personas/"
        f"{m_detail['personaId']}",
        headers=ADMIN)
    record("跃迁 history 留痕(M0)",
           len(r.json()["data"]["history"]) == 1
           and r.json()["data"]["history"][0]
           ["followerTier"] == "M0",
           f"h={r.json()['data'].get('history')}")

    print("[06 意图快照引擎]")

    r = client.post(f"{BASE}/clicks/enrich",
                    headers=ADMIN,
                    json={"clickId": 999999})
    record("点击不存在 404", r.status_code == 404,
           f"s={r.status_code}")

    r = client.post(f"{BASE}/clicks/enrich",
                    headers=ADMIN,
                    json={
                        "clickId": kol_clicks[0],
                        "deviceFingerprint": "fp-test-01",
                        "dwellSeconds": 200.0,
                        "text":
                            "婚宴送礼想看看价格，"
                            "感觉有点太贵了"})
    snap = r.json()["data"]
    record("场景标签(婚宴+送礼)",
           snap["sceneTags"] == ["gift", "wedding"],
           f"s={snap.get('sceneTags')}")
    record("意图+犹豫信号(价格敏感/价格犹豫)",
           "price_sensitive" in snap["intentTags"]
           and "gift_intent" in snap["intentTags"]
           and snap["hesitationSignals"]
           == ["price_hesitation"],
           f"i={snap.get('intentTags')} "
           f"h={snap.get('hesitationSignals')}")
    record("停留≥120s→高参与",
           "high_engagement" in snap["intentTags"],
           f"i={snap.get('intentTags')}")
    record("情绪基线=-1(纯负面)",
           snap["emotionBaseline"] == -1.0,
           f"e={snap.get('emotionBaseline')}")
    record("显式指纹透传",
           snap["deviceFingerprint"] == "fp-test-01",
           f"f={snap.get('deviceFingerprint')}")

    # UA 哈希脱敏(无显式指纹)
    r = client.post(f"{BASE}/clicks/enrich",
                    headers=ADMIN,
                    json={"clickId": kol_clicks[1],
                          "userAgent": "Mozilla/5.0 test",
                          "dwellSeconds": 8.0})
    snap2 = r.json()["data"]
    record("UA 哈希指纹(16 位脱敏)",
           len(snap2["deviceFingerprint"]) == 16,
           f"f={snap2.get('deviceFingerprint')}")
    record("停留<15s→跳出风险",
           snap2["intentTags"] == ["bounce_risk"]
           and snap2["sceneTags"] == [],
           f"i={snap2.get('intentTags')}")

    # 覆盖 upsert(同 click 再 enrich)
    r = client.post(f"{BASE}/clicks/enrich",
                    headers=ADMIN,
                    json={"clickId": kol_clicks[0],
                          "deviceFingerprint": "fp-test-01",
                          "text": "这酒工艺口感怎么样，纯粮的吗"})
    snap3 = r.json()["data"]
    record("同 click 覆盖 upsert",
           snap3["snapshotId"] == kol_clicks[0]
           and "taste_curious" in snap3["intentTags"]
           and snap3["sceneTags"] == [],
           f"s={snap3}")

    r = client.get(f"{BASE}/intent/{kol_clicks[0]}",
                   headers=ADMIN)
    record("快照查询 200",
           r.status_code == 200
           and r.json()["data"]["clickId"]
           == kol_clicks[0])
    r = client.get(f"{BASE}/intent/888888",
                   headers=ADMIN)
    record("快照不存在 404",
           r.status_code == 404,
           f"s={r.status_code}")

    print("[07 外部信号总线(雷达只读+节日日历)]")

    from repositories.radar_repository import (
        RadarRepository,
    )
    radar_repo = RadarRepository()
    # L1 高价值 finance 事件(应消费)
    ev1_id = await radar_repo.next_id("event")
    await radar_repo.save_event({
        "eventId": ev1_id,
        "fingerprint": f"fp-test-{ev1_id}",
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
    # L4 风险事件(永不消费)
    ev2_id = await radar_repo.next_id("event")
    await radar_repo.save_event({
        "eventId": ev2_id,
        "fingerprint": f"fp-test-{ev2_id}",
        "channelId": 2, "channelName": "金梅煮酒",
        "platform": "douyin",
        "title": "国际局势新动向",
        "summary": "大国博弈",
        "category": "politics",
        "heatBase": 900,
        "lifecycle": "new", "totalSlots": 1,
        "firstSeenAt": "2026-09-13T00:00:00Z",
        "lastSeenAt": "2026-09-13T00:00:00Z",
        "grade": "L4", "valueScore": 95.0,
    })
    # L2 低价值事件(<30 不入流)
    ev3_id = await radar_repo.next_id("event")
    await radar_repo.save_event({
        "eventId": ev3_id,
        "fingerprint": f"fp-test-{ev3_id}",
        "channelId": 1, "channelName": "财富故事会",
        "platform": "douyin",
        "title": "消费降级还是理性回归",
        "summary": "性价比时代",
        "category": "finance",
        "heatBase": 300,
        "lifecycle": "new", "totalSlots": 1,
        "firstSeenAt": "2026-09-13T00:00:00Z",
        "lastSeenAt": "2026-09-13T00:00:00Z",
        "grade": "L2", "valueScore": 20.0,
    })

    r = client.post(f"{BASE}/personas/sync",
                   headers=ADMIN,
                   json={"today": "2026-09-13"})
    body = r.json()["data"]
    # 雷达 1 + 节日 2(中秋 09-25/国庆 10-01)
    record("信号摄取(雷达1+节日2)",
           body["signalsIngested"] == 3
           and body["radar"] == 1
           and body["festival"] == 2,
           f"b={body}")

    r = client.get(f"{BASE}/signals?type=radar_event",
                   headers=ADMIN)
    sigs = r.json()["data"]
    record("雷达 L1 信号(weight=0.8)",
           len(sigs) == 1
           and sigs[0]["weight"] == 0.8
           and sigs[0]["payload"]["category"]
           == "finance",
           f"s={sigs}")
    record("雷达类别→影响渠道映射",
           sigs[0]["impactChannels"]
           == ["douyin", "xiaohongshu"],
           f"c={sigs[0].get('impactChannels')}")

    r = client.get(f"{BASE}/signals?type=festival",
                   headers=ADMIN)
    fests = r.json()["data"]
    record("节日窗口(中秋+国庆)",
           len(fests) == 2
           and {f["payload"]["name"] for f in fests}
           == {"mid_autumn", "national_day"},
           f"f={fests}")
    record("节日权重(中秋 0.9 优先)",
           fests[0]["weight"] == 0.9
           and fests[0]["payload"]["name"]
           == "mid_autumn",
           f"f={fests}")

    # ref 幂等: 二次同步不重复
    r = client.post(f"{BASE}/personas/sync",
                   headers=ADMIN,
                   json={"today": "2026-09-13"})
    body = r.json()["data"]
    record("信号 ref 幂等(不重复)",
           body["signalsIngested"] == 0,
           f"b={body}")

    r = client.get(f"{BASE}/signals?type=bad",
                   headers=ADMIN)
    record("信号类型域外 409",
           r.status_code == 409,
           f"s={r.status_code}")

    print("[08 QC(叠加铁律·零破坏) ]")

    from services.attract72_p1_service import (
        Attract72P1Service,
    )

    # 40号雷达数据零修改(只读铁律)
    ev_after = await radar_repo.get_event(ev1_id)
    record("40号雷达数据零修改",
           ev_after["grade"] == "L1"
           and ev_after["valueScore"] == 80.0
           and ev_after["title"]
           == "年轻人理财新趋势",
           f"e={ev_after}")
    l4_count = len(await radar_repo.list_events(
        grade="L4"))
    record("L4 风险事件永不入流",
           l4_count == 1
           and len(
               [s for s in
                (await Attract72P1Service()
                 .list_signals(signal_type="radar_event"))
                if s["payload"].get("grade") == "L4"])
           == 0,
           f"l4={l4_count}")

    # attract v1.0 数据零破坏
    attrs = await svc.list_attributions()
    clicks = await svc.repo.list_clicks(limit=10000)
    # 博主 4 + 会员 12 + 新晋 0 = 16 归因
    record("v1.0 归因/点击零破坏",
           len(attrs) == 16
           and len(clicks) == 6 + 7 + 2 + 5,
           f"attrs={len(attrs)} "
           f"clicks={len(clicks)}")

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
