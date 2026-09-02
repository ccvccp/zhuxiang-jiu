"""41号·AI智能代驾模块 P3 专项测试(双向评价 + AI 审评)

运行方式:
    python test_ride_p3.py

覆盖(设计文档 §2.4 行后双向评价):
    - 第24档案注册(ride_review 注册表/默认权重/评分器单例)
    - 评分器直测(极端词/人身攻击/广告刷评/高频/分值偏离 → show/watch/fold)
    - 增量口碑纯函数(司机评分回写公式)
    - 乘客评司机全流程(正常评价 show → 评分回写; 垃圾评价 fold → 不回写)
    - 司机评乘客(留档观察, 不回写)
    - 幂等与身份校验(重复评价/非乘客本人/非当班司机/星级越界)
    - 行程状态门槛(未结算不可评/平台取消不可评)
    - fold 文本不外泄(司机侧与管理侧观察)
    - HTTP 层(评价提交/查询/统计端点)
"""

import asyncio
import os
import sys

# 必须在 import 业务模块之前设置(对齐 41号 P0-P2 测试惯例)
os.environ["LOCK_MODE"] = "asyncio"
os.environ["STORE_MODE"] = "asyncio"
os.environ.pop("LLM_API_KEY", None)
os.environ["LLM_ENABLED"] = "off"

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


def reset_store():
    from repositories.store import reset_store as _reset
    _reset()


NEAR = {"lat": 36.1905, "lng": 117.130, "address": "泰安老字号饭店"}
CENTER = {"lat": 36.19, "lng": 117.13, "address": "泰安市区中心"}
FAR_PICKUP = {"lat": 36.29, "lng": 117.13, "address": "郊区"}


async def grant_coupon(member_id=1, order_id="ORD", amount=800.0):
    from services.ride_coupon_service import RideCouponService
    return await RideCouponService().grant_for_order(
        member_id, order_id, amount)


async def settle_trip(svc, member_id=1, km=8.0, hour=14, minutes=0):
    """测试前置: 跑完一个 settled 行程(近点 → AI 派单 → 三步结算)

    Returns:
        (ride_id, driver_id)
    """
    await grant_coupon(member_id, f"ORD{member_id}_{km}_{hour}", 800.0)
    r = await svc.call(member_id, NEAR, CENTER, distance_km=km)
    ride_id = r["rideId"]
    driver_id = r["driverSnapshot"]["driverId"]
    ride = await svc.repo.get_ride(ride_id)
    ride["status"] = "driver_arriving"
    ride["arrivingAt"] = ride["requestedAt"]
    await svc.repo.save_ride(ride)
    ride["status"] = "trip_started"
    ride["startedAt"] = ride["requestedAt"]
    await svc.repo.save_ride(ride)
    driver = await svc.repo.get_driver(driver_id)
    ride["completedAt"] = ride["requestedAt"]
    await svc.repo.save_ride(ride)
    await svc._settle(ride, driver=driver,
                      duration_minutes=minutes, pricing_hour=hour)
    return ride_id, driver_id


