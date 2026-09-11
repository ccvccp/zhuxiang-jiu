"""智运·AI智能物流大模型路由(P0-P7 全量, 37 端点)

物流接口管理模块升级更名: 智运·AI智能物流大模型(一代 P0-P3)
+ 智酿运通·自适应酒水供应链物流中枢(二代 P4-P7)。
鉴权: 全部管理端 X-Role: admin(物流调度敏感域);
      唯一例外 binding/verify/{code} 消费者扫码验真(公开+脱敏)。
既有 /api/logistics/* 18 端点保留零改动(叠加式升级)。

端点分布:
    - P0 智能路由:  route/decide / route/decisions / carrier-scores
                    / route/health
    - P1 轨迹智能:  track/eta / track/anomalies / track/delays
    - P2 风控回执:  risk/assess / risk/inspect-receipt / risk/claims
    - P3 分析进化:  analysis/cost / analysis/volume-forecast
                    / evolution/feedback / evolution/feedbacks / status
    - P4a 语义调配: semantic/normalize / semantic/carriers
                    / semantic/register-carrier / semantic/order-profile
                    / semantic/feature-route / semantic/feature-routes
    - P4b 渠道熔断: circuit/status / circuit/report / circuit/reports
    - P5 深度绑定:  binding/capacity-plan / binding/capacity-plans
                    / binding/tri-code / binding/tri-codes
                    / binding/verify/{code}(公开) / binding/reverse
                    / binding/reverses
    - P6 角色提醒: alert/scan / alerts / alerts/{id}/ack
    - P7 进化2.0:  evolution2/preference / evolution2/preferences
                    / evolution2/anomaly-patterns / evolution2/carbon
                    / evolution2/suggest / evolution2/suggestions
                    / evolution2/suggestions/{id}/decide

异常映射: KeyError → 404 / ValueError → 409 / PermissionError → 403
"""

from fastapi import APIRouter, Header, HTTPException, Query
from pydantic import BaseModel as PydBaseModel, Field

from services.zw_fabric_service import _ZwStore
from services.zw_route_service import ZwRouteService
from services.zw_track_service import ZwTrackService
from services.zw_risk_service import ZwRiskService
from services.zw_analysis_service import ZwAnalysisService
from services.zw_semantic_service import ZwSemanticService
from services.zw_circuit_service import ZwCircuitService
from services.zw_binding_service import ZwBindingService
from services.zw_alert_service import ZwAlertService
from services.zw_evolution2_service import ZwEvolution2Service

router = APIRouter()
_store = _ZwStore()
_route = ZwRouteService(store=_store)
_track = ZwTrackService(store=_store)
_risk = ZwRiskService(store=_store)
_analysis = ZwAnalysisService(store=_store)
_semantic = ZwSemanticService(store=_store)
_circuit = ZwCircuitService(store=_store)
_binding = ZwBindingService(store=_store)
_alert = ZwAlertService(store=_store)
_evo2 = ZwEvolution2Service(store=_store)


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


# ============================================================
# 智酿运通 P4a: 语义调配引擎(admin)
# ============================================================

class NormalizeRequest(PydBaseModel):
    carrier: str = Field(..., min_length=1, max_length=10)
    payload: dict = Field(..., description="渠道原始回单(方言字段)")


class RegisterCarrierRequest(PydBaseModel):
    carrier: str = Field(..., min_length=1, max_length=10)
    carrierName: str = Field(..., min_length=1, max_length=30)
    fieldMap: dict = Field(..., description="渠道字段→平台字段映射")
    statusMap: dict = Field(default_factory=dict,
                             description="状态方言→平台状态枚")
    conditions: list = Field(default_factory=list,
                             description="支持的运输条件标签")


class OrderProfileRequest(PydBaseModel):
    orderType: str = Field("retail")
    weight: float = Field(..., ge=0)
    pieceCount: int = Field(..., ge=1)
    insuredValue: float = Field(0.0, ge=0)
    urgent: bool = Field(False)
    receiver: dict = Field(default_factory=dict)


class FeatureRouteRequest(PydBaseModel):
    profile: dict = Field(..., description="order-profile 输出画像")


@router.post("/api/logistics-ai/semantic/normalize",
             tags=["智运AI智能物流大模型"])
