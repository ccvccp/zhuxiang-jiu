"""67号·AI智能叫帮模块路由(13 端点·P0 基础互助)

定位: 信值驱动的社会互助网络(公益优先/有偿为辅/平台零佣金)

鉴权:
    - 用户端(11): X-Member-Id 头(发布/接单/流转/评价/我的)
    - 公开(2):    互助大厅 / 类型字典

异常映射(项目约定):
    - KeyError   → 404(单不存在)
    - ValueError → 409(违禁词/门槛不足/状态非法/在忙等)
    - 未登录     → 401

端点分布:
    - 解析(1):   POST /parse(需求智能解析: 类型+模式推荐+安全预检)
    - 发布(1):   POST /orders
    - 查询(4):   GET /orders(大厅 LBS) / GET /orders/{id} /
                 GET /categories / GET /trust/{member_id}
    - 流转(4):   POST accept / start / complete / cancel
    - 我的(2):   GET /my/published / GET /my/helped
    - 评价(1):   POST /orders/{id}/review
"""

from fastapi import APIRouter, Header, HTTPException, Query
from pydantic import BaseModel as PydBaseModel, Field

from services.help_service import HelpService

router = APIRouter()
_service = HelpService()


# ============================================================
# 鉴权与异常映射辅助(对齐项目约定)
# ============================================================

def _require_member(x_member_id: str | None) -> int:
    if not x_member_id:
        raise HTTPException(status_code=401,
                            detail="未登录: 请提供 X-Member-Id 头")
    try:
        return int(x_member_id)
    except (TypeError, ValueError):
        raise HTTPException(status_code=401, detail="X-Member-Id 须为数字")


def _handle(exc: Exception):
    if isinstance(exc, KeyError):
        msg = str(exc) if str(exc) else "资源不存在"
        if msg.startswith("'") and msg.endswith("'"):
            msg = msg[1:-1]
        raise HTTPException(status_code=404, detail=msg)
    if isinstance(exc, ValueError):
        raise HTTPException(status_code=409, detail=str(exc))
    raise HTTPException(status_code=500, detail=str(exc))


# ============================================================
# 请求模型
# ============================================================

class ParseRequest(PydBaseModel):
    title: str = Field(..., min_length=1, max_length=60, description="求助标题")
    description: str = Field("", max_length=500, description="详细描述")


class PublishRequest(PydBaseModel):
    mode: str = Field("public", description="模式: public公益/paid有偿")
    category: str = Field(..., description="类型: repair/escort/carry/care/teach/other")
    title: str = Field(..., min_length=1, max_length=60)
    description: str = Field("", max_length=500)
    longitude: float = Field(..., ge=-180, le=180, description="经度")
    latitude: float = Field(..., ge=-90, le=90, description="纬度")
    address: str = Field("", max_length=100, description="地址描述")
    durationMinutes: int = Field(60, ge=10, le=1440, description="预计时长(分钟)")
    price: float = Field(0, ge=0, description="有偿协商价(公益为0)")
    urgency: str = Field("normal", description="紧急度: normal/urgent")


class ReviewRequest(PydBaseModel):
    score: int = Field(..., ge=1, le=5, description="星级 1-5")
    content: str = Field("", max_length=200)


class CancelRequest(PydBaseModel):
    reason: str = Field("", max_length=200)


# ============================================================
# 智能需求解析(确定性规则引擎, 发布前置调用)
# ============================================================

@router.post("/api/help/parse", tags=["AI智能叫帮模块"])
async def parse_demand(data: ParseRequest):
    """需求智能解析: 类型识别 + 模式推荐 + 违禁词预检(纯规则可解释)"""
    try:
        return {"success": True, "data": _service.parse_demand(
            data.title, data.description)}
    except Exception as e:
        _handle(e)


# ============================================================
# 发布与查询
# ============================================================

@router.post("/api/help/orders", tags=["AI智能叫帮模块"])
async def publish(
    data: PublishRequest,
    x_member_id: str = Header(None, alias="X-Member-Id"),
):
    """发布求助(公益100%/有偿10%信值预计算 + 安全预检)"""
    member_id = _require_member(x_member_id)
    try:
        result = await _service.publish(
            publisher_id=member_id, mode=data.mode, category=data.category,
            title=data.title, description=data.description,
            longitude=data.longitude, latitude=data.latitude,
            address=data.address, duration_minutes=data.durationMinutes,
            price=data.price, urgency=data.urgency)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.get("/api/help/orders", tags=["AI智能叫帮模块"])
