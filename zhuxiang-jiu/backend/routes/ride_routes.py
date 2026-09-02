"""41号·AI智能代驾模块路由(P0+P1+P2, 28 端点)

鉴权:
    - 会员端: X-Member-Id(券包/叫单/行程/司机申请/上下线)
    - 管理端: X-Role: admin(补发/冲正/复核/司机池/行程/结算/安全)
    - 平台回调: 开放端点(合作平台服务间调用, P3 可加签名)

异常映射(遵循项目约定):
    - KeyError → 404(券/申请/司机/行程不存在)
    - ValueError → 409(门槛不达标/材料缺失/重复申请/状态非法等)

端点分布:
    - 券引擎(5): GET /coupons / GET /coupons/{code}
                  / POST /coupons/grant(补发) / POST /coupons/revoke(冲正)
                  / POST /coupons/redeem(核销)
    - 司机资格(4): POST /driver/apply / GET /driver/application
                    / POST /driver/status / POST /driver/profile
    - 行程(乘客 4): POST /call / GET /orders / GET /orders/{id}
                    / POST /orders/{id}/cancel
    - 行程(司机 5): POST /driver/orders/{id}/accept|start|complete
                    / GET /driver/orders / GET /driver/settlements
    - 管理端(8): GET /admin/applications
                  / POST /admin/applications/{id}/decide
                  / GET /admin/pool / GET /admin/overview
                  / GET /admin/rides / GET /admin/settlements
                  / POST /admin/safety/scan / GET /admin/risk-panel
    - 平台回调(2): POST /partner/callback / POST /admin/risk-events/{id}/resolve

P3 预留: 双向评价 / 日结对账 / 学习回流调度
"""

from fastapi import APIRouter, Header, HTTPException, Query

from pydantic import BaseModel as PydBaseModel, Field

from services.ride_coupon_service import RideCouponService
from services.driver_gate_service import DriverGateService
from services.ride_dispatch_service import RideDispatchService
from services.ride_safety_service import RideSafetyService
from services.ride_review_service import RideReviewService
from services.ride_reconcile_service import RideReconcileService


router = APIRouter()
_coupon_service = RideCouponService()
_gate_service = DriverGateService()
_dispatch_service = RideDispatchService()
_safety_service = RideSafetyService()
_review_service = RideReviewService()
_reconcile_service = RideReconcileService()


# ============================================================
# 鉴权与异常映射辅助
# ============================================================

def _require_admin(x_role: str | None):
    if x_role != "admin":
        raise HTTPException(status_code=403, detail="需要管理员权限")


def _require_member(x_member_id: str | None) -> int:
    if not x_member_id:
        raise HTTPException(status_code=403, detail="缺少 X-Member-Id")
    try:
        return int(x_member_id)
    except ValueError:
        raise HTTPException(status_code=403, detail="X-Member-Id 须为数字")


def _handle(exc: Exception):
    if isinstance(exc, KeyError):
        msg = str(exc) if str(exc) else "资源不存在"
        if msg.startswith("'") and msg.endswith("'"):
            msg = msg[1:-1]
        raise HTTPException(status_code=404, detail=msg)
    if isinstance(exc, ValueError):
        raise HTTPException(status_code=409, detail=str(exc))
    raise HTTPException(status_code=500, detail=str(exc))


# ============================================================
# 请求模型
# ============================================================

class GrantCouponRequest(PydBaseModel):
    memberId: int = Field(..., gt=0, description="会员ID")
    orderId: str = Field(..., min_length=1, max_length=64,
                         description="来源订单号")
    amount: float = Field(..., ge=0, description="订单实付(元)")


class RevokeCouponRequest(PydBaseModel):
    orderId: str = Field(..., min_length=1, max_length=64,
                         description="退款订单号")


class RedeemCouponRequest(PydBaseModel):
    code: str = Field(..., min_length=1, max_length=64, description="券码")
    rideId: str = Field("", max_length=64, description="关联行程ID(P1)")


