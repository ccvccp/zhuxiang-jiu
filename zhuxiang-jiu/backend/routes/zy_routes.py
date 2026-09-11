"""智启元·AI智能财务大模型路由(P0-P3 全量, 22 端点)

「财务管理」模块升级更名: 智启元·AI智能财务大模型。
鉴权: 全部管理端 X-Role: admin(财务数据敏感域)。

端点分布:
    - 问答与分析(P0):  qa / dupont / attribution / health / series
    - 预测与沙盘(P1):  forecast / sandbox / drivers
    - 税务优化(P2):    tax/simulate / tax/policies GET+POST / tax/risk-heatmap
    - 进化与决策(P3):  feedback / feedbacks / anomalies / cash-schedule
                        / decision-memo / memos / logs / params / status

异常映射(遵循项目约定):
    - KeyError → 404 / ValueError → 409 / PermissionError → 403
"""

from fastapi import APIRouter, Header, HTTPException, Query
from pydantic import BaseModel as PydBaseModel, Field

from services.zy_qa_service import ZyQAService
from services.zy_forecast_service import ZyForecastService
from services.zy_tax_service import ZyTaxService
from services.zy_evolution_service import ZyEvolutionService
from services.zy_data_service import ZyDataService


router = APIRouter()
_data = ZyDataService()
_qa = ZyQAService(data=_data)
_tax = ZyTaxService(data=_data)
_forecast = ZyForecastService(data=_data, tax=_tax)
_evolution = ZyEvolutionService(data=_data)


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

class QARequest(PydBaseModel):
    question: str = Field(..., min_length=2, max_length=200,
                         description="自然语言财务问题")


class SandboxRequest(PydBaseModel):
    priceDelta: float = Field(0.0, ge=-0.5, le=0.5,
                              description="售价变动(0.1=+10%)")
    volumeDelta: float = Field(0.0, ge=-0.5, le=0.5,
                                description="销量变动")
    costDelta: float = Field(0.0, ge=-0.5, le=0.5,
                             description="成本变动")


class TaxSimulateRequest(PydBaseModel):
    amount: float = Field(..., gt=0, le=100_000_000,
                          description="含税交易金额(元)")
    quantity: int = Field(10, ge=1, le=10_000,
                          description="白酒数量(斤)")
    structures: list = Field(None,
                             description="结构键(默认全量: standard/"
                                         "discount/bundle/cross_border)")


class PolicyAddRequest(PydBaseModel):
    title: str = Field(..., min_length=2, max_length=100)
    category: str = Field(..., description="vat|consumption_tax|income_tax")
    tags: list = Field(default_factory=list)
    content: str = Field(..., min_length=2, max_length=500)
    condition: str = Field("", max_length=200)
    savingFormula: str = Field("", max_length=200)
    effectiveFrom: str = Field("", max_length=10)
    effectiveTo: str = Field("", max_length=10)


class FeedbackRequest(PydBaseModel):
    targetType: str = Field(..., description="forecast|analysis|"
                                           "tax_suggestion|anomaly|"
                                           "cash_schedule")
    verdict: str = Field(..., description="adopted|corrected|rejected")
    note: str = Field("", max_length=200)
    correction: dict = Field(default_factory=dict)


class MemoRequest(PydBaseModel):
    type: str = Field("investment", description="investment|budget")
    params: dict = Field(default_factory=dict)


# ============================================================
# P0: 问答与分析中枢(admin)
# ============================================================

@router.post("/api/zy/qa", tags=["智启元AI智能财务大模型"])
async def qa(data: QARequest, x_role: str = Header(None, alias="X-Role")):
    """自然语言财务问答(意图路由→确定性查询→数字100%查询层)"""
    _require_admin(x_role)
    try:
        result = await _qa.answer(data.question)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.get("/api/zy/series", tags=["智启元AI智能财务大模型"])
async def monthly_series(
    x_role: str = Header(None, alias="X-Role"),
    months: int = Query(12, ge=1, le=24),
):
    """月度财务时序(收入/成本/税/净利, 图表数据源)"""
    _require_admin(x_role)
    try:
        rows = await _data.monthly_series(months=months)
        return {"success": True, "data": rows, "count": len(rows)}
    except Exception as e:
        _handle(e)


