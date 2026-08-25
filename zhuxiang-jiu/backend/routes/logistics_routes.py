"""物流接口管理模块路由(18 端点)

鉴权:
    - 用户端(4 接口): X-Member-Id 头标识当前会员(下单/查询运单/轨迹)
    - 管理端(9 接口): X-Role: admin 头(订单列表/状态流转/关闭/对账/结算)
    - 公开(2 接口): 物流商回调(实际由签名/Token 鉴权, 此处简化)
    - 公开(3 接口): 查询接口(运单详情/轨迹查询/结算单列表)

异常映射(遵循项目约定):
    - KeyError → 404(资源不存在)
    - ValueError → 409(业务冲突: 状态非法/参数非法/重复下单等)
    - 权限校验 → 401(未登录) / 403(无权操作)

端点分布:
    - 物流下单(4):  create / detail / by-order / list
    - 状态流转(3):  update-status / close-failed / tracks
    - 物流回调(1):  track-callback
    - 月结对账(6):  start-settlement / settlement-detail / settlements /
                    pending-settlements / investigate / resolve
    - 结算确认(2):  confirm / pay
    - 运费估算(1):  estimate-fee
    - 状态机查询(1): status-flow
"""


from fastapi import APIRouter, Header, HTTPException, Query
from pydantic import BaseModel as PydBaseModel, Field

from services.logistics_service import (
    LogisticsService,
    SUPPORTED_ORDER_TYPES, SUPPORTED_SETTLE_MODES,
)
from repositories.logistics_repository import (
    ORDER_STATUS_NAMES, ORDER_STATUS_FLOW,
    SETTLE_STATUS_NAMES,
    CARRIER_SF, CARRIER_LLL, CARRIER_NAMES,
)


router = APIRouter()
_service = LogisticsService()


# ============================================================
# 鉴权与异常映射辅助(对齐 wallet/payment 风格)
# ============================================================

def _require_member_id(x_member_id: str | None) -> str:
    """从 X-Member-Id 头提取会员ID, 缺失返回 401"""
    if not x_member_id:
        raise HTTPException(status_code=401, detail="未登录: 请提供 X-Member-Id 头")
    return x_member_id


def _require_admin(x_role: str | None):
    """校验管理员权限, 失败返回 403"""
    if x_role != "admin":
        raise HTTPException(status_code=403, detail="需要管理员权限")


def _map_key_error(exc: KeyError) -> HTTPException:
    """KeyError → 404"""
    msg = str(exc) if str(exc) else "资源不存在"
    if msg.startswith("'") and msg.endswith("'"):
        msg = msg[1:-1]
    return HTTPException(status_code=404, detail=msg)


def _map_value_error(exc: ValueError) -> HTTPException:
    """ValueError → 409"""
    return HTTPException(status_code=409, detail=str(exc))


def _handle(exc: Exception):
    """统一异常映射"""
    if isinstance(exc, KeyError):
        raise _map_key_error(exc)
    if isinstance(exc, ValueError):
        raise _map_value_error(exc)
    raise HTTPException(status_code=500, detail=str(exc))


# ============================================================
# 请求模型
# ============================================================

class ContactInfo(PydBaseModel):
    name: str = Field(..., description="姓名")
    phone: str = Field(..., description="电话")
    address: str = Field(..., description="地址")


class ReceiverInfo(ContactInfo):
    province: str | None = Field(None, description="省份")
    city: str | None = Field(None, description="城市")


class CreateOrderRequest(PydBaseModel):
    orderId: str = Field(..., description="关联竹香酒订单号")
    orderType: str = Field(..., description="订单类型 retail/groupbuy/return")
    carrier: str = Field(..., description="物流商 SF/JD/LLL/DB/YT")
    serviceType: str = Field("standard", description="服务类型 standard/express")
    sender: ContactInfo
    receiver: ReceiverInfo
    weight: float = Field(..., gt=0, description="重量(kg)")
    pieceCount: int = Field(1, gt=0, description="件数")
    volume: float = Field(0.0, ge=0, description="体积(m³)")
    insuredValue: float = Field(0.0, ge=0, description="保价金额")
    settleMode: str = Field("monthly", description="结算模式 monthly/cash/prepaid")
    extraFee: float = Field(0.0, ge=0, description="附加费")
    discount: float = Field(1.0, gt=0, le=1.0, description="折扣率(0-1.0]")


