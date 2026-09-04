"""47号·L2/L3 信值验真风控模块路由(P0 角色风险画像)

端点(P0, 画像查询 2 + 校准 2——X-Role: admin):
    GET  /api/trust/risk/{trustId}     画像视图(风险指数/
                                      信任分层/命中明细/历史)
    GET  /api/trust/risk               全档案风险排行
                                      (最高风险在前+分层统计)
    POST /api/trust/risk/{trustId}/calibrate
                                      人工校准信任度覆盖
                                      (留痕, 可反复修正)
    POST /api/trust/risk/{trustId}/calibrate/clear
                                      清除校准回到计算值

鉴权: 管理端 X-Role: admin(43-46号同款口径);
画像查询对管理端开放(角色自查走 45号档案视图, 按需再开)。

统一口径:
    - 模块纯增量(零既有路由改动; 回流经 try 包裹 fail-soft)
    - 画像不处罚: P0 只沉淀观察, 不接任何入分/验真通道
    - KeyError → 404 / ValueError → 409(44-46号同款)
"""

from fastapi import APIRouter, Header, HTTPException

router = APIRouter(prefix="/api/trust/risk",
                   tags=["信值验真风控(47号)"])


def _require_admin(x_role: str | None):
    if x_role != "admin":
        raise HTTPException(status_code=403, detail="需要管理员权限")


def _handle(exc: Exception):
    """统一异常映射(43-46号同款)"""
    if isinstance(exc, KeyError):
        msg = str(exc) if str(exc) else "资源不存在"
        if msg.startswith("'") and msg.endswith("'"):
            msg = msg[1:-1]
        raise HTTPException(status_code=404, detail=msg)
    if isinstance(exc, ValueError):
        raise HTTPException(status_code=409, detail=str(exc))
    raise HTTPException(status_code=500, detail=str(exc))


@router.get("/{trust_id}")
async def get_risk_profile(
    trust_id: int,
    x_role: str = Header(default="", alias="X-Role"),
):
    """角色风险画像视图(风险指数/信任分层/命中明细/历史)"""
    _require_admin(x_role)
    try:
        from services.trust_risk_profile_service import (
            TrustRiskProfileService,
        )
        return await TrustRiskProfileService().get_profile(
            trust_id)
    except Exception as e:
        raise _handle(e) from e


@router.get("")
async def list_risk_profiles(
    x_role: str = Header(default="", alias="X-Role"),
):
    """全档案风险排行(最高风险在前 + 信任分层统计)"""
    _require_admin(x_role)
    try:
        from services.trust_risk_profile_service import (
            TrustRiskProfileService,
        )
        return await TrustRiskProfileService().list_profiles()
    except Exception as e:
        raise _handle(e) from e


@router.post("/{trust_id}/calibrate")
async def calibrate_risk_profile(
    trust_id: int,
    body: dict,
    x_role: str = Header(default="", alias="X-Role"),
):
    """人工校准信任度覆盖(复核闭环前置——留痕可反复修正)

    body: {trustLevel ∈ [0,1], note(理由必填)}
    """
    _require_admin(x_role)
    if not isinstance(body, dict) or \
            "trustLevel" not in body:
        raise HTTPException(status_code=409,
                            detail="请求体需含 trustLevel 字段")
    try:
        from services.trust_risk_profile_service import (
            TrustRiskProfileService,
        )
        return await TrustRiskProfileService().calibrate(
            trust_id, body.get("trustLevel"),
            str(body.get("note") or ""))
    except Exception as e:
        raise _handle(e) from e


@router.post("/{trust_id}/calibrate/clear")
async def clear_risk_calibration(
    trust_id: int,
    body: dict,
    x_role: str = Header(default="", alias="X-Role"),
):
    """清除校准覆盖(回到计算值——留痕)"""
    _require_admin(x_role)
    if not isinstance(body, dict):
        raise HTTPException(status_code=409, detail="请求体需为对象")
    try:
        from services.trust_risk_profile_service import (
            TrustRiskProfileService,
        )
        return await TrustRiskProfileService(
        ).clear_calibration(
            trust_id, str(body.get("note") or ""))
    except Exception as e:
        raise _handle(e) from e


def register_trust_risk_routes(app) -> None:
    """注册47号路由(main.py startup 调用)"""
    app.include_router(router)
