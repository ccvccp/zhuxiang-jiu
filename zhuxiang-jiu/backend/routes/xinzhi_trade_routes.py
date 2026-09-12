"""68号 P8+P9·信值·臻选交易路由(购物车+下单+支付+结算)

端点(18, 前缀 /api/xinzhi/*——平台化升级方案 §三):
    POST /api/xinzhi/cart/add               加购(快照=当时试算)*
    POST /api/xinzhi/cart/update           改量*
    POST /api/xinzhi/cart/remove           移除*
    GET  /api/xinzhi/cart/mine             我的购物车*
    POST /api/xinzhi/cart/checkout-preview  结算预览(实时重算)*
    POST /api/xinzhi/order/create          下单(年龄门+库存预扣)*
    POST /api/xinzhi/order/{id}/cancel     取消(库存回补)*
    GET  /api/xinzhi/order/mine            我的臻选订单*
    GET  /api/xinzhi/order/{id}            订单详情*
    POST /api/xinzhi/order/{id}/pay        支付三通道*
    POST /api/xinzhi/order/{id}/ship       发货(商家侧)*
    POST /api/xinzhi/order/{id}/confirm    确认收货*
    POST /api/xinzhi/order/{id}/review     评价(信值回流留痕)*
    POST /api/xinzhi/settlement/run        T+1 分账执行(admin/调度)
    GET  /api/xinzhi/settlement/mine       商家结算单列表
    GET  /api/xinzhi/settlement/{id}       结算单详情
    POST /api/xinzhi/settlement/{id}/reverse  冲正(admin)
    GET  /api/xinzhi/settlements           结算单总览(admin)

    * 消费者侧 X-Member-Id(会员本人域, 68号既有口径);
      商家侧(ship/settlement/mine)X-Member-Id+店铺归属;
      管理侧 X-Role: admin(43/44号同款)。

异常映射(项目约定): KeyError→404 / ValueError→409 /
    PermissionError→403(越权) / 其余→500。

灰度说明: 本期资金链不接入 XINZHI_MODE 决策面门槛——
    支付/结算是用户确认+幂等事务域(对齐主站 order pay
    语义), 灰度开关由主线注册时统一定夺。
"""

from typing import Annotated

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel as PydBaseModel, Field

router = APIRouter()


def _member_id(x_member_id: str | None) -> int:
    """X-Member-Id 解析(消费者/商家侧鉴权锚)"""
    if not (x_member_id or "").strip():
        raise HTTPException(status_code=401,
                            detail="需要登录(X-Member-Id)")
    try:
        return int(x_member_id)
    except ValueError:
        raise HTTPException(status_code=401,
                            detail="X-Member-Id 非法") from None


def _require_admin(x_role: str | None) -> None:
    """管理侧鉴权(X-Role: admin——43/44号同款口径)"""
    if x_role != "admin":
        raise HTTPException(status_code=403,
                            detail="需要管理员权限")


def _handle(e: Exception):
    """统一异常映射(越权 PermissionError→403)"""
    if isinstance(e, KeyError):
        raise HTTPException(status_code=404, detail=str(e))
    if isinstance(e, ValueError):
        raise HTTPException(status_code=409, detail=str(e))
    if isinstance(e, PermissionError):
        raise HTTPException(status_code=403, detail=str(e))
    raise HTTPException(status_code=500, detail=str(e))


def _cart_service():
    from services.xinzhi_cart_service import XinzhiCartService
    return XinzhiCartService()


def _settle_service():
    from services.xinzhi_settle_service import (
        XinzhiSettleService,
    )
    return XinzhiSettleService()


class CartItemRequest(PydBaseModel):
    """加购/改量请求"""
    listingId: int = Field(..., description="铺货商品 ID")
    quantity: int = Field(1, description="数量 1-99")


class CartRemoveRequest(PydBaseModel):
    """移除请求"""
    listingId: int = Field(..., description="铺货商品 ID")


