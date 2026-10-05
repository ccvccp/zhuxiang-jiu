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


def register_drama76_routes(app):
    """注册76号·短剧引流智能模型路由"""
    app.include_router(router)
