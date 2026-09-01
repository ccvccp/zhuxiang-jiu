"""37号·AI智能网站同盟模块·P1/P2 专项测试

P1 覆盖(设计文档 §2.2/§2.6):
    1. GeoGrid: 网格编码/范围分配密度仲裁(满员409)/就近推荐
       (距离+评分排序)/范围查询
    2. 简化溯源上链: 商品凭证 evidenceHash 落库
    3. 评价 AI 审评: 正常评价展示/恶意差评自动折叠/星级影响
    4. 月度考核: 等级判定(S/A/B/C)/连续C级暂停→清退

P2 覆盖(设计文档 §2.7):
    5. 酒友小聚: 一单三子单编排/核销码/线下核销(三子单分润起算)/
       幂等核销409
    6. 定制服务: 状态机全链路(报价→确认→生产→交付)/非法转移409

运行:
    $env:LOCK_MODE="asyncio"; $env:STORE_MODE="asyncio"
    python test_alliance_p1p2.py
"""

import asyncio
import os
import sys


os.environ["LOCK_MODE"] = "asyncio"
os.environ["STORE_MODE"] = "asyncio"

from services.alliance_service import AllianceService
from services.alliance_geo_service import (
    AllianceGeoService, AllianceAssessmentService,
    grid_key_for, haversine_km,
)
from services.alliance_scene_service import AllianceSceneService
from repositories.alliance_repository import (
    CATEGORY_TEA, CATEGORY_DISH, CATEGORY_VENUE, CATEGORY_WINE,
    STATUS_ACTIVE, STATUS_SUSPENDED, STATUS_TERMINATED,
    PRODUCT_STATUS_ACTIVE,
    SCENE_STATUS_CREATED, SCENE_STATUS_REDEEMED,
    CUSTOM_STATUS_DEMAND, CUSTOM_STATUS_QUOTED,
    CUSTOM_STATUS_CONFIRMED, CUSTOM_STATUS_PRODUCING,
    CUSTOM_STATUS_DELIVERED, CUSTOM_STATUS_CANCELLED,
    ASSESSMENT_PASS_GMV,
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


async def _create_member(level: int = 5, hint: int = 0):
    repo = MemberRepository()
    phone = f"137{level:02d}{hint:08d}"[-11:]
    return await repo.create({"phone": phone, "name": f"Lv{level}",
                              "level": level, "realnameVerified": True})


async def _active_merchant(svc, category, hint=0, shop="测试商铺"):
    member = await _create_member(5, hint)
    app = await svc.apply(member["id"], category, shop,
                          credentials=["产地凭证"])
    await svc.audit_application(app["applicationId"], approved=True)
    merchant = await svc.repo.find_merchant_by_member(member["id"])
    await svc.activate_merchant(merchant["merchantId"])
    return await svc.confirm_merchant(merchant["merchantId"])


class TestGeoGrid:
    async def run(self):
        record("网格-编码", grid_key_for(36.06, 120.38) == "721:2407",
               grid_key_for(36.06, 120.38))
        record("网格-距离", abs(haversine_km(36.0, 120.0, 36.05, 120.0)
                                - 5.56) < 0.1,
               str(haversine_km(36.0, 120.0, 36.05, 120.0)))

        svc = AllianceService()
        geo = AllianceGeoService()
        tea1 = await _active_merchant(svc, CATEGORY_TEA, hint=1, shop="茶庄一")
        tea2 = await _active_merchant(svc, CATEGORY_TEA, hint=2, shop="茶庄二")
        tea3 = await _active_merchant(svc, CATEGORY_TEA, hint=3, shop="茶庄三")
        tea4 = await _active_merchant(svc, CATEGORY_TEA, hint=4, shop="茶庄四")

        # 茶类 gridCap=3: 同网格第 4 家 → 409
        lat, lng = 36.06, 120.38
        for m in (tea1, tea2, tea3):
            cov = await geo.apply_coverage(m["merchantId"], "grid",
                                           center_lat=lat, center_lng=lng)
            record(f"范围-{m['shopName']}准入",
                   cov["gridKeys"] == [grid_key_for(lat, lng)])
        try:
            await geo.apply_coverage(tea4["merchantId"], "grid",
                                     center_lat=lat, center_lng=lng)
            record("范围-密度满员409", False)
        except ValueError as e:
            record("范围-密度满员409", "密度已满" in str(e), str(e))

        # 异网格准入
        cov4 = await geo.apply_coverage(tea4["merchantId"], "grid",
                                        center_lat=36.30, center_lng=120.38)
        record("范围-异网格准入",
               cov4["gridKeys"] == [grid_key_for(36.30, 120.38)])

        # 就近推荐: 距离排序(近者在前)
        nearby = await geo.nearby_merchants(lat, lng, category=CATEGORY_TEA)
        record("推荐-返回3家(近网格)", len(nearby) == 3,
               f"实际{len(nearby)}")
        record("推荐-距离升序",
               all(nearby[i]["distanceKm"] <= nearby[i + 1]["distanceKm"]
                   for i in range(len(nearby) - 1)),
               str([n["distanceKm"] for n in nearby]))
        record("推荐-含商户信息",
               all({"merchantId", "shopName", "ratingAvg",
                    "distanceKm"} <= set(n) for n in nearby))
        # 定位在茶庄四附近 → 推荐茶庄四
        nearby2 = await geo.nearby_merchants(36.30, 120.38,
                                             category=CATEGORY_TEA)
        record("推荐-就近命中",
               nearby2 and nearby2[0]["merchantId"] == tea4["merchantId"],
               str(nearby2[:1]))

        # 范围查询
        coverage = await geo.merchant_coverage(tea1["merchantId"])
        record("范围-商户范围查询", len(coverage) == 1
               and coverage[0]["level"] == "grid")


class TestLiteTraceEvidence:
    async def run(self):
        svc = AllianceService()
        merchant = await _active_merchant(svc, CATEGORY_TEA, hint=10)
        product = await svc.create_product(
            merchant["merchantId"], "安吉白茶", "鲜爽回甘", 260.0, 20,
            trace_credentials=["批次AJ2026", "产地安吉"])
        record("溯源-凭证上链哈希",
               bool(product["trace"].get("evidenceHash")),
               f"实际{product['trace'].get('evidenceHash')}")


class TestReviewAI:
    async def run(self):
        svc = AllianceService()
        merchant = await _active_merchant(svc, CATEGORY_TEA, hint=20)
        # 先造一条正常 5 星评价(抬商户均分)
        p1 = await svc.create_product(merchant["merchantId"], "正山小种",
                                      "红茶", 100.0, 10,
                                      trace_credentials=["批次ZS"])
        o1 = await svc.place_order(p1["productId"], 30001)
        await svc.settle_order(o1["orderId"])
        good = await svc.submit_review(o1["orderId"], 30001, 5, "茶汤醇厚")
        record("审评-正常评价展示",
               not good["folded"] and good["aiReview"]["action"] == "show",
               f"ai={good.get('aiReview')}")

        # 恶意差评(极端词+低分偏离均分) → AI 自动折叠
        p2 = await svc.create_product(merchant["merchantId"], "大红袍",
                                      "岩茶", 100.0, 10,
                                      trace_credentials=["批次DHP"])
        o2 = await svc.place_order(p2["productId"], 30002)
        await svc.settle_order(o2["orderId"])
        malicious = await svc.submit_review(
            o2["orderId"], 30002, 1, "垃圾黑店骗子玩意")
        record("审评-恶意差评自动折叠",
               malicious["folded"] is True
               and malicious["aiReview"]["action"] == "fold",
               f"ai={malicious.get('aiReview')}")

        # 折叠评价不计入星级
        rating = await svc.get_merchant_rating(merchant["merchantId"])
        record("审评-折叠不计星级",
               rating["ratingCount"] == 1 and rating["ratingAvg"] == 5.0,
               f"实际{rating}")


class TestAssessment:
    async def run(self):
        svc = AllianceService()
        assess = AllianceAssessmentService()

        # 商户A: 大量订单 → S 级
        big = await _active_merchant(svc, CATEGORY_TEA, hint=30, shop="大户茶庄")
        big_product = await svc.create_product(
            big["merchantId"], "限量茶王", "顶配", 1000.0, 100,
            trace_credentials=["批次W"])
        for i in range(12):
            order = await svc.place_order(big_product["productId"],
                                          40000 + i)
            await svc.settle_order(order["orderId"])
        good = None
        for i in range(5):
            order = await svc.place_order(big_product["productId"],
                                          41000 + i)
            await svc.settle_order(order["orderId"])
            good = await svc.submit_review(order["orderId"], 41000 + i, 5,
                                           "好")
        result = await assess.run_monthly(month="2026-09")
        big_row = next(r for r in result["results"]
                       if r["merchantId"] == big["merchantId"])
        record("考核-大户S级",
               big_row["gmv"] >= ASSESSMENT_PASS_GMV * 2
               and big_row["grade"] == "S", f"实际{big_row}")

        # 商户B: 零订单 → C 级; 连续三月 C → 清退
        small = await _active_merchant(svc, CATEGORY_TEA, hint=31,
                                       shop="零单茶庄")
        # 预造两个月历史 C 级
        for month in ("2026-07", "2026-08"):
            await assess.run_monthly(month=month,
                                     merchant_id=small["merchantId"])
        result2 = await assess.run_monthly(month="2026-09",
                                           merchant_id=small["merchantId"])
        small_row = result2["results"][0]
        record("考核-连续3月C清退",
               small_row["consecutiveC"] == 3
               and small_row["action"] == "terminate"
               and small["merchantId"] in result2["terminated"],
               f"实际{small_row}")
        terminated = await svc.get_merchant(small["merchantId"])
        record("考核-清退状态生效",
               terminated["status"] == STATUS_TERMINATED)

        # 商户C: 两月 C → 暂停
        mid = await _active_merchant(svc, CATEGORY_TEA, hint=32, shop="中庸茶庄")
        await assess.run_monthly(month="2026-08",
                                 merchant_id=mid["merchantId"])
        result3 = await assess.run_monthly(month="2026-09",
                                           merchant_id=mid["merchantId"])
        mid_row = result3["results"][0]
        record("考核-连续2月C暂停",
               mid_row["consecutiveC"] == 2
               and mid_row["action"] == "suspend"
               and mid["merchantId"] in result3["suspended"],
               f"实际{mid_row}")
        suspended = await svc.get_merchant(mid["merchantId"])
        record("考核-暂停状态生效",
               suspended["status"] == STATUS_SUSPENDED)

        # 考核历史查询
        history = await assess.merchant_assessment_history(
            small["merchantId"])
        record("考核-历史留痕", len(history) == 3)


class TestGatheringScene:
    async def run(self):
        svc = AllianceService()
        scene_svc = AllianceSceneService()

        # 三类主体: 酒/菜/境
        wine_m = await _active_merchant(svc, CATEGORY_WINE, hint=40,
                                        shop="同盟酒庄")
        from repositories.trace_prod_repository import TraceProdRepository
        await TraceProdRepository().save_batch({
            "batchNo": "BATCH-SCENE", "batchId": 9, "status": "released",
            "currentStageSeq": 7, "lifeCodes": ["L1"], "createdAt": ""})
        wine_p = await svc.create_product(
            wine_m["merchantId"], "聚会用酒", "口粮酒", 200.0, 10,
            trace_batch_no="BATCH-SCENE")
        dish_m = await _active_merchant(svc, CATEGORY_DISH, hint=41,
                                        shop="私厨")
        dish_p = await svc.create_product(
            dish_m["merchantId"], "私厨配菜", "按位", 88.0, 20,
            trace_credentials=["食材凭证"])
        venue_m = await _active_merchant(svc, CATEGORY_VENUE, hint=42,
                                         shop="酒店")
        venue_p = await svc.create_product(
            venue_m["merchantId"], "包间场次", "6人包间", 300.0, 5,
            trace_credentials=["消防验收", "食品经营许可证"])

        # 类目校验: 配菜商户传酒庄 → 409
        try:
            await scene_svc.create_gathering(50001, 6, wine_p["productId"],
                                             wine_m["merchantId"],
                                             venue_m["merchantId"])
            record("小聚-配菜类目校验409", False)
        except ValueError:
            record("小聚-配菜类目校验409", True)

        # 正常编排: 一单三子单
        scene = await scene_svc.create_gathering(
            50001, 6, wine_p["productId"], dish_m["merchantId"],
            venue_m["merchantId"], gathering_time="2026-09-06 18:00")
        record("小聚-三子单编排", len(scene["items"]) == 3
               and {i["type"] for i in scene["items"]}
               == {"wine", "dish", "venue"})
        expected_total = round(200.0 + 88.0 * 6 + 300.0, 2)
        record("小聚-金额汇总", scene["totalAmount"] == expected_total,
               f"实际{scene['totalAmount']} 期望{expected_total}")
        record("小聚-核销码生成", scene["redeemCode"].startswith("RP-"))
        record("小聚-初始状态", scene["status"] == SCENE_STATUS_CREATED)

        # 库存联动: 菜 20-6=14
        record("小聚-配菜库存按人数扣减",
               (await svc.get_product(dish_p["productId"]))["stock"] == 14)

        # 线下核销: 三子单分润起算
        redeemed = await scene_svc.redeem(scene["redeemCode"])
        record("核销-场景单完成",
               redeemed["scene"]["status"] == SCENE_STATUS_REDEEMED)
        record("核销-三子单结算",
               len(redeemed["settlements"]) == 3,
               f"实际{len(redeemed['settlements'])}")
        # 子单 settled 标记
        for item in scene["items"]:
            order = await svc.repo.get_order(item["orderId"])
            record(f"核销-子单{item['type']}已结算",
                   order.get("settled") is True)

        # 幂等核销 → 409
        try:
            await scene_svc.redeem(scene["redeemCode"])
            record("核销-重复核销409", False)
        except ValueError as e:
            record("核销-重复核销409", "已使用" in str(e), str(e))

        # 我的场景列表
        scenes = await scene_svc.list_scenes(user_id=50001)
        record("小聚-场景列表", len(scenes) == 1)

        # 核销码不存在 → 404
        try:
            await scene_svc.redeem("RP-00000-XXXXXXXX")
            record("核销-码不存在404", False)
        except KeyError:
            record("核销-码不存在404", True)


class TestCustomDemand:
    async def run(self):
        svc = AllianceService()
        scene_svc = AllianceSceneService()
        vessel_m = await _active_merchant(svc, CATEGORY_TEA, hint=50,
                                          shop="定制茶庄")

        # 非法类型 → 409
        try:
            await scene_svc.create_custom_demand(60001,
                                                 vessel_m["merchantId"],
                                                 "unknown", "描述")
            record("定制-非法类型409", False)
        except ValueError:
            record("定制-非法类型409", True)

        # 正常链路: demand→quoted→confirmed→producing→delivered
        demand = await scene_svc.create_custom_demand(
            60001, vessel_m["merchantId"], "engraving",
            "酒具刻字: 竹香雅韵", budget=500)
        record("定制-需求提交",
               demand["status"] == CUSTOM_STATUS_DEMAND)

        quoted = await scene_svc.quote_custom_demand(
            demand["demandId"], quoted_price=680.0)
        record("定制-商户报价",
               quoted["status"] == CUSTOM_STATUS_QUOTED
               and quoted["quotedPrice"] == 680.0)

        # 非本人确认 → 409
        try:
            await scene_svc.confirm_custom_demand(demand["demandId"],
                                                  user_id=99999)
            record("定制-非本人确认409", False)
        except ValueError:
            record("定制-非本人确认409", True)

        confirmed = await scene_svc.confirm_custom_demand(
            demand["demandId"], user_id=60001)
        record("定制-用户确认",
               confirmed["status"] == CUSTOM_STATUS_CONFIRMED)

        producing = await scene_svc.advance_custom_demand(
            demand["demandId"], target=CUSTOM_STATUS_PRODUCING)
        record("定制-生产中",
               producing["status"] == CUSTOM_STATUS_PRODUCING)
        delivered = await scene_svc.advance_custom_demand(
            demand["demandId"], target=CUSTOM_STATUS_DELIVERED)
        record("定制-交付",
               delivered["status"] == CUSTOM_STATUS_DELIVERED)

        # 终态再转移 → 409
        try:
            await scene_svc.advance_custom_demand(
                demand["demandId"], target=CUSTOM_STATUS_CANCELLED)
            record("定制-终态转移409", False)
        except ValueError:
            record("定制-终态转移409", True)

        # 取消链路: 新需求 quote 前取消
        demand2 = await scene_svc.create_custom_demand(
            60002, vessel_m["merchantId"], "private_feast", "家宴定制")
        cancelled = await scene_svc.advance_custom_demand(
            demand2["demandId"], target=CUSTOM_STATUS_CANCELLED)
        record("定制-需求取消",
               cancelled["status"] == CUSTOM_STATUS_CANCELLED)

        # 列表
        demands = await scene_svc.list_custom_demands(
            merchant_id=vessel_m["merchantId"])
        record("定制-列表", len(demands) == 2)


async def main():
    test_classes = [
        ("P1·GeoGrid地图引擎", TestGeoGrid),
        ("P1·简化溯源上链", TestLiteTraceEvidence),
        ("P1·评价AI审评", TestReviewAI),
        ("P1·月度考核", TestAssessment),
        ("P2·酒友小聚场景", TestGatheringScene),
        ("P2·定制服务", TestCustomDemand),
    ]
    print("=" * 62)
    print("37号·AI智能网站同盟模块 P1/P2 专项测试")
    print("=" * 62)
    for name, cls in test_classes:
        reset_store()
        print(f"\n[{name}]")
        try:
            await cls().run()
        except Exception as e:
            record(f"{name} 测试执行异常", False, str(e))

    print("\n" + "-" * 62)
    for line in RESULTS:
        print(line)
    print("-" * 62)
    print(f"总计: {PASS} 通过 / {FAIL} 失败")
    return FAIL


if __name__ == "__main__":
    sys.exit(asyncio.run(main()) and 1 or 0)
