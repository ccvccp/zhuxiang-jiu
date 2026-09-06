"""58号·AI智能优化意图识别路由(P0)

端点(P0 5):
    GET  /api/ii58/registry          注册表自描述(admin, 观测面)
    POST /api/ii58/evaluate          意图识别评估(admin, 管理)
    GET  /api/ii58/evaluations       识别记录列表(admin, 观测面)
    GET  /api/ii58/evaluations/{id}  识别记录详情(admin, 观测面)
    GET  /api/ii58/model/status      模型状态(admin, 观测面)

鉴权: 管理面 X-Role: admin(43-57号同款口径)。
统一口径:
    - 观测面(registry/evaluations/model/status)
      不受 II58_MODE 影响
    - 决策面(evaluate): off=拒绝
      (409——shadow/assist 开放)
    - KeyError → 404 / ValueError → 409
"""

from fastapi import APIRouter, Header, HTTPException

router = APIRouter(prefix="/api/ii58",
                   tags=["AI智能优化意图识别(58号)"])


def _require_admin(x_role: str | None) -> str:
    if not x_role or x_role != "admin":
        raise HTTPException(status_code=403,
                            detail="需要 X-Role: admin")


@router.get("/registry")
async def registry(
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """注册表自描述(意图三位一体+置信度引擎口径
    ——观测面)"""
    _require_admin(x_role)
    from services.ii58_service import Ii58Service
    return Ii58Service.registry()


@router.post("/evaluate")
async def evaluate(
        body: dict,
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """意图识别评估(L1 语料匹配→动态阈值→三态
    响应+归因链; 决策面 off 409)"""
    _require_admin(x_role)
    from services.ii58_service import Ii58Service
    try:
        member_id = body.get("memberId")
        return await Ii58Service().evaluate(
            str(body.get("text") or ""),
            member_id=int(member_id)
            if member_id is not None else None,
            member_role=str(
                body.get("memberRole")
                or "member"))
    except ValueError as exc:
        raise HTTPException(status_code=409,
                            detail=str(exc))


@router.get("/evaluations")
async def evaluations(
        intent_id: str = None,
        state: str = None,
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """识别记录列表(置信度三态+归因链——观测面)"""
    _require_admin(x_role)
    from services.ii58_service import Ii58Service
    return await Ii58Service().list_evaluations(
        intent_id=intent_id, state=state)


@router.get("/evaluations/{eval_id}")
async def evaluation_detail(
        eval_id: int,
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """识别记录详情(归因链——观测面)"""
    _require_admin(x_role)
    from repositories.ii58_repository import (
        Ii58Repository,
    )
    record = await Ii58Repository().get_evaluation(
        int(eval_id))
    if record is None:
        raise HTTPException(
            status_code=404,
            detail=f"识别记录 {eval_id} 不存在")
    return {
        "success": True,
        "evaluation": record,
        "note": "识别记录详情——置信度三态+"
                "归因链(无归因不计入有效服务)",
    }


@router.get("/model/status")
async def model_status(
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """模型状态(第33档案 champion/challenger/八因子
    ——44号复用观测面)"""
    _require_admin(x_role)
    from services.ii58_service import Ii58Service
    return await Ii58Service().model_status()


def register_ii58_routes(app) -> None:
    """注册58号路由(main.py startup 调用)"""
    app.include_router(router)
