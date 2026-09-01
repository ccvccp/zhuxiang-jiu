"""37号·AI智能网站同盟模块路由(P0, 24 端点)

鉴权:
    - 管理端: X-Role: admin(入盟审核/商户管理/分润配置/折叠评价/报表)
    - 商户端: X-Member-Id(下单/评价; 商户操作以申请人为准)
    - 公开: 类目/商品/评价查询

异常映射(遵循项目约定):
    - KeyError → 404(申请/商户/商品/订单/结算/评价不存在)
    - ValueError → 409(门槛不达/状态非法/资质缺失/库存不足/已结算等)

端点分布:
    - 入盟(7):  apply / applications / audit / merchants / activate /
                confirm / suspend / terminate
    - 类目(1):  categories
    - 商品(4):  products(POST/GET) / product / offline
    - 交易(6):  order(下单) / orders / settle / settlement / reverse /
                settle-run(调度触发)
    - 分润(2):  share-settings(GET/PUT) / share-preview
    - 评价(3):  review(POST) / reviews / rating
    - 报表(2):  overview / category
"""

from fastapi import APIRouter, Header, HTTPException, Query
from pydantic import BaseModel as PydBaseModel, Field

from services.alliance_service import AllianceService


router = APIRouter()
_service = AllianceService()


# ============================================================
# 鉴权与异常映射辅助
# ============================================================

def _require_admin(x_role: str | None):
    if x_role != "admin":
        raise HTTPException(status_code=403, detail="需要管理员权限")


def _require_member(x_member_id: str | None) -> int:
    if not x_member_id:
        raise HTTPException(status_code=401,
                            detail="未登录: 请提供 X-Member-Id 头")
    try:
        return int(x_member_id)
    except ValueError:
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

class ApplyRequest(PydBaseModel):
    memberId: int = Field(..., description="申请会员ID(须超级会员)")
    category: str = Field(...,
                          description="类目: water/tea/wine/dish/meat/fish/vessel/venue")
    shopName: str = Field(..., min_length=1, max_length=50, description="店铺名称")
    credentials: list = Field([], description="资质凭证列表(类目要求项)")
    referrerMemberId: int = Field(None, description="推荐人会员ID(分润归因)")


class AuditRequest(PydBaseModel):
    approved: bool = Field(..., description="是否通过")
    reviewer: str = Field("admin", max_length=50)
    note: str = Field("", max_length=200)


