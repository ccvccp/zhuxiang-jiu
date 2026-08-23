"""AI 语义评分层·第二批路由(8 端点)

为 8 个剩余 B 级模块(02会员/03积分/08信息/12钱包/14团购/17后台/18合同/19财务)
暴露 AI 评分接口, 与第一批(ai_scoring_routes)同构。

鉴权:
    - 全部为内部/管理端能力: X-Role: admin 头(与各模块管理端约定一致)

端点(均为 POST, 输入评分上下文, 输出评分结果):
    POST /api/ai-scoring/member-profile     会员智能画像评分
    POST /api/ai-scoring/points-risk        积分防薅羊毛评分
    POST /api/ai-scoring/message-content    信息内容审核评分
    POST /api/ai-scoring/withdraw-risk      提现风控评分
    POST /api/ai-scoring/groupbuy-qualify   团购资格评分
    POST /api/ai-scoring/admin-operation    后台操作风险评分
    POST /api/ai-scoring/agreement-risk     合同条款风险评分
    POST /api/ai-scoring/finance-anomaly    财务异常检测评分
"""

from typing import Optional

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel as PydBaseModel, Field

from services.ai_scoring_ext_service import (
    MemberProfileScorer, PointsRiskScorer, MessageContentScorer,
    WithdrawRiskScorer, GroupbuyQualifyScorer, AdminOperationScorer,
    AgreementRiskScorer, FinanceAnomalyScorer,
)

router = APIRouter()

_profile_scorer = MemberProfileScorer()
_points_scorer = PointsRiskScorer()
_message_scorer = MessageContentScorer()
_withdraw_scorer = WithdrawRiskScorer()
_groupbuy_scorer = GroupbuyQualifyScorer()
_admin_scorer = AdminOperationScorer()
_agreement_scorer = AgreementRiskScorer()
_finance_scorer = FinanceAnomalyScorer()


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

class MemberProfileRequest(PydBaseModel):
    profileFieldCount: Optional[int] = Field(None, ge=0, description="已填资料字段数")
    profileFieldTotal: Optional[int] = Field(None, ge=1, description="总资料字段数")
    accountAgeDays: Optional[float] = Field(None, ge=0, description="账户年龄(天)")
    monthlyLogins: Optional[int] = Field(None, ge=0, description="月登录次数")
    monthlyConsumption: Optional[float] = Field(None, ge=0, description="月消费金额")
    repurchaseRate: Optional[float] = Field(None, ge=0, le=1, description="复购率")
    refundRate: Optional[float] = Field(None, ge=0, le=1, description="退款率")
    bambooScore: Optional[float] = Field(None, ge=0, le=1000, description="竹信分")


class PointsRiskRequest(PydBaseModel):
    todayEarned: Optional[float] = Field(None, ge=0, description="当日获取积分")
    dailyEarnCap: Optional[float] = Field(None, ge=1, description="日获取上限")
    dailyRedeemCount: Optional[int] = Field(None, ge=0, description="当日兑换次数")
    singleChannelRatio: Optional[float] = Field(None, ge=0, le=1, description="单渠道获取占比")
    sameDeviceAccounts: Optional[int] = Field(None, ge=1, description="同设备关联账号数")
    violationCount: Optional[int] = Field(None, ge=0, description="历史违规次数")
    nightActionRatio: Optional[float] = Field(None, ge=0, le=1, description="凌晨操作占比")


class MessageContentRequest(PydBaseModel):
    content: str = Field(..., min_length=1, max_length=5000, description="消息内容")
    sensitiveHitCount: Optional[int] = Field(None, ge=0, description="敏感词命中数(缺省自动识别)")
    linkCount: Optional[int] = Field(None, ge=0, description="链接数量(缺省自动统计)")
    duplicateRatio: Optional[float] = Field(None, ge=0, le=1, description="与历史内容重复度")
    hourlySendCount: Optional[int] = Field(None, ge=0, description="当小时发送数")
    sendHour: Optional[int] = Field(None, ge=0, le=23, description="发送小时(0-23)")


class WithdrawRiskRequest(PydBaseModel):
    amount: float = Field(..., gt=0, description="提现金额")
    balance: float = Field(..., ge=0, description="可用余额")
    monthlyWithdrawCount: Optional[int] = Field(None, ge=0, description="当月提现次数")
    accountAgeDays: Optional[float] = Field(None, ge=0, description="账户年龄(天)")
    abnormalIncomeRatio: Optional[float] = Field(None, ge=0, le=1, description="异常收益占比")
    rejectedCount: Optional[int] = Field(None, ge=0, description="历史驳回次数")
    accountFrozen: Optional[bool] = Field(None, description="账户是否冻结")
    identityVerified: Optional[bool] = Field(None, description="是否实名")


class GroupbuyQualifyRequest(PydBaseModel):
    qualificationDocs: Optional[int] = Field(None, ge=0, le=10, description="资质材料数(0-5)")
    annualPurchaseAmount: Optional[float] = Field(None, ge=0, description="年采购金额")
    onTimePaymentRatio: Optional[float] = Field(None, ge=0, le=1, description="按期付款率")
    violationCount: Optional[int] = Field(None, ge=0, description="历史违约次数")
    targetQuantity: int = Field(..., ge=1, description="意向团购数量(件)")


class AdminOperationRequest(PydBaseModel):
    operationType: str = Field(..., min_length=1, description="操作类型")
    operationHour: Optional[int] = Field(None, ge=0, le=23, description="操作小时(0-23)")
    isWeekend: Optional[bool] = Field(None, description="是否周末")
    operationsLast10Min: Optional[int] = Field(None, ge=0, description="近10分钟操作数")
    operatesOnSelf: Optional[bool] = Field(None, description="是否操作自身账号")
    hasSecondReviewer: Optional[bool] = Field(None, description="是否已有第二复核人")


