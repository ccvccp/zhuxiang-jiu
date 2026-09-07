"""全站跨模块路由(批次七·全站融合收官)

端点:
    POST /api/crossmodule/redteam   跨模块红队七向量
                                   (RT-X1~X7 攻击链贯穿
                                   23/44/46/47/60/64号多模块;
                                   admin; CROSS_RT_MODE=on
                                   显式启用, 默认 off 409)

鉴权: X-Role: admin(与既有红队端点同款口径)。

统一口径:
    - 红队决策面默认关闭(CROSS_RT_MODE=off → 409)
    - 隔离域 9971+ 号段, 种子用后复位, 事件留痕为审计轨
    - KeyError → 404 / ValueError → 409(全站约定)
"""

from fastapi import APIRouter, Header, HTTPException

router = APIRouter(prefix="/api/crossmodule",
                   tags=["跨模块融合(批次七)"])


def _require_admin(x_role: str | None):
    if x_role != "admin":
        raise HTTPException(status_code=403, detail="需要管理员权限")


@router.post("/redteam")
async def crossmodule_redteam(
    x_role: str = Header(default="", alias="X-Role"),
):
    """跨模块红队七向量(RT-X1~X7——伪信用跨域注入/学习域
    反馈伪造/scope越权扩散/治理冻结旁路/tier分裂读/tier
    字段注入/中枢调度处置越权; 每向量独立try+种子自复位)"""
    _require_admin(x_role)
    from services.crossmodule_redteam_service import (
        CrossModuleRedteamService, require_active_mode,
    )
    try:
        require_active_mode()
        return await CrossModuleRedteamService().run_all()
    except ValueError as exc:
        raise HTTPException(status_code=409,
                            detail=str(exc)) from exc


def register_crossmodule_routes(app) -> None:
    app.include_router(router)
