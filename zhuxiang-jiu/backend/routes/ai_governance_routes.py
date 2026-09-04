"""46号·AI 治理与合规中枢路由(P0 资产注册中心 + 变更审批总线
+ P1 档案健康度监控 + P2 公平性审计)

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

端点(P1, 管理面 3——X-Role: admin):
    GET  /api/ai-gov/health               全档案健康度排行+
                                         分层统计(实时)
    POST /api/ai-gov/health/scan          触发一轮巡检
                                         (落快照+生成告警)
    GET  /api/ai-gov/alerts               治理告警队列
                                         (信号/档案过滤)

端点(P2, 管理面 3——X-Role: admin):
    POST /api/ai-gov/fairness/samples     采样上报(批量,
                                         脱敏校验)
    POST /api/ai-gov/fairness/audit        触发审计
                                         (指标计算→报告落库)
    GET  /api/ai-gov/fairness/report       最近报告
                                         (分组统计+flagged+
                                          中文归因)

鉴权: 管理端 X-Role: admin(43/44/45号同款口径)

统一口径:
    - 模块纯增量(零既有路由改动; ai_learning 仅加冻结守卫)
    - 治理不阻断: fail-soft 铁律(守卫异常放行学习;
      健康巡检异常降级留痕不抛出)
    - 采样脱敏: 含个人标识字段的上报直接 409(最小必要红线)
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


@router.get("/health")
async def health_overview(
    x_role: str = Header(default="", alias="X-Role"),
):
    """全档案健康度排行 + 分层统计(实时计算, 不落库)"""
    _require_admin(x_role)
    try:
        from services.ai_governance_health import (
            AiGovernanceHealthService,
        )
        return await AiGovernanceHealthService().live_health()
    except Exception as e:
        raise _handle(e) from e


@router.post("/health/scan")
async def health_scan(
    x_role: str = Header(default="", alias="X-Role"),
):
    """触发一轮健康巡检(评估→落快照→生成告警当日去重)"""
    _require_admin(x_role)
    try:
        from services.ai_governance_health import (
            AiGovernanceHealthService,
        )
        return await AiGovernanceHealthService().scan()
    except Exception as e:
        raise _handle(e) from e


@router.get("/alerts")
async def list_alerts(
    signal: str = Query(None, description="信号过滤"
                                    "(stagnation/depletion/drift_high)"),
    scorerId: str = Query(None, description="档案过滤"),
    limit: int = Query(100, ge=1, le=500,
                       description="返回条数上限"),
    x_role: str = Header(default="", alias="X-Role"),
):
    """治理告警队列(最新在前; 信号/档案过滤)"""
    _require_admin(x_role)
    try:
        from services.ai_governance_health import (
            AiGovernanceHealthService,
        )
        return await AiGovernanceHealthService().list_alerts(
            signal=signal, scorer_id=scorerId, limit=limit)
    except Exception as e:
        raise _handle(e) from e


# ============================================================
# P2 公平性审计(采样→指标→报告)
# ============================================================

@router.post("/fairness/samples")
async def submit_fairness_samples(
    body: dict,
    x_role: str = Header(default="", alias="X-Role"),
):
    """公平性采样上报(批量, 脱敏校验)

    body: {scorerId, samples: [{group, score,
    passed?}], source?: report|trust45}
    """
    _require_admin(x_role)
    if not isinstance(body, dict):
        raise HTTPException(status_code=409, detail="请求体需为对象")
    try:
        from services.ai_governance_fairness import (
            AiGovernanceFairnessService,
        )
        return await AiGovernanceFairnessService(
        ).submit_samples(
            str(body.get("scorerId") or ""),
            body.get("samples"),
            source=str(body.get("source") or "report"))
    except Exception as e:
        raise _handle(e) from e


@router.post("/fairness/audit")
async def run_fairness_audit(
    body: dict,
    x_role: str = Header(default="", alias="X-Role"),
):
    """触发公平性审计(指标计算→报告落库)

    body: {scorerId}(空=全档案逐个审计);
    importTrust45: true 时先执行 45号事件适配器
    """
    _require_admin(x_role)
    if not isinstance(body, dict):
        raise HTTPException(status_code=409, detail="请求体需为对象")
    try:
        from services.ai_governance_fairness import (
            AiGovernanceFairnessService,
        )
        svc = AiGovernanceFairnessService()
        results = []
        if body.get("importTrust45"):
            results.append(await svc.import_trust45())
        scorer_id = str(body.get("scorerId") or "")
        if scorer_id:
            results.append(await svc.run_audit(scorer_id))
        else:
            govs = await svc.repo.list_govs(limit=1000)
            audited = 0
            for gov in govs:
                sid = gov.get("scorerId")
                if await svc.repo.count_samples(sid) > 0:
                    results.append(await svc.run_audit(sid))
                    audited += 1
            results.append({"success": True,
                            "audited": audited})
        return (results[0] if len(results) == 1
                else {"success": True, "results": results})
    except Exception as e:
        raise _handle(e) from e


@router.get("/fairness/report")
async def get_fairness_report(
    scorerId: str = Query(None, description="档案过滤"),
    history: bool = Query(False, description="返回报告历史"),
    x_role: str = Header(default="", alias="X-Role"),
):
    """最近公平性审计报告(分组统计+flagged+中文归因)"""
    _require_admin(x_role)
    try:
        from services.ai_governance_fairness import (
            AiGovernanceFairnessService,
        )
        return await AiGovernanceFairnessService(
        ).get_report(scorer_id=scorerId, history=history)
    except Exception as e:
        raise _handle(e) from e


def register_ai_governance_routes(app) -> None:
    """注册46号路由(main.py startup 调用)"""
    app.include_router(router)
