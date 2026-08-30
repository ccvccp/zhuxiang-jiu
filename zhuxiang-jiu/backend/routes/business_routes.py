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
    """订单结算请求(对齐 checkout-service.js: items+会员等级+积分+券+支付+区域)

    兼容旧字段 consignee/payment(透传存单)。
    """
    items: list[Any] = Field(default_factory=list)
    memberLevel: str = Field("L1", description="会员等级 L1-L5")
    points: int = Field(0, ge=0, description="使用积分(竹叶)")
    couponCode: str | None = Field(None, description="优惠券码")
    paymentMethod: str = Field("wechat", description="支付方式")
    region: str | None = Field(None, description="收货区域(发货方路由)")
    consignee: Any = None
    payment: Any = None
    class Config:
        extra = "allow"


class InventoryRequest(PydBaseModel):
    """库存操作请求(多行契约 items + 旧单品兼容 productId/quantity)"""
    items: list[Any] | None = Field(None, description="[{id, name?, qty}]")
    reason: str | None = Field(None, description="操作原因")
    refNo: str | None = Field(None, description="关联单号")
    # 旧单品兼容字段
    productId: Any = None
    quantity: int | None = Field(None, description="单品数量(旧契约)")
    class Config:
        extra = "allow"


class WarehouseRequest(PydBaseModel):
    """仓储操作请求(对齐 warehouse-service.js 多行多仓契约)"""
    items: list[Any] = Field(default_factory=list, description="操作清单")
    warehouseId: Any = Field(1, description="仓库 ID")
    reason: str | None = Field(None, description="操作原因")
    refNo: str | None = Field(None, description="关联单号")
    # stocktake
    method: str = Field("drone_ai", description="盘点方式")
    # multi-transfer
    fromWarehouseId: Any = Field(None, description="调拨源仓")
    toWarehouseId: Any = Field(None, description="调拨目标仓")
    # loss
    lossType: str = Field("evaporation", description="损耗类型")
    # cross-dock
    carrierId: str = Field("LOGISTICS-06", description="承运商")
    # 旧单品兼容
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
    """订单结算提交(9 阶段事务, 对应前端 checkout-service.js liveSubmit)"""
    return await _checkout_service.submit(
        req.items, consignee=req.consignee, payment=req.payment,
        member_level=req.memberLevel, points=req.points,
        coupon_code=req.couponCode, payment_method=req.paymentMethod,
        region=req.region,
    )


# ============================================================
#  供应链服务
# ============================================================

def _normalize_inventory_items(req: InventoryRequest) -> list[dict]:
    """归一化库存清单: 优先 items 多行, 兼容旧单品 productId/quantity"""
    if req.items:
        return [i if isinstance(i, dict) else {"id": i} for i in req.items]
    if req.productId is not None:
        return [{"id": req.productId, "qty": req.quantity if req.quantity is not None else 1}]
    return []


@router.post("/api/inventory/deduct", tags=["供应链服务"])
async def inventory_deduct(req: InventoryRequest):
    """库存扣减(多行事务: 流水+预警+补偿回滚, 对应 inventory-service.js liveDeduct)"""
    items = _normalize_inventory_items(req)
    return await _inventory_service.deduct_lines(
        items, reason=req.reason or "库存扣减", ref_no=req.refNo)


@router.post("/api/inventory/restock", tags=["供应链服务"])
async def inventory_restock(req: InventoryRequest):
    """库存回补(多行事务: 流水, 对应 inventory-service.js liveRestock)"""
    items = _normalize_inventory_items(req)
    return await _inventory_service.restock_lines(
        items, reason=req.reason or "库存回补", ref_no=req.refNo)


# ============================================================
#  仓储服务
# ============================================================

@router.post("/api/warehouse/inbound", tags=["仓储服务"])
async def warehouse_inbound(req: WarehouseRequest):
    """AI智能入库(视觉验货+自动码垛+库位分配)"""
    items = req.items or ([{"id": req.productId, "qty": 1}] if req.productId else [])
    return await _warehouse_service.inbound(
        items, warehouse_id=req.warehouseId,
        reason=req.reason or "AI智能入库", ref_no=req.refNo)


