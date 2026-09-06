"""64号·信值兑换管理路由(P0-P5)

端点(P0 6; 全期规划 24):
    GET  /api/xx64/registry        刚性规则自描述(admin, 观测面)
    POST /api/xx64/orders         创建订单+锁值(member/admin, 决策面 off 409)
    GET  /api/xx64/orders         订单列表(admin, 观测面)
    GET  /api/xx64/orders/{id}    订单详情(admin, 观测面)
    GET  /api/xx64/quota          限额状态(admin, 观测面)
    GET  /api/xx64/model/status   模型状态(admin, 观测面)
    # P1: POST orders/{id}/pay + /cancel + /refund
    #     + POST /points/exchange + GET /points/preview
    # P2: GET /plan + GET /orders/{id}/explain
    # P3: GET /risk/status + POST /risk/scan
    # P4: anchors/threshold/appeals/feedback/learn
    # P5: GET /dashboard + POST /redteam

鉴权: X-Role: admin 或 member(订单
创建面向会员——双角色口径)。
统一口径(计划 §七):
    - 观测面(registry/orders 列表
      与详情/quota/model status)
      不受 XX64_MODE 影响
    - 决策面(订单创建/取消):
      off=拒绝(409)
    - 后续: 申诉/终审/退款/回流
      不受开关影响(人工铁律)
    - KeyError → 404 / ValueError → 409
"""

from fastapi import APIRouter, Header, HTTPException

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


def register_xx64_routes(app) -> None:
    """注册64号路由(main.py startup 调用)"""
    app.include_router(router)
