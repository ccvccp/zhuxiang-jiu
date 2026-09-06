"""60号·AI智能支付管理路由(P0)

端点(P0 3):
    GET /api/pay60/registry          支付注册表自描述(admin, 观测面)
    GET /api/pay60/orders            支付订单列表(admin, 观测面)
    GET /api/pay60/orders/{payId}    订单单条+归因链(admin, P0 观测面)
    GET /api/pay60/model/status      第35档案模型状态(admin, 观测面)

鉴权: 管理面 X-Role: admin(43-63号同款口径)。
统一口径:
    - 观测面(registry/orders/model
      status)不受 PAY60_MODE 影响
    - 决策面(P1 起订单/收银台/验证/
      执行): off=拒绝(409)
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
