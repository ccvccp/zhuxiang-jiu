"""64号·信值兑换管理路由(P0-P5)

端点(P3 16; 全期规划 24):
    GET  /api/xx64/registry        刚性规则自描述(admin, 观测面)
    POST /api/xx64/orders         创建订单+锁值(member/admin, 决策面 off 409)
    GET  /api/xx64/orders         订单列表(admin, 观测面)
    GET  /api/xx64/orders/{id}    订单详情(admin, 观测面)
    GET  /api/xx64/quota          限额状态(admin, 观测面)
    GET  /api/xx64/model/status   模型状态(admin, 观测面)
    POST /api/xx64/orders/{id}/pay     订单支付(member/admin, 决策面 off 409)
    POST /api/xx64/orders/{id}/cancel  订单取消(member/admin, 决策面 off 409)
    POST /api/xx64/orders/{id}/refund  订单退款(admin, 不受开关影响·人工铁律)
    POST /api/xx64/points/exchange      积分→信值(member/admin, 决策面 off 409)
    GET  /api/xx64/points/preview       换算预览(admin, 观测面)
    GET  /api/xx64/ledger               转移账本(admin, 观测面)
    GET  /api/xx64/plan                 最优支付组合+互斥对比+凑单候选(member/admin, 观测面)
    GET  /api/xx64/orders/{id}/explain  规则可视化解释(member/admin, 观测面)
    GET  /api/xx64/risk/status          用户风险画像(member/admin, 观测面)
    POST /api/xx64/risk/scan            手动五防全量扫描(admin, 决策面 off 409)
    # P4: anchors/threshold/appeals/feedback/learn
    # P5: GET /dashboard + POST /redteam

鉴权: X-Role: admin 或 member(订单
创建面向会员——双角色口径)。
统一口径(计划 §七):
    - 观测面(registry/orders 列表
      与详情/quota/model status/
      ledger/points preview)
      不受 XX64_MODE 影响
    - 决策面(订单创建/支付/取消/
      积分兑换): off=拒绝(409)
    - 退款/申诉/终审/回流不受
      开关影响(人工铁律)
    - KeyError → 404 / ValueError → 409
"""

from fastapi import (APIRouter, Header, HTTPException,
                    Query)

router = APIRouter(prefix="/api/xx64",
                   tags=["信值兑换管理(64号)"])


def _require_role(x_role: str | None) -> str:
    """双角色鉴权(admin 管理面/
    member 会员面)"""
    if not x_role or x_role not in (
            "admin", "member"):
        raise HTTPException(
            status_code=403,
            detail="需要 X-Role: "
                   "admin 或 member")
    return x_role


def _require_admin(x_role: str | None) -> str:
    if x_role != "admin":
        raise HTTPException(
            status_code=403,
            detail="需要 X-Role: admin")


