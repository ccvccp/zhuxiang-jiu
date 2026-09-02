"""41号·AI智能代驾模块 P0 专项测试(券引擎 + 司机资格 AI 审查)

运行方式:
    python test_ride_p0.py

覆盖(设计文档 §2.1/§2.2):
    - 种子司机池(8位/三轨分布/在线统计)
    - 满额赠券引擎(档位梯度/幂等/持有上限/券码格式/有效期)
    - 券生命周期(惰性过期/核销/重复核销409/过期核销/冲正)
    - 司机资格 AI 审查(SVIP硬门槛/材料硬校验/三档决策/入池/重复申请)
    - 司机池管理(牌照前置/上下线/暂停流转)
    - 第22档案注册(ai_learning 注册表/默认权重/评分器单例)
    - HTTP 层(13端点鉴权与正常/异常口径)
    - 订单支付钩子 E2E(买酒满500自动赠券入券包)
"""

import asyncio
import os
import sys
from datetime import datetime, UTC, timedelta

# 必须在 import 业务模块之前设置(对齐 40号测试惯例)
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
    """每类测试前清空内存存储重建初始态(种子惰性重灌)"""
    from repositories.store import reset_store as _reset
    _reset()


# ============================================================
# 测试: 种子司机池
# ============================================================

class TestSeedPool:
    async def run(self):
        from repositories.ride_repository import RideRepository, TRACK_SELF

        repo = RideRepository()
        drivers = await repo.list_drivers(limit=100)
        record("种子池-8位司机", len(drivers) == 8, f"实际{len(drivers)}")

        by_track = {}
        for d in drivers:
            by_track[d["track"]] = by_track.get(d["track"], 0) + 1
        record("种子池-三轨分布(自营3/加盟3/直发2)",
               by_track.get("self") == 3 and by_track.get("partner") == 3
               and by_track.get("platform") == 2, f"实际{by_track}")

        online = [d for d in drivers if d["status"] == "online"]
        record("种子池-在线司机≥5", len(online) >= 5,
               f"实际{len(online)}")

        self_made = [d for d in drivers if d["track"] == TRACK_SELF]
        record("种子池-自营司机字段齐全",
               all(d.get("rating") and d.get("drivingYears", 0) >= 3
                   for d in self_made))

        # 幂等: 重复 list 不重复灌种子
        drivers2 = await repo.list_drivers(limit=100)
        record("种子池-灌入幂等", len(drivers2) == 8)


# ============================================================
# 测试: 满额赠券引擎
# ============================================================

class TestCouponGrant:
    async def run(self):
        from services.ride_coupon_service import RideCouponService
        from repositories.ride_repository import COUPON_VALUE

        svc = RideCouponService()

        # 未达门槛
        r = await svc.grant_for_order(1, "ORDER400", 499.0)
        record("赠券-未达门槛0张", r["granted"] == 0
               and "门槛" in r["reason"], str(r))

        # 一档 500-1000 → 1 张
        r = await svc.grant_for_order(1, "ORDER600", 800.0)
        record("赠券-一档1张", r["granted"] == 1, str(r))
        record("赠券-券码格式", r["codes"] == ["RIDEORDER600_1"],
               str(r["codes"]))
        record("赠券-面值60", COUPON_VALUE == 60.0)

        # 幂等: 同订单重复触发只发一次
        r2 = await svc.grant_for_order(1, "ORDER600", 800.0)
        record("赠券-同订单幂等", r2["granted"] == 0 and r2["skipped"] == 1,
               str(r2))

        # 二档 1000-3000 → 2 张
        r = await svc.grant_for_order(2, "ORDER2000", 2500.0)
        record("赠券-二档2张", r["granted"] == 2, str(r))

        # 三档 ≥3000 → 3 张
        r = await svc.grant_for_order(2, "ORDER3500", 3500.0)
        record("赠券-三档3张", r["granted"] == 3, str(r))

        # 券包聚合
        pkg = await svc.get_package(2)
        record("赠券-券包聚合(2+3=5)", pkg["holdCount"] == 5
               and pkg["totalGranted"] == 5, str(pkg["holdCount"]))

        # 有效期 90 天
        coupon = await svc.repo.get_coupon("RIDEORDER600_1")
        exp = datetime.fromisoformat(coupon["expiresAt"])
        delta_days = (exp - datetime.now(UTC)).days
        record("赠券-有效期90天", 89 <= delta_days <= 90,
               f"实际{delta_days}天")
        record("赠券-市内口径字段", coupon.get("cityRadiusKm") == 40)

        # 持有上限: 会员1已持1张, 上限6 → 再发3张订单只补5张中的差额
        await svc.grant_for_order(1, "ORDER_A", 3500.0)   # 3张 → 持4
        await svc.grant_for_order(1, "ORDER_B", 3500.0)   # 3张 → 只补2张
        r = await svc.grant_for_order(1, "ORDER_C", 3500.0)  # 已达上限
        record("赠券-持有上限封顶", r["granted"] == 0
               and "上限" in r["reason"], str(r))
        pkg = await svc.get_package(1)
        record("赠券-上限后持有=6", pkg["holdCount"] == 6,
               str(pkg["holdCount"]))


