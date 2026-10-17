"""76号·短剧引流智能模型路由(DP0)

端点:
    GET  /api/v1/dramas               在线剧集清单(公开 GET, 游客浏览)
    GET  /api/v1/dramas/{drama_id}    剧集详情(公开, 未放送集不可见)
    GET  /api/v1/dramas/{id}/episodes 分集+解锁状态(公开, user_id 可选)
    POST /api/v1/dramas               组剧(admin)
    POST /api/v1/dramas/{id}/release  放送分集(边更边锁, admin)
"""

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field
import logging

from services.drama76_growth_service import (
    DramaGrowthService, REWARD_LADDERS)
from services.drama76_series_service import DramaSeriesService

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/v1/dramas",
    tags=["短剧引流智能模型(76号)"],
)

_svc = DramaSeriesService()
_growth = DramaGrowthService()


def _require_admin(x_role: str | None) -> None:
    if (x_role or "").strip().lower() != "admin":
        raise HTTPException(status_code=403, detail="admin only")


class DramaCreateRequest(BaseModel):
    drama_id: str | None = None
    title: str
    description: str = ""
    persona: str = "zhuxiaomei"
    cover_url: str = ""
    tags: list[str] = []
    free_episodes: int = 2
    published_episodes: int = 0   # 已放送集数(0=整剧全放; 边更边锁)
    status: str = "online"
    episodes: list[dict]


@router.get("")
async def list_dramas():
    return {"code": 0, "data": await _svc.list_online()}


@router.get("/{drama_id}")
async def get_drama(drama_id: str):
    d = await _svc.get(drama_id)
    if not d:
        raise HTTPException(status_code=404, detail="drama not found")
    # 边更边锁: 未放送分集对外不可见(仅保留 total_episodes 计数)
    return {"code": 0, "data": _svc.public_view(d)}


@router.get("/{drama_id}/episodes")
async def list_episodes(drama_id: str, user_id: str | None = None,
                        device_id: str | None = None):
    """分集+解锁状态(DP0 静态: 免费/锁定; DP1 接注册解锁, DP2 接会员)"""
    d = await _svc.get(drama_id)
    if not d:
        raise HTTPException(status_code=404, detail="drama not found")
    # DP2 前会员态恒 False; user_id/device_id 先落位(事件/归因用)
    return {"code": 0, "data": {
        "drama_id": drama_id,
        "title": d.get("title"),
        "user_id": user_id, "device_id": device_id,
        "episodes": _svc.episode_states(d, is_member=False)}}


