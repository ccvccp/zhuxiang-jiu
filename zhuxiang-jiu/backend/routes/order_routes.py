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

from services import ai_feedback_hooks as ai_hooks
from services.order_service import OrderService


router = APIRouter(prefix="/api/order", tags=["订单服务"])
_service = OrderService()


def _require_member(x_member_id: str) -> int:
    """校验登录态, 返回 member_id"""
    if not x_member_id:
        raise HTTPException(status_code=401, detail="未登录: 缺少 X-Member-Id 头")
    try:
        return int(x_member_id)
    except ValueError:
        raise HTTPException(status_code=401, detail="X-Member-Id 须为数字") from None


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
            age_confirmed=bool(body.get("ageConfirmed", False)),
        )
        # v7.6 自动反馈: 订单风控观察评分 + 决策快照(不阻断业务)
        # v7.8 输入富化: 传入地址/备注, 信用与行为画像由富化层查询
        order_id = (result.get("details") or {}).get("orderId") \
            or result.get("orderId") or ""
        if order_id:
            await ai_hooks.on_order_created(
                order_id, member_id, body.get("items", []),
                address=body.get("address"),
                remark=str(body.get("remark") or ""))
        return result
    except Exception as e:
        raise _handle(e) from e


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
        raise _handle(e) from e


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
        raise _handle(e) from e


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
        raise _handle(e) from e


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
        result = await _service.pay(order_id, payment_method)
        # v7.6 自动反馈: 支付路由观察评分(推荐渠道 vs 实际渠道)
        try:
            order = await _service.get_by_id(order_id)
            amount = (order.get("priceDetail") or {}).get("actualAmount") or 0
            await ai_hooks.on_payment(order_id, payment_method, amount)
        except Exception:
            pass
        # 41号: 买酒满额 → AI 自动发市内代驾券入券包(best-effort 火后不管)
        try:
            order = await _service.order_repo.get_by_id(order_id)
            amount = (order.get("priceDetail") or {}).get("actualAmount") or 0
            if amount >= 500:
                from services.ride_coupon_service import RideCouponService
                await RideCouponService().grant_for_order(
                    order.get("memberId"), order_id, amount)
        except Exception:
            pass
        return result
    except Exception as e:
        raise _handle(e) from e


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
        raise _handle(e) from e


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
        raise _handle(e) from e


@router.post("/{order_id}/review")
async def review_order(
    order_id: str,
    body: dict,
    x_member_id: str = Header(default="", alias="X-Member-Id"),
):
    """评价订单 RECEIVED → COMPLETED"""
    rating = int(body.get("rating", 0))
    content = body.get("content", "")
    try:
        result = await _service.review(order_id, rating, content)
        # v7.6 自动反馈: 订单顺利完成(风控决策正确性信号)
        await ai_hooks.on_order_outcome(order_id, "completed")
        return result
    except Exception as e:
        raise _handle(e) from e


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
        raise _handle(e) from e


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
        raise _handle(e) from e


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
        raise _handle(e) from e


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
        result = await _service.ship(order_id, carrier, waybill_no)
        # v7.6 自动反馈: 物流路由观察评分(推荐承运商 vs 实际承运商)
        await ai_hooks.on_shipped(order_id, carrier)
        return result
    except Exception as e:
        raise _handle(e) from e


@router.post("/{order_id}/refund")
async def refund_order(
    order_id: str,
    body: dict = None,
    x_role: str = Header(default="", alias="X-Role"),
    x_admin_id: str = Header(default="", alias="X-Admin-Id"),
):
    """退款(审核通过)RETURNING → REFUNDED(管理员, P0-7)

    Body(可选): {"auditor": "admin", "auditRemark": "同意全额退款"}
    须先由用户 POST /{order_id}/return 发起申请(创建 pending 审核记录)。
    """
    _require_admin(x_role)
    body = body or {}
    try:
        result = await _service.refund(
            order_id,
            auditor=body.get("auditor") or x_admin_id or "admin",
            audit_remark=body.get("auditRemark", ""),
        )
        # v7.6 自动反馈: 退款完成(风控误放行信号)
        await ai_hooks.on_order_outcome(order_id, "refunded")
        # 41号: 退款 → 未核销代驾券冲正作废(best-effort 火后不管)
        try:
            from services.ride_coupon_service import RideCouponService
            await RideCouponService().revoke_for_order(order_id)
        except Exception:
            pass
        return result
    except Exception as e:
        raise _handle(e) from e


@router.post("/{order_id}/refund/audit")
async def audit_refund(
    order_id: str,
    body: dict,
    x_role: str = Header(default="", alias="X-Role"),
    x_admin_id: str = Header(default="", alias="X-Admin-Id"),
):
    """售后退款审核(P0-7: 管理员, 同意执行退款 / 拒绝回退 COMPLETED)

    Body: {"approve": true/false, "auditRemark": "原因"}
    """
    _require_admin(x_role)
    try:
        result = await _service.audit_refund(
            order_id,
            approve=bool(body.get("approve", False)),
            auditor=body.get("auditor") or x_admin_id or "admin",
            audit_remark=body.get("auditRemark", ""),
        )
        if result.get("status") == "REFUNDED":
            await ai_hooks.on_order_outcome(order_id, "refunded")
        return result
    except Exception as e:
        raise _handle(e) from e


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
        raise _handle(e) from e


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
        raise _handle(e) from e


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
        raise _handle(e) from e


def register_order_routes(app):
    """注册订单路由"""
    app.include_router(router)
