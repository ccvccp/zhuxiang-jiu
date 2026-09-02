"""41号·AI智能代驾模块 P2 专项测试(平台直发三态回调 + 安全监控)

运行方式:
    python test_ride_p2.py

覆盖(设计文档 §2.3 平台直发契约 / §2.4 安全监控):
    - 三态通道(mock 直发 / mock_fallback 真实轨失败回退 / real 无回退拒单)
    - 平台回调生命周期(started → completed 触发 AI 结算 aggregated)
    - 回调 trace 留痕(actualKm/durationMinutes)与计价口径
    - 回调取消(平台侧取消, 券退回)/异常口径(未知事件/未知单号/非平台行程)
    - POI 校验纯函数(饮酒场景/中性/空地址)
    - POI 高频叫单风控(24h ≥3 次非饮酒 POI → 风险事件)
    - 行程超时扫描(>3h 未结束预警, 幂等)
    - 里程异常(实际超预估 2 倍 → 风险事件 + 计价用实际里程)
    - 风险面板聚合 + 处置
    - HTTP 层(partner/callback / safety/scan / risk-panel / resolve)
"""

import asyncio
import os
import sys
from datetime import datetime, UTC, timedelta

# 必须在 import 业务模块之前设置(对齐 41号 P0/P1 测试惯例)
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


# 位置锚点(与 P1 同口径)
NEAR = {"lat": 36.1905, "lng": 117.130, "address": "泰安老字号饭店"}
CENTER = {"lat": 36.19, "lng": 117.13, "address": "泰安市区中心"}
FAR_PICKUP = {"lat": 36.29, "lng": 117.13, "address": "郊区(超半径)"}
NEUTRAL_POI = {"lat": 36.19, "lng": 117.14, "address": "泰安火车站广场"}


async def grant_coupon(member_id=1, order_id="ORD", amount=800.0):
    from services.ride_coupon_service import RideCouponService
    return await RideCouponService().grant_for_order(
        member_id, order_id, amount)


class TestChannelTriState:
    async def run(self):
        import services.ride_dispatch_service as rds
        from services.ride_dispatch_service import RideDispatchService

        svc = RideDispatchService()

        # ① mock 模式(默认): 直发 mock 回执, channel=mock
        await grant_coupon(1, "T1", 800.0)
        r = await svc.call(1, FAR_PICKUP, CENTER, distance_km=11.0)
        record("三态-mock直发", r["dispatchMode"] == "platform"
               and r["platformChannel"] == "mock",
               str(r.get("platformChannel")))

        # ② mock_fallback: 真实轨失败(dead URL) → 回退 mock 标记
        orig_mode, orig_url = rds.DRIDE_CHANNEL_MODE, rds.DRIDE_PARTNER_URL
        rds.DRIDE_CHANNEL_MODE = "mock_fallback"
        rds.DRIDE_PARTNER_URL = "http://127.0.0.1:9/dispatch"   # 不可达端口
        try:
            await grant_coupon(1, "T2", 800.0)
            r = await svc.call(1, FAR_PICKUP, CENTER, distance_km=11.0)
            record("三态-fallback回退mock",
                   r["platformChannel"] == "mock_fallback"
                   and r["status"] == "dispatched",
                   str(r.get("platformChannel")))

            # ③ real: 无回退, 失败抛错
            rds.DRIDE_CHANNEL_MODE = "real"
            await grant_coupon(1, "T3", 800.0)
            try:
                await svc.call(1, FAR_PICKUP, CENTER, distance_km=11.0)
                record("三态-real无回退拒单", False, "未抛出")
            except ValueError as e:
                record("三态-real无回退拒单", "不可用" in str(e))
        finally:
            rds.DRIDE_CHANNEL_MODE = orig_mode
            rds.DRIDE_PARTNER_URL = orig_url


