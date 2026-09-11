"""智法·AI智能法务大模型 P1 供应链金融合规(zf_finance_service)

深化方案 §二(二) 供应链金融合规: 从"静态授信"到"动态数据增信"
    - 多维数据交叉验证: 订单+库存+产能+回款 → 《供应链信用评估
      报告》(数据矛盾→欺诈标记)
    - 动态合约生成: 按实时信用评级差异化《借款协议》《质押监管
      协议》(评级降档 → 追加担保条款建议书)
    - 资金流向合规监控: 融资款定向用途校验 → 异常流向《违约
      预警函》(建议书, 永不自动发送)

铁律: 评分/条款全确定性公式; 法律通知发送须人工; 零回写金融模块。
"""

import logging

from services.zf_fabric_service import _ZfStore, _round2, _now_iso

logger = logging.getLogger(__name__)

# 信用评分权重(确定性——数据增信口径)
W_ORDERS = 0.30
W_TURNOVER = 0.30
W_REPAYMENT = 0.40

# 欺诈矛盾检测: 库存价值超月产能产值×该倍数即标记
INVENTORY_CAPACITY_RATIO = 3.0

# 评级分档
GRADE_LINES = [(85, "A 优"), (70, "B 良"), (0, "C 关注")]

# 动态合约条款模板(评级差异化——确定性插值, 数字 100% 查询层)
CONTRACT_TERMS = {
    "A 优": {"annualRate": 0.048, "guarantee": "信用免担保",
             "collateralRatio": 0.0, "reviewCycle": "季度复核"},
    "B 良": {"annualRate": 0.062, "guarantee": "货物质押",
             "collateralRatio": 0.2, "reviewCycle": "月度复核"},
    "C 关注": {"annualRate": 0.085, "guarantee": "质押+第三方连带担保",
               "collateralRatio": 0.4, "reviewCycle": "周度复核"},
}

# 资金定向用途白名单(供应链融资合规口径)
FUND_USE_WHITELIST = ("procurement", "production", "logistics")
FUND_USE_NAME = {"procurement": "原料采购", "production": "生产制造",
                 "logistics": "仓储物流"}


def _grade(score: float) -> str:
    for line, name in GRADE_LINES:
        if score >= line:
            return name
    return "C 关注"