class TestScorerAndRegistry:
    async def run(self):
        from services.ai_scoring_service import (
            SCORERS, RideReviewScorer,
        )
        from services.ai_learning_service import (
            SCORER_REGISTRY, default_weights,
        )

        record("档案-注册表含ride_review", "ride_review" in SCORER_REGISTRY)
        record("档案-batch=8",
               SCORER_REGISTRY.get("ride_review", {}).get("batch") == 8)
        record("档案-默认权重映射",
               default_weights("ride_review") == RideReviewScorer.WEIGHTS)
        record("档案-评分器单例",
               isinstance(SCORERS.get("ride_review"), RideReviewScorer))
        record("档案-权重和=1",
               abs(sum(RideReviewScorer.WEIGHTS.values()) - 1) < 1e-9)

        scorer = SCORERS["ride_review"]
        # 正常好评 → show
        r = await scorer.score({
            "reviewId": 1, "rideId": "RD1",
            "direction": "passenger_to_driver", "driverId": 1,
            "memberId": 1, "score": 5, "content": "师傅很稳, 服务周到",
            "driverRating": 4.9, "reviewerReviewsToday": 0})
        record("评分-正常好评show", r["action"] == "show", str(r["score"]))

        # 极端+攻击 → fold(单一极端词 60×0.25=15 + 攻击 80×0.25=20 + 偏离)
        r = await scorer.score({
            "reviewId": 2, "rideId": "RD2",
            "direction": "passenger_to_driver", "driverId": 1,
            "memberId": 1, "score": 1, "content": "垃圾玩意, 骗子司机",
            "driverRating": 4.9, "reviewerReviewsToday": 0})
        record("评分-恶意差评fold", r["action"] == "fold", str(r["score"]))

        # 广告刷评(多词+高频) → watch/fold(单广告词 18 分不足 30 线;
        # 多词 clamp 100×0.2=20 + 高频 5 条 100×0.15=15 → 35 过线)
        r = await scorer.score({
            "reviewId": 3, "rideId": "RD3",
            "direction": "passenger_to_driver", "driverId": 1,
            "memberId": 1, "score": 5,
            "content": "加微信低价出, 点击链接领优惠券",
            "driverRating": 4.9, "reviewerReviewsToday": 5})
        record("评分-广告刷评处置", r["action"] in ("watch", "fold"),
               str(r["score"]))

        # 短时高频(当日 5 条) → 满分频次因子
        r = await scorer.score({
            "reviewId": 4, "rideId": "RD4",
            "direction": "passenger_to_driver", "driverId": 1,
            "memberId": 1, "score": 5, "content": "还行",
            "driverRating": 4.9, "reviewerReviewsToday": 5})
        freq = next(f for f in r["factors"] if f["name"] == "frequency")
        record("评分-高频因子满分", freq["score"] == 100.0, str(freq))

        # 分值偏离(1 星 vs 司机 4.9)
        r = await scorer.score({
            "reviewId": 5, "rideId": "RD5",
            "direction": "passenger_to_driver", "driverId": 1,
            "memberId": 1, "score": 1, "content": "一般",
            "driverRating": 4.9, "reviewerReviewsToday": 0})
        dev = next(f for f in r["factors"] if f["name"] == "score_deviation")
        record("评分-偏离因子", dev["score"] >= 100.0, str(dev))

        # 五因子输出
        record("评分-五因子输出", len(r["factors"]) == 5)


class TestIncrementalRating:
    async def run(self):
        from services.ride_review_service import incremental_rating

        # 初始 (4.9, 312 单) + 5 星 → (4.9*312+5)/313
        r = incremental_rating(4.9, 312, 5)
        record("口碑-正常回写", abs(r - 4.9) < 0.01, str(r))
        # 低分拉低
        r = incremental_rating(5.0, 0, 1)
        record("口碑-新司机1星", r == 1.0, str(r))
        r = incremental_rating(5.0, 0, 4)
        record("口碑-新司机4星", r == 4.0, str(r))
        # 大基数稳定
        r = incremental_rating(4.9, 10000, 1)
        record("口碑-大基数稳定", abs(r - 4.9) < 0.01, str(r))


