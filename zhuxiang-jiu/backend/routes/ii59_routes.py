"""58号→59号·AI智能服务编排路由(P0-P1)

端点(P0 5 + P1 4 = 9):
    GET  /api/ii59/registry            注册表自描述(admin, 观测面)
    POST /api/ii59/sessions            开话(admin, 决策面 off 409)
    GET  /api/ii59/sessions/{id}        会话详情(admin, 观测面)
    GET  /api/ii59/sessions             会话列表(admin, 观测面)
    GET  /api/ii59/model/status         模型状态(admin, 观测面)
    POST /api/ii59/sessions/{id}/route  意图路由(admin, 决策面, P1)
    POST /api/ii59/sessions/{id}/advance 步骤推进(admin, 决策面, P1)
    POST /api/ii59/sessions/{id}/escalate 人工接管(admin, 终审铁律, P1)
    POST /api/ii59/sessions/{id}/close  闭话+满意度(admin, 终审铁律, P1)

鉴权: 管理面 X-Role: admin(43-58号同款口径)。
统一口径:
    - 观测面(registry/sessions/model/status)
      不受 II59_MODE 影响
    - 决策面(sessions 开话/route/advance):
      off=拒绝(409——shadow/assist 开放)
    - escalate/close(客服兜底人工铁律):
      不受开关影响
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


@router.post("/sessions/{session_id}/route")
async def session_route(
        session_id: int,
        body: dict,
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """意图路由(58号 evaluate 纯消费→服务通道
    +任务编排启动; 决策面 off 409, P1)

    Body: {text, memberRole?}——上游铁律:
    clarify/partial 不编排; boundary 拒绝"""
    _require_admin(x_role)
    from services.ii59_conversation_service import (
        Ii59ConversationService,
    )
    try:
        return await (
            Ii59ConversationService()
            .route_intent(
                int(session_id),
                str(body.get("text") or ""),
                member_role=str(
                    body.get("memberRole")
                    or "member")))
    except KeyError:
        raise HTTPException(
            status_code=404,
            detail=f"会话 {session_id} 不存在")
    except ValueError as exc:
        raise HTTPException(status_code=409,
                            detail=str(exc))


@router.post("/sessions/{session_id}/advance")
async def session_advance(
        session_id: int,
        body: dict = None,
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """步骤推进(任务下一步; 失败 fail-soft 转
    escalated; 决策面 off 409, P1)

    Body: {result?(done/failed), note?}"""
    _require_admin(x_role)
    from services.ii59_conversation_service import (
        Ii59ConversationService,
    )
    try:
        return await (
            Ii59ConversationService().advance(
                int(session_id),
                result=str(
                    (body or {}).get("result")
                    or "done"),
                note=str(
                    (body or {}).get("note")
                    or "")))
    except KeyError as exc:
        raise HTTPException(
            status_code=404, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=409,
                            detail=str(exc))


@router.post("/sessions/{session_id}/escalate")
async def session_escalate(
        session_id: int,
        body: dict = None,
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """人工接管(escalated——脱敏上下文移交
    +排队; 不受开关影响的客服兜底人工铁律, P1)

    Body: {reason?, contextNote?}"""
    _require_admin(x_role)
    from services.ii59_conversation_service import (
        Ii59ConversationService,
    )
    try:
        return await (
            Ii59ConversationService().escalate(
                int(session_id),
                reason=str(
                    (body or {}).get("reason")
                    or ""),
                context_note=str(
                    (body or {}).get(
                        "contextNote") or "")))
    except KeyError:
        raise HTTPException(
            status_code=404,
            detail=f"会话 {session_id} 不存在")
    except ValueError as exc:
        raise HTTPException(status_code=409,
                            detail=str(exc))


@router.post("/sessions/{session_id}/close")
async def session_close(
        session_id: int,
        body: dict,
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """闭话+满意度采集(1-5 分回流真值源;
    不受开关影响的人工铁律, P1)

    Body: {satisfaction(必填 1-5), note?}"""
    _require_admin(x_role)
    from services.ii59_conversation_service import (
        Ii59ConversationService,
    )
    try:
        return await (
            Ii59ConversationService().close(
                int(session_id),
                satisfaction=body.get(
                    "satisfaction"),
                note=str(
                    body.get("note") or "")))
    except KeyError:
        raise HTTPException(
            status_code=404,
            detail=f"会话 {session_id} 不存在")
    except ValueError as exc:
        raise HTTPException(status_code=409,
                            detail=str(exc))


def register_ii59_routes(app) -> None:
    """注册59号路由(main.py startup 调用)"""
    app.include_router(router)
