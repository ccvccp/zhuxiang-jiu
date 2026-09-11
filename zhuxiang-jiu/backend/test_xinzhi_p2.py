"""68号·信值·臻选·P2 透明定价与导购专项测试

覆盖(《68号 创新规划方案》§三 P2, ~23 断言):
    1. 定价纯函数: α 系数表(只紧不松)/反馈分流三态
       (L3 关键词/L2 标签路由/L1 默认安抚)
    2. 价格构成: 拆解公示格式/α 抵扣计算/D 级零抵扣/
       地板保护不击穿/30% 上限不变量/杀熟价差>20% 留痕
    3. 反馈闭环: L1 自动回复/L2 工单路由+24h/L3 紧急
       15 分钟/进度 404/L2 处置闭环/统计口径
    4. 导购 SOP: 五步全链/意图锚定/信值佐证数字直出/
       透明解释/履约硬闸如实告知/价值沉淀最弱维/
       LLM 禁数字红线/人格卡片

运行:
    $env:LOCK_MODE="asyncio"; $env:STORE_MODE="asyncio"
    python test_xinzhi_p2.py
"""

import asyncio
import os
import sys


os.environ["LOCK_MODE"] = "asyncio"
os.environ["STORE_MODE"] = "asyncio"
os.environ.pop("LLM_API_KEY", None)
os.environ["LLM_ENABLED"] = "off"

from services.xinzhi_pricing_service import (
    XinzhiPricingService, alpha_of_grade,
    classify_feedback, XINZHI_ALPHA, ALPHA_CAP,
    PRICE_DIFF_AUDIT_LINE,
)
from services.xinzhi_guide_service import (
    XinzhiGuideService, classify_intent, GUIDE_PERSONA,
)
from repositories.xinzhi_repository import (
    XinzhiRepository, DIMENSIONS,
)
from repositories.member_repository import MemberRepository

PASS = 0
FAIL = 0
RESULTS = []


def record(name, passed, detail=""):
    global PASS, FAIL
    if passed:
        PASS += 1
        RESULTS.append(f"  \u2713 {name}")
    else:
        FAIL += 1
        RESULTS.append(f"  \u2717 {name} \u2014 {detail}")


def reset_store():
    from repositories.store import reset_store as _reset
    _reset()


async def seed_member(member_id: int) -> None:
    from datetime import datetime, UTC, timedelta
    repo = MemberRepository()
    member = {
        "id": member_id, "phone": f"1380000000{member_id}",
        "password": "x", "nickname": f"测试会员{member_id}",
        "level": 2, "points": 100, "status": 1,
        "created_at": (datetime.now(UTC)
                       - timedelta(days=200)).isoformat(),
        "last_login_at": datetime.now(UTC).isoformat(),
    }
    await repo.save(member_id, member)


async def seed_radar(member_id: int, grade: str,
                     total: float,
                     dims: dict = None) -> dict:
    """播种雷达快照(定价/导购的查询层输入——受控等级)"""
    from datetime import datetime, UTC
    repo = XinzhiRepository()
    sid = await repo.next_id("snapshot")
    d = {"integrity": 80, "mutual": 80, "expert": 80,
         "activity": 80, "growth": 80}
    d.update(dims or {})
    snap = {
        "snapshotId": sid, "memberId": member_id,
        **{k: d[k] for k in DIMENSIONS},
        "totalScore": total, "grade": grade,
        "weights": {"integrity": 0.30, "mutual": 0.25,
                    "expert": 0.20, "activity": 0.15,
                    "growth": 0.10},
        "recentFactors": {}, "circuitBroken": False,
        "coldStart": False, "bonusApplied": False,
        "tier": "standard", "computedAt":
            datetime.now(UTC).isoformat(),
    }
    await repo.save_snapshot(snap)
    return snap


async def seed_tier_profile(member_id: int,
                            risk_ema: float) -> None:
    """播种 45+47号档案(60号信任因子的查询层输入)"""
    from repositories.trust_value_repository import (
        TrustValue45Repository)
    from repositories.trust_risk_repository import (
        TrustRisk47Repository)
    await TrustValue45Repository().save_profile({
        "trustId": member_id, "role": "person",
        "name": f"p2-{member_id}",
        "idDigest": f"p2-{member_id}",
        "factors": {}, "score": 500.0, "rawScore": 500.0,
        "grade": "C", "fused": False, "frozen": False,
        "createdAt": "2026-01-01T00:00:00",
        "updatedAt": "2026-01-01T00:00:00"})
    await TrustRisk47Repository().save_profile({
        "trustId": member_id, "riskEMA": risk_ema,
        "hitCounts": {}, "eventCount": 0,
        "calibrateOverride": "", "calibrateNote": "",
        "calibrateAt": "",
        "createdAt": "2026-01-01T00:00:00",
        "lastUpdated": "2026-01-01T00:00:00",
        "riskHistory": []})