class DriverApplyRequest(PydBaseModel):
    idNumber: str = Field(..., min_length=18, max_length=18,
                          description="身份证号(18位)")
    licenseNumber: str = Field(..., min_length=10, max_length=20,
                               description="驾照号")
    licenseClass: str = Field("C1", max_length=3, description="准驾车型")
    drivingYears: float = Field(..., ge=0, le=60, description="驾龄(年)")
    accidentFreeDecl: bool = Field(..., description="无重大交通事故声明")
    drunkFreeDecl: bool = Field(..., description="无酒驾记录声明")
    emergencyContact: str = Field(..., min_length=1, max_length=50,
                                  description="紧急联系人")
    bambooScore: int = Field(None, ge=0, le=1000,
                             description="竹信分(缺省取会员档案)")
    complaintRate: float = Field(0, ge=0, le=1, description="历史投诉率")


class DriverStatusRequest(PydBaseModel):
    status: str = Field(..., description="online/offline/suspended/revoked")
    reason: str = Field("", max_length=200, description="原因(suspended 用)")


class DriverProfileRequest(PydBaseModel):
    plateNo: str = Field(None, min_length=0, max_length=16,
                         description="车辆牌照(上线接单必填)")
    city: str = Field(None, min_length=1, max_length=32, description="服务城市")
    lat: float = Field(None, ge=-90, le=90, description="常驻位置纬度")
    lng: float = Field(None, ge=-180, le=180, description="常驻位置经度")


class ApplicationDecideRequest(PydBaseModel):
    approve: bool = Field(..., description="true=通过入池 / false=拒绝")
    reviewer: str = Field("admin", max_length=50, description="审核人")
    note: str = Field("", max_length=200, description="裁决备注")


class RideLocation(PydBaseModel):
    lat: float = Field(..., ge=-90, le=90, description="纬度")
    lng: float = Field(..., ge=-180, le=180, description="经度")
    address: str = Field("", max_length=200, description="地址描述")


class RideCallRequest(PydBaseModel):
    pickup: RideLocation = Field(..., description="上车点")
    dropoff: RideLocation = Field(..., description="下车点")
    distanceKm: float = Field(None, gt=0, le=500,
                              description="行程里程(缺省按坐标球面距离; "
                                          "Mock-first 确定性口径)")


class RideCancelRequest(PydBaseModel):
    reason: str = Field("", max_length=200, description="取消原因")


class RideCompleteRequest(PydBaseModel):
    durationMinutes: float = Field(None, ge=0, le=1440,
                                  description="行程时长分钟(缺省按实际起止; "
                                              "Mock-first 确定性口径)")
    pricingHour: int = Field(None, ge=0, le=23,
                             description="计价小时(缺省当前小时; 夜间加成 "
                                         "Mock-first 确定性口径)")
    actualKm: float = Field(None, gt=0, le=500,
                            description="实际里程(缺省按预估计价; "
                                        "参与里程异常比对)")


class PartnerCallbackRequest(PydBaseModel):
    partnerOrderId: str = Field(..., min_length=1, max_length=64,
                                description="平台直发单号")
    event: str = Field(..., description="accepted/started/completed/cancelled")
    trace: dict = Field(default_factory=dict,
                        description="轨迹摘要{actualKm, durationMinutes}")


class RiskResolveRequest(PydBaseModel):
    note: str = Field("", max_length=200, description="处置备注")


class RideReviewRequest(PydBaseModel):
    direction: str = Field(..., description="passenger_to_driver / "
                                         "driver_to_passenger")
    score: int = Field(..., ge=1, le=5, description="星级 1-5")
    content: str = Field("", max_length=500, description="评价文本")


class ReconStartRequest(PydBaseModel):
    period: str = Field(..., pattern=r"^\d{4}-\d{2}-\d{2}$",
                        description="账期日 YYYY-MM-DD")
    track: str = Field(..., description="partner/platform(自营直付不对账)")
    channelBills: list = Field(None,
                               description="平台账单(缺省按本站镜像, "
                                           "Mock 口径)")


