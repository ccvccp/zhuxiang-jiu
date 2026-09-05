"""51号·小竹可信知识图谱路由(P0 本体奠基 + P1 采集管道 + P2 查询面)

端点(P0 共 4 + P1 共 5 + P2 共 2):
    GET  /api/kg51/schema                    本体注册表视图(admin)
    GET  /api/kg51/schema/changes            变更队列/历史(admin)
    POST /api/kg51/schema/changes            提交本体变更申请(admin)
    POST /api/kg51/schema/changes/{id}/decide  变更裁决(admin)
    POST /api/kg51/ingest/run                触发三源采集(P1, admin)
    GET  /api/kg51/ingest/status             采集状态视图(P1, admin)
    GET  /api/kg51/triples                   三元组查询(P1, admin)
    GET  /api/kg51/reviews                   复核队列(P1, admin)
    POST /api/kg51/reviews/{id}/decide       复核裁决(P1, admin)
    GET  /api/kg51/query                     邻域查询(P2, 会员面+admin)
    GET  /api/kg51/grounding                 问答锚定检索(P2, 公开面)

鉴权: 管理端 X-Role: admin(43-50号同款口径);
      会员面 X-Member-Id(48号惯例); 公开面无鉴权。
统一口径:
    - 治理面端点不受 KG_MODE 数据面开关影响
      (off 态亦可管理本体)
    - 采集面 off=采集停(ingest/run 拒绝);
      查询面 off=空态降级(fail-soft 直通);
      观测面(triples/reviews/status)不受影响
    - 模块纯增量(零既有路由改动)
    - KeyError → 404 / ValueError → 409(44-50号同款)
"""

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel

from services.kg51_ingest_service import (
    Kg51IngestService, Kg51ReviewService,
)
from services.kg51_query_service import Kg51QueryService
from services.kg51_schema_service import Kg51SchemaService

router = APIRouter(prefix="/api/kg51",
                   tags=["小竹可信知识图谱(51号)"])


def _require_admin(x_role: str | None) -> str:
    if not x_role or x_role != "admin":
        raise HTTPException(status_code=403,
                            detail="需要 X-Role: admin")
    return x_role


def _handle(exc: Exception):
    if isinstance(exc, KeyError):
        raise HTTPException(status_code=404,
                            detail=str(exc))
    if isinstance(exc, ValueError):
        raise HTTPException(status_code=409,
                            detail=str(exc))
    raise exc


class ChangeIn(BaseModel):
    kind: str
    target: str
    payload: dict = {}
    reason: str


class DecideIn(BaseModel):
    approve: bool
    reviewNote: str = ""


@router.get("/schema")
async def get_schema(
        x_role: str | None = Header(default=None,
                                     alias="X-Role")):
    """本体注册表视图(9 实体/9 关系/敏感度/覆盖报告)"""
    _require_admin(x_role)
    return Kg51SchemaService().view()


@router.get("/schema/changes")
async def list_changes(
        status: str = None,
        x_role: str | None = Header(default=None,
                                     alias="X-Role")):
    """变更审批队列/历史(byStatus 统计)"""
    _require_admin(x_role)
    return await Kg51SchemaService().list_changes(
        status=status)


@router.post("/schema/changes")
async def submit_change(
        body: ChangeIn,
        x_role: str | None = Header(default=None,
                                     alias="X-Role")):
    """提交本体变更申请(pending——审批总线留痕)"""
    _require_admin(x_role)
    try:
        return await Kg51SchemaService().submit_change(
            kind=body.kind, target=body.target,
            payload=body.payload, reason=body.reason)
    except ValueError as exc:
        raise HTTPException(status_code=409,
                            detail=str(exc))


@router.post("/schema/changes/{change_id}/decide")
async def decide_change(
        change_id: int,
        body: DecideIn,
        x_role: str | None = Header(default=None,
                                     alias="X-Role")):
    """变更裁决(approve→approved 留痕; 驳回→rejected)"""
    _require_admin(x_role)
    try:
        return await Kg51SchemaService().decide_change(
            change_id=change_id, approve=body.approve,
            review_note=body.reviewNote)
    except KeyError as exc:
        raise HTTPException(status_code=404,
                            detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=409,
                            detail=str(exc))