async def hall(
    longitude: float = Query(0, description="观察点经度"),
    latitude: float = Query(0, description="观察点纬度"),
    mode: str = Query(None, description="模式筛选 public/paid"),
    category: str = Query(None, description="类型筛选"),
    radius_km: float = Query(20, ge=0.1, le=200, description="半径(km)"),
    limit: int = Query(50, ge=1, le=200),
):
    """互助大厅(LBS: 紧急优先 → 距离近 → 新单优先; 公开)"""
    try:
        result = await _service.hall(longitude, latitude, mode, category,
                                     radius_km, limit)
        return {"success": True, "data": result, "count": len(result)}
    except Exception as e:
        _handle(e)


@router.get("/api/help/orders/{order_id}", tags=["AI智能叫帮模块"])
async def order_detail(order_id: int):
    """叫帮单详情(公开)"""
    try:
        return {"success": True,
                "data": await _service.get_order(order_id)}
    except Exception as e:
        _handle(e)


@router.get("/api/help/categories", tags=["AI智能叫帮模块"])
async def categories():
    """类型字典(公开, 含公益信值基准)"""
    return {"success": True, "data": _service.categories()}


@router.get("/api/help/trust/{member_id}", tags=["AI智能叫帮模块"])
async def trust_profile(member_id: int):
    """信值档案(公益/有偿双轨累计 + 流水 + 评价均分; 公开)"""
    try:
        return {"success": True,
                "data": await _service.trust_profile(member_id)}
    except Exception as e:
        _handle(e)


# ============================================================
# 履约流转(接单/开始/完成/取消)
# ============================================================

@router.post("/api/help/orders/{order_id}/accept", tags=["AI智能叫帮模块"])
async def accept(
    order_id: int,
    x_member_id: str = Header(None, alias="X-Member-Id"),
):
    """接单(信值门槛: 公益≥20/有偿≥50; 在忙不可接)"""
    member_id = _require_member(x_member_id)
    try:
        result = await _service.accept(order_id, member_id)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.post("/api/help/orders/{order_id}/start", tags=["AI智能叫帮模块"])
async def start(
    order_id: int,
    x_member_id: str = Header(None, alias="X-Member-Id"),
):
    """开始服务(matched → in_progress; 帮助者本人)"""
    member_id = _require_member(x_member_id)
    try:
        result = await _service.start(order_id, member_id)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.post("/api/help/orders/{order_id}/complete", tags=["AI智能叫帮模块"])
async def complete(
    order_id: int,
    x_member_id: str = Header(None, alias="X-Member-Id"),
):
    """确认完成(in_progress → completed; 双方确认)

    结算(确定性): 帮助者 +信值(公益100%/有偿10%), 公益单发布者 +1 感谢信值。
    """
    member_id = _require_member(x_member_id)
    try:
        result = await _service.complete(order_id, member_id)
        return {"success": True,
                "data": {"order": result["order"],
                         "settlement": result["settlement"]}}
    except Exception as e:
        _handle(e)


@router.post("/api/help/orders/{order_id}/cancel", tags=["AI智能叫帮模块"])
async def cancel(
    order_id: int,
    data: CancelRequest = None,
    x_member_id: str = Header(None, alias="X-Member-Id"),
):
    """取消(接单后帮助者取消扣 2 信值; 服务开始后不可取消)"""
    member_id = _require_member(x_member_id)
    try:
        result = await _service.cancel(
            order_id, member_id, (data.reason if data else "") or "")
        return {"success": True,
                "data": {"order": result["order"],
                         "trustPenalty": result["trustPenalty"]}}
    except Exception as e:
        _handle(e)


# ============================================================
# 评价与我的互助
# ============================================================

@router.post("/api/help/orders/{order_id}/review", tags=["AI智能叫帮模块"])
async def review(
    order_id: int,
    data: ReviewRequest,
    x_member_id: str = Header(None, alias="X-Member-Id"),
):
    """双向评价(完成后一单一评; 发布者差评≤2星扣帮助者5信值)"""
    member_id = _require_member(x_member_id)
    try:
        result = await _service.review(
            order_id, member_id, data.score, data.content)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.get("/api/help/my/published", tags=["AI智能叫帮模块"])
async def my_published(
    x_member_id: str = Header(None, alias="X-Member-Id"),
):
    """我发布的求助(发布者视角)"""
    member_id = _require_member(x_member_id)
    try:
        result = await _service.my_published(member_id)
        return {"success": True, "data": result, "count": len(result)}
    except Exception as e:
        _handle(e)


@router.get("/api/help/my/helped", tags=["AI智能叫帮模块"])
async def my_helped(
    x_member_id: str = Header(None, alias="X-Member-Id"),
):
    """我接的互助(帮助者视角)"""
    member_id = _require_member(x_member_id)
    try:
        result = await _service.my_helped(member_id)
        return {"success": True, "data": result, "count": len(result)}
    except Exception as e:
        _handle(e)


def register_help_routes(app):
    """注册67号·AI智能叫帮模块路由"""
    app.include_router(router)
