"""51号·小竹可信知识图谱路由(P0 本体奠基)

端点(P0 共 4):
    GET  /api/kg51/schema                    本体注册表视图(admin)
    GET  /api/kg51/schema/changes            变更队列/历史(admin)
    POST /api/kg51/schema/changes            提交本体变更申请(admin)
    POST /api/kg51/schema/changes/{id}/decide  变更裁决(admin)

鉴权: 管理端 X-Role: admin(43-50号同款口径)。
统一口径:
    - 治理面端点不受 KG_MODE 数据面开关影响
      (off 态亦可管理本体)
    - 模块纯增量(零既有路由改动)
    - KeyError → 404 / ValueError → 409(44-50号同款)
"""

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel

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


def register_kg51_routes(app) -> None:
    """注册51号路由(main.py startup 调用)"""
    app.include_router(router)
