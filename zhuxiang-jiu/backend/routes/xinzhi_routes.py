"""68号 P0-P4·信值·臻选路由(雷达+货架+定价导购+互助+商家)

端点(20):
    GET  /api/xinzhi/radar               五维雷达(最新快照/即时计算)
    GET  /api/xinzhi/radar/history        90 日历史曲线
    POST /api/xinzhi/products/score       商品三维评分批次(P1)
    GET  /api/xinzhi/prime                臻选货架 L1(信值加权排序)
    GET  /api/xinzhi/products/{id}/score  商品评分明细(可解释)
    GET  /api/xinzhi/price/{id}           价格构成拆解(P2 透明定价)
    POST /api/xinzhi/feedback             反馈提交(标签路由 L1/L2/L3)
    GET  /api/xinzhi/feedback/{id}        反馈进度(透明)
    POST /api/xinzhi/guide                导购应答(SOP五步法)
    GET  /api/xinzhi/guide/personas        导购人格卡片(能力公示)
    GET  /api/xinzhi/neighbor             邻里臻选(品类聚合, 零个体)
    POST /api/xinzhi/groupbuy             发布邻里求购(P3)
    GET  /api/xinzhi/groupbuy             求购大厅(LBS 紧急→距离→新单)
    POST /api/xinzhi/groupbuy/{id}/respond 求购响应(仅计数脱敏)
    POST /api/xinzhi/groupbuy/{id}/close   求购关闭(发起人+碳折算)
    GET  /api/xinzhi/carbon/{member_id}    邻里购物碳档案(只读)
    POST /api/xinzhi/merchant/apply       商家 4+2 认证(P4)
    POST /api/xinzhi/merchant/simulate    预演沙盘(启航报告)
    GET  /api/xinzhi/merchant/{id}/level  评级查询(分数+归因+历史)
    POST /api/xinzhi/merchant/{id}/regrade 评级重算(降级走 46号)

鉴权: X-Member-Id(会员本人域; P0 与 /api/member/profile
同口径)
异常映射: KeyError→404 / ValueError→409(项目约定)
"""

from typing import Annotated

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel as PydBaseModel, Field

router = APIRouter()


def _member_id(x_member_id: str | None) -> int:
    if not (x_member_id or "").strip():
        raise HTTPException(status_code=401,
                            detail="需要登录(X-Member-Id)")
    try:
        return int(x_member_id)
    except ValueError:
        raise HTTPException(status_code=401,
                            detail="X-Member-Id 非法") from None


def _handle(e: Exception):
    if isinstance(e, KeyError):
        raise HTTPException(status_code=404, detail=str(e))
    if isinstance(e, ValueError):
        raise HTTPException(status_code=409, detail=str(e))
    raise HTTPException(status_code=500, detail=str(e))


def _service():
    from services.xinzhi_radar_service import (
        XinzhiRadarService)
    return XinzhiRadarService()


def _prime_service():
    from services.xinzhi_prime_service import (
        XinzhiPrimeService)
    return XinzhiPrimeService()


def _pricing_service():
    from services.xinzhi_pricing_service import (
        XinzhiPricingService)
    return XinzhiPricingService()


def _guide_service():
    from services.xinzhi_guide_service import (
        XinzhiGuideService)
    return XinzhiGuideService()


def _neighbor_service():
    from services.xinzhi_neighbor_service import (
        XinzhiNeighborService)
    return XinzhiNeighborService()


def _merchant_service():
    from services.xinzhi_merchant_service import (
        XinzhiMerchantService)
    return XinzhiMerchantService()


class ScoreRequest(PydBaseModel):
    productIds: list = Field(None,
                             description="指定商品(空=全量)")


class FeedbackRequest(PydBaseModel):
    scene: str = Field("product",
                       description="场景(product/logistics/"
                                   "radar/service/guide)")
    tags: list = Field(None, description="场景标签")
    content: str = Field("", description="反馈文本(≤500字)")


class GuideRequest(PydBaseModel):
    productId: str = Field(...,
                            description="商品ID")
    query: str = Field("", description="用户咨询(意图锚定)")