@router.post("/api/warehouse/outbound", tags=["仓储服务"])
async def warehouse_outbound(req: WarehouseRequest):
    """AI智能出库(波次拣选, 库存不足事务回滚)"""
    items = req.items or ([{"id": req.productId, "qty": 1}] if req.productId else [])
    return await _warehouse_service.outbound(
        items, warehouse_id=req.warehouseId,
        reason=req.reason or "AI智能出库", ref_no=req.refNo)


@router.post("/api/warehouse/stocktake", tags=["仓储服务"])
async def warehouse_stocktake(req: WarehouseRequest):
    """AI智能盘点(以实盘数量覆盖系统库存, 盘盈/亏汇总)"""
    items = req.items or ([{"id": req.productId, "actualQty": 0}] if req.productId else [])
    return await _warehouse_service.stocktake(
        items, warehouse_id=req.warehouseId, method=req.method, ref_no=req.refNo)


@router.post("/api/warehouse/slot-optimize", tags=["仓储服务"])
async def warehouse_slot_optimize(req: WarehouseRequest):
    """AI智能库位优化(ABC 分类重排 hot/warm/cold 分区)"""
    return await _warehouse_service.slot_optimize(warehouse_id=req.warehouseId)


@router.post("/api/warehouse/multi-transfer", tags=["仓储服务"])
async def warehouse_multi_transfer(req: WarehouseRequest):
    """AI智能多仓调拨(源减目标增+双向流水)"""
    return await _warehouse_service.multi_transfer(
        req.items or [], req.fromWarehouseId or req.warehouseId,
        req.toWarehouseId, reason=req.reason or "AI智能多仓调拨",
        ref_no=req.refNo)


@router.post("/api/warehouse/loss", tags=["仓储服务"])
async def warehouse_loss(req: WarehouseRequest):
    """AI智能损耗管理(损耗登记+根因分析)"""
    return await _warehouse_service.loss(
        req.items or [], warehouse_id=req.warehouseId,
        loss_type=req.lossType, ref_no=req.refNo)


@router.post("/api/warehouse/cross-dock", tags=["仓储服务"])
async def warehouse_cross_dock(req: WarehouseRequest):
    """AI智能仓配一体(越库作业, 不入库直接分发)"""
    return await _warehouse_service.cross_dock(
        req.items or [], warehouse_id=req.warehouseId,
        carrier_id=req.carrierId, ref_no=req.refNo)


@router.get("/api/warehouse/forecast", tags=["仓储服务"])
async def warehouse_forecast(productId: str = None, warehouseId: int = 1,
                             horizonDays: int = 30):
    """AI智能库存预测(LSTM, 补货建议)"""
    return await _warehouse_service.forecast(
        productId or "ZX42-2026L07", warehouse_id=warehouseId,
        horizon_days=horizonDays)


@router.get("/api/warehouse/safety-stock", tags=["仓储服务"])
async def warehouse_safety_stock(productId: str = None, warehouseId: int = 1):
    """AI智能安全库存(动态安全库存模型+再订货点)"""
    return await _warehouse_service.safety_stock(
        productId or "ZX42-2026L07", warehouse_id=warehouseId)


@router.get("/api/warehouse/env-monitor", tags=["仓储服务"])
async def warehouse_env_monitor(warehouseId: int = 1):
    """AI智能温湿度监控(IoT 采集+异常预警+酒龄管理)"""
    return await _warehouse_service.env_monitor(warehouse_id=warehouseId)


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


@router.post("/api/agent-shipping/release", tags=["代理商服务"])
async def agent_shipping_release(req: AgentShippingClaimRequest):
    """释放区域认领(状态机: 已认领→已退出, 区域可再被认领)"""
    try:
        return await _shipping_service.release(req.agentId, req.region)
    except ValueError as e:
        raise _map_value_error(e) from e


@router.get("/api/agent-shipping/claims", tags=["代理商服务"])
async def agent_shipping_list_claims():
    """查询所有区域认领记录"""
    return await _shipping_service.list_claims()


def register_business_routes(app):
    """注册跨模块业务端点到 FastAPI app"""
    app.include_router(router)