@router.get("/api/zy/dupont", tags=["智启元AI智能财务大模型"])
async def dupont(
    x_role: str = Header(None, alias="X-Role"),
    period: str = Query("", max_length=6,
                        description="YYYYMM(空=最新期)"),
):
    """杜邦分析(ROE = 净利率 × 周转率 × 权益乘数, 可解释)"""
    _require_admin(x_role)
    try:
        result = await _qa.dupont(period or None)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.get("/api/zy/attribution", tags=["智启元AI智能财务大模型"])
async def attribution(x_role: str = Header(None, alias="X-Role")):
    """净利环比归因(量/价/本/税四因素连环替代法)"""
    _require_admin(x_role)
    try:
        result = await _qa.attribution()
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.get("/api/zy/health", tags=["智启元AI智能财务大模型"])
async def health(x_role: str = Header(None, alias="X-Role")):
    """财务健康度五维评分(偿债/营运/盈利/成长/现金, Sigmoid)"""
    _require_admin(x_role)
    try:
        result = await _qa.health()
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


# ============================================================
# P1: 预测与情景沙盘(admin)
# ============================================================

@router.get("/api/zy/forecast", tags=["智启元AI智能财务大模型"])
async def rolling_forecast(
    x_role: str = Header(None, alias="X-Role"),
    horizon: int = Query(12, ge=1, le=12),
):
    """滚动预测(加权移动平均 0.6近期+0.4全期 + 趋势外推, 确定性)"""
    _require_admin(x_role)
    try:
        result = await _forecast.rolling_forecast(horizon=horizon)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.post("/api/zy/sandbox", tags=["智启元AI智能财务大模型"])
async def sandbox(data: SandboxRequest,
                  x_role: str = Header(None, alias="X-Role")):
    """What-if 情景沙盘(三维假设→多维影响推演+应对预案)"""
    _require_admin(x_role)
    try:
        result = await _forecast.sandbox(
            price_delta=data.priceDelta,
            volume_delta=data.volumeDelta,
            cost_delta=data.costDelta)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.get("/api/zy/drivers", tags=["智启元AI智能财务大模型"])
async def drivers(x_role: str = Header(None, alias="X-Role")):
    """收入驱动因素建模(订单数/销量/客单价/退款率 相关性排序)"""
    _require_admin(x_role)
    try:
        result = await _forecast.drivers()
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


# ============================================================
# P2: 税务优化引擎(admin)
# ============================================================

@router.post("/api/zy/tax/simulate", tags=["智启元AI智能财务大模型"])
async def tax_simulate(data: TaxSimulateRequest,
                       x_role: str = Header(None, alias="X-Role")):
    """交易级税负模拟器(四结构对比+最优建议, 留痕不自动变更)"""
    _require_admin(x_role)
    try:
        result = await _tax.simulate(
            amount=data.amount, quantity=data.quantity,
            structures=data.structures)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.get("/api/zy/tax/policies", tags=["智启元AI智能财务大模型"])
async def tax_policies(
    x_role: str = Header(None, alias="X-Role"),
    tags: str = Query("", description="业务标签逗号分隔(如 小微,白酒)"),
):
    """政策库+标签匹配(建议书模式, 永不自动享受)"""
    _require_admin(x_role)
    try:
        business_tags = [t.strip() for t in tags.split(",") if t.strip()]
        result = await _tax.policies(business_tags or None)
        return {"success": True, "data": result,
                "count": len(result.get("policies", []))}
    except Exception as e:
        _handle(e)


@router.post("/api/zy/tax/policies", tags=["智启元AI智能财务大模型"])
async def tax_policy_add(data: PolicyAddRequest,
                         x_role: str = Header(None, alias="X-Role")):
    """新增优惠政策(人工维护库, 生效期标记)"""
    _require_admin(x_role)
    try:
        from services.zy_tax_service import _PolicyStore
        store = _PolicyStore()
        policy_id = await store.next_policy_id()
        policy = {
            "policyId": policy_id, "title": data.title,
            "category": data.category, "tags": data.tags,
            "content": data.content, "condition": data.condition,
            "savingFormula": data.savingFormula,
            "effectiveFrom": data.effectiveFrom,
            "effectiveTo": data.effectiveTo,
        }
        await store.save_policy(policy)
        return {"success": True, "data": policy}
    except Exception as e:
        _handle(e)


