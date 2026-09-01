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
