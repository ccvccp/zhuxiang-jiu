"""限时秒杀模块路由(15 端点)

鉴权:
    - 用户端(7 接口): X-Member-Id 头标识当前会员
    - 管理端(8 接口): X-Role: admin 头
    - 公开(2 接口): 场次列表/场次详情(游客可浏览, 下单需登录)

异常映射(遵循项目约定):
    - KeyError → 404(资源不存在)
    - ValueError → 409(业务冲突: 库存不足/限购/状态非法等)
    - 权限校验 → 401(未登录) / 403(无权操作)

端点分布(静态路径先于动态路径声明):
    - 场次(2 公开):  sessions / sessions/{sessionId}
    - 抢购(4 会员):  order / my/orders / orders/{orderNo} / orders/{orderNo}/pay
    - 订单取消(1):   orders/{orderNo}/cancel
    - 管理端(8):     sessions(建)/items/publish/cancel/settings(2)/stats/expire-cancel
"""

from typing import Optional

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel as PydBaseModel, Field

from services.flashsale_service import FlashSaleService


router = APIRouter()
_service = FlashSaleService()


# ============================================================
# 鉴权与异常映射辅助(对齐 groupbuy/wallet 风格)
# ============================================================

def _member_id_or_none(x_member_id: Optional[str]) -> Optional[int]:
    """从 X-Member-Id 头提取会员ID(可为空, 游客浏览场景); 非数字视为未登录"""
    if not x_member_id:
        return None
    try:
        return int(x_member_id)
    except (TypeError, ValueError):
        return None


def _require_member_id(x_member_id: Optional[str]) -> int:
    """必须登录, 缺失返回 401"""
    member_id = _member_id_or_none(x_member_id)
    if member_id is None:
        raise HTTPException(status_code=401, detail="未登录: 请提供 X-Member-Id 头")
    return member_id


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


def _ok(**payload) -> dict:
    return {"success": True, **payload}


# ============================================================
# 请求模型
# ============================================================

class CreateSessionRequest(PydBaseModel):
    name: str = Field(..., min_length=1, description="场次名称")
    startTime: str = Field(..., description="开始时间 ISO8601")
    endTime: str = Field(..., description="结束时间 ISO8601")


class AddItemRequest(PydBaseModel):
    productId: str = Field(..., min_length=1, description="产品ID")
    flashPrice: float = Field(..., gt=0, description="秒杀价(必须低于原价)")
    flashStock: int = Field(..., ge=1, description="秒杀库存")
    limitPerMember: int = Field(..., ge=1, description="每人限购")


class PurchaseRequest(PydBaseModel):
    sessionId: str = Field(..., min_length=1, description="场次ID")
    itemId: str = Field(..., min_length=1, description="秒杀商品ID")
    quantity: int = Field(1, ge=1, description="购买数量")


class SettingsRequest(PydBaseModel):
    enabled: Optional[bool] = Field(None, description="秒杀总开关")
    minRegisterHours: Optional[int] = Field(None, ge=0, description="注册时长要求(小时)")
    minMemberLevel: Optional[int] = Field(None, ge=0, description="会员等级要求")
    orderExpireMinutes: Optional[int] = Field(None, ge=1, description="订单超时分钟")
    maxQuantityPerOrder: Optional[int] = Field(None, ge=1, description="单笔最大数量")


# ============================================================
# 公开端点: 场次浏览(游客可看, 引流)
# ============================================================

@router.get("/api/flash/sessions", tags=["限时秒杀模块"])
async def list_sessions():
    """场次列表(仅已发布, 附运行时状态)"""
    try:
        sessions = await _service.list_sessions(only_published=True)
        return _ok(sessions=sessions, count=len(sessions))
    except Exception as exc:
        _handle(exc)


@router.get("/api/flash/sessions/{session_id}", tags=["限时秒杀模块"])
async def get_session_detail(session_id: str):
    """场次详情 + 秒杀商品(剩余库存/抢购进度)"""
    try:
        detail = await _service.get_session_detail(session_id)
        return _ok(session=detail)
    except Exception as exc:
        _handle(exc)


# ============================================================
# 用户端: 抢购与订单
# ============================================================

@router.post("/api/flash/order", tags=["限时秒杀模块"])
async def purchase(data: PurchaseRequest,
                   x_member_id: Optional[str] = Header(None, alias="X-Member-Id")):
    """抢购下单(锁内: 幂等/限购/库存原子判定)"""
    member_id = _require_member_id(x_member_id)
    try:
        order = await _service.purchase(member_id, data.sessionId,
                                        data.itemId, data.quantity)
        return _ok(order=order)
    except Exception as exc:
        _handle(exc)


@router.get("/api/flash/my/orders", tags=["限时秒杀模块"])
async def my_orders(x_member_id: Optional[str] = Header(None, alias="X-Member-Id")):
    """我的秒杀订单(倒序)"""
    member_id = _require_member_id(x_member_id)
    try:
        orders = await _service.my_orders(member_id)
        return _ok(orders=orders, count=len(orders))
    except Exception as exc:
        _handle(exc)


