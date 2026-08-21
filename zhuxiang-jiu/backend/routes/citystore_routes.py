"""市级网店模块路由(12 端点)

鉴权:
    - 用户端(7 接口): X-Member-Id 头标识当前会员(申请/查询/订单关联)
    - 管理端(5 接口): X-Role: admin 头(审核/状态流转/考核/待审核列表/统计)
    - 公开(1 接口): 可用城市查询

异常映射(遵循项目约定):
    - KeyError → 404(资源不存在)
    - ValueError → 409(业务冲突: 资格不符/城市被占/状态非法等)
    - 权限校验 → 401(未登录) / 403(无权操作)

端点分布:
    - 开店(3):      apply / detail / list
    - 审核(1):      audit
    - 状态流转(1):   status
    - 月度考核(3):   assessment-run / assessment-get / assessment-list
    - 订单关联(2):   orders-add / orders-list
    - 管理端(2):     pending / stats
"""

from typing import Any, List, Optional

from fastapi import APIRouter, Header, HTTPException, Query
from pydantic import BaseModel as PydBaseModel, Field

from services.citystore_service import CityStoreService
from repositories.citystore_repository import (
    # 网店状态
    STORE_STATUS_PENDING, STORE_STATUS_OPERATING, STORE_STATUS_WARNING,
    STORE_STATUS_SUSPENDED, STORE_STATUS_CANCELLED,
    STORE_STATUS_NAMES, STORE_STATUS_FLOW,
    # 考核资格状态
    QUAL_STATUS_NORMAL, QUAL_STATUS_WARNING, QUAL_STATUS_YELLOW_CARD, QUAL_STATUS_CANCELLED,
    # 阶梯折扣
    DISCOUNT_EXCELLENT, DISCOUNT_QUALIFIED, DISCOUNT_UNQUALIFIED,
    PURCHASE_TARGET, SALES_TARGET,
    # 销售渠道
    CHANNEL_LIVE, CHANNEL_MINIPROGRAM, CHANNEL_COMMUNITY, CHANNEL_H5, CHANNEL_DOUYIN,
)


router = APIRouter()
_service = CityStoreService()


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

class ApplyRequest(PydBaseModel):
    memberId: int = Field(..., description="会员ID")
    memberLevel: int = Field(..., description="会员等级(5 = SVIP)")
    storeName: str = Field(..., description="网店名称")
    cityCode: str = Field(..., description="地级市行政区划码")
    cityName: str = Field(..., description="城市名称")
    provinceCode: str = Field(..., description="省份码")
    provinceName: str = Field(..., description="省份名称")
    businessLicense: str = Field(..., description="营业执照号")
    foodLicense: str = Field(..., description="食品经营许可证号")
    taxRegNo: str = Field("", description="税务登记号")


class AuditRequest(PydBaseModel):
    auditor: str = Field(..., description="审核人")
    approved: bool = Field(..., description="是否通过")
    remark: str = Field("", description="审核备注")


class StatusRequest(PydBaseModel):
    status: int = Field(..., description="新状态: 1运营 2预警 3暂停 4取消")
    operator: str = Field("", description="操作人")


class AssessmentRequest(PydBaseModel):
    month: str = Field(..., description="考核月份(YYYY-MM)")


class AddOrderRequest(PydBaseModel):
    orderNo: str = Field(..., description="订单号")
    productId: str = Field(..., description="商品ID")
    productName: str = Field("", description="商品名称")
    quantity: int = Field(..., ge=1, description="数量")
    retailPrice: float = Field(..., ge=0, description="零售单价")
    totalAmount: float = Field(..., ge=0, description="订单总金额")
    customerPhone: str = Field("", description="消费者手机(脱敏)")
    deliveryCityCode: str = Field("", description="收货城市码")
    salesChannel: int = Field(CHANNEL_MINIPROGRAM, description="销售渠道: 1直播 2小程序 3社群 4H5 5抖音")


# ============================================================
# P0 接口(12 个)
# ============================================================

