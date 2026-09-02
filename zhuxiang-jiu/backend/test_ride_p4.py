"""41号·AI智能代驾模块 P4 专项测试(日结对账 + Hedge 学习回流)

运行方式:
    python test_ride_p4.py

覆盖(设计文档 §2.5 对账 / §2.6 学习回流):
    - 对账主链(镜像零差异 → reconciling → confirmed → paid)
    - 差异分支(注入平台账单 → 四类差异 → diff → investigating
      → resolved → confirmed)
    - 对账门槛(自营轨道拒绝/重复单号 409/不存在 404)
    - 学习回流-派单(settled+评价 → expectedAction 真值映射/幂等/
      无评价 skip/平台直发 skip)
    - 学习回流-审查(approved 司机服务数据 → correct/rating 回流/
      幂等/无服务数据 skip)
    - DECISION_THRESHOLDS 三档案配置
    - HTTP 层(reconciliation 6 端点 + learning 3 端点)
"""

import asyncio
import os
import sys
from datetime import datetime, UTC

# 必须在 import 业务模块之前设置(对齐 41号 P0-P3 测试惯例)
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
FAR = {"lat": 36.29, "lng": 117.13, "address": "郊区"}
TODAY = datetime.now(UTC).strftime("%Y-%m-%d")


async def grant_coupon(member_id=1, order_id="ORD", amount=800.0):
    from services.ride_coupon_service import RideCouponService
    return await RideCouponService().grant_for_order(
        member_id, order_id, amount)


async def settle_partner_ride(svc, member_id=1, km=11.0, hour=14):
    """跑一个平台直发 settled 行程, 返回 ride"""
    await grant_coupon(member_id, f"P4P{km}{member_id}", 800.0)
    r = await svc.call(member_id, FAR, CENTER, distance_km=km)
    po = r["driverSnapshot"]["partnerOrderId"]
    await svc.partner_callback({"partnerOrderId": po,
                                "event": "started"})
    r = await svc.partner_callback({"partnerOrderId": po,
                                    "event": "completed",
                                    "trace": {"actualKm": km,
                                              "durationMinutes": 0}})
    return r["ride"]