@router.get("/registry")
async def registry(
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """刚性规则 R1-R7 自描述
    (观测面不受开关影响)"""
    _require_role(x_role)
    from services.xx64_service import (
        Xx64Service,
    )
    return Xx64Service.registry()


@router.post("/orders")
async def create_order(
        body: dict,
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """创建订单+锁值(P0——预校验四查
    →reserved; 决策面 off 409)

    Body: {buyerId, sellerId, trustId,
    price, product?, useTrust? 默认
    true, createdBy?}"""
    _require_role(x_role)
    from services.xx64_service import (
        Xx64Service,
    )
    try:
        return await (
            Xx64Service().create_order(
                buyer_id=body.get("buyerId"),
                seller_id=body.get(
                    "sellerId"),
                trust_id=body.get("trustId"),
                price=body.get("price"),
                product=str(
                    body.get("product")
                    or ""),
                use_trust=bool(
                    body.get("useTrust",
                             True)),
                created_by=str(
                    body.get("createdBy")
                    or "member")))
    except KeyError as exc:
        raise HTTPException(status_code=404,
                            detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=409,
                            detail=str(exc))


@router.get("/orders")
async def orders(
        buyer_id: int = None,
        seller_id: int = None,
        status: str = None,
        limit: int = 100,
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """订单列表(九态分布——观测面)"""
    _require_admin(x_role)
    from services.xx64_service import (
        Xx64Service,
    )
    return await Xx64Service() \
        .list_orders(
            buyer_id=buyer_id,
            seller_id=seller_id,
            status=status, limit=limit)


@router.get("/orders/{order_id}")
async def get_order(
        order_id: int,
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """订单详情(九态+快照——观测面;
    不存在 404)"""
    _require_role(x_role)
    from services.xx64_service import (
        Xx64Service,
    )
    try:
        return await (
            Xx64Service().get_order(
                order_id))
    except KeyError as exc:
        raise HTTPException(status_code=404,
                            detail=str(exc))


@router.get("/quota")
async def quota(
        trust_id: int,
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """限额状态(单次 20%/窗口 40%
    基准快照——观测面)"""
    _require_role(x_role)
    from services.xx64_service import (
        Xx64Service,
    )
    try:
        return await (
            Xx64Service().quota_status(
                trust_id))
    except KeyError as exc:
        raise HTTPException(status_code=404,
                            detail=str(exc))


@router.get("/model/status")
async def model_status(
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """模型状态(第38档案 champion/
    challenger——44号复用观测面)"""
    _require_admin(x_role)
    from services.xx64_service import (
        Xx64Service,
    )
    return await Xx64Service() \
        .model_status()


@router.post("/orders/{order_id}/pay")
async def pay_order(
        order_id: int,
        body: dict,
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """订单支付(P1——买扣卖增原子
    转移; 决策面 off 409)

    Body: {paidBy?}"""
    _require_role(x_role)
    from services.xx64_settle_service import (
        Xx64SettleService,
    )
    try:
        return await (
            Xx64SettleService()
            .pay_order(
                int(order_id),
                paid_by=str(
                    body.get("paidBy")
                    or "member")))
    except KeyError as exc:
        raise HTTPException(status_code=404,
                            detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=409,
                            detail=str(exc))


@router.post("/orders/{order_id}/cancel")
async def cancel_order(
        order_id: int,
        body: dict,
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """订单取消(P1——解锁信值;
    决策面 off 409)

    Body: {cancelledBy?}"""
    _require_role(x_role)
    from services.xx64_service import (
        Xx64Service,
    )
    try:
        return await (
            Xx64Service().cancel_order(
                int(order_id),
                cancelled_by=str(
                    body.get("cancelledBy")
                    or "member")))
    except KeyError as exc:
        raise HTTPException(status_code=404,
                            detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=409,
                            detail=str(exc))


@router.post("/orders/{order_id}/refund")
async def refund_order(
        order_id: int,
        body: dict,
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """订单退款(P1——反向转移;
    不受开关影响·资金安全人工铁律)

    Body: {refundedBy?}"""
    _require_admin(x_role)
    from services.xx64_settle_service import (
        Xx64SettleService,
    )
    try:
        return await (
            Xx64SettleService()
            .refund_order(
                int(order_id),
                refunded_by=str(
                    body.get("refundedBy")
                    or "admin")))
    except KeyError as exc:
        raise HTTPException(status_code=404,
                            detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=409,
                            detail=str(exc))


@router.post("/points/exchange")
async def points_exchange(
        body: dict,
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """积分→信值兑换(P1——100:1+
    T+1 冻结观察+日限频; 决策面
    off 409)

    Body: {userId, trustId, points,
    exchangedBy?}"""
    _require_role(x_role)
    from services.xx64_points_service import (
        Xx64PointsService,
    )
    try:
        return await (
            Xx64PointsService()
            .exchange(
                user_id=body.get("userId"),
                trust_id=body.get(
                    "trustId"),
                points=body.get("points"),
                exchanged_by=str(
                    body.get("exchangedBy")
                    or "member")))
    except KeyError as exc:
        raise HTTPException(status_code=404,
                            detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=409,
                            detail=str(exc))


@router.get("/points/preview")
async def points_preview(
        trust_id: int,
        needed_trust: float = None,
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """换算预览(P1——100:1+
    冻结/已入账统计; 观测面)"""
    _require_role(x_role)
    from services.xx64_points_service import (
        Xx64PointsService,
    )
    return await Xx64PointsService() \
        .preview(trust_id,
                 needed_trust)


@router.get("/ledger")
async def ledger(
        order_id: int = None,
        trust_id: int = None,
        limit: int = 100,
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """转移账本(P1——借贷对+
    来源标记; 观测面)"""
    _require_admin(x_role)
    from services.xx64_settle_service import (
        Xx64SettleService,
    )
    return await Xx64SettleService() \
        .ledger_view(
            order_id=order_id,
            trust_id=trust_id,
            limit=limit)


@router.get("/plan")
async def payment_plan(
        trust_id: int,
        price: float,
        discount_value: float = 0.0,
        candidates: str | None = Query(
            default=None,
            description="凑单候选"
            "(name:price 逗号串——"
            "可选)"),
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """最优支付组合+凑单(P2——
    30/70 刚性结构内方案 A/B
    对比+积分缺口换算; 观测面)"""
    _require_role(x_role)
    from services.xx64_experience_service import (
        Xx64ExperienceService,
    )
    exp = Xx64ExperienceService()
    try:
        result = await exp.payment_plan(
            trust_id=trust_id,
            price=price,
            discount_value=(
                discount_value))
        # 凑单(可选——候选以
        # name:price 逗号串传入)
        if candidates:
            cands = []
            for part in candidates.split(","):
                name, _, p = part.rpartition(
                    ":")
                if not name:
                    continue
                try:
                    cands.append({
                        "name": name.strip(),
                        "price": float(p)})
                except ValueError:
                    continue
            result["smartFill"] = (
                await exp.smart_fill(
                    trust_id=trust_id,
                    candidates=cands))
        return result
    except KeyError as exc:
        raise HTTPException(status_code=404,
                            detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=409,
                            detail=str(exc))


@router.get("/orders/{order_id}/explain")
async def explain_order(
        order_id: int,
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """规则可视化解释(P2——"为什么
    这样算"逐条 R1-R6+数字可溯源;
    观测面)"""
    _require_role(x_role)
    from services.xx64_experience_service import (
        Xx64ExperienceService,
    )
    try:
        return await (
            Xx64ExperienceService()
            .explain_order(order_id))
    except KeyError as exc:
        raise HTTPException(status_code=404,
                            detail=str(exc))


@router.get("/risk/status")
async def risk_status(
        trust_id: int,
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """用户风险画像(P3——当前风险分
    +tier 摩擦+命中事件+处置状态;
    观测面不受开关影响)"""
    _require_role(x_role)
    from services.xx64_risk_service import (
        Xx64RiskService,
    )
    try:
        return await (
            Xx64RiskService()
            .user_status(trust_id))
    except KeyError as exc:
        raise HTTPException(status_code=404,
                            detail=str(exc))


@router.post("/risk/scan")
async def risk_scan(
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """手动五防全量扫描(P3——
    仅落事件+建议书不阻断已成立交易;
    决策面 off 409)"""
    _require_admin(x_role)
    from services.xx64_risk_service import (
        Xx64RiskService,
    )
    from services.xx64_service import (
        require_active_mode,
    )
    try:
        require_active_mode()
        return await (
            Xx64RiskService().scan_all())
    except ValueError as exc:
        raise HTTPException(status_code=409,
                            detail=str(exc))


def register_xx64_routes(app) -> None:
    """注册64号路由(main.py startup 调用)"""
    app.include_router(router)
