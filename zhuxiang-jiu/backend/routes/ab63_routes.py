"""63号·AI智能后台管理路由(P0-P3)

端点(P0+P1+P2+P3 14):
    GET  /api/ab63/registry            注册表自描述(admin, 观测面)
    POST /api/ab63/grants              权限裁决(admin, 决策面 off 409)
    GET  /api/ab63/grants              裁决记录列表(admin, 观测面)
    GET  /api/ab63/grants/{grantId}    裁决单条 reason 链(admin, P1 观测面)
    POST /api/ab63/workbench/render    工作台渲染(admin, 决策面 off 409)
    POST /api/ab63/guard/check          编辑态护航检测(admin, P2 决策面 off 409)
    GET  /api/ab63/model/status         模型状态(admin, 观测面)
    POST /api/ab63/submissions          发布提交+预检分流(admin, P3 决策面 off 409)
    GET  /api/ab63/submissions/{subId}  提交详情+证据链(admin, P3 观测面)
    POST /api/ab63/submissions/{subId}/review    人工裁决(admin, P3 终审——不受开关影响)
    POST /api/ab63/submissions/{subId}/appeal     提交申诉(admin, P3——不受开关影响)
    POST /api/ab63/submissions/{subId}/appeal/resolve  申诉裁决(admin, P3 终审——不受开关影响)
    GET  /api/ab63/reviews/queue       审核队列风险分布(admin, P3 观测面)
    POST /api/ab63/threshold/calibrate 阈值校准(admin, P3——管理+终审双模)
    GET  /api/ab63/thresholds          阈值视图(admin, P3 观测面)

鉴权: 管理面 X-Role: admin(43-59号同款口径)。
统一口径:
    - 观测面(registry/grants/model/status/
      submissions 详情/reviews 队列/
      thresholds)不受 AB63_MODE 影响
    - 决策面(grants/workbench/guard/
      submissions 提交): off=拒绝(409)
    - review/appeal/resolve 终审不受
      开关影响(人工铁律)
    - KeyError → 404 / ValueError → 409
"""

from fastapi import APIRouter, Header, HTTPException

router = APIRouter(prefix="/api/ab63",
                   tags=["AI智能后台管理(63号)"])


def _require_admin(x_role: str | None) -> str:
    if not x_role or x_role != "admin":
        raise HTTPException(status_code=403,
                            detail="需要 X-Role: admin")


