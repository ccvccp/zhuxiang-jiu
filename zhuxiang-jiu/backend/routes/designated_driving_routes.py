"""免费代驾域路由(10 端点, P2: 合作商模块·第三章)

设计对齐: 酒店酒吧会所合作商模块设计文档 3.2.3 核心 API 列表
    免费代驾 = 合作商场所消费≥1瓶竹奕酒 → 30公里内本站代付(上限¥200/次)

鉴权:
    - 用户端(8 接口): X-Member-Id 头(下单/查询/取消/评价/记录/代付/消费验证)
    - 公开(2 接口): 费用预估 / 合作商查询(仅读)

异常映射(遵循项目约定):
    - KeyError → 404(凭证/订单/合作商不存在)
    - ValueError → 409(距离超限/凭证失效/状态非法等)
    - 权限校验 → 401(未登录)

端点分布:
    - 下单链(3):    order / order/{id} / order/{id}/cancel
    - 费用(1):      fee/estimate(纯计算, 公开)
    - 状态(1):      order/{id}/status
    - 评价(1):      order/{id}/rate
    - 代付(1):      payment(本站代付)
    - 记录(1):      records
    - 凭证(1):      consumption/verify
    - 合作商(1):    partner/{id}(公开)
"""


from fastapi import APIRouter, Header, HTTPException, Query
from pydantic import BaseModel as PydBaseModel, Field

from services.designated_driving_service import (
    DesignatedDrivingService, estimate_fare,
)


router = APIRouter()
_service = DesignatedDrivingService()


# ============================================================
# 鉴权与异常映射辅助(对齐 venue/points 风格)
# ============================================================

def _require_member_id(x_member_id: str | None) -> int:
    """从 X-Member-Id 头提取会员ID, 缺失 401 / 非数字 400"""
    if not x_member_id:
        raise HTTPException(status_code=401,
                            detail="未登录: 请提供 X-Member-Id 头")
    try:
        return int(x_member_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="X-Member-Id 须为数字会员ID")


def _handle(exc: Exception):
    """统一异常映射: KeyError→404 / ValueError→409 / 其它→500"""
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

class ConsumptionVerifyRequest(PydBaseModel):
    partnerId: int = Field(..., description="合作商ID(消费场所)")
    bottles: int = Field(..., ge=1, description="消费竹奕酒数量(瓶, ≥1 触发)")
    consumptionRef: str = Field("", max_length=64, description="消费凭证号(合作商订单号等)")


class DDCallRequest(PydBaseModel):
    verificationId: int = Field(..., description="消费验证凭证ID")
    pickupAddress: str = Field(..., min_length=2, max_length=120, description="上车地点")
    dropoffAddress: str = Field(..., min_length=2, max_length=120, description="目的地")
    distanceKm: float = Field(..., gt=0, le=200, description="预估距离(公里)")
    weather: str = Field("normal", description="天气: normal/rain/snow/wind")
    waitMinutes: int = Field(0, ge=0, le=240, description="预估等待(分钟)")


class DDPayRequest(PydBaseModel):
    orderId: int = Field(..., description="代驾订单ID")
    finalFee: float | None = Field(None, ge=0, description="实际费用(缺省按预估)")
    waitMinutes: int | None = Field(None, ge=0, le=240, description="实际等待分钟(重算用)")


class DDCancelRequest(PydBaseModel):
    reason: str = Field("", max_length=200, description="取消原因")


class DDRateRequest(PydBaseModel):
    rating: int = Field(..., ge=1, le=5, description="满意度 1-5 星")


class FeeEstimateRequest(PydBaseModel):
    distanceKm: float = Field(..., gt=0, le=200, description="距离(公里)")
    hour: int | None = Field(None, ge=0, le=23, description="出发小时(0-23, 缺省当前)")
    weather: str = Field("normal", description="天气: normal/rain/snow/wind")
    holiday: bool = Field(False, description="是否法定节假日")
    waitMinutes: int = Field(0, ge=0, le=240, description="预估等待(分钟)")


# ============================================================
# 1. 消费验证(免费代驾资格)
# ============================================================

@router.post("/api/venue/designated-driving/consumption/verify",
             tags=["合作商模块-免费代驾"])