class TestPassengerReview:
    async def run(self):
        from services.ride_dispatch_service import RideDispatchService
        from services.ride_review_service import RideReviewService

        svc = RideDispatchService()
        review_svc = RideReviewService()
        ride_id, driver_id = await settle_trip(svc)

        # 未评价前司机评分 4.9(种子王师傅)
        driver_before = await svc.repo.get_driver(driver_id)
        record("评价前-司机评分4.9", driver_before["rating"] == 4.9)

        # 正常 5 星好评 → show + 评分回写
        r = await review_svc.submit(1, ride_id, "passenger_to_driver",
                                    5, "师傅提前到, 开车稳, 点赞")
        record("乘客评-正常提交", r["success"] is True)
        review = r["review"]
        record("乘客评-AI审评show", review["action"] == "show",
               str(review.get("action")))
        record("乘客评-评分回写标记", review["ratingApplied"] is True)
        driver_after = await svc.repo.get_driver(driver_id)
        expected = round((4.9 * 312 + 5) / 313, 1)
        record("乘客评-司机评分回写",
               abs(driver_after["rating"] - expected) < 0.05,
               f"{driver_after['rating']} vs {expected}")

        # 行程评价状态回写
        ride = await svc.repo.get_ride(ride_id)
        record("乘客评-行程状态回写",
               ride["review"]["passenger_to_driver"] == "done")

        # 重复评价 → 409
        try:
            await review_svc.submit(1, ride_id, "passenger_to_driver",
                                    4, "再评一次")
            record("乘客评-重复拒绝", False, "未抛出")
        except ValueError:
            record("乘客评-重复拒绝", True)

        # 非乘客本人 → 409
        ride_id2, _ = await settle_trip(svc, member_id=1, km=9.0)
        try:
            await review_svc.submit(2, ride_id2, "passenger_to_driver",
                                    5, "冒充评价")
            record("乘客评-非本人拒绝", False, "未抛出")
        except ValueError:
            record("乘客评-非本人拒绝", True)

        # 星级越界 → 409
        try:
            await review_svc.submit(1, ride_id2, "passenger_to_driver",
                                    6, "超星")
            record("乘客评-星级越界拒绝", False, "未抛出")
        except ValueError:
            record("乘客评-星级越界拒绝", True)

        # 垃圾评价(恶意差评) → fold + 不回写
        driver_before = await svc.repo.get_driver(
            (await svc.repo.get_ride(ride_id2))["driverId"])
        r = await review_svc.submit(1, ride_id2, "passenger_to_driver",
                                    1, "垃圾玩意, 骗子司机")
        review = r["review"]
        record("乘客评-恶意差评fold", review["action"] == "fold",
               str(review.get("action")))
        record("乘客评-fold不回写", review["ratingApplied"] is False)
        driver_after = await svc.repo.get_driver(
            (await svc.repo.get_ride(ride_id2))["driverId"])
        record("乘客评-fold评分不变",
               driver_after["rating"] == driver_before["rating"],
               f"{driver_before['rating']} → {driver_after['rating']}")


class TestDriverReview:
    async def run(self):
        from services.ride_dispatch_service import RideDispatchService
        from services.ride_review_service import RideReviewService
        from repositories.member_repository import MemberRepository

        svc = RideDispatchService()
        review_svc = RideReviewService()

        # 造司机会员: 会员1 审查通过入池
        await MemberRepository().update_fields(1, {"level": 5})
        from services.driver_gate_service import DriverGateService
        await DriverGateService().apply(1, {
            "idNumber": "370900199001010011",
            "licenseNumber": "370900123456",
            "licenseClass": "C1", "drivingYears": 8,
            "accidentFreeDecl": True, "drunkFreeDecl": True,
            "emergencyContact": "王紧急", "bambooScore": 800})
        driver = await svc.repo.get_driver_by_member(1)
        # 挂载本站司机行程: 用司机自己的车接会员2? 简化——直接改行程司机
        # 造会员2的 settled 行程后, 把 driverId 指到会员1 的司机(测试口径)
        await grant_coupon(2, "DRV2", 800.0)
        r = await svc.call(2, NEAR, CENTER, distance_km=8.0)
        ride_id = r["rideId"]
        ride = await svc.repo.get_ride(ride_id)
        old_driver_id = ride["driverId"]
        ride["driverId"] = driver["driverId"]
        await svc.repo.save_ride(ride)
        # 释放原司机, 占用新司机
        old_driver = await svc.repo.get_driver(old_driver_id)
        old_driver["currentRideId"] = ""
        await svc.repo.save_driver(old_driver)
        # 推进结算
        ride["status"] = "trip_started"
        ride["startedAt"] = ride["requestedAt"]
        ride["completedAt"] = ride["requestedAt"]
        await svc.repo.save_ride(ride)
        d = await svc.repo.get_driver(driver["driverId"])
        await svc._settle(ride, driver=d, duration_minutes=0,
                          pricing_hour=14)

        # 司机评乘客 → 留档, 不回写
        r = await review_svc.submit(1, ride_id, "driver_to_passenger",
                                    5, "乘客礼貌, 目的地清晰")
        record("司机评-正常提交", r["success"] is True)
        record("司机评-留档观察", r["review"]["direction"]
               == "driver_to_passenger")
        record("司机评-不回写标记", r["review"].get("ratingApplied") is False
               or "ratingApplied" not in r["review"])

        # 行程双向状态
        ride = await svc.repo.get_ride(ride_id)
        record("司机评-行程状态回写",
               ride["review"].get("driver_to_passenger") == "done")

        # 双向独立: 乘客仍可评
        r = await review_svc.submit(2, ride_id, "passenger_to_driver",
                                    4, "服务不错")
        record("双向-乘客后评独立", r["success"] is True)

        # 非当班司机 → 409(会员2 无司机身份)
        ride_id3, _ = await settle_trip(svc, member_id=2, km=7.0)
        try:
            await review_svc.submit(2, ride_id3, "driver_to_passenger",
                                    5, "冒充司机")
            record("司机评-非当班拒绝", False, "未抛出")
        except ValueError:
            record("司机评-非当班拒绝", True)