class ReconResolveRequest(PydBaseModel):
    resolution: str = Field("", max_length=500, description="差异处理说明")


class ReviewAnnotateRequest(PydBaseModel):
    expectedAction: str = Field(..., description="真值动作: show/watch/fold")
    note: str = Field("", max_length=200, description="标注说明")


# ============================================================
# 券引擎(乘客端 + 管理端)
# ============================================================

@router.get("/api/ride/coupons")
async def get_my_coupons(
    x_member_id: str = Header(default="", alias="X-Member-Id"),
):
    """我的券包(含即将过期提醒, 惰性过期)"""
    member_id = _require_member(x_member_id)
    try:
        return await _coupon_service.get_package(member_id)
    except Exception as e:
        raise _handle(e) from e


@router.get("/api/ride/coupons/{code}")
async def get_coupon_detail(
    code: str,
    x_member_id: str = Header(default="", alias="X-Member-Id"),
):
    """券详情(持有人校验)"""
    member_id = _require_member(x_member_id)
    try:
        coupon = await _coupon_service.repo.get_coupon(code)
        if coupon is None:
            raise KeyError(f"代驾券 {code} 不存在")
        if int(coupon.get("memberId") or 0) != member_id:
            raise HTTPException(status_code=403, detail="非本人券")
        return {"success": True, "coupon": coupon}
    except HTTPException:
        raise
    except Exception as e:
        raise _handle(e) from e


@router.post("/api/ride/coupons/grant")
async def grant_coupons(
    body: GrantCouponRequest,
    x_role: str = Header(default="", alias="X-Role"),
):
    """满额赠券(订单支付钩子自动触发; 此端点为 admin 手动补发/测试口径)"""
    _require_admin(x_role)
    try:
        return await _coupon_service.grant_for_order(
            body.memberId, body.orderId, body.amount)
    except Exception as e:
        raise _handle(e) from e


@router.post("/api/ride/coupons/revoke")
async def revoke_coupons(
    body: RevokeCouponRequest,
    x_role: str = Header(default="", alias="X-Role"),
):
    """订单退款 → 未核销券冲正作废"""
    _require_admin(x_role)
    try:
        return await _coupon_service.revoke_for_order(body.orderId)
    except Exception as e:
        raise _handle(e) from e


@router.post("/api/ride/coupons/redeem")
async def redeem_coupon(
    body: RedeemCouponRequest,
    x_role: str = Header(default="", alias="X-Role"),
):
    """核销代驾券(P1 智能结算入口, P0 提供口径)"""
    _require_admin(x_role)
    try:
        return await _coupon_service.redeem(body.code, body.rideId)
    except Exception as e:
        raise _handle(e) from e


# ============================================================
# 司机资格(会员端)
# ============================================================

@router.post("/api/ride/driver/apply")
async def driver_apply(
    body: DriverApplyRequest,
    x_member_id: str = Header(default="", alias="X-Member-Id"),
):
    """超级会员提交代驾员注册申请(AI 全自动审查, 即时出档)"""
    member_id = _require_member(x_member_id)
    try:
        return await _gate_service.apply(member_id, body.model_dump())
    except Exception as e:
        raise _handle(e) from e


@router.get("/api/ride/driver/application")
async def driver_application(
    x_member_id: str = Header(default="", alias="X-Member-Id"),
):
    """查询我的审查进度(含 AI 评分快照)"""
    member_id = _require_member(x_member_id)
    try:
        app = await _gate_service.get_application(member_id)
        return {"success": True, "application": app}
    except Exception as e:
        raise _handle(e) from e


@router.post("/api/ride/driver/status")
async def driver_status(
    body: DriverStatusRequest,
    x_member_id: str = Header(default="", alias="X-Member-Id"),
):
    """司机上下线(online/offline)"""
    member_id = _require_member(x_member_id)
    try:
        driver = await _gate_service.set_driver_status(
            member_id, body.status, body.reason)
        return {"success": True, "driver": driver}
    except Exception as e:
        raise _handle(e) from e


