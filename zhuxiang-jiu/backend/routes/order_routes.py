"""订单管理路由(17 端点)

鉴权:
    - 用户端: X-Member-Id 头(创建/支付/取消/确认/评价/我的订单/详情/价格试算)
    - 管理端: X-Role: admin 头(发货/退款/超时/订单列表)

异常映射(遵循项目约定):
    - KeyError → 404(资源不存在)
    - ValueError → 409(状态/业务冲突)
    - 权限校验 → 401/403
"""

from fastapi import APIRouter, Header, HTTPException, Query

from services.order_service import OrderService, STATUS_CN


router = APIRouter(prefix="/api/order", tags=["订单服务"])
_service = OrderService()


def _require_member(x_member_id: str) -> int:
    """校验登录态, 返回 member_id"""
    if not x_member_id:
        raise HTTPException(status_code=401, detail="未登录: 缺少 X-Member-Id 头")
    try:
        return int(x_member_id)
    except ValueError:
        raise HTTPException(status_code=401, detail="X-Member-Id 须为数字")


def _require_admin(x_role: str):
    """校验管理员权限"""
    if x_role != "admin":
        raise HTTPException(status_code=403, detail="需要管理员权限")


def _handle(exc):
    """统一异常映射"""
    if isinstance(exc, KeyError):
        raise HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, ValueError):
        raise HTTPException(status_code=409, detail=str(exc))
    raise HTTPException(status_code=500, detail=str(exc))


# ============================================================
# 用户端接口
# ============================================================

@router.post("/create")
async def create_order(
    body: dict,
    x_member_id: str = Header(default="", alias="X-Member-Id"),
):
    """创建订单

    Body:
        {
            "items": [{"productId": "ZX42-2026L07", "productName": "竹奕·竹香型 42°",
                       "quantity": 2, "unitPrice": 268.00}],
            "address": {"name": "张三", "phone": "138...", "province": "...",
                         "city": "...", "district": "...", "detail": "..."},
            "usePoints": 0,
            "remark": ""
        }
    """
    member_id = _require_member(x_member_id)
    try:
        result = await _service.create(
            member_id,
            items=body.get("items", []),
            address=body.get("address", {}),
            use_points=int(body.get("usePoints", 0)),
            remark=body.get("remark", ""),
        )
        return result
    except Exception as e:
        raise _handle(e)


@router.post("/price/preview")
async def preview_price(
    body: dict,
    x_member_id: str = Header(default="", alias="X-Member-Id"),
):
    """价格试算(不创建订单)"""
    member_id = _require_member(x_member_id)
    try:
        result = await _service.preview_price(
            member_id,
            items=body.get("items", []),
            use_points=int(body.get("usePoints", 0)),
        )
        return result
    except Exception as e:
        raise _handle(e)


@router.get("/my")
async def my_orders(
    status: str = Query(default=None),
    x_member_id: str = Header(default="", alias="X-Member-Id"),
):
    """我的订单(可按状态筛选)"""
    member_id = _require_member(x_member_id)
    try:
        return await _service.get_my_orders(member_id, status)
    except Exception as e:
        raise _handle(e)


@router.get("/statuses")
async def list_statuses():
    """状态列表"""
    return await _service.list_statuses()


@router.get("/{order_id}")
async def get_order(
    order_id: str,
    x_member_id: str = Header(default="", alias="X-Member-Id"),
):
    """订单详情"""
    _require_member(x_member_id)
    try:
        return await _service.get_by_id(order_id)
    except Exception as e:
        raise _handle(e)


@router.post("/{order_id}/pay")
async def pay_order(
    order_id: str,
    body: dict = None,
    x_member_id: str = Header(default="", alias="X-Member-Id"),
):
    """支付订单 PENDING → PAID"""
    _require_member(x_member_id)
    payment_method = (body or {}).get("method", "wechat")
    try:
        return await _service.pay(order_id, payment_method)
    except Exception as e:
        raise _handle(e)


@router.post("/{order_id}/cancel")
async def cancel_order(
    order_id: str,
    body: dict = None,
    x_member_id: str = Header(default="", alias="X-Member-Id"),
):
    """取消订单 PENDING → CANCELLED"""
    _require_member(x_member_id)
    reason = (body or {}).get("reason", "用户取消")
    try:
        return await _service.cancel(order_id, reason)
    except Exception as e:
        raise _handle(e)


