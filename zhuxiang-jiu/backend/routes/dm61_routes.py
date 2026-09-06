"""61号·AI智能系统升级决策路由(P0-P5)

端点(P0 5; 全期规划 17):
    GET  /api/dm61/registry          注册表自描述(admin, 观测面)
    POST /api/dm61/requests          决策请求接收(admin, 决策面 off 409)
    GET  /api/dm61/requests          请求列表(admin, 观测面)
    GET  /api/dm61/requests/{id}     请求详情(admin, 观测面)
    GET  /api/dm61/model/status      模型状态(admin, 观测面)
    # P1: POST /assess + /recommend + /decisions/{id}/decide
    # P2: POST /simulate + /threshold/calibrate + GET /thresholds
    # P3: POST /decisions/{id}/dissent + POST /feedback + GET /cases
    # P4: POST /feedback/collect
    # P5: GET /dashboard + POST /redteam

鉴权: 管理面 X-Role: admin(43-63号同款口径)。
统一口径(计划 §六):
    - 观测面(registry/requests 列表与
      详情/model status)不受 DM61_MODE
      影响
    - 决策面(请求接收): off=拒绝(409)
    - 后续: decide/dissent 终审与 collect
      回流不受开关影响(人工铁律)
    - KeyError → 404 / ValueError → 409
"""

from fastapi import APIRouter, Header, HTTPException

router = APIRouter(prefix="/api/dm61",
                   tags=["AI智能升级决策(61号)"])


def _require_admin(x_role: str | None) -> str:
    if not x_role or x_role != "admin":
        raise HTTPException(status_code=403,
                            detail="需要 X-Role: admin")


@router.get("/registry")
async def registry(
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """决策注册表自描述(语义标签六类+依赖
    映射+环境适宜性——观测面)"""
    _require_admin(x_role)
    from services.dm61_service import Dm61Service
    return Dm61Service.registry()


@router.post("/requests")
async def create_request(
        body: dict,
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """决策请求接收(P0——三源统一+语义
    标签轨+影响面+环境感知; 决策面
    off 409)

    Body: {title, description?, source?
    (proposal/signal/manual), proposalId?,
    signalId?, requestedBy?, hour?,
    recentFailureRate?, trustVolatility?}"""
    _require_admin(x_role)
    from services.dm61_service import Dm61Service
    try:
        proposal_id = body.get("proposalId")
        signal_id = body.get("signalId")
        return await (
            Dm61Service().create_request(
                title=str(
                    body.get("title") or ""),
                description=str(
                    body.get("description")
                    or ""),
                source=str(
                    body.get("source")
                    or "manual"),
                proposal_id=int(proposal_id)
                if proposal_id is not None
                else None,
                signal_id=int(signal_id)
                if signal_id is not None
                else None,
                requested_by=str(
                    body.get("requestedBy")
                    or "admin"),
                hour=body.get("hour"),
                recent_failure_rate=body.get(
                    "recentFailureRate"),
                trust_volatility=body.get(
                    "trustVolatility")))
    except ValueError as exc:
        raise HTTPException(status_code=409,
                            detail=str(exc))


@router.get("/requests")
async def requests(
        source: str = None,
        tag: str = None,
        status: str = None,
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """决策请求列表(来源/标签/状态三过滤
    ——观测面)"""
    _require_admin(x_role)
    from services.dm61_service import Dm61Service
    return await Dm61Service().list_requests(
        source=source, tag=tag, status=status)


@router.get("/requests/{request_id}")
async def get_request(
        request_id: int,
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """请求详情(语义+影响面+环境快照
    ——观测面; 不存在 404)"""
    _require_admin(x_role)
    from services.dm61_service import Dm61Service
    try:
        return await Dm61Service().get_request(
            request_id=request_id)
    except KeyError as exc:
        raise HTTPException(status_code=404,
                            detail=str(exc))


@router.get("/model/status")
async def model_status(
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """模型状态(第36档案 champion/
    challenger/八因子——44号复用观测面)"""
    _require_admin(x_role)
    from services.dm61_service import Dm61Service
    return await Dm61Service().model_status()


def register_dm61_routes(app) -> None:
    """注册61号路由(main.py startup 调用)"""
    app.include_router(router)
