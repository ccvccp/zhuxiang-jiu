"""跨模块业务路由:对接前端 5 个服务

    - /api/agent           代理商升级/降级(对应 main.js AgentUpgradeClient)
    - /api/checkout        订单结算提交(对应 checkout-service.js)
    - /api/inventory       库存扣减/回补(对应 inventory-service.js)
    - /api/warehouse       仓储入库/出库/盘点/库位/预测(对应 warehouse-service.js)
    - /api/agent-shipping  代理商区域认领(对应 agent-shipping-service.js)

异常映射:
    KeyError  → 404(资源不存在)
    ValueError → 409(资源冲突,如区域已被认领)
"""

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel as PydBaseModel, Field

from services import (
    AgentService, CheckoutService, InventoryService,
    WarehouseService, ShippingClaimService,
)

router = APIRouter()


# ============================================================
#  请求模型(允许任意字段透传,Mock 模式不做严格校验)
# ============================================================

class _GenericRequest(PydBaseModel):
    """通用请求体(允许任意字段透传)"""
    class Config:
        extra = "allow"


class AgentUpgradeRequest(PydBaseModel):
    agentId: Any = Field(..., description="代理商ID")
    fromLevel: str = Field("D", description="当前等级 D/C/B/A/S")
    toLevel: str = Field("C", description="目标等级")
    payAmount: float = Field(0, ge=0, description="支付金额")
    class Config:
        extra = "allow"


class AgentDowngradeRequest(PydBaseModel):
    agentId: Any
    fromLevel: str
    reason: str = "考核未达标"


class CheckoutSubmitRequest(PydBaseModel):
    items: list[Any] = Field(default_factory=list)
    consignee: Any = None
    payment: Any = None
    class Config:
        extra = "allow"


class InventoryRequest(PydBaseModel):
    productId: Any
    quantity: int = Field(default=1, ge=0)
    class Config:
        extra = "allow"


class WarehouseRequest(PydBaseModel):
    warehouseId: Any = None
    productId: Any = None
    class Config:
        extra = "allow"


class AgentShippingClaimRequest(PydBaseModel):
    agentId: Any
    region: str


# ============================================================
#  Service 单例(进程级共享,Repository 绑定同一 _mock_store)
# ============================================================

_agent_service = AgentService()
_checkout_service = CheckoutService()
_inventory_service = InventoryService()
_warehouse_service = WarehouseService()
_shipping_service = ShippingClaimService()


def _map_key_error(exc: KeyError) -> HTTPException:
    """KeyError → 404(detail 直接用异常消息,兼容旧契约)"""
    msg = str(exc) if str(exc) else "资源不存在"
    # 去掉 KeyError 默认的引号包裹(若 arg 是字符串)
    if msg.startswith("'") and msg.endswith("'"):
        msg = msg[1:-1]
    return HTTPException(status_code=404, detail=msg)


def _map_value_error(exc: ValueError) -> HTTPException:
    """ValueError → 409"""
    return HTTPException(status_code=409, detail=str(exc))


# ============================================================
#  代理商服务
# ============================================================

@router.post("/api/agent/upgrade", tags=["代理商服务"])
async def agent_upgrade(req: AgentUpgradeRequest):
    """代理商升级(对应前端 main.js AgentUpgradeClient.liveUpgrade)"""
    try:
        return await _agent_service.upgrade(req.agentId, req.toLevel, req.payAmount)
    except KeyError as e:
        raise _map_key_error(e) from e


@router.post("/api/agent/downgrade", tags=["代理商服务"])
async def agent_downgrade(req: AgentDowngradeRequest):
    """代理商降级(对应前端 main.js AgentUpgradeClient.liveDowngrade)"""
    try:
        return await _agent_service.downgrade(req.agentId, req.reason)
    except KeyError as e:
        raise _map_key_error(e) from e


# ============================================================
#  交易服务
# ============================================================

@router.post("/api/checkout/submit", tags=["交易服务"])
async def checkout_submit(req: CheckoutSubmitRequest):
    """订单结算提交(对应前端 checkout-service.js liveSubmit)"""
    return await _checkout_service.submit(req.items, req.consignee, req.payment)


# ============================================================
#  供应链服务
# ============================================================

@router.post("/api/inventory/deduct", tags=["供应链服务"])
async def inventory_deduct(req: InventoryRequest):
    """库存扣减(对应前端 inventory-service.js liveDeduct)"""
    try:
        return await _inventory_service.deduct(req.productId, req.quantity)
    except KeyError as e:
        raise _map_key_error(e) from e


@router.post("/api/inventory/restock", tags=["供应链服务"])
async def inventory_restock(req: InventoryRequest):
    """库存回补(对应前端 inventory-service.js liveRestock)"""
    try:
        return await _inventory_service.restock(req.productId, req.quantity)
    except KeyError as e:
        raise _map_key_error(e) from e


# ============================================================
#  仓储服务
# ============================================================

@router.post("/api/warehouse/inbound", tags=["仓储服务"])
async def warehouse_inbound(req: WarehouseRequest):
    """AI智能入库(对应前端 warehouse-service.js inbound)"""
    return await _warehouse_service.inbound(req.productId)


@router.post("/api/warehouse/outbound", tags=["仓储服务"])
async def warehouse_outbound(req: WarehouseRequest):
    """AI智能出库(对应前端 warehouse-service.js outbound)"""
    return await _warehouse_service.outbound(req.productId)


@router.post("/api/warehouse/stocktake", tags=["仓储服务"])
async def warehouse_stocktake(req: WarehouseRequest):
    """AI智能盘点(对应前端 warehouse-service.js stocktake)"""
    return await _warehouse_service.stocktake()


@router.post("/api/warehouse/slot-optimize", tags=["仓储服务"])
async def warehouse_slot_optimize(req: WarehouseRequest):
    """AI智能库位优化(对应前端 warehouse-service.js slotOptimize)"""
    return _warehouse_service.slot_optimize()


@router.get("/api/warehouse/forecast", tags=["仓储服务"])
async def warehouse_forecast(productId: str = None):
    """AI智能库存预测(对应前端 warehouse-service.js forecast)"""
    return _warehouse_service.forecast(productId)


# ============================================================
#  代理商区域认领
# ============================================================

@router.post("/api/agent-shipping/claim", tags=["代理商服务"])
async def agent_shipping_claim(req: AgentShippingClaimRequest):
    """代理商区域认领(对应前端 agent-shipping-service.js liveClaim)"""
    try:
        return await _shipping_service.claim(req.agentId, req.region)
    except KeyError as e:
        raise _map_key_error(e) from e
    except ValueError as e:
        raise _map_value_error(e) from e


@router.get("/api/agent-shipping/claims", tags=["代理商服务"])
async def agent_shipping_list_claims():
    """查询所有区域认领记录"""
    return await _shipping_service.list_claims()


def register_business_routes(app):
    """注册跨模块业务端点到 FastAPI app"""
    app.include_router(router)