async def semantic_normalize(data: NormalizeRequest,
                              x_role: str = Header(None, alias="X-Role")):
    """统一语义层: 异构渠道回单 → 平台标准数据模型"""
    _require_admin(x_role)
    try:
        result = await _semantic.normalize_payload(
            data.carrier, data.payload)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.get("/api/logistics-ai/semantic/carriers",
            tags=["智运AI智能物流大模型"])
async def semantic_carriers(
        x_role: str = Header(None, alias="X-Role")):
    """渠道注册表总览(内置五渠道+配置化接入)"""
    _require_admin(x_role)
    try:
        result = await _semantic.carriers()
        return {"success": True, "data": result, "count": len(result)}
    except Exception as e:
        _handle(e)


@router.post("/api/logistics-ai/semantic/register-carrier",
             tags=["智运AI智能物流大模型"])
async def semantic_register_carrier(
        data: RegisterCarrierRequest,
        x_role: str = Header(None, alias="X-Role")):
    """配置化新运力接入(建议书制: 投产须人工确认)"""
    _require_admin(x_role)
    try:
        result = await _semantic.register_carrier(
            carrier=data.carrier, carrier_name=data.carrierName,
            field_map=data.fieldMap, status_map=data.statusMap,
            conditions=data.conditions)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.post("/api/logistics-ai/semantic/order-profile",
             tags=["智运AI智能物流大模型"])
async def semantic_order_profile(
        data: OrderProfileRequest,
        x_role: str = Header(None, alias="X-Role")):
    """四维订单画像(品类+包装+时效+风险, 确定性推断)"""
    _require_admin(x_role)
    try:
        result = _semantic.order_profile(
            order_type=data.orderType, weight=data.weight,
            piece_count=data.pieceCount,
            insured_value=data.insuredValue, urgent=data.urgent,
            receiver=data.receiver)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.post("/api/logistics-ai/semantic/feature-route",
             tags=["智运AI智能物流大模型"])
async def semantic_feature_route(
        data: FeatureRouteRequest,
        x_role: str = Header(None, alias="X-Role")):
    """特征路由策略表(优先+备选+进化反馈信号, 决策留痕)"""
    _require_admin(x_role)
    try:
        result = await _semantic.feature_route(data.profile)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.get("/api/logistics-ai/semantic/feature-routes",
            tags=["智运AI智能物流大模型"])
async def semantic_feature_routes(
        x_role: str = Header(None, alias="X-Role"),
        limit: int = Query(50, ge=1, le=200)):
    """特征路由决策留痕列表"""
    _require_admin(x_role)
    try:
        result = await _semantic.feature_routes(limit=limit)
        return {"success": True, "data": result, "count": len(result)}
    except Exception as e:
        _handle(e)


# ============================================================
# 智酿运通 P4b: 渠道熔断(admin)
# ============================================================

@router.get("/api/logistics-ai/circuit/status",
            tags=["智运AI智能物流大模型"])
async def circuit_status(x_role: str = Header(None, alias="X-Role")):
    """全渠道熔断状态总览(揽收率/中转时效/异常率三指标)"""
    _require_admin(x_role)
    try:
        result = await _circuit.circuit_status()
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.post("/api/logistics-ai/circuit/report",
             tags=["智运AI智能物流大模型"])
async def circuit_report(x_role: str = Header(None, alias="X-Role")):
    """生成《运力异常报告》(open 渠道切换建议, 人工确认)"""
    _require_admin(x_role)
    try:
        result = await _circuit.generate_report()
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.get("/api/logistics-ai/circuit/reports",
            tags=["智运AI智能物流大模型"])
async def circuit_reports(x_role: str = Header(None, alias="X-Role"),
                           limit: int = Query(20, ge=1, le=100)):
    """运力异常报告列表"""
    _require_admin(x_role)
    try:
        result = await _circuit.reports(limit=limit)
        return {"success": True, "data": result, "count": len(result)}
    except Exception as e:
        _handle(e)


# ============================================================
# 智酿运通 P5: 订单-物流深度绑定
# ============================================================