# ============================================================
# 测试: 券生命周期(过期/核销/冲正)
# ============================================================

class TestCouponLifecycle:
    async def run(self):
        from services.ride_coupon_service import RideCouponService
        from repositories.ride_repository import (
            RideRepository, COUPON_STATUS_GRANTED, COUPON_STATUS_USED,
            COUPON_STATUS_EXPIRED, COUPON_STATUS_REVOKED,
        )

        svc = RideCouponService()
        repo = RideRepository()
        await svc.grant_for_order(1, "ORD1", 800.0)
        await svc.grant_for_order(1, "ORD2", 800.0)
        await svc.grant_for_order(1, "ORD3", 800.0)

        # 核销
        r = await svc.redeem("RIDEORD1_1", ride_id="R1")
        record("核销-成功", r["success"] is True and r["value"] == 60,
               str(r))
        coupon = await repo.get_coupon("RIDEORD1_1")
        record("核销-状态used", coupon["status"] == COUPON_STATUS_USED)
        pkg = await svc.get_package(1)
        record("核销-券包扣减", pkg["holdCount"] == 2
               and pkg["totalUsed"] == 1, str(pkg["holdCount"]))

        # 重复核销 → 409 语义
        try:
            await svc.redeem("RIDEORD1_1")
            record("核销-重复核销拒绝", False, "未抛出")
        except ValueError:
            record("核销-重复核销拒绝", True)

        # 人工改过期时间 → 惰性过期(get_package 触发)
        coupon = await repo.get_coupon("RIDEORD2_1")
        coupon["expiresAt"] = (datetime.now(UTC)
                               - timedelta(days=1)).isoformat()
        await repo.save_coupon(coupon)
        pkg = await svc.get_package(1)
        coupon2 = await repo.get_coupon("RIDEORD2_1")
        record("过期-惰性标记expired",
               coupon2["status"] == COUPON_STATUS_EXPIRED)
        record("过期-持有数回减", pkg["holdCount"] == 1,
               str(pkg["holdCount"]))

        # 过期券核销 → 409 语义
        try:
            await svc.redeem("RIDEORD2_1")
            record("核销-过期券拒绝", False, "未抛出")
        except ValueError:
            record("核销-过期券拒绝", True)

        # 核销时惰性过期兜底(redeem 路径)
        coupon = await repo.get_coupon("RIDEORD3_1")
        coupon["expiresAt"] = (datetime.now(UTC)
                               - timedelta(days=2)).isoformat()
        await repo.save_coupon(coupon)
        try:
            await svc.redeem("RIDEORD3_1")
            record("核销-核销路径过期兜底", False, "未抛出")
        except ValueError:
            coupon3 = await repo.get_coupon("RIDEORD3_1")
            record("核销-核销路径过期兜底",
                   coupon3["status"] == COUPON_STATUS_EXPIRED)

        # 冲正: 退款 → granted 作废, used 保留
        await svc.grant_for_order(1, "ORDR", 800.0)
        await svc.redeem("RIDEORDR_1", ride_id="R9")   # 先核销一张
        r = await svc.revoke_for_order("ORDR")
        record("冲正-未核销作废+已核销保留",
               r["total"] == 1 and r["keptUsed"] == 1, str(r))
        # ORD1 已核销的券不受其他订单退款影响
        c1 = await repo.get_coupon("RIDEORD1_1")
        record("冲正-已核销券不追回", c1["status"] == COUPON_STATUS_USED)
        # 再发再冲
        await svc.grant_for_order(1, "ORDN", 800.0)
        r = await svc.revoke_for_order("ORDN")
        cn = await repo.get_coupon("RIDEORDN_1")
        record("冲正-granted作废revoked",
               r["revoked"] == 1
               and cn["status"] == COUPON_STATUS_REVOKED, str(r))
        # 作废券核销 → 409
        try:
            await svc.redeem("RIDEORDN_1")
            record("核销-作废券拒绝", False, "未抛出")
        except ValueError:
            record("核销-作废券拒绝", True)
        # 不存在的券 → 404 语义
        try:
            await svc.redeem("RIDE_NOPE_1")
            record("核销-不存在券404", False, "未抛出")
        except KeyError:
            record("核销-不存在券404", True)


