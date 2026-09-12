"""68号 P6-P7·信值·臻选平台化路由(角色店铺 + 店铺铺货)

端点(21):
    ---- P6 角色店铺(10) ----
    GET  /api/xinzhi/shop/statuses          状态字典(九态+门槛公示)
    POST /api/xinzhi/shop/apply             开店申请(X-Member-Id)*
    GET  /api/xinzhi/shop/mine              我的店铺(X-Member-Id)
    GET  /api/xinzhi/shop/{shopId}          店铺主页公开信息
    GET  /api/xinzhi/shops                  店铺列表(admin, 按 status)
    POST /api/xinzhi/shop/{shopId}/review   人工审核(admin, 建议书)
    POST /api/xinzhi/shop/{shopId}/suspend  暂停整改(admin)
    POST /api/xinzhi/shop/{shopId}/activate 激活/恢复(admin)
    POST /api/xinzhi/shop/{shopId}/close    商家自关(X-Member-Id, 仅 active)
    GET  /api/xinzhi/shop/{shopId}/radar-snapshot  雷达快照+预警建议书

    ---- P7 店铺铺货(11) ----
    GET  /api/xinzhi/listing/gates          门禁字典(四门禁公示)
    POST /api/xinzhi/listing/submit         铺货发布(X-Member-Id)*
    GET  /api/xinzhi/listing/mine           我的铺货(X-Member-Id)
    GET  /api/xinzhi/listing/{id}           铺货详情(归属/admin)
    GET  /api/xinzhi/listings               铺货列表(admin, 按 status)
    POST /api/xinzhi/listing/{id}/review    铺货审核(admin, 四门禁复核)*
    POST /api/xinzhi/listing/{id}/delist    商家下架(X-Member-Id)
    POST /api/xinzhi/listing/{id}/list      商家重新上架(X-Member-Id)
    POST /api/xinzhi/listing/{id}/remove    平台移除(admin, 建议书)
    GET  /api/xinzhi/shelf                  公开货架(按品类聚合)
    GET  /api/xinzhi/shop/{shopId}/listings 店铺商品(公开)

    * 审核/暂停/预警类响应含 disposition 字段——建议书模式,
      处罚类决策永不自动执行(宪法域)。

鉴权(68号惯例):
    - 商家侧: X-Member-Id 定位店铺归属(非本店资源 403)
    - 管理侧: X-Role: admin
异常映射: KeyError→404 / ValueError→409 / PermissionError→403
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


def _require_admin(x_role: str | None) -> None:
    if x_role != "admin":
        raise HTTPException(status_code=403,
                            detail="需要管理员权限(X-Role: admin)")


def _handle(e: Exception):
    if isinstance(e, KeyError):
        raise HTTPException(status_code=404, detail=str(e))
    if isinstance(e, ValueError):
        raise HTTPException(status_code=409, detail=str(e))
    if isinstance(e, PermissionError):
        raise HTTPException(status_code=403, detail=str(e))
    raise HTTPException(status_code=500, detail=str(e))


def _shop_service():
    from services.xinzhi_shop_service import XinzhiShopService
    return XinzhiShopService()


def _listing_service():
    from services.xinzhi_listing_service import (
        XinzhiListingService)
    return XinzhiListingService()


# ============================================================
# 请求模型
# ============================================================

class ShopApplyRequest(PydBaseModel):
    shopName: str = Field(...,
                          description="店铺名称(≤20字, 服务端校验)")
    category: str = Field(...,
                          description="类目: wine/vessel/venue")
    intro: str = Field("", description="店铺简介(≤100字; "
                                       "空=签名材料不全)")


class ShopReviewRequest(PydBaseModel):
    approved: bool = Field(..., description="是否通过")
    note: str = Field("", max_length=200, description="审核备注")


class ShopSuspendRequest(PydBaseModel):
    reason: str = Field("", max_length=200, description="暂停原因")


class ShopCloseRequest(PydBaseModel):
    reason: str = Field("", max_length=200, description="关店原因")


class ListingSubmitRequest(PydBaseModel):
    productId: str = Field(..., description="主站商品ID(只读选品)")


class ListingReviewRequest(PydBaseModel):
    approved: bool = Field(..., description="是否通过")
    note: str = Field("", max_length=200, description="审核备注")


class ListingRemoveRequest(PydBaseModel):
    reason: str = Field("", max_length=200, description="移除原因")


# ============================================================
# P6 角色店铺(固定路径先于参数路径声明)
# ============================================================

@router.get("/api/xinzhi/shop/statuses",
            tags=["信值臻选购物平台"])
async def xinzhi_shop_statuses():
    """店铺状态字典(九态状态机+转移表+门槛公示——公开口径)"""
    return {"success": True, "data": _shop_service().statuses()}


@router.post("/api/xinzhi/shop/apply",
             tags=["信值臻选购物平台"])
async def xinzhi_shop_apply(
        req: ShopApplyRequest,
        x_member_id: Annotated[str | None,
                               Header(alias="X-Member-Id")]
        = None):
    """开店申请(准入门槛: 雷达总分≥60+会员等级≥L3+一人一铺;
    AI 预审三档确定性路由——≥80 快车道 pending→signed 直接)"""
    mid = _member_id(x_member_id)
    try:
        return {"success": True, "data":
                await _shop_service().apply(
                    mid, req.shopName, req.category, req.intro)}
    except Exception as e:
        _handle(e)


@router.get("/api/xinzhi/shop/mine",
            tags=["信值臻选购物平台"])
async def xinzhi_shop_mine(
        x_member_id: Annotated[str | None,
                               Header(alias="X-Member-Id")]
        = None):
    """我的店铺(未开店返回 data=None)"""
    mid = _member_id(x_member_id)
    try:
        return {"success": True, "data":
                await _shop_service().get_my_shop(mid)}
    except Exception as e:
        _handle(e)


@router.get("/api/xinzhi/shops",
            tags=["信值臻选购物平台"])
async def xinzhi_shops(
        x_role: Annotated[str | None,
                          Header(alias="X-Role")] = None,
        status: str = None,
        limit: int = 200):
    """店铺列表(admin; 可按 status 过滤)"""
    _require_admin(x_role)
    try:
        return {"success": True, "data":
                await _shop_service().list_shops(
                    status=status, limit=limit)}
    except Exception as e:
        _handle(e)


@router.get("/api/xinzhi/shop/{shop_id}",
            tags=["信值臻选购物平台"])
async def xinzhi_shop_page(shop_id: int):
    """店铺主页公开信息(零个体数据——不含 memberId/审核明细)"""
    try:
        return {"success": True, "data":
                await _shop_service().get_shop(shop_id)}
    except Exception as e:
        _handle(e)


@router.post("/api/xinzhi/shop/{shop_id}/review",
             tags=["信值臻选购物平台"])
async def xinzhi_shop_review(
        shop_id: int,
        req: ShopReviewRequest,
        x_role: Annotated[str | None,
                          Header(alias="X-Role")] = None):
    """店铺人工审核(manual_reviewing→signed/rejected;
    审核结果带 disposition——人工执行留痕)"""
    _require_admin(x_role)
    try:
        return {"success": True, "data":
                await _shop_service().review_shop(
                    shop_id, req.approved, req.note)}
    except Exception as e:
        _handle(e)


@router.post("/api/xinzhi/shop/{shop_id}/suspend",
             tags=["信值臻选购物平台"])
async def xinzhi_shop_suspend(
        shop_id: int,
        req: ShopSuspendRequest,
        x_role: Annotated[str | None,
                          Header(alias="X-Role")] = None):
    """暂停整改(admin; active/probation→suspended——处罚类
    须 admin 确认, disposition 留痕)"""
    _require_admin(x_role)
    try:
        return {"success": True, "data":
                await _shop_service().suspend_shop(
                    shop_id, req.reason)}
    except Exception as e:
        _handle(e)


@router.post("/api/xinzhi/shop/{shop_id}/activate",
             tags=["信值臻选购物平台"])
async def xinzhi_shop_activate(
        shop_id: int,
        x_role: Annotated[str | None,
                          Header(alias="X-Role")] = None):
    """激活/恢复(admin; signed→probation→active(30天观察)/
    suspended→active 三路径)"""
    _require_admin(x_role)
    try:
        return {"success": True, "data":
                await _shop_service().activate_shop(shop_id)}
    except Exception as e:
        _handle(e)


@router.post("/api/xinzhi/shop/{shop_id}/close",
             tags=["信值臻选购物平台"])
async def xinzhi_shop_close(
        shop_id: int,
        req: ShopCloseRequest,
        x_member_id: Annotated[str | None,
                               Header(alias="X-Member-Id")]
        = None):
    """商家自关店(仅 active→terminated; 非本店资源 403)"""
    mid = _member_id(x_member_id)
    try:
        return {"success": True, "data":
                await _shop_service().close_shop(
                    shop_id, mid, req.reason)}
    except Exception as e:
        _handle(e)


@router.get("/api/xinzhi/shop/{shop_id}/radar-snapshot",
            tags=["信值臻选购物平台"])
async def xinzhi_shop_radar_snapshot(shop_id: int):
    """店铺雷达定期快照(留痕)+跌破 40 预警建议书——
    永不自动 suspend, 须 admin 确认(宪法域)"""
    try:
        return {"success": True, "data":
                await _shop_service().radar_check(shop_id)}
    except Exception as e:
        _handle(e)


# ============================================================
# P7 店铺铺货(固定路径先于参数路径声明)
# ============================================================

@router.get("/api/xinzhi/listing/gates",
            tags=["信值臻选购物平台"])
async def xinzhi_listing_gates():
    """铺货门禁字典(四门禁规则/阈值/状态机公示——公开口径)"""
    return {"success": True, "data":
            _listing_service().gates_dict()}


@router.post("/api/xinzhi/listing/submit",
             tags=["信值臻选购物平台"])
async def xinzhi_listing_submit(
        req: ListingSubmitRequest,
        x_member_id: Annotated[str | None,
                               Header(alias="X-Member-Id")]
        = None):
    """铺货发布(主站商品池只读选品→四门禁逐道检查留痕;
    全过→reviewing, 任一失败→draft)"""
    mid = _member_id(x_member_id)
    try:
        return {"success": True, "data":
                await _listing_service().submit(
                    mid, req.productId)}
    except Exception as e:
        _handle(e)


@router.get("/api/xinzhi/listing/mine",
            tags=["信值臻选购物平台"])
async def xinzhi_listing_mine(
        x_member_id: Annotated[str | None,
                               Header(alias="X-Member-Id")]
        = None,
        limit: int = 200):
    """我的铺货(商家维度, 最新优先)"""
    mid = _member_id(x_member_id)
    try:
        return {"success": True, "data":
                await _listing_service().my_listings(
                    mid, limit=limit)}
    except Exception as e:
        _handle(e)


@router.get("/api/xinzhi/listings",
            tags=["信值臻选购物平台"])
async def xinzhi_listings(
        x_role: Annotated[str | None,
                          Header(alias="X-Role")] = None,
        status: str = None,
        limit: int = 200):
    """铺货列表(admin; 可按 status 过滤)"""
    _require_admin(x_role)
    try:
        return {"success": True, "data":
                await _listing_service().admin_list(
                    status=status, limit=limit)}
    except Exception as e:
        _handle(e)


@router.get("/api/xinzhi/listing/{listing_id}",
            tags=["信值臻选购物平台"])
async def xinzhi_listing_detail(
        listing_id: int,
        x_member_id: Annotated[str | None,
                               Header(alias="X-Member-Id")]
        = None,
        x_role: Annotated[str | None,
                          Header(alias="X-Role")] = None):
    """铺货详情(完整记录含门禁明细; 归属商家或 admin,
    非本店资源 403)"""
    mid = _member_id(x_member_id)
    try:
        listing = await _listing_service().get_listing(listing_id)
        if x_role != "admin" and listing.get("memberId") != mid:
            raise PermissionError(
                f"非本店资源(listingId={listing_id})")
        return {"success": True, "data": listing}
    except Exception as e:
        _handle(e)


@router.post("/api/xinzhi/listing/{listing_id}/review",
             tags=["信值臻选购物平台"])
async def xinzhi_listing_review(
        listing_id: int,
        req: ListingReviewRequest,
        x_role: Annotated[str | None,
                          Header(alias="X-Role")] = None):
    """铺货审核(admin, 四门禁复核+approve→listed/reject→
    removed; 审核结果带 disposition——建议书模式)"""
    _require_admin(x_role)
    try:
        return {"success": True, "data":
                await _listing_service().review_listing(
                    listing_id, req.approved, req.note)}
    except Exception as e:
        _handle(e)


@router.post("/api/xinzhi/listing/{listing_id}/delist",
             tags=["信值臻选购物平台"])
async def xinzhi_listing_delist(
        listing_id: int,
        x_member_id: Annotated[str | None,
                               Header(alias="X-Member-Id")]
        = None):
    """商家下架(仅 listed→delisted; 非本店资源 403)"""
    mid = _member_id(x_member_id)
    try:
        return {"success": True, "data":
                await _listing_service().delist(listing_id, mid)}
    except Exception as e:
        _handle(e)


@router.post("/api/xinzhi/listing/{listing_id}/list",
             tags=["信值臻选购物平台"])
async def xinzhi_listing_list(
        listing_id: int,
        x_member_id: Annotated[str | None,
                               Header(alias="X-Member-Id")]
        = None):
    """商家重新上架(仅 delisted→listed; 非本店资源 403)"""
    mid = _member_id(x_member_id)
    try:
        return {"success": True, "data":
                await _listing_service().relist(
                    listing_id, mid)}
    except Exception as e:
        _handle(e)


@router.post("/api/xinzhi/listing/{listing_id}/remove",
             tags=["信值臻选购物平台"])
async def xinzhi_listing_remove(
        listing_id: int,
        req: ListingRemoveRequest,
        x_role: Annotated[str | None,
                          Header(alias="X-Role")] = None):
    """平台移除(违规——处置类, admin 确认后执行,
    disposition 留痕)"""
    _require_admin(x_role)
    try:
        return {"success": True, "data":
                await _listing_service().remove_listing(
                    listing_id, req.reason)}
    except Exception as e:
        _handle(e)


@router.get("/api/xinzhi/shelf",
            tags=["信值臻选购物平台"])
async def xinzhi_listing_shelf(
        category: str = None,
        limit: int = 20):
    """公开货架(listed 按品类=主站商品系列聚合+店铺名,
    对齐 xinzhi prime 货架风格——信值价快照展示)"""
    try:
        return {"success": True, "data":
                await _listing_service().shelf(
                    category=category, limit=limit)}
    except Exception as e:
        _handle(e)


@router.get("/api/xinzhi/shop/{shop_id}/listings",
            tags=["信值臻选购物平台"])
async def xinzhi_shop_listing_view(
        shop_id: int,
        limit: int = 200):
    """店铺商品(公开——listed/delisted 透明可见, removed 隐藏)"""
    try:
        return {"success": True, "data":
                await _listing_service().shop_listings(
                    shop_id, limit=limit)}
    except Exception as e:
        _handle(e)


def register_xinzhi_shop_routes(app) -> None:
    """注册信值臻选平台化路由(main.py startup 调用)"""
    app.include_router(router)
