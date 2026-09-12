"""智单·AI智能订单大模型路由(P0-P3 全量, 20 端点)

「订单管理」模块叠加式升级: 智单·AI智能订单大模型(订单域
只读智能中枢)。鉴权: 全部管理端 X-Role: admin(订单数据敏感域)。
既有 /api/order/* 17 端点保留零改动(叠加式升级)。

端点分布:
    - 织物底座: status / overview / orders
    - P0 洞察中枢: qa / checkup / checkups / portrait / portraits
    - P1 预测沙盘: eta / whatif / forecast
    - P2 退款裁决与风险: refund-score/{orderId} / anomaly-scan
                         / anomalies
    - P3 进化闭环: feedback / feedbacks / params / detect
                   / memo / memos

铁律: 确定性规则引擎(LLM 禁入判定链) / 裁决处置一律建议书
(永不自动执行) / 预测评分类响应带 formula 推理链。

异常映射: KeyError → 404 / ValueError → 409
"""

from fastapi import APIRouter, Header, HTTPException, Query
from pydantic import BaseModel as PydBaseModel, Field

from repositories.backend import is_redis_mode
from services.zd_fabric_service import _ZdStore, ZdFabricService
from services.zd_insight_service import ZdInsightService
from services.zd_forecast_service import ZdForecastService
from services.zd_risk_service import ZdRiskService
from services.zd_evolution_service import ZdEvolutionService

router = APIRouter()
_store = _ZdStore()
_fabric = ZdFabricService(store=_store)
_insight = ZdInsightService(fabric=_fabric, store=_store)
_forecast = ZdForecastService(fabric=_fabric, store=_store)
_risk = ZdRiskService(fabric=_fabric, store=_store)
_evo = ZdEvolutionService(fabric=_fabric, store=_store)


def _require_admin(x_role: str | None):
    if x_role != "admin":
        raise HTTPException(status_code=403, detail="需要管理员权限")


def _handle(exc: Exception):
    if isinstance(exc, KeyError):
        msg = str(exc) if str(exc) else "资源不存在"
        if msg.startswith("'") and msg.endswith("'"):
            msg = msg[1:]
        raise HTTPException(status_code=404, detail=msg)
    if isinstance(exc, ValueError):
        raise HTTPException(status_code=409, detail=str(exc))
    raise HTTPException(status_code=500, detail=str(exc))


# ============================================================
# 请求模型
# ============================================================

class QARequest(PydBaseModel):
    text: str = Field(..., min_length=1, max_length=200,
                      description="自然语言订单问题")


class WhatIfRequest(PydBaseModel):
    shipDelayDays: float = Field(0.0, description="发货延迟天数 [0,30]")
    cancelRateDelta: float = Field(
        0.0, description="取消率变动(0.1=+10%, ±50% 内)")
    aovDelta: float = Field(
        0.0, description="客单价变动(0.1=+10%, ±50% 内)")


class FeedbackRequest(PydBaseModel):
    targetType: str = Field(..., description="eta_forecast/"
                                         "volume_forecast/whatif/"
                                         "refund_score/anomaly_scan/"
                                         "checkup/portrait")
    verdict: str = Field(..., description="adopted|corrected|rejected")
    note: str = Field("", max_length=200)


class MemoRequest(PydBaseModel):
    topic: str = Field(..., description="promotion_prep|timeout_policy")
    notes: str = Field("", max_length=200)


# ============================================================
# 织物底座(admin)
# ============================================================

@router.get("/api/order-ai/status", tags=["智单AI智能订单大模型"])
async def zd_status(x_role: str = Header(None, alias="X-Role")):
    """模块状态(五域服务视图 + 宪法铁律声明)"""
    _require_admin(x_role)
    try:
        ov = await _fabric.overview()
        return {"success": True, "data": {
            "module": "智单·AI智能订单大模型",
            "version": "1.0.0",
            "storeMode": "redis" if is_redis_mode() else "memory",
            "domains": [
                {"code": "fabric", "name": "数据织物",
                 "endpoints": ["overview", "orders"]},
                {"code": "p0", "name": "洞察中枢",
                 "endpoints": ["qa", "checkup", "portrait"]},
                {"code": "p1", "name": "预测沙盘",
                 "endpoints": ["eta", "whatif", "forecast"]},
                {"code": "p2", "name": "退款裁决与风险",
                 "endpoints": ["refund-score", "anomaly-scan",
                               "anomalies"]},
                {"code": "p3", "name": "进化闭环",
                 "endpoints": ["feedback", "params", "detect", "memo"]},
            ],
            "fabric": {"totalOrders": ov["totalOrders"],
                       "gmv": ov["gmv"],
                       "dailyDays": len(ov["dailySeries"])},
            "constitution": [
                "确定性规则引擎(LLM 禁入判定链, 同输入同输出)",
                "裁决/处置一律建议书(永不自动执行)",
                "预测/评分类响应带 formula 推理链留痕",
                "数字 100% 来自查询层(数字不出现在模板层)",
            ],
        }}
    except Exception as e:
        _handle(e)