class GroupbuyRequest(PydBaseModel):
    title: str = Field(..., description="求购标题")
    productId: str = Field("", description="指定商品(可选)")
    quantity: int = Field(1, description="数量 1-99")
    urgency: str = Field("normal",
                         description="normal/urgent")
    longitude: float = Field(0.0, description="经度")
    latitude: float = Field(0.0, description="纬度")
    address: str = Field("", description="位置描述")


class MerchantApplyRequest(PydBaseModel):
    shopName: str = Field(..., description="店铺名称")
    checks: dict = Field(...,
                         description="四必查 {entity,"
                         "fulfillment,service,backend}")
    bonuses: dict = Field(None,
                          description="两加分 {eco_"
                          "contribution,external_"
                          "endorsement}")
    allianceMerchantId: int = Field(None,
                                    description="37号同盟商"
                                    "ID(履约数据关联)")


class MerchantSimulateRequest(PydBaseModel):
    merchantId: int = Field(None,
                            description="已认证商家(空=纯申报)")
    dailyOrders: int = Field(50, description="日均单量")
    fulfillmentRate: float = Field(0.95,
                                  description="申报履约率")
    complaintRate: float = Field(0.03,
                                description="申报客诉率")
    onTimeRate: float = Field(0.92,
                              description="申报时效达标率")
    certScore: float = Field(None,
                             description="纯申报模式认证分")


@router.get("/api/xinzhi/radar",
            tags=["信值臻选购物平台"])
async def xinzhi_radar(
        x_member_id: Annotated[str | None,
                               Header(alias="X-Member-Id")]
        = None):
    """信值五维雷达(诚信30/互助25/专业20/活跃15/成长10——
    分段Sigmoid+熔断+衰减; 每维影响因素TOP3 可解释)"""
    mid = _member_id(x_member_id)
    try:
        return {"success": True, "data":
                await _service().get_radar(mid)}
    except Exception as e:
        _handle(e)


@router.get("/api/xinzhi/radar/history",
            tags=["信值臻选购物平台"])
async def xinzhi_radar_history(
        x_member_id: Annotated[str | None,
                               Header(alias="X-Member-Id")]
        = None,
        limit: int = 90):
    """信值 90 日历史曲线(快照时序)"""
    mid = _member_id(x_member_id)
    try:
        return {"success": True, "data":
                await _service().get_history(mid, limit=limit)}
    except Exception as e:
        _handle(e)


@router.post("/api/xinzhi/products/score",
             tags=["信值臻选购物平台"])
async def xinzhi_products_score(
        req: ScoreRequest,
        x_member_id: Annotated[str | None,
                               Header(alias="X-Member-Id")]
        = None):
    """商品三维评分批次(契合×安全硬闸×转化 → L1-L4;
    信值加权排序——文档"信值加权排序算法"公式)"""
    mid = _member_id(x_member_id)
    try:
        return {"success": True, "data":
                await _prime_service().score_products(
                    mid, product_ids=req.productIds)}
    except Exception as e:
        _handle(e)


@router.get("/api/xinzhi/prime",
            tags=["信值臻选购物平台"])
async def xinzhi_prime_shelf(
        x_member_id: Annotated[str | None,
                               Header(alias="X-Member-Id")]
        = None,
        limit: int = 20):
    """臻选货架(L1 臻选位, 信值加权排序降序——懒加载)"""
    mid = _member_id(x_member_id)
    try:
        return {"success": True, "data":
                await _prime_service().prime_shelf(
                    mid, limit=limit)}
    except Exception as e:
        _handle(e)


@router.get("/api/xinzhi/products/{product_id}/score",
            tags=["信值臻选购物平台"])
async def xinzhi_product_detail(
        product_id: str,
        x_member_id: Annotated[str | None,
                               Header(alias="X-Member-Id")]
        = None):
    """商品三维分明细(可解释——文档"透明化决策解释")"""
    mid = _member_id(x_member_id)
    try:
        return {"success": True, "data":
                await _prime_service().product_detail(
                    mid, product_id)}
    except Exception as e:
        _handle(e)


@router.get("/api/xinzhi/price/{product_id}",
            tags=["信值臻选购物平台"])