class IngestIn(BaseModel):
    sources: list[str] | None = None


class ReviewDecideIn(BaseModel):
    approve: bool
    decisionNote: str = ""


@router.post("/ingest/run")
async def run_ingest(
        body: IngestIn | None = None,
        x_role: str | None = Header(default=None,
                                     alias="X-Role")):
    """触发三源采集抽取(P1——KG_MODE=on 时可用,
    off=采集停铁律)"""
    _require_admin(x_role)
    sources = (body.sources if body
               and body.sources else None)
    try:
        return await Kg51IngestService().run_ingest(
            sources=sources)
    except ValueError as exc:
        raise HTTPException(status_code=409,
                            detail=str(exc))


@router.get("/ingest/status")
async def ingest_status(
        x_role: str | None = Header(default=None,
                                     alias="X-Role")):
    """采集状态视图(实时统计——观测不受 off 影响)"""
    _require_admin(x_role)
    return await Kg51IngestService().status()


@router.get("/triples")
async def query_triples(
        status: str = None,
        predicate: str = None,
        sourceType: str = None,
        subject: str = None,
        x_role: str | None = Header(default=None,
                                     alias="X-Role")):
    """三元组查询(计分路径只取 verified——
    unverified 物理隔离)"""
    _require_admin(x_role)
    return await Kg51ReviewService().query_triples(
        status=status, predicate=predicate,
        source_type=sourceType, subject=subject)


@router.get("/reviews")
async def list_reviews(
        status: str = None,
        reason: str = None,
        x_role: str | None = Header(default=None,
                                     alias="X-Role")):
    """复核队列/历史(confidence|conflict|feedback)"""
    _require_admin(x_role)
    return await Kg51ReviewService().list_reviews(
        status=status, reason=reason)


@router.post("/reviews/{review_id}/decide")
async def decide_review(
        review_id: int,
        body: ReviewDecideIn,
        x_role: str | None = Header(default=None,
                                     alias="X-Role")):
    """复核裁决(approve→verified/active;
    reject→retired 留痕)"""
    _require_admin(x_role)
    try:
        return await Kg51ReviewService().decide_review(
            review_id=review_id, approve=body.approve,
            decision_note=body.decisionNote)
    except KeyError as exc:
        raise HTTPException(status_code=404,
                            detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=409,
                            detail=str(exc))


@router.get("/query")
async def neighborhood_query(
        subject: str,
        depth: int = 1,
        x_role: str | None = Header(default=None,
                                     alias="X-Role"),
        x_member_id: str | None = Header(
            default=None, alias="X-Member-Id")):
    """邻域查询(P2——会员面: 自身 digest+L0, 预算感知;
    admin: 任意主体不过滤; off 态空态降级)

    权限: admin 任意; member 仅自身 digest 主体或
    L0 公开实体(他人主体 → 409 越权语义)。
    预算: 返回实体类型去重合计隐私成本(L0=0;
    无结果零消耗; admin 不扣)。
    """
    admin = bool(x_role and x_role == "admin")
    member_id = None
    if not admin:
        if not x_member_id:
            raise HTTPException(
                status_code=401,
                detail="需要 X-Member-Id"
                       "(或 X-Role: admin)")
        try:
            member_id = int(x_member_id)
        except (TypeError, ValueError):
            raise HTTPException(
                status_code=401,
                detail="X-Member-Id 需为整数")
    try:
        return await Kg51QueryService(
        ).neighborhood_query(
            subject=subject, member_id=member_id,
            admin=admin, depth=depth)
    except ValueError as exc:
        raise HTTPException(status_code=409,
                            detail=str(exc))


@router.get("/grounding")
async def grounding_search(keyword: str):
    """问答锚定检索(P2——公开面: L0 实体, 零成本零鉴权;
    off 态空态降级 fail-soft)"""
    try:
        return await Kg51QueryService().grounding_search(
            keyword=keyword)
    except ValueError as exc:
        raise HTTPException(status_code=409,
                            detail=str(exc))


def register_kg51_routes(app) -> None:
    """注册51号路由(main.py startup 调用)"""
    app.include_router(router)
