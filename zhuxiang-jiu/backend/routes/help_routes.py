"""67号·AI智能叫帮模块路由(P0 13 + P1 5 + P2 7 + P3 4 = 29 端点)

定位: 信值驱动的社会互助网络(公益优先/有偿为辅/平台零佣金)

鉴权:
    - 用户端:    X-Member-Id 头(发布/接单/流转/评价/我的/偏好/捐赠/传承/CSR)
    - 公开:     互助大厅 / 类型字典 / 单详情 / 信值档案 /
                匹配推荐 / 故事卡 / 安全护航 / 碳积分 / 白皮书 /
                信任指数 / 开放统计

异常映射(项目约定):
    - KeyError   → 404(单不存在)
    - ValueError → 409(违禁词/门槛不足/状态非法/在忙等)
    - 未登录     → 401

端点分布:
    - 解析(1):   POST /parse(需求智能解析: 类型+模式推荐+安全预检)
    - 发布(1):   POST /orders(P2 支持 relay 接力多段)
    - 查询(4):   GET /orders(大厅 LBS+个性化+接力元数据) / GET /orders/{id} /
                 GET /categories / GET /trust/{member_id}(含 P3 碳积分)
    - 流转(4):   POST accept / start / complete / cancel(接力感知)
    - 我的(2):   GET /my/published / GET /my/helped(含接力段历史)
    - 评价(1):   POST /orders/{id}/review
    - P1(5):     GET /orders/{id}/match(三维匹配) / GET /preferences(偏好画像) /
                 GET /orders/{id}/story(故事卡) / GET /orders/{id}/guard(安全护航) /
                 POST /orders/{id}/donate(信值捐赠)
    - P2(7):     POST /heritage/apply(发起传承) / POST /heritage/{id}/accept(确认传承) /
                 POST /heritage/{id}/cancel(撤回传承, v2) /
                 GET /heritage/my(传承记录) / POST /csr/packages(创建企业包) /
                 GET /csr/my(企业包列表) / POST /csr/packages/{id}/donate(定向捐助)
    - P3(4):     GET /carbon/{member_id}(碳积分档案) / GET /whitepaper(年度白皮书) /
                 GET /trust-index(社会信任指数) / GET /open/stats(开放统计摘要)
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
    relay: bool = Field(False, description="P2 互助接力(偏远地区多人分段完成)")
    legs: int = Field(0, ge=0, le=5, description="接力段数(2-5, relay 时必填)")


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
    """发布求助(公益100%/有偿10%信值预计算 + 安全预检; P2 支持接力)"""
    member_id = _require_member(x_member_id)
    try:
        result = await _service.publish(
            publisher_id=member_id, mode=data.mode, category=data.category,
            title=data.title, description=data.description,
            longitude=data.longitude, latitude=data.latitude,
            address=data.address, duration_minutes=data.durationMinutes,
            price=data.price, urgency=data.urgency,
            relay=data.relay, legs=data.legs)
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
    x_member_id: str = Header(None, alias="X-Member-Id"),
):
    """互助大厅(LBS: 紧急优先 → 偏好命中 → 距离近 → 新单优先; 公开)

    P1: 携带 X-Member-Id 时启用个性化推荐(意愿偏好学习, 确定性统计)。
    """
    member_id = None
    if x_member_id:
        try:
            member_id = int(x_member_id)
        except (TypeError, ValueError):
            member_id = None
    try:
        result = await _service.hall(longitude, latitude, mode, category,
                                     radius_km, limit, member_id)
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
    return {"success": True, "data": await _service.categories()}


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


# ============================================================
# P1·智能调度层(三维匹配/偏好画像/故事卡/安全护航/信值捐赠)
# ============================================================

@router.get("/api/help/orders/{order_id}/match", tags=["AI智能叫帮模块"])
async def match_candidates(order_id: int):
    """三维匹配推荐(信值40%+同类经验30%+地理30%; Top3 候选+可解释理由)

    公开观测面: 发布者可据此了解哪些社区成员最适合接单(不自动派单)。
    """
    try:
        return {"success": True, "data": await _service.match(order_id)}
    except Exception as e:
        _handle(e)


@router.get("/api/help/preferences", tags=["AI智能叫帮模块"])
async def preferences(
    x_member_id: str = Header(None, alias="X-Member-Id"),
):
    """我的互助偏好画像(常接类型/活跃时段/累计统计; 确定性聚合)"""
    member_id = _require_member(x_member_id)
    try:
        return {"success": True, "data": await _service.preferences(member_id)}
    except Exception as e:
        _handle(e)


@router.get("/api/help/orders/{order_id}/story", tags=["AI智能叫帮模块"])
async def story_card(order_id: int):
    """互助故事卡(仅已完成单; 双方评价摘录+类别文案+分享文案)"""
    try:
        return {"success": True, "data": await _service.story_card(order_id)}
    except Exception as e:
        _handle(e)


@router.get("/api/help/orders/{order_id}/guard", tags=["AI智能叫帮模块"])
async def guard(order_id: int):
    """服务安全护航(观测面: 破冰提示+超时温和提醒; 惩罚处置永不自动)"""
    try:
        return {"success": True, "data": await _service.guard(order_id)}
    except Exception as e:
        _handle(e)


class DonateRequest(PydBaseModel):
    amount: int = Field(..., ge=1, le=10, description="捐赠信值(1-10)")


@router.post("/api/help/orders/{order_id}/donate", tags=["AI智能叫帮模块"])
async def donate(
    order_id: int,
    data: DonateRequest,
    x_member_id: str = Header(None, alias="X-Member-Id"),
):
    """信值捐赠(高信值用户 ≥50 反哺公益单; 完成时奖励帮助者; 每单限一次)"""
    member_id = _require_member(x_member_id)
    try:
        result = await _service.donate(order_id, member_id, data.amount)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


# ============================================================
# P2·生态深化层(信值传承 / 企业CSR / 互助接力经既有端点)
# ============================================================

class HeritageApplyRequest(PydBaseModel):
    heirMemberId: int = Field(..., ge=1, description="受让人成员ID")
    amount: float = Field(None, ge=0, description="传承额度(缺省=全部公益信值)")


@router.post("/api/help/heritage/apply", tags=["AI智能叫帮模块"])
async def heritage_apply(
    data: HeritageApplyRequest,
    x_member_id: str = Header(None, alias="X-Member-Id"),
):
    """发起信值传承(数字功德碑: 仅公益信值, 受让人确认后划转)"""
    member_id = _require_member(x_member_id)
    try:
        result = await _service.heritage_apply(
            member_id, data.heirMemberId, data.amount)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.post("/api/help/heritage/{heritage_id}/accept", tags=["AI智能叫帮模块"])
async def heritage_accept(
    heritage_id: int,
    x_member_id: str = Header(None, alias="X-Member-Id"),
):
    """受让人确认传承(划转即时生效, 双边流水留痕)"""
    member_id = _require_member(x_member_id)
    try:
        result = await _service.heritage_accept(heritage_id, member_id)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.post("/api/help/heritage/{heritage_id}/cancel", tags=["AI智能叫帮模块"])
async def heritage_cancel(
    heritage_id: int,
    x_member_id: str = Header(None, alias="X-Member-Id"),
):
    """发起人撤回待确认传承(v2: 撤回即时生效, 留痕不删除)"""
    member_id = _require_member(x_member_id)
    try:
        result = await _service.heritage_cancel(heritage_id, member_id)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.get("/api/help/heritage/my", tags=["AI智能叫帮模块"])
async def heritage_my(
    x_member_id: str = Header(None, alias="X-Member-Id"),
):
    """我的传承记录(发起 + 受让两向)"""
    member_id = _require_member(x_member_id)
    try:
        return {"success": True,
                "data": await _service.heritage_my(member_id)}
    except Exception as e:
        _handle(e)


class CsrPackageRequest(PydBaseModel):
    name: str = Field(..., min_length=1, max_length=60, description="企业包名称")
    amount: float = Field(..., ge=10, le=10000, description="认捐额度(信值)")
    note: str = Field("", max_length=200, description="CSR 备注说明")


@router.post("/api/help/csr/packages", tags=["AI智能叫帮模块"])
async def csr_create_package(
    data: CsrPackageRequest,
    x_member_id: str = Header(None, alias="X-Member-Id"),
):
    """创建企业信值包(CSR 认捐留痕; 平台零资金流)"""
    member_id = _require_member(x_member_id)
    try:
        result = await _service.csr_create_package(
            member_id, data.name, data.amount, data.note)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.get("/api/help/csr/my", tags=["AI智能叫帮模块"])
async def csr_my_packages(
    x_member_id: str = Header(None, alias="X-Member-Id"),
):
    """我的企业信值包列表(含定向捐助记录)"""
    member_id = _require_member(x_member_id)
    try:
        result = await _service.csr_my_packages(member_id)
        return {"success": True, "data": result, "count": len(result)}
    except Exception as e:
        _handle(e)


class CsrDonateRequest(PydBaseModel):
    orderId: int = Field(..., ge=1, description="目标公益求助ID")
    amount: float = Field(..., ge=1, le=100, description="捐助额度(1-100)")


@router.post("/api/help/csr/packages/{package_id}/donate", tags=["AI智能叫帮模块"])
async def csr_donate(
    package_id: int,
    data: CsrDonateRequest,
    x_member_id: str = Header(None, alias="X-Member-Id"),
):
    """企业包定向捐助公益单(入单捐赠池, 完成时奖励帮助者; 每包每单限一次)"""
    member_id = _require_member(x_member_id)
    try:
        result = await _service.csr_donate(
            package_id, member_id, data.orderId, data.amount)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


# ============================================================
# P3·社会影响力层(碳积分 / 白皮书 / 信任指数 / 开放API)
# ============================================================

@router.get("/api/help/carbon/{member_id}", tags=["AI智能叫帮模块"])
async def carbon_profile(member_id: int):
    """碳积分档案(公开; 确定性折算: 时长×类别系数, 只读观测不可交易)"""
    try:
        return {"success": True,
                "data": await _service.carbon_profile(member_id)}
    except Exception as e:
        _handle(e)


@router.get("/api/help/whitepaper", tags=["AI智能叫帮模块"])
async def whitepaper(
    year: int = Query(None, ge=2020, le=2100, description="年份(缺省=全量)"),
):
    """年度互助白皮书数据(公开; 全量聚合, 无个人隐私)"""
    try:
        return {"success": True,
                "data": await _service.whitepaper(year)}
    except Exception as e:
        _handle(e)


@router.get("/api/help/trust-index", tags=["AI智能叫帮模块"])
async def trust_index():
    """社会信任指数(公开; 100×(40%完成率+30%好评率+20%公益占比+10%参与度))"""
    try:
        return {"success": True, "data": await _service.trust_index()}
    except Exception as e:
        _handle(e)


@router.get("/api/help/open/stats", tags=["AI智能叫帮模块"])
async def open_stats():
    """开放统计摘要(公开; 政府/NGO 对接最小数据集, 仅聚合指标无个人数据)"""
    try:
        return {"success": True, "data": await _service.open_stats()}
    except Exception as e:
        _handle(e)


def register_help_routes(app):
    """注册67号·AI智能叫帮模块路由"""
    app.include_router(router)
