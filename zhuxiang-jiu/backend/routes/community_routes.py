# -*- coding: utf-8 -*-
"""站内内容社区路由(蓝图对照分析近期项③, 6 端点)

鉴权:
    - 公开(2): 内容 feed / approved 帖子列表(只读)
    - 登录态(1): 发帖(X-Member-Id, 对齐 order_routes 会员鉴权惯例)
    - 管理端(3): 商品挂载 / 待审帖 / 审核流转(X-Role: admin)

响应包裹形态对齐 live_qa/knowledge_routes: {code: 0, msg: "ok", data: ...}。
异常转换对齐 promo_routes: KeyError → 404 / ValueError → 409。
"""

from fastapi import APIRouter, Header, HTTPException, Query
from pydantic import BaseModel as PydBaseModel, Field

from services.community_service import CommunityService

router = APIRouter()
_service = CommunityService()


def _require_member(x_member_id: str) -> int:
    """校验登录态(对齐 order_routes: X-Member-Id 头)"""
    if not x_member_id or not str(x_member_id).strip():
        raise HTTPException(status_code=401, detail="未登录: 缺少 X-Member-Id 头")
    try:
        return int(str(x_member_id).strip())
    except ValueError:
        raise HTTPException(
            status_code=401, detail="X-Member-Id 须为数字") from None


def _require_admin(x_role: str):
    """校验管理员权限(对齐 knowledge_routes/live_qa_routes 写法)"""
    if x_role != "admin":
        raise HTTPException(status_code=403, detail="需要管理员权限")


def _raise_mapped(exc: Exception):
    """业务异常转换(对齐 promo_routes: KeyError→404 / ValueError→409)"""
    if isinstance(exc, KeyError):
        msg = str(exc.args[0]) if exc.args else str(exc)
        if msg.startswith("'") and msg.endswith("'"):
            msg = msg[1:-1]
        raise HTTPException(status_code=404, detail=msg)
    if isinstance(exc, ValueError):
        raise HTTPException(status_code=409, detail=str(exc))
    raise exc


class CommunityPostRequest(PydBaseModel):
    """UGC 发帖请求(登录态)"""
    targetType: str = Field(..., description="评测对象类型 content|product")
    targetId: str = Field(..., min_length=1, description="评测对象ID")
    text: str = Field(..., description="正文(strip 后 1-1000 字, 服务端复核)")
    rating: int | None = Field(None, description="评分 1-5, 可选")


class BindProductRequest(PydBaseModel):
    """内容挂载商品请求(admin)"""
    contentId: int = Field(..., description="36号内容ID")
    productId: str = Field(..., min_length=1, description="商品ID")


class ReviewPostRequest(PydBaseModel):
    """帖子审核请求(admin)"""
    postId: int = Field(..., description="帖子ID")
    approve: bool = Field(..., description="True=通过 / False=拒绝")


# ============================================================
# C 端(公开只读 + 登录态发帖)
# ============================================================

@router.get("/api/community/feed", tags=["站内内容社区"])
async def feed(
    page: int = Query(1, ge=1, description="页码(从 1 开始)"),
    page_size: int = Query(10, ge=1, le=50, description="每页条数"),
    platform: str = Query(None, description="平台过滤(douyin/xiaohongshu/...)"),
):
    """站内化内容 feed(公开; sv73 产物已发布态倒序分页 + 挂载商品快照)"""
    return {"code": 0, "msg": "ok",
            "data": await _service.feed(page=page, page_size=page_size,
                                        platform=platform)}


@router.get("/api/community/posts", tags=["站内内容社区"])
async def list_posts(
    target_type: str | None = Query(None, description="按对象类型过滤(content|product)"),
    target_id: str | None = Query(None, description="按对象ID过滤"),
    page: int = Query(1, ge=1),
    page_size: int = Query(10, ge=1, le=50),
):
    """UGC 评测帖列表(公开; 只显 approved, 先审后显铁律)"""
    return {"code": 0, "msg": "ok",
            "data": await _service.list_posts(target_type=target_type,
                                              target_id=target_id,
                                              page=page, page_size=page_size)}


@router.post("/api/community/posts", tags=["站内内容社区"])
async def create_post(
    data: CommunityPostRequest,
    x_member_id: str = Header(default="", alias="X-Member-Id"),
):
    """发帖(登录态 X-Member-Id; 落 pending 待审, 审核通过前不公开)"""
    member_id = _require_member(x_member_id)
    try:
        post = await _service.create_post(
            member_id=member_id, nickname=f"会员{member_id}",
            target_type=data.targetType, target_id=data.targetId,
            text=data.text, rating=data.rating)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"code": 0, "msg": "ok", "data": post}


# ============================================================
# admin(挂载 + 审核)
# ============================================================

@router.post("/api/community/bind-product", tags=["站内内容社区"])
async def bind_product(
    data: BindProductRequest,
    x_role: str = Header(None, alias="X-Role"),
):
    """内容挂载商品(admin; 幂等 upsert, 存在校验 best-effort 不硬拒)"""
    _require_admin(x_role)
    return {"code": 0, "msg": "ok",
            "data": await _service.bind_product(data.contentId,
                                                data.productId)}


@router.get("/api/community/posts/pending", tags=["站内内容社区"])
async def pending_posts(
    limit: int = Query(50, ge=1, le=200),
    x_role: str = Header(None, alias="X-Role"),
):
    """待审帖列表(admin)"""
    _require_admin(x_role)
    return {"code": 0, "msg": "ok", "data": await _service.pending_posts(limit)}


@router.post("/api/community/posts/review", tags=["站内内容社区"])
async def review_post(
    data: ReviewPostRequest,
    x_role: str = Header(None, alias="X-Role"),
    x_admin_id: str = Header(default="", alias="X-Admin-Id"),
):
    """帖子审核(admin; pending→approved/rejected, 重复审拒绝)"""
    _require_admin(x_role)
    try:
        post = await _service.review_post(data.postId, data.approve,
                                          operator=x_admin_id)
    except KeyError as exc:
        _raise_mapped(exc)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"code": 0, "msg": "ok", "data": post}


def register_community_routes(app) -> None:
    """注册路由(遵循项目注册模式)"""
    app.include_router(router)
