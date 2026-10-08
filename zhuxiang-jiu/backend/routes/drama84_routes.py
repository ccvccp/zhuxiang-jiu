"""84号·短剧智能生产工厂路由(P0 连续剧本引擎)

端点(3):
    POST /api/drama84/series/generate   生成连续剧(bible+分集,
                                        决策面 off/kill=409, admin)
    GET  /api/drama84/series/{sid}      剧本详情(观测面, admin)
    GET  /api/drama84/series            剧本清单(观测面, admin)

统一口径(73/76号范式): X-Role: admin; DRAMA84_KILL 一键回退;
KeyError → 404 / ValueError → 409。
"""

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field

from services.drama84_script_service import (
    Drama84ScriptService, drama84_mode, is_kill,
    DEFAULT_EPISODES, DEFAULT_CAST,
)

router = APIRouter()
_service = Drama84ScriptService()


def _require_admin(x_role: str | None) -> None:
    if (x_role or "").strip().lower() != "admin":
        raise HTTPException(status_code=403, detail="需 X-Role: admin")


def _mode_guard() -> None:
    if is_kill():
        raise HTTPException(status_code=409, detail="DRAMA84_KILL=on")
    if drama84_mode() == "off":
        raise HTTPException(status_code=409,
                            detail="DRAMA84_MODE=off")


class SeriesGenerateRequest(BaseModel):
    hotspotId: int = Field(0, description="36号雷达热点ID(0=自选当前最优)")
    title: str = Field("", max_length=20, description="剧名倾向(空=LLM 自拟)")
    episodes: int = Field(DEFAULT_EPISODES, ge=4, le=80,
                          description="集数(默认 6)")
    cast: list[str] = Field(default=list(),
                            description="人设阵容(空=IP 双主角)")


@router.post("/api/drama84/series/generate")
async def generate_series(payload: SeriesGenerateRequest,
                          x_role: str | None = Header(None)):
    """生成连续剧(决策面: 热点+阵容 → bible+分集剧本)"""
    _require_admin(x_role)
    _mode_guard()
    try:
        hotspot = {"hotspotId": payload.hotspotId,
                   "title": payload.title or "竹香酒馆",
                   "platform": "manual", "score": 80}
        if payload.hotspotId:
            hotspot = await _resolve_hotspot(payload.hotspotId)
        cast = tuple(payload.cast) or DEFAULT_CAST
        result = await _service.generate_series(
            hotspot, episodes=payload.episodes, cast=cast,
            title=payload.title)
        return {"success": True, "data": result}
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e


class AttachEpisodeRequest(BaseModel):
    ep: int = Field(ge=1, le=80, description="集号")
    video: str = Field(max_length=300,
                       description="成片路径(sv73_videos/d84_xxx_epN.mp4)")
    duration: float = Field(75.0, gt=0, le=600,
                            description="成片时长秒")


@router.post("/api/drama84/series/{sid}/attach-episode")
async def attach_episode(sid: str, payload: AttachEpisodeRequest,
                         x_role: str | None = Header(None)):
    """集成片登记 36号 content(P1: 76号组剧 episode_id 消费)"""
    _require_admin(x_role)
    _mode_guard()
    try:
        row = await _service.register_episode(
            sid, payload.ep, payload.video, payload.duration)
        return {"success": True, "data": {
            "contentId": row.get("contentId"),
            "storyboardId": ((row.get("sv73") or {})
                             .get("storyboardId")),
            "status": row.get("status")}}
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e


@router.get("/api/drama84/series")
async def list_series(x_role: str | None = Header(None)):
    """剧本清单(观测面)"""
    _require_admin(x_role)
    rows = _service.list_series()
    return {"success": True, "data": rows, "count": len(rows)}


@router.get("/api/drama84/series/{sid}")
async def get_series(sid: str,
                     x_role: str | None = Header(None)):
    """剧本详情(观测面)"""
    _require_admin(x_role)
    row = _service.get_series(sid)
    if row is None:
        raise HTTPException(status_code=404,
                            detail=f"剧本不存在({sid})")
    return {"success": True, "data": row}


async def _resolve_hotspot(hotspot_id: int) -> dict:
    """36号雷达热点详情(services 层直取, fail-soft 回占位)"""
    try:
        from repositories.promo_repository import (
            PromoRepository,
        )
        rows = await PromoRepository().list_hotspots(limit=50) \
            if hasattr(PromoRepository(), "list_hotspots") \
            else []
        for h in rows or []:
            if int(h.get("hotspotId") or 0) == int(hotspot_id):
                return {"hotspotId": hotspot_id,
                        "title": h.get("title") or "",
                        "platform": h.get("platform") or "",
                        "score": h.get("score") or 0,
                        "fingerprint": h.get("fingerprint") or ""}
    except Exception as exc:  # noqa: BLE001
        from services.drama84_script_service import logger
        logger.info("drama84_hotspot_resolve_skip: %s", exc)
    return {"hotspotId": hotspot_id, "title": "竹香酒馆",
            "platform": "manual", "score": 80}


def register_drama84_routes(app):
    """注册84号·短剧智能生产工厂路由"""
    app.include_router(router)