def make_product(**kw):
    """受控测试商品(product_repository 口径)"""
    return {
        "product_id": kw.get("product_id", "TEST-P2-01"),
        "name": kw.get("name", "竹奕·测试款 42° 500ml"),
        "subtitle": kw.get("subtitle", "测试副标题"),
        "series": kw.get("series", "经典系列"),
        "price": kw.get("price", 268),
        "status": kw.get("status", "on_sale"),
        "rating_avg": kw.get("rating_avg", 4.8),
        "rating_count": kw.get("rating_count", 100),
        "sales_monthly": kw.get("sales_monthly", 500),
        "sales_total": kw.get("sales_total", 5000),
        "tags": kw.get("tags", ["主打"]),
        "scenes": kw.get("scenes", ["老友小聚"]),
        "description": kw.get("description", "测试描述"),
        "images": {"main": "", "gallery": []},
        "attributes": {},
        "created_at": "2026-08-01T00:00:00+00:00",
    }


# ============================================================
# 1. 定价与分流纯函数(4 断言)
# ============================================================

class TestPure:
    async def run(self):
        # 1) α 系数表(文档口径——只紧不松硬编码)
        record("α系数表-S/A/B/C/D",
               XINZHI_ALPHA["S"] == 0.15
               and XINZHI_ALPHA["A"] == 0.12
               and XINZHI_ALPHA["B"] == 0.08
               and XINZHI_ALPHA["C"] == 0.0
               and XINZHI_ALPHA["D"] == 0.0,
               str(XINZHI_ALPHA))
        record("α系数-未知等级归零",
               alpha_of_grade("") == 0.0
               and alpha_of_grade("X") == 0.0,
               "fallback")

        # 2) 反馈分流 L3(紧急关键词)
        lv, to, _ = classify_feedback(
            "我遇到诈骗了", [], "product")
        record("分流-L3紧急关键词",
               lv == "L3" and to == "风控负责人",
               f"{lv}/{to}")

        # 3) 反馈分流 L2(标签路由)
        lv, to, _ = classify_feedback(
            "发货太慢", ["物流"], "product")
        record("分流-L2标签路由",
               lv == "L2" and to == "物流运营组",
               f"{lv}/{to}")

        # 4) 反馈分流 L1(默认安抚)
        lv, to, reply = classify_feedback(
            "没啥就是随便说说", [], "guide")
        record("分流-L1默认安抚",
               lv == "L1" and to == "" and len(reply) > 0,
               f"{lv}/{to}")


# ============================================================
# 2. 价格构成拆解(6 断言)
# ============================================================

