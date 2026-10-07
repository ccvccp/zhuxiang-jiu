"""83号·小竹语音准确率与延迟优化路由(观测面)

守卫: SLA 报表/事件观测 admin; 事件上报 member(JWT——面板
      fire-and-forget 同 /lat 口径)。
分段: 全观测面无模式门槛(看得见永远先于管得着)。
"""

from fastapi import APIRouter, Header, HTTPException

from services.voice83_latency_service import Voice83LatencyService

router = APIRouter(prefix="/api/voice83",
                   tags=["小竹语音准确率与延迟优化(83号)"])


def _require_admin(x_role: str | None):
    if x_role != "admin":
        raise HTTPException(status_code=403, detail="需要管理员权限")


def _require_member(x_member_id: str | None):
    if not x_member_id:
        raise HTTPException(status_code=401,
                            detail="需要登录态(X-Member-Id)")


@router.get("/latency-sla")
async def voice83_latency_sla(
        days: int = 7,
        x_role: str = Header(default="", alias="X-Role")):
    """SLA 达标率报表(四段 P50/P90 + 验收线达标率; 观测面)"""
    _require_admin(x_role)
    return await Voice83LatencyService().latency_sla_report(
        max(1, min(9, days)))


@router.post("/events")
async def voice83_events_report(
        payload: dict = None,
        x_member_id: str | None = Header(
            None, alias="X-Member-Id")):
    """事件上报(面板 fire-and-forget: wake_hit/barge_cut)"""
    _require_member(x_member_id)
    p = payload or {}
    if not p.get("type"):
        raise HTTPException(status_code=422, detail="type 必填")
    r = await Voice83LatencyService().record_event(
        str(p.get("type")), p.get("meta") or {})
    if not r.get("success"):
        raise HTTPException(status_code=422, detail=r.get("error"))
    return r


@router.get("/events")
async def voice83_events_view(
        days: int = 1, limit: int = 100,
        x_role: str = Header(default="", alias="X-Role")):
    """事件观测(按类型计数 + 最近样本)"""
    _require_admin(x_role)
    return await Voice83LatencyService().events_view(
        max(1, min(9, days)), max(1, min(limit, 500)))


def register_voice83_routes(app):
    """路由注册(routes/__init__ register 函数族惯例)"""
    app.include_router(router)
