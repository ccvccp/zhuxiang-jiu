"""69号·AI智能支付大模型 交易级授信服务
(pay69_credit_service, P3)

规划(docs/69号_AI智能支付大模型_创新规划方案.md
§4.5/§七 P3):
    ① 交易级实时授信评估(信值等级×
       现金流×履约统计×金额压力——四轴
       确定性查表, LLM 禁定价)
    ② 分期方案生成(利率规则表封闭
       ——期数×利率, 评级门控)
    ③ 调额建议书(proposed→admin 终审
       approved/rejected——资金域
       永不自动铁律, 60号 calibrate
       submit/apply 双模范式)
    ④ 还款/逾期/提前结清回流(快环
       统计——履约轴数据源)

铁律(规划 §1.2/§4.5):
    - 授信生效=建议书+admin 确认(资金
      域永不自动——调额建议书必经终审)
    - 利率档=确定性规则表(无 LLM 定价)
    - 45号 TV redeem 熔断链零改动
      (69号只是评估消费方)
"""

import logging

from core.helpers import ts

from repositories.pay69_repository import (
    Pay69Repository,
)
from services.pay69_registry import (
    ADJUSTMENT_STATES,
    CREDIT_BASE_LIMITS,
    CREDIT_FACTORS, CREDIT_GRADES,
    CREDIT_WEIGHTS,
    GRADE_INSTALLMENTS,
    INSTALLMENT_RATES,
    MODEL_VERSION,
)

logger = logging.getLogger("pay69_credit")

# 评级分档阈值(综合分→评级, 封闭)
GRADE_THRESHOLDS = (
    (0.80, "excellent"),
    (0.65, "good"),
    (0.50, "fair"),
    (0.35, "cautious"),
    (0.00, "rejected"),
)

# 信值轴分档(0-1)
TRUST_SCORES = {
    "S": 1.00, "A": 0.80, "B": 0.60,
    "C": 0.40, "D": 0.00,
    "trusted": 1.00, "standard": 0.60,
    "watched": 0.30, "restricted": 0.00,
}

# 现金流指数域(0-1, 外部传入——60号
# 预测或调用方口径; 默认 0.5 中性)
# 履约分: ontime×1.0+early×0.8-late×0.6
# 压力分: 1-amount/base_limit(截 0-1)

REPAY_EVENT_TYPES = (
    "ontime", "late", "early",
)


