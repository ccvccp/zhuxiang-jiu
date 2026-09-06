"""63号·AI智能后台管理路由(P0+P1)

端点(P0+P1 6):
    GET  /api/ab63/registry            注册表自描述(admin, 观测面)
    POST /api/ab63/grants              权限裁决(admin, 决策面 off 409)
    GET  /api/ab63/grants              裁决记录列表(admin, 观测面)
    GET  /api/ab63/grants/{grantId}    裁决单条 reason 链(admin, P1 观测面)
    POST /api/ab63/workbench/render    工作台渲染(admin, 决策面 off 409)
    GET  /api/ab63/model/status         模型状态(admin, 观测面)

鉴权: 管理面 X-Role: admin(43-59号同款口径)。
统一口径:
    - 观测面(registry/grants/model/status)
      不受 AB63_MODE 影响
    - 决策面(grants 裁决/workbench 渲染):
      off=拒绝(409——shadow/assist 开放)
    - KeyError → 404 / ValueError → 409
"""

from fastapi import APIRouter, Header, HTTPException

router = APIRouter(prefix="/api/ab63",
                   tags=["AI智能后台管理(63号)"])


def _require_admin(x_role: str | None) -> str:
    if not x_role or x_role != "admin":
        raise HTTPException(status_code=403,
                            detail="需要 X-Role: admin")


@router.get("/registry")
async def registry(
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """后台注册表自描述(四轴规则+五角色
    模板——观测面)"""
    _require_admin(x_role)
    from services.ab63_service import Ab63Service
    return Ab63Service.registry()


@router.post("/grants")
async def evaluate_grant(
        body: dict,
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """权限裁决(四轴确定性计算+reason 可
    解释链; 决策面 off 409)

    Body: {memberId, role, action, tier?,
    complianceRate?, period?, sensitivity?}"""
    _require_admin(x_role)
    from services.ab63_service import Ab63Service
    try:
        member_id = body.get("memberId")
        return await (
            Ab63Service().evaluate_grant(
                member_id=int(member_id)
                if member_id is not None
                else 0,
                role=str(
                    body.get("role") or ""),
                action=str(
                    body.get("action") or ""),
                tier=body.get("tier"),
                compliance_rate=body.get(
                    "complianceRate"),
                period=str(
                    body.get("period")
                    or "normal"),
                sensitivity=str(
                    body.get("sensitivity")
                    or "low")))
    except ValueError as exc:
        raise HTTPException(status_code=409,
                            detail=str(exc))


@router.get("/grants")
async def grants(
        member_id: int = None,
        role: str = None,
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """权限裁决记录列表(reason 可解释链
    ——观测面)"""
    _require_admin(x_role)
    from services.ab63_service import Ab63Service
    return await Ab63Service().list_grants(
        member_id=member_id, role=role)


@router.get("/grants/{grant_id}")
async def get_grant(
        grant_id: int,
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """裁决单条(P1 观测面——ruleId+
    recoveryPath 完整可解释链; 不存在 404)"""
    _require_admin(x_role)
    from services.ab63_service import Ab63Service
    try:
        return await Ab63Service().get_grant(
            grant_id=grant_id)
    except KeyError as exc:
        raise HTTPException(status_code=404,
                            detail=str(exc))


@router.post("/workbench/render")
async def render_workbench(
        body: dict,
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """工作台渲染(角色模板+novice/mature
    视图——情境化呈现; 决策面 off 409)

    Body: {memberId, role, novice?}"""
    _require_admin(x_role)
    from services.ab63_service import Ab63Service
    try:
        member_id = body.get("memberId")
        return await (
            Ab63Service().render_workbench(
                member_id=int(member_id)
                if member_id is not None
                else 0,
                role=str(
                    body.get("role") or ""),
                novice=bool(
                    body.get("novice"))))
    except ValueError as exc:
        raise HTTPException(status_code=409,
                            detail=str(exc))


@router.get("/model/status")
async def model_status(
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """模型状态(第38档案 champion/
    challenger/八因子——44号复用观测面)"""
    _require_admin(x_role)
    from services.ab63_service import Ab63Service
    return await Ab63Service().model_status()


def register_ab63_routes(app) -> None:
    """注册63号路由(main.py startup 调用)"""
    app.include_router(router)
