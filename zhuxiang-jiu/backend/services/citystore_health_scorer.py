"""25号·市级网店 AI 升级 评分器
(citystore_health, 全站批次四)

《全站AI智能混合架构升级总计划》批次四:
    网店健康评分——第48档案(batch32, 47→48):
    挂 run_assessment 月度考核门
    (双达标+连续不达标+折扣调整+
    资格判定一体——健康度最完整
    确定性计算点)。

    八因子(全反向——高分=高健康风险):
        purchase_deficit 0.20 月采购缺口
        sales_deficit 0.20 月销售缺口
        consecutive_below 0.15 连续不达标
        discount_floor 0.10 折扣触底
        status_risk 0.10 状态预警
        channel_concentration 0.10 渠道集中
        order_thin 0.10 订单稀薄
        trend_down 0.05 环比下行

铁律: 确定性加权/LLM 不进判定链/
observe 默认只评分快照(不改变
考核判定的既有硬规则)。
"""

import logging
from typing import ClassVar

from core.helpers import ts

logger = logging.getLogger(
    "citystore_health_scorer")

MODEL_VERSION = "v1-citystore-health"

SCORER_ID = "citystore_health"


def _clamp(v, low=0.0, high=100.0):
    return max(low, min(high, float(v)))


def _factor(name, label, score, weight, detail):
    return {"name": name, "label": label,
            "score": round(_clamp(score), 1),
            "weight": round(float(weight), 4),
            "contribution": round(
                _clamp(score) * float(weight), 2),
            "detail": detail}


class CitystoreHealthScorer:
    """25号市级网店健康 AI 评分器(第48档案)"""

    WEIGHTS: ClassVar[dict] = {
        "purchase_deficit": 0.20,
        "sales_deficit": 0.20,
        "consecutive_below": 0.15,
        "discount_floor": 0.10,
        "status_risk": 0.10,
        "channel_concentration": 0.10,
        "order_thin": 0.10,
        "trend_down": 0.05,
    }

    REQUIRED: ClassVar[list] = [
        "monthlyPurchase", "monthlySales"]

    async def score(self, ctx: dict) -> dict:
        if not ctx:
            raise ValueError("评分上下文不能为空")

        purchase = float(
            ctx.get("monthlyPurchase") or 0)
        sales = float(
            ctx.get("monthlySales") or 0)
        p_target = float(
            ctx.get("purchaseTarget") or 9000)
        s_target = float(
            ctx.get("salesTarget") or 5000)
        below = int(
            ctx.get("consecutiveBelow") or 0)
        discount = int(
            ctx.get("currentDiscount") or 90)
        status = int(ctx.get("status") or 1)
        order_n = int(
            ctx.get("orderCount") or 0)
        channels = int(
            ctx.get("channelCount") or 1)

        # ① 月采购缺口(双达标采购线)
        f1 = _clamp((1 - purchase / p_target)
                    * 100) if p_target > 0 else 100.0
        d1 = (f"采购 ¥{purchase:.0f}/"
              f"¥{p_target:.0f}")

        # ② 月销售缺口
        f2 = _clamp((1 - sales / s_target)
                    * 100) if s_target > 0 else 100.0
        d2 = (f"销售 ¥{sales:.0f}/"
              f"¥{s_target:.0f}")

        # ③ 连续不达标(每+1 月 35)
        f3 = _clamp(below * 35)
        d3 = f"连续不达标 {below} 月"

        # ④ 折扣触底(70 最低档=100)
        f4 = _clamp((90 - discount) * 5) \
            if discount < 90 else 0.0
        d4 = f"折扣 {discount}%"

        # ⑤ 状态预警(2 预警 60/3 暂停 100)
        f5 = (100.0 if status >= 3
              else 60.0 if status == 2 else 0.0)
        d5 = f"状态 {status}"

        # ⑥ 渠道集中(单一渠道=100)
        f6 = _clamp((2 - channels) * 100)
        d6 = f"渠道数 {channels}"

        # ⑦ 订单稀薄(<4 单线性)
        f7 = _clamp((4 - order_n) * 25)
        d7 = f"月订单 {order_n} 单"

        # ⑧ 环比下行(缺省中性)
        f8 = float(ctx.get("trendDown") or 0.0)
        d8 = f"环比 {f8:.0f}"

        factors = [
            _factor("purchase_deficit", "采购缺口",
                    f1, self.WEIGHTS["purchase_deficit"], d1),
            _factor("sales_deficit", "销售缺口",
                    f2, self.WEIGHTS["sales_deficit"], d2),
            _factor("consecutive_below", "连续不达标",
                    f3, self.WEIGHTS["consecutive_below"], d3),
            _factor("discount_floor", "折扣触底",
                    f4, self.WEIGHTS["discount_floor"], d4),
            _factor("status_risk", "状态预警",
                    f5, self.WEIGHTS["status_risk"], d5),
            _factor("channel_concentration", "渠道集中",
                    f6, self.WEIGHTS["channel_concentration"], d6),
            _factor("order_thin", "订单稀薄",
                    f7, self.WEIGHTS["order_thin"], d7),
            _factor("trend_down", "环比下行",
                    f8, self.WEIGHTS["trend_down"], d8),
        ]
        risk = round(sum(
            f["contribution"] for f in factors), 1)
        level = ("high" if risk >= 60
                 else "medium" if risk >= 30
                 else "low")
        return {
            "success": True,
            "scorer": SCORER_ID,
            "module": "25市级网店",
            "score": risk,
            "level": level,
            "levelName": {
                "low": "低风险(健康运营)",
                "medium": "中风险(黄牌预警)",
                "high": "高风险(资格复核)",
            }[level],
            "action": {
                "low": "考核正常通过",
                "medium": "考核黄牌预警",
                "high": "考核资格复核",
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