@router.get("/api/order-ai/overview", tags=["智单AI智能订单大模型"])
async def zd_overview(x_role: str = Header(None, alias="X-Role")):
    """织物总览(九态/GMV/退款率/客单价/履约/三维聚合/日时序)"""
    _require_admin(x_role)
    try:
        return {"success": True,
                "data": await _fabric.overview()}
    except Exception as e:
        _handle(e)


@router.get("/api/order-ai/orders", tags=["智单AI智能订单大模型"])
async def zd_orders(x_role: str = Header(None, alias="X-Role"),
                    limit: int = Query(500, ge=1, le=2000)):
    """织物订单列表(只读, createdAt 倒序; 空数据诚实返回 [])"""
    _require_admin(x_role)
    try:
        rows = await _fabric.orders(limit=limit)
        return {"success": True, "data": rows, "count": len(rows)}
    except Exception as e:
        _handle(e)


# ============================================================
# P0: 洞察中枢(admin)
# ============================================================

@router.post("/api/order-ai/qa", tags=["智单AI智能订单大模型"])
async def zd_qa(data: QARequest,
                x_role: str = Header(None, alias="X-Role")):
    """自然语言订单问答(五域关键词路由→确定性查询→模板拼接)"""
    _require_admin(x_role)
    try:
        result = await _insight.qa(data.text)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.get("/api/order-ai/checkup", tags=["智单AI智能订单大模型"])
async def zd_checkup(x_role: str = Header(None, alias="X-Role")):
    """订单健康体检(四维确定性打分, 建议书)"""
    _require_admin(x_role)
    try:
        return {"success": True, "data": await _insight.checkup()}
    except Exception as e:
        _handle(e)


@router.get("/api/order-ai/checkups", tags=["智单AI智能订单大模型"])
async def zd_checkups(x_role: str = Header(None, alias="X-Role"),
                      limit: int = Query(50, ge=1, le=200)):
    """体检报告历史留痕"""
    _require_admin(x_role)
    try:
        rows = await _insight.checkups(limit=limit)
        return {"success": True, "data": rows, "count": len(rows)}
    except Exception as e:
        _handle(e)


@router.get("/api/order-ai/portrait", tags=["智单AI智能订单大模型"])
async def zd_portrait(x_role: str = Header(None, alias="X-Role")):
    """会员×商品×时段三维画像(top 榜 + 时段分布)"""
    _require_admin(x_role)
    try:
        return {"success": True, "data": await _insight.portrait()}
    except Exception as e:
        _handle(e)


@router.get("/api/order-ai/portraits", tags=["智单AI智能订单大模型"])
async def zd_portraits(x_role: str = Header(None, alias="X-Role"),
                       limit: int = Query(50, ge=1, le=200)):
    """画像快照历史留痕"""
    _require_admin(x_role)
    try:
        rows = await _insight.portraits(limit=limit)
        return {"success": True, "data": rows, "count": len(rows)}
    except Exception as e:
        _handle(e)


# ============================================================
# P1: 预测沙盘(admin)
# ============================================================

@router.get("/api/order-ai/eta", tags=["智单AI智能订单大模型"])
async def zd_eta(x_role: str = Header(None, alias="X-Role")):
    """履约 ETA 加权预测(近3单×0.6 + 全期×0.4; 样本0诚实 None)"""
    _require_admin(x_role)
    try:
        return {"success": True, "data": await _forecast.eta_forecast()}
    except Exception as e:
        _handle(e)


@router.post("/api/order-ai/whatif", tags=["智单AI智能订单大模型"])
async def zd_whatif(data: WhatIfRequest,
                    x_role: str = Header(None, alias="X-Role")):
    """三维 What-if 推演(发货延迟/取消率/客单价, 建议书)"""
    _require_admin(x_role)
    try:
        result = await _forecast.whatif(
            ship_delay_days=data.shipDelayDays,
            cancel_rate_delta=data.cancelRateDelta,
            aov_delta=data.aovDelta)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.get("/api/order-ai/forecast", tags=["智单AI智能订单大模型"])
