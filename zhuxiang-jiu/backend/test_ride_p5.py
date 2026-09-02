"""41号·AI智能代驾模块 P5 专项测试(评价真值标注回流 + 营销ROI报表)

运行方式:
    python test_ride_p5.py

覆盖:
    - 真值标注(正常标注/非法动作/重复标注幂等/不存在404)
    - 评价回流(已标注→提交/expectedAction=标注/correct口径/fold误判
      reward=-0.8/幂等/未标注skip)
    - ride_review 学习触发(run_learning 反馈不足口径)
    - 调度器接线(三档案 collect 均被调用)
    - ROI 报表(发放/核销率/复购/成本/券均拉动)
    - HTTP 层(annotate/roi/learning 三档案扩展)
"""

import asyncio
import os
import sys
from datetime import datetime, UTC

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


async def grant_coupon(member_id=1, order_id="ORD", amount=800.0):
    from services.ride_coupon_service import RideCouponService
    return await RideCouponService().grant_for_order(
        member_id, order_id, amount)


async def settle_and_review(svc, review_svc, stars, content,
                            member_id=1, km=8.0, tag=""):
    """settled 行程 + 乘客评价 → 返回 (ride_id, review)"""
    await grant_coupon(member_id, f"P5{tag}{stars}", 800.0)
    r = await svc.call(member_id, NEAR, CENTER, distance_km=km)
    ride_id = r["rideId"]
    ride = await svc.repo.get_ride(ride_id)
    ride["status"] = "trip_started"
    ride["startedAt"] = ride["requestedAt"]
    ride["completedAt"] = ride["requestedAt"]
    await svc.repo.save_ride(ride)
    driver = await svc.repo.get_driver(r["driverSnapshot"]["driverId"])
    await svc._settle(ride, driver=driver,
                      duration_minutes=0, pricing_hour=14)
    result = await review_svc.submit(member_id, ride_id,
                                     "passenger_to_driver",
                                     stars, content)
    return ride_id, result["review"]


class TestAnnotate:
    async def run(self):
        from services.ride_dispatch_service import RideDispatchService
        from services.ride_review_service import RideReviewService

        svc = RideDispatchService()
        review_svc = RideReviewService()
        _, review = await settle_and_review(svc, review_svc, 5,
                                            "很好", tag="A")

        # 正常标注
        annotated = await review_svc.annotate(review["reviewId"],
                                              "show", note="正常好评")
        record("标注-正常", annotated["annotatedAction"] == "show"
               and annotated["annotator"] == "admin")
        record("标注-留痕", "正常好评" in annotated.get("annotationNote",
                                                    ""))

        # 重复标注 → 409
        try:
            await review_svc.annotate(review["reviewId"], "fold")
            record("标注-重复拒绝", False, "未抛出")
        except ValueError:
            record("标注-重复拒绝", True)

        # 非法动作
        _, review2 = await settle_and_review(svc, review_svc, 3,
                                             "还行", tag="B")
        try:
            await review_svc.annotate(review2["reviewId"], "flying")
            record("标注-非法动作拒绝", False, "未抛出")
        except ValueError:
            record("标注-非法动作拒绝", True)

        # 不存在 → 404
        try:
            await review_svc.annotate(9999, "show")
            record("标注-不存在404", False, "未抛出")
        except KeyError:
            record("标注-不存在404", True)