class ZfFinanceService:
    """P1: 信用评估交叉验证 + 动态合约 + 资金流向监控"""

    def __init__(self, store: _ZfStore = None):
        self.store = store or _ZfStore()

    # ============================================================
    # 供应链信用评估(多维交叉验证)
    # ============================================================

    async def credit_assess(self, entity_id: str, entity_name: str,
                            monthly_orders: float, inventory_value: float,
                            production_capacity: float,
                            repayment_rate: float) -> dict:
        """订单+库存+产能+回款 → 信用评分 + 矛盾检测

        评分(确定性): 订单规模(0-100 归一)×0.3 + 库存周转
        (库存/月产能, 健康区间 0.5-2.0)×0.3 + 回款率×0.4
        矛盾检测: 库存价值 > 月产能产值×3 → 欺诈标记(数据矛盾)
        """
        if not entity_id or not entity_name:
            raise ValueError("主体 ID 与名称不可为空")
        if monthly_orders < 0 or inventory_value < 0 \
                or production_capacity <= 0 or repayment_rate < 0:
            raise ValueError("输入不可为负(且产能须为正)")
        if repayment_rate > 1:
            repayment_rate = 1.0

        # 订单规模分: 以月订单额 5 万为满分基准(确定性归一)
        orders_score = min(100.0, monthly_orders / 50000.0 * 100.0)
        turnover = inventory_value / production_capacity
        # 周转健康区间 [0.5, 2.0]: 区间内满分, 偏离线性衰减
        if 0.5 <= turnover <= 2.0:
            turnover_score = 100.0
        elif turnover < 0.5:
            turnover_score = turnover / 0.5 * 100.0
        else:
            turnover_score = max(0.0, 100.0 - (turnover - 2.0) * 50.0)
        repayment_score = repayment_rate * 100.0
        score = _round2(W_ORDERS * orders_score + W_TURNOVER
                        * turnover_score + W_REPAYMENT * repayment_score)

        # 矛盾检测(库存远超产能 → 欺诈风险)
        contradictions = []
        fraud = False
        if inventory_value > production_capacity * INVENTORY_CAPACITY_RATIO:
            fraud = True
            contradictions.append(
                f"库存价值 {inventory_value} 超月产能产值 "
                f"{production_capacity}×{INVENTORY_CAPACITY_RATIO}, "
                "数据矛盾(虚增库存骗取授信嫌疑)")

        credit_id = await self.store.next_id("credit")
        record = {
            "creditId": credit_id, "entityId": entity_id,
            "entityName": entity_name,
            "monthlyOrders": monthly_orders,
            "inventoryValue": inventory_value,
            "productionCapacity": production_capacity,
            "repaymentRate": repayment_rate,
            "turnoverRatio": _round2(turnover),
            "score": score, "grade": _grade(score),
            "fraudSuspected": fraud, "contradictions": contradictions,
            "formula": (f"{W_ORDERS}×订单{orders_score:.0f} + "
                        f"{W_TURNOVER}×周转{turnover_score:.0f} + "
                        f"{W_REPAYMENT}×回款{repayment_score:.0f}"),
            "note": "确定性评分; 欺诈标记仅预警, 授信决策须人工",
            "assessedAt": _now_iso(),
        }
        await self.store.save("credits", entity_id, record)
        return record

    async def credit_file(self, entity_id: str) -> dict:
        record = await self.store.get("credits", entity_id)
        if not record:
            raise KeyError(entity_id)
        return record

    # ============================================================
    # 动态合约生成(评级差异化)
    # ============================================================

    async def contract_generate(self, entity_id: str,
                                loan_amount: float) -> dict:
        """按实时信用评级生成《借款协议》/《质押监管协议》

        条款差异化(确定性映射):
            A 优 → 信用免担保 4.8% / B 良 → 货物质押 6.2%+20% /
            C 关注 → 质押+连带担保 8.5%+40%
        降档场景 → 附加《追加担保通知》(建议书, 发送须人工)
        """
        if loan_amount <= 0:
            raise ValueError("借款金额须为正")
        credit = await self.store.get("credits", entity_id)
        if not credit:
            raise KeyError(entity_id)
        if credit.get("fraudSuspected"):
            raise ValueError("主体存在欺诈标记, 禁止生成授信合约"
                             "(须先人工排除矛盾)")

        grade = credit["grade"]
        terms = CONTRACT_TERMS[grade]
        contract_id = await self.store.next_id("contract")
        record = {
            "contractId": contract_id, "entityId": entity_id,
            "entityName": credit["entityName"],
            "loanAmount": loan_amount,
            "creditGrade": grade, "creditScore": credit["score"],
            "terms": {
                "annualRate": terms["annualRate"],
                "guarantee": terms["guarantee"],
                "collateralRatio": terms["collateralRatio"],
                "collateralAmount": _round2(
                    loan_amount * terms["collateralRatio"]),
                "reviewCycle": terms["reviewCycle"],
            },
            "interestAnnual": _round2(loan_amount * terms["annualRate"]),
            "clauses": [
                "第1条 借款用途: 定向用于供应链采购/生产(白名单校验)",
                f"第2条 年利率: {terms['annualRate']:.1%}"
                f"(评级 {grade} 档)",
                f"第3条 担保方式: {terms['guarantee']}"
                + (f"(质押比例 {terms['collateralRatio']:.0%})"
                   if terms["collateralRatio"] > 0 else ""),
                f"第4条 复核周期: {terms['reviewCycle']}",
                "第5条 违约条款: 评级降档或资金挪用 → 触发追加担保"
                "与提前到期条款(须经人工法律通知)",
            ],
            "downgradeClause": {
                "trigger": "信用评级下调或资金流向异常",
                "action": "生成《追加担保通知》建议书(发送须人工确认)",
            },
            "note": "条款为确定性模板插值; 签署须电子签+人工审批",
            "generatedAt": _now_iso(),
        }
        await self.store.save("contracts", contract_id, record)
        return record

    async def contracts(self, limit: int = 50) -> list[dict]:
        rows = await self.store.list("contracts")
        return sorted(rows, key=lambda r: r.get("generatedAt", ""),
                      reverse=True)[:limit]

    # ============================================================
    # 资金流向合规监控
    # ============================================================

    async def fund_monitor(self, entity_id: str, contract_id: int,
                           flows: list[dict]) -> dict:
        """融资款流向定向校验 → 违规即《违约预警函》(建议书)

        每笔流向: {amount, use: procurement|production|logistics|other}
        白名单外用途(other) → 违规预警, 累计违规 > 授信额 20% →
        生成追加担保通知建议书(永不自动发送)。
        """
        if not flows:
            raise ValueError("流向记录不可为空")
        contract = await self.store.get("contracts", contract_id)
        if not contract:
            raise KeyError(f"contract:{contract_id}")
        if contract.get("entityId") != entity_id:
            raise ValueError("流向记录与合约主体不符")

        violations = []
        for f in flows:
            use = str(f.get("use", ""))
            if use not in FUND_USE_WHITELIST:
                violations.append({
                    "amount": float(f.get("amount", 0)),
                    "use": use or "unknown",
                    "reason": f"用途 {use or '未申报'} 不在定向白名单"
                              f"({'+'.join(FUND_USE_NAME[u] for u in FUND_USE_WHITELIST)})",
                })
        violated_amount = _round2(sum(v["amount"] for v in violations))
        total = _round2(sum(float(f.get("amount", 0)) for f in flows))
        loan = float(contract["loanAmount"])
        breach_ratio = _round2(violated_amount / loan if loan else 0)
        breach = breach_ratio > 0.2

        monitor_id = await self.store.next_id("fund_monitor")
        record = {
            "monitorId": monitor_id, "entityId": entity_id,
            "contractId": contract_id,
            "totalFlow": total, "violatedAmount": violated_amount,
            "violations": violations, "breachRatio": breach_ratio,
            "breach": breach,
            "warningLetter": self._warning_letter(
                contract, violations, breach_ratio) if violations else None,
            "note": "白名单定向校验(确定性); 预警函为建议书, "
                    "发送须人工",
            "monitoredAt": _now_iso(),
        }
        await self.store.save("fund_monitors", monitor_id, record)
        return record

    @staticmethod
    def _warning_letter(contract: dict, violations: list[dict],
                         ratio: float) -> dict:
        """《违约预警函》(建议书——发送须人工)"""
        return {
            "title": "资金流向违约预警函",
            "to": contract.get("entityName", ""),
            "body": (f"贵司授信资金累计 {ratio:.0%} 流向白名单外用途, "
                     f"涉及 {len(violations)} 笔; 依据借款协议第 5 条, "
                     "触发追加担保与提前到期条款评估"),
            "action": "建议书模式: 实际发送/追加担保须人工确认",
        }