class UpdateStatusRequest(PydBaseModel):
    status: str = Field(..., description="新状态")
    operator: str = Field("system", description="操作人")
    trackDesc: str | None = Field(None, description="轨迹描述")
    trackLocation: str | None = Field(None, description="轨迹所在城市")
    signerName: str | None = Field(None, description="签收人(签收时)")
    signType: str | None = Field("self", description="签收方式 self/agent/station")
    signPhoto: str | None = Field(None, description="签收照片URL")
    signLocation: str | None = Field(None, description="签收GPS")


class CloseFailedRequest(PydBaseModel):
    reason: str = Field("", description="关闭原因")


class TrackCallbackRequest(PydBaseModel):
    waybillNo: str = Field(..., description="运单号")
    trackStatus: str = Field(..., description="物流商原始状态")
    unifiedStatus: str = Field(..., description="统一状态")
    description: str = Field(..., description="轨迹描述")
    location: str = Field("", description="所在城市")
    operator: str = Field("carrier", description="操作人")
    trackTime: str | None = Field(None, description="轨迹时间")


class StartSettlementRequest(PydBaseModel):
    period: str = Field(..., description="账期 YYYY-MM")
    carrier: str = Field(..., description="物流商编码")
    channelOrders: list[dict] | None = Field(
        None, description="物流商对账明细 [{waybillNo, totalFee}]"
    )


class ResolveRequest(PydBaseModel):
    resolution: str = Field("", description="处理说明")


class EstimateFeeRequest(PydBaseModel):
    carrier: str = Field(..., description="物流商")
    serviceType: str = Field("standard", description="服务类型")
    weight: float = Field(..., gt=0, description="重量(kg)")
    pieceCount: int = Field(1, gt=0, description="件数")
    insuredValue: float = Field(0.0, ge=0, description="保价金额")
    extraFee: float = Field(0.0, ge=0, description="附加费")
    discount: float = Field(1.0, gt=0, le=1.0, description="折扣率")


# ============================================================
# 1. 物流下单(4 端点)
# ============================================================

@router.post("/api/logistics/order", tags=["物流接口管理"])
async def create_logistics_order(
    data: CreateOrderRequest,
    x_member_id: str = Header(..., alias="X-Member-Id"),
):
    """物流下单(幂等: 同一 orderId 只能有一个未关闭运单)"""
    _require_member_id(x_member_id)
    try:
        sender = data.sender.model_dump()
        receiver = data.receiver.model_dump()
        result = await _service.create_order(
            order_id=data.orderId,
            order_type=data.orderType,
            carrier=data.carrier,
            service_type=data.serviceType,
            sender=sender,
            receiver=receiver,
            weight=data.weight,
            piece_count=data.pieceCount,
            volume=data.volume,
            insured_value=data.insuredValue,
            settle_mode=data.settleMode,
            extra_fee=data.extraFee,
            discount=data.discount,
        )
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.get("/api/logistics/order/{waybill_no}", tags=["物流接口管理"])
async def get_logistics_order(waybill_no: str):
    """查询物流订单详情(公开)"""
    try:
        result = await _service.get_order(waybill_no)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.get("/api/logistics/order-by-order/{order_id}", tags=["物流接口管理"])
async def get_order_by_order_id(order_id: str):
    """按订单号查询物流单(可能不存在)"""
    result = await _service.get_order_by_order_id(order_id)
    if not result:
        return {"success": True, "data": None, "message": "该订单暂无物流单"}
    return {"success": True, "data": result}


@router.get("/api/logistics/orders", tags=["物流接口管理"])
async def list_logistics_orders(
    carrier: str | None = Query(None, description="物流商筛选"),
    status: str | None = Query(None, description="状态筛选"),
    order_type: str | None = Query(None, description="订单类型筛选"),
    limit: int = Query(50, ge=1, le=200, description="返回条数上限"),
    x_role: str | None = Header(None, alias="X-Role"),
):
    """物流订单列表(管理端)"""
    _require_admin(x_role)
    result = await _service.list_orders(carrier, status, order_type, limit)
    return {"success": True, "data": result, "total": len(result)}