async def xinzhi_price_breakdown(
        product_id: str,
        x_member_id: Annotated[str | None,
                               Header(alias="X-Member-Id")]
        = None,
        promo: float = 1.0):
    """价格构成拆解(原价-信值抵扣-三因子折扣=实付,
    强制公示; 杀熟价差>20% 留痕审计)"""
    mid = _member_id(x_member_id)
    try:
        return {"success": True, "data":
                await _pricing_service().price_breakdown(
                    mid, product_id,
                    promo_factor=promo)}
    except Exception as e:
        _handle(e)


@router.post("/api/xinzhi/feedback",
             tags=["信值臻选购物平台"])
async def xinzhi_feedback_submit(
        req: FeedbackRequest,
        x_member_id: Annotated[str | None,
                               Header(alias="X-Member-Id")]
        = None):
    """反馈提交(标签路由: L1安抚自动回复/L2工单24h/
    L3紧急15分钟——文档四步闭环)"""
    mid = _member_id(x_member_id)
    try:
        return {"success": True, "data":
                await _pricing_service().submit_feedback(
                    mid, req.scene, req.tags or [],
                    req.content)}
    except Exception as e:
        _handle(e)


@router.get("/api/xinzhi/feedback/{feedback_id}",
            tags=["信值臻选购物平台"])
async def xinzhi_feedback_status(
        feedback_id: int,
        x_member_id: Annotated[str | None,
                               Header(alias="X-Member-Id")]
        = None):
    """反馈进度(状态透明——文档"用户可见状态")"""
    mid = _member_id(x_member_id)
    try:
        fb = await _pricing_service().feedback_status(
            feedback_id)
        if fb.get("memberId") != mid:
            raise KeyError(
                f"反馈不存在(feedbackId={feedback_id})")
        return {"success": True, "data": fb}
    except Exception as e:
        _handle(e)


@router.post("/api/xinzhi/guide",
             tags=["信值臻选购物平台"])
async def xinzhi_guide_reply(
        req: GuideRequest,
        x_member_id: Annotated[str | None,
                               Header(alias="X-Member-Id")]
        = None):
    """导购应答(SOP五步法: 意图锚定→信值佐证→透明解释→
    履约跟进→价值沉淀; 数字全查询层, LLM 禁入判定)"""
    mid = _member_id(x_member_id)
    try:
        return {"success": True, "data":
                await _guide_service().guide_reply(
                    mid, req.productId, req.query)}
    except Exception as e:
        _handle(e)


@router.get("/api/xinzhi/guide/personas",
            tags=["信值臻选购物平台"])
async def xinzhi_guide_personas():
    """导购人格卡片(SOP步骤/意图域/红线公示——
    无需登录, 能力透明)"""
    return {"success": True, "data":
            _guide_service().persona_card()}


@router.get("/api/xinzhi/neighbor",
            tags=["信值臻选购物平台"])
async def xinzhi_neighbor_shelf(
        city: str = None):
    """邻里臻选频道(同城市民在买什么——品类聚合,
    零个体数据红线: <5 人品类不展示)"""
    try:
        return {"success": True, "data":
                await _neighbor_service().neighbor_shelf(
                    city=city)}
    except Exception as e:
        _handle(e)


@router.post("/api/xinzhi/groupbuy",
             tags=["信值臻选购物平台"])
async def xinzhi_groupbuy_publish(
        req: GroupbuyRequest,
        x_member_id: Annotated[str | None,
                               Header(alias="X-Member-Id")]
        = None):
    """发布邻里求购(三单上限+违禁词预检——67号叫帮范式)"""
    mid = _member_id(x_member_id)
    try:
        return {"success": True, "data":
                await _neighbor_service().publish_groupbuy(
                    mid, req.title,
                    product_id=req.productId,
                    quantity=req.quantity,
                    urgency=req.urgency,
                    longitude=req.longitude,
                    latitude=req.latitude,
                    address=req.address)}
    except Exception as e:
        _handle(e)


@router.get("/api/xinzhi/groupbuy",
            tags=["信值臻选购物平台"])
async def xinzhi_groupbuy_hall(
        longitude: float = 0.0,
        latitude: float = 0.0,
        limit: int = 50):
    """求购大厅(20km LBS; 紧急→距离→新单——67号排序范式)"""
    try:
        return {"success": True, "data":
                await _neighbor_service().groupbuy_hall(
                    longitude, latitude, limit=limit)}
    except Exception as e:
        _handle(e)


@router.post("/api/xinzhi/groupbuy/{groupbuy_id}/respond",
             tags=["信值臻选购物平台"])