class ProductRequest(PydBaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    description: str = Field("", max_length=2000)
    price: float = Field(..., gt=0, description="商户售价")
    stock: int = Field(..., ge=0, description="库存")
    traceBatchNo: str = Field("", max_length=50,
                              description="溯源批次号(酒类必填)")
    traceCredentials: list = Field([], description="简化溯源凭证(非酒类必填)")


class OrderRequest(PydBaseModel):
    productId: int = Field(..., description="同盟商品ID")
    quantity: int = Field(1, ge=1, description="购买数量")


class ShareSettingsRequest(PydBaseModel):
    commissionRate: float = Field(None, gt=0, le=0.5,
                                  description="平台抽佣率(0-0.5)")
    shareRates: dict = Field(None,
                             description="五方分润比例(合计=1)")


class ReviewRequest(PydBaseModel):
    orderId: str = Field(..., max_length=50)
    score: int = Field(..., ge=1, le=5)
    content: str = Field("", max_length=500)


class FoldRequest(PydBaseModel):
    reason: str = Field("", max_length=200)


class CoverageRequest(PydBaseModel):
    level: str = Field(..., description="范围层级: city/district/grid")
    adcode: str = Field("", max_length=12, description="行政区划码(市/区县层)")
    gridKeys: list = Field([], description="网格键列表(grid 层可选)")
    centerLat: float = Field(None, ge=-90, le=90,
                             description="中心纬度(grid 层可代替 gridKeys)")
    centerLng: float = Field(None, ge=-180, le=180,
                             description="中心经度")


class GatheringRequest(PydBaseModel):
    partySize: int = Field(..., ge=1, le=50, description="聚会人数")
    wineProductId: int = Field(..., description="酒品ID(同盟在售)")
    dishMerchantId: int = Field(..., description="配菜商户ID(好菜类目)")
    venueMerchantId: int = Field(..., description="订境商户ID(好境类目)")
    gatheringTime: str = Field("", max_length=30, description="聚会时间")


class RedeemRequest(PydBaseModel):
    code: str = Field(..., min_length=6, max_length=30,
                      description="线下核销码")


class CustomDemandRequest(PydBaseModel):
    merchantId: int = Field(..., description="商户ID")
    demandType: str = Field(...,
                            description="定制类型: engraving/private_feast/sealing")
    description: str = Field(..., min_length=1, max_length=1000)
    budget: float = Field(0, ge=0, description="预算(可选)")


class QuoteRequest(PydBaseModel):
    quotedPrice: float = Field(..., gt=0, description="报价")


# ============================================================
# 入盟网关
# ============================================================

@router.post("/api/alliance/apply", tags=["AI智能网站同盟模块"])
async def apply(data: ApplyRequest):
    """超级会员入盟申请(自动 AI 预审: ≥80快车道/60-79人工审/<60拒)"""
    try:
        result = await _service.apply(
            member_id=data.memberId, category=data.category,
            shop_name=data.shopName, credentials=data.credentials,
            referrer_member_id=data.referrerMemberId)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.get("/api/alliance/applications", tags=["AI智能网站同盟模块"])
async def list_applications(
    x_role: str = Header(None, alias="X-Role"),
    status: str = Query(None, description="状态筛选"),
):
    """入盟申请列表(含 AI 预审报告)"""
    _require_admin(x_role)
    try:
        result = await _service.list_applications(status=status)
        return {"success": True, "data": result, "count": len(result)}
    except Exception as e:
        _handle(e)


@router.post("/api/alliance/applications/{application_id}/audit",
             tags=["AI智能网站同盟模块"])
async def audit_application(
    application_id: int,
    data: AuditRequest,
    x_role: str = Header(None, alias="X-Role"),
):
    """人工终审(通过→签约建档; 拒→rejected 触发 90 天冷却)"""
    _require_admin(x_role)
    try:
        result = await _service.audit_application(
            application_id=application_id, approved=data.approved,
            reviewer=data.reviewer, note=data.note)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.get("/api/alliance/merchants", tags=["AI智能网站同盟模块"])
async def list_merchants(
    x_role: str = Header(None, alias="X-Role"),
    status: str = Query(None, description="状态筛选"),
    category: str = Query(None, description="类目筛选"),
):
    """同盟商列表"""
    _require_admin(x_role)
    try:
        result = await _service.list_merchants(status=status,
                                               category=category)
        return {"success": True, "data": result, "count": len(result)}
    except Exception as e:
        _handle(e)


@router.post("/api/alliance/merchants/{merchant_id}/activate",
             tags=["AI智能网站同盟模块"])
async def activate_merchant(
    merchant_id: int,
    x_role: str = Header(None, alias="X-Role"),
):
    """激活试用期(signed→probation, 90 天)"""
    _require_admin(x_role)
    try:
        result = await _service.activate_merchant(merchant_id)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.post("/api/alliance/merchants/{merchant_id}/confirm",
             tags=["AI智能网站同盟模块"])
async def confirm_merchant(
    merchant_id: int,
    x_role: str = Header(None, alias="X-Role"),
):
    """试用转正(probation→active)"""
    _require_admin(x_role)
    try:
        result = await _service.confirm_merchant(merchant_id)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.post("/api/alliance/merchants/{merchant_id}/suspend",
             tags=["AI智能网站同盟模块"])
async def suspend_merchant(
    merchant_id: int,
    x_role: str = Header(None, alias="X-Role"),
    reason: str = Query("", description="暂停原因"),
):
    """暂停商户(在售商品自动下架)"""
    _require_admin(x_role)
    try:
        result = await _service.suspend_merchant(merchant_id, reason=reason)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.post("/api/alliance/merchants/{merchant_id}/terminate",
             tags=["AI智能网站同盟模块"])
async def terminate_merchant(
    merchant_id: int,
    x_role: str = Header(None, alias="X-Role"),
    reason: str = Query("", description="终止原因(退出/清退)"),
):
    """终止商户(主动退出/强制清退; 90 天冷却)"""
    _require_admin(x_role)
    try:
        result = await _service.terminate_merchant(merchant_id, reason=reason)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


# ============================================================
# 类目(公开)
# ============================================================

@router.get("/api/alliance/categories", tags=["AI智能网站同盟模块"])
async def list_categories():
    """类目字典(酒水不分家: 水茶酒菜肉鱼器境, 含溯源级别/资质要求)"""
    try:
        result = await _service.list_categories()
        return {"success": True, "data": result, "count": len(result)}
    except Exception as e:
        _handle(e)


# ============================================================
# 商品中心
# ============================================================

@router.post("/api/alliance/products", tags=["AI智能网站同盟模块"])
async def create_product(
    data: ProductRequest,
    x_member_id: str = Header(None, alias="X-Member-Id"),
):
    """商品上架(三道门禁: 资质/溯源/合规; 酒类须挂已放行批次)"""
    member_id = _require_member(x_member_id)
    try:
        # 以申请人会员身份定位其商铺
        merchant = await _service.repo.find_merchant_by_member(member_id)
        if merchant is None:
            raise ValueError("当前会员无同盟商铺, 请先入盟")
        result = await _service.create_product(
            merchant_id=merchant["merchantId"], name=data.name,
            description=data.description, price=data.price,
            stock=data.stock, trace_batch_no=data.traceBatchNo,
            trace_credentials=data.traceCredentials)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.get("/api/alliance/products", tags=["AI智能网站同盟模块"])
async def list_products(
    category: str = Query(None, description="类目筛选"),
    status: str = Query(None, description="状态筛选(active 默认公开)"),
    merchantId: int = Query(None, description="商户筛选"),
):
    """同盟商品列表(公开浏览)"""
    try:
        result = await _service.list_products(
            merchant_id=merchantId, category=category,
            status=status or "active")
        return {"success": True, "data": result, "count": len(result)}
    except Exception as e:
        _handle(e)


@router.get("/api/alliance/products/{product_id}", tags=["AI智能网站同盟模块"])
async def get_product(product_id: int):
    """商品详情(含溯源绑定信息)"""
    try:
        result = await _service.get_product(product_id)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.post("/api/alliance/products/{product_id}/offline",
             tags=["AI智能网站同盟模块"])
async def offline_product(
    product_id: int,
    x_member_id: str = Header(None, alias="X-Member-Id"),
):
    """商户下架自己的商品"""
    member_id = _require_member(x_member_id)
    try:
        product = await _service.get_product(product_id)
        merchant = await _service.repo.find_merchant_by_member(member_id)
        if merchant is None or merchant["merchantId"] != product["merchantId"]:
            raise ValueError("仅商品归属商户可下架")
        result = await _service.offline_product(product_id)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


# ============================================================
# 交易与分润
# ============================================================

@router.post("/api/alliance/order", tags=["AI智能网站同盟模块"])
async def place_order(
    data: OrderRequest,
    x_member_id: str = Header(None, alias="X-Member-Id"),
):
    """同盟商品下单(原子扣库存; 支付口径 P0=下单即付)"""
    member_id = _require_member(x_member_id)
    try:
        result = await _service.place_order(
            product_id=data.productId, buyer_id=member_id,
            quantity=data.quantity)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.get("/api/alliance/orders", tags=["AI智能网站同盟模块"])
async def list_orders(
    x_role: str = Header(None, alias="X-Role"),
    status: str = Query(None),
    merchantId: int = Query(None),
):
    """订单列表(admin)"""
    _require_admin(x_role)
    try:
        result = await _service.list_orders(merchant_id=merchantId,
                                            status=status)
        return {"success": True, "data": result, "count": len(result)}
    except Exception as e:
        _handle(e)


@router.post("/api/alliance/orders/{order_id}/settle",
             tags=["AI智能网站同盟模块"])
async def settle_order(
    order_id: str,
    x_role: str = Header(None, alias="X-Role"),
):
    """订单结算(15%抽佣五方拆账+总账双写+货款入账; 幂等)"""
    _require_admin(x_role)
    try:
        result = await _service.settle_order(order_id)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.post("/api/alliance/orders/{order_id}/reverse",
             tags=["AI智能网站同盟模块"])
async def reverse_settlement(
    order_id: str,
    x_role: str = Header(None, alias="X-Role"),
    reason: str = Query("", description="冲正原因(退款)"),
):
    """结算冲正(退款场景)"""
    _require_admin(x_role)
    try:
        result = await _service.reverse_settlement(order_id, reason=reason)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.get("/api/alliance/settlements", tags=["AI智能网站同盟模块"])
async def list_settlements(
    x_role: str = Header(None, alias="X-Role"),
    status: str = Query(None, description="状态筛选 settled/reversed"),
):
    """结算单列表(拆账明细/状态)"""
    _require_admin(x_role)
    try:
        result = await _service.list_settlements(status=status)
        return {"success": True, "data": result, "count": len(result)}
    except Exception as e:
        _handle(e)


@router.get("/api/alliance/settlements/{order_id}",
            tags=["AI智能网站同盟模块"])
async def get_settlement(
    order_id: str,
    x_role: str = Header(None, alias="X-Role"),
):
    """结算单详情(拆账明细/总账流水号/货款流水)"""
    _require_admin(x_role)
    try:
        result = await _service.get_settlement(order_id)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.post("/api/alliance/settle-run", tags=["AI智能网站同盟模块"])
async def run_scheduled_settlement(
    x_role: str = Header(None, alias="X-Role"),
):
    """触发 T+1 定时结算(调度器同款; 手动运维通道)"""
    _require_admin(x_role)
    try:
        result = await _service.run_scheduled_settlement()
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.get("/api/alliance/share-settings", tags=["AI智能网站同盟模块"])
async def get_share_settings(
    x_role: str = Header(None, alias="X-Role"),
):
    """分润配置查询(抽佣率/五方比例)"""
    _require_admin(x_role)
    try:
        result = await _service.get_share_settings()
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.put("/api/alliance/share-settings", tags=["AI智能网站同盟模块"])
async def update_share_settings(
    data: ShareSettingsRequest,
    x_role: str = Header(None, alias="X-Role"),
):
    """更新分润配置(比例合计须=1)"""
    _require_admin(x_role)
    try:
        result = await _service.update_share_settings(
            commission_rate=data.commissionRate,
            share_rates=data.shareRates)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.get("/api/alliance/share-preview", tags=["AI智能网站同盟模块"])
async def share_preview(
    x_role: str = Header(None, alias="X-Role"),
    amount: float = Query(..., gt=0, description="订单金额"),
):
    """分润拆账预览(成交价→15%抽佣五方明细)"""
    _require_admin(x_role)
    try:
        result = await _service.preview_shares(amount)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


# ============================================================
# 评价
# ============================================================

@router.post("/api/alliance/review", tags=["AI智能网站同盟模块"])
async def submit_review(
    data: ReviewRequest,
    x_member_id: str = Header(None, alias="X-Member-Id"),
):
    """消费者评价商户(结算后一单一评, 1-5星)"""
    member_id = _require_member(x_member_id)
    try:
        result = await _service.submit_review(
            order_id=data.orderId, reviewer_id=member_id,
            score=data.score, content=data.content)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.get("/api/alliance/reviews", tags=["AI智能网站同盟模块"])
async def list_reviews(
    merchantId: int = Query(None, description="商户筛选"),
    folded: bool = Query(None, description="折叠状态筛选"),
):
    """评价列表(公开; 默认含未折叠)"""
    try:
        result = await _service.list_reviews(merchant_id=merchantId,
                                             folded=folded)
        return {"success": True, "data": result, "count": len(result)}
    except Exception as e:
        _handle(e)


@router.get("/api/alliance/merchants/{merchant_id}/rating",
            tags=["AI智能网站同盟模块"])
async def get_merchant_rating(merchant_id: int):
    """商户星级概览(均分/数量/分布)"""
    try:
        result = await _service.get_merchant_rating(merchant_id)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.post("/api/alliance/reviews/{review_id}/fold",
             tags=["AI智能网站同盟模块"])
async def fold_review(
    review_id: int,
    data: FoldRequest,
    x_role: str = Header(None, alias="X-Role"),
):
    """折叠违规评价(P1 接 AI 语义审评自动折叠)"""
    _require_admin(x_role)
    try:
        result = await _service.fold_review(review_id, reason=data.reason)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


# ============================================================
# 报表
# ============================================================

@router.get("/api/alliance/report/overview", tags=["AI智能网站同盟模块"])
async def report_overview(
    x_role: str = Header(None, alias="X-Role"),
):
    """全景报表(商户/商品/订单GMV/分润汇总)"""
    _require_admin(x_role)
    try:
        result = await _service.report_overview()
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.get("/api/alliance/report/category", tags=["AI智能网站同盟模块"])
async def report_category(
    x_role: str = Header(None, alias="X-Role"),
):
    """类目维度报表(水茶酒菜肉鱼器境八类目)"""
    _require_admin(x_role)
    try:
        result = await _service.report_category()
        return {"success": True, "data": result, "count": len(result)}
    except Exception as e:
        _handle(e)


def register_alliance_routes(app):
    """注册37号·AI智能网站同盟模块路由"""
    app.include_router(router)


# ============================================================
# P1: 地图引擎 GeoGrid(admin/公开)
# ============================================================

@router.post("/api/alliance/merchants/{merchant_id}/coverage",
             tags=["AI智能网站同盟模块"])
async def apply_coverage(
    merchant_id: int,
    data: CoverageRequest,
    x_role: str = Header(None, alias="X-Role"),
):
    """申请服务范围(密度上限仲裁: 同网格同类目<gridCap 优质优先)"""
    _require_admin(x_role)
    try:
        from services.alliance_geo_service import AllianceGeoService
        result = await AllianceGeoService().apply_coverage(
            merchant_id=merchant_id, level=data.level,
            adcode=data.adcode, grid_keys=data.gridKeys,
            center_lat=data.centerLat, center_lng=data.centerLng)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.get("/api/alliance/merchants/{merchant_id}/coverage",
            tags=["AI智能网站同盟模块"])
async def merchant_coverage(
    merchant_id: int,
    x_role: str = Header(None, alias="X-Role"),
):
    """商户服务范围查询"""
    _require_admin(x_role)
    try:
        from services.alliance_geo_service import AllianceGeoService
        result = await AllianceGeoService().merchant_coverage(merchant_id)
        return {"success": True, "data": result, "count": len(result)}
    except Exception as e:
        _handle(e)


@router.get("/api/alliance/geo/nearby", tags=["AI智能网站同盟模块"])
async def nearby_merchants(
    lat: float = Query(..., ge=-90, le=90, description="纬度"),
    lng: float = Query(..., ge=-180, le=180, description="经度"),
    category: str = Query(None, description="类目筛选"),
    limit: int = Query(10, ge=1, le=50),
):
    """就近推荐商户(公开: 定位→网格→类目商户, 距离+评分排序)"""
    try:
        from services.alliance_geo_service import AllianceGeoService
        result = await AllianceGeoService().nearby_merchants(
            lat=lat, lng=lng, category=category, limit=limit)
        return {"success": True, "data": result, "count": len(result)}
    except Exception as e:
        _handle(e)


# ============================================================
# P1: 月度考核(admin)
# ============================================================

@router.post("/api/alliance/assessment/run", tags=["AI智能网站同盟模块"])
async def run_assessment(
    x_role: str = Header(None, alias="X-Role"),
    month: str = Query(None, description="考核月份(YYYY-MM, 空=当月)"),
    merchantId: int = Query(None, description="指定商户(空=全部 active)"),
):
    """执行月度考核(GMV/星级→S/A/B/C→连续C级暂停/清退)"""
    _require_admin(x_role)
    try:
        from services.alliance_geo_service import AllianceAssessmentService
        result = await AllianceAssessmentService().run_monthly(
            month=month, merchant_id=merchantId)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.get("/api/alliance/merchants/{merchant_id}/assessment",
            tags=["AI智能网站同盟模块"])
async def merchant_assessment(
    merchant_id: int,
    x_role: str = Header(None, alias="X-Role"),
):
    """商户考核历史"""
    _require_admin(x_role)
    try:
        from services.alliance_geo_service import AllianceAssessmentService
        result = await AllianceAssessmentService(
        ).merchant_assessment_history(merchant_id)
        return {"success": True, "data": result, "count": len(result)}
    except Exception as e:
        _handle(e)


# ============================================================
# P2: 场景服务(酒友小聚/线下核销/定制)
# ============================================================

@router.post("/api/alliance/scenes/gathering",
             tags=["AI智能网站同盟模块"])
async def create_gathering(
    data: GatheringRequest,
    x_member_id: str = Header(None, alias="X-Member-Id"),
):
    """酒友小聚编排出单(选酒→配菜→订境, 一单三子单+核销码)"""
    member_id = _require_member(x_member_id)
    try:
        from services.alliance_scene_service import AllianceSceneService
        result = await AllianceSceneService().create_gathering(
            user_id=member_id, party_size=data.partySize,
            wine_product_id=data.wineProductId,
            dish_merchant_id=data.dishMerchantId,
            venue_merchant_id=data.venueMerchantId,
            gathering_time=data.gatheringTime)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.get("/api/alliance/scenes", tags=["AI智能网站同盟模块"])
async def list_scenes(
    x_member_id: str = Header(None, alias="X-Member-Id"),
    status: str = Query(None),
):
    """我的场景订单(一单三子单+核销码状态)"""
    member_id = _require_member(x_member_id)
    try:
        from services.alliance_scene_service import AllianceSceneService
        result = await AllianceSceneService().list_scenes(
            user_id=member_id, status=status)
        return {"success": True, "data": result, "count": len(result)}
    except Exception as e:
        _handle(e)


@router.post("/api/alliance/redeem", tags=["AI智能网站同盟模块"])
async def redeem(
    data: RedeemRequest,
):
    """线下核销(到店扫码; 三子单立即结算分润, 幂等+72h有效)"""
    try:
        from services.alliance_scene_service import AllianceSceneService
        result = await AllianceSceneService().redeem(data.code)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.post("/api/alliance/custom-demands",
             tags=["AI智能网站同盟模块"])
async def create_custom_demand(
    data: CustomDemandRequest,
    x_member_id: str = Header(None, alias="X-Member-Id"),
):
    """提交定制需求(酒具刻字/私宴定制/封坛定制)"""
    member_id = _require_member(x_member_id)
    try:
        from services.alliance_scene_service import AllianceSceneService
        result = await AllianceSceneService().create_custom_demand(
            user_id=member_id, merchant_id=data.merchantId,
            demand_type=data.demandType, description=data.description,
            budget=data.budget)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.get("/api/alliance/custom-demands", tags=["AI智能网站同盟模块"])
async def list_custom_demands(
    x_role: str = Header(None, alias="X-Role"),
    status: str = Query(None),
    merchantId: int = Query(None),
):
    """定制需求列表(admin/商户侧)"""
    _require_admin(x_role)
    try:
        from services.alliance_scene_service import AllianceSceneService
        result = await AllianceSceneService().list_custom_demands(
            merchant_id=merchantId, status=status)
        return {"success": True, "data": result, "count": len(result)}
    except Exception as e:
        _handle(e)


@router.post("/api/alliance/custom-demands/{demand_id}/quote",
             tags=["AI智能网站同盟模块"])
async def quote_custom_demand(
    demand_id: int,
    data: QuoteRequest,
    x_role: str = Header(None, alias="X-Role"),
):
    """商户报价(demand→quoted)"""
    _require_admin(x_role)
    try:
        from services.alliance_scene_service import AllianceSceneService
        result = await AllianceSceneService().quote_custom_demand(
            demand_id, quoted_price=data.quotedPrice)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.post("/api/alliance/custom-demands/{demand_id}/confirm",
             tags=["AI智能网站同盟模块"])
async def confirm_custom_demand(
    demand_id: int,
    x_member_id: str = Header(None, alias="X-Member-Id"),
):
    """用户确认报价(quoted→confirmed; 须本人)"""
    member_id = _require_member(x_member_id)
    try:
        from services.alliance_scene_service import AllianceSceneService
        result = await AllianceSceneService().confirm_custom_demand(
            demand_id, user_id=member_id)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.post("/api/alliance/custom-demands/{demand_id}/advance",
             tags=["AI智能网站同盟模块"])
async def advance_custom_demand(
    demand_id: int,
    x_role: str = Header(None, alias="X-Role"),
    target: str = Query(..., description="目标状态: producing/delivered/cancelled"),
):
    """推进定制(商户/管理侧)"""
    _require_admin(x_role)
    try:
        from services.alliance_scene_service import AllianceSceneService
        result = await AllianceSceneService().advance_custom_demand(
            demand_id, target=target)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)