@router.post("")
async def create_drama(body: DramaCreateRequest,
                       x_role: str | None = Header(default=None)):
    _require_admin(x_role)
    try:
        drama = await _svc.create(body.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return {"code": 0, "data": drama.model_dump()}


class ReleaseRequest(BaseModel):
    up_to: int | None = None     # None=放送下一集(首次启用=放送到免费集数)
    free_episodes: int | None = None  # 卡点弹性: 免费集数 1..total-1 动态调整


@router.post("/{drama_id}/release")
async def release_drama(drama_id: str, body: ReleaseRequest,
                        x_role: str | None = Header(default=None)):
    """放送分集(边更边锁·admin, 2026-10-17 对标懒趴趴短剧):
    首周连发 3-4 集抢合集连续推荐窗口, 卡点落在热度峰而非第 2 集;
    free_episodes 卡点弹性(对标第四篇·点众免费窗第 9 集)——首周
    观察张力峰后动态挪免费窗, 一次请求可同时放送+调卡点"""
    _require_admin(x_role)
    try:
        drama = await _svc.release_episode(
            drama_id, body.up_to, body.free_episodes)
    except KeyError:
        raise HTTPException(status_code=404,
                            detail="drama not found") from None
    except ValueError as exc:
        raise HTTPException(status_code=409,
                            detail=str(exc)) from None
    return {"code": 0, "data": _svc.public_view(drama)}


class EpisodeReviewRequest(BaseModel):
    episode_number: int
    approved: bool = Field(..., description="三审结论: True=过审可放送 / False=驳回")


@router.post("/{drama_id}/episodes/review")
async def review_episode(drama_id: str, body: EpisodeReviewRequest,
                         x_role: str | None = Header(default=None)):
    """分集三审人工过审(2026-10-17 风险⑥·admin): 对齐 36号
    pending→approved/rejected 惯例——放送闸(release)只放行
    approved 或已放送的集, 未过审即放送返回 409"""
    _require_admin(x_role)
    try:
        drama = await _svc.review_episode(
            drama_id, body.episode_number, body.approved)
    except KeyError:
        raise HTTPException(status_code=404,
                            detail="drama not found") from None
    except ValueError as exc:
        raise HTTPException(status_code=409,
                            detail=str(exc)) from None
    return {"code": 0, "data": _svc.public_view(drama)}


# ---------- DP1: 注册解锁 + 卡点配置 ----------

class UnlockRequest(BaseModel):
    episode_id: str
    method: str = "register"
    src: str = ""            # rLink 短码归因


unlock_router = APIRouter(
    prefix="/api/v1",
    tags=["短剧引流智能模型(76号)"],
)


def _member_id(x_member_id: str | None) -> str:
    v = (x_member_id or "").strip()
    if not v:
        raise HTTPException(status_code=401, detail="login required")
    return v


@unlock_router.post("/unlocks")
async def unlock_episode(body: UnlockRequest,
                         x_member_id: str | None = Header(default=None)):
    """注册解锁(登录态——JWT 中间件注入 x-member-id; 公开路径外
    自动要求 Bearer): 一次解锁本剧后段, 幂等"""
    if not (x_member_id or "").strip():
        raise HTTPException(status_code=401, detail="login required")
    user_id = x_member_id.strip()
    try:
        drama, rec = await _svc.unlock(
            user_id, body.episode_id, body.method, body.src)
    except KeyError:
        raise HTTPException(status_code=404, detail="episode not found")
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    unlocked = await _svc.unlocked_dramas_of(user_id)
    return {"code": 0, "data": {
        "unlock": rec,
        "drama_id": drama["drama_id"],
        "episodes": _svc.episode_states(drama,
                                        unlocked_dramas=unlocked)}}


@router.get("/{drama_id}/episodes/{episode_id}/checkpoint")
async def episode_checkpoint(drama_id: str, episode_id: str,
                             src: str = "", session_id: str = ""):
    """卡点落地页配置(DP1 固定排序 register 优先; DP4 共鸣驱动动态排序
    —— hesitant→invite 优先 / eager→register 直解 / sticky→member 置顶)"""
    d = await _svc.get(drama_id)
    if not d:
        raise HTTPException(status_code=404, detail="drama not found")
    ep = next((e for e in d.get("episodes") or []
               if e.get("episode_id") == episode_id), None)
    if not ep:
        raise HTTPException(status_code=404, detail="episode not found")
    free_n = int(d.get("free_episodes") or 0)
    n = int(ep.get("episode_number") or 0)
    # 边更边锁: 未放送集卡点页不暴露(防提前透剧)
    pub_n = int(d.get("published_episodes") or 0)
    if pub_n > 0 and n > pub_n:
        raise HTTPException(status_code=404, detail="episode not published")
    if _svc.is_alcohol_drama(d):
        # 酒类品牌剧全集免登录(2026-10-09 定案)——无卡点
        raise HTTPException(status_code=409, detail="酒类剧全集免费无卡点")
    if n <= free_n:
        raise HTTPException(status_code=409, detail="该集免费无卡点")
    methods = [
        {"type": "register", "title": "注册即可免费解锁本剧后段",
         "action_url": f"/login.html?redirect=/drama.html?d={drama_id}"},
        {"type": "invite", "title": REWARD_LADDERS[0]["title"],
         "action_url": f"/drama.html?d={drama_id}"},
        {"type": "member", "title": "开通超级会员全站免费看",
         "action_url": "/index.html"},
    ]
    ordered, meta = _svc.order_unlock_methods(methods, session_id)
    return {"code": 0, "data": {
        "drama_id": drama_id, "episode_id": episode_id,
        "checkpoint_type": "register_to_unlock",
        "free_preview_seconds": 10,
        "src": src[:32],
        "dp4": meta,
        "unlock_methods": ordered}}


# ---------- DP2: 超级会员(判定/开通面/个人视图/权益校验) ----------

@router.get("/{drama_id}/episodes/me")
async def episodes_for_me(drama_id: str,
                          x_member_id: str | None = Header(default=None)):
    """登录态全集视图: 会员/已解锁合并计算(前端带 token 调此端点)"""
    user = _member_id(x_member_id)
    d = await _svc.get(drama_id)
    if not d:
        raise HTTPException(status_code=404, detail="drama not found")
    eps = await _svc.user_view(user, d)
    # DP3: 有效转化埋点——被邀人登录态使用即视为转化(pending→confirmed)
    try:
        await _growth.auto_confirm(user)
    except Exception:  # 发奖异常不阻断主视图
        logger.exception("drama76_auto_confirm_failed user=%s", user)
    return {"code": 0, "data": {
        "drama_id": drama_id, "user_id": user,
        "is_super": await _svc.is_super(user),
        "episodes": eps}}


@unlock_router.get("/memberships/check")
async def membership_check(x_member_id: str | None = Header(default=None),
                           drama_id: str = "", episode_id: str = ""):
    """权益校验: can_watch + reason(super_member_active/locked/free)"""
    user = _member_id(x_member_id)
    if not drama_id:
        raise HTTPException(status_code=409, detail="drama_id required")
    d = await _svc.get(drama_id)
    if not d:
        raise HTTPException(status_code=404, detail="drama not found")
    eps = await _svc.user_view(user, d)
    if episode_id:
        ep = next((e for e in eps
                   if e["episode_id"] == episode_id), None)
        if not ep:
            raise HTTPException(status_code=404, detail="episode not found")
        return {"code": 0, "data": {
            "user_id": user, "episode_id": episode_id,
            "can_watch": ep["is_unlocked"],
            "reason": ("super_member_active"
                       if await _svc.is_super(user)
                       else ep["status"])}}
    return {"code": 0, "data": {
        "user_id": user, "is_super": await _svc.is_super(user),
        "episodes": eps}}


@unlock_router.get("/memberships/plans")
async def membership_plans():
    """套餐表(公开; 支付下单 DP2b 接 pay60)"""
    from services.drama76_series_service import SUPER_PLANS
    return {"code": 0, "data": SUPER_PLANS}


class GrantRequest(BaseModel):
    user_id: str
    days: int = 0            # 0=永久
    source: str = "admin"


@unlock_router.post("/memberships/grant")
async def grant_super(body: GrantRequest,
                      x_role: str | None = Header(default=None)):
    _require_admin(x_role)
    rec = await _svc.grant_super(body.user_id, body.days, body.source)
    return {"code": 0, "data": rec}


@unlock_router.post("/memberships/revoke")
async def revoke_super(body: GrantRequest,
                       x_role: str | None = Header(default=None)):
    _require_admin(x_role)
    ok = await _svc.revoke_super(body.user_id)
    if not ok:
        raise HTTPException(status_code=404, detail="not a super member")
    return {"code": 0, "data": {"user_id": body.user_id, "revoked": True}}


class PurchaseRequest(BaseModel):
    plan_id: str
    pay_channel: str = "wechat"   # wechat/alipay


@unlock_router.post("/memberships/purchase")
async def purchase_super(body: PurchaseRequest,
                         x_member_id: str | None = Header(default=None)):
    """购买超级会员·创建支付单(DP2b; 登录态)。

    铁律: 本端点只创建支付单不直接开通——开通由支付回调分发
    (payment_service._dispatch_business → grant_super_by_order)完成。
    返回 payNo 后由前端发起渠道支付(POST /api/payment/{pay_no}/start)。
    """
    user = _member_id(x_member_id)
    try:
        rec = await _svc.create_super_purchase_pay(
            user, body.plan_id, body.pay_channel)
    except KeyError:
        raise HTTPException(status_code=404, detail="plan not found")
    return {"code": 0, "data": rec}


# ---------- DP3: 邀请裂变 ----------

growth_router = APIRouter(
    prefix="/api/v1/growth",
    tags=["短剧引流智能模型(76号)"],
)


class BindRequest(BaseModel):
    invite_code: str


@growth_router.post("/invite-code")
async def my_invite_code(
        x_member_id: str | None = Header(default=None)):
    """取本人邀请码(幂等: 同用户恒同码); 登录态"""
    user = _member_id(x_member_id)
    rec = await _growth.invite_code(user)
    return {"code": 0, "data": rec}


@growth_router.post("/referrals/bind")
async def bind_referral(body: BindRequest,
                        x_member_id: str | None = Header(default=None)):
    """绑定邀请关系(被邀人登录态调): 自邀拒/码无效 404/唯一绑定幂等"""
    user = _member_id(x_member_id)
    try:
        rec = await _growth.bind(body.invite_code, user)
    except KeyError:
        raise HTTPException(status_code=404, detail="invite code not found")
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return {"code": 0, "data": rec}


@growth_router.get("/invites/me")
async def my_invites(x_member_id: str | None = Header(default=None)):
    """我的邀请进度: 码/明细/有效数/下一阶梯/已发奖励; 登录态"""
    user = _member_id(x_member_id)
    return {"code": 0, "data": await _growth.progress(user)}


def register_drama76_routes(app):
    """注册76号·短剧引流智能模型路由"""
    app.include_router(router)
    app.include_router(unlock_router)
    app.include_router(growth_router)
