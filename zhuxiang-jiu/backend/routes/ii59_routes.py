"""58号→59号·AI智能服务编排路由(P0)

端点(P0 4):
    GET  /api/ii59/registry            注册表自描述(admin, 观测面)
    POST /api/ii59/sessions            开话(admin, 决策面 off 409)
    GET  /api/ii59/sessions/{id}        会话详情(admin, 观测面)
    GET  /api/ii59/sessions             会话列表(admin, 观测面)
    GET  /api/ii59/model/status         模型状态(admin, 观测面)

鉴权: 管理面 X-Role: admin(43-58号同款口径)。
统一口径:
    - 观测面(registry/sessions/model/status)
      不受 II59_MODE 影响
    - 决策面(sessions 开话): off=拒绝
      (409——shadow/assist 开放)
    - KeyError → 404 / ValueError → 409
"""

from fastapi import APIRouter, Header, HTTPException

router = APIRouter(prefix="/api/ii59",
                   tags=["AI智能服务编排(59号)"])


def _require_admin(x_role: str | None) -> str:
    if not x_role or x_role != "admin":
        raise HTTPException(status_code=403,
                            detail="需要 X-Role: admin")


@router.get("/registry")
async def registry(
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """服务编排注册表自描述(三位一体+状态机
    口径——观测面)"""
    _require_admin(x_role)
    from services.ii59_service import Ii59Service
    return Ii59Service.registry()


@router.post("/sessions")
async def open_session(
        body: dict,
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """开话(opened 态——归因链强制; 决策面
    off 409)

    Body: {memberId?, channel?(text/voice)}"""
    _require_admin(x_role)
    from services.ii59_service import Ii59Service
    try:
        member_id = body.get("memberId")
        return await Ii59Service().open_session(
            member_id=int(member_id)
            if member_id is not None else 0,
            channel=str(
                body.get("channel") or "text"))
    except ValueError as exc:
        raise HTTPException(status_code=409,
                            detail=str(exc))


@router.get("/sessions/{session_id}")
async def session_detail(
        session_id: int,
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """会话详情(状态机+归因链——观测面)"""
    _require_admin(x_role)
    from services.ii59_service import Ii59Service
    try:
        return await Ii59Service().get_session(
            int(session_id))
    except KeyError:
        raise HTTPException(
            status_code=404,
            detail=f"会话 {session_id} 不存在")


@router.get("/sessions")
async def sessions(
        member_id: int = None,
        state: str = None,
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """会话列表(六态统计——观测面)"""
    _require_admin(x_role)
    from services.ii59_service import Ii59Service
    return await Ii59Service().list_sessions(
        member_id=member_id, state=state)


@router.get("/model/status")
async def model_status(
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """模型状态(第34档案 champion/challenger
    /八因子——44号复用观测面)"""
    _require_admin(x_role)
    from services.ii59_service import Ii59Service
    return await Ii59Service().model_status()


def register_ii59_routes(app) -> None:
    """注册59号路由(main.py startup 调用)"""
    app.include_router(router)
