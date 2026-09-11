"""智运·AI智能物流大模型路由(P0-P3 全量, 14 端点)

物流接口管理模块升级更名: 智运·AI智能物流大模型。
鉴权: 全部管理端 X-Role: admin(物流调度敏感域)。
既有 /api/logistics/* 18 端点保留零改动(叠加式升级)。

端点分布:
    - P0 智能路由:  route/decide / route/decisions / carrier-scores
                    / route/health
    - P1 轨迹智能:  track/eta / track/anomalies / track/delays
    - P2 风控回执:  risk/assess / risk/inspect-receipt / risk/claims
    - P3 分析进化:  analysis/cost / analysis/volume-forecast
                    / evolution/feedback / evolution/feedbacks / status

异常映射: KeyError → 404 / ValueError → 409 / PermissionError → 403
"""

from fastapi import APIRouter, Header, HTTPException, Query
from pydantic import BaseModel as PydBaseModel, Field

from services.zw_fabric_service import _ZwStore
from services.zw_route_service import ZwRouteService
from services.zw_track_service import ZwTrackService
from services.zw_risk_service import ZwRiskService
from services.zw_analysis_service import ZwAnalysisService

router = APIRouter()
_store = _ZwStore()
_route = ZwRouteService(store=_store)
_track = ZwTrackService(store=_store)
_risk = ZwRiskService(store=_store)
_analysis = ZwAnalysisService(store=_store)


def _require_admin(x_role: str | None):
    if x_role != "admin":
        raise HTTPException(status_code=403, detail="需要管理员权限")


def _handle(exc: Exception):
    if isinstance(exc, KeyError):
        msg = str(exc) if str(exc) else "资源不存在"
        if msg.startswith("'") and msg.endswith("'"):
            msg = msg[1:-1]
        raise HTTPException(status_code=404, detail=msg)
    if isinstance(exc, PermissionError):
        raise HTTPException(status_code=403, detail=str(exc))
    if isinstance(exc, ValueError):
        raise HTTPException(status_code=409, detail=str(exc))
    raise HTTPException(status_code=500, detail=str(exc))


# ============================================================
# 请求模型
# ============================================================

class RouteDecideRequest(PydBaseModel):
    orderType: str = Field(..., description="retail|groupbuy|return")
    weight: float = Field(..., ge=0)
    pieceCount: int = Field(..., ge=1)
    insuredValue: float = Field(0.0, ge=0)
    sender: dict = Field(default_factory=dict)
    receiver: dict = Field(default_factory=dict)


class RiskAssessRequest(PydBaseModel):
    orderType: str = Field("retail")
    weight: float = Field(..., ge=0)
    pieceCount: int = Field(..., ge=1)
    insuredValue: float = Field(0.0, ge=0)
    receiverProvince: str = Field("", max_length=20)
    urgent: bool = Field(False)


class InspectReceiptRequest(PydBaseModel):
    orderId: str = Field(..., min_length=1, max_length=40)
    expectedCount: int = Field(..., ge=1)
    actualCount: int = Field(..., ge=0)
    inspector: str = Field(..., min_length=1, max_length=40)
    photos: list = Field(default_factory=list)
    remark: str = Field("", max_length=200)


class ClaimRequest(PydBaseModel):
    waybillNo: str = Field(..., min_length=1, max_length=50)
    orderId: str = Field(..., min_length=1, max_length=40)
    carrier: str = Field(..., min_length=1, max_length=10)
    claimType: str = Field(..., description="damage|loss|delay|stain")
    claimAmount: float = Field(..., gt=0)
    description: str = Field("", max_length=300)
    evidenceUrls: list = Field(default_factory=list)


class FeedbackRequest(PydBaseModel):
    targetType: str = Field(..., description="route_decision|eta|"
                                            "risk_assess|claim")
    verdict: str = Field(..., description="adopted|corrected|rejected")
    note: str = Field("", max_length=200)


# ============================================================
# P0: 智能路由(admin)
# ============================================================

@router.post("/api/logistics-ai/route/decide",
             tags=["智运AI智能物流大模型"])
async def route_decide(data: RouteDecideRequest,
                       x_role: str = Header(None, alias="X-Role")):
    """多维路由决策(规则基座+质量评分, 决策留痕)"""
    _require_admin(x_role)
    try:
        result = await _route.route_decide(
            order_type=data.orderType, weight=data.weight,
            piece_count=data.pieceCount,
            insured_value=data.insuredValue, sender=data.sender,
            receiver=data.receiver)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.get("/api/logistics-ai/route/decisions",
            tags=["智运AI智能物流大模型"])
async def route_decisions(x_role: str = Header(None, alias="X-Role"),
                          limit: int = Query(50, ge=1, le=200)):
    """路由决策留痕列表"""
    _require_admin(x_role)
    try:
        rows = await _route.route_decisions(limit=limit)
        return {"success": True, "data": rows, "count": len(rows)}
    except Exception as e:
        _handle(e)


@router.get("/api/logistics-ai/route/carrier-scores",
            tags=["智运AI智能物流大模型"])
async def carrier_scores(x_role: str = Header(None, alias="X-Role")):
    """物流商质量评分(签收率40%+时效30%+费率30%)"""
    _require_admin(x_role)
    try:
        result = await _route.carrier_scores()
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.get("/api/logistics-ai/route/health",
            tags=["智运AI智能物流大模型"])
async def route_health(x_role: str = Header(None, alias="X-Role")):
    """物流商健康度监测(降级→切换建议书, 永不自动)"""
    _require_admin(x_role)
    try:
        result = await _route.carrier_health()
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


# ============================================================
# P1: 轨迹智能(admin)
# ============================================================