class TestPricing:
    async def run(self):
        reset_store()
        await seed_member(1)
        await seed_member(2)
        # 会员1: trusted(riskEMA=0)+S 级; 会员2: restricted+D 级
        await seed_tier_profile(1, 0.0)
        await seed_tier_profile(2, 0.9)
        await seed_radar(1, "S", 95,
                         {"growth": 10})
        await seed_radar(2, "D", 40)
        svc = XinzhiPricingService()

        # 5) 构成拆解公示(原价-信值抵扣-折扣=实付)
        d1 = await svc.price_breakdown(1, "ZX42-2026L07")
        line = d1["breakdownLine"]
        record("定价-构成拆解公示",
               all(w in line for w in
                   ("原价", "信值抵扣", "三因子折扣", "实付"))
               and d1["basePrice"] == 268,
               line)

        # 6) α 抵扣计算(trusted×S: 268×0.95×0.15)
        expect_credit = round(268 * 0.95 * 0.15, 2)
        record("定价-α抵扣计算",
               d1["tier"] == "trusted"
               and d1["xinzhiAlpha"] == 0.15
               and abs(d1["xinzhiCredit"] - expect_credit)
               < 0.01,
               f"cr={d1['xinzhiCredit']} "
               f"e={expect_credit}")

        # 7) D 级零抵扣(restricted×D: 实付=268×1.05)
        d2 = await svc.price_breakdown(2, "ZX42-2026L07")
        record("定价-D级零抵扣",
               d2["xinzhiCredit"] == 0.0
               and d2["finalPrice"] == round(
                   268 * 1.05, 2),
               f"final={d2['finalPrice']}")

        # 8) 杀熟审计(价差 23%>20% → 留痕, 处置永不自动)
        diff = (d2["finalPrice"] - d1["finalPrice"]) \
               / d2["finalPrice"]
        record("定价-杀熟审计留痕",
               diff > PRICE_DIFF_AUDIT_LINE
               and "price_diff>20%" in str(
                   d2["auditFlag"])
               and d1["auditFlag"] == "",
               f"diff={diff:.2%} flag={d2['auditFlag']}")

        # 9) 地板保护(α 不可击穿 0.7 地板)
        d3 = await svc.price_breakdown(
            1, "ZX42-2026L07", promo_factor=0.5)
        record("定价-地板保护不击穿",
               d3["floored"] is True
               and d3["finalPrice"]
               == round(268 * 0.7, 2),
               f"final={d3['finalPrice']}")

        # 10) 抵扣上限不变量(单笔≤30% 防套现)
        details = await svc.repo.list_price_details(
            product_id="ZX42-2026L07", limit=100)
        record("定价-30%抵扣上限",
               all(float(x["xinzhiCredit"])
                   <= 268 * ALPHA_CAP + 0.01
                   for x in details),
               f"cap={268 * ALPHA_CAP}")

        # 11) 商品 404
        ok = False
        try:
            await svc.price_breakdown(1, "NOPE-999")
        except KeyError:
            ok = True
        record("定价-商品404", ok)

        # 12) 会员无快照自动计算(P0 联动——不抛错)
        await seed_member(3)
        d4 = await svc.price_breakdown(3, "ZX42-2026B01")
        record("定价-无快照自动计算",
               d4["grade"] in ("S", "A", "B", "C", "D"),
               f"grade={d4['grade']}")


# ============================================================
# 3. 反馈闭环(6 断言)
# ============================================================

class TestFeedback:
    async def run(self):
        reset_store()
        await seed_member(1)
        svc = XinzhiPricingService()

        # 13) L1 自动回复(即时安抚, 不入人工队列)
        f1 = await svc.submit_feedback(
            1, "guide", [], "没啥用不顺手")
        record("反馈-L1自动回复",
               f1["level"] == "L1"
               and f1["status"] == "auto_replied"
               and f1["sla"] == "即时"
               and len(f1["autoReply"]) > 0,
               f"{f1['level']}/{f1['status']}")

        # 14) L2 工单(标签路由+24h 时效)
        f2 = await svc.submit_feedback(
            1, "radar", ["分数不合理"], "分数怎么算的")
        record("反馈-L2工单路由",
               f2["level"] == "L2"
               and f2["routedTo"] == "信值产品组"
               and f2["status"] == "pending"
               and f2["sla"] == "24小时内",
               f"{f2['level']}/{f2['routedTo']}")

        # 15) L3 紧急(15 分钟响应承诺)
        f3 = await svc.submit_feedback(
            1, "product", [], "怀疑泄露隐私了")
        record("反馈-L3紧急15分钟",
               f3["level"] == "L3"
               and f3["routedTo"] == "风控负责人"
               and f3["sla"] == "15分钟",
               f"{f3['level']}/{f3['sla']}")

        # 16) 进度透明查询(404)
        ok = False
        try:
            await svc.feedback_status(9999)
        except KeyError:
            ok = True
        record("反馈-进度404", ok)

        # 17) L2 处置闭环(人工轨——不可重复处置)
        done = await svc.resolve_feedback(
            f2["feedbackId"], "已复核公式无误")
        again = False
        try:
            await svc.resolve_feedback(
                f2["feedbackId"], "重复")
        except ValueError:
            again = True
        record("反馈-处置闭环不可重复",
               done["status"] == "resolved"
               and "复核" in done["resolvedNote"]
               and again,
               f"{done['status']}")

        # 18) L1 无需处置(自动回复已闭环)
        ok = False
        try:
            await svc.resolve_feedback(
                f1["feedbackId"], "x")
        except ValueError:
            ok = True
        record("反馈-L1无需处置", ok)

        # 19) 统计口径(分级计数+TOP标签)
        stats = await svc.feedback_stats()
        record("反馈-统计口径",
               stats["total"] == 3
               and stats["byLevel"]["L2"] == 1
               and stats["byLevel"]["L3"] == 1
               and any(t["tag"] == "分数不合理"
                       for t in stats["topTags"]),
               f"total={stats['total']}")