class OrderCreateRequest(PydBaseModel):
    """下单请求(items 空=购物车全量)"""
    items: list | None = Field(
        None, description="指定条目 [{listingId, quantity}]")
    address: dict = Field(..., description="收货地址")
    remark: str = Field("", description="备注(≤200字)")
    ageConfirmed: bool = Field(
        False, description="年满18周岁声明(首次下单必勾)")


class OrderCancelRequest(PydBaseModel):
    """取消请求"""
    reason: str = Field("用户取消", description="取消原因")


class PayRequest(PydBaseModel):
    """支付请求(method: wallet|trust_value|mixed)"""
    method: str = Field(..., description="支付方式")
    useTrustValue: float | None = Field(
        None, description="mixed 指定 TV 额(≤xinzhiCredit)")
    trustId: int | None = Field(
        None, description="45号信值档案 ID(TV 支付必填)")


class ShipRequest(PydBaseModel):
    """发货请求(商家侧)"""
    carrier: str = Field(..., description="承运商")
    waybillNo: str = Field(..., description="运单号")


class ReviewRequest(PydBaseModel):
    """评价请求"""
    rating: int = Field(..., description="评分 1-5")
    content: str = Field("", description="评价内容(≤500字)")


class ReverseRequest(PydBaseModel):
    """冲正请求(admin)"""
    reason: str = Field("", description="冲正原因")


# ============================================================
# P8 购物车
# ============================================================

@router.post("/api/xinzhi/cart/add",
             tags=["信值臻选购物平台"])
async def xinzhi_cart_add(
        req: CartItemRequest,
        x_member_id: Annotated[str | None,
                               Header(alias="X-Member-Id")]
        = None):
    """加购(校验 listing 存在且 listed; 价格快照=当时试算)"""
    mid = _member_id(x_member_id)
    try:
        cart = await _cart_service().cart_add(
            mid, req.listingId, req.quantity)
        return {"success": True, "data": cart}
    except Exception as e:
        _handle(e)


@router.post("/api/xinzhi/cart/update",
             tags=["信值臻选购物平台"])
async def xinzhi_cart_update(
        req: CartItemRequest,
        x_member_id: Annotated[str | None,
                               Header(alias="X-Member-Id")]
        = None):
    """改量(条目须已在购物车)"""
    mid = _member_id(x_member_id)
    try:
        cart = await _cart_service().cart_update(
            mid, req.listingId, req.quantity)
        return {"success": True, "data": cart}
    except Exception as e:
        _handle(e)


@router.post("/api/xinzhi/cart/remove",
             tags=["信值臻选购物平台"])
async def xinzhi_cart_remove(
        req: CartRemoveRequest,
        x_member_id: Annotated[str | None,
                               Header(alias="X-Member-Id")]
        = None):
    """移除条目"""
    mid = _member_id(x_member_id)
    try:
        cart = await _cart_service().cart_remove(
            mid, req.listingId)
        return {"success": True, "data": cart}
    except Exception as e:
        _handle(e)


@router.get("/api/xinzhi/cart/mine",
            tags=["信值臻选购物平台"])
async def xinzhi_cart_mine(
        x_member_id: Annotated[str | None,
                               Header(alias="X-Member-Id")]
        = None):
    """我的购物车(空车诚实零值)"""
    mid = _member_id(x_member_id)
    return {"success": True,
            "data": await _cart_service().cart_mine(mid)}


@router.post("/api/xinzhi/cart/checkout-preview",
             tags=["信值臻选购物平台"])
async def xinzhi_cart_checkout_preview(
        x_member_id: Annotated[str | None,
                               Header(alias="X-Member-Id")]
        = None):
    """结算预览(逐项实时重算——快照仅展示防旧价套利;
    合计+α抵扣≤30%校验+运费满99免)"""
    mid = _member_id(x_member_id)
    try:
        return {"success": True, "data":
                await _cart_service().checkout_preview(mid)}
    except Exception as e:
        _handle(e)


# ============================================================
# P8 下单与流转
# ============================================================

@router.post("/api/xinzhi/order/create",
             tags=["信值臻选购物平台"])