# ============================================================
# 测试: 司机资格 AI 审查
# ============================================================

class TestDriverGate:
    async def run(self):
        from repositories.member_repository import MemberRepository
        from services.driver_gate_service import DriverGateService

        gate = DriverGateService()
        member_repo = MemberRepository()

        # 优质画像(全部材料+8年驾龄+高信用)
        good = {
            "idNumber": "370900199001010011",
            "licenseNumber": "370900123456",
            "licenseClass": "C1",
            "drivingYears": 8,
            "accidentFreeDecl": True,
            "drunkFreeDecl": True,
            "emergencyContact": "王紧急 13800000000",
            "bambooScore": 800,
        }
        # 边缘画像(4年驾龄+缺双声明 → 人工复核档)
        edge = {
            "idNumber": "370900199203020022",
            "licenseNumber": "370900654321",
            "licenseClass": "C1",
            "drivingYears": 4,
            "accidentFreeDecl": False,
            "drunkFreeDecl": False,
            "emergencyContact": "李紧急 13900000000",
            "bambooScore": 400,
        }

        # 非 SVIP 硬门槛: 会员1 L1 → 409
        try:
            await gate.apply(1, good)
            record("审查-非SVIP硬拒", False, "未抛出")
        except ValueError as e:
            record("审查-非SVIP硬拒", "SVIP" in str(e))

        # 会员1 升 L5 → 自动通过入池
        await member_repo.update_fields(1, {"level": 5})
        r = await gate.apply(1, good)
        record("审查-优质画像自动通过", r["status"] == "approved",
               str(r.get("score")))
        record("审查-通过即入池", r.get("driverId") is not None, str(r))
        record("审查-评分≥70", r["score"] >= 70, str(r["score"]))
        driver = await gate.repo.get_driver(r["driverId"])
        record("审查-入池自营轨道+offline初始",
               driver["track"] == "self" and driver["status"] == "offline")

        # 重复申请 → 409
        try:
            await gate.apply(1, good)
            record("审查-重复申请拒绝", False, "未抛出")
        except ValueError:
            record("审查-重复申请拒绝", True)

        # 会员2 升 L5 → 边缘画像人工复核档
        await member_repo.update_fields(2, {"level": 5})
        r = await gate.apply(2, edge)
        record("审查-边缘画像人工复核", r["status"] == "manual_review",
               f"score={r.get('score')}")
        record("审查-复核档不入池", r.get("driverId") is None)

        # 人工裁决通过 → 入池
        app = await gate.get_application(2)
        decided = await gate.decide(app["applicationId"], True,
                                    reviewer="admin", note="材料补验通过")
        record("复核-裁决通过入池", decided["status"] == "approved"
               and decided.get("driverId") is not None, str(decided.get("status")))

        # 已裁决申请再裁决 → 409
        try:
            await gate.decide(app["applicationId"], True)
            record("复核-重复裁决拒绝", False, "未抛出")
        except ValueError:
            record("复核-重复裁决拒绝", True)

        # 材料硬校验(先造会员3: L5 SVIP)
        await member_repo.create({
            "phone": "13800000003", "password": "test123456",
            "nickname": "SVIP测试会员", "level": 5,
            "growth_value": 9999, "points": 100, "status": 1,
            "role": "member", "ageConfirmed": True,
            "birthdate": "1992-05-05", "ageVerified": True,
            "created_at": "2026-08-21T00:00:00+00:00",
        })
        member3_id = max(int(m["id"]) for m in await member_repo.list_all())
        bad_years = dict(good, drivingYears=2)
        try:
            await gate.apply(member3_id, bad_years)
            record("审查-驾龄不足硬拒", False, "未抛出")
        except ValueError as e:
            record("审查-驾龄不足硬拒", "驾龄" in str(e))
        # 身份证格式硬拒
        try:
            await gate.apply(member3_id, dict(good, idNumber="123"))
            record("审查-证件格式硬拒", False, "未抛出")
        except ValueError as e:
            record("审查-证件格式硬拒", "18" in str(e))
        # 会员不存在 → 404
        try:
            await gate.apply(999, good)
            record("审查-会员不存在404", False, "未抛出")
        except KeyError:
            record("审查-会员不存在404", True)

        # 劣质画像(驾龄3+材料缺+低信用) → rejected/复核档
        r = await gate.apply(member3_id, dict(good, idNumber="370900199001010013",
                                              drivingYears=3,
                                              accidentFreeDecl=False,
                                              drunkFreeDecl=False,
                                              emergencyContact="",
                                              bambooScore=200))
        record("审查-劣质画像处置", r["status"] in ("rejected",
                                                  "manual_review"),
               f"score={r.get('score')} status={r.get('status')}")

        # 概览
        ov = await gate.overview()
        record("概览-三轨统计", ov["byTrack"].get("self") >= 3,
               str(ov["byTrack"]))