@router.post("/api/ride/driver/profile")
async def driver_profile(
    body: DriverProfileRequest,
    x_member_id: str = Header(default="", alias="X-Member-Id"),
):
    """司机补充信息(牌照/城市, 上线前必填牌照)"""
    member_id = _require_member(x_member_id)
    try:
        driver = await _gate_service.update_driver(
            member_id, body.model_dump(exclude_none=True))
        return {"success": True, "driver": driver}
    except Exception as e:
        raise _handle(e) from e


# ============================================================
# 行程(乘客端: 叫单/查询/取消)
# ============================================================

@router.post("/api/ride/call")
async def ride_call(
    body: RideCallRequest,
    x_member_id: str = Header(default="", alias="X-Member-Id"),
):
    """叫代驾: 选券(FEFO) → 规则过滤 → AI 评分 → 三轨派单(永不拒单)"""
    member_id = _require_member(x_member_id)
    try:
        return await _dispatch_service.call(
            member_id,
            pickup=body.pickup.model_dump(),
            dropoff=body.dropoff.model_dump(),
            distance_km=body.distanceKm)
    except Exception as e:
        raise _handle(e) from e


@router.get("/api/ride/orders")
async def my_rides(
    status: str = Query(None, description="按状态过滤"),
    x_member_id: str = Header(default="", alias="X-Member-Id"),
):
    """我的行程列表"""
    member_id = _require_member(x_member_id)
    try:
        rides = await _dispatch_service.list_my_rides(member_id,
                                                      status=status)
        return {"success": True, "total": len(rides), "rides": rides}
    except Exception as e:
        raise _handle(e) from e


@router.get("/api/ride/orders/{ride_id}")
async def ride_detail(
    ride_id: str,
    x_member_id: str = Header(default="", alias="X-Member-Id"),
):
    """行程详情(司机信息/状态/计价明细)"""
    member_id = _require_member(x_member_id)
    try:
        return {"success": True,
                "ride": await _dispatch_service.get_ride(member_id,
                                                        ride_id)}
    except Exception as e:
        raise _handle(e) from e


@router.post("/api/ride/orders/{ride_id}/cancel")
async def ride_cancel(
    ride_id: str,
    body: RideCancelRequest = None,
    x_member_id: str = Header(default="", alias="X-Member-Id"),
):
    """取消行程(免责窗口判定: 派单后 3 分钟内券退回)"""
    member_id = _require_member(x_member_id)
    try:
        ride = await _dispatch_service.cancel(
            member_id, ride_id,
            reason=(body.reason if body else ""))
        return {"success": True, "ride": ride}
    except Exception as e:
        raise _handle(e) from e


# ============================================================
# 行程(司机端: 接单/开始/结束/我的行程/我的结算)
# ============================================================

@router.post("/api/ride/driver/orders/{ride_id}/accept")
async def driver_accept_ride(
    ride_id: str,
    x_member_id: str = Header(default="", alias="X-Member-Id"),
):
    """司机确认接单 dispatched → driver_arriving"""
    member_id = _require_member(x_member_id)
    try:
        return {"success": True,
                "ride": await _dispatch_service.driver_accept(member_id,
                                                              ride_id)}
    except Exception as e:
        raise _handle(e) from e


@router.post("/api/ride/driver/orders/{ride_id}/start")
async def driver_start_ride(
    ride_id: str,
    x_member_id: str = Header(default="", alias="X-Member-Id"),
):
    """行程开始 driver_arriving → trip_started(乘客上车)"""
    member_id = _require_member(x_member_id)
    try:
        return {"success": True,
                "ride": await _dispatch_service.driver_start(member_id,
                                                             ride_id)}
    except Exception as e:
        raise _handle(e) from e


