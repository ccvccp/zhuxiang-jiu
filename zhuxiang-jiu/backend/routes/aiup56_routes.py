"""56号·AI智能升级管理路由(P0-P5)

端点(P0 5 + P1 4 + P2 2 + P3 3 + P4 4 + P5 2 = 20):
    GET  /api/aiup56/registry            注册表自描述(admin, 观测面)
    POST /api/aiup56/signals/scan        信号采集+决策评估(admin, 管理)
    GET  /api/aiup56/proposals           提案列表(admin, 观测面)
    GET  /api/aiup56/proposal/{id}       提案详情(admin, 观测面)
    GET  /api/aiup56/model/status         模型状态(admin, 观测面)
    POST /api/aiup56/proposals/{id}/plan 规划Agent(admin, P1)
    GET  /api/aiup56/proposals/{id}/tasks 任务列表(admin, 观测面, P1)
    POST /api/aiup56/proposals/{id}/code  编码Agent(admin, P1)
    GET  /api/aiup56/proposals/{id}/assets 资产列表(admin, 观测面, P1)
    POST /api/aiup56/proposals/{id}/test  测试Agent+信值沙箱(admin, P2)
    GET  /api/aiup56/proposals/{id}/sandboxes 沙箱列表(admin, 观测面, P2)
    POST /api/aiup56/proposals/{id}/audit 审计Agent(admin, P3)
    GET  /api/aiup56/proposals/{id}/panel  审批面板视图(admin, 观测面, P3)
    POST /api/aiup56/proposals/{id}/review 人类审批(admin, P3)
    POST /api/aiup56/proposals/{id}/deliver  资产包交付(admin, P4)
    POST /api/aiup56/proposals/{id}/rollback 语义回滚(admin, P4)
    POST /api/aiup56/feedback/collect     决策回流补标(admin, P4)
    GET  /api/aiup56/feedback/stats        回流统计(admin, 观测面, P4)
    GET  /api/aiup56/dashboard             四区看板(admin, 观测面, P5)
    POST /api/aiup56/redteam               红队六向量(admin, P5)

鉴权: 管理面 X-Role: admin(43-55号同款口径)。
统一口径:
    - 观测面(registry/proposals/proposal/model/
      status/tasks/assets/sandboxes/panel/stats/
      dashboard)不受 AIUP56_MODE 影响
    - 决策面(signals/scan+plan+code+test+
      audit): off=拒绝(409——shadow/assist 开放)
    - review/deliver/rollback(交付链人工动作):
      不受开关影响(终审人工铁律)
    - feedback/collect: 不受开关影响(回流管理面)
    - redteam: 需决策面开放(off 409——
      红队需攻击面)
    - KeyError → 404 / ValueError → 409
"""

from fastapi import APIRouter, Header, HTTPException

router = APIRouter(prefix="/api/aiup56",
                   tags=["AI智能升级管理(56号)"])


def _require_admin(x_role: str | None) -> str:
    if not x_role or x_role != "admin":
        raise HTTPException(status_code=403,
                            detail="需要 X-Role: admin")
    return x_role


