"""产品展示路由(7 端点)

端点:
    GET    /api/product/categories                  分类导航(返回分类树)
    GET    /api/product/list                         产品列表(筛选+排序+分页)
    GET    /api/product/search                       搜索(关键词匹配)
    GET    /api/product/hot                          热销推荐
    GET    /api/product/featured                     主推产品
    GET    /api/product/{product_id}                产品详情(含关联推荐)
    GET    /api/product/{product_id}/reviews        评价列表(分页)
    POST   /api/product/{product_id}/reviews        提交评价(需 X-Member-Id)

鉴权:
    - 提交评价: 需 X-Member-Id 头标识当前会员(Mock 模式)
    - 其他接口: 公开(浏览/搜索/详情)

异常映射(遵循项目约定):
    - KeyError  → 404(资源不存在)
    - ValueError → 409(参数/业务冲突)
"""

from typing import Annotated

from fastapi import APIRouter, Header, HTTPException, Query
from pydantic import BaseModel as PydBaseModel, Field

from services.product_service import (
    ProductService,
    DEFAULT_PAGE,
    DEFAULT_PAGE_SIZE,
)


router = APIRouter(prefix="/api/product", tags=["产品展示"])
_service = ProductService()


# ============================================================
#  请求模型
# ============================================================

class ReviewCreateRequest(PydBaseModel):
    rating: int = Field(..., ge=1, le=5, description="评分 1-5")
    content: str = Field(..., min_length=1, max_length=500, description="评价内容(1-500 字)")
    orderId: str | None = Field("", description="订单号(可选, 用于订单评价闭环)")
    images: list[str] | None = Field(None, description="评价图片URL列表(≤9张)")
    isAnonymous: bool = Field(False, description="是否匿名评价")


class ReviewUpdateRequest(PydBaseModel):
    rating: int | None = Field(None, ge=1, le=5, description="评分 1-5(可选)")
    content: str | None = Field(None, min_length=1, max_length=500, description="评价内容(可选)")
    images: list[str] | None = Field(None, description="评价图片URL列表(≤9张)")


class ReplyCreateRequest(PydBaseModel):
    content: str = Field(..., min_length=1, max_length=500, description="回复内容(1-500 字)")
    replierRole: str = Field("admin", description="回复角色 merchant/admin")
    replierName: str = Field("", description="回复人名称")
    parentReplyId: str | None = Field("", description="父回复ID(多轮对话)")


class ReportCreateRequest(PydBaseModel):
    reason: str = Field(..., description="举报原因 ad/abuse/porn/other")
    description: str | None = Field("", max_length=500, description="举报描述(≤500 字)")


class HandleReportRequest(PydBaseModel):
    action: str = Field(..., description="处理动作 confirmed/rejected")
    remark: str | None = Field("", max_length=500, description="处理备注")


class HideReviewRequest(PydBaseModel):
    isHide: bool = Field(True, description="True=隐藏, False=恢复")


# ============================================================
#  异常映射辅助(与 member_routes / order_routes 一致)
# ============================================================

def _map_key_error(exc: KeyError) -> HTTPException:
    msg = str(exc) if str(exc) else "资源不存在"
    if msg.startswith("'") and msg.endswith("'"):
        msg = msg[1:-1]
    return HTTPException(status_code=404, detail=msg)


def _map_value_error(exc: ValueError) -> HTTPException:
    return HTTPException(status_code=409, detail=str(exc))


def _require_member_id(x_member_id: str | None) -> int:
    """从 X-Member-Id 头提取会员ID, 缺失/格式错误返回 401"""
    if not x_member_id:
        raise HTTPException(status_code=401, detail="未登录: 请提供 X-Member-Id 头")
    try:
        return int(x_member_id)
    except (TypeError, ValueError):
        raise HTTPException(status_code=401, detail="X-Member-Id 格式不正确") from None


def _require_admin(x_role: str | None):
    """校验管理员权限, 失败返回 403"""
    if x_role != "admin":
        raise HTTPException(status_code=403, detail="需要管理员权限")


def _require_reply_role(x_role: str | None):
    """校验评价回复角色(商家/管理员), 失败返回 403

    与 service 层 add_reply 的角色白名单(merchant/admin)保持一致。
    """
    if x_role not in ("admin", "merchant"):
        raise HTTPException(status_code=403, detail="需要商家或管理员权限")


