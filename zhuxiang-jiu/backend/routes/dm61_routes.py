"""61号·AI智能系统升级决策路由(P0-P5)

端点(P0-P5 全期 17——收官):
    GET  /api/dm61/registry          注册表自描述(admin, 观测面)
    POST /api/dm61/requests          决策请求接收(admin, 决策面 off 409)
    GET  /api/dm61/requests          请求列表(admin, 观测面)
    GET  /api/dm61/requests/{id}     请求详情(admin, 观测面——含归因链)
    GET  /api/dm61/model/status      模型状态(admin, 观测面)
    POST /api/dm61/assess            风险评估(admin, P1 决策面 off 409)
    POST /api/dm61/recommend         Top3 方案(admin, P1 决策面 off 409)
    POST /api/dm61/decisions/{id}/decide  人类裁决(admin, P1 终审——不受开关影响)
    POST /api/dm61/simulate          影子沙箱推演(admin, P2 决策面 off 409)
    POST /api/dm61/threshold/calibrate    阈值校准(admin, P2 管理+终审双模)
    GET  /api/dm61/thresholds        阈值视图(admin, P2 观测面)
    POST /api/dm61/decisions/{id}/dissent  反对意见 raise/override/confirm(admin, P3——不受开关影响·AI 安全机制)
    POST /api/dm61/feedback          RLHF 反馈(admin, P3——不受开关影响·人工铁律)
    GET  /api/dm61/cases             决策图谱检索(admin, P3 观测面)
    POST /api/dm61/feedback/collect  反馈回流(admin, P4——不受开关影响·人工铁律)
    GET  /api/dm61/dashboard         四区看板(admin, P5 观测面——度量+请求+决策+防御)
    POST /api/dm61/redteam           红队七向量(admin, P5——需 shadow/assist)

鉴权: 管理面 X-Role: admin(43-63号同款口径)。
统一口径(计划 §六):
    - 观测面(registry/requests 列表与
      详情/model status/thresholds/
      cases/dashboard)不受 DM61_MODE
      影响
    - 决策面(请求接收/评估/推荐/推演):
      off=拒绝(409)
    - decide/dissent/feedback/collect 与
      threshold apply 不受开关影响
      (人工铁律+AI 安全机制)
    - redteam 需决策面开放(off 409)
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


@router.post("/assess")
async def assess(
        body: dict,
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """风险评估(P1——四因子 riskScore+容错
    预算域+L1/L2/L3 判定+窗口升级;
    决策面 off 409)

    Body: {requestId, tier?, errorBudget?,
    historyFailRate?}"""
    _require_admin(x_role)
    from services.dm61_assess_service import (
        Dm61AssessService,
    )
    try:
        request_id = body.get("requestId")
        return await (
            Dm61AssessService().assess(
                request_id=int(request_id)
                if request_id is not None
                else 0,
                tier=body.get("tier"),
                error_budget=body.get(
                    "errorBudget"),
                history_fail_rate=body.get(
                    "historyFailRate")))
    except KeyError as exc:
        raise HTTPException(status_code=404,
                            detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=409,
                            detail=str(exc))


@router.post("/recommend")
async def recommend(
        body: dict,
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """Top3 方案生成(P1——确定性规则模板
    按级别+推荐理由; 决策面 off 409)

    Body: {requestId}"""
    _require_admin(x_role)
    from services.dm61_decision_service import (
        Dm61DecisionService,
    )
    try:
        request_id = body.get("requestId")
        return await (
            Dm61DecisionService().recommend(
                request_id=int(request_id)
                if request_id is not None
                else 0))
    except KeyError as exc:
        raise HTTPException(status_code=404,
                            detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=409,
                            detail=str(exc))


@router.post("/decisions/{decision_id}/decide")
async def decide(
        decision_id: int,
        body: dict,
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """人类裁决(P1 终审——adopted/modified/
    rejected 三态+46号总线提交; L3 双人
    复核铁律; 不受开关影响——人工铁律)

    Body: {action, decidedBy?, note?,
    optionIndex?, modifiedDetail?,
    coReviewer?}"""
    _require_admin(x_role)
    from services.dm61_decision_service import (
        Dm61DecisionService,
    )
    try:
        return await (
            Dm61DecisionService().decide(
                decision_id=decision_id,
                action=str(
                    body.get("action") or ""),
                decided_by=str(
                    body.get("decidedBy")
                    or "admin"),
                note=str(
                    body.get("note") or ""),
                option_index=body.get(
                    "optionIndex"),
                modified_detail=str(
                    body.get("modifiedDetail")
                    or ""),
                co_reviewer=str(
                    body.get("coReviewer")
                    or "")))
    except KeyError as exc:
        raise HTTPException(status_code=404,
                            detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=409,
                            detail=str(exc))


@router.post("/simulate")
async def simulate(
        body: dict,
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """影子沙箱推演(P2——静态校验+指标
    回放+灰度建议+回滚预案校验; 零代码
    执行; 决策面 off 409)

    Body: {requestId, changeText?}"""
    _require_admin(x_role)
    from services.dm61_sim_service import (
        Dm61SimService,
    )
    try:
        request_id = body.get("requestId")
        return await (
            Dm61SimService().simulate(
                request_id=int(request_id)
                if request_id is not None
                else 0,
                change_text=str(
                    body.get("changeText")
                    or "")))
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
    """阈值校准(P2——管理+终审双模)

    Body: {mode: submit|apply,
    l1MaxRisk?, l3MinRisk?, changeId?,
    requestedBy?, reason?, appliedBy?}

    submit: 提交 46号审批(不直接生效)
    apply: 46号 approved 后人工确认落库
    (不受开关影响——终审人工铁律)"""
    _require_admin(x_role)
    from services.dm61_threshold_service import (
        Dm61ThresholdService,
    )
    mode = str(body.get("mode") or "submit")
    try:
        if mode == "apply":
            change_id = body.get("changeId")
            return await (
                Dm61ThresholdService()
                .calibrate_apply(
                    change_id=int(change_id)
                    if change_id is not None
                    else 0,
                    applied_by=str(
                        body.get("appliedBy")
                        or "admin")))
        return await (
            Dm61ThresholdService()
            .calibrate_submit(
                l1_max_risk=float(
                    body.get("l1MaxRisk")
                    or 0),
                l3_min_risk=float(
                    body.get("l3MinRisk")
                    or 0),
                requested_by=str(
                    body.get("requestedBy")
                    or "admin"),
                reason=str(
                    body.get("reason")
                    or "")))
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
    """阈值视图(P2 观测面——当前生效值
    +46号审批留痕; 不受开关影响)"""
    _require_admin(x_role)
    from services.dm61_threshold_service import (
        Dm61ThresholdService,
    )
    return await (
        Dm61ThresholdService()
        .thresholds_view())


@router.post(
    "/decisions/{decision_id}/dissent")
async def dissent(
        decision_id: int,
        body: dict,
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """反对意见机制(P3——AI 可说"不";
    不受开关影响·AI 安全机制)

    Body: {mode: raise(默认)|
    override|confirm, reason?,
    raisedBy?, resolvedBy?}

    raise: 发起(确定性触发评估+可选
    人工理由)——决策挂 dissentFlag
    override: 人类驳回 AI 意见
    (reason 必填留痕)
    confirm: 采纳 AI 意见
    (recommended 态决策终止)"""
    _require_admin(x_role)
    from services.dm61_dissent_service import (
        Dm61DissentService,
    )
    mode = str(body.get("mode") or "raise")
    try:
        if mode == "override":
            return await (
                Dm61DissentService()
                .resolve(
                    decision_id=decision_id,
                    action="override",
                    reason=str(
                        body.get("reason")
                        or ""),
                    resolved_by=str(
                        body.get("resolvedBy")
                        or "admin")))
        if mode == "confirm":
            return await (
                Dm61DissentService()
                .resolve(
                    decision_id=decision_id,
                    action="confirm",
                    reason=str(
                        body.get("reason")
                        or ""),
                    resolved_by=str(
                        body.get("resolvedBy")
                        or "admin")))
        if mode == "evaluate":
            return await (
                Dm61DissentService()
                .evaluate(decision_id))
        return await (
            Dm61DissentService()
            .raise_dissent(
                decision_id=decision_id,
                raised_by=str(
                    body.get("raisedBy")
                    or "ai"),
                reason=str(
                    body.get("reason")
                    or "")))
    except KeyError as exc:
        raise HTTPException(status_code=404,
                            detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=409,
                            detail=str(exc))


@router.post("/feedback")
async def submit_feedback(
        body: dict,
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """RLHF 反馈(P3——决策 1:1 三态+
    执行结果; 不受开关影响·人工铁律)

    Body: {decisionId, action:
    adopted|modified|rejected,
    outcome?: good|bad, comment?,
    by?}"""
    _require_admin(x_role)
    from services.dm61_feedback_service import (
        Dm61FeedbackService,
    )
    try:
        decision_id = body.get("decisionId")
        return await (
            Dm61FeedbackService().submit(
                decision_id=int(decision_id)
                if decision_id is not None
                else 0,
                action=str(
                    body.get("action") or ""),
                outcome=body.get("outcome"),
                comment=str(
                    body.get("comment")
                    or ""),
                by=str(
                    body.get("by")
                    or "admin")))
    except KeyError as exc:
        raise HTTPException(status_code=404,
                            detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=409,
                            detail=str(exc))


@router.get("/cases")
async def cases(
        tag: str = None,
        sensitivity: str = None,
        level: str = None,
        outcome: str = None,
        risk: float = None,
        limit: int = 10,
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """决策图谱检索(P3 观测面——标签×
    敏感级×风险带×结果; 不受开关影响)

    Query: ?tag=&sensitivity=&level=
    &outcome=&risk=(±15 同带)&limit="""
    _require_admin(x_role)
    from services.dm61_graph_service import (
        Dm61GraphService,
    )
    return await (
        Dm61GraphService().similar_cases(
            tag=tag,
            sensitivity=sensitivity,
            level=level,
            outcome=outcome,
            risk=risk,
            limit=max(1, min(
                int(limit or 10), 50))))


@router.post("/feedback/collect")
async def feedback_collect(
        body: dict,
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """反馈回流(P4——七类终态信号→44号池
    双写 decisionId 1:1 幂等+置信度校准
    预警; 不受开关影响——人工铁律)

    Body: {limit?}"""
    _require_admin(x_role)
    from services.dm61_learn_service import (
        Dm61LearnService,
    )
    try:
        limit = body.get("limit")
        return await (
            Dm61LearnService()
            .collect_feedback(
                limit=int(limit)
                if limit is not None
                else 500))
    except ValueError as exc:
        raise HTTPException(status_code=409,
                            detail=str(exc))


@router.get("/dashboard")
async def dashboard(
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """四区看板(P5 观测面——度量+请求
    +决策+防御; 纯确定性聚合不受开关
    影响)"""
    _require_admin(x_role)
    from services.dm61_dashboard_service import (
        Dm61DashboardService,
    )
    return await (
        Dm61DashboardService().dashboard())


@router.post("/redteam")
async def redteam(
        body: dict,
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """红队七向量(P5——标签伪造/矩阵
    操纵/沙箱逃逸/先验投毒/裁决伪造/
    反馈污染/图谱污染; 确定性离线
    可复现——需 shadow/assist)"""
    _require_admin(x_role)
    from services.dm61_redteam_service import (
        Dm61RedteamService,
    )
    try:
        return await (
            Dm61RedteamService().run_all())
    except ValueError as exc:
        raise HTTPException(status_code=409,
                            detail=str(exc))


def register_dm61_routes(app) -> None:
    """注册61号路由(main.py startup 调用)"""
    app.include_router(router)