class TestReviewGuards:
    async def run(self):
        from services.ride_dispatch_service import RideDispatchService
        from services.ride_review_service import RideReviewService

        svc = RideDispatchService()
        review_svc = RideReviewService()

        # 未结算行程不可评
        await grant_coupon(1, "G1", 800.0)
        r = await svc.call(1, NEAR, CENTER, distance_km=8.0)
        try:
            await review_svc.submit(1, r["rideId"],
                                    "passenger_to_driver", 5, "早评")
            record("门槛-未结算拒绝", False, "未抛出")
        except ValueError:
            record("门槛-未结算拒绝", True)

        # 平台取消行程不可评
        await grant_coupon(1, "G2", 800.0)
        r = await svc.call(1, FAR_PICKUP, CENTER, distance_km=11.0)
        po = r["driverSnapshot"]["partnerOrderId"]
        await svc.partner_callback({"partnerOrderId": po,
                                    "event": "cancelled"})
        ride = await svc.repo.get_ride(r["rideId"])
        record("门槛-平台取消状态", ride["status"] == "cancelled")
        try:
            await review_svc.submit(1, r["rideId"],
                                    "passenger_to_driver", 5, "评取消单")
            record("门槛-平台取消拒绝", False, "未抛出")
        except ValueError:
            record("门槛-平台取消拒绝", True)

        # 乘客免责取消的行程也不可评(非 settled) — 已由状态门槛覆盖
        # 不存在行程 → 404
        try:
            await review_svc.submit(1, "RD99999999",
                                    "passenger_to_driver", 5, "x")
            record("门槛-行程不存在404", False, "未抛出")
        except KeyError:
            record("门槛-行程不存在404", True)

        # 非法方向
        ride_id, _ = await settle_trip(svc)
        try:
            await review_svc.submit(1, ride_id, "sideways", 5, "x")
            record("门槛-非法方向拒绝", False, "未抛出")
        except ValueError:
            record("门槛-非法方向拒绝", True)

        # fold 文本不外泄: 管理侧可见原文(审评观察), 司机侧见 HTTP 层测试
        r = await review_svc.submit(1, ride_id, "passenger_to_driver",
                                    1, "垃圾玩意骗子司机")
        admin_list = await review_svc.admin_reviews(action="fold")
        record("外泄-fold管理侧可见原文", len(admin_list) >= 1
               and "垃圾" in admin_list[0]["content"])


