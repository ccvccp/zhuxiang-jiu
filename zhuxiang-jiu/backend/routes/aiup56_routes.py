"""56号·AI智能升级管理路由(P0)

端点(P0 5):
    GET  /api/aiup56/registry          注册表自描述(admin, 观测面)
    POST /api/aiup56/signals/scan      信号采集+决策评估(admin, 管理)
    GET  /api/aiup56/proposals         提案列表(admin, 观测面)
    GET  /api/aiup56/proposal/{id}     提案详情(admin, 观测面)
    GET  /api/aiup56/model/status      模型状态(admin, 观测面)

鉴权: 管理面 X-Role: admin(43-55号同款口径)。
统一口径:
    - 观测面(registry/proposals/proposal/model/
      status)不受 AIUP56_MODE 影响
    - 决策面(signals/scan): off=拒绝(409——
      shadow/assist 开放)
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


def register_aiup56_routes(app) -> None:
    """注册56号路由(main.py startup 调用)"""
    app.include_router(router)