class TestReconciliation:
    async def run(self):
        from services.ride_dispatch_service import RideDispatchService
        from services.ride_reconcile_service import (
            RideReconcileService, gen_recon_no,
        )

        svc = RideDispatchService()
        recon_svc = RideReconcileService()

        # 前置: 两个平台直发 settled 行程(当日)
        ride1 = await settle_partner_ride(svc)
        ride2 = await settle_partner_ride(svc, km=12.0)
        record("对账-前置两单settled", ride1["status"] == "settled"
               and ride2["status"] == "settled")

        # ① 镜像生成(零差异) → reconciling 主链
        recon = await recon_svc.generate(TODAY, "platform")
        record("对账-镜像生成reconciling",
               recon["status"] == "reconciling", str(recon["status"]))
        record("对账-单号格式",
               recon["reconNo"] == gen_recon_no(TODAY, "platform"),
               recon["reconNo"])
        record("对账-订单数2", recon["totalOrders"] == 2,
               str(recon["totalOrders"]))
        record("对账-总额一致", recon["siteTotal"]
               == recon["channelTotal"] > 0,
               f"{recon['siteTotal']} vs {recon['channelTotal']}")
        record("对账-零差异", recon["diffCount"] == 0,
               str(recon["diffCount"]))

        # 重复生成 → 409
        try:
            await recon_svc.generate(TODAY, "platform")
            record("对账-重复单号409", False, "未抛出")
        except ValueError:
            record("对账-重复单号409", True)

        # 主链: confirm → paid
        recon = await recon_svc.confirm(recon["reconNo"])
        record("对账-confirm", recon["status"] == "confirmed")
        recon = await recon_svc.pay(recon["reconNo"])
        record("对账-pay终态", recon["status"] == "paid"
               and recon.get("payAt"))
        # paid 后不可再 confirm
        try:
            await recon_svc.confirm(recon["reconNo"])
            record("对账-终态后流转拒绝", False, "未抛出")
        except ValueError:
            record("对账-终态后流转拒绝", True)

        # ② 造一单加盟(partner)行程 → 注入差异账单 → diff 分支
        # (王/李置 offline → 派单选中加盟陈师傅)
        for did in (1, 2):
            d = await svc.repo.get_driver(did)
            d["status"] = "offline"
            await svc.repo.save_driver(d)
        await grant_coupon(1, "P4PSELF", 800.0)
        r = await svc.call(1, NEAR, CENTER, distance_km=8.0)
        record("对账-加盟派单", r["driverSnapshot"]["track"] == "partner",
               str(r["driverSnapshot"].get("track")))
        ride_p = await svc.repo.get_ride(r["rideId"])
        ride_p["status"] = "trip_started"
        ride_p["startedAt"] = ride_p["requestedAt"]
        ride_p["completedAt"] = ride_p["requestedAt"]
        await svc.repo.save_ride(ride_p)
        driver_p = await svc.repo.get_driver(r["driverSnapshot"]
                                            ["driverId"])
        await svc._settle(ride_p, driver=driver_p,
                          duration_minutes=0, pricing_hour=14)

        channel_bills = [
            # amount_mismatch(本站 50, 平台记 45)
            {"rideId": ride_p["rideId"], "totalAmount": 45.0},
            # extra_order(本站无)
            {"rideId": "RD99999999", "totalAmount": 88.0},
        ]
        recon = await recon_svc.generate(TODAY, "partner",
                                         channel_bills=channel_bills)
        types = sorted(d["type"] for d in recon["diffDetails"])
        record("对账-差异生成diff", recon["status"] == "diff"
               and recon["diffCount"] == 2, str(types))
        record("对账-金额不符差异",
               "amount_mismatch" in types, str(types))
        record("对账-多余单据差异", "extra_order" in types, str(types))

        # 差异分支: investigate → resolve → confirm
        recon = await recon_svc.investigate(recon["reconNo"],
                                            investigator="admin")
        record("对账-investigate", recon["status"] == "investigating"
               and recon.get("investigator") == "admin")
        # 非法流转: investigating 不可直接 confirm
        try:
            await recon_svc.confirm(recon["reconNo"])
            record("对账-调查中confirm拒绝", False, "未抛出")
        except ValueError:
            record("对账-调查中confirm拒绝", True)
        recon = await recon_svc.resolve(recon["reconNo"],
                                        resolution="平台补单+多记调整")
        record("对账-resolve留痕", recon["status"] == "resolved"
               and "补单" in recon.get("resolution", ""))
        recon = await recon_svc.confirm(recon["reconNo"])
        record("对账-resolved后confirm", recon["status"] == "confirmed")

        # 自营轨道 → 409
        try:
            await recon_svc.generate(TODAY, "self")
            record("对账-自营轨道拒绝", False, "未抛出")
        except ValueError:
            record("对账-自营轨道拒绝", True)

        # 不存在 → 404
        try:
            await recon_svc.get("RCN20990101partner")
            record("对账-不存在404", False, "未抛出")
        except KeyError:
            record("对账-不存在404", True)


class TestCouponDiff:
    async def run(self):
        from services.ride_dispatch_service import RideDispatchService
        from services.ride_reconcile_service import RideReconcileService

        svc = RideDispatchService()
        recon_svc = RideReconcileService()
        # 造券未核销差异: 发券 → 造一个 settlement 引用该券但券
        # 状态被外力改回 granted
        ride = await settle_partner_ride(svc)
        code = ride["couponCode"]
        # 取该行程结算单
        settlement = await svc.repo.get_settlement_by_ride(ride["rideId"])
        # 结算单没存 couponCode → 服务层差异检测用 deduction 判断
        settlement["couponCode"] = code
        settlement["couponDeduction"] = 60.0
        await svc.repo.save_settlement(settlement)
        # 外力改券为 granted(模拟核销丢失)
        coupon = await svc.repo.get_coupon(code)
        coupon["status"] = "granted"
        await svc.repo.save_coupon(coupon)

        recon = await recon_svc.generate(TODAY, "platform")
        types = [d["type"] for d in recon["diffDetails"]]
        record("券差异-coupon_unredeemed",
               "coupon_unredeemed" in types, str(types))
        record("券差异-状态diff", recon["status"] == "diff")


