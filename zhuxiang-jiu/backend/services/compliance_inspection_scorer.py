"""24号·合规合法智能监控 AI 升级 评分器
(compliance_inspection, 全站批次四)

《全站AI智能混合架构升级总计划》批次四:
    合规巡检评分——第47档案(batch31, 46→47):
    挂 monitor_behavior 行为巡检门
    (riskLevel 此前由调用方直传无自动
    判定——评分→riskLevel→disposal
    链路现成)。

    八因子(全反向——高分=高合规风险):
        amount_ratio 0.20 单笔/大额线
        daily_ratio 0.15 日累计/大额线
        entity_history 0.15 实体历史违规
        action_risk 0.15 行为类型敏感
        frequency 0.10 短窗频次
        evidence_missing 0.10 证据缺失
        manual_flag 0.10 人工标记
        escalation_trend 0.05 升级趋势

铁律: 确定性加权/LLM 不进判定链/
observe 默认只评分快照(不覆盖
调用方 riskLevel 传参)。
"""

import logging
from typing import ClassVar

from core.helpers import ts

logger = logging.getLogger(
    "compliance_inspection_scorer")

MODEL_VERSION = "v1-compliance-inspection"

SCORER_ID = "compliance_inspection"

# 高敏感行为类型(资金邻域)
SENSITIVE_ACTIONS = {
    "withdraw", "transfer", "refund",
    "cash_out", "settlement", "invoice",
}


def _clamp(v, low=0.0, high=100.0):
    return max(low, min(high, float(v)))


def _factor(name, label, score, weight, detail):
    return {"name": name, "label": label,
            "score": round(_clamp(score), 1),
            "weight": round(float(weight), 4),
            "contribution": round(
                _clamp(score) * float(weight), 2),
            "detail": detail}


class ComplianceInspectionScorer:
    """24号合规巡检 AI 评分器(第47档案)"""

    WEIGHTS: ClassVar[dict] = {
        "amount_ratio": 0.20,
        "daily_ratio": 0.15,
        "entity_history": 0.15,
        "action_risk": 0.15,
        "frequency": 0.10,
        "evidence_missing": 0.10,
        "manual_flag": 0.10,
        "escalation_trend": 0.05,
    }

    REQUIRED: ClassVar[list] = ["amount"]

    async def score(self, ctx: dict) -> dict:
        if not ctx:
            raise ValueError("评分上下文不能为空")

        amount = float(ctx.get("amount") or 0)
        daily_total = float(
            ctx.get("dailyTotal") or amount)
        single_line = float(
            ctx.get("largeSingle") or 50000)
        daily_line = float(
            ctx.get("largeDaily") or 200000)
        violations = int(
            ctx.get("entityViolations") or 0)
        action = str(ctx.get("action") or "")
        freq = int(ctx.get("frequency") or 1)

        # ① 单笔大额比(≥单笔线满分)
        f1 = _clamp(amount / single_line * 100) \
            if single_line > 0 else 100.0
        d1 = f"单笔 ¥{amount:.0f}"

        # ② 日累计大额比
        f2 = _clamp(daily_total / daily_line * 100) \
            if daily_line > 0 else 100.0
        d2 = f"日累计 ¥{daily_total:.0f}"

        # ③ 实体历史违规(每次+30)
        f3 = _clamp(violations * 30)
        d3 = f"历史违规 {violations} 次"

        # ④ 行为类型敏感(资金邻域 70)
        f4 = 70.0 if action.lower() \
            in SENSITIVE_ACTIONS else 20.0
        d4 = f"行为 {action or '未知'}"

        # ⑤ 短窗频次(≥5 次满分)
        f5 = _clamp((freq - 1) * 25)
        d5 = f"频次 {freq}"

        # ⑥ 证据缺失
        f6 = 60.0 if not ctx.get("hasEvidence") \
            else 0.0
        d6 = "证据缺失" if f6 else "证据在案"

        # ⑦ 人工标记
        f7 = 50.0 if ctx.get("manualFlag") \
            else 0.0
        d7 = "人工标记" if f7 else "无标记"

        # ⑧ 升级趋势(缺省中性)
        f8 = float(
            ctx.get("escalationTrend") or 0.0)
        d8 = f"趋势 {f8:.0f}"

        factors = [
            _factor("amount_ratio", "单笔大额",
                    f1, self.WEIGHTS["amount_ratio"], d1),
            _factor("daily_ratio", "日累计大额",
                    f2, self.WEIGHTS["daily_ratio"], d2),
            _factor("entity_history", "历史违规",
                    f3, self.WEIGHTS["entity_history"], d3),
            _factor("action_risk", "行为敏感",
                    f4, self.WEIGHTS["action_risk"], d4),
            _factor("frequency", "频次",
                    f5, self.WEIGHTS["frequency"], d5),
            _factor("evidence_missing", "证据缺失",
                    f6, self.WEIGHTS["evidence_missing"], d6),
            _factor("manual_flag", "人工标记",
                    f7, self.WEIGHTS["manual_flag"], d7),
            _factor("escalation_trend", "升级趋势",
                    f8, self.WEIGHTS["escalation_trend"], d8),
        ]
        risk = round(sum(
            f["contribution"] for f in factors), 1)
        level = ("high" if risk >= 60
                 else "medium" if risk >= 30
                 else "low")
        return {
            "success": True,
            "scorer": SCORER_ID,
            "module": "24合规监控",
            "score": risk,
            "level": level,
            "levelName": {
                "low": "低风险(正常行为)",
                "medium": "中风险(限流观察)",
                "high": "高风险(阻断上报)",
            }[level],
            "action": {
                "low": "行为放行",
                "medium": "行为限流观察",
                "high": "行为阻断上报",
            }[level],
            "factors": factors,
            "confidence": self._confidence(ctx),
            "modelVersion": MODEL_VERSION,
            "scoredAt": ts(),
        }

    @classmethod
    def _confidence(cls, ctx: dict) -> float:
        missing = [k for k in cls.REQUIRED
                   if ctx.get(k) is None]
        return round(
            1.0 - 0.2 * len(missing), 2) \
            if missing else 1.0