class TriCodeRequest(PydBaseModel):
    waybillNo: str = Field(..., min_length=1, max_length=60)
    orderId: str = Field(..., min_length=1, max_length=60)
    batchCode: str = Field(..., min_length=1, max_length=60)
    antiFakeCode: str = Field(..., min_length=1, max_length=60)


class ReverseRequest(PydBaseModel):
    orderId: str = Field(..., min_length=1, max_length=60)
    condition: str = Field(..., description="unopened/opened/damaged")
    reason: str = Field("", max_length=200)


@router.get("/api/logistics-ai/binding/capacity-plan",
            tags=["智运AI智能物流大模型"])
async def binding_capacity_plan(
        x_role: str = Header(None, alias="X-Role")):
    """生产-物流联动: 未来 3 天分渠道运力舱位预约建议书"""
    _require_admin(x_role)
    try:
        result = await _binding.capacity_plan()
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.get("/api/logistics-ai/binding/capacity-plans",
            tags=["智运AI智能物流大模型"])
async def binding_capacity_plans(
        x_role: str = Header(None, alias="X-Role"),
        limit: int = Query(20, ge=1, le=100)):
    """运力舱位预约建议书历史"""
    _require_admin(x_role)
    try:
        result = await _binding.capacity_plans(limit=limit)
        return {"success": True, "data": result, "count": len(result)}
    except Exception as e:
        _handle(e)


@router.post("/api/logistics-ai/binding/tri-code",
             tags=["智运AI智能物流大模型"])
async def binding_tri_code(data: TriCodeRequest,
                           x_role: str = Header(None, alias="X-Role")):
    """三码合一绑定(运单号+批次码+防伪码)"""
    _require_admin(x_role)
    try:
        result = await _binding.tri_code_bind(
            waybill_no=data.waybillNo, order_id=data.orderId,
            batch_code=data.batchCode,
            anti_fake_code=data.antiFakeCode)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.get("/api/logistics-ai/binding/tri-codes",
            tags=["智运AI智能物流大模型"])
async def binding_tri_codes(x_role: str = Header(None, alias="X-Role"),
                            limit: int = Query(50, ge=1, le=200)):
    """三码绑定列表"""
    _require_admin(x_role)
    try:
        result = await _binding.tri_codes(limit=limit)
        return {"success": True, "data": result, "count": len(result)}
    except Exception as e:
        _handle(e)


@router.get("/api/logistics-ai/binding/verify/{code}",
            tags=["智运AI智能物流大模型"])
async def binding_verify(code: str):
    """消费者扫码验真(公开, 任一码→全链档案, 脱敏输出)"""
    try:
        result = await _binding.verify_by_code(code)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.post("/api/logistics-ai/binding/reverse",
             tags=["智运AI智能物流大模型"])
async def binding_reverse(data: ReverseRequest,
                          x_role: str = Header(None, alias="X-Role")):
    """逆向物流: 商品状态→最优退回路径+《退货处置协议》"""
    _require_admin(x_role)
    try:
        result = await _binding.reverse_bind(
            order_id=data.orderId, condition=data.condition,
            reason=data.reason)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.get("/api/logistics-ai/binding/reverses",
            tags=["智运AI智能物流大模型"])
async def binding_reverses(x_role: str = Header(None, alias="X-Role"),
                           limit: int = Query(50, ge=1, le=200)):
    """退货处置协议列表"""
    _require_admin(x_role)
    try:
        result = await _binding.reverses(limit=limit)
        return {"success": True, "data": result, "count": len(result)}
    except Exception as e:
        _handle(e)


# ============================================================
# 智酿运通 P6: 多角色智能提醒(admin)
# ============================================================

@router.post("/api/logistics-ai/alert/scan",
             tags=["智运AI智能物流大模型"])
async def alert_scan(x_role: str = Header(None, alias="X-Role")):
    """五角色触发面扫描(延误/异常/爆仓/成本/KPI→草稿+建议)"""
    _require_admin(x_role)
    try:
        result = await _alert.scan()
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.get("/api/logistics-ai/alerts",
            tags=["智运AI智能物流大模型"])
