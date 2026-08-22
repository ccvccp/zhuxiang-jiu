"""老酒兑换及回收模块路由(12 端点)

鉴权:
    - 用户端: X-Member-Id 头标识当前会员(估价/申请/查询)
    - 管理端: X-Role: admin 头(审核/状态流转/完成/库存/统计)

端点分布:
    - 估价(2):     submit-valuation / get-valuation
    - 申请(3):     submit-application / review-application / list-applications
    - 兑换(2):     exchange-new-wine / complete-exchange
    - 回收(1):     recycle-for-cash
    - 查询(2):     exchanges / inventory
    - 管理(1):     transition-status
    - 统计(1):     stats
"""

from typing import Any, List, Optional

from fastapi import APIRouter, Header, HTTPException, Query
from pydantic import BaseModel as PydBaseModel, Field

from services.recycle_service import RecycleService
from repositories.recycle_repository import (
    TYPE_EXCHANGE, TYPE_RECYCLE,
    GRADE_A, GRADE_B, GRADE_C, GRADE_D,
    STATUS_PENDING, STATUS_VALUED, STATUS_APPROVED, STATUS_REJECTED,
)


router = APIRouter()
_service = RecycleService()


# ============================================================
# 鉴权与异常映射辅助
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


def _handle(exc: Exception):
    """统一异常映射"""
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

class ValuationRequest(PydBaseModel):
    userId: int = Field(..., description="会员ID")
    productId: str = Field(..., description="老酒产品ID")
    purchasePrice: float = Field(..., gt=0, description="购买原价")
    purchaseDate: str = Field(..., description="购买日期(YYYY-MM-DD)")
    conditionGrade: str = Field(GRADE_A, description="品质分级(A/B/C/D)")
    memberLevel: int = Field(1, ge=1, le=5, description="会员等级(1-5)")
    forExchange: bool = Field(True, description="是否用于兑换(影响等级加成)")


class ApplicationRequest(PydBaseModel):
    userId: int = Field(..., description="会员ID")
    type: str = Field(..., description="业务类型(exchange/recycle)")
    valuationIds: List[int] = Field(..., min_items=1, description="估价记录ID数组")
    newProductId: Optional[str] = Field(None, description="新酒产品ID(兑换时必填)")
    newProductPrice: Optional[float] = Field(None, gt=0, description="新酒价格(兑换时必填)")
    payoutMethod: Optional[str] = Field(None, description="打款方式(回收时填写)")
    payoutAccount: Optional[str] = Field(None, description="收款账户(回收时填写)")


class ReviewRequest(PydBaseModel):
    approved: bool = Field(..., description="是否通过")
    reviewer: str = Field("admin", description="审核人")
    remark: str = Field("", description="审核备注")


class ExchangeRequest(PydBaseModel):
    newProductId: str = Field(..., description="新酒产品ID")
    newProductPrice: float = Field(..., gt=0, description="新酒价格")
    diffPaymentMethod: str = Field("wechat", description="差价支付方式")


class RecycleRequest(PydBaseModel):
    payoutMethod: str = Field(..., description="打款方式")
    payoutAccount: str = Field(..., description="收款账户")


class TransitionRequest(PydBaseModel):
    newStatus: str = Field(..., description="目标状态")
    operator: str = Field("admin", description="操作人")


# ============================================================
# P0 接口(12 个)
# ============================================================

# --- 估价 ---

@router.post("/api/recycle/valuation/submit", tags=["老酒兑换回收模块"])
async def submit_valuation(
    data: ValuationRequest,
    x_member_id: str = Header(None, alias="X-Member-Id"),
):
    """提交老酒估价(增值率+品质分级+折现)"""
    _require_member_id(x_member_id)
    try:
        result = await _service.submit_valuation(
            user_id=data.userId,
            product_id=data.productId,
            purchase_price=data.purchasePrice,
            purchase_date=data.purchaseDate,
            condition_grade=data.conditionGrade,
            member_level=data.memberLevel,
            for_exchange=data.forExchange,
        )
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.get("/api/recycle/valuation/{val_id}", tags=["老酒兑换回收模块"])
async def get_valuation(val_id: int):
    """查询估价记录"""
    try:
        result = await _service.get_valuation(val_id)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


# --- 回收申请 ---