def _safe_int(value: str | None, *, field: str) -> int | None:
    """安全转换查询参数为 int, 非法时抛 409"""
    if value is None or value == "":
        return None
    try:
        return int(value)
    except ValueError:
        raise HTTPException(status_code=409, detail=f"{field} 须为整数") from None


def _safe_float(value: str | None, *, field: str) -> float | None:
    """安全转换查询参数为 float, 非法时抛 409"""
    if value is None or value == "":
        return None
    try:
        return float(value)
    except ValueError:
        raise HTTPException(status_code=409, detail=f"{field} 须为数字") from None


# ============================================================
#  分类导航: GET /api/product/categories
# ============================================================

@router.get("/categories")
async def get_categories():
    """分类导航(返回分类树: 系列/度数/容量/价格区间/场景)"""
    return await _service.get_categories()


# ============================================================
#  评价管理全局路由(静态路径优先, 避免被 GET /{product_id} 捕获)
# ============================================================

@router.get("/reviews/by-order/{order_id}")
async def get_review_by_order(order_id: str):
    """按订单号查询评价(判断是否已评价)"""
    try:
        return await _service.get_review_by_order(order_id)
    except KeyError as e:
        raise _map_key_error(e) from e
    except ValueError as e:
        raise _map_value_error(e) from e


@router.get("/reviews/reports")
async def list_reports(
    status: str | None = Query(None, description="状态筛选 pending/confirmed/rejected"),
    x_role: Annotated[str | None, Header(alias="X-Role")] = None,
):
    """举报列表(管理员)"""
    _require_admin(x_role)
    try:
        return await _service.list_reports(status=status)
    except ValueError as e:
        raise _map_value_error(e) from e


@router.post("/reviews/reports/{report_id}/handle")
async def handle_report(
    report_id: str,
    req: HandleReportRequest,
    x_role: Annotated[str | None, Header(alias="X-Role")] = None,
    x_member_id: Annotated[str | None, Header(alias="X-Member-Id")] = None,
):
    """处理举报(管理员)"""
    _require_admin(x_role)
    try:
        return await _service.handle_report(
            report_id=report_id,
            handler_id=x_member_id or "admin",
            action=req.action,
            remark=req.remark or "",
        )
    except KeyError as e:
        raise _map_key_error(e) from e
    except ValueError as e:
        raise _map_value_error(e) from e


# ============================================================
#  产品列表: GET /api/product/list
# ============================================================

@router.get("/list")
async def list_products(
    category: str | None = Query(default=None, description="分类(兼容字段, 等同 series)"),
    series: str | None = Query(default=None, description="系列(经典/珍藏/年份/礼盒/便携/典藏/竹香)"),
    alcohol: str | None = Query(default=None, description="度数(42/45/50/52/53)"),
    volume: str | None = Query(default=None, description="容量(250ml/500ml/500ml×2/750ml)"),
    price_min: str | None = Query(default=None, description="价格下限(元)"),
    price_max: str | None = Query(default=None, description="价格上限(元)"),
    scene: str | None = Query(default=None, description="场景(商务宴请/高端礼赠/老友小聚/收藏投资/团购定制)"),
    sort: str = Query(default="comprehensive",
                      description="排序(comprehensive/sales/price_asc/price_desc/new/rating)"),
    page: int = Query(default=DEFAULT_PAGE, ge=1, description="页码(从 1 开始)"),
    page_size: int = Query(default=DEFAULT_PAGE_SIZE, ge=1, le=100,
                           description="每页条数(1-100)"),
):
    """产品列表(筛选 + 排序 + 分页)

    支持筛选: series/category(系列)/alcohol(度数)/volume(容量)/price_min/price_max(价格区间)/scene(场景)
    支持排序: comprehensive(综合,默认)/sales(销量)/price_asc(价格升序)/price_desc(价格降序)/new(新品)/rating(评分)
    """
    # category 兼容字段(等同 series)
    series_val = series or category
    filters = {
        "series": series_val,
        "alcohol": _safe_int(alcohol, field="alcohol"),
        "volume": volume,
        "price_min": _safe_float(price_min, field="price_min"),
        "price_max": _safe_float(price_max, field="price_max"),
        "scene": scene,
    }
    try:
        return await _service.list_products(
            filters=filters, sort=sort, page=page, page_size=page_size)
    except KeyError as e:
        raise _map_key_error(e) from e
    except ValueError as e:
        raise _map_value_error(e) from e