async def alerts_list(x_role: str = Header(None, alias="X-Role"),
                      role: str = Query(None),
                      status: str = Query(None)):
    """提醒列表(可按角色/状态过滤)"""
    _require_admin(x_role)
    try:
        result = await _alert.alerts(role=role, status=status)
        return {"success": True, "data": result, "count": len(result)}
    except Exception as e:
        _handle(e)


class AlertAckRequest(PydBaseModel):
    disposition: str = Field("acked", description="acked/dismissed")


@router.post("/api/logistics-ai/alerts/{alert_id}/ack",
             tags=["智运AI智能物流大模型"])
async def alert_ack(alert_id: int, data: AlertAckRequest,
                    x_role: str = Header(None, alias="X-Role")):
    """提醒确认闭环(驳回记负样本留痕)"""
    _require_admin(x_role)
    try:
        result = await _alert.ack(alert_id, data.disposition)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


# ============================================================
# 智酿运通 P7: 进化引擎 2.0(admin)
# ============================================================

class PreferenceRequest(PydBaseModel):
    memberId: int = Field(..., ge=1)
    scope: str = Field(..., description="b2b/b2c")
    prefs: dict = Field(..., description="偏好键值(白名单校验)")
    note: str = Field("", max_length=200)


@router.post("/api/logistics-ai/evolution2/preference",
             tags=["智运AI智能物流大模型"])
async def evolution2_preference(
        data: PreferenceRequest,
        x_role: str = Header(None, alias="X-Role")):
    """偏好登记(B端特殊要求/C端收货习惯, 同键合并更新)"""
    _require_admin(x_role)
    try:
        result = await _evo2.save_preference(
            member_id=data.memberId, scope=data.scope,
            prefs=data.prefs, note=data.note)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.get("/api/logistics-ai/evolution2/preferences",
            tags=["智运AI智能物流大模型"])
async def evolution2_preferences(
        x_role: str = Header(None, alias="X-Role"),
        scope: str = Query(None)):
    """偏好档案列表(可按 b2b/b2c 过滤)"""
    _require_admin(x_role)
    try:
        result = await _evo2.preferences(scope=scope)
        return {"success": True, "data": result, "count": len(result)}
    except Exception as e:
        _handle(e)


@router.get("/api/logistics-ai/evolution2/anomaly-patterns",
            tags=["智运AI智能物流大模型"])
async def evolution2_patterns(
        x_role: str = Header(None, alias="X-Role")):
    """异常模式识别(渠道×类型聚类→预防性规则建议)"""
    _require_admin(x_role)
    try:
        result = await _evo2.anomaly_patterns()
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.get("/api/logistics-ai/evolution2/carbon",
            tags=["智运AI智能物流大模型"])
async def evolution2_carbon(x_role: str = Header(None, alias="X-Role")):
    """碳足迹总览(排放因子法+渠道占比+绿色切换建议)"""
    _require_admin(x_role)
    try:
        result = await _evo2.carbon_overview()
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.post("/api/logistics-ai/evolution2/suggest",
             tags=["智运AI智能物流大模型"])
async def evolution2_suggest(x_role: str = Header(None, alias="X-Role")):
    """生成策略建议(熔断+碳排+异常模式聚合, 人工裁决)"""
    _require_admin(x_role)
    try:
        result = await _evo2.suggest()
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.get("/api/logistics-ai/evolution2/suggestions",
            tags=["智运AI智能物流大模型"])
async def evolution2_suggestions(
        x_role: str = Header(None, alias="X-Role"),
        status: str = Query(None)):
    """策略建议列表(可按状态过滤)"""
    _require_admin(x_role)
    try:
        result = await _evo2.suggestions(status=status)
        return {"success": True, "data": result, "count": len(result)}
    except Exception as e:
        _handle(e)


class SuggestionDecideRequest(PydBaseModel):
    verdict: str = Field(..., description="adopted/rejected")


@router.post("/api/logistics-ai/evolution2/suggestions/{sid}/decide",
             tags=["智运AI智能物流大模型"])
async def evolution2_decide(sid: int, data: SuggestionDecideRequest,
                            x_role: str = Header(None, alias="X-Role")):
    """人机协同裁决(采纳生效留痕/拒绝记负样本回流)"""
    _require_admin(x_role)
    try:
        result = await _evo2.decide(sid, data.verdict)
        return {"success": True, "data": result}
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