@router.get("/api/logistics-ai/track/eta/{waybill_no}",
            tags=["智运AI智能物流大模型"])
async def track_eta(waybill_no: str,
                    x_role: str = Header(None, alias="X-Role")):
    """运单 ETA 预测(历史时效均值×进度外推, 确定性)"""
    _require_admin(x_role)
    try:
        result = await _track.eta_predict(waybill_no)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.get("/api/logistics-ai/track/anomalies",
            tags=["智运AI智能物流大模型"])
async def track_anomalies(x_role: str = Header(None, alias="X-Role"),
                          limit: int = Query(100, ge=1, le=500)):
    """异常四检测器(揽收超时/停滞/派送失败/签收超时)"""
    _require_admin(x_role)
    try:
        rows = await _track.detect_anomalies(limit=limit)
        return {"success": True, "data": rows, "count": len(rows)}
    except Exception as e:
        _handle(e)


@router.get("/api/logistics-ai/track/delays",
            tags=["智运AI智能物流大模型"])
async def track_delays(x_role: str = Header(None, alias="X-Role"),
                       limit: int = Query(100, ge=1, le=500)):
    """延误预警总览(阈值口径+分级明细)"""
    _require_admin(x_role)
    try:
        result = await _track.delay_warnings(limit=limit)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


# ============================================================
# P2: 风控与回执(admin)
# ============================================================

@router.post("/api/logistics-ai/risk/assess",
             tags=["智运AI智能物流大模型"])
async def risk_assess(data: RiskAssessRequest,
                      x_role: str = Header(None, alias="X-Role")):
    """下单前四防风控评分(防破损/防丢失/防延误/前置四级)"""
    _require_admin(x_role)
    try:
        result = await _risk.risk_assess(
            order_type=data.orderType, weight=data.weight,
            piece_count=data.pieceCount,
            insured_value=data.insuredValue,
            receiver_province=data.receiverProvince,
            urgent=data.urgent)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.post("/api/logistics-ai/risk/inspect-receipt",
             tags=["智运AI智能物流大模型"])
async def inspect_receipt(data: InspectReceiptRequest,
                          x_role: str = Header(None, alias="X-Role")):
    """团购开箱验货回执(差异→补寄工单建议, 永不自动)"""
    _require_admin(x_role)
    try:
        result = await _risk.inspect_receipt(
            order_id=data.orderId, expected_count=data.expectedCount,
            actual_count=data.actualCount, inspector=data.inspector,
            photos=data.photos, remark=data.remark)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.post("/api/logistics-ai/risk/claims",
             tags=["智运AI智能物流大模型"])
async def create_claim(data: ClaimRequest,
                       x_role: str = Header(None, alias="X-Role")):
    """创建理赔工单(四类; 赔付审批须人工, 建议书)"""
    _require_admin(x_role)
    try:
        result = await _risk.create_claim(
            waybill_no=data.waybillNo, order_id=data.orderId,
            carrier=data.carrier, claim_type=data.claimType,
            claim_amount=data.claimAmount,
            description=data.description,
            evidence_urls=data.evidenceUrls)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.get("/api/logistics-ai/risk/claims",
            tags=["智运AI智能物流大模型"])
async def list_claims(x_role: str = Header(None, alias="X-Role"),
                      limit: int = Query(50, ge=1, le=200)):
    """理赔工单列表"""
    _require_admin(x_role)
    try:
        rows = await _risk.claims(limit=limit)
        return {"success": True, "data": rows, "count": len(rows)}
    except Exception as e:
        _handle(e)


# ============================================================
# P3: 分析与进化(admin)
# ============================================================

@router.get("/api/logistics-ai/analysis/cost",
            tags=["智运AI智能物流大模型"])
async def cost_analysis(x_role: str = Header(None, alias="X-Role")):
    """物流成本分析(物流商对比+月度趋势+议价建议)"""
    _require_admin(x_role)
    try:
        result = await _analysis.cost_analysis()
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.get("/api/logistics-ai/analysis/volume-forecast",
            tags=["智运AI智能物流大模型"])
async def volume_forecast(x_role: str = Header(None, alias="X-Role"),
                         horizon: int = Query(1, ge=1, le=6)):
    """运量预测(加权移动平均+趋势, 运力规划参考)"""
    _require_admin(x_role)
    try:
        result = await _analysis.volume_forecast(horizon=horizon)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.post("/api/logistics-ai/evolution/feedback",
             tags=["智运AI智能物流大模型"])
async def evolution_feedback(data: FeedbackRequest,
                             x_role: str = Header(None,
                                                  alias="X-Role")):
    """反馈闭环(etaWeight ±0.1, 安全阀 [0.4, 0.8])"""
    _require_admin(x_role)
    try:
        result = await _analysis.feedback(
            target_type=data.targetType, verdict=data.verdict,
            note=data.note)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.get("/api/logistics-ai/evolution/feedbacks",
            tags=["智运AI智能物流大模型"])
async def evolution_feedbacks(x_role: str = Header(None,
                                                   alias="X-Role"),
                              limit: int = Query(50, ge=1, le=200)):
    """反馈进化记录列表"""
    _require_admin(x_role)
    try:
        rows = await _analysis.feedbacks(limit=limit)
        return {"success": True, "data": rows, "count": len(rows)}
    except Exception as e:
        _handle(e)


@router.get("/api/logistics-ai/status",
            tags=["智运AI智能物流大模型"])
async def status(x_role: str = Header(None, alias="X-Role")):
    """智运大模型总览(三率+四引擎+进化参数)"""
    _require_admin(x_role)
    try:
        result = await _analysis.status()
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


def register_zw_routes(app):
    """注册智运·AI智能物流大模型路由"""
    app.include_router(router)