class Pay69CreditService:
    """69号交易级授信(P3)"""

    def __init__(self):
        self.repo = Pay69Repository()

    # ============================================================
    # 四轴计算(确定性——查表)
    # ============================================================

    def _trust_axis(self, trust_tier: str) -> float:
        """信值轴(未知档回退 0.3 保守)"""
        return TRUST_SCORES.get(
            str(trust_tier or ""), 0.3)

    def _cashflow_axis(
            self, cashflow_index: float) -> float:
        """现金流轴(0-1 外部指数, 截断)"""
        idx = float(cashflow_index
                    if cashflow_index is not None
                    else 0.5)
        return round(max(0.0, min(1.0, idx)), 4)

    def _repayment_axis(
            self, stats: dict) -> float:
        """履约轴(ontime×1.0+early×0.8
        -late×0.6, 截 0-1; 无记录=0.5
        中性——新会员不惩罚)"""
        if not stats or not any(
                stats.get(k)
                for k in REPAY_EVENT_TYPES):
            return 0.5
        ontime = int(stats.get("ontime", 0))
        early = int(stats.get("early", 0))
        late = int(stats.get("late", 0))
        total = ontime + early + late
        if total <= 0:
            return 0.5
        raw = (ontime * 1.0 + early * 0.8
               - late * 0.6) / total
        return round(max(0.0, min(1.0, raw)),
                     4)

    def _pressure_axis(
            self, amount: float,
            base_limit: float) -> float:
        """金额压力轴(交易级——金额占基础
        额度比; 超限=0, 小额=1)"""
        if base_limit <= 0:
            return 0.0
        ratio = float(amount) / base_limit
        return round(
            max(0.0, 1.0 - ratio), 4)

    @staticmethod
    def _grade_of(score: float) -> str:
        """综合分→评级(封闭查表)"""
        for cap, grade in GRADE_THRESHOLDS:
            if score >= cap:
                return grade
        return "rejected"

    # ============================================================
    # 交易级授信评估(决策面)
    # ============================================================

    async def evaluate_credit(
            self, member_id: int, amount: float,
            trust_tier: str = "",
            cashflow_index: float = 0.5,
            base_limit: float = None) -> dict:
        """交易级授信评估(四轴+评级+分期
        方案建议+留痕)

        base_limit: 现行额度(缺省取信值
        等级映射——调额生效后以生效值传入)

        Raises:
            ValueError: 金额非法/信值等级
            零额度(D/restricted 域内禁授信)
        """
        amount = round(float(amount or 0), 2)
        if amount <= 0:
            raise ValueError(
                f"金额非法: {amount}(须>0)")
        tier = str(trust_tier or "")
        limit = (float(base_limit)
                 if base_limit is not None
                 else CREDIT_BASE_LIMITS.get(
                     tier, 0.0))
        if limit <= 0:
            raise ValueError(
                f"信值等级 {tier or '(空)'} "
                f"基础额度为零——禁授信"
                f"(资金域保守铁律)")
        stats = await self.repo.get_repay_stats(
            member_id)
        axes = {
            "trust": self._trust_axis(tier),
            "cashflow": self._cashflow_axis(
                cashflow_index),
            "repayment": self._repayment_axis(
                stats),
            "pressure": self._pressure_axis(
                amount, limit),
        }
        score = round(sum(
            CREDIT_WEIGHTS[k] * axes[k]
            for k in CREDIT_FACTORS), 4)
        grade = self._grade_of(score)
        installments = self._installment_plans(
            grade, amount)
        approved = grade in (
            "excellent", "good", "fair")
        record = {
            "memberId": int(member_id or 0),
            "amount": amount,
            "trustTier": tier,
            "baseLimit": round(limit, 2),
            "axes": axes,
            "repayStats": {
                "ontime": stats.get("ontime", 0),
                "late": stats.get("late", 0),
                "early": stats.get("early", 0),
            },
            "score": score,
            "grade": grade,
            "creditApproved": approved,
            "installmentPlans": installments,
            "evaluatedAt": ts(),
        }
        await self.repo.save_credit(
            dict(record))
        await self.repo.save_event({
            "type": "credit_evaluated",
            "memberId": int(member_id or 0),
            "detail": {
                "amount": amount,
                "grade": grade,
                "approved": approved,
            },
            "at": ts(),
        })
        return record

    def _installment_plans(
            self, grade: str,
            amount: float) -> list[dict]:
        """分期方案生成(利率规则表——评级
        门控期数, 确定性查表)"""
        allowed = GRADE_INSTALLMENTS.get(
            grade, ())
        plans = []
        for periods in (3, 6, 12):
            if periods not in allowed:
                continue
            rate = INSTALLMENT_RATES[periods]
            interest = round(
                amount * rate, 2)
            per_installment = round(
                (amount + interest) / periods,
                2)
            plans.append({
                "periods": periods,
                "annualRate": rate,
                "totalInterest": interest,
                "perInstallment": per_installment,
                "totalRepay": round(
                    per_installment * periods, 2),
            })
        return plans

    # ============================================================
    # 调额建议书(资金域永不自动)
    # ============================================================

    async def propose_adjustment(
            self, member_id: int,
            requested_limit: float,
            trust_tier: str = "",
            reason: str = "",
            proposed_by: str = "admin") -> dict:
        """发起调额建议书(proposed——
        待终审; 永不直接生效)

        资金域铁律: 本接口只产生建议书
        (disposition), 生效必经 admin
        终审 decide_adjustment

        Raises:
            ValueError: 额度非法(≤0 或
            超当前等级上限×2)
        """
        requested = round(
            float(requested_limit or 0), 2)
        if requested <= 0:
            raise ValueError(
                f"目标额度非法: {requested}(须>0)")
        tier = str(trust_tier or "")
        base = CREDIT_BASE_LIMITS.get(tier, 0.0)
        cap = base * 2  # 临时提额上限=基础×2
        if requested > cap:
            raise ValueError(
                f"目标额度超临时上限: "
                f"{requested} > {cap}"
                f"(等级 {tier or '(空)'} 基础"
                f"{base}×2)")
        if base <= 0:
            raise ValueError(
                f"信值等级 {tier or '(空)'} "
                f"基础额度为零——禁调额")
        adj_id = await self.repo.next_adjust_seq()
        record = {
            "adjustmentId": adj_id,
            "memberId": int(member_id or 0),
            "trustTier": tier,
            "currentLimit": round(base, 2),
            "requestedLimit": requested,
            "status": "proposed",
            "reason": str(reason or ""),
            "proposedBy": str(proposed_by
                              or "admin"),
            "proposedAt": ts(),
            "decidedBy": "",
            "decidedAt": "",
        }
        await self.repo.save_adjustment(
            adj_id, record)
        await self.repo.save_event({
            "type": "adjustment_proposed",
            "memberId": int(member_id or 0),
            "detail": {
                "adjustmentId": adj_id,
                "requested": requested,
            },
            "at": ts(),
        })
        return record

    async def decide_adjustment(
            self, adj_id: int,
            approve: bool,
            decided_by: str = "admin") -> dict:
        """调额终审(admin 人工——资金域
        永不自动的最后一环; 生效即写回
        现行额度供 evaluate 消费)

        Raises:
            KeyError: 建议书不存在
            ValueError: 非 proposed 态
        """
        record = await self.repo.get_adjustment(
            adj_id)
        if not record:
            raise KeyError(
                f"调额建议书不存在"
                f"(adjustmentId={adj_id})")
        if record.get("status") != "proposed":
            raise ValueError(
                f"建议书状态异常: 仅 proposed "
                f"可终审, 当前 "
                f"{record.get('status')}")
        record["status"] = ("approved"
                            if approve
                            else "rejected")
        record["decidedBy"] = str(
            decided_by or "admin")
        record["decidedAt"] = ts()
        await self.repo.save_adjustment(
            adj_id, record)
        await self.repo.save_event({
            "type": "adjustment_decided",
            "memberId": record.get("memberId"),
            "detail": {
                "adjustmentId": adj_id,
                "approved": bool(approve),
                "effectiveLimit": (
                    record["requestedLimit"]
                    if approve else None),
            },
            "at": ts(),
        })
        return record

    async def current_limit(
            self, member_id: int,
            trust_tier: str = "") -> dict:
        """现行额度视图(基础映射+最新已
        生效调额覆盖; 未调额=基础)"""
        tier = str(trust_tier or "")
        base = CREDIT_BASE_LIMITS.get(tier, 0.0)
        effective = base
        source = "base"
        # 最新 approved 建议书覆盖
        approved = await self.repo\
            .list_adjustments(
                status="approved",
                member_id=member_id, limit=1)
        if approved:
            effective = approved[0][
                "requestedLimit"]
            source = "adjustment"
        return {
            "memberId": int(member_id),
            "trustTier": tier,
            "baseLimit": round(base, 2),
            "effectiveLimit": round(
                effective, 2),
            "source": source,
        }

    # ============================================================
    # 还款回流(快环——不受开关影响)
    # ============================================================

    async def report_repayment(
            self, member_id: int,
            event_type: str) -> dict:
        """还款事件回流(ontime/late/early
        ——履约轴数据源; 每笔还款/逾期/
        提前结清即时反馈, 快环)

        Raises:
            ValueError: 事件类型域外
        """
        if event_type not in \
                REPAY_EVENT_TYPES:
            raise ValueError(
                f"还款事件域外: {event_type}"
                f"(须 {REPAY_EVENT_TYPES})")
        stats = await self.repo.report_repayment(
            member_id, event_type)
        await self.repo.save_event({
            "type": "repayment_report",
            "memberId": int(member_id or 0),
            "detail": {
                "eventType": event_type,
                "stats": stats,
            },
            "at": ts(),
        })
        return {
            "memberId": int(member_id),
            "eventType": event_type,
            "stats": stats,
        }

    # ============================================================
    # 观测面
    # ============================================================

    def credit_dict(self) -> dict:
        """授信规则表公示(四轴+权重+评级
        +利率表+状态机——观测面)"""
        return {
            "modelVersion": MODEL_VERSION,
            "factors": list(CREDIT_FACTORS),
            "weights": dict(CREDIT_WEIGHTS),
            "grades": list(CREDIT_GRADES),
            "gradeThresholds": [
                {"min": t, "grade": g}
                for t, g in GRADE_THRESHOLDS
            ],
            "installmentRates": {
                str(k): v
                for k, v in
                INSTALLMENT_RATES.items()
            },
            "baseLimits": dict(
                CREDIT_BASE_LIMITS),
            "adjustmentStates": list(
                ADJUSTMENT_STATES),
            "ironRules": (
                "调额生效=建议书+admin 终审"
                "(资金域永不自动)",
                "利率档=确定性规则表"
                "(LLM 禁定价)",
                "45号 TV 熔断链零改动"
                "(评估消费方)"),
        }

    async def credit_records_view(
            self, member_id: int = None,
            limit: int = 50) -> dict:
        """授信评估留痕视图"""
        records = await self.repo.list_credit(
            member_id, limit=limit)
        return {
            "modelVersion": MODEL_VERSION,
            "count": len(records),
            "records": records,
        }

    async def adjustments_view(
            self, status: str = None,
            member_id: int = None,
            limit: int = 50) -> dict:
        """调额建议书视图"""
        records = await self.repo\
            .list_adjustments(
                status=status,
                member_id=member_id,
                limit=limit)
        return {
            "modelVersion": MODEL_VERSION,
            "count": len(records),
            "adjustments": records,
        }
