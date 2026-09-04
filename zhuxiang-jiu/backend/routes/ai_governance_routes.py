"""46号·AI 治理与合规中枢路由(P0 资产注册中心 + 变更审批总线)

端点(P0, 管理面 5——X-Role: admin):
    GET  /api/ai-gov/registry             治理台账(状态/batch
                                         过滤+分布统计)
    POST /api/ai-gov/registry/sync        手动重扫(SCORER_
                                         REGISTRY → 台账 diff)
    POST /api/ai-gov/changes              提交变更申请
                                         (pending 不直接生效)
    GET  /api/ai-gov/changes              审批队列/历史
    POST /api/ai-gov/changes/{id}/review  人工审批
                                         (approved→执行器/
                                          rejected→留痕)

鉴权: 管理端 X-Role: admin(43/44/45号同款口径)

统一口径:
    - 模块纯增量(零既有路由改动; ai_learning 仅加冻结守卫)
    - 治理不阻断: fail-soft 铁律(守卫异常放行学习)
    - KeyError → 404 / ValueError → 409(44/45号同款)
"""

from fastapi import APIRouter, Header, HTTPException, Query

router = APIRouter(prefix="/api/ai-gov",
                   tags=["AI治理中枢(46号)"])


def _require_admin(x_role: str | None):
    if x_role != "admin":
        raise HTTPException(status_code=403, detail="需要管理员权限")


def _handle(exc: Exception):
    """统一异常映射(43/44/45号同款)"""
    if isinstance(exc, KeyError):
        msg = str(exc) if str(exc) else "资源不存在"
        if msg.startswith("'") and msg.endswith("'"):
            msg = msg[1:-1]
        raise HTTPException(status_code=404, detail=msg)
    if isinstance(exc, ValueError):
        raise HTTPException(status_code=409, detail=str(exc))
    raise HTTPException(status_code=500, detail=str(exc))


@router.get("/registry")
async def list_registry(
    status: str = Query(None, description="状态过滤"
                                    "(active/frozen/retired)"),
    batch: int = Query(None, description="批次过滤(1-12)"),
    x_role: str = Header(default="", alias="X-Role"),
):
    """AI 资产治理台账(28 档案 + 状态/batch 分布)"""
    _require_admin(x_role)
    try:
        from services.ai_governance_service import (
            AiGovernanceService,
        )
        return await AiGovernanceService().list_registry(
            status=status, batch=batch)
    except Exception as e:
        raise _handle(e) from e


@router.post("/registry/sync")
async def sync_registry(
    x_role: str = Header(default="", alias="X-Role"),
):
    """手动重扫(SCORER_REGISTRY → 台账 upsert 幂等 + diff)"""
    _require_admin(x_role)
    try:
        from services.ai_governance_service import (
            AiGovernanceService,
        )
        return await AiGovernanceService().sync_registry()
    except Exception as e:
        raise _handle(e) from e


@router.post("/changes")
async def submit_change(
    body: dict,
    x_role: str = Header(default="", alias="X-Role"),
):
    """提交变更申请(pending——审批通过后才生效)

    body: {scorerId, kind: promote|patch|config|freeze|
    unfreeze, payload{before, after}?, reason}
    """
    _require_admin(x_role)
    if not isinstance(body, dict):
        raise HTTPException(status_code=409, detail="请求体需为对象")
    try:
        from services.ai_governance_service import (
            AiGovernanceService,
        )
        return await AiGovernanceService().submit_change(
            str(body.get("scorerId") or ""),
            str(body.get("kind") or ""),
            body.get("payload") or {},
            str(body.get("reason") or ""),
            requested_by=str(body.get("requestedBy")
                             or "admin"))
    except (TypeError, ValueError) as e:
        raise _handle(e) from e
    except Exception as e:
        raise _handle(e) from e


@router.get("/changes")
async def list_changes(
    status: str = Query(None, description="状态过滤"
                                    "(pending/approved/rejected)"),
    scorerId: str = Query(None, description="档案过滤"),
    x_role: str = Header(default="", alias="X-Role"),
):
    """审批队列/历史(最新在前)"""
    _require_admin(x_role)
    try:
        from services.ai_governance_service import (
            AiGovernanceService,
        )
        return await AiGovernanceService().list_changes(
            status=status, scorer_id=scorerId)
    except Exception as e:
        raise _handle(e) from e


@router.post("/changes/{change_id}/review")
async def review_change(
    change_id: int,
    body: dict,
    x_role: str = Header(default="", alias="X-Role"),
):
    """人工审批(approved→执行器生效 / rejected→留痕)

    body: {approve: bool, reviewNote?, reviewedBy?}
    """
    _require_admin(x_role)
    if not isinstance(body, dict) or \
            "approve" not in body:
        raise HTTPException(status_code=409,
                            detail="请求体需含 approve 字段")
    try:
        from services.ai_governance_service import (
            AiGovernanceService,
        )
        return await AiGovernanceService().review_change(
            change_id, bool(body.get("approve")),
            reviewed_by=str(body.get("reviewedBy") or "admin"),
            review_note=str(body.get("reviewNote") or ""))
    except Exception as e:
        raise _handle(e) from e


def register_ai_governance_routes(app) -> None:
    """注册46号路由(main.py startup 调用)"""
    app.include_router(router)
