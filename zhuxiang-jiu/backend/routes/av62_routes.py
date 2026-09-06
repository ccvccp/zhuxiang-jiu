"""62号·AI智能无形资产估值路由(P0-P5)

端点(P2 14; 全期规划):
    GET  /api/av62/registry          注册表自描述(admin, 观测面)
    POST /api/av62/assets            资产登记(admin, 决策面 off 409)
    GET  /api/av62/assets            资产列表(admin, 观测面)
    GET  /api/av62/assets/{id}       资产详情(admin, 观测面)
    GET  /api/av62/model/status      模型状态(admin, 观测面)
    POST /api/av62/assess            估值引擎(admin, 决策面 off 409)
    GET  /api/av62/assessments      评估列表(admin, 观测面)
    GET  /api/av62/assessments/{id}  评估详情(admin, 观测面)
    POST /api/av62/scenarios/convert 场景折算(admin, 决策面 off 409)
    GET  /api/av62/scenarios         折算表+流动性+衰减(admin, 观测面)
    POST /api/av62/stress            反事实压测(admin, 决策面 off 409)
    POST /api/av62/activate          衰减重激活(admin, 决策面 off 409)
    POST /api/av62/threshold/calibrate 阈值校准(管理+终审双模)
    GET  /api/av62/thresholds        阈值视图(admin, 观测面)
    # P3: POST /appeals + /appeals/{id}/review + GET /fairness/report
    # P4: POST /feedback/collect
    # P5: GET /dashboard + POST /redteam

鉴权: 管理面 X-Role: admin(43-61号同款口径)。
统一口径(计划 §六):
    - 观测面(registry/assets 列表与
      详情/model status/assessments/
      scenarios 表/thresholds)不受
      AV62_MODE 影响
    - 决策面(资产登记/估值引擎/场景
      折算/压测/激活): off=拒绝(409)
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


@router.post("/assess")
async def assess(
        body: dict,
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """估值引擎(P1——因果估值+置信度三档
    +归因报告; 决策面 off 409)

    Body: {assetId} 或 {subjectId}
    (二选一——资产级/主体级聚合),
    assessedBy?"""
    _require_admin(x_role)
    from services.av62_assess_service import (
        Av62AssessService,
    )
    asset_id = body.get("assetId")
    subject_id = body.get("subjectId")
    if asset_id is None \
            and subject_id is None:
        raise HTTPException(
            status_code=409,
            detail="assetId 或 subjectId "
                   "必填其一")
    try:
        if asset_id is not None:
            return await (
                Av62AssessService()
                .assess_asset(
                    int(asset_id),
                    assessed_by=str(
                        body.get("assessedBy")
                        or "admin")))
        return await (
            Av62AssessService()
            .assess_subject(
                int(subject_id),
                assessed_by=str(
                    body.get("assessedBy")
                    or "admin")))
    except KeyError as exc:
        raise HTTPException(status_code=404,
                            detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=409,
                            detail=str(exc))


@router.get("/assessments")
async def assessments(
        asset_id: int = None,
        limit: int = 100,
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """评估列表(版本链倒序——观测面)"""
    _require_admin(x_role)
    from services.av62_assess_service import (
        Av62AssessService,
    )
    return await Av62AssessService() \
        .list_assessments(
            asset_id=asset_id, limit=limit)


@router.get("/assessments/{assess_id}")
async def get_assessment(
        assess_id: int,
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """评估详情(贡献度+因子快照+归因链
    ——观测面; 不存在 404)"""
    _require_admin(x_role)
    from services.av62_assess_service import (
        Av62AssessService,
    )
    try:
        return await (
            Av62AssessService()
            .get_assessment(assess_id))
    except KeyError as exc:
        raise HTTPException(status_code=404,
                            detail=str(exc))


@router.post("/scenarios/convert")
async def convert_scenario(
        body: dict,
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """场景信值折算(P2——SCENARIO_FACTORS×
    衰减×流动性约束+45号增益域输出;
    决策面 off 409)

    Body: {subjectId, scenario(bidding/
    financing/partnership/expedited),
    deposit? 默认 true, convertedBy?}"""
    _require_admin(x_role)
    from services.av62_liquidity_service import (
        Av62LiquidityService,
    )
    try:
        return await (
            Av62LiquidityService()
            .convert_scenario(
                subject_id=int(
                    body.get("subjectId")
                    or 0),
                scenario=str(
                    body.get("scenario")
                    or ""),
                deposit=bool(
                    body.get("deposit",
                             True)),
                converted_by=str(
                    body.get("convertedBy")
                    or "admin")))
    except ValueError as exc:
        raise HTTPException(status_code=409,
                            detail=str(exc))


@router.get("/scenarios")
async def scenarios(
        subject_id: int = None,
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """场景折算表+流动性三档+衰减模型
    (观测面不受开关影响)"""
    _require_admin(x_role)
    from services.av62_liquidity_service import (
        Av62LiquidityService,
    )
    view = Av62LiquidityService() \
        .scenario_view()
    if subject_id:
        profiles = await (
            Av62LiquidityService()
            .list_profiles(
                subject_id=subject_id))
        view["subjectProfiles"] = {
            "total": profiles.get("total"),
            "byTier":
                profiles.get("byTier"),
        }
    return view


@router.post("/stress")
async def stress(
        body: dict,
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """反事实压测(P2——要素摘除重算
    Δ%; 决策面 off 409)

    Body: {subjectId, removeAssetIds?
    [..], removeDomains? [..]}"""
    _require_admin(x_role)
    from services.av62_liquidity_service import (
        Av62LiquidityService,
    )
    try:
        return await (
            Av62LiquidityService()
            .stress_subject(
                subject_id=int(
                    body.get("subjectId")
                    or 0),
                remove_asset_ids=body.get(
                    "removeAssetIds"),
                remove_domains=body.get(
                    "removeDomains")))
    except ValueError as exc:
        raise HTTPException(status_code=409,
                            detail=str(exc))


@router.post("/activate")
async def activate(
        body: dict,
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """衰减重激活(P2——合规使用/知识
    更新→衰减重置; 决策面 off 409)

    Body: {assetId, reason(compliance_
    use/knowledge_update), activatedBy?}"""
    _require_admin(x_role)
    from services.av62_liquidity_service import (
        Av62LiquidityService,
    )
    try:
        return await (
            Av62LiquidityService()
            .activate_asset(
                asset_id=int(
                    body.get("assetId")
                    or 0),
                reason=str(
                    body.get("reason")
                    or ""),
                activated_by=str(
                    body.get("activatedBy")
                    or "admin")))
    except KeyError as exc:
        raise HTTPException(status_code=404,
                            detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=409,
                            detail=str(exc))


@router.post("/threshold/calibrate")
async def threshold_calibrate(
        body: dict,
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """阈值校准(P2——46号审批双模:
    submit 管理模/apply 终审模
    不受开关影响)

    Body(submit): {halfLifeDays} 或
    {scenario, multiplier}, reason,
    requestedBy?
    Body(apply): {changeId, mode:
    "apply", appliedBy?}"""
    _require_admin(x_role)
    from services.av62_threshold_service import (
        Av62ThresholdService,
    )
    svc = Av62ThresholdService()
    mode = str(body.get("mode") or "")
    try:
        if mode == "apply":
            return await svc.calibrate_apply(
                change_id=int(
                    body.get("changeId")
                    or 0),
                applied_by=str(
                    body.get("appliedBy")
                    or "admin"))
        m = body.get("multiplier")
        return await svc.calibrate_submit(
            half_life_days=body.get(
                "halfLifeDays"),
            scenario=str(
                body.get("scenario")
                or ""),
            multiplier=float(m)
            if m is not None else None,
            requested_by=str(
                body.get("requestedBy")
                or "admin"),
            reason=str(
                body.get("reason") or ""))
    except KeyError as exc:
        raise HTTPException(status_code=404,
                            detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=409,
                            detail=str(exc))


@router.get("/thresholds")
async def thresholds(
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """阈值视图(生效配置+46号审批留痕
    ——观测面不受开关影响)"""
    _require_admin(x_role)
    from services.av62_threshold_service import (
        Av62ThresholdService,
    )
    return await Av62ThresholdService() \
        .thresholds_view()


def register_av62_routes(app) -> None:
    """注册62号路由(main.py startup 调用)"""
    app.include_router(router)