# ============================================================
# 测试: 评分器直测 + 第22档案注册
# ============================================================

class TestScorerAndRegistry:
    async def run(self):
        from services.ai_scoring_service import (
            SCORERS, DriverApplicationScorer,
        )
        from services.ai_learning_service import (
            SCORER_REGISTRY, default_weights,
        )

        # 第22档案注册
        record("档案-注册表含driver_application_gate",
               "driver_application_gate" in SCORER_REGISTRY)
        record("档案-batch=8",
               SCORER_REGISTRY.get("driver_application_gate", {})
               .get("batch") == 8)
        record("档案-默认权重映射",
               default_weights("driver_application_gate")
               == DriverApplicationScorer.WEIGHTS)
        record("档案-评分器单例",
               isinstance(SCORERS.get("driver_application_gate"),
                          DriverApplicationScorer))
        record("档案-权重和=1",
               abs(sum(DriverApplicationScorer.WEIGHTS.values()) - 1) < 1e-9)

        scorer = SCORERS["driver_application_gate"]
        # 优质画像
        r = await scorer.score({
            "applicationId": 1, "memberId": 1,
            "idNumber": "370900199001010011",
            "licenseNumber": "370900123456",
            "licenseClass": "C1", "drivingYears": 8,
            "age": 38, "ageVerified": True, "registerHours": 8760,
            "bambooScore": 800, "complaintRate": 0,
            "accidentFreeDecl": True, "drunkFreeDecl": True,
            "emergencyContact": "王紧急",
        })
        record("评分-优质画像approved", r["action"] == "approved"
               and r["score"] >= 70, str(r["score"]))
        record("评分-五因子输出", len(r["factors"]) == 5
               and all("contribution" in f for f in r["factors"]))

        # 边缘画像(3年+缺声明) → manual_review
        r = await scorer.score({
            "applicationId": 2, "memberId": 2,
            "idNumber": "370900199203020022",
            "licenseNumber": "370900654321",
            "licenseClass": "C1", "drivingYears": 3,
            "age": 30, "ageVerified": True, "registerHours": 8760,
            "bambooScore": 400, "complaintRate": 0,
            "accidentFreeDecl": False, "drunkFreeDecl": False,
            "emergencyContact": "李紧急",
        })
        record("评分-边缘画像manual_review", r["action"] == "manual_review",
               str(r["score"]))

        # 劣质画像(证件格式存疑+驾龄3+低信用+未实名) → rejected
        r = await scorer.score({
            "applicationId": 3, "memberId": 3,
            "idNumber": "370900199001010033",
            "licenseNumber": "3709",       # 格式不合规
            "licenseClass": "C1", "drivingYears": 3,
            "age": 30, "ageVerified": False, "registerHours": 720,
            "bambooScore": 200, "complaintRate": 0.1,
            "accidentFreeDecl": False, "drunkFreeDecl": False,
            "emergencyContact": "",
        })
        record("评分-劣质画像rejected", r["action"] == "rejected",
               str(r["score"]))

        # 声明一致性: 年龄小于驾龄+18 → 存疑
        r = await scorer.score({
            "applicationId": 4, "memberId": 4,
            "idNumber": "370900199001010044",
            "licenseNumber": "370900123456",
            "licenseClass": "C1", "drivingYears": 8,
            "age": 20,   # 20 岁不可能 8 年驾龄
            "ageVerified": True, "registerHours": 8760,
            "bambooScore": 800, "complaintRate": 0,
            "accidentFreeDecl": True, "drunkFreeDecl": True,
            "emergencyContact": "x",
        })
        consist = next(f for f in r["factors"]
                       if f["name"] == "consistency")
        record("评分-年龄驾龄矛盾扣分", consist["score"] < 60,
               str(consist))