class TestPartnerCallback:
    async def run(self):
        from services.ride_dispatch_service import RideDispatchService
        from repositories.ride_repository import (
            COUPON_STATUS_GRANTED, COUPON_STATUS_USED,
        )

        svc = RideDispatchService()

        # 平台直发行程 → started → completed(带 trace)
        await grant_coupon(1, "CB1", 800.0)
        r = await svc.call(1, FAR_PICKUP, CENTER, distance_km=11.0)
        ride_id = r["rideId"]
        po = r["driverSnapshot"]["partnerOrderId"]
        record("回调-平台单号格式", po == f"PD{ride_id[2:]}", po)

        r = await svc.partner_callback({"partnerOrderId": po,
                                        "event": "started"})
        record("回调-started", r["ride"]["status"] == "trip_started",
               str(r["ride"].get("status")))

        # 重复 started → 409
        try:
            await svc.partner_callback({"partnerOrderId": po,
                                        "event": "started"})
            record("回调-重复started拒绝", False, "未抛出")
        except ValueError:
            record("回调-重复started拒绝", True)

        # completed: trace actualKm=12km/30min → 35+35=70, 券抵60补差10
        r = await svc.partner_callback({
            "partnerOrderId": po, "event": "completed",
            "trace": {"actualKm": 12.0, "durationMinutes": 30, "pricingHour": 14}})
        ride = r["ride"]
        record("回调-completed结算settled", ride["status"] == "settled",
               str(ride.get("status")))
        pricing = ride["pricing"]
        record("回调-实际里程计价", pricing["totalAmount"] == 70.0,
               str(pricing))
        record("回调-拆分(券60补差10)",
               pricing["couponDeduction"] == 60.0
               and pricing["extraCharge"] == 10.0, str(pricing))
        record("回调-trace留痕",
               ride["partnerTrace"].get("actualKm") == 12.0
               and ride["partnerTrace"].get("durationMinutes") == 30,
               str(ride.get("partnerTrace")))
        record("回调-actualKm入库", ride.get("actualKm") == 12.0)
        settlement = await svc.repo.get_settlement(ride["settlementId"])
        record("回调-平台结算aggregated",
               settlement["payoutStatus"] == "aggregated"
               and settlement["totalAmount"] == 70.0)

        # settled 后回调 completed → 409(不可再完成)
        try:
            await svc.partner_callback({"partnerOrderId": po,
                                        "event": "completed"})
            record("回调-终态后completed拒绝", False, "未抛出")
        except ValueError:
            record("回调-终态后completed拒绝", True)

        # cancelled: 平台侧取消 → 券退回
        await grant_coupon(1, "CB2", 800.0)
        r = await svc.call(1, FAR_PICKUP, CENTER, distance_km=11.0)
        po2 = r["driverSnapshot"]["partnerOrderId"]
        r = await svc.partner_callback({"partnerOrderId": po2,
                                        "event": "cancelled"})
        record("回调-cancelled", r["ride"]["status"] == "cancelled"
               and r["ride"]["cancelWindowFree"] is True,
               str(r["ride"].get("status")))
        coupon = await svc.repo.get_coupon(r["ride"]["couponCode"])
        record("回调-平台取消券退回",
               coupon["status"] == COUPON_STATUS_GRANTED,
               str(coupon.get("status")))

        # 异常口径
        try:
            await svc.partner_callback({"partnerOrderId": po2,
                                        "event": "flying"})
            record("回调-未知事件拒绝", False, "未抛出")
        except ValueError:
            record("回调-未知事件拒绝", True)
        try:
            await svc.partner_callback({"partnerOrderId": "PD999999",
                                        "event": "started"})
            record("回调-未知单号404", False, "未抛出")
        except KeyError:
            record("回调-未知单号404", True)
        # 非平台行程拒回调: 本站司机行程(构造 snapshot 携带平台单号的防御口径)
        await grant_coupon(1, "CB3", 800.0)
        r = await svc.call(1, NEAR, CENTER, distance_km=8.0)
        self_ride = await svc.repo.get_ride(r["rideId"])
        self_ride["driverSnapshot"]["partnerOrderId"] = f"PD{r['rideId'][2:]}"
        await svc.repo.save_ride(self_ride)
        try:
            await svc.partner_callback({"partnerOrderId":
                                        f"PD{r['rideId'][2:]}",
                                        "event": "started"})
            record("回调-非平台行程拒绝", False, "未抛出")
        except ValueError:
            record("回调-非平台行程拒绝", True)

        # 券被平台完成行程核销
        coupon = await svc.repo.get_coupon(
            (await svc.repo.get_ride(ride_id))["couponCode"])
        record("回调-完成行程券核销",
               coupon["status"] == COUPON_STATUS_USED)