class TestDispatchLearning:
    async def run(self):
        from services.ride_dispatch_service import RideDispatchService
        from services.ride_review_service import RideReviewService
        from repositories.ride_repository import REVIEW_BY_PASSENGER

        svc = RideDispatchService()
        review_svc = RideReviewService()

        # AI 派单 settled 行程 + 5 星评价 → 回流 expected=dispatch
        await grant_coupon(1, "L1", 800.0)
        r = await svc.call(1, NEAR, CENTER, distance_km=8.0)
        ride_id = r["rideId"]
        record("回流-派单快照存档", len(
            (r.get("dispatchScoring") or {}).get("factors") or []) == 5,
            str((r.get("dispatchScoring") or {}).get("factors")))
        ride = await svc.repo.get_ride(ride_id)
        ride["status"] = "trip_started"
        ride["startedAt"] = ride["requestedAt"]
        ride["completedAt"] = ride["requestedAt"]
        await svc.repo.save_ride(ride)
        driver = await svc.repo.get_driver(r["driverSnapshot"]["driverId"])
        await svc._settle(ride, driver=driver,
                          duration_minutes=0, pricing_hour=14)
        await review_svc.submit(1, ride_id, REVIEW_BY_PASSENGER,
                                5, "非常好")

        result = await svc.collect_learning_feedback()
        record("回流-5星提交1条", result["submitted"] == 1
               and result["skipped"] == 0, str(result))

        # 幂等: 重跑 skip
        result = await svc.collect_learning_feedback()
        record("回流-幂等skip", result["submitted"] == 0
               and result["skipped"] >= 1, str(result))

        # 反馈入库校验(expectedAction 口径)
        from repositories.ai_learning_repository import AiLearningRepository
        feedbacks = await AiLearningRepository().list_feedback(
            "ride_dispatch", limit=10)
        record("回流-反馈入库", len(feedbacks) >= 1, str(len(feedbacks)))
        fb = feedbacks[-1] if feedbacks else {}
        record("回流-expectedAction=dispatch",
               fb.get("expectedAction") == "dispatch"
               and fb.get("actualAction") == "dispatch",
               str(fb.get("expectedAction")))
        record("回流-correct标记", fb.get("correct") is True)
        record("回流-reward连续", fb.get("reward") == 1.0,
               str(fb.get("reward")))

        # 1 星差评行程 → expected=escalate(不匹配, correct=False)
        await grant_coupon(1, "L2", 800.0)
        r = await svc.call(1, NEAR, CENTER, distance_km=8.0)
        ride = await svc.repo.get_ride(r["rideId"])
        ride["status"] = "trip_started"
        ride["startedAt"] = ride["requestedAt"]
        ride["completedAt"] = ride["requestedAt"]
        await svc.repo.save_ride(ride)
        driver = await svc.repo.get_driver(r["driverSnapshot"]["driverId"])
        await svc._settle(ride, driver=driver,
                          duration_minutes=0, pricing_hour=14)
        await review_svc.submit(1, r["rideId"], REVIEW_BY_PASSENGER,
                                1, "一般般不太行")
        await svc.collect_learning_feedback()
        feedbacks = await AiLearningRepository().list_feedback(
            "ride_dispatch", limit=10)
        fb_last = feedbacks[-1]
        record("回流-1星expected=escalate",
               fb_last.get("expectedAction") == "escalate"
               and fb_last.get("correct") is False,
               str(fb_last.get("expectedAction")))

        # 无评价行程 → skip(无真值)
        await grant_coupon(1, "L3", 800.0)
        r = await svc.call(1, NEAR, CENTER, distance_km=8.0)
        ride = await svc.repo.get_ride(r["rideId"])
        ride["status"] = "trip_started"
        ride["startedAt"] = ride["requestedAt"]
        ride["completedAt"] = ride["requestedAt"]
        await svc.repo.save_ride(ride)
        driver = await svc.repo.get_driver(r["driverSnapshot"]["driverId"])
        await svc._settle(ride, driver=driver,
                          duration_minutes=0, pricing_hour=14)
        before = len(await AiLearningRepository().list_feedback(
            "ride_dispatch", limit=100))
        result = await svc.collect_learning_feedback()
        after = len(await AiLearningRepository().list_feedback(
            "ride_dispatch", limit=100))
        record("回流-无评价skip", after - before == 0,
               f"{before}→{after}")

        # 学习(反馈不足 → ValueError; 足量则跑通)
        try:
            learned = await svc.run_learning()
            record("学习-Hedge一轮", learned.get("success") is True,
                   str(learned.get("detail", ""))[:120])
        except ValueError:
            record("学习-Hedge一轮", True, "反馈不足跳过(阈值内)")


