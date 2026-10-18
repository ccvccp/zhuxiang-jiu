"""超级会员智能库 路由(mtier_routes)

运营面(admin): 分层总览 / 名单筛选 / 手动打标 / 迁移留痕 /
手动全量评估。画像出口零明文 PII(phoneMasked)。
C 端本人视图留作后续批次(需会员侧鉴权口径对齐)。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Header, HTTPException, Query
from pydantic import BaseModel

from services.mtier_service import MtierService

router = APIRouter(prefix="/api/mtier", tags=["超级会员智能库"])

_svc = MtierService()


def _require_admin(x_role: str | None) -> None:
    if (x_role or "").strip().lower() != "admin":
        raise HTTPException(status_code=403, detail="admin only")


class TagReq(BaseModel):
    key: str
    value: str


@router.get("/summary")
async def mtier_summary(
    x_role: Annotated[str | None, Header(alias="X-Role")] = None,
):
    """分层总览(admin): tier/lifecycle 分布 + 画像总数 + 最近评估"""
    _require_admin(x_role)
    from services.mtier_scheduler import last_run
    return {"code": 0, "data": {"summary": await _svc.summary(),
                                "lastEvaluate": last_run()}}


@router.get("/members")
async def mtier_members(
    tier: str = Query("", description="筛选 silver/gold/black/none"),
    lifecycle: str = Query("",
        description="筛选 new/active/repeat/dormant/churn_risk/won_back"),
    limit: int = Query(100, ge=1, le=500),
    x_role: Annotated[str | None, Header(alias="X-Role")] = None,
):
    """会员名单(admin, 按累计实付倒序; 出口零明文 PII)"""
    _require_admin(x_role)
    return {"code": 0, "data": await _svc.members(
        tier=tier, lifecycle=lifecycle, limit=limit)}


@router.get("/members/{member_id}")
async def mtier_profile(
    member_id: int,
    x_role: Annotated[str | None, Header(alias="X-Role")] = None,
):
    """单会员画像(admin): tier/lifecycle/标签/消费快照"""
    _require_admin(x_role)
    try:
        return {"code": 0, "data": await _svc.get_profile(member_id)}
    except KeyError as e:
        raise HTTPException(
            status_code=404, detail=str(e)) from None


@router.post("/members/{member_id}/tags")
async def mtier_set_tag(
    member_id: int,
    req: TagReq,
    x_role: Annotated[str | None, Header(alias="X-Role")] = None,
):
    """运营手动打标(manual 源; 自动刷新永不覆盖)"""
    _require_admin(x_role)
    try:
        return {"code": 0, "data": await _svc.set_tag(
            member_id, req.key, req.value)}
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e)) from None
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from None


@router.get("/logs")
async def mtier_logs(
    limit: int = Query(100, ge=1, le=500),
    x_role: Annotated[str | None, Header(alias="X-Role")] = None,
):
    """生命周期迁移留痕(admin)"""
    _require_admin(x_role)
    return {"code": 0, "data": await _svc.logs(limit)}


@router.get("/me")
async def mtier_me(
    x_member_id: Annotated[str | None,
                           Header(alias="X-Member-Id")] = None,
):
    """C 端本人会员卡视图(需登录态; 仅 auto 标签, manual 内部标记不外露)"""
    raw = (x_member_id or "").strip()
    if not raw:
        raise HTTPException(status_code=401,
                            detail="未登录: 请提供 X-Member-Id 头")
    try:
        member_id = int(raw)
    except ValueError:
        raise HTTPException(status_code=401,
                            detail="X-Member-Id 须为数字") from None
    try:
        return {"code": 0, "data": await _svc.me(member_id)}
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e)) from None


@router.post("/refresh")
async def mtier_refresh(
    x_role: Annotated[str | None, Header(alias="X-Role")] = None,
):
    """手动全量评估(admin; 幂等, 仅刷新 auto 标签与快照)"""
    _require_admin(x_role)
    return {"code": 0, "data": await _svc.evaluate_all()}


def register_mtier_routes(app) -> None:
    app.include_router(router)