async def verify_consumption(
    data: ConsumptionVerifyRequest,
    x_member_id: str = Header(None, alias="X-Member-Id"),
):
    """验证合作商场所消费(≥1瓶竹奕酒 → 发 24h 消费凭证)"""
    user_id = _require_member_id(x_member_id)
    try:
        result = await _service.verify_consumption(
            user_id, data.partnerId, data.bottles,
            consumption_ref=data.consumptionRef)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


# ============================================================
# 2. 代驾下单 / 查询 / 取消
# ============================================================

@router.post("/api/venue/designated-driving/order",
             tags=["合作商模块-免费代驾"])
async def create_order(
    data: DDCallRequest,
    x_member_id: str = Header(None, alias="X-Member-Id"),
):
    """创建代驾订单(凭证核销+距离门槛≤30km+费用预估)"""
    user_id = _require_member_id(x_member_id)
    try:
        result = await _service.create_order(
            user_id, data.verificationId,
            data.pickupAddress, data.dropoffAddress,
            data.distanceKm, weather=data.weather,
            wait_minutes=data.waitMinutes)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.get("/api/venue/designated-driving/order/{order_id}",
            tags=["合作商模块-免费代驾"])
async def get_order(order_id: int):
    """查询代驾订单(含费用明细)"""
    try:
        result = await _service.get_order(order_id)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.post("/api/venue/designated-driving/order/{order_id}/cancel",
             tags=["合作商模块-免费代驾"])
async def cancel_order(
    order_id: int,
    data: DDCancelRequest = None,
    x_member_id: str = Header(None, alias="X-Member-Id"),
):
    """取消代驾订单(仅行程开始前, 消费凭证退回)"""
    user_id = _require_member_id(x_member_id)
    try:
        result = await _service.cancel_order(
            order_id, user_id,
            reason=(data.reason if data else ""))
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


# ============================================================
# 3. 费用预估(公开纯计算)
# ============================================================

@router.post("/api/venue/designated-driving/fee/estimate",
             tags=["合作商模块-免费代驾"])
async def fee_estimate(data: FeeEstimateRequest):
    """代驾费用预估(设计文档 3.1.2 计价规则, 含代付拆分预览)"""
    try:
        result = estimate_fare(
            data.distanceKm, hour=data.hour,
            weather=data.weather, holiday=data.holiday,
            wait_minutes=data.waitMinutes)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


# ============================================================
# 4. 状态查询 / 评价
# ============================================================

@router.get("/api/venue/designated-driving/order/{order_id}/status",
            tags=["合作商模块-免费代驾"])
async def get_order_status(order_id: int):
    """查询代驾订单状态(轻量)"""
    try:
        result = await _service.get_order_status(order_id)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.post("/api/venue/designated-driving/order/{order_id}/rate",
             tags=["合作商模块-免费代驾"])
async def rate_order(
    order_id: int,
    data: DDRateRequest,
    x_member_id: str = Header(None, alias="X-Member-Id"),
):
    """评价代驾服务(1-5 星, 已代付订单)"""
    user_id = _require_member_id(x_member_id)
    try:
        result = await _service.rate_order(order_id, user_id, data.rating)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


# ============================================================
# 5. 本站代付 / 记录 / 合作商查询
# ============================================================

@router.post("/api/venue/designated-driving/payment",
             tags=["合作商模块-免费代驾"])
async def pay_order(
    data: DDPayRequest,
    x_member_id: str = Header(None, alias="X-Member-Id"),
):
    """本站代付代驾费(≤¥200 全额代付, 超出部分用户自付)"""
    _require_member_id(x_member_id)
    try:
        result = await _service.pay_order(
            data.orderId, final_fee=data.finalFee,
            wait_minutes=data.waitMinutes)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.get("/api/venue/designated-driving/records",
            tags=["合作商模块-免费代驾"])
async def list_records(
    x_member_id: str = Header(None, alias="X-Member-Id"),
    limit: int = Query(50, ge=1, le=200, description="查询条数"),
):
    """会员代驾记录(按创建时间倒序)"""
    user_id = _require_member_id(x_member_id)
    try:
        result = await _service.list_records(user_id, limit)
        return {"success": True, "data": result, "count": len(result)}
    except Exception as e:
        _handle(e)


@router.get("/api/venue/designated-driving/partner/{partner_id}",
            tags=["合作商模块-免费代驾"])
async def get_partner(partner_id: int):
    """查询合作商信息(免费代驾视角, 公开)"""
    try:
        result = await _service.get_partner(partner_id)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


def register_designated_driving_routes(app):
    """注册免费代驾域路由"""
    app.include_router(router)