class TestGateLearning:
    async def run(self):
        from repositories.member_repository import MemberRepository
        from services.driver_gate_service import DriverGateService
        from repositories.ai_learning_repository import AiLearningRepository

        # 造 L5 会员司机(审查通过)
        await MemberRepository().update_fields(1, {"level": 5})
        gate = DriverGateService()
        r = await gate.apply(1, {
            "idNumber": "370900199001010011",
            "licenseNumber": "370900123456",
            "licenseClass": "C1", "drivingYears": 8,
            "accidentFreeDecl": True, "drunkFreeDecl": True,
            "emergencyContact": "王紧急", "bambooScore": 800})
        driver_id = r["driverId"]
        app_id = r["applicationId"]

        # 无服务数据 → skip
        result = await gate.collect_application_feedback()
        record("审查回流-无完单skip", result["submitted"] == 0
               and result["skipped"] >= 1, str(result))

        # 造服务数据: 完单 +1(评分 5.0) → correct
        driver = await gate.repo.get_driver(driver_id)
        driver["completedOrders"] = 10
        await gate.repo.save_driver(driver)
        result = await gate.collect_application_feedback()
        record("审查回流-提交1条", result["submitted"] == 1,
               str(result))
        feedbacks = await AiLearningRepository().list_feedback(
            "driver_application_gate", limit=10)
        fb = feedbacks[-1] if feedbacks else {}
        record("审查回流-correct(评分5)",
               fb.get("correct") is True
               and fb.get("expectedAction") == "approved",
               str(fb.get("expectedAction")))

        # 幂等
        result = await gate.collect_application_feedback()
        record("审查回流-幂等", result["submitted"] == 0,
               str(result))

        # 服务恶化(评分降到 3.5 + suspended)→ 无新回流因已 fed;
        # 造第二个司机验证误放行口径
        await MemberRepository().create({
            "phone": "13800000003", "password": "test123456",
            "nickname": "司机乙", "level": 5, "growth_value": 9999,
            "points": 100, "status": 1, "role": "member",
            "ageConfirmed": True, "birthdate": "1992-05-05",
            "ageVerified": True,
            "created_at": "2026-08-21T00:00:00+00:00"})
        members = await MemberRepository().list_all()
        m3 = max(int(m["id"]) for m in members)
        r = await gate.apply(m3, {
            "idNumber": "370900199001010022",
            "licenseNumber": "370900654321",
            "licenseClass": "C1", "drivingYears": 8,
            "accidentFreeDecl": True, "drunkFreeDecl": True,
            "emergencyContact": "李紧急", "bambooScore": 800})
        driver2 = await gate.repo.get_driver(r["driverId"])
        driver2["completedOrders"] = 5
        driver2["rating"] = 3.2      # 低分
        await gate.repo.save_driver(driver2)
        result = await gate.collect_application_feedback()
        record("审查回流-低分提交", result["submitted"] == 1,
               str(result))
        feedbacks = await AiLearningRepository().list_feedback(
            "driver_application_gate", limit=10)
        fb = feedbacks[-1]
        record("审查回流-误放行correct=False",
               fb.get("correct") is False
               and fb.get("expectedAction") == "rejected",
               str(fb.get("expectedAction")))


class TestThresholds:
    async def run(self):
        from services.ai_learning_service import DECISION_THRESHOLDS

        record("阈值-三档案配置",
               "driver_application_gate" in DECISION_THRESHOLDS
               and "ride_dispatch" in DECISION_THRESHOLDS
               and "ride_review" in DECISION_THRESHOLDS)
        record("阈值-审查档位",
               DECISION_THRESHOLDS["driver_application_gate"]
               == [(70.0, "approved"), (50.0, "manual_review"),
                   (0.0, "rejected")])
        record("阈值-派单档位",
               DECISION_THRESHOLDS["ride_dispatch"]
               == [(70.0, "dispatch"), (50.0, "dispatch_backup"),
                   (0.0, "escalate")])
        record("阈值-评价档位",
               DECISION_THRESHOLDS["ride_review"]
               == [(45.0, "fold"), (30.0, "watch"), (0.0, "show")])


