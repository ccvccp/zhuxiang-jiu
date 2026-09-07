"""22号·双码追溯 AI 智能升级 评分器
(trace_integrity, 全站批次四)

《全站AI智能混合架构升级总计划》批次四:
    追溯完整性评分——第46档案(batch30, 45→46):
    挂 activate_life_code 首扫激活门
    (一瓶一激活/锁首启日期——追溯档案
    消费者端诞生点)。

    八因子(全反向——高分=高完整性风险):
        order_missing 0.15 关联订单缺失
        cross_region 0.20 跨区激活
        channel_risk 0.10 渠道未知
        price_deviation 0.15 购买价偏离
        box_unbound 0.15 瓶未绑箱
        box_opened 0.10 箱已开封
        activation_burst 0.10 激活爆发
        batch_anomaly 0.05 批次异常

铁律: 确定性加权/LLM 不进判定链/
observe 默认只评分快照/不破坏
orderId 供 65号分润取数契约。
"""

import logging
from typing import ClassVar

from core.helpers import ts

logger = logging.getLogger("trace_integrity_scorer")

MODEL_VERSION = "v1-trace-integrity"

SCORER_ID = "trace_integrity"


def _clamp(v, low=0.0, high=100.0):
    return max(low, min(high, float(v)))


def _factor(name, label, score, weight, detail):
    return {"name": name, "label": label,
            "score": round(_clamp(score), 1),
            "weight": round(float(weight), 4),
            "contribution": round(
                _clamp(score) * float(weight), 2),
            "detail": detail}


class TraceIntegrityScorer:
    """22号追溯完整性 AI 评分器(第46档案)"""

    WEIGHTS: ClassVar[dict] = {
        "order_missing": 0.15,
        "cross_region": 0.20,
        "channel_risk": 0.10,
        "price_deviation": 0.15,
        "box_unbound": 0.15,
        "box_opened": 0.10,
        "activation_burst": 0.10,
        "batch_anomaly": 0.05,
    }

    REQUIRED: ClassVar[list] = ["lifeCode"]

    async def score(self, ctx: dict) -> dict:
        if not ctx:
            raise ValueError("评分上下文不能为空")

        price = float(ctx.get("purchasePrice") or 0)
        expected = float(
            ctx.get("expectedPrice") or price)
        burst = int(
            ctx.get("userActivationCount") or 1)

        # ① 关联订单缺失(65号分润取数锚点)
        f1 = 100.0 if not ctx.get("orderId") \
            else 0.0
        d1 = ("订单缺失" if f1 else "订单在案")

        # ② 跨区激活
        f2 = 100.0 if ctx.get("crossRegion") \
            else 0.0
        d2 = "跨区" if f2 else "同区"

        # ③ 渠道未知
        f3 = 80.0 if not ctx.get(
            "purchaseChannel") else 0.0
        d3 = f"渠道 {ctx.get('purchaseChannel')
                       or '未知'}"

        # ④ 购买价偏离(缺失或偏离 50%+)
        if price <= 0:
            f4 = 60.0
            d4 = "购买价缺失"
        elif expected > 0:
            dev = abs(price - expected) / expected
            f4 = _clamp(dev / 0.5 * 100)
            d4 = f"价偏 {dev:.0%}"
        else:
            f4 = 0.0
            d4 = "价格正常"

        # ⑤ 瓶未绑箱
        f5 = 100.0 if not ctx.get("boxCode") \
            else 0.0
        d5 = "未绑箱" if f5 else "已绑箱"

        # ⑥ 箱已开封(开箱后再激活异常)
        f6 = 80.0 if ctx.get("boxOpened") \
            else 0.0
        d6 = "箱已开" if f6 else "箱未开"

        # ⑦ 激活爆发(同用户当日激活≥5 满)
        f7 = _clamp((burst - 1) * 25)
        d7 = f"当日激活 {burst} 次"

        # ⑧ 批次异常(缺省中性)
        f8 = float(ctx.get("batchRisk") or 0.0)
        d8 = f"批次风险 {f8:.0f}"

        factors = [
            _factor("order_missing", "订单缺失",
                    f1, self.WEIGHTS["order_missing"], d1),
            _factor("cross_region", "跨区激活",
                    f2, self.WEIGHTS["cross_region"], d2),
            _factor("channel_risk", "渠道未知",
                    f3, self.WEIGHTS["channel_risk"], d3),
            _factor("price_deviation", "价偏",
                    f4, self.WEIGHTS["price_deviation"], d4),
            _factor("box_unbound", "未绑箱",
                    f5, self.WEIGHTS["box_unbound"], d5),
            _factor("box_opened", "箱已开",
                    f6, self.WEIGHTS["box_opened"], d6),
            _factor("activation_burst", "激活爆发",
                    f7, self.WEIGHTS["activation_burst"], d7),
            _factor("batch_anomaly", "批次异常",
                    f8, self.WEIGHTS["batch_anomaly"], d8),
        ]
        risk = round(sum(
            f["contribution"] for f in factors), 1)
        level = ("high" if risk >= 60
                 else "medium" if risk >= 30
                 else "low")
        return {
            "success": True,
            "scorer": SCORER_ID,
            "module": "22双码追溯",
            "score": risk,
            "level": level,
            "levelName": {
                "low": "低风险(正常激活)",
                "medium": "中风险(转人工核验)",
                "high": "高风险(激活拦截)",
            }[level],
            "action": {
                "low": "追溯码正常激活",
                "medium": "追溯激活转人工核验",
                "high": "追溯激活拦截",
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
