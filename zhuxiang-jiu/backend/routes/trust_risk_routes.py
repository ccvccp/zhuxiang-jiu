"""47号·L2/L3 信值验真风控模块路由(P0 画像 + P1 扫描 + P2 协同 + P3 复核)

端点(P0 画像 2 + 校准 2 + P1 扫描 1 + P2 协同 2 + P3 复核 2):
    GET  /api/trust/risk/{trustId}     画像视图(风险指数/信任
                                      分层/命中明细/历史/复核
                                      队列; X-Role: admin)
    GET  /api/trust/risk               全档案风险排行
                                      (最高风险在前+分层统计)
    POST /api/trust/risk/{trustId}/scan
                                      角色级检测(P1 语义指纹
                                      +价值分布, 幂等)
    POST /api/trust/risk/collusion/scan
                                      协同扫描+嫌疑标记
                                      (P2 互证对+指纹共享,
                                      幂等——已标记跳过)
    GET  /api/trust/risk/collusion    团伙视图(嫌疑对+证据链
                                      明细, 供人工复核; 纯读)
    POST /api/trust/risk/{trustId}/review-request
                                      角色申诉画像误判
                                      (P3 复核通道入口——
                                      开放端点, 45号申诉流
                                      范式; 同一档案同时只挂
                                      一条待复核)
    POST /api/trust/risk/{trustId}/reviews/{reviewId}/decide
                                      管理端复核决定(误判确认
                                      →人工校准留痕/维持原判;
                                      X-Role: admin)
    POST /api/trust/risk/{trustId}/calibrate
                                      人工校准信任度覆盖
                                      (留痕, 可反复修正)
    POST /api/trust/risk/{trustId}/calibrate/clear
                                      清除校准回到计算值

鉴权: 管理端 X-Role: admin(43-46号同款口径); 画像查询对
管理端开放(角色自查走 45号档案视图, 按需再开); 复核申诉
(review-request)按 45号申诉流范式开放(角色侧入口)。

统一口径:
    - 模块纯增量(零既有路由改动; 回流经 try 包裹 fail-soft)
    - 画像不处罚: 嫌疑仅标记, 处罚走人工复核(红线④)
    - /collusion 字面路由须注册在 /{trust_id} 之前
      (防 int 路径参数 422 抢匹配)
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


# ============================================================
# P2 协同分析(字面路由——须在 /{trust_id} 之前注册)
# ============================================================

@router.post("/collusion/scan")
async def scan_collusion(
    x_role: str = Header(default="", alias="X-Role"),
):
    """协同扫描+嫌疑标记(互证对+跨角色指纹共享→
    collusive_suspect 沉淀; 幂等——已标记角色跳过)"""
    _require_admin(x_role)
    try:
        from services.trust_risk_collusion_service import (
            TrustRiskCollusionService,
        )
        return await TrustRiskCollusionService().scan()
    except Exception as e:
        raise _handle(e) from e


@router.get("/collusion")
async def collusion_view(
    x_role: str = Header(default="", alias="X-Role"),
):
    """团伙视图(嫌疑对列表+证据链明细——互证时间线/共享
    指纹, 供人工复核; 纯读零写入)"""
    _require_admin(x_role)
    try:
        from services.trust_risk_collusion_service import (
            TrustRiskCollusionService,
        )
        return await TrustRiskCollusionService().view()
    except Exception as e:
        raise _handle(e) from e


# ============================================================
# P3 复核通道(45号申诉流范式)
# ============================================================

@router.post("/{trust_id}/review-request")
async def submit_review_request(trust_id: int, body: dict):
    """角色申诉画像误判(复核通道入口——开放端点, 45号
    申诉流范式; 同一档案同时只挂一条待复核申诉)

    body: {reason(8-500 字符)}
    """
    if not isinstance(body, dict):
        raise HTTPException(status_code=409, detail="请求体需为对象")
    try:
        from services.trust_risk_profile_service import (
            TrustRiskProfileService,
        )
        return await TrustRiskProfileService(
        ).submit_review_request(
            trust_id, str(body.get("reason") or ""))
    except Exception as e:
        raise _handle(e) from e


@router.post("/{trust_id}/reviews/{review_id}/decide")
async def decide_review(trust_id: int, review_id: str,
                        body: dict,
                        x_role: str = Header(default="",
                                             alias="X-Role")):
    """管理端复核决定(误判确认→人工校准留痕 / 维持原判)

    body: {approve(必填), trustLevel(approve 时必填
    ∈[0,1]), note(复核理由必填 1-300 字符)}
    """
    _require_admin(x_role)
    if not isinstance(body, dict) \
            or not isinstance(body.get("approve"), bool):
        raise HTTPException(status_code=409,
                            detail="请求体需含 approve 布尔字段")
    try:
        from services.trust_risk_profile_service import (
            TrustRiskProfileService,
        )
        return await TrustRiskProfileService().decide_review(
            trust_id, review_id,
            body.get("approve"),
            str(body.get("note") or ""),
            trust_level=body.get("trustLevel"))
    except Exception as e:
        raise _handle(e) from e


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


@router.post("/{trust_id}/scan")
async def scan_risk_detectors(
    trust_id: int,
    x_role: str = Header(default="", alias="X-Role"),
):
    """触发一轮角色级检测(P1: 语义复用桶况+价值分布
    小额高频——命中沉淀画像, 幂等)"""
    _require_admin(x_role)
    try:
        from services.trust_risk_detector_service import (
            TrustRiskDetectorService,
        )
        return await TrustRiskDetectorService().scan(
            trust_id)
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
