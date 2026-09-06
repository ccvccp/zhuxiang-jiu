"""56号·AI智能升级管理路由(P0-P2)

端点(P0 5 + P1 4 + P2 2 = 11):
    GET  /api/aiup56/registry            注册表自描述(admin, 观测面)
    POST /api/aiup56/signals/scan        信号采集+决策评估(admin, 管理)
    GET  /api/aiup56/proposals           提案列表(admin, 观测面)
    GET  /api/aiup56/proposal/{id}       提案详情(admin, 观测面)
    GET  /api/aiup56/model/status        模型状态(admin, 观测面)
    POST /api/aiup56/proposals/{id}/plan 规划Agent(admin, P1)
    GET  /api/aiup56/proposals/{id}/tasks 任务列表(admin, 观测面, P1)
    POST /api/aiup56/proposals/{id}/code  编码Agent(admin, P1)
    GET  /api/aiup56/proposals/{id}/assets 资产列表(admin, 观测面, P1)
    POST /api/aiup56/proposals/{id}/test  测试Agent+信值沙箱(admin, P2)
    GET  /api/aiup56/proposals/{id}/sandboxes 沙箱列表(admin, 观测面, P2)

鉴权: 管理面 X-Role: admin(43-55号同款口径)。
统一口径:
    - 观测面(registry/proposals/proposal/model/
      status/tasks/assets/sandboxes)不受
      AIUP56_MODE 影响
    - 决策面(signals/scan+plan+code+test):
      off=拒绝(409——shadow/assist 开放)
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


def register_aiup56_routes(app) -> None:
    """注册56号路由(main.py startup 调用)"""
    app.include_router(router)