# ============================================================
# 4. 导购 SOP 五步法(8 断言)
# ============================================================

class TestGuide:
    async def run(self):
        reset_store()
        await seed_member(1)
        await seed_tier_profile(1, 0.0)
        await seed_radar(1, "S", 95,
                         {"growth": 10})
        # 硬闸商品(违禁词——L4 如实告知不静默)
        from repositories.product_repository import (
            ProductRepository)
        await ProductRepository().save_product(
            make_product(product_id="TEST-L4-01",
                         description="三无渠道流通测试"))
        svc = XinzhiGuideService()

        # 20) SOP 五步全链(意图→佐证→透明→履约→沉淀)
        g = await svc.guide_reply(
            1, "ZX42-2026L07", "这个多少钱")
        record("导购-SOP五步全链",
               list(g["steps"].keys()) ==
               ["intent_anchor", "trust_evidence",
                "transparent_price", "fulfillment",
                "value_growth"]
               and g["persona"] == GUIDE_PERSONA
               and len(g["reply"]) > 50,
               str(list(g["steps"].keys())))

        # 21) 意图锚定(确定性分类)
        record("导购-意图锚定",
               classify_intent("多少钱") == "price"
               and classify_intent("帮我推荐") == "recommend"
               and classify_intent("和那款什么区别") == "doubt"
               and classify_intent("") == "browse"
               and g["steps"]["intent_anchor"]["intent"]
               == "price",
               g["steps"]["intent_anchor"]["intent"])

        # 22) 信值佐证数字直出(等级来自雷达查询层)
        record("导购-信值佐证直出",
               g["steps"]["trust_evidence"]["grade"] == "S"
               and g["steps"]["trust_evidence"]
               ["totalScore"] == 95,
               str(g["steps"]["trust_evidence"]))

        # 23) 透明解释(LLM 禁数字——话术数字=
        #     价格构成查询层结果)
        tp = g["steps"]["transparent_price"]
        record("导购-透明解释数字一致",
               f"¥{tp['finalPrice']}" in g["reply"]
               and "原价" in g["reply"]
               and "信值抵扣" in g["reply"],
               f"final={tp['finalPrice']}")

        # 24) 履约硬闸如实告知(L4 不静默丢弃)
        g4 = await svc.guide_reply(
            1, "TEST-L4-01", "")
        record("导购-L4硬闸如实告知",
               "风险观察" in g4["steps"]["fulfillment"]["say"],
               g4["steps"]["fulfillment"]["say"][:40])

        # 25) 履约口碑数字直出(月销/评分来自商品层)
        fu = g["steps"]["fulfillment"]
        record("导购-履约口碑直出",
               f"月销{fu['sales']}" in g["reply"]
               and f"评分{fu['rating']}" in g["reply"],
               f"sales={fu['sales']} rating={fu['rating']}")

        # 26) 价值沉淀最弱维(growth=10 → 成长路径)
        vg = g["steps"]["value_growth"]
        record("导购-价值沉淀最弱维",
               vg["weakestDim"] == "growth"
               and "成长力" in vg["say"],
               f"{vg['weakestDim']}")

        # 27) 人格卡片(能力边界公示)
        card = svc.persona_card()
        record("导购-人格卡片公示",
               card["persona"] == GUIDE_PERSONA
               and len(card["sopSteps"]) == 5
               and len(card["redLines"]) >= 4
               and "LLM" in card["llmBoundary"],
               f"steps={len(card['sopSteps'])}")

        # 28) 导购 404(商品不存在)
        ok = False
        try:
            await svc.guide_reply(1, "NOPE-999", "")
        except KeyError:
            ok = True
        record("导购-商品404", ok)


async def main():
    tests = [TestPure(), TestPricing(), TestFeedback(),
             TestGuide()]
    for t in tests:
        await t.run()
    print("=" * 60)
    print("68号 P2 信值·臻选 透明定价与导购专项测试")
    print("=" * 60)
    for line in RESULTS:
        print(line)
    print("-" * 60)
    print(f"通过: {PASS} / {PASS + FAIL}")
    if FAIL:
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