class TestReviewFeedback:
    async def run(self):
        from services.ride_dispatch_service import RideDispatchService
        from services.ride_review_service import RideReviewService
        from repositories.ai_learning_repository import (
            AiLearningRepository,
        )

        svc = RideDispatchService()
        review_svc = RideReviewService()

        # ① 命中口径: show 评价标注 show → correct=True
        _, good = await settle_and_review(svc, review_svc, 5,
                                          "非常好", tag="C")
        await review_svc.annotate(good["reviewId"], "show")
        result = await review_svc.collect_review_feedback()
        record("回流-命中提交1条", result["submitted"] == 1,
               str(result))
        feedbacks = await AiLearningRepository().list_feedback(
            "ride_review", limit=10)
        fb = feedbacks[-1]
        record("回流-expectedAction=标注",
               fb.get("expectedAction") == "show"
               and fb.get("actualAction") == "show")
        record("回流-correct=True", fb.get("correct") is True)
        record("回流-命中reward=0.5", fb.get("reward") == 0.5,
               str(fb.get("reward")))

        # 幂等: 重跑 skip
        result = await review_svc.collect_review_feedback()
        record("回流-幂等skip", result["submitted"] == 0
               and result["skipped"] >= 1, str(result))

        # ② fold 误判口径: fold 评价被管理员改判 show → 重罚 -0.8
        _, folded = await settle_and_review(
            svc, review_svc, 1, "垃圾玩意骗子", tag="D")
        record("回流-fold前置", folded["action"] == "fold",
               str(folded.get("action")))
        await review_svc.annotate(folded["reviewId"], "show",
                                  note="误折叠")
        result = await review_svc.collect_review_feedback()
        record("回流-误折叠提交", result["submitted"] == 1, str(result))
        feedbacks = await AiLearningRepository().list_feedback(
            "ride_review", limit=10)
        fb = feedbacks[-1]
        record("回流-误折叠correct=False", fb.get("correct") is False)
        record("回流-fold误判reward=-0.8", fb.get("reward") == -0.8,
               str(fb.get("reward")))

        # ③ 漏折叠口径: show 评价被管理员改判 fold → -0.8
        _, missed = await settle_and_review(svc, review_svc, 4,
                                            "广告刷评加微信", tag="E")
        # 该评价内容触发广告词 → 可能 watch/fold; 构造确定 show:
        # 用无垃圾词评价但管理员标 fold
        _, missed = await settle_and_review(svc, review_svc, 4,
                                            "服务不错", tag="F")
        record("回流-show前置", missed["action"] == "show",
               str(missed.get("action")))
        await review_svc.annotate(missed["reviewId"], "fold",
                                  note="漏折叠")
        await review_svc.collect_review_feedback()
        feedbacks = await AiLearningRepository().list_feedback(
            "ride_review", limit=10)
        fb = feedbacks[-1]
        record("回流-漏折叠correct=False", fb.get("correct") is False)
        record("回流-漏折叠reward=-0.8", fb.get("reward") == -0.8,
               str(fb.get("reward")))

        # ④ 未标注 → skip(无真值)
        _, unannotated = await settle_and_review(svc, review_svc, 3,
                                                 "一般", tag="G")
        before = len(await AiLearningRepository().list_feedback(
            "ride_review", limit=100))
        result = await review_svc.collect_review_feedback()
        after = len(await AiLearningRepository().list_feedback(
            "ride_review", limit=100))
        record("回流-未标注skip", after - before == 0,
               f"{before}→{after}")

        # ⑤ 学习触发(3 条反馈不足 10 → ValueError; 足量则跑通)
        try:
            learned = await review_svc.run_learning()
            record("回流-Hedge学习", learned.get("success") is True,
                   str(learned.get("detail", ""))[:100])
        except ValueError:
            record("回流-Hedge学习", True, "反馈不足(阈值内)")


class TestScheduler:
    async def run(self):
        # 调度器接线: _learning_loop 引用三档案 collect
        import inspect
        from services import ride_scheduler

        src = inspect.getsource(ride_scheduler._learning_loop)
        record("调度-三档案collect接线",
               "collect_learning_feedback" in src
               and "collect_application_feedback" in src
               and "collect_review_feedback" in src)
        record("调度-三档案run接线", src.count("run_learning") >= 1
               and src.count("(review, \"ride_review\")") == 1)
        record("调度-默认off", ride_scheduler.learning_enabled() is False)


class TestRoi:
    async def run(self):
        from services.ride_coupon_service import RideCouponService
        from services.ride_dispatch_service import RideDispatchService
        from services.ride_review_service import RideReviewService

        coupon_svc = RideCouponService()
        svc = RideDispatchService()
        review_svc = RideReviewService()

        # 数据: 会员1 两订单赠券(复购) + 会员2 一订单
        await coupon_svc.grant_for_order(1, "ROI-A", 800.0)   # 1张
        await coupon_svc.grant_for_order(1, "ROI-B", 2500.0)  # 2张
        await coupon_svc.grant_for_order(2, "ROI-C", 3500.0)  # 3张

        # 核销一张(行程结算): 会员1 用券 → settled
        # (settle_and_review 自身会再发 1 张券 P5H5 → 总发放 7)
        ride_id, _ = await settle_and_review(svc, review_svc, 5,
                                             "好", tag="H")

        roi = await coupon_svc.admin_roi()
        record("ROI-发放总数7", roi["totalGranted"] == 7,
               str(roi["totalGranted"]))
        record("ROI-核销1", (roi["byStatus"].get("used") or 0) == 1,
               str(roi["byStatus"]))
        record("ROI-核销率", roi["usedRate"] == round(1 / 7, 4),
               str(roi["usedRate"]))
        record("ROI-独立会员2", roi["distinctMembers"] == 2,
               str(roi["distinctMembers"]))
        record("ROI-复购会员1", roi["repeatMembers"] == 1,
               str(roi["repeatMembers"]))
        record("ROI-复购率0.5", roi["repeatRate"] == 0.5,
               str(roi["repeatRate"]))
        record("ROI-营销成本=核销面值", roi["marketingCost"] > 0,
               str(roi["marketingCost"]))
        record("ROI-券均拉动", roi["avgRideAmountPerUsedCoupon"] > 0,
               str(roi["avgRideAmountPerUsedCoupon"]))
        record("ROI-核销行程计数",
               roi["settledRidesWithCoupon"] == 1,
               str(roi["settledRidesWithCoupon"]))

        # 空数据口径
        from repositories.store import reset_store
        reset_store()
        roi = await coupon_svc.admin_roi()
        record("ROI-空数据零除容错", roi["totalGranted"] == 0
               and roi["usedRate"] == 0.0
               and roi["repeatRate"] == 0.0
               and roi["marketingCost"] == 0.0, str(roi))