@router.get("/api/zy/tax/risk-heatmap", tags=["智启元AI智能财务大模型"])
async def tax_risk_heatmap(x_role: str = Header(None, alias="X-Role")):
    """税务风险热力图(五维扫描→五级热力, 处置永不自动)"""
    _require_admin(x_role)
    try:
        result = await _tax.risk_heatmap()
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


# ============================================================
# P3: 自主进化与决策支持(admin)
# ============================================================

@router.post("/api/zy/evolution/feedback",
             tags=["智启元AI智能财务大模型"])
async def evolution_feedback(data: FeedbackRequest,
                            x_role: str = Header(None, alias="X-Role")):
    """反馈闭环(采纳/修正/拒绝→预测参数确定性调优, 安全阀内)"""
    _require_admin(x_role)
    try:
        result = await _evolution.feedback(
            target_type=data.targetType, verdict=data.verdict,
            note=data.note, correction=data.correction)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.get("/api/zy/evolution/feedbacks",
            tags=["智启元AI智能财务大模型"])
async def evolution_feedbacks(
    x_role: str = Header(None, alias="X-Role"),
    limit: int = Query(100, ge=1, le=500),
):
    """反馈记录列表(进化审计)"""
    _require_admin(x_role)
    try:
        result = await _evolution.feedbacks(limit=limit)
        return {"success": True, "data": result, "count": len(result)}
    except Exception as e:
        _handle(e)


@router.get("/api/zy/evolution/params", tags=["智启元AI智能财务大模型"])
async def evolution_params(x_role: str = Header(None, alias="X-Role")):
    """预测参数现状(trendWeight 进化态)"""
    _require_admin(x_role)
    try:
        result = await _evolution.get_forecast_params()
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.get("/api/zy/evolution/anomalies",
            tags=["智启元AI智能财务大模型"])
async def evolution_anomalies(x_role: str = Header(None, alias="X-Role")):
    """异常自发现(月度流三检测器: spike/drop/surge, 确定性)"""
    _require_admin(x_role)
    try:
        result = await _evolution.anomalies()
        return {"success": True, "data": result, "count": len(result)}
    except Exception as e:
        _handle(e)


@router.get("/api/zy/evolution/cash-schedule",
            tags=["智启元AI智能财务大模型"])
async def cash_schedule(
    x_role: str = Header(None, alias="X-Role"),
    days: int = Query(90, ge=7, le=90),
):
    """资金智能调度(90日逐日缺口推演+融资建议, 建议书模式)"""
    _require_admin(x_role)
    try:
        result = await _evolution.cash_schedule(days=days)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.post("/api/zy/evolution/decision-memo",
             tags=["智启元AI智能财务大模型"])
async def decision_memo(data: MemoRequest,
                        x_role: str = Header(None, alias="X-Role")):
    """决策备忘录(投资DCF+敏感性 / 预算调整, 确定性+假设标注)"""
    _require_admin(x_role)
    try:
        result = await _evolution.decision_memo(
            memo_type=data.type, params=data.params)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.get("/api/zy/evolution/memos", tags=["智启元AI智能财务大模型"])
async def list_memos(
    x_role: str = Header(None, alias="X-Role"),
    limit: int = Query(50, ge=1, le=200),
):
    """决策备忘录列表"""
    _require_admin(x_role)
    try:
        result = await _evolution.memos(limit=limit)
        return {"success": True, "data": result, "count": len(result)}
    except Exception as e:
        _handle(e)


@router.get("/api/zy/evolution/logs", tags=["智启元AI智能财务大模型"])
async def evolution_logs(
    x_role: str = Header(None, alias="X-Role"),
    engine: str = Query("", description="feedback|anomaly|memo"),
    limit: int = Query(100, ge=1, le=500),
):
    """进化日志(全量留痕, 月度审计)"""
    _require_admin(x_role)
    try:
        result = await _evolution.logs(engine=engine or None,
                                       limit=limit)
        return {"success": True, "data": result, "count": len(result)}
    except Exception as e:
        _handle(e)


@router.get("/api/zy/status", tags=["智启元AI智能财务大模型"])
async def status(x_role: str = Header(None, alias="X-Role")):
    """智启元总览(反馈进化态+异常+口径说明)"""
    _require_admin(x_role)
    try:
        result = await _evolution.status()
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


def register_zy_routes(app):
    """注册智启元·AI智能财务大模型路由"""
    app.include_router(router)
