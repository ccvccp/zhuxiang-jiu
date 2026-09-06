"""57号·AI智能知识库路由(P0-P2)

端点(P0 6 + P1 3 + P2 5 = 14):
    GET  /api/kb57/registry           注册表自描述(admin, 观测面)
    GET  /api/kb57/sources            采集源注册表(admin, 观测面)
    POST /api/kb57/sources/register   采集源注册(admin, 管理)
    POST /api/kb57/gaps/scan          缺口诊断+决策评估(admin, 管理)
    GET  /api/kb57/gaps               缺口清单(admin, 观测面)
    GET  /api/kb57/model/status       模型状态(admin, 观测面)
    POST /api/kb57/collect/run        定向采集运行(admin, 管理, P1)
    POST /api/kb57/resources/{id}/compliance 三重合规鉴别(admin, 管理, P1)
    GET  /api/kb57/compliance/{id}    合规鉴别报告(admin, 观测面, P1)
    POST /api/kb57/seeds/craft         种子锻造(admin, 管理, P2)
    GET  /api/kb57/seeds              种子列表(admin, 观测面, P2)
    GET  /api/kb57/seeds/{id}         种子详情(admin, 观测面, P2)
    POST /api/kb57/seeds/{id}/review   种子发布终审(admin, 终审, P2)
    POST /api/kb57/seeds/{id}/recall   种子紧急召回(admin, 管理, P2)

鉴权: 管理面 X-Role: admin(43-56号同款口径)。
统一口径:
    - 观测面(registry/sources/gaps/model/status/
      compliance/seeds)不受 KB57_MODE 影响
    - 决策面(gaps/scan+sources/register+collect/run
      +resources/{id}/compliance+seeds/craft):
      off=拒绝(409——shadow/assist 开放)
    - review/recall(发布链人工动作):
      不受开关影响(终审人工铁律)
    - KeyError → 404 / ValueError → 409
"""

from fastapi import APIRouter, Header, HTTPException

router = APIRouter(prefix="/api/kb57",
                   tags=["AI智能知识库(57号)"])


def _require_admin(x_role: str | None) -> str:
    if not x_role or x_role != "admin":
        raise HTTPException(status_code=403,
                            detail="需要 X-Role: admin")