@router.get("/api/flash/orders/{order_no}", tags=["限时秒杀模块"])
async def get_order(order_no: str,
                    x_member_id: Optional[str] = Header(None, alias="X-Member-Id"),
                    x_role: Optional[str] = Header(None, alias="X-Role")):
    """订单详情(本人或管理员)"""
    member_id = _member_id_or_none(x_member_id)
    is_admin = x_role == "admin"
    try:
        order = await _service.get_order(order_no, member_id=member_id,
                                         is_admin=is_admin)
        return _ok(order=order)
    except Exception as exc:
        _handle(exc)


@router.post("/api/flash/orders/{order_no}/pay", tags=["限时秒杀模块"])
async def pay_order(order_no: str,
                    x_member_id: Optional[str] = Header(None, alias="X-Member-Id")):
    """模拟支付成功(对接支付网关后替换为回调)"""
    member_id = _require_member_id(x_member_id)
    try:
        order = await _service.pay_order(order_no, member_id=member_id)
        return _ok(order=order)
    except Exception as exc:
        _handle(exc)


@router.post("/api/flash/orders/{order_no}/cancel", tags=["限时秒杀模块"])
async def cancel_order(order_no: str,
                       x_member_id: Optional[str] = Header(None, alias="X-Member-Id"),
                       x_role: Optional[str] = Header(None, alias="X-Role")):
    """取消订单(本人或管理员, 锁内回补库存)"""
    member_id = _member_id_or_none(x_member_id)
    is_admin = x_role == "admin"
    if member_id is None and not is_admin:
        raise HTTPException(status_code=401, detail="未登录: 请提供 X-Member-Id 头")
    try:
        order = await _service.cancel_order(order_no, member_id=member_id,
                                            is_admin=is_admin)
        return _ok(order=order)
    except Exception as exc:
        _handle(exc)


# ============================================================
# 管理端: 场次管理(静态路径优先声明)
# ============================================================

@router.post("/api/flash/admin/sessions", tags=["限时秒杀模块"])
async def create_session(data: CreateSessionRequest,
                         x_role: Optional[str] = Header(None, alias="X-Role")):
    """创建秒杀场次(草稿)"""
    _require_admin(x_role)
    try:
        session = await _service.create_session(data.name, data.startTime,
                                                data.endTime)
        return _ok(session=session)
    except Exception as exc:
        _handle(exc)


@router.post("/api/flash/admin/sessions/{session_id}/items", tags=["限时秒杀模块"])
async def add_item(session_id: str, data: AddItemRequest,
                   x_role: Optional[str] = Header(None, alias="X-Role")):
    """添加秒杀商品(仅草稿场次)"""
    _require_admin(x_role)
    try:
        item = await _service.add_item(session_id, data.productId,
                                       data.flashPrice, data.flashStock,
                                       data.limitPerMember)
        return _ok(item=item)
    except Exception as exc:
        _handle(exc)


@router.post("/api/flash/admin/sessions/{session_id}/publish", tags=["限时秒杀模块"])
async def publish_session(session_id: str,
                          x_role: Optional[str] = Header(None, alias="X-Role")):
    """发布场次"""
    _require_admin(x_role)
    try:
        session = await _service.publish_session(session_id)
        return _ok(session=session)
    except Exception as exc:
        _handle(exc)


@router.post("/api/flash/admin/sessions/{session_id}/cancel", tags=["限时秒杀模块"])
async def cancel_session(session_id: str,
                         x_role: Optional[str] = Header(None, alias="X-Role")):
    """取消场次(联动取消待支付订单并回补库存)"""
    _require_admin(x_role)
    try:
        session = await _service.cancel_session(session_id)
        return _ok(session=session)
    except Exception as exc:
        _handle(exc)


# ============================================================
# 管理端: 参数与运维
# ============================================================

@router.get("/api/flash/admin/settings", tags=["限时秒杀模块"])
async def get_settings(x_role: Optional[str] = Header(None, alias="X-Role")):
    """查询秒杀参数"""
    _require_admin(x_role)
    try:
        return _ok(settings=await _service.get_settings())
    except Exception as exc:
        _handle(exc)


@router.post("/api/flash/admin/settings", tags=["限时秒杀模块"])
async def update_settings(data: SettingsRequest,
                          x_role: Optional[str] = Header(None, alias="X-Role")):
    """修改秒杀参数(白名单字段, 即时生效)"""
    _require_admin(x_role)
    try:
        payload = {k: v for k, v in data.model_dump().items() if v is not None}
        settings = await _service.update_settings(payload)
        return _ok(settings=settings)
    except Exception as exc:
        _handle(exc)


@router.get("/api/flash/admin/stats", tags=["限时秒杀模块"])
async def stats(x_role: Optional[str] = Header(None, alias="X-Role")):
    """全局销售统计(按场次聚合)"""
    _require_admin(x_role)
    try:
        return _ok(stats=await _service.stats())
    except Exception as exc:
        _handle(exc)


@router.post("/api/flash/admin/orders/expire-cancel", tags=["限时秒杀模块"])
async def cancel_expired_orders(
        x_role: Optional[str] = Header(None, alias="X-Role")):
    """批量取消超时未支付订单(回补库存); 供定时任务触发"""
    _require_admin(x_role)
    try:
        result = await _service.cancel_expired_orders()
        return _ok(**result)
    except Exception as exc:
        _handle(exc)


def register_flashsale_routes(app) -> None:
    """注册限时秒杀模块路由"""
    app.include_router(router)