class TestHttpRoutes:
    async def run(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from routes.ride_routes import register_ride_routes
        from services.ride_dispatch_service import RideDispatchService
        from services.ride_review_service import RideReviewService

        app = FastAPI()
        register_ride_routes(app)
        client = TestClient(app)
        admin = {"X-Role": "admin"}

        # 前置: settled+评价
        svc = RideDispatchService()
        review_svc = RideReviewService()
        _, review = await settle_and_review(svc, review_svc, 5,
                                            "不错", tag="I")

        # 标注端点
        resp = client.post(
            f"/api/ride/admin/reviews/{review['reviewId']}/annotate",
            headers=admin, json={"expectedAction": "show",
                                 "note": "HTTP标注"})
        body = resp.json()
        record("HTTP-标注", resp.status_code == 200
               and body["review"]["annotatedAction"] == "show",
               str(resp.text[:150]))
        resp = client.post(
            f"/api/ride/admin/reviews/{review['reviewId']}/annotate",
            headers=admin, json={"expectedAction": "fold"})
        record("HTTP-重复标注409", resp.status_code == 409,
               str(resp.status_code))
        resp = client.post(
            "/api/ride/admin/reviews/9999/annotate", headers=admin,
            json={"expectedAction": "show"})
        record("HTTP-标注不存在404", resp.status_code == 404,
               str(resp.status_code))
        resp = client.post(
            f"/api/ride/admin/reviews/{review['reviewId']}/annotate",
            json={"expectedAction": "show"})
        record("HTTP-标注非admin403", resp.status_code == 403)

        # learning collect 含 review
        resp = client.post("/api/ride/admin/learning/collect",
                           headers=admin)
        body = resp.json()
        record("HTTP-collect三档案", resp.status_code == 200
               and "dispatch" in body and "gate" in body
               and "review" in body
               and body["review"]["submitted"] == 1, str(body)[:150])

        # learning status 含 review
        resp = client.get("/api/ride/admin/learning/status", headers=admin)
        body = resp.json()
        record("HTTP-status含review", resp.status_code == 200
               and body["review"]["annotated"] == 1
               and body["review"]["fed"] == 1,
               str(body.get("review")))
        record("HTTP-status三权重视图",
               set(body.get("weights", {}).keys())
               == {"ride_dispatch", "driver_application_gate",
                   "ride_review"}, str(body.get("weights", {}).keys()))

        # learning run 三档案
        resp = client.post("/api/ride/admin/learning/run", headers=admin)
        body = resp.json()
        record("HTTP-run三档案", resp.status_code == 200
               and set(body.get("results", {}).keys())
               == {"ride_dispatch", "driver_application_gate",
                   "ride_review"}, str(body.get("results", {}).keys()))

        # ROI 端点
        resp = client.get("/api/ride/admin/roi", headers=admin)
        body = resp.json()
        record("HTTP-ROI", resp.status_code == 200
               and body["totalGranted"] >= 1
               and "usedRate" in body and "repeatRate" in body,
               str(body)[:150])
        resp = client.get("/api/ride/admin/roi")
        record("HTTP-ROI非admin403", resp.status_code == 403)


async def main():
    test_classes = [
        ("真值标注", TestAnnotate),
        ("评价回流", TestReviewFeedback),
        ("调度器接线", TestScheduler),
        ("营销ROI", TestRoi),
        ("HTTP层", TestHttpRoutes),
    ]
    print("=" * 62)
    print("41号·AI智能代驾模块 P5 专项测试(真值标注回流+营销ROI)")
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
