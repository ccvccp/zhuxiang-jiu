"""09号·活动管理 AI 智能升级 评分器
(activity_risk, 全站批次三)

《全站AI智能混合架构升级总计划》批次三:
    活动风控评分——第44档案(batch28, 43→44):
    挂 activity audit_activity 审核门
    (发布前预算/时长/奖品概率量化风控)。

    八因子(全反向——高分=高活动风险):
        budget_scale 0.20 预算规模
        prize_pressure 0.15 奖池预算压力
        probability_risk 0.15 奖品概率总和
        duration_risk 0.10 时长异常
        type_risk 0.10 活动类型敏感度
        concurrent_pressure 0.10 同类并发
        night_window 0.10 跨夜窗口
        scope_breadth 0.10 定向广度

铁律: 确定性加权/LLM 不进判定链/
observe 默认只评分快照。
"""

import logging
from typing import ClassVar

from core.helpers import ts

logger = logging.getLogger("activity_risk_scorer")

MODEL_VERSION = "v1-activity-risk"

SCORER_ID = "activity_risk"


def _clamp(v, low=0.0, high=100.0):
    return max(low, min(high, float(v)))


def _factor(name, label, score, weight, detail):
    return {"name": name, "label": label,
            "score": round(_clamp(score), 1),
            "weight": round(float(weight), 4),
            "contribution": round(
                _clamp(score) * float(weight), 2),
            "detail": detail}


class ActivityRiskScorer:
    """09号活动 AI 评分器(第44档案)"""

    # 高敏感类型(营销合规红线邻域)
    SENSITIVE_TYPES = {"lottery": 70.0,
                       "seckill": 70.0,
                       "presale": 50.0}

    WEIGHTS: ClassVar[dict] = {
        "budget_scale": 0.20,
        "prize_pressure": 0.15,
        "probability_risk": 0.15,
        "duration_risk": 0.10,
        "type_risk": 0.10,
        "concurrent_pressure": 0.10,
        "night_window": 0.10,
        "scope_breadth": 0.10,
    }

    REQUIRED: ClassVar[list] = ["budget", "durationDays"]

    async def score(self, ctx: dict) -> dict:
        if not ctx:
            raise ValueError("评分上下文不能为空")

        budget = float(ctx.get("budget") or 0)
        prize_total = float(
            ctx.get("prizeTotalValue") or 0)
        probability = float(
            ctx.get("prizeProbability") or 0)
        duration = float(
            ctx.get("durationDays") or 0)
        atype = str(ctx.get("type") or "")
        concurrent = int(
            ctx.get("concurrentSameType") or 0)
        night = bool(ctx.get("nightWindow"))
        scope = str(ctx.get("applicableScope")
                   or "all")

        # ① 预算规模(单奖红线 ¥5000 口径双倍)
        f1 = _clamp(budget / 100)
        d1 = f"总预算 ¥{budget:.0f}"

        # ② 奖池预算压力(奖池/预算 ≥0.8 满)
        ratio = (prize_total / budget
                 if budget > 0 else 1.0)
        f2 = _clamp(ratio / 0.8 * 100)
        d2 = f"奖池占比 {ratio:.0%}"

        # ③ 奖品概率总和(100% 满分)
        f3 = _clamp(probability)
        d3 = f"概率总和 {probability:.0f}%"

        # ④ 时长异常(7 天基准, 偏离线性)
        f4 = _clamp(abs(duration - 7) * 4)
        d4 = f"时长 {duration:.0f} 天"

        # ⑤ 类型敏感度
        f5 = self.SENSITIVE_TYPES.get(atype, 20.0)
        d5 = f"类型 {atype or '未知'}"

        # ⑥ 同类并发
        f6 = _clamp(concurrent * 25)
        d6 = f"同类并发 {concurrent} 个"

        # ⑦ 跨夜窗口
        f7 = 100.0 if night else 0.0
        d7 = "跨夜窗口" if night else "日间窗口"

        # ⑧ 定向广度(all 全量=广)
        f8 = 100.0 if scope == "all" else 30.0
        d8 = f"定向 {scope}"

        factors = [
            _factor("budget_scale", "预算规模",
                    f1, self.WEIGHTS["budget_scale"], d1),
            _factor("prize_pressure", "奖池压力",
                    f2, self.WEIGHTS["prize_pressure"], d2),
            _factor("probability_risk", "概率风险",
                    f3, self.WEIGHTS["probability_risk"], d3),
            _factor("duration_risk", "时长异常",
                    f4, self.WEIGHTS["duration_risk"], d4),
            _factor("type_risk", "类型敏感",
                    f5, self.WEIGHTS["type_risk"], d5),
            _factor("concurrent_pressure", "同类并发",
                    f6, self.WEIGHTS["concurrent_pressure"], d6),
            _factor("night_window", "跨夜窗口",
                    f7, self.WEIGHTS["night_window"], d7),
            _factor("scope_breadth", "定向广度",
                    f8, self.WEIGHTS["scope_breadth"], d8),
        ]
        risk = round(sum(
            f["contribution"] for f in factors), 1)
        level = ("high" if risk >= 60
                 else "medium" if risk >= 30
                 else "low")
        return {
            "success": True,
            "scorer": SCORER_ID,
            "module": "09活动管理",
            "score": risk,
            "level": level,
            "levelName": {
                "low": "低风险(正常发布)",
                "medium": "中风险(转人工复核)",
                "high": "高风险(发布拦截)",
            }[level],
            "action": {
                "low": "活动正常发布",
                "medium": "活动发布转人工复核",
                "high": "活动发布拦截",
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