@router.post("/api/ride/driver/orders/{ride_id}/complete")
async def driver_complete_ride(
    ride_id: str,
    body: RideCompleteRequest = None,
    x_member_id: str = Header(default="", alias="X-Member-Id"),
):
    """行程结束 → AI 自动结算(计价/核券/拆分/结算单)→ settled"""
    member_id = _require_member(x_member_id)
    try:
        ride = await _dispatch_service.driver_complete(
            member_id, ride_id,
            duration_minutes=(body.durationMinutes if body else None),
            pricing_hour=(body.pricingHour if body else None),
            actual_km=(body.actualKm if body else None))
        return {"success": True, "ride": ride}
    except Exception as e:
        raise _handle(e) from e


@router.get("/api/ride/driver/orders")
async def driver_rides(
    status: str = Query(None, description="按状态过滤"),
    x_member_id: str = Header(default="", alias="X-Member-Id"),
):
    """司机我的行程"""
    member_id = _require_member(x_member_id)
    try:
        rides = await _dispatch_service.list_driver_rides(member_id,
                                                          status=status)
        return {"success": True, "total": len(rides), "rides": rides}
    except Exception as e:
        raise _handle(e) from e


@router.get("/api/ride/driver/settlements")
async def driver_settlements(
    x_member_id: str = Header(default="", alias="X-Member-Id"),
):
    """司机我的结算单"""
    member_id = _require_member(x_member_id)
    try:
        settlements = await _dispatch_service.list_driver_settlements(
            member_id)
        return {"success": True, "total": len(settlements),
                "settlements": settlements}
    except Exception as e:
        raise _handle(e) from e


# ============================================================
# 管理端(审查队列/司机池/行程/结算)
# ============================================================

@router.get("/api/ride/admin/applications")
async def list_applications(
    status: str = Query(None, description="approved/manual_review/rejected"),
    x_role: str = Header(default="", alias="X-Role"),
):
    """审查流水列表(可按状态过滤, manual_review 为复核队列)"""
    _require_admin(x_role)
    try:
        apps = await _gate_service.list_applications(status=status)
        return {"success": True, "total": len(apps),
                "status": status, "applications": apps}
    except Exception as e:
        raise _handle(e) from e


@router.post("/api/ride/admin/applications/{application_id}/decide")
async def decide_application(
    application_id: int,
    body: ApplicationDecideRequest,
    x_role: str = Header(default="", alias="X-Role"),
):
    """人工复核裁决(manual_review → approved 入池 / rejected)"""
    _require_admin(x_role)
    try:
        app = await _gate_service.decide(
            application_id, body.approve, body.reviewer, body.note)
        return {"success": True, "application": app}
    except Exception as e:
        raise _handle(e) from e


@router.get("/api/ride/admin/pool")
async def list_pool(
    track: str = Query(None, description="self/partner/platform"),
    status: str = Query(None, description="online/offline/suspended/revoked"),
    x_role: str = Header(default="", alias="X-Role"),
):
    """司机池列表(可按轨道/状态过滤)"""
    _require_admin(x_role)
    try:
        drivers = await _gate_service.list_pool(track=track, status=status)
        return {"success": True, "total": len(drivers), "track": track,
                "status": status, "drivers": drivers}
    except Exception as e:
        raise _handle(e) from e


@router.get("/api/ride/admin/overview")
async def admin_overview(
    x_role: str = Header(default="", alias="X-Role"),
):
    """司机池与审查概览(管理端看板)"""
    _require_admin(x_role)
    try:
        return await _gate_service.overview()
    except Exception as e:
        raise _handle(e) from e


@router.get("/api/ride/admin/rides")
async def admin_rides(
    status: str = Query(None, description="按行程状态过滤"),
    x_role: str = Header(default="", alias="X-Role"),
):
    """行程列表(管理端)"""
    _require_admin(x_role)
    try:
        rides = await _dispatch_service.admin_rides(status=status)
        return {"success": True, "total": len(rides), "status": status,
                "rides": rides}
    except Exception as e:
        raise _handle(e) from e


