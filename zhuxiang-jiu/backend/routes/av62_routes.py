"""62号·AI智能无形资产估值路由(P0-P5)

端点(P0 4; 全期规划 15):
    GET  /api/av62/registry          注册表自描述(admin, 观测面)
    POST /api/av62/assets            资产登记(admin, 决策面 off 409)
    GET  /api/av62/assets            资产列表(admin, 观测面)
    GET  /api/av62/assets/{id}       资产详情(admin, 观测面)
    GET  /api/av62/model/status      模型状态(admin, 观测面)
    # P1: POST /assess
    # P2: POST /stress + /activate + GET /scenarios
    #     + POST /threshold/calibrate + GET /thresholds
    # P3: POST /appeals + /appeals/{id}/review + GET /fairness/report
    # P4: POST /feedback/collect
    # P5: GET /dashboard + POST /redteam

鉴权: 管理面 X-Role: admin(43-61号同款口径)。
统一口径(计划 §六):
    - 观测面(registry/assets 列表与
      详情/model status)不受 AV62_MODE
      影响
    - 决策面(资产登记): off=拒绝(409)
    - 后续: 申诉/终审/回流不受开关
      影响(人工铁律)
    - KeyError → 404 / ValueError → 409
"""

from fastapi import APIRouter, Header, HTTPException

router = APIRouter(prefix="/api/av62",
                   tags=["AI智能无形资产估值(62号)"])


def _require_admin(x_role: str | None) -> str:
    if not x_role or x_role != "admin":
        raise HTTPException(status_code=403,
                            detail="需要 X-Role: admin")


@router.get("/registry")
async def registry(
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """信任要素注册表自描述(三角色×九资产域
    +负资产域封闭注册——观测面)"""
    _require_admin(x_role)
    from services.av62_service import Av62Service
    return Av62Service.registry()


@router.post("/assets")
async def register_asset(
        body: dict,
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """资产登记(P0——主体×角色×要素域+
    证据快照封闭校验; 决策面 off 409)

    Body: {subjectId, role
    (enterprise/organization/personal),
    domain(九正域+risk), evidence
    {封闭字段}, label?, registeredBy?}"""
    _require_admin(x_role)
    from services.av62_service import Av62Service
    try:
        subject_id = body.get("subjectId")
        evidence = body.get("evidence")
        return await (
            Av62Service().register_asset(
                subject_id=int(subject_id)
                if subject_id is not None
                else 0,
                role=str(
                    body.get("role") or ""),
                domain=str(
                    body.get("domain") or ""),
                evidence=evidence
                if isinstance(evidence, dict)
                else {},
                label=str(
                    body.get("label") or ""),
                registered_by=str(
                    body.get("registeredBy")
                    or "admin")))
    except ValueError as exc:
        raise HTTPException(status_code=409,
                            detail=str(exc))


@router.get("/assets")
async def assets(
        subject_id: int = None,
        role: str = None,
        domain: str = None,
        status: str = None,
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """资产列表(主体/角色/域/状态四过滤
    ——观测面)"""
    _require_admin(x_role)
    from services.av62_service import Av62Service
    return await Av62Service().list_assets(
        subject_id=subject_id, role=role,
        domain=domain, status=status)


@router.get("/assets/{asset_id}")
async def get_asset(
        asset_id: int,
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """资产详情(证据快照+要素定义——
    观测面; 不存在 404)"""
    _require_admin(x_role)
    from services.av62_service import Av62Service
    try:
        return await Av62Service().get_asset(
            asset_id=asset_id)
    except KeyError as exc:
        raise HTTPException(status_code=404,
                            detail=str(exc))


@router.get("/model/status")
async def model_status(
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """模型状态(第37档案 champion/
    challenger/八因子——44号复用观测面)"""
    _require_admin(x_role)
    from services.av62_service import Av62Service
    return await Av62Service().model_status()


def register_av62_routes(app) -> None:
    """注册62号路由(main.py startup 调用)"""
    app.include_router(router)
