"""57号·AI智能知识库路由(P0)

端点(P0 5):
    GET  /api/kb57/registry           注册表自描述(admin, 观测面)
    GET  /api/kb57/sources            采集源注册表(admin, 观测面)
    POST /api/kb57/sources/register   采集源注册(admin, 管理)
    POST /api/kb57/gaps/scan          缺口诊断+决策评估(admin, 管理)
    GET  /api/kb57/gaps               缺口清单(admin, 观测面)
    GET  /api/kb57/model/status       模型状态(admin, 观测面)

鉴权: 管理面 X-Role: admin(43-56号同款口径)。
统一口径:
    - 观测面(registry/sources/gaps/model/status)
      不受 KB57_MODE 影响
    - 决策面(gaps/scan+sources/register):
      off=拒绝(409——shadow/assist 开放)
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


def register_kb57_routes(app) -> None:
    """注册57号路由(main.py startup 调用)"""
    app.include_router(router)
