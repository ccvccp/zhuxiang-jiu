"""进化观测面(编年史/陈酿指数/Elo 角斗场, 全 GET 观测口径)

对齐 zyh/synapse/zjian/arch 观测面范式: GET 公开(只读聚合, 事件
summary 由埋点方控制不含敏感内部路径), 无 POST 决策面。
"""
import logging

from fastapi import APIRouter, Query

import services.evolution_chronicle_service as chronicle

logger = logging.getLogger(__name__)
router = APIRouter(tags=["进化观测面(编年史/陈酿/Elo)"])


def _handle(e: Exception):
    logger.error("evolution_routes_error: %s", e, exc_info=True)
    from fastapi import HTTPException
    detail = str(e)[:200] or e.__class__.__name__
    raise HTTPException(500, detail)


@router.get("/api/evolution/chronicle")
async def get_chronicle(
    limit: int = Query(50, ge=1, le=500, description="返回条数(新→旧)"),
    domain: str = Query(None, max_length=32, description="域过滤: agent75/voice82/kb57/ai_learning/…"),
):
    """进化编年史(统一 evolution_record 流水)"""
    try:
        items = await chronicle.list_records(limit=limit, domain=domain)
        return {"success": True, "data": items, "count": len(items)}
    except Exception as e:
        _handle(e)


@router.get("/api/evolution/aging")
async def get_aging():
    """陈酿指数(域级聚合健康分, 5 分钟缓存)"""
    try:
        return {"success": True, "data": await chronicle.aging_index()}
    except Exception as e:
        _handle(e)


@router.get("/api/evolution/arena")
async def get_arena():
    """Elo 角斗场积分榜(challenger vs baseline, 跨域可比)"""
    try:
        rows = await chronicle.elo_table()
        return {"success": True, "data": rows, "count": len(rows)}
    except Exception as e:
        _handle(e)


def register_evolution_routes(app) -> None:
    app.include_router(router)