@router.get("/api/ride/admin/settlements")
async def admin_settlements(
    track: str = Query(None, description="self/partner/platform"),
    payout_status: str = Query(None, description="paid/aggregated"),
    x_role: str = Header(default="", alias="X-Role"),
):
    """结算单列表(管理端, 可按轨道/支付状态过滤)"""
    _require_admin(x_role)
    try:
        settlements = await _dispatch_service.admin_settlements(
            track=track, payout_status=payout_status)
        return {"success": True, "total": len(settlements),
                "settlements": settlements}
    except Exception as e:
        raise _handle(e) from e


# ============================================================
# 平台直发回调(合作平台服务间调用) + 安全监控(管理端)
# ============================================================

@router.post("/api/ride/partner/callback")
async def partner_callback(body: PartnerCallbackRequest):
    """平台直发回执(三态): accepted/started/completed/cancelled

    completed 事件携带 trace 摘要(actualKm/durationMinutes),
    触发 AI 结算(aggregated)与里程异常比对。
    """
    try:
        return await _dispatch_service.partner_callback(
            body.model_dump())
    except Exception as e:
        raise _handle(e) from e


@router.post("/api/ride/admin/safety/scan")
async def safety_scan(
    x_role: str = Header(default="", alias="X-Role"),
):
    """安全扫描: 进行中超时行程预警(>3h 未结束, 幂等)"""
    _require_admin(x_role)
    try:
        return await _safety_service.scan_active()
    except Exception as e:
        raise _handle(e) from e


@router.get("/api/ride/admin/risk-panel")
async def risk_panel(
    x_role: str = Header(default="", alias="X-Role"),
):
    """风险面板: POI 高频/行程超时/里程异常聚合(管理端)"""
    _require_admin(x_role)
    try:
        return await _safety_service.risk_panel()
    except Exception as e:
        raise _handle(e) from e


@router.post("/api/ride/admin/risk-events/{risk_id}/resolve")
async def resolve_risk_event(
    risk_id: int,
    body: RiskResolveRequest = None,
    x_role: str = Header(default="", alias="X-Role"),
):
    """处置风险事件(管理端)"""
    _require_admin(x_role)
    try:
        event = await _safety_service.resolve_event(
            risk_id, (body.note if body else ""))
        return {"success": True, "event": event}
    except Exception as e:
        raise _handle(e) from e


# ============================================================
# 双向评价(P3: 乘客/司机提交 + AI 审评)
# ============================================================

@router.post("/api/ride/orders/{ride_id}/review")
async def submit_review(
    ride_id: str,
    body: RideReviewRequest,
    x_member_id: str = Header(default="", alias="X-Member-Id"),
):
    """提交双向评价 → AI 审评(show/watch/fold)→ 司机评分回写"""
    member_id = _require_member(x_member_id)
    try:
        return await _review_service.submit(
            member_id, ride_id, body.direction, body.score,
            body.content)
    except Exception as e:
        raise _handle(e) from e


@router.get("/api/ride/orders/{ride_id}/reviews")
async def ride_reviews(
    ride_id: str,
    x_member_id: str = Header(default="", alias="X-Member-Id"),
):
    """行程的双向评价(乘客本人可见, fold 文本不外泄)"""
    member_id = _require_member(x_member_id)
    try:
        return await _review_service.get_ride_reviews(member_id, ride_id)
    except Exception as e:
        raise _handle(e) from e


@router.get("/api/ride/driver/reviews")
async def driver_reviews(
    action: str = Query(None, description="show/watch/fold 过滤"),
    x_member_id: str = Header(default="", alias="X-Member-Id"),
):
    """司机收到的评价(fold 文本不外泄)"""
    member_id = _require_member(x_member_id)
    try:
        reviews = await _review_service.driver_reviews(
            member_id, action=action)
        return {"success": True, "total": len(reviews),
                "reviews": reviews}
    except Exception as e:
        raise _handle(e) from e


@router.get("/api/ride/admin/reviews")
async def admin_reviews(
    action: str = Query(None, description="show/watch/fold 过滤"),
    direction: str = Query(None, description="评价方向过滤"),
    x_role: str = Header(default="", alias="X-Role"),
):
    """评价审评观察列表(管理端)"""
    _require_admin(x_role)
    try:
        reviews = await _review_service.admin_reviews(
            action=action, direction=direction)
        return {"success": True, "total": len(reviews),
                "reviews": reviews}
    except Exception as e:
        raise _handle(e) from e