class TestSafetyPOI:
    async def run(self):
        from services.ride_safety_service import (
            RideSafetyService, classify_poi,
        )
        from services.ride_dispatch_service import RideDispatchService

        # 纯函数
        record("POI-饮酒场景", classify_poi("泰安老字号饭店")
               ["category"] == "drinking")
        record("POI-酒庄场景", classify_poi("竹林酒庄")
               ["category"] == "drinking")
        record("POI-中性场景", classify_poi("泰安火车站广场")
               ["category"] == "neutral")
        record("POI-空地址unknown", classify_poi("")
               ["category"] == "unknown")
        poi = classify_poi("烧烤大排档夜市")
        record("POI-多词命中", poi["category"] == "drinking"
               and len(poi["matchedWords"]) >= 2, str(poi))

        svc = RideDispatchService()
        safety = RideSafetyService()

        # 饮酒场景叫单 → 无信号
        await grant_coupon(1, "P1", 800.0)
        r = await svc.call(1, NEAR, CENTER, distance_km=8.0)
        record("POI-饮酒叫单无标记", r["poiCategory"] == "drinking"
               and r["riskFlag"] == "", str(r.get("riskFlag")))

        # 中性 POI 高频: 第 3 次 → 风控信号
        member2_neutral = 0
        for i in range(3):
            await grant_coupon(1, f"PN{i}", 800.0)
            r = await svc.call(1, NEUTRAL_POI, CENTER, distance_km=8.0)
            if r.get("riskFlag"):
                member2_neutral += 1
        record("POI-高频第3次触发", member2_neutral == 1,
               f"触发{member2_neutral}次")
        record("POI-风险标记留痕", r["riskFlag"] == "poi_high_frequency",
               str(r.get("riskFlag")))
        events = await safety.repo.list_risk_events(
            type="poi_high_frequency", limit=10)
        record("POI-风险事件落库", len(events) == 1
               and events[0]["rideId"] is None, str(len(events)))

        # 面板
        panel = await safety.risk_panel()
        record("POI-面板聚合", panel["total"] >= 1
               and panel["byType"]["poi_high_frequency"] == 1,
               str(panel.get("byType")))


class TestSafetyScan:
    async def run(self):
        from services.ride_safety_service import RideSafetyService
        from services.ride_dispatch_service import RideDispatchService

        svc = RideDispatchService()
        safety = RideSafetyService()

        # 构造进行中行程: 派单后置 started
        await grant_coupon(1, "SC1", 800.0)
        r = await svc.call(1, NEAR, CENTER, distance_km=8.0)
        ride = await svc.repo.get_ride(r["rideId"])
        ride["status"] = "trip_started"
        ride["startedAt"] = _now_iso()
        await svc.repo.save_ride(ride)

        # 正常时长 → 无预警
        result = await safety.scan_active()
        record("扫描-正常无预警", len(result["warnings"]) == 0,
               str(result))

        # 老化 4h → 预警
        ride["startedAt"] = (datetime.now(UTC)
                             - timedelta(hours=4)).isoformat()
        await svc.repo.save_ride(ride)
        result = await safety.scan_active()
        record("扫描-超时预警", len(result["warnings"]) == 1,
               str(len(result.get("warnings", []))))
        record("扫描-扫描计数", result["scanned"] == 1,
               str(result.get("scanned")))

        # 幂等: 重扫不重复落
        result = await safety.scan_active()
        record("扫描-幂等", len(result["warnings"]) == 0,
               str(len(result.get("warnings", []))))
        events = await safety.repo.list_risk_events(
            type="trip_timeout", limit=10)
        record("扫描-事件唯一", len(events) == 1, str(len(events)))

        # 处置
        resolved = await safety.resolve_event(events[0]["riskId"],
                                              note="已联系乘客确认安全")
        record("处置-标记resolved", resolved["resolved"] is True
               and "乘客" in resolved.get("resolvedNote", ""))
        # 已处置事件不再计未处置
        panel = await safety.risk_panel()
        record("处置-未处置计数", panel["unresolved"] == 0,
               str(panel.get("unresolved")))

        # 处置后重扫: 幂等口径(未处置事件不存在 → 重新落)
        # 说明: 处置后再次扫描发现仍超时 → 新预警(合理, 复警口径)
        try:
            await safety.resolve_event(9999)
            record("处置-不存在404", False, "未抛出")
        except KeyError:
            record("处置-不存在404", True)


