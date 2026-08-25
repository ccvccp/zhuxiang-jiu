"""团购模块路由(10 端点)

鉴权:
    - 用户端(7 接口): X-Member-Id 头标识当前会员(产品列表/计算/申请/查询/取消)
    - 管理端(2 接口): X-Role: admin 头(审核/待审核列表/统计)
    - 公开(1 接口): 阶梯折扣表查询

异常映射(遵循项目约定):
    - KeyError → 404(资源不存在)
    - ValueError → 409(业务冲突: 资格不符/门槛不足/状态非法等)
    - 权限校验 → 401(未登录) / 403(无权操作)

端点分布:
    - 团购产品(1):  products
    - 阶梯折扣(1):  tiers
    - 团购计算(1):  calculate
    - 团购申请(1):  apply
    - 订单查询(2):  list / detail
    - 订单操作(2):  audit / cancel
    - 管理端(2):    pending / stats
"""

from typing import List, Optional

from fastapi import APIRouter, Header, HTTPException, Query
from pydantic import BaseModel as PydBaseModel, Field

from services.groupbuy_service import GroupBuyService
from repositories.groupbuy_repository import (
    # 订单状态
    AUDIT_LEVEL_STAFF,
)


router = APIRouter()
_service = GroupBuyService()


# ============================================================
# 鉴权与异常映射辅助(对齐 wallet/payment 风格)
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

class CalcItem(PydBaseModel):
    productId: str = Field(..., description="产品ID")
    quantity: int = Field(..., ge=1, description="数量")


class CalcRequest(PydBaseModel):
    items: List[CalcItem] = Field(..., description="产品列表")


class ApplyItem(CalcItem):
    pass


class ApplyRequest(PydBaseModel):
    userId: int = Field(..., description="用户ID")
    userLevel: int = Field(..., description="会员等级(5 = SVIP)")
    groupType: str = Field(..., description="团购类型: enterprise/wedding/festival/custom")
    items: List[ApplyItem] = Field(..., description="产品列表")
    purpose: str = Field("", description="用途说明")
    customNeeds: Optional[dict] = Field(None, description="定制需求(JSON)")
    invoiceInfo: Optional[dict] = Field(None, description="发票信息(JSON)")
    addresses: Optional[list] = Field(None, description="收货地址(JSON 数组)")


class AuditRequest(PydBaseModel):
    auditor: str = Field(..., description="审核人")
    auditResult: str = Field(..., description="审核结果: approved/rejected")
    auditRemark: str = Field("", description="审核备注")
    auditLevel: str = Field(AUDIT_LEVEL_STAFF, description="审批层级: staff/supervisor/director/general_manager")


class CancelRequest(PydBaseModel):
    userId: int = Field(..., description="取消人ID(需为订单所有者)")
    reason: str = Field("", description="取消原因")


# ============================================================
# P0 接口(8 个)
# ============================================================

@router.get("/api/groupbuy/products", tags=["团购模块"])
async def list_products(
    x_member_id: str = Header(None, alias="X-Member-Id"),
):
    """获取可团购产品列表"""
    _require_member_id(x_member_id)
    try:
        result = await _service.list_products()
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.get("/api/groupbuy/tiers", tags=["团购模块"])
async def get_tiers():
    """阶梯折扣表查询(公开接口)"""
    try:
        result = await _service.get_tiers()
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.post("/api/groupbuy/calculate", tags=["团购模块"])
async def calculate_price(
    data: CalcRequest,
    x_member_id: str = Header(None, alias="X-Member-Id"),
):
    """计算团购价(输入产品+数量, 输出阶梯/折扣/团购价)"""
    _require_member_id(x_member_id)
    try:
        items = [item.model_dump() for item in data.items]
        result = await _service.calculate_price(items)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.post("/api/groupbuy/apply", tags=["团购模块"])
async def apply(
    data: ApplyRequest,
    x_member_id: str = Header(None, alias="X-Member-Id"),
):
    """提交团购申请(含资格校验 + 门槛校验 + 频次限制)"""
    _require_member_id(x_member_id)
    try:
        items = [item.model_dump() for item in data.items]
        result = await _service.apply(
            user_id=data.userId,
            user_level=data.userLevel,
            group_type=data.groupType,
            items=items,
            purpose=data.purpose,
            custom_needs=data.customNeeds,
            invoice_info=data.invoiceInfo,
            addresses=data.addresses,
        )
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.get("/api/groupbuy/list", tags=["团购模块"])
async def list_orders(
    x_member_id: str = Header(None, alias="X-Member-Id"),
    status: Optional[str] = Query(None, description="按状态筛选"),
    limit: int = Query(50, ge=1, le=200, description="返回数量"),
):
    """我的团购订单列表"""
    user_id = _require_member_id(x_member_id)
    try:
        result = await _service.list_orders(
            user_id=int(user_id),
            status=status,
            limit=limit,
        )
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.get("/api/groupbuy/{order_no}", tags=["团购模块"])
async def get_order_detail(
    order_no: str,
    x_member_id: str = Header(None, alias="X-Member-Id"),
):
    """团购订单详情(含明细 + 审核流水)"""
    _require_member_id(x_member_id)
    try:
        result = await _service.get_order_detail(order_no)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.post("/api/groupbuy/{order_no}/audit", tags=["团购模块"])
async def audit_order(
    order_no: str,
    data: AuditRequest,
    x_role: str = Header(None, alias="X-Role"),
):
    """审核团购申请(后台, 待审核 → 审核通过/已驳回)"""
    _require_admin(x_role)
    try:
        result = await _service.audit_order(
            order_no=order_no,
            auditor=data.auditor,
            audit_result=data.auditResult,
            audit_remark=data.auditRemark,
            audit_level=data.auditLevel,
        )
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.put("/api/groupbuy/{order_no}/cancel", tags=["团购模块"])
async def cancel_order(
    order_no: str,
    data: CancelRequest,
    x_member_id: str = Header(None, alias="X-Member-Id"),
):
    """取消团购申请(仅活跃状态可取消)"""
    _require_member_id(x_member_id)
    try:
        result = await _service.cancel_order(
            order_no=order_no,
            user_id=data.userId,
            reason=data.reason,
        )
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


# ============================================================
# 管理端接口(2 个)
# ============================================================

@router.get("/api/groupbuy/pending", tags=["团购模块"])
async def list_pending_orders(
    x_role: str = Header(None, alias="X-Role"),
    limit: int = Query(50, ge=1, le=200, description="返回数量"),
):
    """待审核订单列表(管理端)"""
    _require_admin(x_role)
    try:
        result = await _service.list_pending_orders(limit=limit)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.get("/api/groupbuy/stats", tags=["团购模块"])
async def get_stats(
    x_role: str = Header(None, alias="X-Role"),
):
    """团购统计(管理端)"""
    _require_admin(x_role)
    try:
        result = await _service.get_stats()
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


def register_groupbuy_routes(app):
    """注册团购模块路由"""
    app.include_router(router)