@router.get("/api/ride/admin/review-stats")
async def admin_review_stats(
    x_role: str = Header(default="", alias="X-Role"),
):
    """评价审评统计(管理端看板: 处置动作/方向分布)"""
    _require_admin(x_role)
    try:
        return await _review_service.admin_fold_stats()
    except Exception as e:
        raise _handle(e) from e


# ============================================================
# 日结对账(P4: 三方对平 + 状态机, 管理端)
# ============================================================

@router.post("/api/ride/admin/reconciliation/start")
async def start_reconciliation(
    body: ReconStartRequest,
    x_role: str = Header(default="", alias="X-Role"),
):
    """生成日结对账单(本站结算 vs 平台账单 vs 券核销三方比对)"""
    _require_admin(x_role)
    try:
        return await _reconcile_service.generate(
            body.period, body.track, body.channelBills)
    except Exception as e:
        raise _handle(e) from e


@router.get("/api/ride/admin/reconciliation/{recon_no}")
async def get_reconciliation(
    recon_no: str,
    x_role: str = Header(default="", alias="X-Role"),
):
    """对账单详情(含差异明细)"""
    _require_admin(x_role)
    try:
        return {"success": True,
                "reconciliation":
                    await _reconcile_service.get(recon_no)}
    except Exception as e:
        raise _handle(e) from e


@router.get("/api/ride/admin/reconciliations")
async def list_reconciliations(
    track: str = Query(None, description="partner/platform"),
    status: str = Query(None, description="按状态过滤"),
    period: str = Query(None, description="按账期过滤"),
    x_role: str = Header(default="", alias="X-Role"),
):
    """对账单列表(可按轨道/状态/账期过滤)"""
    _require_admin(x_role)
    try:
        recons = await _reconcile_service.list_all(
            track=track, status=status, period=period)
        return {"success": True, "total": len(recons),
                "reconciliations": recons}
    except Exception as e:
        raise _handle(e) from e


@router.post("/api/ride/admin/reconciliation/{recon_no}/investigate")
async def investigate_reconciliation(
    recon_no: str,
    x_role: str = Header(default="", alias="X-Role"),
):
    """diff → investigating(介入调查差异)"""
    _require_admin(x_role)
    try:
        recon = await _reconcile_service.investigate(recon_no)
        return {"success": True, "reconciliation": recon}
    except Exception as e:
        raise _handle(e) from e


@router.post("/api/ride/admin/reconciliation/{recon_no}/resolve")
async def resolve_reconciliation(
    recon_no: str,
    body: ReconResolveRequest = None,
    x_role: str = Header(default="", alias="X-Role"),
):
    """investigating → resolved(差异处理完毕)"""
    _require_admin(x_role)
    try:
        recon = await _reconcile_service.resolve(
            recon_no, (body.resolution if body else ""))
        return {"success": True, "reconciliation": recon}
    except Exception as e:
        raise _handle(e) from e


@router.post("/api/ride/admin/reconciliation/{recon_no}/confirm")
async def confirm_reconciliation(
    recon_no: str,
    x_role: str = Header(default="", alias="X-Role"),
):
    """reconciling/resolved → confirmed(确认对账结果)"""
    _require_admin(x_role)
    try:
        recon = await _reconcile_service.confirm(recon_no)
        return {"success": True, "reconciliation": recon}
    except Exception as e:
        raise _handle(e) from e


@router.post("/api/ride/admin/reconciliation/{recon_no}/pay")
async def pay_reconciliation(
    recon_no: str,
    x_role: str = Header(default="", alias="X-Role"),
):
    """confirmed → paid(付款完成, 终态)"""
    _require_admin(x_role)
    try:
        recon = await _reconcile_service.pay(recon_no)
        return {"success": True, "reconciliation": recon}
    except Exception as e:
        raise _handle(e) from e


