"""时空情景感知模块路由(P0)

端点(游客可用, GET 前缀公开白名单):
    GET /api/scene/context        情景聚合(城市+天气+代理+问候)
    GET /api/scene/weather        独立天气查询(缓存 30min)

IP 提取: X-Forwarded-For(首个, nginx 前置) → X-Real-IP → client.host
隐私: 城市级粒度, IP 原文仅瞬态解析不落库。
"""

from fastapi import APIRouter, Header, Query, Request
from fastapi.responses import JSONResponse

from services.scene_service import SceneService

router = APIRouter()
_service = SceneService()


def _client_ip(request: Request) -> str:
    """提取客户端真实 IP(信任链: nginx 前置注入的转发头)"""
    xff = request.headers.get("x-forwarded-for") or ""
    first = xff.split(",")[0].strip() if xff else ""
    if first:
        return first
    return (request.headers.get("x-real-ip")
            or (request.client.host if request.client else ""))


def _member_id(x_member_id: str | None) -> int | None:
    """登录会员(中间件从 JWT 注入; 游客为空)"""
    try:
        return int(x_member_id) if x_member_id else None
    except (TypeError, ValueError):
        return None


@router.get("/api/scene/context", tags=["时空情景感知模块"])
async def get_scene_context(
    request: Request,
    city_code: str = Query(None, max_length=6,
                           description="手动切换城市(区划码, 覆盖 IP 定位)"),
    x_member_id: str | None = Header(None, alias="X-Member-Id"),
):
    """情景聚合: IP 城市 + 天气 + 城市代理权益 + 个性化问候(一次返回)"""
    try:
        result = await _service.get_context(
            ip=_client_ip(request), city_code=city_code,
            member_id=_member_id(x_member_id))
        return {"success": True, "data": result}
    except Exception as e:
        return JSONResponse(status_code=500,
                            content={"success": False, "error": str(e)})


@router.get("/api/scene/geo", tags=["时空情景感知模块"])
async def get_scene_geo(
    request: Request,
    lng: float = Query(..., ge=-180, le=180,
                       description="经度(前端 H5 Geolocation)"),
    lat: float = Query(..., ge=-90, le=90,
                       description="纬度(前端 H5 Geolocation)"),
    x_member_id: str | None = Header(None, alias="X-Member-Id"),
):
    """坐标精确定位 → 完整情景聚合(高德逆地理修正运营商 IP 池偏差)

    移动 CGNAT 池修正: 前端 Geolocation 静默取坐标调本端点,
    located=True 时按精确城市聚合(城市/天气/代理/问候)。"""
    try:
        geo = await _service.resolve_geo(lng, lat)
        if not geo.get("located"):
            return {"success": True, "data": {"located": False}}
        result = await _service.get_context(
            ip=_client_ip(request),
            city_code=geo.get("cityCode") or "",
            member_id=_member_id(x_member_id))
        result["location"]["located"] = True
        return {"success": True, "data": result}
    except Exception as e:
        return JSONResponse(status_code=500,
                            content={"success": False, "error": str(e)})


@router.get("/api/scene/weather", tags=["时空情景感知模块"])
async def get_scene_weather(
    city_code: str = Query(..., max_length=6,
                           description="城市行政区划码(如 370900)"),
):
    """独立天气查询(缓存 30min; 未配置高德 Key 优雅降级)"""
    try:
        result = await _service.get_weather(city_code)
        return {"success": True, "data": result}
    except Exception as e:
        return JSONResponse(status_code=500,
                            content={"success": False, "error": str(e)})


def register_scene_routes(app) -> None:
    """注册时空情景感知模块路由"""
    app.include_router(router)
