"""10号·广告投放 AI 智能升级 评分器
(ad_placement, 全站批次三)

《全站AI智能混合架构升级总计划》批次三:
    广告投放评分——第45档案(batch29, 44→45):
    挂 ad online_ad 投放门(此前上线仅
    查 approved 状态——预算/排期/容量
    零校验, 为最大空白点)。

    八因子(全反向——高分=高投放风险):
        review_deficit 0.20 合规分缺口
        budget_scale 0.15 日预算规模
        slot_congestion 0.15 广告位拥塞
        type_risk 0.15 高扰类型敏感
        duration_risk 0.10 排期时长异常
        target_breadth 0.10 定向广度
        ctr_baseline 0.10 广告位低效
        schedule_gaps 0.05 排期缺失

铁律: 确定性加权/LLM 不进判定链/
observe 默认只评分快照。
"""

import logging
from typing import ClassVar

from core.helpers import ts

logger = logging.getLogger("ad_placement_scorer")

MODEL_VERSION = "v1-ad-placement"

SCORER_ID = "ad_placement"


def _clamp(v, low=0.0, high=100.0):
    return max(low, min(high, float(v)))


def _factor(name, label, score, weight, detail):
    return {"name": name, "label": label,
            "score": round(_clamp(score), 1),
            "weight": round(float(weight), 4),
            "contribution": round(
                _clamp(score) * float(weight), 2),
            "detail": detail}


class AdPlacementScorer:
    """10号广告投放 AI 评分器(第45档案)"""

    # 高打扰类型(弹窗/浮层=高危)
    INTRUSIVE_TYPES = {"POPUP": 80.0, "FLOAT": 80.0,
                       "SPLASH": 60.0,
                       "PRE_ROLL": 60.0}

    WEIGHTS: ClassVar[dict] = {
        "review_deficit": 0.20,
        "budget_scale": 0.15,
        "slot_congestion": 0.15,
        "type_risk": 0.15,
        "duration_risk": 0.10,
        "target_breadth": 0.10,
        "ctr_baseline": 0.10,
        "schedule_gaps": 0.05,
    }

    REQUIRED: ClassVar[list] = ["reviewScore",
                                "dailyBudget"]

    async def score(self, ctx: dict) -> dict:
        if not ctx:
            raise ValueError("评分上下文不能为空")

        review_score = float(
            ctx.get("reviewScore") or 0)
        daily_budget = float(
            ctx.get("dailyBudget") or 0)
        concurrency = int(
            ctx.get("slotConcurrency") or 1)
        capacity = int(
            ctx.get("slotCapacity") or 1)
        ad_type = str(
            ctx.get("adType") or "BANNER")
        duration = float(
            ctx.get("durationDays") or 0)
        target = str(ctx.get("targetRules") or "all")
        slot_ctr = ctx.get("slotCtr")
        if slot_ctr is not None:
            slot_ctr = float(slot_ctr)
        schedule_missing = bool(
            ctx.get("scheduleMissing"))

        # ① 合规分缺口(80 分及格线下)
        f1 = _clamp(100 - review_score)
        d1 = f"合规分 {review_score:.0f}"

        # ② 日预算规模(¥10000 满)
        f2 = _clamp(daily_budget / 100)
        d2 = f"日预算 ¥{daily_budget:.0f}"

        # ③ 广告位拥塞(并发/容量)
        f3 = _clamp(
            concurrency / max(capacity, 1) * 100)
        d3 = (f"位拥塞 {concurrency}/"
              f"{max(capacity, 1)}")

        # ④ 高扰类型敏感
        f4 = self.INTRUSIVE_TYPES.get(
            ad_type, 20.0)
        d4 = f"类型 {ad_type}"

        # ⑤ 排期时长异常(7 天基准)
        f5 = _clamp(abs(duration - 7) * 3)
        d5 = f"排期 {duration:.0f} 天"

        # ⑥ 定向广度
        f6 = 100.0 if target in ("all", "") \
            else 30.0
        d6 = f"定向 {target[:20]}"

        # ⑦ 广告位低效(CTR <5% 线性; 无数据中性 30)
        if slot_ctr is None:
            f7 = 30.0
            d7 = "位 CTR 无数据(中性)"
        else:
            f7 = _clamp((0.05 - slot_ctr)
                        / 0.05 * 100)
            d7 = f"位 CTR {slot_ctr:.1%}"

        # ⑧ 排期缺失
        f8 = 100.0 if schedule_missing else 0.0
        d8 = ("排期缺失"
              if schedule_missing else "排期在案")

        factors = [
            _factor("review_deficit", "合规缺口",
                    f1, self.WEIGHTS["review_deficit"], d1),
            _factor("budget_scale", "预算规模",
                    f2, self.WEIGHTS["budget_scale"], d2),
            _factor("slot_congestion", "位拥塞",
                    f3, self.WEIGHTS["slot_congestion"], d3),
            _factor("type_risk", "类型敏感",
                    f4, self.WEIGHTS["type_risk"], d4),
            _factor("duration_risk", "时长异常",
                    f5, self.WEIGHTS["duration_risk"], d5),
            _factor("target_breadth", "定向广度",
                    f6, self.WEIGHTS["target_breadth"], d6),
            _factor("ctr_baseline", "位低效",
                    f7, self.WEIGHTS["ctr_baseline"], d7),
            _factor("schedule_gaps", "排期缺失",
                    f8, self.WEIGHTS["schedule_gaps"], d8),
        ]
        risk = round(sum(
            f["contribution"] for f in factors), 1)
        level = ("high" if risk >= 60
                 else "medium" if risk >= 30
                 else "low")
        return {
            "success": True,
            "scorer": SCORER_ID,
            "module": "10广告投放",
            "score": risk,
            "level": level,
            "levelName": {
                "low": "低风险(正常投放)",
                "medium": "中风险(转人工复核)",
                "high": "高风险(投放拦截)",
            }[level],
            "action": {
                "low": "广告正常投放",
                "medium": "广告投放转人工复核",
                "high": "广告投放拦截",
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