@router.get("/registry")
async def get_registry(
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """信号注册表自描述(10 项四侧+权重——观测面
    不受开关影响)"""
    _require_admin(x_role)
    from services.aiup56_service import Aiup56Service
    return Aiup56Service.registry()


@router.post("/signals/scan")
async def signals_scan(
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """信号采集+决策评估(采集→融合→八因子评分→
    三级决策→提案落库; off 409)"""
    _require_admin(x_role)
    from services.aiup56_service import Aiup56Service
    try:
        return await Aiup56Service(
        ).evaluate_and_propose()
    except ValueError as exc:
        raise HTTPException(status_code=409,
                            detail=str(exc))


@router.get("/proposals")
async def list_proposals(
        status: str | None = None,
        limit: int = 100,
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """提案列表(状态过滤——观测面)"""
    _require_admin(x_role)
    from repositories.aiup56_repository import (
        Aiup56Repository,
    )
    limit = max(1, min(int(limit or 100), 500))
    proposals = await Aiup56Repository(
    ).list_proposals(status=status, limit=limit)
    return {
        "success": True, "total": len(proposals),
        "proposals": proposals,
        "note": "提案列表(九态状态机: draft→planned"
                "→coded→tested→audited→approved"
                "→delivered→rolled_back/archived)",
    }


@router.get("/proposal/{proposal_id}")
async def get_proposal(
        proposal_id: int,
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """提案详情(信号快照+摘要+预算——观测面)"""
    _require_admin(x_role)
    from repositories.aiup56_repository import (
        Aiup56Repository,
    )
    repo = Aiup56Repository()
    proposal = await repo.get_proposal(
        int(proposal_id))
    if proposal is None:
        raise HTTPException(
            status_code=404,
            detail=f"提案 {proposal_id} 不存在")
    events = await repo.list_events(
        proposal_id=int(proposal_id))
    return {
        "success": True, "proposal": proposal,
        "events": events,
        "note": "提案详情+全链事件(signal_scan/"
                "proposal_create...)",
    }


@router.get("/model/status")
async def model_status(
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """模型状态(champion/challenger/八因子——44号
    get_weights_view 复用; 观测面)"""
    _require_admin(x_role)
    from services.aiup56_service import Aiup56Service
    return await Aiup56Service().model_status()


@router.post("/proposals/{proposal_id}/plan")
async def plan_proposal(
        proposal_id: int,
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """规划Agent(任务拆解+依赖分析+信值预估+
    回滚预案框架——mock/real 三态; off 409)"""
    _require_admin(x_role)
    from services.aiup56_plan_service import (
        Aiup56PlanService,
    )
    try:
        return await Aiup56PlanService().plan(
            int(proposal_id))
    except KeyError as exc:
        raise HTTPException(status_code=404,
                            detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=409,
                            detail=str(exc))


@router.get("/proposals/{proposal_id}/tasks")
async def list_tasks(
        proposal_id: int,
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """任务列表(规划产出——观测面)"""
    _require_admin(x_role)
    from services.aiup56_plan_service import (
        Aiup56PlanService,
    )
    try:
        return await Aiup56PlanService().list_tasks(
            int(proposal_id))
    except KeyError as exc:
        raise HTTPException(status_code=404,
                            detail=str(exc))


@router.post("/proposals/{proposal_id}/code")
async def code_proposal(
        proposal_id: int,
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """编码Agent(代码草稿+测试计划+VALUE_REASON
    注释+资产版本化; off 409)"""
    _require_admin(x_role)
    from services.aiup56_code_service import (
        Aiup56CodeService,
    )
    try:
        return await Aiup56CodeService().code(
            int(proposal_id))
    except KeyError as exc:
        raise HTTPException(status_code=404,
                            detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=409,
                            detail=str(exc))


@router.get("/proposals/{proposal_id}/assets")
async def list_assets(
        proposal_id: int,
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """资产列表(版本化资产包——观测面)"""
    _require_admin(x_role)
    from services.aiup56_code_service import (
        Aiup56CodeService,
    )
    return await Aiup56CodeService().list_assets(
        int(proposal_id))


@router.post("/proposals/{proposal_id}/test")
async def test_proposal(
        proposal_id: int,
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """测试Agent+信值沙箱(用例矩阵+三关影子
    评估: 静态/预算/价值——超支熔断; off 409)"""
    _require_admin(x_role)
    from services.aiup56_test_service import (
        Aiup56TestService,
    )
    try:
        return await Aiup56TestService().test(
            int(proposal_id))
    except KeyError as exc:
        raise HTTPException(status_code=404,
                            detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=409,
                            detail=str(exc))


@router.get("/proposals/{proposal_id}/sandboxes")
async def list_sandboxes(
        proposal_id: int,
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """沙箱评估列表(三关留痕——观测面)"""
    _require_admin(x_role)
    from services.aiup56_test_service import (
        Aiup56TestService,
    )
    return await Aiup56TestService(
    ).list_sandboxes(int(proposal_id))


@router.post("/proposals/{proposal_id}/audit")
async def audit_proposal(
        proposal_id: int,
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """审计Agent(合规三重校验: 代码/逻辑/文档层+
    一票否决+LLM 归因报告; off 409)"""
    _require_admin(x_role)
    from services.aiup56_audit_service import (
        Aiup56AuditService,
    )
    try:
        return await Aiup56AuditService().audit(
            int(proposal_id))
    except KeyError as exc:
        raise HTTPException(status_code=404,
                            detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=409,
                            detail=str(exc))


@router.get("/proposals/{proposal_id}/panel")
async def review_panel(
        proposal_id: int,
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """审批面板视图(信号/沙箱/审计报告/确认
    清单——观测面)"""
    _require_admin(x_role)
    from services.aiup56_review_service import (
        Aiup56ReviewService,
    )
    try:
        return await Aiup56ReviewService().panel(
            int(proposal_id))
    except KeyError as exc:
        raise HTTPException(status_code=404,
                            detail=str(exc))


@router.post("/proposals/{proposal_id}/review")
async def review_proposal(
        proposal_id: int,
        body: dict,
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """人类审批(终审人工铁律——强制确认清单+
    escalate 双人复核; 不受开关影响)"""
    _require_admin(x_role)
    from services.aiup56_review_service import (
        Aiup56ReviewService,
    )
    try:
        return await Aiup56ReviewService().review(
            int(proposal_id),
            reviewer=str(body.get("reviewer") or ""),
            approved=bool(body.get("approved")),
            confirmations=body.get("confirmations")
            or [],
            note=str(body.get("note") or ""),
            second_reviewer=str(
                body.get("secondReviewer") or ""))
    except KeyError as exc:
        raise HTTPException(status_code=404,
                            detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=409,
                            detail=str(exc))


@router.post("/proposals/{proposal_id}/deliver")
async def deliver_proposal(
        proposal_id: int,
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """资产包交付(versioned 出口——人工下载后走
    既有 CI; 无审批不可交付铁律)"""
    _require_admin(x_role)
    from services.aiup56_deliver_service import (
        Aiup56DeliverService,
    )
    try:
        return await Aiup56DeliverService().deliver(
            int(proposal_id))
    except KeyError as exc:
        raise HTTPException(status_code=404,
                            detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=409,
                            detail=str(exc))


@router.post("/proposals/{proposal_id}/rollback")
async def rollback_proposal(
        proposal_id: int,
        body: dict,
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """语义回滚(预案分步执行留痕+45号 L2 受影响
    用户信值补偿)"""
    _require_admin(x_role)
    from services.aiup56_deliver_service import (
        Aiup56DeliverService,
    )
    try:
        return await Aiup56DeliverService().rollback(
            int(proposal_id),
            reason=str(body.get("reason") or ""),
            affected_members=body.get(
                "affectedMembers") or [])
    except KeyError as exc:
        raise HTTPException(status_code=404,
                            detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=409,
                            detail=str(exc))


@router.post("/feedback/collect")
async def feedback_collect(
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """决策回流补标(七类信号真值+44号池双写
    ——幂等 proposalId 1:1; 不受开关影响)"""
    _require_admin(x_role)
    from services.aiup56_feedback_service import (
        Aiup56FeedbackService,
    )
    try:
        return await Aiup56FeedbackService(
        ).collect_feedback()
    except ValueError as exc:
        raise HTTPException(status_code=409,
                            detail=str(exc))


@router.get("/feedback/stats")
async def feedback_stats(
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """回流统计(信号分布/池双写——观测面)"""
    _require_admin(x_role)
    from services.aiup56_feedback_service import (
        Aiup56FeedbackService,
    )
    return await Aiup56FeedbackService(
    ).feedback_stats()


@router.get("/dashboard")
async def dashboard(
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """四区看板(提案漏斗/资产产出/审计合规/
    回滚防御——观测面, P5)"""
    _require_admin(x_role)
    from services.aiup56_dashboard_service import (
        Aiup56DashboardService,
    )
    return await Aiup56DashboardService().build()


@router.post("/redteam")
async def redteam(
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """红队六向量验证(提案投毒/预算耗尽/审批绕过/
    资产注入/信号伪造/回滚破坏; P5)"""
    _require_admin(x_role)
    from services.aiup56_redteam_service import (
        Aiup56RedteamService,
    )
    try:
        return await Aiup56RedteamService().run_all()
    except ValueError as exc:
        raise HTTPException(status_code=409,
                            detail=str(exc))


def register_aiup56_routes(app) -> None:
    """注册56号路由(main.py startup 调用)"""
    app.include_router(router)