@router.get("/registry")
async def registry(
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """注册表自描述(缺口信号+采集源+评分器——观测面)"""
    _require_admin(x_role)
    from services.kb57_service import Kb57Service
    return Kb57Service.registry()


@router.get("/sources")
async def sources(
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """采集源注册表(内置白名单+admin 动态注册域
    ——观测面)"""
    _require_admin(x_role)
    from services.kb57_registry import sources_view
    view = sources_view()
    # 动态注册域合并
    from repositories.kb57_repository import (
        Kb57Repository,
    )
    dynamic = await Kb57Repository().list_sources(
        limit=100)
    view["dynamicSources"] = dynamic
    view["dynamicTotal"] = len(dynamic)
    return view


@router.post("/sources/register")
async def register_source(
        body: dict,
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """采集源注册(admin 动态白名单扩展——可信源
    封闭域, 注册后方可在 P1 版权关通过)"""
    _require_admin(x_role)
    from services.kb57_service import (
        require_active_mode,
    )
    try:
        require_active_mode()
    except ValueError as exc:
        raise HTTPException(status_code=409,
                            detail=str(exc))

    from services.kb57_registry import (
        SOURCE_TYPES, CREDIBILITY_REVIEW_LINE,
    )
    source_key = str(
        body.get("sourceKey") or "").strip()
    label = str(body.get("label") or "").strip()
    source_type = str(
        body.get("sourceType") or "").strip()
    credibility = body.get("credibility")
    license_ = str(body.get("license") or "").strip()

    if not source_key or len(source_key) > 64:
        raise HTTPException(
            status_code=422, detail="sourceKey 必填(1-64 字符)")
    if not label or len(label) > 64:
        raise HTTPException(
            status_code=422, detail="label 必填(1-64 字符)")
    if source_type not in SOURCE_TYPES:
        raise HTTPException(
            status_code=422,
            detail=f"非法源类型(合法值: "
                   f"{list(SOURCE_TYPES)})")
    try:
        credibility = float(credibility or 0)
    except (TypeError, ValueError):
        raise HTTPException(
            status_code=422, detail="credibility 须为数值")
    if not 0 <= credibility <= 1:
        raise HTTPException(
            status_code=422,
            detail="credibility 须在 [0,1]")
    if not license_:
        raise HTTPException(
            status_code=422, detail="license(授权协议)必填")

    from repositories.kb57_repository import (
        Kb57Repository,
    )
    repo = Kb57Repository()
    # 重复 sourceKey 拒绝(封闭域幂等)
    existing = await repo.list_sources(limit=1000)
    if any(s.get("sourceKey") == source_key
           for s in existing):
        raise HTTPException(
            status_code=409,
            detail=f"采集源 {source_key} 已注册"
                   f"(白名单封闭域——重复注册拒绝)")

    source_id = await repo.next_source_id()
    record = {
        "sourceId": source_id,
        "sourceKey": source_key,
        "label": label,
        "sourceType": source_type,
        "credibility": round(credibility, 4),
        "license": license_,
        "reviewRequired":
            credibility < CREDIBILITY_REVIEW_LINE,
        "status": "active",
        "createdAt": (await _now()),
    }
    await repo.save_source(record)

    # 留痕
    event_id = await repo.next_event_id()
    await repo.add_event({
        "eventId": event_id,
        "gapId": 0,
        "eventType": "source_register",
        "detail": {
            "sourceKey": source_key,
            "sourceType": source_type,
            "credibility": round(credibility, 4),
            "reviewRequired": record["reviewRequired"],
        },
        "createdAt": record["createdAt"],
    })
    return {
        "success": True,
        "sourceId": source_id,
        "sourceKey": source_key,
        "reviewRequired": record["reviewRequired"],
        "note": "采集源已注册(动态白名单)——P1 版权关"
                "白名单校验域(可信度<0.75 强制人工复审)",
    }


async def _now() -> str:
    from core.helpers import ts
    return ts()


@router.post("/gaps/scan")
async def scan_gaps(
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """缺口诊断+决策评估(信号纯读取→八因子评分
    →三级决策 defer/collect/urgent)"""
    _require_admin(x_role)
    from services.kb57_service import Kb57Service
    try:
        return await Kb57Service().diagnose_and_plan()
    except ValueError as exc:
        raise HTTPException(status_code=409,
                            detail=str(exc))


@router.get("/gaps")
async def list_gaps(
        status: str = None,
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """缺口清单(《知识补全优先级清单》——观测面)"""
    _require_admin(x_role)
    from services.kb57_service import Kb57Service
    return await Kb57Service().list_gaps(
        status=status)


@router.get("/model/status")
async def model_status(
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """模型状态(第32档案 champion/challenger/八因子
    ——44号复用观测面)"""
    _require_admin(x_role)
    from services.kb57_service import Kb57Service
    return await Kb57Service().model_status()


@router.post("/collect/run")
async def collect_run(
        body: dict = None,
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """定向采集运行(open 缺口→源白名单资源落库
    quarantined 沙箱隔离态; P1)"""
    _require_admin(x_role)
    from services.kb57_collect_service import (
        Kb57CollectService,
    )
    gap_id = None
    if isinstance(body, dict):
        raw = body.get("gapId")
        if raw is not None:
            try:
                gap_id = int(raw)
            except (TypeError, ValueError):
                raise HTTPException(
                    status_code=422,
                    detail="gapId 须为整数")
    try:
        return await Kb57CollectService().run_collect(
            gap_id=gap_id)
    except KeyError as exc:
        raise HTTPException(status_code=404,
                            detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=409,
                            detail=str(exc))


@router.post("/resources/{resource_id}/compliance")
async def resource_compliance(
        resource_id: int,
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """三重合规鉴别(版权/隐私/内容安全串行+
    合规指纹生成; P1)"""
    _require_admin(x_role)
    from services.kb57_compliance_service import (
        Kb57ComplianceService,
    )
    try:
        return await (
            Kb57ComplianceService()
            .run_compliance(int(resource_id)))
    except KeyError as exc:
        raise HTTPException(status_code=404,
                            detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=409,
                            detail=str(exc))


@router.get("/compliance/{compliance_id}")
async def compliance_detail(
        compliance_id: int,
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """合规鉴别报告详情(三关明细+合规指纹——观测面, P1)"""
    _require_admin(x_role)
    from services.kb57_compliance_service import (
        Kb57ComplianceService,
    )
    try:
        return await (
            Kb57ComplianceService()
            .get_compliance(int(compliance_id)))
    except KeyError as exc:
        raise HTTPException(status_code=404,
                            detail=str(exc))


@router.post("/seeds/craft")
async def seeds_craft(
        body: dict,
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """种子锻造(compliant 资源→结构化认知种子
    sandbox 态——无指纹不入库铁律; P2)"""
    _require_admin(x_role)
    from services.kb57_seed_service import (
        Kb57SeedService,
    )
    try:
        return await Kb57SeedService().craft(
            gap_id=int(body.get("gapId") or 0),
            resource_id=int(
                body.get("resourceId") or 0),
            seed_type=str(
                body.get("type") or "text"),
            value_tags=body.get("valueTags") or [])
    except KeyError as exc:
        raise HTTPException(status_code=404,
                            detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=409,
                            detail=str(exc))


@router.get("/seeds")
async def seeds_list(
        status: str = None,
        seed_type: str = None,
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """种子列表(版本化+八态状态机——观测面, P2)"""
    _require_admin(x_role)
    from services.kb57_seed_service import (
        Kb57SeedService,
    )
    return await Kb57SeedService().list_seeds(
        status=status, seed_type=seed_type)


@router.get("/seeds/{seed_id}")
async def seeds_detail(
        seed_id: int,
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """种子详情(合规指纹+KNOWLEDGE_REASON+多模态
    content——观测面, P2)"""
    _require_admin(x_role)
    from services.kb57_seed_service import (
        Kb57SeedService,
    )
    try:
        return await Kb57SeedService().get_seed(
            int(seed_id))
    except KeyError as exc:
        raise HTTPException(status_code=404,
                            detail=str(exc))


@router.post("/seeds/{seed_id}/review")
async def seeds_review(
        seed_id: int,
        body: dict,
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """种子发布终审(published 唯一出口——
    终审人工铁律不受开关影响; P2)"""
    _require_admin(x_role)
    from services.kb57_review_service import (
        Kb57ReviewService,
    )
    try:
        return await Kb57ReviewService().review(
            int(seed_id),
            reviewer=str(
                body.get("reviewer") or ""),
            approved=bool(body.get("approved")),
            note=str(body.get("note") or ""))
    except KeyError as exc:
        raise HTTPException(status_code=404,
                            detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=409,
                            detail=str(exc))


@router.post("/seeds/{seed_id}/recall")
async def seeds_recall(
        seed_id: int,
        body: dict,
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """种子紧急召回(误导/过期下架——
    recalled+受影响用户补偿接口; P2)"""
    _require_admin(x_role)
    from services.kb57_review_service import (
        Kb57ReviewService,
    )
    try:
        return await Kb57ReviewService().recall(
            int(seed_id),
            reason=str(body.get("reason") or ""),
            affected_members=body.get(
                "affectedMembers") or [])
    except KeyError as exc:
        raise HTTPException(status_code=404,
                            detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=409,
                            detail=str(exc))


def register_kb57_routes(app) -> None:
    """注册57号路由(main.py startup 调用)"""
    app.include_router(router)