class TestHttpRoutes:
    async def run(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from routes.ride_routes import register_ride_routes
        from services.ride_dispatch_service import RideDispatchService

        app = FastAPI()
        register_ride_routes(app)
        client = TestClient(app)
        admin = {"X-Role": "admin"}

        # 前置: 一单平台直发 settled
        svc = RideDispatchService()
        await settle_partner_ride(svc)

        # 生成对账单
        resp = client.post("/api/ride/admin/reconciliation/start",
                           headers=admin, json={"period": TODAY,
                                               "track": "platform"})
        body = resp.json()
        record("HTTP-对账生成", resp.status_code == 200
               and body.get("status") == "reconciling",
               str(body)[:150])
        recon_no = body.get("reconNo")

        # 详情/列表
        resp = client.get(f"/api/ride/admin/reconciliation/{recon_no}",
                          headers=admin)
        record("HTTP-对账详情", resp.status_code == 200
               and resp.json()["reconciliation"]["reconNo"] == recon_no)
        resp = client.get("/api/ride/admin/reconciliations",
                          headers=admin,
                          params={"status": "reconciling"})
        record("HTTP-对账列表过滤", resp.status_code == 200
               and resp.json()["total"] >= 1)

        # 状态机: confirm → pay
        resp = client.post(
            f"/api/ride/admin/reconciliation/{recon_no}/confirm",
            headers=admin)
        record("HTTP-对账confirm", resp.status_code == 200
               and resp.json()["reconciliation"]["status"] == "confirmed")
        resp = client.post(
            f"/api/ride/admin/reconciliation/{recon_no}/pay",
            headers=admin)
        record("HTTP-对账pay", resp.status_code == 200
               and resp.json()["reconciliation"]["status"] == "paid")

        # 自营轨道 409 / 不存在 404 / 鉴权 403
        resp = client.post("/api/ride/admin/reconciliation/start",
                           headers=admin, json={"period": TODAY,
                                                "track": "self"})
        record("HTTP-自营轨道409", resp.status_code == 409,
               str(resp.status_code))
        resp = client.post(
            "/api/ride/admin/reconciliation/RCN20990101platform/investigate",
            headers=admin)
        record("HTTP-对账不存在404", resp.status_code == 404,
               str(resp.status_code))
        resp = client.post("/api/ride/admin/reconciliation/start",
                           json={"period": TODAY, "track": "platform"})
        record("HTTP-对账非admin403", resp.status_code == 403,
               str(resp.status_code))

        # learning 端点
        resp = client.post("/api/ride/admin/learning/collect",
                           headers=admin)
        body = resp.json()
        record("HTTP-学习批量回流", resp.status_code == 200
               and "dispatch" in body and "gate" in body,
               str(body)[:150])
        resp = client.post("/api/ride/admin/learning/run", headers=admin)
        record("HTTP-学习触发", resp.status_code == 200
               and "ride_dispatch" in resp.json().get("results", {}),
               str(resp.json())[:150])
        resp = client.get("/api/ride/admin/learning/status", headers=admin)
        body = resp.json()
        record("HTTP-学习状态", resp.status_code == 200
               and body["dispatch"]["settled"] >= 1
               and "weights" in body, str(body.get("dispatch")))
        resp = client.post("/api/ride/admin/learning/collect")
        record("HTTP-学习非admin403", resp.status_code == 403)


async def main():
    test_classes = [
        ("日结对账", TestReconciliation),
        ("券核销差异", TestCouponDiff),
        ("派单学习回流", TestDispatchLearning),
        ("审查学习回流", TestGateLearning),
        ("阈值表配置", TestThresholds),
        ("HTTP层", TestHttpRoutes),
    ]
    print("=" * 62)
    print("41号·AI智能代驾模块 P4 专项测试(日结对账+学习回流)")
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