@router.post("/api/citystore/apply", tags=["市级网店模块"])
async def apply(
    data: ApplyRequest,
    x_member_id: str = Header(None, alias="X-Member-Id"),
):
    """申请开店(SVIP 资格 + 城市独占校验)"""
    _require_member_id(x_member_id)
    try:
        result = await _service.apply(
            member_id=data.memberId,
            member_level=data.memberLevel,
            store_name=data.storeName,
            city_code=data.cityCode,
            city_name=data.cityName,
            province_code=data.provinceCode,
            province_name=data.provinceName,
            business_license=data.businessLicense,
            food_license=data.foodLicense,
            tax_reg_no=data.taxRegNo,
        )
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.get("/api/citystore/cities/available", tags=["市级网店模块"])
async def list_available_cities(
    x_member_id: str = Header(None, alias="X-Member-Id"),
):
    """查询可用城市列表(未被独占的城市)"""
    _require_member_id(x_member_id)
    try:
        result = await _service.list_available_cities()
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.get("/api/citystore/list", tags=["市级网店模块"])
async def list_stores(
    x_member_id: str = Header(None, alias="X-Member-Id"),
    status: Optional[int] = Query(None, description="按状态筛选: 0待审核 1运营 2预警 3暂停 4取消"),
    limit: int = Query(50, ge=1, le=200, description="返回数量"),
):
    """我的网店列表"""
    user_id = _require_member_id(x_member_id)
    try:
        result = await _service.list_stores(
            member_id=int(user_id),
            status=status,
            limit=limit,
        )
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.get("/api/citystore/pending", tags=["市级网店模块"])
async def list_pending_stores(
    x_role: str = Header(None, alias="X-Role"),
    limit: int = Query(50, ge=1, le=200, description="返回数量"),
):
    """待审核网店列表(管理端)"""
    _require_admin(x_role)
    try:
        result = await _service.list_pending_stores(limit=limit)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.get("/api/citystore/stats", tags=["市级网店模块"])
async def get_stats(
    x_role: str = Header(None, alias="X-Role"),
):
    """网店统计(管理端)"""
    _require_admin(x_role)
    try:
        result = await _service.get_stats()
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.get("/api/citystore/{store_code}", tags=["市级网店模块"])
async def get_store_detail(
    store_code: str,
    x_member_id: str = Header(None, alias="X-Member-Id"),
):
    """网店详情"""
    _require_member_id(x_member_id)
    try:
        result = await _service.get_store_detail(store_code)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.post("/api/citystore/{store_code}/audit", tags=["市级网店模块"])
async def audit_store(
    store_code: str,
    data: AuditRequest,
    x_role: str = Header(None, alias="X-Role"),
):
    """审核开店申请(后台, 待审核 → 运营中/已取消)"""
    _require_admin(x_role)
    try:
        result = await _service.audit_store(
            store_code=store_code,
            auditor=data.auditor,
            approved=data.approved,
            remark=data.remark,
        )
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.put("/api/citystore/{store_code}/status", tags=["市级网店模块"])
async def update_status(
    store_code: str,
    data: StatusRequest,
    x_role: str = Header(None, alias="X-Role"),
):
    """更新网店状态(运营/预警/暂停/取消)"""
    _require_admin(x_role)
    try:
        result = await _service.update_status(
            store_code=store_code,
            new_status=data.status,
            operator=data.operator,
        )
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.post("/api/citystore/{store_code}/assessment", tags=["市级网店模块"])
async def run_assessment(
    store_code: str,
    data: AssessmentRequest,
    x_role: str = Header(None, alias="X-Role"),
):
    """触发月度考核(管理端)"""
    _require_admin(x_role)
    try:
        result = await _service.run_assessment(
            store_code=store_code,
            month=data.month,
        )
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.get("/api/citystore/{store_code}/assessment/{month}", tags=["市级网店模块"])
async def get_assessment(
    store_code: str,
    month: str,
    x_member_id: str = Header(None, alias="X-Member-Id"),
):
    """查询月度考核结果"""
    _require_member_id(x_member_id)
    try:
        result = await _service.get_assessment(store_code, month)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.get("/api/citystore/{store_code}/assessments", tags=["市级网店模块"])
async def list_assessments(
    store_code: str,
    x_member_id: str = Header(None, alias="X-Member-Id"),
):
    """查询网店所有考核记录"""
    _require_member_id(x_member_id)
    try:
        result = await _service.list_assessments(store_code)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.post("/api/citystore/{store_code}/orders", tags=["市级网店模块"])
async def add_order(
    store_code: str,
    data: AddOrderRequest,
    x_member_id: str = Header(None, alias="X-Member-Id"),
):
    """关联订单到网店(用于销售额统计)"""
    _require_member_id(x_member_id)
    try:
        result = await _service.add_order(
            store_code=store_code,
            order_no=data.orderNo,
            product_id=data.productId,
            product_name=data.productName,
            quantity=data.quantity,
            retail_price=data.retailPrice,
            total_amount=data.totalAmount,
            customer_phone=data.customerPhone,
            delivery_city_code=data.deliveryCityCode,
            sales_channel=data.salesChannel,
        )
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.get("/api/citystore/{store_code}/orders", tags=["市级网店模块"])
async def list_orders(
    store_code: str,
    x_member_id: str = Header(None, alias="X-Member-Id"),
    month: Optional[str] = Query(None, description="按月份筛选(YYYY-MM)"),
):
    """查询网店订单"""
    _require_member_id(x_member_id)
    try:
        result = await _service.list_orders(store_code, month)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


def register_citystore_routes(app):
    """注册市级网店模块路由"""
    app.include_router(router)