@router.get("/registry")
async def registry(
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """后台注册表自描述(四轴规则+五角色
    模板——观测面)"""
    _require_admin(x_role)
    from services.ab63_service import Ab63Service
    return Ab63Service.registry()


@router.post("/grants")
async def evaluate_grant(
        body: dict,
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """权限裁决(四轴确定性计算+reason 可
    解释链; 决策面 off 409)

    Body: {memberId, role, action, tier?,
    complianceRate?, period?, sensitivity?}"""
    _require_admin(x_role)
    from services.ab63_service import Ab63Service
    try:
        member_id = body.get("memberId")
        return await (
            Ab63Service().evaluate_grant(
                member_id=int(member_id)
                if member_id is not None
                else 0,
                role=str(
                    body.get("role") or ""),
                action=str(
                    body.get("action") or ""),
                tier=body.get("tier"),
                compliance_rate=body.get(
                    "complianceRate"),
                period=str(
                    body.get("period")
                    or "normal"),
                sensitivity=str(
                    body.get("sensitivity")
                    or "low")))
    except ValueError as exc:
        raise HTTPException(status_code=409,
                            detail=str(exc))


@router.get("/grants")
async def grants(
        member_id: int = None,
        role: str = None,
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """权限裁决记录列表(reason 可解释链
    ——观测面)"""
    _require_admin(x_role)
    from services.ab63_service import Ab63Service
    return await Ab63Service().list_grants(
        member_id=member_id, role=role)


@router.get("/grants/{grant_id}")
async def get_grant(
        grant_id: int,
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """裁决单条(P1 观测面——ruleId+
    recoveryPath 完整可解释链; 不存在 404)"""
    _require_admin(x_role)
    from services.ab63_service import Ab63Service
    try:
        return await Ab63Service().get_grant(
            grant_id=grant_id)
    except KeyError as exc:
        raise HTTPException(status_code=404,
                            detail=str(exc))


@router.post("/workbench/render")
async def render_workbench(
        body: dict,
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """工作台渲染(P2 情境化——意图导航+
    无障碍标记+模板推荐均为建议性;
    决策面 off 409)

    Body: {memberId, role, novice?,
    intentText?, accessibility?
    {largeFont, voiceAssist, pauseDetected},
    industry?}"""
    _require_admin(x_role)
    from services.ab63_service import Ab63Service
    try:
        member_id = body.get("memberId")
        return await (
            Ab63Service().render_workbench(
                member_id=int(member_id)
                if member_id is not None
                else 0,
                role=str(
                    body.get("role") or ""),
                novice=bool(
                    body.get("novice")),
                intent_text=body.get(
                    "intentText"),
                accessibility=body.get(
                    "accessibility")
                if isinstance(
                    body.get("accessibility"),
                    dict) else None,
                industry=str(
                    body.get("industry")
                    or "") or None))
    except ValueError as exc:
        raise HTTPException(status_code=409,
                            detail=str(exc))


@router.post("/guard/check")
async def guard_check(
        body: dict,
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """编辑态护航检测(P2 三轨三档——
    确定性规则 LLM 不进判定链; 决策面
    off 409)

    Body: {memberId, role, content?,
    form? {title, price, validityStart,
    validityEnd, refundPolicy,
    collectFields}, estimatedCost?}"""
    _require_admin(x_role)
    from services.ab63_guard_service import (
        Ab63GuardService,
    )
    try:
        member_id = body.get("memberId")
        return await (
            Ab63GuardService().check(
                member_id=int(member_id)
                if member_id is not None
                else 0,
                role=str(
                    body.get("role") or ""),
                content=body.get("content"),
                form=body.get("form")
                if isinstance(
                    body.get("form"), dict)
                else None,
                estimated_cost=float(
                    body.get("estimatedCost")
                    or 0)))
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=409,
                            detail=str(exc))


@router.post("/submissions")
async def submit_submission(
        body: dict,
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """发布提交+预检分流(P3——Publish_Score
    三因子确定性公式→L1/L2/L3; L3 永不
    自动; 决策面 off 409)

    Body: {memberId, role, content,
    sensitivity?, tags?, tier?}"""
    _require_admin(x_role)
    from services.ab63_submission_service import (
        Ab63SubmissionService,
    )
    try:
        member_id = body.get("memberId")
        return await (
            Ab63SubmissionService().submit(
                member_id=int(member_id)
                if member_id is not None
                else 0,
                role=str(
                    body.get("role") or ""),
                content=str(
                    body.get("content")
                    or ""),
                sensitivity=str(
                    body.get("sensitivity")
                    or "low"),
                tags=body.get("tags")
                if isinstance(
                    body.get("tags"), list)
                else None,
                tier=body.get("tier")))
    except ValueError as exc:
        raise HTTPException(status_code=409,
                            detail=str(exc))


@router.get("/submissions/{sub_id}")
async def get_submission(
        sub_id: int,
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """提交详情+审核证据链(P3 观测面——
    指纹链可追溯可申诉)"""
    _require_admin(x_role)
    from services.ab63_submission_service import (
        Ab63SubmissionService,
    )
    try:
        return await (
            Ab63SubmissionService()
            .get_submission(
                sub_id=sub_id))
    except KeyError as exc:
        raise HTTPException(status_code=404,
                            detail=str(exc))


@router.post("/submissions/{sub_id}/review")
async def review_submission(
        sub_id: int,
        body: dict,
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """人工裁决(P3 终审——L2 单人确认/
    L3 双人独立+合规官终审; 不受开关
    影响——人工铁律)

    Body: {approve, reviewer?, reviewNote?}"""
    _require_admin(x_role)
    from services.ab63_submission_service import (
        Ab63SubmissionService,
    )
    try:
        return await (
            Ab63SubmissionService().review(
                sub_id=sub_id,
                approve=bool(
                    body.get("approve")),
                reviewer=str(
                    body.get("reviewer")
                    or "admin"),
                review_note=str(
                    body.get("reviewNote")
                    or "")))
    except KeyError as exc:
        raise HTTPException(status_code=404,
                            detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=409,
                            detail=str(exc))


@router.post("/submissions/{sub_id}/appeal")
async def appeal_submission(
        sub_id: int,
        body: dict,
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """提交申诉(P3——published/rejected
    →disputed; 不受开关影响)

    Body: {appellant?, reason?}"""
    _require_admin(x_role)
    from services.ab63_submission_service import (
        Ab63SubmissionService,
    )
    try:
        return await (
            Ab63SubmissionService().appeal(
                sub_id=sub_id,
                appellant=str(
                    body.get("appellant")
                    or "member"),
                reason=str(
                    body.get("reason")
                    or "")))
    except KeyError as exc:
        raise HTTPException(status_code=404,
                            detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=409,
                            detail=str(exc))


@router.post(
    "/submissions/{sub_id}/appeal/resolve")
async def resolve_appeal(
        sub_id: int,
        body: dict,
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """申诉裁决(P3 终审——disputed→
    adjusted 翻转留痕; 不受开关影响)

    Body: {overturn, adjudicator?, note?}"""
    _require_admin(x_role)
    from services.ab63_submission_service import (
        Ab63SubmissionService,
    )
    try:
        return await (
            Ab63SubmissionService()
            .resolve_appeal(
                sub_id=sub_id,
                overturn=bool(
                    body.get("overturn")),
                adjudicator=str(
                    body.get("adjudicator")
                    or "admin"),
                note=str(
                    body.get("note")
                    or "")))
    except KeyError as exc:
        raise HTTPException(status_code=404,
                            detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=409,
                            detail=str(exc))


@router.get("/reviews/queue")
async def reviews_queue(
        status: str = None,
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """审核队列(P3 观测面——风险分布+
    待审列表)"""
    _require_admin(x_role)
    from services.ab63_submission_service import (
        Ab63SubmissionService,
    )
    return await (
        Ab63SubmissionService()
        .queue_view(status=status))


@router.post("/threshold/calibrate")
async def threshold_calibrate(
        body: dict,
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """阈值校准(P3——管理+终审双模)

    Body: {mode: submit|apply,
    l1Threshold?, l2Threshold?, changeId?,
    requestedBy?, reason?, appliedBy?}

    submit: 提交 46号审批(不直接生效)
    apply: 46号 approved 后人工确认落库"""
    _require_admin(x_role)
    from services.ab63_submission_service import (
        Ab63SubmissionService,
    )
    mode = str(body.get("mode") or "submit")
    try:
        if mode == "apply":
            change_id = body.get("changeId")
            return await (
                Ab63SubmissionService()
                .calibrate_apply(
                    change_id=int(
                        change_id)
                    if change_id
                    is not None else 0,
                    applied_by=str(
                        body.get("appliedBy")
                        or "admin")))
        return await (
            Ab63SubmissionService()
            .calibrate_submit(
                l1_threshold=float(
                    body.get("l1Threshold")
                    or 0),
                l2_threshold=float(
                    body.get("l2Threshold")
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
    """阈值视图(P3 观测面——当前生效值
    +46号审批留痕)"""
    _require_admin(x_role)
    from services.ab63_submission_service import (
        Ab63SubmissionService,
    )
    return await (
        Ab63SubmissionService()
        .thresholds_view())


@router.get("/model/status")
async def model_status(
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """模型状态(第38档案 champion/
    challenger/八因子——44号复用观测面)"""
    _require_admin(x_role)
    from services.ab63_service import Ab63Service
    return await Ab63Service().model_status()


def register_ab63_routes(app) -> None:
    """注册63号路由(main.py startup 调用)"""
    app.include_router(router)