# ============================================================
#  搜索: GET /api/product/search
# ============================================================

@router.get("/search")
async def search_products(
    keyword: str = Query(default="", description="搜索关键词(必填)"),
    page: int = Query(default=DEFAULT_PAGE, ge=1, description="页码"),
    page_size: int = Query(default=DEFAULT_PAGE_SIZE, ge=1, le=100,
                           description="每页条数"),
):
    """关键词搜索(匹配 name/subtitle/series/tags/scenes)

    Raises:
        409: 关键词为空
    """
    try:
        return await _service.search(keyword, page=page, page_size=page_size)
    except ValueError as e:
        raise _map_value_error(e) from e


# ============================================================
#  热销推荐: GET /api/product/hot
# ============================================================

@router.get("/hot")
async def get_hot_products(
    limit: int = Query(default=6, ge=1, le=50, description="返回条数(1-50)"),
):
    """热销推荐(按 sales_monthly 降序)"""
    try:
        return await _service.get_hot_products(limit=limit)
    except ValueError as e:
        raise _map_value_error(e) from e


# ============================================================
#  主推产品: GET /api/product/featured
# ============================================================

@router.get("/featured")
async def get_featured_products(
    limit: int = Query(default=4, ge=1, le=20, description="返回条数(1-20)"),
):
    """主推产品(featured=True, 按 hot_rank 升序)"""
    try:
        return await _service.get_featured(limit=limit)
    except ValueError as e:
        raise _map_value_error(e) from e


# ============================================================
#  产品详情: GET /api/product/{product_id}
# ============================================================

@router.get("/{product_id}")
async def get_product_detail(product_id: str):
    """产品详情(含 4 条关联推荐)"""
    try:
        return await _service.get_detail(product_id)
    except KeyError as e:
        raise _map_key_error(e) from e


# ============================================================
#  评价列表: GET /api/product/{product_id}/reviews
# ============================================================

@router.get("/{product_id}/reviews")
async def list_reviews(
    product_id: str,
    page: int = Query(default=DEFAULT_PAGE, ge=1, description="页码"),
    page_size: int = Query(default=10, ge=1, le=100, description="每页条数"),
):
    """评价列表(分页)"""
    try:
        return await _service.list_reviews(product_id, page=page, page_size=page_size)
    except KeyError as e:
        raise _map_key_error(e) from e
    except ValueError as e:
        raise _map_value_error(e) from e


# ============================================================
#  提交评价: POST /api/product/{product_id}/reviews
# ============================================================

@router.post("/{product_id}/reviews")
async def create_review(
    product_id: str,
    req: ReviewCreateRequest,
    x_member_id: Annotated[str | None, Header(alias="X-Member-Id")] = None,
):
    """提交评价(需 X-Member-Id 头, rating 1-5, content 1-500 字)

    可选: orderId(订单评价闭环, 同订单同产品仅可评价一次)/
    images(≤9张)/isAnonymous(匿名)
    """
    member_id = _require_member_id(x_member_id)
    try:
        return await _service.add_review(
            product_id=product_id,
            member_id=member_id,
            member_nickname="",  # Mock 模式暂不查会员资料, service 内兜底
            rating=req.rating,
            content=req.content,
            order_id=req.orderId or "",
            images=req.images,
            is_anonymous=req.isAnonymous,
        )
    except KeyError as e:
        raise _map_key_error(e) from e
    except ValueError as e:
        raise _map_value_error(e) from e


# ============================================================
#  注册函数
# ============================================================

def register_product_routes(app):
    """注册产品展示路由到 FastAPI app"""
    app.include_router(router)


# ============================================================
#  评价管理扩展路由(9 端点, 静态路径已在 categories 后注册)
#  带 {product_id} 的动态路由 → 定义在 GET /{product_id} 之后即可
# ============================================================

@router.get("/{product_id}/reviews/{review_id}")
async def get_review_detail(product_id: str, review_id: str):
    """评价详情(含回复列表)"""
    try:
        return await _service.get_review_detail(product_id, review_id)
    except KeyError as e:
        raise _map_key_error(e) from e


