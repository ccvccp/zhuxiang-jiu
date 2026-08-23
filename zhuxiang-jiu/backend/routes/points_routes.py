"""会员积分管理模块路由(10 端点)

鉴权:
    - 用户端(7 接口): X-Member-Id 头标识当前会员(签到/返分/抵扣/退款/查询)
    - 管理端(1 接口): X-Role: admin 头(过期扫描)
    - 公开(2 接口): 账户/流水/将过期/统计查询(仅读)

异常映射(遵循项目约定):
    - KeyError → 404(账户不存在)
    - ValueError → 409(业务冲突: 重复签到/积分不足/超上限等)
    - 权限校验 → 401(未登录) / 403(无权操作)

端点分布:
    - 签到(2):     signin / signin-records
    - 返分(1):     earn-order
    - 抵扣(1):     deduct
    - 退款(1):     refund
    - 过期(2):     expire-run / expiring
    - 查询(3):     account / logs / stats
"""

from typing import Any, List, Optional

from fastapi import APIRouter, Header, HTTPException, Query
from pydantic import BaseModel as PydBaseModel, Field

from services import ai_feedback_hooks as ai_hooks
from services.points_service import PointsService


router = APIRouter()
_service = PointsService()


# ============================================================
# 鉴权与异常映射辅助(对齐 citystore/payment 风格)
# ============================================================

def _require_member_id(x_member_id: Optional[str]) -> str:
    """从 X-Member-Id 头提取会员ID, 缺失返回 401"""
    if not x_member_id:
        raise HTTPException(status_code=401, detail="未登录: 请提供 X-Member-Id 头")
    return x_member_id


def _require_admin(x_role: Optional[str]):
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

class SigninRequest(PydBaseModel):
    userId: int = Field(..., description="会员ID")


class EarnOrderRequest(PydBaseModel):
    userId: int = Field(..., description="会员ID")
    orderId: str = Field(..., description="订单号")
    orderAmount: float = Field(..., gt=0, description="订单金额(元)")
    memberLevel: int = Field(1, ge=1, le=5, description="会员等级(1-5)")


class DeductRequest(PydBaseModel):
    userId: int = Field(..., description="会员ID")
    orderId: str = Field(..., description="订单号")
    orderAmount: float = Field(..., gt=0, description="订单金额(元)")
    deductPoints: int = Field(..., ge=100, description="抵扣积分数(100的整数倍)")


class RefundRequest(PydBaseModel):
    userId: int = Field(..., description="会员ID")
    orderId: str = Field(..., description="订单号")
    refundPoints: int = Field(..., gt=0, description="扣回积分数")


# ============================================================
# P0 接口(10 个)
# ============================================================

# --- 签到 ---

@router.post("/api/points/signin", tags=["会员积分模块"])
async def signin(
    data: SigninRequest,
    x_member_id: str = Header(None, alias="X-Member-Id"),
):
    """每日签到(连续签到+宝箱奖励+幂等防重)"""
    _require_member_id(x_member_id)
    try:
        result = await _service.signin(data.userId)
        # v7.6 自动反馈: 积分发放 → 防薅羊毛观察评分+配对(正常发放期望 low)
        await ai_hooks.on_points_earned(
            str(data.userId), float(result.get("pointsEarned") or 0))
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.get("/api/points/signin/{user_id}", tags=["会员积分模块"])
async def get_signin_records(
    user_id: int,
    limit: int = Query(30, ge=1, le=365, description="查询条数"),
):
    """查询签到记录(按日期倒序)"""
    try:
        result = await _service.get_signin_records(user_id, limit)
        return {"success": True, "data": result, "count": len(result)}
    except Exception as e:
        _handle(e)


# --- 消费返分 ---

@router.post("/api/points/earn/order", tags=["会员积分模块"])
async def earn_order_points(
    data: EarnOrderRequest,
    x_member_id: str = Header(None, alias="X-Member-Id"),
):
    """订单消费返分(等级加成+每日/每月上限)"""
    _require_member_id(x_member_id)
    try:
        result = await _service.earn_order_points(
            user_id=data.userId,
            order_id=data.orderId,
            order_amount=data.orderAmount,
            member_level=data.memberLevel,
        )
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


# --- 积分抵现 ---

@router.post("/api/points/deduct", tags=["会员积分模块"])
async def deduct_points(
    data: DeductRequest,
    x_member_id: str = Header(None, alias="X-Member-Id"),
):
    """积分抵现(FIFO过期消耗+30%订单上限)"""
    _require_member_id(x_member_id)
    try:
        result = await _service.deduct_points(
            user_id=data.userId,
            order_id=data.orderId,
            order_amount=data.orderAmount,
            deduct_points=data.deductPoints,
        )
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


# --- 退款返还 ---

@router.post("/api/points/refund", tags=["会员积分模块"])
async def refund_points(
    data: RefundRequest,
    x_member_id: str = Header(None, alias="X-Member-Id"),
):
    """退款扣回已发放积分"""
    _require_member_id(x_member_id)
    try:
        result = await _service.refund_points(
            user_id=data.userId,
            order_id=data.orderId,
            refund_points=data.refundPoints,
        )
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


# --- 过期处理(静态路径优先) ---

@router.post("/api/points/expire/run", tags=["会员积分模块"])
async def run_expire_process(
    x_role: str = Header(None, alias="X-Role"),
):
    """执行积分过期扫描(管理员)"""
    _require_admin(x_role)
    try:
        result = await _service.run_expire_process()
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.get("/api/points/expiring/{user_id}", tags=["会员积分模块"])
async def get_expiring_points(
    user_id: int,
    days: int = Query(30, ge=1, le=365, description="查询N天内将过期"),
):
    """查询将过期积分(默认30天)"""
    try:
        result = await _service.get_expiring_points(user_id, days)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


# --- 查询接口(静态路径优先于动态路径) ---

@router.get("/api/points/account/{user_id}", tags=["会员积分模块"])
async def get_account(
    user_id: int,
):
    """查询积分账户(不存在则创建)"""
    try:
        result = await _service.get_account(user_id)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.get("/api/points/logs/{user_id}", tags=["会员积分模块"])
async def list_logs(
    user_id: int,
    source: str = Query(None, description="按来源筛选: checkin/order/refund/expire/deduct"),
    log_type: str = Query(None, description="按类型筛选: earn/spend/freeze/unfreeze"),
    limit: int = Query(50, ge=1, le=500, description="查询条数"),
):
    """查询积分流水(支持筛选)"""
    try:
        result = await _service.list_logs(user_id, source, log_type, limit)
        return {"success": True, "data": result, "count": len(result)}
    except Exception as e:
        _handle(e)


@router.get("/api/points/stats/{user_id}", tags=["会员积分模块"])
async def get_stats(
    user_id: int,
):
    """积分统计(按来源统计+签到统计)"""
    try:
        result = await _service.get_stats(user_id)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


def register_points_routes(app):
    """注册会员积分模块路由"""
    app.include_router(router)