# ============================================================
# 测试: 司机池管理
# ============================================================

class TestDriverPool:
    async def run(self):
        from repositories.member_repository import MemberRepository
        from services.driver_gate_service import DriverGateService

        gate = DriverGateService()
        member_repo = MemberRepository()
        await member_repo.update_fields(1, {"level": 5})
        r = await gate.apply(1, {
            "idNumber": "370900199001010011",
            "licenseNumber": "370900123456",
            "licenseClass": "C1", "drivingYears": 8,
            "accidentFreeDecl": True, "drunkFreeDecl": True,
            "emergencyContact": "王紧急", "bambooScore": 800,
        })
        member_id = 1

        # 无牌照上线 → 409
        try:
            await gate.set_driver_status(member_id, "online")
            record("司机-无牌照上线拒绝", False, "未抛出")
        except ValueError:
            record("司机-无牌照上线拒绝", True)

        # 补牌照 → 上线
        await gate.update_driver(member_id, {"plateNo": "鲁J88888"})
        driver = await gate.set_driver_status(member_id, "online")
        record("司机-补牌照后上线", driver["status"] == "online")

        # 下线
        driver = await gate.set_driver_status(member_id, "offline")
        record("司机-下线", driver["status"] == "offline")

        # 暂停 → 暂停中仅支持吊销
        driver = await gate.set_driver_status(member_id, "suspended",
                                              reason="投诉调查")
        record("司机-违规暂停留痕", driver["status"] == "suspended"
               and "投诉调查" in driver.get("suspendedReason", ""))
        try:
            await gate.set_driver_status(member_id, "online")
            record("司机-暂停中上线拒绝", False, "未抛出")
        except ValueError:
            record("司机-暂停中上线拒绝", True)

        # 吊销 → 终态
        driver = await gate.set_driver_status(member_id, "revoked")
        try:
            await gate.set_driver_status(member_id, "online")
            record("司机-吊销后不可变更", False, "未抛出")
        except ValueError:
            record("司机-吊销后不可变更", True)

        # 非司机会员 → 404
        try:
            await gate.set_driver_status(2, "online")
            record("司机-无资格404", False, "未抛出")
        except KeyError:
            record("司机-无资格404", True)

        # 池过滤
        online = await gate.list_pool(status="online")
        record("司机池-状态过滤", all(d["status"] == "online" for d in online)
               and len(online) >= 4, str(len(online)))
        partners = await gate.list_pool(track="partner")
        record("司机池-轨道过滤", len(partners) == 3
               and all(d["track"] == "partner" for d in partners))


# ============================================================
# 测试: HTTP 层
# ============================================================