async def xinzhi_order_create(
        req: OrderCreateRequest,
        x_member_id: Annotated[str | None,
                               Header(alias="X-Member-Id")]
        = None):
    """下单(逐项校验/年龄门/实时计价/锁内库存预扣;
    XZ+时间戳订单号)"""
    mid = _member_id(x_member_id)
    try:
        data = await _cart_service().create_order(
            mid, items=req.items, address=req.address,
            remark=req.remark,
            age_confirmed=req.ageConfirmed)
        return {"success": True, "data": data}
    except Exception as e:
        _handle(e)


@router.post("/api/xinzhi/order/{order_id}/cancel",
             tags=["信值臻选购物平台"])
async def xinzhi_order_cancel(
        order_id: str,
        req: OrderCancelRequest,
        x_member_id: Annotated[str | None,
                               Header(alias="X-Member-Id")]
        = None):
    """取消订单 PENDING→CANCELLED(库存回补)"""
    mid = _member_id(x_member_id)
    try:
        order = await _cart_service().cancel_order(
            mid, order_id, reason=req.reason)
        return {"success": True, "data": order}
    except Exception as e:
        _handle(e)


@router.get("/api/xinzhi/order/mine",
            tags=["信值臻选购物平台"])
async def xinzhi_order_mine(
        x_member_id: Annotated[str | None,
                               Header(alias="X-Member-Id")]
        = None,
        status: str = None):
    """我的臻选订单(最新优先)"""
    mid = _member_id(x_member_id)
    orders = await _cart_service().my_orders(mid, status=status)
    return {"success": True, "memberId": mid,
            "count": len(orders), "data": orders}


@router.get("/api/xinzhi/order/{order_id}",
            tags=["信值臻选购物平台"])
async def xinzhi_order_detail(
        order_id: str,
        x_member_id: Annotated[str | None,
                               Header(alias="X-Member-Id")]
        = None,
        x_role: Annotated[str | None,
                          Header(alias="X-Role")] = None):
    """订单详情(买家/店铺归属商家/admin 可见)"""
    mid = _member_id(x_member_id)
    try:
        order = await _cart_service().get_order(order_id)
        if (mid != order.get("memberId")
                and mid != order.get("shopMemberId")
                and x_role != "admin"):
            raise PermissionError("无权查看该订单")
        return {"success": True, "data": order}
    except Exception as e:
        _handle(e)


@router.post("/api/xinzhi/order/{order_id}/ship",
             tags=["信值臻选购物平台"])
async def xinzhi_order_ship(
        order_id: str,
        req: ShipRequest,
        x_member_id: Annotated[str | None,
                               Header(alias="X-Member-Id")]
        = None):
    """发货 PAID→SHIPPED(商家侧——X-Member-Id 店铺归属校验)"""
    mid = _member_id(x_member_id)
    try:
        order = await _cart_service().ship_order(
            mid, order_id, req.carrier, req.waybillNo)
        return {"success": True, "data": order}
    except Exception as e:
        _handle(e)


@router.post("/api/xinzhi/order/{order_id}/confirm",
             tags=["信值臻选购物平台"])
async def xinzhi_order_confirm(
        order_id: str,
        x_member_id: Annotated[str | None,
                               Header(alias="X-Member-Id")]
        = None):
    """确认收货 SHIPPED→RECEIVED"""
    mid = _member_id(x_member_id)
    try:
        order = await _cart_service().confirm_order(
            mid, order_id)
        return {"success": True, "data": order}
    except Exception as e:
        _handle(e)


@router.post("/api/xinzhi/order/{order_id}/review",
             tags=["信值臻选购物平台"])
async def xinzhi_order_review(
        order_id: str,
        req: ReviewRequest,
        x_member_id: Annotated[str | None,
                               Header(alias="X-Member-Id")]
        = None):
    """评价 RECEIVED→COMPLETED(信值回流留痕——
    加分为 68号既有信值体系职责, 本期不自动加分)"""
    mid = _member_id(x_member_id)
    try:
        order = await _cart_service().review_order(
            mid, order_id, req.rating, req.content)
        return {"success": True, "data": order}
    except Exception as e:
        _handle(e)


