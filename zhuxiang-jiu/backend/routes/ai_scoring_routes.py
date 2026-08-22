"""AI 语义评分层路由(5 端点)

为 5 个高落差模块(04订单/05收款/06物流/11流量/29推广码)暴露 AI 评分接口。

鉴权:
    - 全部为内部/管理端能力: X-Role: admin 头(与各模块管理端约定一致)

端点(均为 POST, 输入评分上下文, 输出评分结果):
    POST /api/ai-scoring/order-risk           订单风控评分
    POST /api/ai-scoring/payment-routing      支付路由评分(推荐渠道)
    POST /api/ai-scoring/logistics-routing    物流路由评分(推荐承运商)
    POST /api/ai-scoring/traffic-antifraud    流量防作弊评分
    POST /api/ai-scoring/promotion-antifraud  推广码防作弊评分
"""

from typing import Optional

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel as PydBaseModel, Field

from services.ai_scoring_service import (
    OrderRiskScorer, PaymentRoutingScorer, LogisticsRoutingScorer,
    TrafficAntiFraudScorer, PromotionAntiFraudScorer,
)

router = APIRouter()

_order_scorer = OrderRiskScorer()
_payment_scorer = PaymentRoutingScorer()
_logistics_scorer = LogisticsRoutingScorer()
_traffic_scorer = TrafficAntiFraudScorer()
_promotion_scorer = PromotionAntiFraudScorer()


# ============================================================
# 鉴权与异常映射(对齐项目约定)
# ============================================================

def _require_admin(x_role: Optional[str]):
    if x_role != "admin":
        raise HTTPException(status_code=403, detail="需要管理员权限")


def _handle(exc: Exception):
    """ValueError → 409, 其余 → 500"""
    if isinstance(exc, ValueError):
        raise HTTPException(status_code=409, detail=str(exc))
    raise HTTPException(status_code=500, detail=str(exc))


# ============================================================
# 请求模型
# ============================================================

class OrderRiskRequest(PydBaseModel):
    bambooScore: Optional[float] = Field(None, ge=0, le=1000, description="竹信分")
    registerHours: Optional[float] = Field(None, ge=0, description="注册至今年时数")
    orderAmount: float = Field(..., ge=0, description="订单金额")
    totalQuantity: Optional[int] = Field(None, ge=0, description="总件数")
    historyOrders: Optional[int] = Field(None, ge=0, description="历史订单数")
    historyCancels: Optional[int] = Field(None, ge=0, description="历史取消数")
    addressComplete: Optional[bool] = Field(None, description="地址完整性")
    remark: Optional[str] = Field(None, max_length=200, description="订单备注")
    orderHour: Optional[int] = Field(None, ge=0, le=23, description="下单小时(0-23)")


class ChannelInput(PydBaseModel):
    channelCode: str = Field(..., description="渠道编码")
    channelType: Optional[str] = Field(None, description="渠道类型")
    feeRate: Optional[float] = Field(None, ge=0, description="费率")
    fixedFee: Optional[float] = Field(None, ge=0, description="固定手续费")
    minAmount: Optional[float] = Field(None, ge=0, description="单笔下限")
    maxAmount: Optional[float] = Field(None, ge=0, description="单笔上限")
    dailyLimit: Optional[float] = Field(None, ge=0, description="日累计限额")
    dailyAmount: Optional[float] = Field(None, ge=0, description="日已用金额")
    status: Optional[str] = Field(None, description="渠道状态")


class PaymentRoutingRequest(PydBaseModel):
    amount: float = Field(..., gt=0, description="实付金额")
    sceneType: str = Field("order_pay", description="场景类型")
    channels: Optional[list[ChannelInput]] = Field(None, description="候选渠道(缺省用内置画像)")


class LogisticsRoutingRequest(PydBaseModel):
    weight: float = Field(..., gt=0, description="重量 kg")
    pieceCount: Optional[int] = Field(None, ge=1, description="件数")
    insuredValue: Optional[float] = Field(None, ge=0, description="保价金额")
    settleMode: Optional[str] = Field(None, description="结算模式 monthly/cash/prepaid")
    sameCity: Optional[bool] = Field(None, description="是否同城")
    budget: Optional[str] = Field(None, description="策略偏好 speed/cost/balanced")
    serviceType: Optional[str] = Field(None, description="服务类型 standard/express")