# ============================================================
# 2. 状态流转(3 端点)
# ============================================================

@router.post("/api/logistics/order/{waybill_no}/status", tags=["物流接口管理"])
async def update_logistics_status(
    waybill_no: str,
    data: UpdateStatusRequest,
    x_role: str | None = Header(None, alias="X-Role"),
):
    """更新物流订单状态(管理端, 状态机校验 + 自动轨迹)"""
    _require_admin(x_role)
    try:
        sign_info = None
        if data.status == "signed":
            sign_info = {
                "signerName": data.signerName or "",
                "signType": data.signType or "self",
                "signPhoto": data.signPhoto or "",
                "signLocation": data.signLocation or "",
            }
        result = await _service.update_status(
            waybill_no=waybill_no,
            new_status=data.status,
            operator=data.operator,
            track_desc=data.trackDesc,
            track_location=data.trackLocation,
            sign_info=sign_info,
        )
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.post("/api/logistics/order/{waybill_no}/close", tags=["物流接口管理"])
async def close_failed_order(
    waybill_no: str,
    data: CloseFailedRequest,
    x_role: str | None = Header(None, alias="X-Role"),
):
    """关闭失败的运单(管理端, failed → returned)"""
    _require_admin(x_role)
    try:
        result = await _service.close_failed_order(waybill_no, data.reason)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.get("/api/logistics/order/{waybill_no}/tracks", tags=["物流接口管理"])
async def list_logistics_tracks(
    waybill_no: str,
    limit: int = Query(50, ge=1, le=200, description="返回条数上限"),
):
    """查询运单轨迹列表(公开)"""
    try:
        result = await _service.list_tracks(waybill_no, limit)
        return {"success": True, "data": result, "total": len(result)}
    except Exception as e:
        _handle(e)


# ============================================================
# 3. 物流回调(1 端点)
# ============================================================

@router.post("/api/logistics/callback/track", tags=["物流接口管理"])
async def track_callback(data: TrackCallbackRequest):
    """物流商轨迹回调(公开, 实际由签名鉴权)

    自动添加轨迹 + 尝试更新订单状态(非法流转忽略不报错)
    """
    try:
        result = await _service.add_track_callback(
            waybill_no=data.waybillNo,
            track_status=data.trackStatus,
            unified_status=data.unifiedStatus,
            description=data.description,
            location=data.location,
            operator=data.operator,
            track_time=data.trackTime,
        )
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


# ============================================================
# 4. 月结对账(6 端点)
# ============================================================

@router.post("/api/logistics/settlement/start", tags=["物流接口管理"])
async def start_settlement(
    data: StartSettlementRequest,
    x_role: str | None = Header(None, alias="X-Role"),
):
    """启动月结对账(管理端)"""
    _require_admin(x_role)
    try:
        result = await _service.start_settlement(
            period=data.period,
            carrier=data.carrier,
            channel_orders=data.channelOrders,
        )
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.get("/api/logistics/settlement/{settle_no}", tags=["物流接口管理"])
async def get_settlement(settle_no: str):
    """查询结算单详情(公开)"""
    try:
        result = await _service.get_settlement(settle_no)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.get("/api/logistics/settlements", tags=["物流接口管理"])
async def list_settlements(
    carrier: str | None = Query(None, description="物流商筛选"),
    period: str | None = Query(None, description="账期筛选"),
    status: str | None = Query(None, description="状态筛选"),
    limit: int = Query(50, ge=1, le=200),
):
    """结算单列表(公开)"""
    result = await _service.list_settlements(carrier, period, status, limit)
    return {"success": True, "data": result, "total": len(result)}


@router.get("/api/logistics/settlements/pending", tags=["物流接口管理"])
async def list_pending_settlements(
    limit: int = Query(50, ge=1, le=200),
    x_role: str | None = Header(None, alias="X-Role"),
):
    """待处理结算单列表(管理端)"""
    _require_admin(x_role)
    result = await _service.list_pending_settlements(limit)
    return {"success": True, "data": result, "total": len(result)}


