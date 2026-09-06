"""60号·AI智能支付管理路由(P0+P1)

端点(P0+P1 7):
    GET  /api/pay60/registry          支付注册表自描述(admin, 观测面)
    GET  /api/pay60/orders            支付订单列表(admin, 观测面)
    GET  /api/pay60/orders/{payId}    订单单条+归因链(admin, P0 观测面)
    GET  /api/pay60/model/status      第35档案模型状态(admin, 观测面)
    POST /api/pay60/orders            意图联动开单(admin, P1 决策面 off 409)
    POST /api/pay60/checkout/render   收银台上下文渲染(admin, P1 决策面 off 409)
    POST /api/pay60/orders/{payId}/recover  失败智能恢复(admin, P1 决策面 off 409——建议性)
    GET  /api/pay60/checkouts         收银台渲染留痕(admin, P1 观测面)

鉴权: 管理面 X-Role: admin(43-63号同款口径)。
统一口径:
    - 观测面(registry/orders/model
      status/checkouts)不受 PAY60_MODE
      影响
    - 决策面(订单/收银台/恢复): off=拒绝
      (409——shadow/assist 开放)
    - KeyError → 404 / ValueError → 409
"""

from fastapi import APIRouter, Header, HTTPException

router = APIRouter(prefix="/api/pay60",
                   tags=["AI智能支付管理(60号)"])


def _require_admin(x_role: str | None) -> str:
    if not x_role or x_role != "admin":
        raise HTTPException(status_code=403,
                            detail="需要 X-Role: admin")


@router.get("/registry")
async def registry(
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """支付注册表自描述(定价三因子+
    分账合约+收银台上下文+九态状态机
    +渠道三态——观测面)"""
    _require_admin(x_role)
    from services.pay60_service import Pay60Service
    return Pay60Service.registry()


@router.post("/orders")
async def create_order(
        body: dict,
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """意图联动开单(P1——58号意图纯消费
    fail-soft+三因子定价归因透明+归因链
    六要素; 决策面 off 409)

    Body: {memberId, scene, role,
    basePrice, intentText?,
    complianceMonths?, promoFactor?,
    tier?}"""
    _require_admin(x_role)
    from services.pay60_checkout_service import (
        Pay60CheckoutService,
    )
    try:
        member_id = body.get("memberId")
        return await (
            Pay60CheckoutService()
            .create_order(
                member_id=int(member_id)
                if member_id is not None
                else 0,
                scene=str(
                    body.get("scene") or ""),
                role=str(
                    body.get("role") or ""),
                base_price=float(
                    body.get("basePrice")
                    or 0),
                intent_text=body.get(
                    "intentText"),
                compliance_months=int(
                    body.get(
                        "complianceMonths")
                    or 0),
                promo_factor=float(
                    body.get("promoFactor")
                    or 1.0),
                tier=body.get("tier")))
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=409,
                            detail=str(exc))


@router.post("/checkout/render")
async def render_checkout(
        body: dict,
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """收银台上下文感知渲染(P1——场景×
    角色×意图→支付方式组合; 老年用户
    子女代付/语音确认优先; 高信值续费
    信用免密默认; 决策面 off 409)

    Body: {memberId, scene, role,
    intentText?, senior?, orderId?}"""
    _require_admin(x_role)
    from services.pay60_checkout_service import (
        Pay60CheckoutService,
    )
    try:
        member_id = body.get("memberId")
        order_id = body.get("orderId")
        return await (
            Pay60CheckoutService()
            .render_checkout(
                member_id=int(member_id)
                if member_id is not None
                else 0,
                scene=str(
                    body.get("scene") or ""),
                role=str(
                    body.get("role") or ""),
                intent_text=body.get(
                    "intentText"),
                senior=bool(
                    body.get("senior")),
                order_id=int(order_id)
                if order_id is not None
                else None))
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=409,
                            detail=str(exc))


@router.post(
    "/orders/{pay_id}/recover")
async def recover_order(
        pay_id: int,
        body: dict,
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """失败智能恢复(P1——四类失败→
    有序建议集; 建议性不自动执行铁律;
    决策面 off 409)

    Body: {failureReason, channelMode?}"""
    _require_admin(x_role)
    from services.pay60_checkout_service import (
        Pay60CheckoutService,
    )
    try:
        return await (
            Pay60CheckoutService()
            .recover(
                pay_id=pay_id,
                failure_reason=str(
                    body.get(
                        "failureReason")
                    or ""),
                channel_mode=body.get(
                    "channelMode")))
    except KeyError as exc:
        raise HTTPException(status_code=404,
                            detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=409,
                            detail=str(exc))


@router.get("/checkouts")
async def checkouts(
        member_id: int = None,
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """收银台渲染留痕(P1 观测面——
    renderOptions 可审计)"""
    _require_admin(x_role)
    from services.pay60_checkout_service import (
        Pay60CheckoutService,
    )
    return await (
        Pay60CheckoutService()
        .checkout_view(
            member_id=member_id))


@router.get("/orders")
async def orders(
        member_id: int = None,
        status: str = None,
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """支付订单列表(九态状态机+归因链
    ——观测面)"""
    _require_admin(x_role)
    from services.pay60_service import Pay60Service
    return await Pay60Service().list_orders(
        member_id=member_id, status=status)


@router.get("/orders/{pay_id}")
async def get_order(
        pay_id: int,
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """订单单条(P0 观测面——归因链指纹
    可追溯; 不存在 404)"""
    _require_admin(x_role)
    from services.pay60_service import Pay60Service
    try:
        return await (
            Pay60Service().get_order(
                pay_id=pay_id))
    except KeyError as exc:
        raise HTTPException(status_code=404,
                            detail=str(exc))


@router.get("/model/status")
async def model_status(
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """模型状态(第35档案 champion/
    challenger/八因子——44号复用观测面)"""
    _require_admin(x_role)
    from services.pay60_service import Pay60Service
    return await (
        Pay60Service().model_status())


def register_pay60_routes(app) -> None:
    """注册60号路由(main.py startup 调用)"""
    app.include_router(router)