@router.post("/{order_id}/confirm")
async def confirm_order(
    order_id: str,
    x_member_id: str = Header(default="", alias="X-Member-Id"),
):
    """确认收货 SHIPPED → RECEIVED"""
    _require_member(x_member_id)
    try:
        return await _service.confirm(order_id)
    except Exception as e:
        raise _handle(e)


@router.post("/{order_id}/review")
async def review_order(
    order_id: str,
    body: dict,
    x_member_id: str = Header(default="", alias="X-Member-Id"),
):
    """评价订单 RECEIVED → COMPLETED"""
    _require_member(x_member_id)
    rating = int(body.get("rating", 0))
    content = body.get("content", "")
    try:
        return await _service.review(order_id, rating, content)
    except Exception as e:
        raise _handle(e)


@router.post("/{order_id}/return")
async def apply_return(
    order_id: str,
    body: dict,
    x_member_id: str = Header(default="", alias="X-Member-Id"),
):
    """申请退货 COMPLETED → RETURNING"""
    _require_member(x_member_id)
    reason = body.get("reason", "")
    if not reason:
        raise HTTPException(status_code=400, detail="退货原因不能为空")
    try:
        return await _service.apply_return(order_id, reason)
    except Exception as e:
        raise _handle(e)


@router.delete("/{order_id}")
async def delete_order(
    order_id: str,
    x_member_id: str = Header(default="", alias="X-Member-Id"),
):
    """删除订单(仅终态可删除)"""
    _require_member(x_member_id)
    try:
        return await _service.delete(order_id)
    except Exception as e:
        raise _handle(e)


# ============================================================
# 管理端接口(需 admin 权限)
# ============================================================

@router.get("/admin/list")
async def admin_list_orders(
    status: str = Query(default=None),
    x_role: str = Header(default="", alias="X-Role"),
):
    """订单列表(管理端, 可按状态筛选)"""
    _require_admin(x_role)
    try:
        return await _service.list_all(status)
    except Exception as e:
        raise _handle(e)


@router.post("/{order_id}/ship")
async def ship_order(
    order_id: str,
    body: dict,
    x_role: str = Header(default="", alias="X-Role"),
):
    """发货 PAID → SHIPPED(管理员)"""
    _require_admin(x_role)
    carrier = body.get("carrier", "")
    waybill_no = body.get("waybillNo", "")
    if not carrier or not waybill_no:
        raise HTTPException(status_code=400, detail="carrier 和 waybillNo 不能为空")
    try:
        return await _service.ship(order_id, carrier, waybill_no)
    except Exception as e:
        raise _handle(e)


@router.post("/{order_id}/refund")
async def refund_order(
    order_id: str,
    x_role: str = Header(default="", alias="X-Role"),
):
    """退款 RETURNING → REFUNDED(管理员)"""
    _require_admin(x_role)
    try:
        return await _service.refund(order_id)
    except Exception as e:
        raise _handle(e)


@router.post("/{order_id}/timeout/close")
async def timeout_close(
    order_id: str,
    x_role: str = Header(default="", alias="X-Role"),
):
    """超时关闭 PENDING → CLOSED(管理员)"""
    _require_admin(x_role)
    try:
        return await _service.timeout_close(order_id)
    except Exception as e:
        raise _handle(e)


@router.post("/{order_id}/timeout/confirm")
async def timeout_confirm(
    order_id: str,
    x_role: str = Header(default="", alias="X-Role"),
):
    """超时自动确认收货 SHIPPED → RECEIVED(管理员)"""
    _require_admin(x_role)
    try:
        return await _service.timeout_confirm(order_id)
    except Exception as e:
        raise _handle(e)


@router.post("/{order_id}/timeout/complete")
async def timeout_complete(
    order_id: str,
    x_role: str = Header(default="", alias="X-Role"),
):
    """超时自动完成 RECEIVED → COMPLETED(管理员)"""
    _require_admin(x_role)
    try:
        return await _service.timeout_complete(order_id)
    except Exception as e:
        raise _handle(e)


def register_order_routes(app):
    """注册订单路由"""
    app.include_router(router)