class TestHttpRoutes:
    async def run(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from routes.ride_routes import register_ride_routes
        from repositories.member_repository import MemberRepository

        app = FastAPI()
        register_ride_routes(app)
        client = TestClient(app)
        admin = {"X-Role": "admin"}

        # 鉴权: 会员端缺头 403
        resp = client.get("/api/ride/coupons")
        record("HTTP-券包缺头403", resp.status_code == 403,
               str(resp.status_code))
        # 鉴权: 管理端非 admin 403
        resp = client.get("/api/ride/admin/overview")
        record("HTTP-概览非admin403", resp.status_code == 403,
               str(resp.status_code))

        # 满额赠券(admin 补发口径)
        resp = client.post("/api/ride/coupons/grant", headers=admin, json={
            "memberId": 1, "orderId": "HTTP600", "amount": 800})
        body = resp.json()
        record("HTTP-满额赠券", resp.status_code == 200
               and body["granted"] == 1, str(body))

        # 券包查询
        resp = client.get("/api/ride/coupons",
                          headers={"X-Member-Id": "1"})
        body = resp.json()
        record("HTTP-券包查询", resp.status_code == 200
               and body["holdCount"] == 1, str(body.get("holdCount")))

        # 券详情 + 非本人 403
        resp = client.get("/api/ride/coupons/RIDEHTTP600_1",
                          headers={"X-Member-Id": "1"})
        record("HTTP-券详情", resp.status_code == 200)
        resp = client.get("/api/ride/coupons/RIDEHTTP600_1",
                          headers={"X-Member-Id": "2"})
        record("HTTP-非本人券403", resp.status_code == 403,
               str(resp.status_code))
        resp = client.get("/api/ride/coupons/RIDE_NOPE",
                          headers={"X-Member-Id": "1"})
        record("HTTP-券不存在404", resp.status_code == 404,
               str(resp.status_code))

        # 核销 + 重复核销 409
        resp = client.post("/api/ride/coupons/redeem", headers=admin,
                           json={"code": "RIDEHTTP600_1", "rideId": "R1"})
        record("HTTP-核销", resp.status_code == 200)
        resp = client.post("/api/ride/coupons/redeem", headers=admin,
                           json={"code": "RIDEHTTP600_1"})
        record("HTTP-重复核销409", resp.status_code == 409,
               str(resp.status_code))

        # 司机申请: 会员1 L1 → 409; 升 L5 → 通过
        resp = client.post("/api/ride/driver/apply",
                           headers={"X-Member-Id": "1"}, json={
                               "idNumber": "370900199001010011",
                               "licenseNumber": "370900123456",
                               "licenseClass": "C1", "drivingYears": 8,
                               "accidentFreeDecl": True,
                               "drunkFreeDecl": True,
                               "emergencyContact": "王紧急",
                               "bambooScore": 800})
        record("HTTP-非SVIP申请409", resp.status_code == 409,
               str(resp.status_code))
        await MemberRepository().update_fields(1, {"level": 5})
        resp = client.post("/api/ride/driver/apply",
                           headers={"X-Member-Id": "1"}, json={
                               "idNumber": "370900199001010011",
                               "licenseNumber": "370900123456",
                               "licenseClass": "C1", "drivingYears": 8,
                               "accidentFreeDecl": True,
                               "drunkFreeDecl": True,
                               "emergencyContact": "王紧急",
                               "bambooScore": 800})
        body = resp.json()
        record("HTTP-SVIP申请通过", resp.status_code == 200
               and body["status"] == "approved", str(body.get("status")))

        # 审查进度查询
        resp = client.get("/api/ride/driver/application",
                          headers={"X-Member-Id": "1"})
        record("HTTP-审查进度", resp.status_code == 200
               and resp.json()["application"]["status"] == "approved")
        resp = client.get("/api/ride/driver/application",
                          headers={"X-Member-Id": "2"})
        record("HTTP-无申请404", resp.status_code == 404,
               str(resp.status_code))

        # 人工复核队列: 会员2 边缘画像 → manual_review → 裁决
        await MemberRepository().update_fields(2, {"level": 5})
        resp = client.post("/api/ride/driver/apply",
                           headers={"X-Member-Id": "2"}, json={
                               "idNumber": "370900199203020022",
                               "licenseNumber": "370900654321",
                               "licenseClass": "C1", "drivingYears": 4,
                               "accidentFreeDecl": False,
                               "drunkFreeDecl": False,
                               "emergencyContact": "李紧急",
                               "bambooScore": 400})
        body = resp.json()
        record("HTTP-边缘画像manual", body.get("status") == "manual_review",
               str(body.get("status")))
        app_id = body["applicationId"]
        resp = client.get("/api/ride/admin/applications",
                          headers=admin,
                          params={"status": "manual_review"})
        record("HTTP-复核队列过滤",
               resp.status_code == 200 and resp.json()["total"] == 1,
               str(resp.json().get("total")))
        resp = client.post(
            f"/api/ride/admin/applications/{app_id}/decide",
            headers=admin, json={"approve": True, "note": "补验通过"})
        record("HTTP-裁决通过", resp.status_code == 200
               and resp.json()["application"]["status"] == "approved")

        # 司机上下线
        resp = client.post("/api/ride/driver/status",
                           headers={"X-Member-Id": "1"},
                           json={"status": "online"})
        record("HTTP-无牌照上线409", resp.status_code == 409,
               str(resp.status_code))
        client.post("/api/ride/driver/profile",
                    headers={"X-Member-Id": "1"},
                    json={"plateNo": "鲁J88888"})
        resp = client.post("/api/ride/driver/status",
                           headers={"X-Member-Id": "1"},
                           json={"status": "online"})
        record("HTTP-上线", resp.status_code == 200
               and resp.json()["driver"]["status"] == "online")

        # 管理端池/概览
        resp = client.get("/api/ride/admin/pool", headers=admin,
                          params={"track": "self", "status": "online"})
        record("HTTP-池过滤", resp.status_code == 200
               and resp.json()["total"] >= 1)
        resp = client.get("/api/ride/admin/overview", headers=admin)
        body = resp.json()
        record("HTTP-概览", resp.status_code == 200
               and body["applications"]["total"] == 2
               and body["poolTotal"] == 10, str(body.get("poolTotal")))


# ============================================================
# 测试: 订单支付钩子 E2E(买酒满 500 自动赠券)
# ============================================================

class TestOrderPayHook:
    async def run(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from routes.order_routes import register_order_routes
        from routes.ride_routes import register_ride_routes

        app = FastAPI()
        register_order_routes(app)
        register_ride_routes(app)
        client = TestClient(app)
        member = {"X-Member-Id": "1"}

        # 取种子商品第一个(库存存在的真实商品ID)
        from repositories.store import _mock_store
        product_id = next(iter(_mock_store["products"].keys()))

        # 下单: 10 × ¥80 = ¥800 → 一档 1 张券
        resp = client.post("/api/order/create", headers=member, json={
            "items": [{"productId": product_id, "productName": "竹香酒",
                       "quantity": 10, "unitPrice": 80.0}],
            "address": {"name": "张三", "phone": "13800000001",
                        "province": "山东省", "city": "泰安市",
                        "district": "泰山区", "detail": "竹香路 1 号"},
        })
        record("E2E-下单", resp.status_code == 200, str(resp.text[:200]))
        order_id = (resp.json().get("details") or {}).get("orderId") \
            or resp.json().get("orderId")

        # 支付前无券
        resp = client.get("/api/ride/coupons", headers=member)
        record("E2E-支付前券包空", resp.json().get("holdCount") == 0,
               str(resp.json().get("holdCount")))

        # 支付 → 钩子自动赠券
        resp = client.post(f"/api/order/{order_id}/pay", headers=member,
                           json={"method": "wechat"})
        record("E2E-支付", resp.status_code == 200,
               str(resp.text[:200]))
        resp = client.get("/api/ride/coupons", headers=member)
        body = resp.json()
        record("E2E-支付自动赠券入包", body.get("holdCount") == 1
               and body.get("totalGranted") == 1,
               str(body.get("holdCount")))
        codes = [c["code"] for c in body.get("coupons", [])]
        record("E2E-券码来源订单", codes == [f"RIDE{order_id}_1"],
               str(codes))
        coupon = body["coupons"][0]
        record("E2E-券面值60", coupon.get("value") == 60, str(coupon))


# ============================================================
# 主入口
# ============================================================

async def main():
    test_classes = [
        ("种子司机池", TestSeedPool),
        ("满额赠券引擎", TestCouponGrant),
        ("券生命周期", TestCouponLifecycle),
        ("司机资格AI审查", TestDriverGate),
        ("评分器与第22档案", TestScorerAndRegistry),
        ("司机池管理", TestDriverPool),
        ("HTTP层", TestHttpRoutes),
        ("订单支付钩子E2E", TestOrderPayHook),
    ]
    print("=" * 62)
    print("41号·AI智能代驾模块 P0 专项测试(券引擎+司机资格审查)")
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