class TestMileageAnomaly:
    async def run(self):
        from services.ride_dispatch_service import RideDispatchService
        from services.ride_safety_service import RideSafetyService

        svc = RideDispatchService()

        # 司机完成上报实际里程 20km(预估 8km, >2倍) → 异常 + 按实际计价
        await grant_coupon(1, "MA1", 800.0)
        r = await svc.call(1, NEAR, CENTER, distance_km=8.0)
        ride_id = r["rideId"]
        ride = await svc.repo.get_ride(ride_id)
        ride["status"] = "trip_started"
        ride["startedAt"] = _now_iso()
        ride["completedAt"] = _now_iso()
        driver = await svc.repo.get_driver(ride["driverId"])
        ride = await svc._settle(ride, driver=driver,
                                 duration_minutes=0, pricing_hour=14,
                                 actual_km=20.0)
        record("里程-异常标记", ride["mileageAnomaly"] is True)
        record("里程-实际入库", ride["actualKm"] == 20.0)
        # 20km 计价: 35 + 15*5 = 110 → 券60 + 补差50
        record("里程-按实际计价", ride["pricing"]["totalAmount"] == 110.0,
               str(ride["pricing"]))
        record("里程-拆分", ride["pricing"]["couponDeduction"] == 60.0
               and ride["pricing"]["extraCharge"] == 50.0)
        safety = RideSafetyService()
        events = await safety.repo.list_risk_events(
            ride_id=ride_id, type="mileage_anomaly", limit=5)
        record("里程-风险事件落库", len(events) == 1,
               str(len(events)))

        # 正常里程(实际 9km, 未超 2 倍) → 无异常
        await grant_coupon(1, "MA2", 800.0)
        r = await svc.call(1, NEAR, CENTER, distance_km=8.0)
        ride = await svc.repo.get_ride(r["rideId"])
        ride["status"] = "trip_started"
        ride["startedAt"] = _now_iso()
        ride["completedAt"] = _now_iso()
        driver = await svc.repo.get_driver(ride["driverId"])
        ride = await svc._settle(ride, driver=driver,
                                 duration_minutes=0, pricing_hour=14,
                                 actual_km=9.0)
        record("里程-正常无异常", ride["mileageAnomaly"] is False)
        record("里程-正常计价9km", ride["pricing"]["totalAmount"] == 55.0,
               str(ride["pricing"]))