# ============================================================
# P4 学习回流(第22/23档案 Hedge 闭环, 管理端)
# ============================================================

@router.post("/api/ride/admin/learning/collect")
async def collect_learning(
    x_role: str = Header(default="", alias="X-Role"),
):
    """批量回流: 派单决策(settled+有评价)+审查决策(有服务数据)
    +评价审评决策(已标注)"""
    _require_admin(x_role)
    try:
        dispatch = await _dispatch_service.collect_learning_feedback()
        gate = await _gate_service.collect_application_feedback()
        review = await _review_service.collect_review_feedback()
        return {"success": True,
                "dispatch": {"submitted": dispatch["submitted"],
                             "skipped": dispatch["skipped"]},
                "gate": {"submitted": gate["submitted"],
                         "skipped": gate["skipped"]},
                "review": {"submitted": review["submitted"],
                           "skipped": review["skipped"]}}
    except Exception as e:
        raise _handle(e) from e


@router.post("/api/ride/admin/learning/run")
async def run_learning(
    x_role: str = Header(default="", alias="X-Role"),
):
    """触发三个档案的 Hedge 学习(反馈不足 409)"""
    _require_admin(x_role)
    results = {}
    for scorer_id, service in (
            ("ride_dispatch", _dispatch_service),
            ("driver_application_gate", _gate_service),
            ("ride_review", _review_service)):
        try:
            results[scorer_id] = await service.run_learning()
        except ValueError as exc:
            results[scorer_id] = {"success": False, "detail": str(exc)}
    return {"success": True, "results": results}


@router.get("/api/ride/admin/learning/status")
async def learning_status(
    x_role: str = Header(default="", alias="X-Role"),
):
    """学习回流状态(三档案 pending 反馈/幂等标记统计)"""
    _require_admin(x_role)
    try:
        from services.ai_learning_service import get_weights_view
        rides = await _dispatch_service.repo.list_rides(limit=2000)
        apps = await _gate_service.repo.list_applications(limit=1000)
        reviews = await _review_service.repo.list_reviews(limit=2000)
        return {
            "success": True,
            "dispatch": {
                "settled": sum(1 for r in rides
                              if r.get("status") == "settled"),
                "fed": sum(1 for r in rides if r.get("dispatchFed")),
            },
            "gate": {
                "approved": sum(1 for a in apps
                                if a.get("status") == "approved"),
                "fed": sum(1 for a in apps if a.get("appFed")),
            },
            "review": {
                "annotated": sum(1 for r in reviews
                                 if r.get("annotatedAction")),
                "fed": sum(1 for r in reviews if r.get("reviewFed")),
            },
            "weights": {
                "ride_dispatch":
                    await get_weights_view("ride_dispatch"),
                "driver_application_gate":
                    await get_weights_view("driver_application_gate"),
                "ride_review":
                    await get_weights_view("ride_review"),
            },
        }
    except Exception as e:
        raise _handle(e) from e


# ============================================================
# P5: 评价真值标注 + 营销 ROI 报表(管理端)
# ============================================================

@router.post("/api/ride/admin/reviews/{review_id}/annotate")
async def annotate_review(
    review_id: int,
    body: ReviewAnnotateRequest,
    x_role: str = Header(default="", alias="X-Role"),
):
    """管理端标注评价处置真值(ride_review 学习回流真值源)"""
    _require_admin(x_role)
    try:
        review = await _review_service.annotate(
            review_id, body.expectedAction, note=body.note)
        return {"success": True, "review": review}
    except Exception as e:
        raise _handle(e) from e


@router.get("/api/ride/admin/roi")
async def admin_roi(
    x_role: str = Header(default="", alias="X-Role"),
):
    """赠券营销 ROI: 发放/核销率/复购/成本/券均拉动(管理端看板)"""
    _require_admin(x_role)
    try:
        return await _coupon_service.admin_roi()
    except Exception as e:
        raise _handle(e) from e


def register_ride_routes(app) -> None:
    """注册41号路由(main.py startup 调用)"""
    app.include_router(router)
