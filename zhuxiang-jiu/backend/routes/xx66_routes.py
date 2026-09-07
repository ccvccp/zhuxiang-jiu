"""66号·AI智能工程师大模块路由(P0 生命体征底座)

端点(观测面 3 + 决策面 1):
    GET  /api/xx66/status            模块状态(模式/评分器
                                     入册/快照量级; admin;
                                     观测面——永不关停)
    GET  /api/xx66/vitals            生命体征图谱(四区红黄绿
                                     +总评; admin; 纯读聚合)
    GET  /api/xx66/vitals/history   快照时序(观测面; admin)
    POST /api/xx66/vitals/scan       主动巡检(决策面:
                                     聚合→落快照→黄/红区起
                                     27号自愈链(纯记录零执行);
                                     XX66_MODE off → 409)

鉴权: X-Role: admin(与 47号 hub/既有 xx 模块同款口径)。

统一口径:
    - 观测面永不关停(宪法); 决策面三态开关
      XX66_MODE: off(默认)/shadow/assist
    - KeyError → 404 / ValueError → 409(全站约定)
"""

from fastapi import APIRouter, Header, HTTPException

router = APIRouter(prefix="/api/xx66",
                   tags=["AI智能工程师(66号)"])


def _require_admin(x_role: str | None):
    if x_role != "admin":
        raise HTTPException(status_code=403,
                            detail="需要管理员权限")


@router.get("/status")
async def xx66_status(
    x_role: str = Header(default="", alias="X-Role"),
):
    """模块状态(模式/LLM 轨/评分器入册——观测面永不关停)"""
    _require_admin(x_role)
    try:
        from services.xx66_service import Xx66Service
        return await Xx66Service().status()
    except ValueError as exc:
        raise HTTPException(status_code=409,
                            detail=str(exc)) from exc


@router.get("/vitals")
async def xx66_vitals(
    x_role: str = Header(default="", alias="X-Role"),
):
    """生命体征图谱(四区: system/business/ai_services/trust
    红黄绿灯+总评——纯读聚合, fail-soft 分区)"""
    _require_admin(x_role)
    try:
        from services.xx66_service import Xx66Service
        return await Xx66Service().vitals()
    except ValueError as exc:
        raise HTTPException(status_code=409,
                            detail=str(exc)) from exc


@router.get("/vitals/history")
async def xx66_vitals_history(
    limit: int = 20,
    x_role: str = Header(default="", alias="X-Role"),
):
    """快照时序(最近 N 条——观测面永不关停)"""
    _require_admin(x_role)
    try:
        from services.xx66_service import Xx66Service
        return await Xx66Service().vitals_history(limit)
    except ValueError as exc:
        raise HTTPException(status_code=409,
                            detail=str(exc)) from exc


@router.post("/vitals/scan")
async def xx66_vitals_scan(
    x_role: str = Header(default="", alias="X-Role"),
):
    """主动巡检(决策面: 真实聚合四源生成快照 + 黄/红区起
    27号自愈链(纯状态机记录零执行)——替代 inspect_all
    硬编码清单; XX66_MODE=off → 409)"""
    _require_admin(x_role)
    from services.xx66_service import Xx66Service
    try:
        return await Xx66Service().scan()
    except ValueError as exc:
        raise HTTPException(status_code=409,
                            detail=str(exc)) from exc


def register_xx66_routes(app) -> None:
    app.include_router(router)