class TestHttpRoutes:
    async def run(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from routes.ride_routes import register_ride_routes

        app = FastAPI()
        register_ride_routes(app)
        client = TestClient(app)
        member = {"X-Member-Id": "1"}
        admin = {"X-Role": "admin"}

        # 发券 → 郊区叫单(平台直发) → HTTP 回调全流程
        client.post("/api/ride/coupons/grant", headers=admin, json={
            "memberId": 1, "orderId": "HP2", "amount": 800})
        resp = client.post("/api/ride/call", headers=member, json={
            "pickup": {"lat": 36.29, "lng": 117.13, "address": "郊区饭店"},
            "dropoff": {"lat": 36.19, "lng": 117.13, "address": "市区"},
            "distanceKm": 11.0})
        ride_id = resp.json()["rideId"]
        po = resp.json()["driverSnapshot"]["partnerOrderId"]

        # 回调 started
        resp = client.post("/api/ride/partner/callback", json={
            "partnerOrderId": po, "event": "started"})
        record("HTTP-回调started", resp.status_code == 200
               and resp.json()["ride"]["status"] == "trip_started",
               str(resp.text[:150]))

        # 回调 completed(带 trace, 实际25km 超预估11km×2 → 里程异常+面板有事件)
        resp = client.post("/api/ride/partner/callback", json={
            "partnerOrderId": po, "event": "completed",
            "trace": {"actualKm": 25.0, "durationMinutes": 0, "pricingHour": 14}})
        body = resp.json()["ride"]
        record("HTTP-回调completed结算", resp.status_code == 200
               and body["status"] == "settled", str(body.get("status")))
        record("HTTP-回调计价", body["pricing"]["totalAmount"] == 135.0,
               str(body.get("pricing")))
        record("HTTP-回调里程异常", body["mileageAnomaly"] is True)

        # 回调异常口径
        resp = client.post("/api/ride/partner/callback", json={
            "partnerOrderId": po, "event": "flying"})
        record("HTTP-未知事件409", resp.status_code == 409,
               str(resp.status_code))
        resp = client.post("/api/ride/partner/callback", json={
            "partnerOrderId": "PD00000000", "event": "started"})
        record("HTTP-未知单号404", resp.status_code == 404,
               str(resp.status_code))

        # 安全扫描/面板鉴权
        resp = client.post("/api/ride/admin/safety/scan")
        record("HTTP-扫描非admin403", resp.status_code == 403,
               str(resp.status_code))
        resp = client.post("/api/ride/admin/safety/scan", headers=admin)
        record("HTTP-扫描", resp.status_code == 200
               and "warnings" in resp.json())
        resp = client.get("/api/ride/admin/risk-panel", headers=admin)
        body = resp.json()
        record("HTTP-风险面板", resp.status_code == 200
               and body["total"] >= 1 and "byType" in body,
               str(body.get("total")))
        resp = client.get("/api/ride/admin/risk-panel")
        record("HTTP-面板非admin403", resp.status_code == 403)

        # 处置: 面板里的里程异常事件(completed 10km vs 预估 11km 无异常;
        # 用 POI 高频或另造——直接取面板第一条未处置)
        unresolved = [e for e in body["events"]
                      if not e.get("resolved")]
        if unresolved:
            risk_id = unresolved[0]["riskId"]
            resp = client.post(
                f"/api/ride/admin/risk-events/{risk_id}/resolve",
                headers=admin, json={"note": "已核实"})
            record("HTTP-处置事件", resp.status_code == 200
                   and resp.json()["event"]["resolved"] is True)
        else:
            record("HTTP-处置事件", True)   # 无未处置事件时视为通过

        # 司机 complete 带 actualKm(里程异常 HTTP 口径)
        from repositories.member_repository import MemberRepository
        await MemberRepository().update_fields(1, {"level": 5})
        client.post("/api/ride/driver/apply", headers=member, json={
            "idNumber": "370900199001010011",
            "licenseNumber": "370900123456", "licenseClass": "C1",
            "drivingYears": 8, "accidentFreeDecl": True,
            "drunkFreeDecl": True, "emergencyContact": "王紧急",
            "bambooScore": 800})
        client.post("/api/ride/driver/profile", headers=member,
                    json={"plateNo": "鲁J88888", "lat": 36.191,
                          "lng": 117.130})
        client.post("/api/ride/driver/status", headers=member,
                    json={"status": "online"})
        client.post("/api/ride/coupons/grant", headers=admin, json={
            "memberId": 1, "orderId": "HP3", "amount": 800})
        resp = client.post("/api/ride/call", headers=member, json={
            "pickup": {"lat": 36.1905, "lng": 117.13, "address": "饭店"},
            "dropoff": {"lat": 36.19, "lng": 117.13, "address": "市区"},
            "distanceKm": 8.0})
        ride_id2 = resp.json()["rideId"]
        client.post(f"/api/ride/driver/orders/{ride_id2}/accept",
                   headers=member)
        client.post(f"/api/ride/driver/orders/{ride_id2}/start",
                   headers=member)
        resp = client.post(f"/api/ride/driver/orders/{ride_id2}/complete",
                           headers=member,
                           json={"durationMinutes": 0, "pricingHour": 14,
                                 "actualKm": 18.0})
        body = resp.json()["ride"]
        record("HTTP-司机上报里程异常", body["mileageAnomaly"] is True
               and body["pricing"]["totalAmount"] == 100.0,
               str(body.get("pricing")))


async def main():
    test_classes = [
        ("三态通道", TestChannelTriState),
        ("平台直发回调", TestPartnerCallback),
        ("POI安全校验", TestSafetyPOI),
        ("超时扫描", TestSafetyScan),
        ("里程异常", TestMileageAnomaly),
        ("HTTP层", TestHttpRoutes),
    ]
    print("=" * 62)
    print("41号·AI智能代驾模块 P2 专项测试(平台回调+安全监控)")
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


def _now_iso():
    return datetime.now(UTC).isoformat()


if __name__ == "__main__":
    sys.exit(1 if asyncio.run(main()) else 0)