class TrafficAntiFraudRequest(PydBaseModel):
    promoterId: Optional[int] = Field(None, description="推广员ID")
    recentCount: Optional[int] = Field(None, ge=0, description="近1小时引流数")
    avgIntervalSeconds: Optional[float] = Field(None, ge=0, description="平均引流间隔秒")
    newAccountRatio: Optional[float] = Field(None, ge=0, le=1, description="新账号占比")
    nightRatio: Optional[float] = Field(None, ge=0, le=1, description="凌晨占比")
    conversionRate: Optional[float] = Field(None, ge=0, le=1, description="转化率")
    uniqueSources: Optional[int] = Field(None, ge=0, description="唯一来源数")
    totalRecords: Optional[int] = Field(None, ge=0, description="总记录数")
    effectiveRecords: Optional[int] = Field(None, ge=0, description="有效记录数")
    fraudCount: Optional[int] = Field(None, ge=0, description="历史作弊次数")


class PromotionAntiFraudRequest(PydBaseModel):
    promoterId: Optional[int] = Field(None, description="推广员ID")
    relationCount: Optional[int] = Field(None, ge=0, description="下级绑定数")
    avgBindToRewardHours: Optional[float] = Field(None, ge=0, description="绑定到领奖平均时长(小时)")
    inactiveInviteeRatio: Optional[float] = Field(None, ge=0, le=1, description="僵尸下级占比")
    nightBindRatio: Optional[float] = Field(None, ge=0, le=1, description="凌晨绑定占比")
    fastestHundredDays: Optional[int] = Field(None, ge=0, description="最快百人天数")
    selfLoopSuspect: Optional[bool] = Field(None, description="疑似环/自绑")
    revokedCount: Optional[int] = Field(None, ge=0, description="历史撤销次数")
    appealCount: Optional[int] = Field(None, ge=0, description="申诉次数")


# ============================================================
# 端点
# ============================================================

@router.post("/api/ai-scoring/order-risk", tags=["AI语义评分层"])
async def score_order_risk(data: OrderRiskRequest,
                           x_role: Optional[str] = Header(None, alias="X-Role")):
    """订单风控评分(模块04): 8因子 → 风险分 → pass/review/block"""
    _require_admin(x_role)
    try:
        return await _order_scorer.score(data.model_dump(exclude_none=True))
    except Exception as exc:
        _handle(exc)


@router.post("/api/ai-scoring/payment-routing", tags=["AI语义评分层"])
async def score_payment_routing(data: PaymentRoutingRequest,
                                x_role: Optional[str] = Header(None, alias="X-Role")):
    """支付路由评分(模块05): 5因子/渠道 → 适配分 → 推荐渠道"""
    _require_admin(x_role)
    try:
        ctx = data.model_dump(exclude_none=True)
        if ctx.get("channels"):
            ctx["channels"] = [c.model_dump(exclude_none=True) for c in data.channels]
        return await _payment_scorer.score(ctx)
    except Exception as exc:
        _handle(exc)


@router.post("/api/ai-scoring/logistics-routing", tags=["AI语义评分层"])
async def score_logistics_routing(data: LogisticsRoutingRequest,
                                  x_role: Optional[str] = Header(None, alias="X-Role")):
    """物流路由评分(模块06): 6因子/承运商 → 适配分 → 推荐承运商"""
    _require_admin(x_role)
    try:
        return await _logistics_scorer.score(data.model_dump(exclude_none=True))
    except Exception as exc:
        _handle(exc)


@router.post("/api/ai-scoring/traffic-antifraud", tags=["AI语义评分层"])
async def score_traffic_antifraud(data: TrafficAntiFraudRequest,
                                  x_role: Optional[str] = Header(None, alias="X-Role")):
    """流量防作弊评分(模块11): 7因子 → 作弊分 → pass/review/block"""
    _require_admin(x_role)
    try:
        return await _traffic_scorer.score(data.model_dump(exclude_none=True))
    except Exception as exc:
        _handle(exc)


@router.post("/api/ai-scoring/promotion-antifraud", tags=["AI语义评分层"])
async def score_promotion_antifraud(data: PromotionAntiFraudRequest,
                                    x_role: Optional[str] = Header(None, alias="X-Role")):
    """推广码防作弊评分(模块29): 6因子 → 作弊分 → pay/hold/review"""
    _require_admin(x_role)
    try:
        return await _promotion_scorer.score(data.model_dump(exclude_none=True))
    except Exception as exc:
        _handle(exc)


def register_ai_scoring_routes(app) -> None:
    """注册 AI 语义评分层路由"""
    app.include_router(router)