@router.put("/{product_id}/reviews/{review_id}")
async def update_review(
    product_id: str,
    review_id: str,
    req: ReviewUpdateRequest,
    x_member_id: Annotated[str | None, Header(alias="X-Member-Id")] = None,
    x_role: Annotated[str | None, Header(alias="X-Role")] = None,
):
    """修改评价(仅本人或管理员)"""
    member_id = _require_member_id(x_member_id)
    is_admin = (x_role == "admin")
    try:
        return await _service.update_review(
            product_id=product_id,
            review_id=review_id,
            member_id=member_id,
            rating=req.rating,
            content=req.content,
            images=req.images,
            is_admin=is_admin,
        )
    except KeyError as e:
        raise _map_key_error(e) from e
    except ValueError as e:
        raise _map_value_error(e) from e


@router.delete("/{product_id}/reviews/{review_id}")
async def delete_review(
    product_id: str,
    review_id: str,
    x_member_id: Annotated[str | None, Header(alias="X-Member-Id")] = None,
    x_role: Annotated[str | None, Header(alias="X-Role")] = None,
):
    """删除评价(仅本人或管理员)"""
    member_id = _require_member_id(x_member_id)
    is_admin = (x_role == "admin")
    try:
        return await _service.delete_review(
            product_id=product_id,
            review_id=review_id,
            member_id=member_id,
            is_admin=is_admin,
        )
    except KeyError as e:
        raise _map_key_error(e) from e
    except ValueError as e:
        raise _map_value_error(e) from e


# ---------- 评价回复 ----------

@router.post("/{product_id}/reviews/{review_id}/replies")
async def create_reply(
    product_id: str,
    review_id: str,
    req: ReplyCreateRequest,
    x_member_id: Annotated[str | None, Header(alias="X-Member-Id")] = None,
    x_role: Annotated[str | None, Header(alias="X-Role")] = None,
):
    """提交评价回复(商家/管理员)"""
    _require_reply_role(x_role)
    try:
        return await _service.add_reply(
            product_id=product_id,
            review_id=review_id,
            replier_id=x_member_id or x_role,
            replier_role=req.replierRole,
            replier_name=req.replierName,
            content=req.content,
            parent_reply_id=req.parentReplyId or "",
        )
    except KeyError as e:
        raise _map_key_error(e) from e
    except ValueError as e:
        raise _map_value_error(e) from e


@router.get("/{product_id}/reviews/{review_id}/replies")
async def list_replies(product_id: str, review_id: str):
    """评价回复列表"""
    try:
        return await _service.list_replies(product_id, review_id)
    except KeyError as e:
        raise _map_key_error(e) from e


# ---------- 评价点赞(P1) ----------

@router.post("/{product_id}/reviews/{review_id}/like")
async def like_review(
    product_id: str,
    review_id: str,
    x_member_id: Annotated[str | None, Header(alias="X-Member-Id")] = None,
):
    """点赞评价"""
    member_id = _require_member_id(x_member_id)
    try:
        return await _service.like_review(review_id, member_id)
    except ValueError as e:
        raise _map_value_error(e) from e


@router.delete("/{product_id}/reviews/{review_id}/like")
async def unlike_review(
    product_id: str,
    review_id: str,
    x_member_id: Annotated[str | None, Header(alias="X-Member-Id")] = None,
):
    """取消点赞"""
    member_id = _require_member_id(x_member_id)
    try:
        return await _service.unlike_review(review_id, member_id)
    except ValueError as e:
        raise _map_value_error(e) from e


# ---------- 评价举报(P1) ----------

@router.post("/{product_id}/reviews/{review_id}/report")
async def report_review(
    product_id: str,
    review_id: str,
    req: ReportCreateRequest,
    x_member_id: Annotated[str | None, Header(alias="X-Member-Id")] = None,
):
    """举报评价"""
    member_id = _require_member_id(x_member_id)
    try:
        return await _service.report_review(
            review_id=review_id,
            reporter_id=member_id,
            reason=req.reason,
            description=req.description or "",
        )
    except ValueError as e:
        raise _map_value_error(e) from e


# ---------- 评价隐藏/恢复(P1, 管理员) ----------

@router.put("/{product_id}/reviews/{review_id}/hide")
async def hide_review(
    product_id: str,
    review_id: str,
    req: HideReviewRequest,
    x_role: Annotated[str | None, Header(alias="X-Role")] = None,
):
    """管理员隐藏/恢复评价"""
    _require_admin(x_role)
    try:
        return await _service.hide_review(product_id, review_id, is_hide=req.isHide)
    except KeyError as e:
        raise _map_key_error(e) from e
    except ValueError as e:
        raise _map_value_error(e) from e
