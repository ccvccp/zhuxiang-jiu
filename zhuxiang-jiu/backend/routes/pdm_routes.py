"""38号·AI智能产品管理模块路由(P0, 19 端点)

鉴权(设计文档 §4):
    - 全部端点须登录(X-Member-Id + X-Role, JWT 中间件注入)
    - 权限判定在服务层: perm_grants(product 域) > JWT 角色(admin) > 403
    - operator(product.operate): 创建/编辑/提交/图片/上下架
    - auditor(product.approve): 人工终审
    - manage(product.manage): draft 直通上架/紧急下架

异常映射(遵循项目约定):
    - KeyError → 404(商品/版本/图片不存在)
    - ValueError → 409(状态非法转移/参数非法/SoD 冲突)
    - PermissionError → 403(无产品域权限)
"""

from fastapi import APIRouter, Header, HTTPException, Query
from pydantic import BaseModel as PydBaseModel, Field

from services.pdm_service import PdmService


router = APIRouter()
_service = PdmService()


# ============================================================
# 鉴权与异常映射辅助
# ============================================================

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
    if isinstance(exc, PermissionError):
        raise HTTPException(status_code=403, detail=str(exc))
    if isinstance(exc, ValueError):
        raise HTTPException(status_code=409, detail=str(exc))
    raise HTTPException(status_code=500, detail=str(exc))


# ============================================================
# 请求模型
# ============================================================

