"""84号·短剧智能生产工厂路由(P0 连续剧本引擎)

端点(6):
    POST /api/drama84/series/generate   生成连续剧(bible+分集,
                                        决策面 off/kill=409, admin)
    GET  /api/drama84/series/{sid}      剧本详情(观测面, admin)
    GET  /api/drama84/series            剧本清单(观测面, admin)
    POST /api/drama84/series/{sid}/attach-episode
                                        集成片登记 36号 content(P1)
    POST /api/drama84/topics/derive     题材研判(P2-A: 热点→选题包)
    POST /api/drama84/series/{sid}/score
                                        剧集评分回流(P2-D: 五因子
                                        →44号治理档案, 聚合76号解锁)

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


class TopicsDeriveRequest(BaseModel):
    limit: int = Field(8, ge=1, le=20,
                       description="取热点条数(选题包上限)")


@router.post("/api/drama84/topics/derive")
async def derive_topics(payload: TopicsDeriveRequest,
                        x_role: str | None = Header(None)):
    """题材研判(P2-A 决策面: 36号热点 → 短剧选题包)

    LLM 研判 fail-soft——失败回热点直通占位包;
    产物落盘 series84/d84_topics.json, generate_series 可直接消费。
    """
    _require_admin(x_role)
    _mode_guard()
    try:
        out = await _service.derive_topics(limit=payload.limit)
        return {"success": True, "data": out}
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e


@router.post("/api/drama84/series/{sid}/score")
async def score_series(sid: str,
                       x_role: str | None = Header(None)):
    """剧集评分回流(P2-D 决策面: 五因子 → 44号治理档案)

    路由侧聚合 76号解锁漏斗(sid→content→drama76 剧反查),
    快照落盘 series84/{sid}_score.json, 44号回流 best-effort。
    """
    _require_admin(x_role)
    _mode_guard()
    try:
        stats = await _drama76_stats(sid)
        snap = await _service.score_series(
            sid, unlocks=stats["unlocks"],
            online_days=stats["onlineDays"])
        return {"success": True, "data": snap, "traffic": stats}
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e


async def _drama76_stats(sid: str) -> dict:
    """聚合76号解锁漏斗: sid → content(storyboardId={sid}_ep{n})
    → drama76 剧反查(episodes[].episode_id 交集) → 解锁计数+上线
    天数; 无关联剧/无记录 → (0, 1.0) fail-soft 不阻断评分"""
    import datetime as _dt
    from repositories.promo_repository import PromoRepository
    from services.drama76_series_service import (
        DramaSeriesService,
    )
    blank = {"dramaId": "", "unlocks": 0, "onlineDays": 1.0}
    try:
        repo = PromoRepository()
        cids = {str(c.get("contentId"))
                for c in (await repo.list_contents() or [])
                if str(((c.get("sv73") or {})
                        .get("storyboardId") or "")
                       ).startswith(f"{sid}_ep")}
        if not cids:
            return blank
        svc = DramaSeriesService(repo=repo)
        drama = None
        for d in await svc._list_all():
            eids = {str(e.get("episode_id"))
                    for e in d.get("episodes") or []}
            if eids & cids:
                drama = d
                break
        if not drama:
            return blank
        store = getattr(repo, "store", None)
        if isinstance(store, dict):
            store.setdefault(svc.UNLOCK_TABLE, {})
        rows = await repo._list(svc.UNLOCK_TABLE, 500) or []
        unlocks = sum(1 for r in rows
                      if r.get("drama_id") == drama.get("drama_id"))
        online_days = 1.0
        try:
            created = _dt.datetime.fromisoformat(
                str(drama.get("created_at") or ""))
            online_days = max(
                0.5, (_dt.datetime.now(_dt.timezone.utc) - created)
                .total_seconds() / 86400.0)
        except ValueError:
            pass
        return {"dramaId": drama.get("drama_id") or "",
                "unlocks": unlocks,
                "onlineDays": round(online_days, 2)}
    except Exception as exc:  # noqa: BLE001
        from services.drama84_script_service import logger
        logger.info("drama84_stats_skip: %s", exc)
        return blank


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
