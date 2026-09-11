"""68号 P0·信值·臻选路由(信值五维雷达)

端点(2):
    GET /api/xinzhi/radar           五维雷达(最新快照/即时计算)
    GET /api/xinzhi/radar/history    90 日历史曲线

鉴权: Authorization Bearer(会员本人)——/api/xinzhi/* 走
会员域; P0 期与 /api/member/profile 同口径(X-Member-Id 头)
异常映射: KeyError→404 / ValueError→409(项目约定)
"""

from typing import Annotated

from fastapi import APIRouter, Header, HTTPException

router = APIRouter()


def _member_id(x_member_id: str | None) -> int:
    if not (x_member_id or "").strip():
        raise HTTPException(status_code=401,
                            detail="需要登录(X-Member-Id)")
    try:
        return int(x_member_id)
    except ValueError:
        raise HTTPException(status_code=401,
                            detail="X-Member-Id 非法")


def _handle(e: Exception):
    if isinstance(e, KeyError):
        raise HTTPException(status_code=404, detail=str(e))
    if isinstance(e, ValueError):
        raise HTTPException(status_code=409, detail=str(e))
    raise HTTPException(status_code=500, detail=str(e))


def _service():
    from services.xinzhi_radar_service import (
        XinzhiRadarService)
    return XinzhiRadarService()


@router.get("/api/xinzhi/radar",
            tags=["信值臻选购物平台"])
async def xinzhi_radar(
        x_member_id: Annotated[str | None,
                               Header(alias="X-Member-Id")]
        = None):
    """信值五维雷达(诚信30/互助25/专业20/活跃15/成长10——
    分段Sigmoid+熔断+衰减; 每维影响因素TOP3 可解释)"""
    mid = _member_id(x_member_id)
    try:
        return {"success": True, "data":
                await _service().get_radar(mid)}
    except Exception as e:
        _handle(e)


@router.get("/api/xinzhi/radar/history",
            tags=["信值臻选购物平台"])
async def xinzhi_radar_history(
        x_member_id: Annotated[str | None,
                               Header(alias="X-Member-Id")]
        = None,
        limit: int = 90):
    """信值 90 日历史曲线(快照时序)"""
    mid = _member_id(x_member_id)
    try:
        return {"success": True, "data":
                await _service().get_history(mid, limit=limit)}
    except Exception as e:
        _handle(e)


def register_xinzhi_routes(app) -> None:
    """注册信值臻选路由(main.py startup 调用)"""
    app.include_router(router)