class ProductCreateRequest(PydBaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    subtitle: str = Field("", max_length=100)
    series: str = Field("经典系列", max_length=50)
    alcohol: int = Field(42, ge=1, le=80)
    volume: str = Field("500ml", max_length=20)
    price: float = Field(..., gt=0)
    originalPrice: float = Field(None, gt=0)
    stock: int = Field(0, ge=0)
    tags: list = Field([])
    scenes: list = Field([])
    description: str = Field("", max_length=2000)
    mainImage: str = Field("", max_length=500)


class ProductUpdateRequest(PydBaseModel):
    name: str = Field(None, min_length=1, max_length=100)
    subtitle: str = Field(None, max_length=100)
    description: str = Field(None, max_length=2000)
    tags: list = Field(None)
    scenes: list = Field(None)
    price: float = Field(None, gt=0)
    originalPrice: float = Field(None, gt=0)
    series: str = Field(None, max_length=50)
    alcohol: int = Field(None, ge=1, le=80)
    volume: str = Field(None, max_length=20)
    mainImage: str = Field(None, max_length=500)
    gallery: list = Field(None)
    changeType: str = Field(None, description="显式声明: cosmetic/substantive")


class ReviewRequest(PydBaseModel):
    approved: bool = Field(..., description="是否通过")
    note: str = Field("", max_length=200, description="驳回理由")


class DelistRequest(PydBaseModel):
    reason: str = Field(..., min_length=1, max_length=200,
                        description="下架原因(必填)")


class ImageUploadRequest(PydBaseModel):
    dataBase64: str = Field(..., min_length=1, description="图片 base64")
    ext: str = Field(".png", description="扩展名(.jpg/.png/.webp/.gif)")


class ImagesUpdateRequest(PydBaseModel):
    main: str = Field(..., min_length=1, max_length=500)
    gallery: list = Field([], description="细节图 URL 列表")


class ImageRollbackRequest(PydBaseModel):
    version: int = Field(..., ge=1, description="目标版本号")


class VersionRollbackRequest(PydBaseModel):
    version: int = Field(..., ge=1, description="目标版本号")


# ============================================================
# 商品管理(operator)
# ============================================================

@router.post("/api/pdm/products", tags=["AI智能产品管理模块"])
async def create_product(
    data: ProductCreateRequest,
    x_member_id: str = Header(None, alias="X-Member-Id"),
    x_role: str = Header(None, alias="X-Role"),
):
    """创建商品草稿(消费端不可见)"""
    member_id = _require_member(x_member_id)
    try:
        result = await _service.create_product(
            member_id, x_role, data.model_dump())
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.get("/api/pdm/products", tags=["AI智能产品管理模块"])
async def list_products(
    x_member_id: str = Header(None, alias="X-Member-Id"),
    x_role: str = Header(None, alias="X-Role"),
    status: str = Query(None,
                        description="筛选: draft/ai_reviewing/"
                                    "manual_reviewing/rejected/on_sale/off_sale"),
    limit: int = Query(100, ge=1, le=500),
):
    """商品管理列表(operator 及以上)"""
    member_id = _require_member(x_member_id)
    try:
        await _service.check_permission(member_id, x_role, "view")
        result = await _service.list_admin_products(status=status,
                                                    limit=limit)
        return {"success": True, "data": result, "count": len(result)}
    except Exception as e:
        _handle(e)


@router.get("/api/pdm/products/{product_id}",
            tags=["AI智能产品管理模块"])
async def get_product(
    product_id: str,
    x_member_id: str = Header(None, alias="X-Member-Id"),
    x_role: str = Header(None, alias="X-Role"),
):
    """商品管理视图(主数据 + 管理态合并)"""
    member_id = _require_member(x_member_id)
    try:
        await _service.check_permission(member_id, x_role, "view")
        result = await _service.get_admin_product(product_id)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.put("/api/pdm/products/{product_id}",
            tags=["AI智能产品管理模块"])
async def update_product(
    product_id: str,
    data: ProductUpdateRequest,
    x_member_id: str = Header(None, alias="X-Member-Id"),
    x_role: str = Header(None, alias="X-Role"),
):
    """编辑商品(cosmetic 微调 / substantive 实质变更回落重审)"""
    member_id = _require_member(x_member_id)
    try:
        result = await _service.update_product(
            member_id, x_role, product_id,
            {k: v for k, v in data.model_dump().items()
             if v is not None},
            change_type=data.changeType)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.post("/api/pdm/products/{product_id}/submit",
             tags=["AI智能产品管理模块"])
async def submit_product(
    product_id: str,
    x_member_id: str = Header(None, alias="X-Member-Id"),
    x_role: str = Header(None, alias="X-Role"),
):
    """提交审核(draft/rejected → AI 预审自动流转)"""
    member_id = _require_member(x_member_id)
    try:
        result = await _service.submit_product(member_id, x_role,
                                               product_id)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.post("/api/pdm/products/{product_id}/ai-precheck",
             tags=["AI智能产品管理模块"])
async def ai_precheck(
    product_id: str,
    x_member_id: str = Header(None, alias="X-Member-Id"),
    x_role: str = Header(None, alias="X-Role"),
):
    """手动触发 AI 预审(排障/复评, 不改状态)"""
    member_id = _require_member(x_member_id)
    try:
        await _service.check_permission(member_id, x_role, "view")
        result = await _service.ai_precheck(product_id)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


# ============================================================
# 审核(auditor)
# ============================================================

@router.get("/api/pdm/reviews/pending",
            tags=["AI智能产品管理模块"])
async def list_pending_reviews(
    x_member_id: str = Header(None, alias="X-Member-Id"),
    x_role: str = Header(None, alias="X-Role"),
    limit: int = Query(100, ge=1, le=500),
):
    """待审队列(含 AI 预审报告)"""
    member_id = _require_member(x_member_id)
    try:
        await _service.check_permission(member_id, x_role, "approve")
        result = await _service.list_reviews_pending(limit=limit)
        return {"success": True, "data": result, "count": len(result)}
    except Exception as e:
        _handle(e)


@router.post("/api/pdm/products/{product_id}/review",
             tags=["AI智能产品管理模块"])
async def review_product(
    product_id: str,
    data: ReviewRequest,
    x_member_id: str = Header(None, alias="X-Member-Id"),
    x_role: str = Header(None, alias="X-Role"),
):
    """人工终审(SoD: 审核人不得为最近实质编辑人)"""
    member_id = _require_member(x_member_id)
    try:
        result = await _service.review_product(
            member_id, x_role, product_id, data.approved, data.note)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


# ============================================================
# 上下架(operator / admin)
# ============================================================

@router.post("/api/pdm/products/{product_id}/list",
             tags=["AI智能产品管理模块"])
async def put_on_sale(
    product_id: str,
    x_member_id: str = Header(None, alias="X-Member-Id"),
    x_role: str = Header(None, alias="X-Role"),
):
    """上架(off_sale → on_sale, 幂等; draft 直通须 manage)"""
    member_id = _require_member(x_member_id)
    try:
        result = await _service.put_on_sale(member_id, x_role,
                                            product_id)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.post("/api/pdm/products/{product_id}/delist",
             tags=["AI智能产品管理模块"])
async def take_off_sale(
    product_id: str,
    data: DelistRequest,
    x_member_id: str = Header(None, alias="X-Member-Id"),
    x_role: str = Header(None, alias="X-Role"),
):
    """下架(on_sale → off_sale, reason 必填, 幂等)"""
    member_id = _require_member(x_member_id)
    try:
        result = await _service.take_off_sale(
            member_id, x_role, product_id, data.reason)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.post("/api/pdm/products/{product_id}/force-delist",
             tags=["AI智能产品管理模块"])
async def force_delist(
    product_id: str,
    data: DelistRequest,
    x_member_id: str = Header(None, alias="X-Member-Id"),
    x_role: str = Header(None, alias="X-Role"),
):
    """紧急下架(admin manage, 任意状态直达 off_sale, 跳过审批留痕)"""
    member_id = _require_member(x_member_id)
    try:
        result = await _service.force_delist(
            member_id, x_role, product_id, data.reason)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


# ============================================================
# 版本快照与回滚(operator)
# ============================================================

@router.get("/api/pdm/products/{product_id}/versions",
            tags=["AI智能产品管理模块"])
async def list_versions(
    product_id: str,
    x_member_id: str = Header(None, alias="X-Member-Id"),
    x_role: str = Header(None, alias="X-Role"),
):
    """版本列表(倒序, 含快照与变更类型)"""
    member_id = _require_member(x_member_id)
    try:
        await _service.check_permission(member_id, x_role, "view")
        result = await _service.list_versions(product_id)
        return {"success": True, "data": result, "count": len(result)}
    except Exception as e:
        _handle(e)


@router.post("/api/pdm/products/{product_id}/versions/rollback",
             tags=["AI智能产品管理模块"])
async def rollback_version(
    product_id: str,
    data: VersionRollbackRequest,
    x_member_id: str = Header(None, alias="X-Member-Id"),
    x_role: str = Header(None, alias="X-Role"),
):
    """版本回滚(= substantive 编辑, 在售商品回落 draft 重审)"""
    member_id = _require_member(x_member_id)
    try:
        result = await _service.rollback_version(
            member_id, x_role, product_id, data.version)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


# ============================================================
# 图片中心(operator)
# ============================================================

@router.post("/api/pdm/images", tags=["AI智能产品管理模块"])
async def upload_image(
    data: ImageUploadRequest,
    x_member_id: str = Header(None, alias="X-Member-Id"),
    x_role: str = Header(None, alias="X-Role"),
):
    """上传图片(base64 → hub media 管线 → 图库; P1 接 AI 审图)"""
    member_id = _require_member(x_member_id)
    try:
        result = await _service.upload_image(
            member_id, x_role, data.dataBase64, data.ext)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.get("/api/pdm/images", tags=["AI智能产品管理模块"])
async def list_images(
    x_member_id: str = Header(None, alias="X-Member-Id"),
    x_role: str = Header(None, alias="X-Role"),
    status: str = Query(None, description="usable/flagged"),
    limit: int = Query(100, ge=1, le=500),
):
    """图库列表"""
    member_id = _require_member(x_member_id)
    try:
        await _service.check_permission(member_id, x_role, "view")
        result = await _service.list_images(status=status, limit=limit)
        return {"success": True, "data": result, "count": len(result)}
    except Exception as e:
        _handle(e)


@router.get("/api/pdm/images/{image_id}",
            tags=["AI智能产品管理模块"])
async def get_image(
    image_id: int,
    x_member_id: str = Header(None, alias="X-Member-Id"),
    x_role: str = Header(None, alias="X-Role"),
):
    """图片详情(含审图报告)"""
    member_id = _require_member(x_member_id)
    try:
        await _service.check_permission(member_id, x_role, "view")
        result = await _service.get_image(image_id)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.put("/api/pdm/products/{product_id}/images",
            tags=["AI智能产品管理模块"])
async def update_images(
    product_id: str,
    data: ImagesUpdateRequest,
    x_member_id: str = Header(None, alias="X-Member-Id"),
    x_role: str = Header(None, alias="X-Role"),
):
    """更换商品图片组(substantive: 在售商品回落 draft 重审)"""
    member_id = _require_member(x_member_id)
    try:
        result = await _service.update_images(
            member_id, x_role, product_id, data.main, data.gallery)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.post("/api/pdm/products/{product_id}/images/rollback",
             tags=["AI智能产品管理模块"])
async def rollback_images(
    product_id: str,
    data: ImageRollbackRequest,
    x_member_id: str = Header(None, alias="X-Member-Id"),
    x_role: str = Header(None, alias="X-Role"),
):
    """图片组回滚(取历史版本快照 images, 须重审)"""
    member_id = _require_member(x_member_id)
    try:
        result = await _service.rollback_images(
            member_id, x_role, product_id, data.version)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


# ============================================================
# 看板报表
# ============================================================

@router.get("/api/pdm/report/overview",
            tags=["AI智能产品管理模块"])
async def report_overview(
    x_member_id: str = Header(None, alias="X-Member-Id"),
    x_role: str = Header(None, alias="X-Role"),
):
    """全景报表(各状态商品数/待审队列/AI 预审统计/图片/今日流水)"""
    member_id = _require_member(x_member_id)
    try:
        await _service.check_permission(member_id, x_role, "view")
        result = await _service.overview()
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


def register_pdm_routes(app):
    """注册38号·AI智能产品管理模块路由"""
    app.include_router(router)