class AgreementRiskRequest(PydBaseModel):
    exemptionClauseCount: Optional[int] = Field(None, ge=0, description="免责条款数量")
    penaltyRatio: Optional[float] = Field(None, ge=0, le=10, description="违约金占比")
    unilateralClauseCount: Optional[int] = Field(None, ge=0, description="单方权利条款数")
    jurisdictionType: Optional[str] = Field(None, description="管辖类型")
    presentKeyClauses: Optional[list[str]] = Field(None, description="已含关键条款")
    missingKeyClauses: Optional[list[str]] = Field(None, description="缺失关键条款(显式)")


class FinanceAnomalyRequest(PydBaseModel):
    amount: float = Field(..., ge=0, description="本笔金额")
    accountAverageAmount: Optional[float] = Field(None, ge=0, description="科目均值")
    summaryMatchScore: Optional[float] = Field(None, ge=0, le=100, description="摘要匹配分")
    entryHour: Optional[int] = Field(None, ge=0, le=23, description="记账小时(0-23)")
    isWeekend: Optional[bool] = Field(None, description="是否周末")
    entriesToday: Optional[int] = Field(None, ge=0, description="当日凭证数")
    dailyAverageEntries: Optional[int] = Field(None, ge=0, description="日均凭证数")
    unbalanceAmount: Optional[float] = Field(None, ge=0, description="借贷不平衡差额")


# ============================================================
# 端点
# ============================================================

@router.post("/api/ai-scoring/member-profile", tags=["AI语义评分层"])
async def score_member_profile(data: MemberProfileRequest,
                               x_role: Optional[str] = Header(None, alias="X-Role")):
    """会员智能画像评分(模块02): 7因子 → 价值分 → high_value/standard/at_risk"""
    _require_admin(x_role)
    try:
        return await _profile_scorer.score(data.model_dump(exclude_none=True))
    except Exception as exc:
        _handle(exc)


@router.post("/api/ai-scoring/points-risk", tags=["AI语义评分层"])
async def score_points_risk(data: PointsRiskRequest,
                            x_role: Optional[str] = Header(None, alias="X-Role")):
    """积分防薅羊毛评分(模块03): 6因子 → 风险分 → pass/review/block"""
    _require_admin(x_role)
    try:
        return await _points_scorer.score(data.model_dump(exclude_none=True))
    except Exception as exc:
        _handle(exc)


@router.post("/api/ai-scoring/message-content", tags=["AI语义评分层"])
async def score_message_content(data: MessageContentRequest,
                                x_role: Optional[str] = Header(None, alias="X-Role")):
    """信息内容审核评分(模块08): 6因子 → 风险分 → pass/review/reject"""
    _require_admin(x_role)
    try:
        return await _message_scorer.score(data.model_dump(exclude_none=True))
    except Exception as exc:
        _handle(exc)


@router.post("/api/ai-scoring/withdraw-risk", tags=["AI语义评分层"])
async def score_withdraw_risk(data: WithdrawRiskRequest,
                              x_role: Optional[str] = Header(None, alias="X-Role")):
    """提现风控评分(模块12): 6因子 → 风险分 → auto_approve/manual_review/freeze"""
    _require_admin(x_role)
    try:
        return await _withdraw_scorer.score(data.model_dump(exclude_none=True))
    except Exception as exc:
        _handle(exc)


@router.post("/api/ai-scoring/groupbuy-qualify", tags=["AI语义评分层"])
async def score_groupbuy_qualify(data: GroupbuyQualifyRequest,
                                 x_role: Optional[str] = Header(None, alias="X-Role")):
    """团购资格评分(模块14): 5因子 → 资格分 → T3/T2/T1/rejected"""
    _require_admin(x_role)
    try:
        return await _groupbuy_scorer.score(data.model_dump(exclude_none=True))
    except Exception as exc:
        _handle(exc)


@router.post("/api/ai-scoring/admin-operation", tags=["AI语义评分层"])
async def score_admin_operation(data: AdminOperationRequest,
                                x_role: Optional[str] = Header(None, alias="X-Role")):
    """后台操作风险评分(模块17): 5因子 → 风险分 → allow/confirm_2fa/block"""
    _require_admin(x_role)
    try:
        return await _admin_scorer.score(data.model_dump(exclude_none=True))
    except Exception as exc:
        _handle(exc)


@router.post("/api/ai-scoring/agreement-risk", tags=["AI语义评分层"])
async def score_agreement_risk(data: AgreementRiskRequest,
                               x_role: Optional[str] = Header(None, alias="X-Role")):
    """合同条款风险评分(模块18): 5因子 → 风险分 → low/medium/high + 修订建议"""
    _require_admin(x_role)
    try:
        return await _agreement_scorer.score(data.model_dump(exclude_none=True))
    except Exception as exc:
        _handle(exc)


@router.post("/api/ai-scoring/finance-anomaly", tags=["AI语义评分层"])
async def score_finance_anomaly(data: FinanceAnomalyRequest,
                                x_role: Optional[str] = Header(None, alias="X-Role")):
    """财务异常检测评分(模块19): 5因子 → 异常分 → normal/attention/alert"""
    _require_admin(x_role)
    try:
        return await _finance_scorer.score(data.model_dump(exclude_none=True))
    except Exception as exc:
        _handle(exc)


def register_ai_scoring_ext_routes(app) -> None:
    """注册 AI 语义评分层·第二批路由"""
    app.include_router(router)