@router.post("/api/logistics/settlement/{settle_no}/investigate", tags=["物流接口管理"])
async def investigate_diff(
    settle_no: str,
    x_role: str | None = Header(None, alias="X-Role"),
):
    """介入调查差异(管理端, diff → investigating)"""
    _require_admin(x_role)
    try:
        result = await _service.investigate_diff(settle_no)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.post("/api/logistics/settlement/{settle_no}/resolve", tags=["物流接口管理"])
async def resolve_settlement(
    settle_no: str,
    data: ResolveRequest,
    x_role: str | None = Header(None, alias="X-Role"),
):
    """处理完毕(管理端, investigating → resolved)"""
    _require_admin(x_role)
    try:
        result = await _service.resolve_settlement(settle_no, data.resolution)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


# ============================================================
# 5. 结算确认(2 端点)
# ============================================================

@router.post("/api/logistics/settlement/{settle_no}/confirm", tags=["物流接口管理"])
async def confirm_settlement(
    settle_no: str,
    x_role: str | None = Header(None, alias="X-Role"),
):
    """确认结算单(管理端, reconciling/resolved → confirmed)"""
    _require_admin(x_role)
    try:
        result = await _service.confirm_settlement(settle_no)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.post("/api/logistics/settlement/{settle_no}/pay", tags=["物流接口管理"])
async def pay_settlement(
    settle_no: str,
    x_role: str | None = Header(None, alias="X-Role"),
):
    """付款(管理端, confirmed → paid)"""
    _require_admin(x_role)
    try:
        result = await _service.pay_settlement(settle_no)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


# ============================================================
# 6. 运费估算(1 端点)
# ============================================================

@router.post("/api/logistics/estimate-fee", tags=["物流接口管理"])
async def estimate_fee(data: EstimateFeeRequest):
    """运费估算(公开, 不创建订单)"""
    from services.logistics_service import (
        _calc_insured_fee, _calc_package_fee, _calc_sf_base_fee,
        _calc_lll_base_fee, _calc_total_fee,
    )
    try:
        if data.carrier == CARRIER_SF:
            base_fee = _calc_sf_base_fee(data.serviceType, data.weight)
        elif data.carrier == CARRIER_LLL:
            base_fee = _calc_lll_base_fee(data.weight)
        else:
            base_fee = _calc_sf_base_fee("standard", data.weight)

        insured_fee = _calc_insured_fee(data.insuredValue)
        package_fee = _calc_package_fee(data.pieceCount)
        total_fee = _calc_total_fee(
            base_fee, insured_fee, package_fee, data.extraFee, data.discount
        )
        return {
            "success": True,
            "data": {
                "carrier": data.carrier,
                "carrierName": CARRIER_NAMES.get(data.carrier, data.carrier),
                "serviceType": data.serviceType,
                "baseFee": base_fee,
                "insuredFee": insured_fee,
                "packageFee": package_fee,
                "extraFee": data.extraFee,
                "discount": data.discount,
                "totalFee": total_fee,
            },
        }
    except Exception as e:
        _handle(e)


# ============================================================
# 7. 状态机查询(1 端点)
# ============================================================

@router.get("/api/logistics/status-flow", tags=["物流接口管理"])
async def get_status_flow():
    """查询物流状态机(公开, 便于前端展示状态流转图)"""
    return {
        "success": True,
        "data": {
            "orderStatus": {
                "names": ORDER_STATUS_NAMES,
                "flow": {k: list(v) for k, v in ORDER_STATUS_FLOW.items()},
            },
            "settleStatus": {
                "names": SETTLE_STATUS_NAMES,
            },
            "carriers": CARRIER_NAMES,
            "supportedOrderTypes": list(SUPPORTED_ORDER_TYPES),
            "supportedSettleModes": list(SUPPORTED_SETTLE_MODES),
        },
    }


# ============================================================
# 路由注册
# ============================================================

def register_logistics_routes(app):
    """注册物流接口管理模块路由"""
    app.include_router(router)
