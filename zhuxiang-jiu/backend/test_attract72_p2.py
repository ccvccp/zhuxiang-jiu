"""72号·AI智能自动引流大模型 P2 专项测试
(因果认知: 反事实推理引擎+定律结晶
46号审批+57号同步+反知识+自然语言查询)

运行方式:
    python test_attract72_p2.py

覆盖(72号规划 §四 4.3/§七 P2):
    - 注册表 P2 扩展封闭: 因果维度/
      定律状态机/时段分桶/NL 路由
    - 因果推理: 反事实对照确定性
      (scene driver 正分/channel driver/
      timing loss 负分/样本不足 neutral/
      evidence 留痕)
    - 决策面门控: 结晶/发布 off=409
    - 定律结晶: driver→law/loss→anti/
      statement 数字插值/46号建议书
      (pending 冲突 409)
    - 46号审批链: approve→publish→
      active+57号知识库同步
    - 定律生命周期: 复现 validatedCount
      递增/连续未复现衰减 expired
    - 自然语言查询: 确定性路由三线/
      数字 100% 查询层插值
    - QC: v1.0 数据零破坏/46号零改动
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

    print("[01 注册表 P2 扩展封闭]")

    from services import attract72_registry as reg

    record("因果维度域封闭(三维度)",
           set(reg.CAUSAL_DIMENSIONS) == {
               "content_element",
               "channel_feature", "timing"})
    record("定律状态机封闭(五态)",
           set(reg.LAW_STATUSES) == {
               "draft", "submitted", "active",
               "expired", "rejected"})
    record("时段分桶查表确定性",
           reg.bucket_for_hour(2) == "late_night"
           and reg.bucket_for_hour(10) == "morning"
           and reg.bucket_for_hour(20) == "evening")
    record("NL 路由映射闭合",
           set(reg.NL_ROUTE_KEYWORDS)
           == set(reg.NL_QUERY_ROUTES))
    record("46号档案口径",
           reg.GOVERNANCE_SCORER_ID
           == "growth_intelligence")
    record("启动自检通过(导入即验)",
           reg._validate_registry() is None)

    print("[02 造数(反事实对照三组)]")

    from repositories.member_repository import (
        MemberRepository,
    )
    member_repo = MemberRepository()
    m = await member_repo.create({
        "id": 7002, "nickname": "因果P2测试",
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

    async def make_click(channel, hour):
        r = await svc.resolve_click(
            zxbj, utm_source=channel)
        click = await attract_repo.get_click(
            r["clickId"])
        click["at"] = (f"2026-09-13T{hour:02d}:"
                      "30:00+00:00")
        await attract_repo.save_click(click)
        return r["clickId"]

    # A 组: wedding 场景×小红书×白天×高转化
    group_a = []
    for _ in range(10):
        group_a.append(
            await make_click("xiaohongshu", 10))
    for i, cid in enumerate(group_a[:8]):
        await svc.attach_registration(cid, 7400 + i)
    for i, cid in enumerate(group_a[:6]):
        await svc.attach_order(
            cid, f"ORD-A{i}", 400.0,
            commission=20.0)
    # A 组意图快照(婚宴送礼场景)
    for cid in group_a:
        r = client.post(f"{BASE}/clicks/enrich",
                        headers=ADMIN,
                        json={"clickId": cid,
                              "text": "婚宴用酒送礼"})
        assert r.status_code == 200, r.text

    # B 组: 无场景×抖音×深夜×低转化
    group_b = []
    for _ in range(12):
        group_b.append(
            await make_click("douyin", 2))
    for i, cid in enumerate(group_b[:2]):
        await svc.attach_registration(cid, 7500 + i)
    await svc.attach_order(
        group_b[0], "ORD-B0", 50.0,
        commission=2.5)

    # C 组: 无场景×微信×白天×零转化
    group_c = []
    for _ in range(6):
        group_c.append(
            await make_click("wechat", 10))
    await svc.attach_registration(group_c[0], 7600)

    print("[03 因果推理引擎]")

    r = client.post(f"{BASE}/causal/run",
                    headers=ADMIN)
    body = r.json()["data"]
    record("运行 200(factors≥6)",
           r.status_code == 200
           and body["factors"] >= 6,
           f"b={body}")
    record("drivers/losses 计数",
           body["drivers"] >= 2
           and body["losses"] >= 1,
           f"b={body}")

    insights = client.get(
        f"{BASE}/causal/insights",
        headers=ADMIN).json()["data"]
    by_factor = {i["factor"]: i
                 for i in insights}

    wed = by_factor.get("scene:wedding")
    record("scene:wedding driver 正分",
           wed is not None
           and wed["effectType"] == "driver"
           and abs(wed["counterfactualScore"]
                   - 0.5444) < 0.001
           and wed["status"] == "verified"
           and wed["confidence"] == 0.5,
           f"w={wed}")
    record("wedding evidence 留痕",
           wed["evidence"]["ordersA"] == 6
           and wed["evidence"]["ordersB"] == 1
           and wed["evidence"]["totalClicks"]
           == 28,
           f"e={wed.get('evidence')}")
    record("scene:gift 同组 driver",
           by_factor.get("scene:gift",
                         {}).get("effectType")
           == "driver")

    xhs = by_factor.get("channel:xiaohongshu")
    record("channel:xiaohongshu driver",
           xhs is not None
           and xhs["effectType"] == "driver"
           and xhs["status"] == "verified",
           f"x={xhs}")

    late = by_factor.get("timing:late_night")
    record("timing:late_night loss 负分",
           late is not None
           and late["effectType"] == "loss"
           and abs(late["counterfactualScore"]
                   - (-0.2917)) < 0.001
           and late["confidence"] == 0.6,
           f"l={late}")

    promo = by_factor.get("code_type:promotion")
    record("code_type 全覆盖→对照样本不足"
           " neutral",
           promo is not None
           and promo["effectType"] == "neutral"
           and promo["baseSampleSize"] == 0,
           f"p={promo}")

    r = client.get(f"{BASE}/causal/insights"
                   "?effectType=bad",
                   headers=ADMIN)
    record("效应筛选域外 409",
           r.status_code == 409,
           f"s={r.status_code}")

    print("[04 决策面门控(off=409)]")

    r = client.post(f"{BASE}/knowledge/crystallize",
                    headers=ADMIN,
                    json={"insightId": 1})
    record("结晶 off=409(决策面)",
           r.status_code == 409,
           f"s={r.status_code}")
    r = client.post(
        f"{BASE}/knowledge/laws/1/publish",
        headers=ADMIN)
    record("发布 off=409(决策面)",
           r.status_code == 409,
           f"s={r.status_code}")

    print("[05 定律结晶(law)+46号审批链]")

    os.environ["ATTRACT72_MODE"] = "shadow"

    r = client.post(f"{BASE}/knowledge/crystallize",
                    headers=ADMIN,
                    json={"insightId":
                          wed["insightId"]})
    law = r.json()["data"]
    record("结晶 law(submitted+46号)",
           r.status_code == 200
           and law["kind"] == "law"
           and law["status"] == "submitted"
           and law["changeId"] > 0,
           f"l={law}")
    record("statement 数字插值",
           "提升转化" in law["statement"]
           and "54.44%" in law["statement"]
           and "60.00%" in law["statement"],
           f"s={law.get('statement')}")
    record("boundary 边界口径",
           "样本 10/18" in law["boundary"]
           and "渠道全域" in law["boundary"],
           f"b={law.get('boundary')}")

    # 46号 pending 冲突 → 409
    r = client.post(f"{BASE}/knowledge/"
                    "crystallize",
                    headers=ADMIN,
                    json={"insightId":
                          xhs["insightId"]})
    conflict_msg = r.json().get(
        "error", r.json().get("detail", ""))
    record("46号 pending 冲突 409",
           r.status_code == 409
           and "待审批" in conflict_msg,
           f"s={r.status_code} d={conflict_msg}")

    # 发布(71号范式: 46号 submit 为审批
    # 留痕, 发布为人工显式动作——直发)
    r = client.post(
        f"{BASE}/knowledge/laws/"
        f"{law['lawId']}/publish",
        headers=ADMIN)
    published = r.json()["data"]
    record("发布 active+KB 同步",
           r.status_code == 200
           and published["status"] == "active"
           and published["syncedToKB"] is True
           and published["kbEntryId"] > 0,
           f"p={published}")

    # 重复发布 409(状态机)
    r = client.post(
        f"{BASE}/knowledge/laws/"
        f"{law['lawId']}/publish",
        headers=ADMIN)
    record("重复发布 409(仅 submitted)",
           r.status_code == 409,
           f"s={r.status_code}")

    # 46号驳回清 pending(71号测试范式
    # ——同档案连续提交前处置)
    from services.ai_governance_service import (
        AiGovernanceService,
    )
    gov = AiGovernanceService()
    await gov.review_change(
        law["changeId"], approve=False,
        reviewed_by="admin-测试",
        review_note="46号驳回留痕(测试清"
                    " pending——发布已人工"
                    "确认)")

    from services.knowledge_service import (
        KnowledgeService,
    )
    hits = await KnowledgeService().search(
        "婚宴 scene:wedding 增长定律",
        top_k=5, min_similarity=0.0,
        record_hit=False)
    hit_cats = [h.get("category") for h in hits]
    record("57号知识检索命中定律",
           len(hits) >= 1
           and any(c == "growth_law"
                   for c in hit_cats),
           f"h={hit_cats}")

    print("[06 反知识(anti 结晶)]")

    r = client.post(f"{BASE}/knowledge/"
                    "crystallize",
                    headers=ADMIN,
                    json={"insightId":
                          late["insightId"]})
    anti = r.json()["data"]
    record("loss→anti 结晶",
           r.status_code == 200
           and anti["kind"] == "anti"
           and "拉低转化" in anti["statement"],
           f"a={anti}")
    r = client.post(
        f"{BASE}/knowledge/laws/"
        f"{anti['lawId']}/publish",
        headers=ADMIN)
    anti_pub = r.json()["data"]
    record("anti 发布 active",
           r.status_code == 200
           and anti_pub["status"] == "active",
           f"a={anti_pub}")
    # 46号驳回清 pending(anti 连续提交链)
    await gov.review_change(
        anti["changeId"], approve=False,
        reviewed_by="admin-测试",
        review_note="46号驳回留痕(测试清"
                    " pending——发布已人工"
                    "确认)")

    anti_list = client.get(
        f"{BASE}/knowledge/anti",
        headers=ADMIN).json()["data"]
    record("反知识清单含 anti",
           len(anti_list) == 1
           and anti_list[0]["kind"] == "anti",
           f"a={anti_list}")

    print("[07 定律生命周期(复现)]")

    # 复现: 再运行(数据不变) → 两定律 validated
    r = client.post(f"{BASE}/causal/run",
                    headers=ADMIN)
    laws = client.get(
        f"{BASE}/knowledge/laws"
        "?status=active",
        headers=ADMIN).json()["data"]
    vc = {x["factor"]: x["validatedCount"]
          for x in laws}
    record("复现 validatedCount 递增",
           vc.get("scene:wedding") == 2
           and vc.get("timing:late_night") == 2,
           f"l={vc}")

    print("[08 自然语言查询(衰减前——loss 因子在场)]")

    r = client.post(f"{BASE}/knowledge/query",
                    headers=ADMIN,
                    json={"question":
                          "为什么最近的下单"
                          "流失这么严重"})
    q1 = r.json()["data"]
    record("路由 drivers(流失/为什么)",
           q1["route"] == "drivers"
           and "首要流失因子" in q1["answer"],
           f"q={q1.get('route')} "
           f"a={q1.get('answer')}")
    loss_factor = (q1["evidence"]
                   .get("topLosses")
                   or [{}])[0].get("factor", "")
    record("drivers 答案数字=evidence",
           loss_factor in q1["answer"],
           f"a={q1.get('answer')} "
           f"f={loss_factor}")

    r = client.post(f"{BASE}/knowledge/query",
                    headers=ADMIN,
                    json={"question":
                          "抖音渠道ROI怎么样"})
    q2 = r.json()["data"]
    record("路由 channel_roi 定向",
           q2["route"] == "channel_roi"
           and "渠道 douyin" in q2["answer"],
           f"q={q2}")
    ev_ch = q2["evidence"]["channel"]
    record("ROI 数字 100% 查询层插值",
           f"点击 {ev_ch['clicks']} 次"
           in q2["answer"]
           and ev_ch["clicks"] == 12,
           f"a={q2['answer']} "
           f"e={ev_ch}")

    r = client.post(f"{BASE}/knowledge/query",
                    headers=ADMIN,
                    json={"question":
                          "有哪些增长定律"})
    q3 = r.json()["data"]
    record("路由 laws 台账",
           q3["route"] == "laws"
           and "定律台账" in q3["answer"],
           f"q={q3.get('route')}")

    r = client.post(f"{BASE}/knowledge/query",
                    headers=ADMIN,
                    json={"question": " "})
    record("空问题 409",
           r.status_code == 409,
           f"s={r.status_code}")

    print("[09 定律衰减(数据漂移)]")

    # 衰减: 灌 30 个 wedding×wechat×白天
    # 零单点击 → wedding 转化率被稀释
    # 至不足效应线 → 连续两次运行 → expired
    for _ in range(30):
        cid = await make_click("wechat", 10)
        r2 = client.post(
            f"{BASE}/clicks/enrich",
            headers=ADMIN,
            json={"clickId": cid,
                  "text": "婚宴用酒送礼"})
        assert r2.status_code == 200
    client.post(f"{BASE}/causal/run",
                 headers=ADMIN)
    client.post(f"{BASE}/causal/run",
                 headers=ADMIN)
    laws = client.get(
        f"{BASE}/knowledge/laws",
        headers=ADMIN).json()["data"]
    wed_law = next(l for l in laws
                   if l["factor"]
                   == "scene:wedding")
    record("连续未复现→expired",
           wed_law["status"] == "expired",
           f"l={wed_law.get('status')}")
    late_law = next(l for l in laws
                    if l["factor"]
                    == "timing:late_night")
    # 对照组被 30 个零单点击稀释 →
    # late_night 反事实分也不足线
    # (0.0833-0.1304=-0.047) → 同样衰减
    record("对照稀释致 late_night 同样"
           "衰减 expired",
           late_law["status"] == "expired",
           f"l={late_law.get('status')}")

    print("[10 QC(零破坏·叠加铁律)]")

    clicks = await attract_repo.list_clicks(
        limit=10000)
    attrs = await svc.list_attributions()
    record("v1.0 点击/归因零破坏",
           len(clicks) == 58
           and len(attrs) == 11,
           f"c={len(clicks)} "
           f"a={len(attrs)}")

    from repositories.ai_governance_repository \
        import AiGovernance46Repository
    changes = await AiGovernance46Repository() \
        .list_changes(scorer_id="growth_intelligence",
                      limit=10)
    ch_states = [(x.get("changeId"),
                  x.get("status")) for x in changes]
    record("46号两建议书留痕(驳回清 pending"
           "——71号范式)",
           len(changes) == 2
           and all(c.get("status") == "rejected"
                   for c in changes),
           f"c={ch_states}")

    kb = await KnowledgeService().list_entries(
        category="growth_law", limit=10)
    kb_states = [(e.get("id"),
                  e.get("status")) for e in kb]
    record("57号 growth_law 类别 2 条"
           " published",
           len(kb) == 2
           and all(e.get("status") == "published"
                   for e in kb),
           f"k={kb_states}")

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