@router.post("/api/recycle/application/submit", tags=["老酒兑换回收模块"])
async def submit_application(
    data: ApplicationRequest,
    x_member_id: str = Header(None, alias="X-Member-Id"),
):
    """提交回收申请(兑换/回收)"""
    _require_member_id(x_member_id)
    try:
        result = await _service.submit_application(
            user_id=data.userId,
            app_type=data.type,
            valuation_ids=data.valuationIds,
            new_product_id=data.newProductId,
            new_product_price=data.newProductPrice,
            payout_method=data.payoutMethod,
            payout_account=data.payoutAccount,
        )
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.post("/api/recycle/application/{app_id}/review", tags=["老酒兑换回收模块"])
async def review_application(
    app_id: int,
    data: ReviewRequest,
    x_role: str = Header(None, alias="X-Role"),
):
    """审核回收申请(管理员)"""
    _require_admin(x_role)
    try:
        result = await _service.review_application(
            app_id=app_id,
            approved=data.approved,
            reviewer=data.reviewer,
            remark=data.remark,
        )
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.get("/api/recycle/applications", tags=["老酒兑换回收模块"])
async def list_applications(
    user_id: int = Query(None, description="会员ID筛选"),
    status: str = Query(None, description="状态筛选"),
    app_type: str = Query(None, description="类型筛选(exchange/recycle)"),
    limit: int = Query(50, ge=1, le=500, description="查询条数"),
):
    """查询回收申请列表"""
    try:
        result = await _service.list_applications(user_id, status, app_type, limit)
        return {"success": True, "data": result, "count": len(result)}
    except Exception as e:
        _handle(e)


# --- 兑换新酒 ---

@router.post("/api/recycle/application/{app_id}/exchange", tags=["老酒兑换回收模块"])
async def exchange_new_wine(
    app_id: int,
    data: ExchangeRequest,
    x_member_id: str = Header(None, alias="X-Member-Id"),
):
    """兑换新酒(老酒价值抵扣+差价处理)"""
    _require_member_id(x_member_id)
    try:
        result = await _service.exchange_new_wine(
            app_id=app_id,
            new_product_id=data.newProductId,
            new_product_price=data.newProductPrice,
            diff_payment_method=data.diffPaymentMethod,
        )
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.post("/api/recycle/exchange/{ex_id}/complete", tags=["老酒兑换回收模块"])
async def complete_exchange(
    ex_id: int,
    x_role: str = Header(None, alias="X-Role"),
):
    """完成兑换/回收(老酒入库+状态完成, 管理员)"""
    _require_admin(x_role)
    try:
        result = await _service.complete_exchange(ex_id)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


# --- 折现回收 ---

@router.post("/api/recycle/application/{app_id}/recycle", tags=["老酒兑换回收模块"])
async def recycle_for_cash(
    app_id: int,
    data: RecycleRequest,
    x_member_id: str = Header(None, alias="X-Member-Id"),
):
    """折现回收(老酒价值×80%+个税扣除)"""
    _require_member_id(x_member_id)
    try:
        result = await _service.recycle_for_cash(
            app_id=app_id,
            payout_method=data.payoutMethod,
            payout_account=data.payoutAccount,
        )
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


# --- 查询接口 ---

@router.get("/api/recycle/exchanges", tags=["老酒兑换回收模块"])
async def list_exchanges(
    user_id: int = Query(None, description="会员ID筛选"),
    ex_type: str = Query(None, description="类型筛选(exchange/recycle)"),
    limit: int = Query(50, ge=1, le=500, description="查询条数"),
):
    """查询兑换记录列表"""
    try:
        result = await _service.list_exchanges(user_id, ex_type, limit)
        return {"success": True, "data": result, "count": len(result)}
    except Exception as e:
        _handle(e)


@router.get("/api/recycle/inventory", tags=["老酒兑换回收模块"])
async def get_inventory(
    product_id: str = Query(None, description="产品ID筛选"),
    x_role: str = Header(None, alias="X-Role"),
):
    """查询回收库存(管理员)"""
    _require_admin(x_role)
    try:
        result = await _service.get_inventory(product_id)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


# --- 状态流转(管理员) ---

@router.post("/api/recycle/application/{app_id}/transition", tags=["老酒兑换回收模块"])
async def transition_status(
    app_id: int,
    data: TransitionRequest,
    x_role: str = Header(None, alias="X-Role"),
):
    """申请状态流转(管理员)"""
    _require_admin(x_role)
    try:
        result = await _service.transition_status(app_id, data.newStatus, data.operator)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


# --- 统计 ---

@router.get("/api/recycle/stats", tags=["老酒兑换回收模块"])
async def get_stats(
    user_id: int = Query(None, description="会员ID筛选"),
    x_role: str = Header(None, alias="X-Role"),
):
    """回收统计(管理员或按会员)"""
    if user_id is None:
        _require_admin(x_role)
    try:
        result = await _service.get_stats(user_id)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


def register_recycle_routes(app):
    """注册老酒兑换回收模块路由"""
    app.include_router(router)