async def xinzhi_groupbuy_respond(
        groupbuy_id: int,
        x_member_id: Annotated[str | None,
                               Header(alias="X-Member-Id")]
        = None):
    """响应邻里求购(仅计数+昵称脱敏——零个体数据红线)"""
    mid = _member_id(x_member_id)
    try:
        return {"success": True, "data":
                await _neighbor_service().respond_groupbuy(
                    groupbuy_id, mid)}
    except Exception as e:
        _handle(e)


@router.post("/api/xinzhi/groupbuy/{groupbuy_id}/close",
             tags=["信值臻选购物平台"])
async def xinzhi_groupbuy_close(
        groupbuy_id: int,
        x_member_id: Annotated[str | None,
                               Header(alias="X-Member-Id")]
        = None):
    """关闭求购(发起人确认; 碳折算 500g×参与人数,
    只读观测不可交易)"""
    mid = _member_id(x_member_id)
    try:
        return {"success": True, "data":
                await _neighbor_service().close_groupbuy(
                    groupbuy_id, mid)}
    except Exception as e:
        _handle(e)


@router.get("/api/xinzhi/carbon/{member_id}",
            tags=["信值臻选购物平台"])
async def xinzhi_carbon_profile(member_id: int):
    """邻里购物碳档案(67号互助碳+68号求购碳合并;
    不可交易——宪法口径防金融化)"""
    try:
        return {"success": True, "data":
                await _neighbor_service().carbon_profile(
                    member_id)}
    except Exception as e:
        _handle(e)


@router.post("/api/xinzhi/merchant/apply",
             tags=["信值臻选购物平台"])
async def xinzhi_merchant_apply(
        req: MerchantApplyRequest,
        x_member_id: Annotated[str | None,
                               Header(alias="X-Member-Id")]
        = None):
    """商家 4+2 认证(四必查缺一拒; 生态贡献/外部背书
    加分; 确定性评估+留痕)"""
    mid = _member_id(x_member_id)
    try:
        return {"success": True, "data":
                await _merchant_service(
                ).apply_certification(
                    mid, req.shopName, req.checks,
                    bonuses=req.bonuses,
                    alliance_merchant_id=(
                        req.allianceMerchantId))}
    except Exception as e:
        _handle(e)


@router.post("/api/xinzhi/merchant/simulate",
             tags=["信值臻选购物平台"])
async def xinzhi_merchant_simulate(req: MerchantSimulateRequest):
    """预演沙盘(申报配置→模拟1000单→信值轨迹→
    《启航报告》TOP3风险+成长路线图; 确定性推演)"""
    try:
        return {"success": True, "data":
                await _merchant_service(
                ).simulate_voyage(
                    merchant_id=req.merchantId,
                    daily_orders=req.dailyOrders,
                    fulfillment_rate=req.fulfillmentRate,
                    complaint_rate=req.complaintRate,
                    on_time_rate=req.onTimeRate,
                    cert_score=req.certScore)}
    except Exception as e:
        _handle(e)


@router.get("/api/xinzhi/merchant/{merchant_id}/level",
            tags=["信值臻选购物平台"])
async def xinzhi_merchant_level(merchant_id: int):
    """商家评级查询(S-A-B-C-D; 分数+归因+
    升降级历史; 降级须 46号裁决公示)"""
    try:
        return {"success": True, "data":
                await _merchant_service().level_view(
                    merchant_id)}
    except Exception as e:
        _handle(e)


@router.post("/api/xinzhi/merchant/{merchant_id}/regrade",
             tags=["信值臻选购物平台"])
async def xinzhi_merchant_regrade(
        merchant_id: int,
        x_member_id: Annotated[str | None,
                               Header(alias="X-Member-Id")]
        = None):
    """评级重算(37号履约只读聚合; 升级自动生效留痕,
    降级仅生成 46号建议书——处罚永不自动)"""
    mid = _member_id(x_member_id)
    try:
        return {"success": True, "data":
                await _merchant_service().regrade(
                    merchant_id, operator=f"m{mid}")}
    except Exception as e:
        _handle(e)


def register_xinzhi_routes(app) -> None:
    """注册信值臻选路由(main.py startup 调用)"""
    app.include_router(router)