# ============================================================
# P9 支付与结算
# ============================================================

@router.post("/api/xinzhi/order/{order_id}/pay",
             tags=["信值臻选购物平台"])
async def xinzhi_order_pay(
        order_id: str,
        req: PayRequest,
        x_member_id: Annotated[str | None,
                               Header(alias="X-Member-Id")]
        = None):
    """支付三通道(wallet / trust_value[1TV=1元] / mixed
    TV优先抵信值抵扣部分+wallet付余下; 资金源留痕
    funding{source, amount, txRef})"""
    mid = _member_id(x_member_id)
    try:
        data = await _settle_service().pay_order(
            mid, order_id, req.method,
            use_trust_value=req.useTrustValue,
            trust_id=req.trustId)
        return {"success": True, "data": data}
    except Exception as e:
        _handle(e)


@router.post("/api/xinzhi/settlement/run",
             tags=["信值臻选购物平台"])
async def xinzhi_settlement_run(
        x_role: Annotated[str | None,
                          Header(alias="X-Role")] = None):
    """T+1 分账执行(admin 或调度触发; 幂等: 已 settled
    跳过——货款入商家 wallet.deposit_reward, 平台费留痕)"""
    _require_admin(x_role)
    try:
        data = await _settle_service().run_settlements(
            operator=f"role:{x_role}")
        return {"success": True, "data": data}
    except Exception as e:
        _handle(e)


@router.get("/api/xinzhi/settlement/mine",
            tags=["信值臻选购物平台"])
async def xinzhi_settlement_mine(
        x_member_id: Annotated[str | None,
                               Header(alias="X-Member-Id")]
        = None):
    """商家侧结算单列表(X-Member-Id 店铺归属)"""
    mid = _member_id(x_member_id)
    rows = await _settle_service().my_settlements(mid)
    return {"success": True, "memberId": mid,
            "count": len(rows), "data": rows}


@router.get("/api/xinzhi/settlement/{settle_id}",
            tags=["信值臻选购物平台"])
async def xinzhi_settlement_detail(
        settle_id: int,
        x_member_id: Annotated[str | None,
                               Header(alias="X-Member-Id")]
        = None,
        x_role: Annotated[str | None,
                          Header(alias="X-Role")] = None):
    """结算单详情(买家/店铺归属商家/admin 可见)"""
    mid = _member_id(x_member_id)
    try:
        s = await _settle_service().get_settlement(settle_id)
        if (mid != s.get("memberId")
                and mid != s.get("shopMemberId")
                and x_role != "admin"):
            raise PermissionError("无权查看该结算单")
        return {"success": True, "data": s}
    except Exception as e:
        _handle(e)


@router.post("/api/xinzhi/settlement/{settle_id}/reverse",
             tags=["信值臻选购物平台"])
async def xinzhi_settlement_reverse(
        settle_id: int,
        req: ReverseRequest,
        x_role: Annotated[str | None,
                          Header(alias="X-Role")] = None):
    """结算冲正(admin——退货语义预留; wallet 不足记负债
    字段, 诚实标注, 追缴走人工/建议书轨)"""
    _require_admin(x_role)
    try:
        s = await _settle_service().reverse_settlement(
            settle_id, reason=req.reason,
            operator=f"role:{x_role}")
        return {"success": True, "data": s}
    except Exception as e:
        _handle(e)


@router.get("/api/xinzhi/settlements",
            tags=["信值臻选购物平台"])
async def xinzhi_settlements(
        x_role: Annotated[str | None,
                          Header(alias="X-Role")] = None,
        status: str = None):
    """结算单总览(admin, 可按状态筛)"""
    _require_admin(x_role)
    rows = await _settle_service().list_settlements(
        status=status)
    return {"success": True, "count": len(rows), "data": rows}


def register_xinzhi_trade_routes(app) -> None:
    """注册信值臻选交易路由(main.py startup 调用)"""
    app.include_router(router)
