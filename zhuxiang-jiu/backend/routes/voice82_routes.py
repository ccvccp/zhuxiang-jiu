"""82号·小竹语音对话逻辑学习进化模型路由(P0 观测面+清除)

守卫: X-Role: admin(同 48/81号惯例)
分段: P0 全观测面无模式门槛(看得见永远先于管得着);
      rebuild 为只读聚合(写画像三表, 不触语音链参数);
      参数生效在 P2(VOICE82_MODE 三态), P0 params 仅展示
"""

from fastapi import APIRouter, Header, HTTPException

from services.voice82_dialog_service import (
    Voice82DialogService, voice82_mode,
)

router = APIRouter(prefix="/api/voice82",
                   tags=["小竹对话逻辑学习进化(82号)"])


def _require_admin(x_role: str | None):
    if x_role != "admin":
        raise HTTPException(status_code=403, detail="需要管理员权限")


@router.get("/status")
async def voice82_status(x_role: str = Header(default="",
                                              alias="X-Role")):
    """模块状态(P0: 模式/白名单/冷启动口径)"""
    _require_admin(x_role)
    from services.voice82_dialog_service import (
        COLD_START_SESSIONS, PARAM_DEFAULTS, PARAM_KEYS,
    )
    return {"success": True, "data": {
        "module": "voice82-dialog-evolution", "phase": "P0",
        "mode": voice82_mode(),
        "paramWhitelist": list(PARAM_KEYS),
        "paramDefaults": PARAM_DEFAULTS,
        "coldStartSessions": COLD_START_SESSIONS,
        "note": "P0 零侵入(48号表离线聚合); 参数生效于 P2 三态",
    }}


@router.get("/profile/{member_id}")
async def voice82_profile(member_id: int,
                          rebuild: int = 0,
                          x_role: str = Header(default="",
                                               alias="X-Role")):
    """画像三表+参数映射(观测面; ?rebuild=1 即时重建)"""
    _require_admin(x_role)
    svc = Voice82DialogService()
    result = await (svc.rebuild_profile(member_id)
                    if rebuild else svc.get_profile(member_id))
    if not result.get("success"):
        raise HTTPException(status_code=404,
                            detail=result.get("error") or "无画像")
    return result


@router.delete("/profile/{member_id}")
async def voice82_profile_clear(
        member_id: int,
        x_role: str = Header(default="", alias="X-Role")):
    """一键清除画像(隐私红线: 用户可清除)"""
    _require_admin(x_role)
    return await Voice82DialogService().clear_profile(member_id)


def register_voice82_routes(app):
    """路由注册(routes/__init__ register 函数族惯例)"""
    app.include_router(router)