async def zd_forecast(x_role: str = Header(None, alias="X-Role"),
                      periods: int = Query(12, ge=1, le=24)):
    """日单量滚动预测(加权移动平均 + 趋势外推, 确定性)"""
    _require_admin(x_role)
    try:
        result = await _forecast.volume_forecast(periods=periods)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


# ============================================================
# P2: 退款裁决与风险(admin)
# ============================================================

@router.get("/api/order-ai/refund-score/{order_id}",
            tags=["智单AI智能订单大模型"])
async def zd_refund_score(order_id: str,
                          x_role: str = Header(None, alias="X-Role")):
    """退款裁决评分(四因子加权, 建议书永不自动执行)"""
    _require_admin(x_role)
    try:
        result = await _risk.refund_score(order_id)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.post("/api/order-ai/anomaly-scan",
             tags=["智单AI智能订单大模型"])
async def zd_anomaly_scan(x_role: str = Header(None, alias="X-Role")):
    """三类异常订单扫描(高频/囤货/秒退款, 建议书不拦截)"""
    _require_admin(x_role)
    try:
        return {"success": True, "data": await _risk.anomaly_scan()}
    except Exception as e:
        _handle(e)


@router.get("/api/order-ai/anomalies", tags=["智单AI智能订单大模型"])
async def zd_anomalies(x_role: str = Header(None, alias="X-Role"),
                       limit: int = Query(50, ge=1, le=200)):
    """历史异常记录列表(留痕)"""
    _require_admin(x_role)
    try:
        rows = await _risk.anomalies(limit=limit)
        return {"success": True, "data": rows, "count": len(rows)}
    except Exception as e:
        _handle(e)


# ============================================================
# P3: 进化闭环(admin)
# ============================================================

@router.post("/api/order-ai/feedback", tags=["智单AI智能订单大模型"])
async def zd_feedback(data: FeedbackRequest,
                      x_role: str = Header(None, alias="X-Role")):
    """反馈闭环(adopted/corrected/rejected → 参数权重学习)"""
    _require_admin(x_role)
    try:
        result = await _evo.feedback(
            target_type=data.targetType, verdict=data.verdict,
            note=data.note)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.get("/api/order-ai/feedbacks", tags=["智单AI智能订单大模型"])
async def zd_feedbacks(x_role: str = Header(None, alias="X-Role"),
                       limit: int = Query(50, ge=1, le=200)):
    """反馈留痕列表"""
    _require_admin(x_role)
    try:
        rows = await _evo.feedbacks(limit=limit)
        return {"success": True, "data": rows, "count": len(rows)}
    except Exception as e:
        _handle(e)


@router.get("/api/order-ai/params", tags=["智单AI智能订单大模型"])
async def zd_params(x_role: str = Header(None, alias="X-Role")):
    """进化参数视图(etaRecentWeight + 安全阀说明)"""
    _require_admin(x_role)
    try:
        return {"success": True, "data": await _evo.params()}
    except Exception as e:
        _handle(e)


@router.get("/api/order-ai/detect", tags=["智单AI智能订单大模型"])
async def zd_detect(x_role: str = Header(None, alias="X-Role")):
    """三检测器(单量 spike/PAID 停滞 drop/取消 surge, 确定性)"""
    _require_admin(x_role)
    try:
        return {"success": True, "data": await _evo.detect()}
    except Exception as e:
        _handle(e)


@router.post("/api/order-ai/memo", tags=["智单AI智能订单大模型"])
async def zd_memo(data: MemoRequest,
                  x_role: str = Header(None, alias="X-Role")):
    """决策备忘录(大促备货/超时策略, 数据插值+假设标注)"""
    _require_admin(x_role)
    try:
        result = await _evo.memo(topic=data.topic, notes=data.notes)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.get("/api/order-ai/memos", tags=["智单AI智能订单大模型"])
async def zd_memos(x_role: str = Header(None, alias="X-Role"),
                   limit: int = Query(50, ge=1, le=200)):
    """备忘录列表(留痕)"""
    _require_admin(x_role)
    try:
        rows = await _evo.memos(limit=limit)
        return {"success": True, "data": rows, "count": len(rows)}
    except Exception as e:
        _handle(e)


def register_zd_routes(app):
    """注册智单·AI智能订单大模型路由"""
    app.include_router(router)