class TestHttpRoutes:
    async def run(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from routes.ride_routes import register_ride_routes
        from services.ride_dispatch_service import RideDispatchService

        app = FastAPI()
        register_ride_routes(app)
        client = TestClient(app)
        member = {"X-Member-Id": "1"}
        admin = {"X-Role": "admin"}

        # settled 行程
        svc = RideDispatchService()
        ride_id, driver_id = await settle_trip(svc)

        # 正常评价
        resp = client.post(f"/api/ride/orders/{ride_id}/review",
                           headers=member, json={
                               "direction": "passenger_to_driver",
                               "score": 5, "content": "很棒"})
        body = resp.json()
        record("HTTP-评价提交", resp.status_code == 200
               and body["review"]["action"] == "show",
               str(resp.text[:150]))

        # 重复 → 409
        resp = client.post(f"/api/ride/orders/{ride_id}/review",
                           headers=member, json={
                               "direction": "passenger_to_driver",
                               "score": 4, "content": "再评"})
        record("HTTP-重复评价409", resp.status_code == 409,
               str(resp.status_code))

        # 行程评价查询
        resp = client.get(f"/api/ride/orders/{ride_id}/reviews",
                          headers=member)
        record("HTTP-行程评价查询", resp.status_code == 200
               and resp.json()["passengerReview"] is not None)
        resp = client.get(f"/api/ride/orders/{ride_id}/reviews",
                          headers={"X-Member-Id": "2"})
        record("HTTP-非本人评价查询409", resp.status_code == 409,
               str(resp.status_code))

        # fold 评价
        ride_id2, _ = await settle_trip(svc, member_id=1, km=9.0)
        resp = client.post(f"/api/ride/orders/{ride_id2}/review",
                           headers=member, json={
                               "direction": "passenger_to_driver",
                               "score": 1, "content": "垃圾玩意骗子"})
        record("HTTP-fold评价", resp.status_code == 200
               and resp.json()["review"]["action"] == "fold")

        # 管理端列表/统计
        resp = client.get("/api/ride/admin/reviews", headers=admin,
                          params={"action": "fold"})
        record("HTTP-管理fold过滤", resp.status_code == 200
               and resp.json()["total"] == 1, str(resp.json().get("total")))
        resp = client.get("/api/ride/admin/review-stats", headers=admin)
        body = resp.json()
        record("HTTP-评价统计", resp.status_code == 200
               and body["byAction"]["fold"] == 1
               and body["byAction"]["show"] >= 1, str(body.get("byAction")))
        resp = client.get("/api/ride/admin/review-stats")
        record("HTTP-统计非admin403", resp.status_code == 403)

        # 司机侧查询(会员1非司机 → 404)
        resp = client.get("/api/ride/driver/reviews", headers=member)
        record("HTTP-非司机查评价404", resp.status_code == 404,
               str(resp.status_code))

        # 鉴权: 无头 403
        resp = client.post(f"/api/ride/orders/{ride_id}/review", json={
            "direction": "passenger_to_driver", "score": 5})
        record("HTTP-评价缺头403", resp.status_code == 403,
               str(resp.status_code))


async def main():
    test_classes = [
        ("评分器与第24档案", TestScorerAndRegistry),
        ("增量口碑纯函数", TestIncrementalRating),
        ("乘客评司机", TestPassengerReview),
        ("司机评乘客", TestDriverReview),
        ("门槛与外泄防护", TestReviewGuards),
        ("HTTP层", TestHttpRoutes),
    ]
    print("=" * 62)
    print("41号·AI智能代驾模块 P3 专项测试(双向评价+AI审评)")
    print("=" * 62)
    for name, cls in test_classes:
        reset_store()
        print(f"\n[{name}]")
        try:
            await cls().run()
        except Exception as e:
            record(f"{name} 测试执行异常", False, repr(e))

    print("\n" + "-" * 62)
    print("\n".join(RESULTS))
    print("-" * 62)
    print(f"总计: {PASS} 通过 / {FAIL} 失败")
    return FAIL


if __name__ == "__main__":
    sys.exit(1 if asyncio.run(main()) else 0)
