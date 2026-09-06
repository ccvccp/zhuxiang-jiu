"""62号·AI智能无形资产估值路由(P0-P5)

端点(P3 20; 全期规划):
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
    POST /api/av62/appeals           申诉提交(不受开关影响)
    GET  /api/av62/appeals           申诉列表(admin, 观测面)
    GET  /api/av62/appeals/{id}      申诉详情(admin, 观测面)
    POST /api/av62/appeals/{id}/review 申诉裁决(终审, 人工铁律)
    GET  /api/av62/fairness/report   公平审计报告(admin, 观测面)
    POST /api/av62/fairness/audit    触发公平审计(管理面)
    POST /api/av62/verifications     验证提交(admin, 管理面)
    POST /api/av62/feedback/collect   验证回流(不受开关影响)
    GET  /api/av62/learn/status      回流状态(admin, 观测面)
    GET  /api/av62/dashboard         四区看板(admin, 观测面)
    POST /api/av62/redteam           红队七向量(admin, 决策面 off 409)

鉴权: 管理面 X-Role: admin(43-61号同款口径)。
统一口径(计划 §六):
    - 观测面(registry/assets 列表与
      详情/model status/assessments/
      scenarios 表/thresholds/
      fairness report)不受 AV62_MODE
      影响
    - 决策面(资产登记/估值引擎/场景
      折算/压测/激活): off=拒绝(409)
    - 申诉提交/裁决不受开关影响
      (人工铁律)
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


@router.post("/appeals")
async def submit_appeal(
        body: dict,
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """申诉提交(P3——异议+补充证据+
    自动重估; 不受开关影响)

    Body: {assetId, reason,
    newEvidence? {封闭字段}, appealedBy?
    (会员/管理员标识)}"""
    _require_admin(x_role)
    from services.av62_appeal_service import (
        Av62AppealService,
    )
    try:
        new_evidence = body.get(
            "newEvidence")
        return await (
            Av62AppealService()
            .submit_appeal(
                asset_id=int(
                    body.get("assetId")
                    or 0),
                reason=str(
                    body.get("reason")
                    or ""),
                new_evidence=new_evidence
                if isinstance(
                    new_evidence, dict)
                else {},
                appealed_by=str(
                    body.get("appealedBy")
                    or "member")))
    except KeyError as exc:
        raise HTTPException(status_code=404,
                            detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=409,
                            detail=str(exc))


@router.post("/appeals/{appeal_id}/review")
async def review_appeal(
        appeal_id: int,
        body: dict,
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """申诉裁决(P3——终审人工铁律,
    不受开关影响; uphold 维持/
    overturn 翻转+留痕)

    Body: {decision(uphold/overturn),
    reviewedBy, reviewNote}"""
    _require_admin(x_role)
    from services.av62_appeal_service import (
        Av62AppealService,
    )
    try:
        return await (
            Av62AppealService()
            .review_appeal(
                appeal_id=int(appeal_id),
                decision=str(
                    body.get("decision")
                    or ""),
                reviewed_by=str(
                    body.get("reviewedBy")
                    or ""),
                review_note=str(
                    body.get("reviewNote")
                    or "")))
    except KeyError as exc:
        raise HTTPException(status_code=404,
                            detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=409,
                            detail=str(exc))


@router.get("/appeals")
async def appeals(
        asset_id: int = None,
        status: str = None,
        limit: int = 100,
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """申诉列表(三态分布+翻转率
    ——观测面)"""
    _require_admin(x_role)
    from services.av62_appeal_service import (
        Av62AppealService,
    )
    return await Av62AppealService() \
        .list_appeals(
            asset_id=asset_id,
            status=status, limit=limit)


@router.get("/appeals/{appeal_id}")
async def get_appeal(
        appeal_id: int,
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """申诉详情(原值/重估值/裁决留痕
    ——观测面; 不存在 404)"""
    _require_admin(x_role)
    from services.av62_appeal_service import (
        Av62AppealService,
    )
    try:
        return await (
            Av62AppealService()
            .get_appeal(appeal_id))
    except KeyError as exc:
        raise HTTPException(status_code=404,
                            detail=str(exc))


@router.get("/fairness/report")
async def fairness_report(
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """公平审计报告(分角色估值分布
    ——46号 fairness 口径; 观测面)"""
    _require_admin(x_role)
    from services.av62_fairness_service import (
        Av62FairnessService,
    )
    return await Av62FairnessService() \
        .get_report()


@router.post("/fairness/audit")
async def fairness_audit(
        body: dict,
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """触发公平审计(P3——管理面
    手动触发; 不受开关影响)"""
    _require_admin(x_role)
    from services.av62_fairness_service import (
        Av62FairnessService,
    )
    return await Av62FairnessService() \
        .run_audit(
            triggered_by=str(
                body.get("triggeredBy")
                or "admin"))


@router.post("/verifications")
async def submit_verification(
        body: dict,
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """业务结果验证提交(P4——预测 vs
    实际→偏差三档信号; 管理面)"""
    _require_admin(x_role)
    from services.av62_learn_service import (
        Av62LearnService,
    )
    try:
        return await (
            Av62LearnService()
            .submit_verification(
                assess_id=int(
                    body.get("assessId")
                    or 0),
                actual_value=body.get(
                    "actualValue"),
                verified_by=str(
                    body.get("verifiedBy")
                    or "admin")))
    except KeyError as exc:
        raise HTTPException(status_code=404,
                            detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=409,
                            detail=str(exc))


@router.post("/feedback/collect")
async def feedback_collect(
        body: dict,
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """验证回流批处理(P4——44号池双写
    assessId 1:1 幂等+偏差预警经 46号;
    不受开关影响·回流管理面铁律)"""
    _require_admin(x_role)
    from services.av62_learn_service import (
        Av62LearnService,
    )
    return await Av62LearnService() \
        .collect_verification()


@router.get("/learn/status")
async def learn_status(
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """回流状态观测面(P4——验证统计+
    幂等标记; 不受开关影响)"""
    _require_admin(x_role)
    from services.av62_learn_service import (
        Av62LearnService,
    )
    return await Av62LearnService() \
        .learn_status()


@router.get("/dashboard")
async def dashboard(
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """四区看板(P5——度量/资产/评估/
    防御; 观测面不受开关影响)"""
    _require_admin(x_role)
    from services.av62_dashboard_service import (
        Av62DashboardService,
    )
    return await Av62DashboardService() \
        .get_dashboard()


@router.post("/redteam")
async def redteam(
        body: dict,
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """红队七向量(P5——证据伪造/权重
    操纵/归因幻觉/流动性滥用/估值
    套利/申诉刷分/负资产洗白;
    决策面 off 409)"""
    _require_admin(x_role)
    from services.av62_redteam_service import (
        Av62RedteamService,
    )
    try:
        return await (
            Av62RedteamService()
            .run_all())
    except ValueError as exc:
        raise HTTPException(status_code=409,
                            detail=str(exc))


def register_av62_routes(app) -> None:
    """注册62号路由(main.py startup 调用)"""
    app.include_router(router)
