"""76号·短剧引流智能模型路由(DP0)

端点:
    GET  /api/v1/dramas               在线剧集清单(公开 GET, 游客浏览)
    GET  /api/v1/dramas/{drama_id}    剧集详情(公开)
    GET  /api/v1/dramas/{id}/episodes 分集+解锁状态(公开, user_id 可选)
    POST /api/v1/dramas               组剧(admin)
"""

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel

from services.drama76_series_service import DramaSeriesService

router = APIRouter(
    prefix="/api/v1/dramas",
    tags=["短剧引流智能模型(76号)"],
)

_svc = DramaSeriesService()


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
    return {"code": 0, "data": d}


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


# ---------- DP1: 注册解锁 + 卡点配置 ----------

class UnlockRequest(BaseModel):
    episode_id: str
    method: str = "register"
    src: str = ""            # rLink 短码归因


unlock_router = APIRouter(
    prefix="/api/v1",
    tags=["短剧引流智能模型(76号)"],
)


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
                             src: str = ""):
    """卡点落地页配置(DP1 固定排序 register 优先; DP4 接共鸣动态排序)"""
    d = await _svc.get(drama_id)
    if not d:
        raise HTTPException(status_code=404, detail="drama not found")
    ep = next((e for e in d.get("episodes") or []
               if e.get("episode_id") == episode_id), None)
    if not ep:
        raise HTTPException(status_code=404, detail="episode not found")
    free_n = int(d.get("free_episodes") or 0)
    n = int(ep.get("episode_number") or 0)
    if n <= free_n:
        raise HTTPException(status_code=409, detail="该集免费无卡点")
    return {"code": 0, "data": {
        "drama_id": drama_id, "episode_id": episode_id,
        "checkpoint_type": "register_to_unlock",
        "free_preview_seconds": 10,
        "src": src[:32],
        "unlock_methods": [
            {"type": "register", "title": "注册即可免费解锁本剧后段",
             "action_url": f"/login.html?redirect=/drama.html?d={drama_id}"},
            {"type": "invite", "title": "邀请 1 位好友解锁(DP3)",
             "action_url": "#"},
            {"type": "member", "title": "开通超级会员全站免费看(DP2)",
             "action_url": "/index.html"},
        ]}}


def register_drama76_routes(app):
    """注册76号·短剧引流智能模型路由"""
    app.include_router(router)
    app.include_router(unlock_router)
