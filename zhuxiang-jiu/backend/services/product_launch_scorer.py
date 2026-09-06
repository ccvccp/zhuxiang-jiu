"""01号·产品展示 AI 智能升级 评分器
(product_launch, 全站批次三)

《全站AI智能混合架构升级总计划》批次三:
    商品上架评分——第43档案(batch27, 42→43):
    挂 pdm put_on_sale 上架终审(与 38号
    product_gate 提交预审构成双门)。

    八因子(全反向——高分=高上架风险):
        price_deviation 0.20 售价折扣深度
        info_completeness 0.15 信息完备缺口
        stock_deficit 0.15 库存缺口
        rating_risk 0.15 历史评分风险
        sales_stagnation 0.10 销量停滞
        review_thin 0.10 评价稀薄
        age_staleness 0.05 档案陈旧
        flags_risk 0.10 风险标记数

铁律: 确定性加权/LLM 不进判定链/
observe 默认只评分快照。
"""

import logging
from typing import ClassVar

from core.helpers import ts

logger = logging.getLogger("product_launch_scorer")

MODEL_VERSION = "v1-product-launch"

SCORER_ID = "product_launch"


def _clamp(v, low=0.0, high=100.0):
    return max(low, min(high, float(v)))


def _factor(name, label, score, weight, detail):
    return {"name": name, "label": label,
            "score": round(_clamp(score), 1),
            "weight": round(float(weight), 4),
            "contribution": round(
                _clamp(score) * float(weight), 2),
            "detail": detail}


class ProductLaunchScorer:
    """01号商品上架 AI 评分器(第43档案)"""

    WEIGHTS: ClassVar[dict] = {
        "price_deviation": 0.20,
        "info_completeness": 0.15,
        "stock_deficit": 0.15,
        "rating_risk": 0.15,
        "sales_stagnation": 0.10,
        "review_thin": 0.10,
        "age_staleness": 0.05,
        "flags_risk": 0.10,
    }

    REQUIRED: ClassVar[list] = ["price", "stock"]

    async def score(self, ctx: dict) -> dict:
        if not ctx:
            raise ValueError("评分上下文不能为空")

        price = float(ctx.get("price") or 0)
        original = float(
            ctx.get("originalPrice") or price)
        stock = int(ctx.get("stock") or 0)
        rating = float(
            ctx.get("ratingAvg") or 5.0)
        rating_n = int(
            ctx.get("ratingCount") or 0)
        sales = int(
            ctx.get("salesMonthly") or 0)
        age_days = float(
            ctx.get("ageDays") or 0)
        missing = int(
            ctx.get("infoMissing") or 0)
        flags = int(
            ctx.get("riskFlags") or 0)

        # ① 折扣深度(35 折内零风险, 五折以下线性升高)
        discount = (1 - price / original
                    if original > 0 else 0.0)
        f1 = _clamp((discount - 0.35) * 250)
        d1 = f"折扣深度 {discount:.0%}"

        # ② 信息完备缺口(每缺一项+30)
        f2 = _clamp(missing * 30)
        d2 = f"信息缺口 {missing} 项"

        # ③ 库存缺口(0 库存=100, <10=60, <50=25)
        f3 = (100.0 if stock <= 0
              else 60.0 if stock < 10
              else 25.0 if stock < 50 else 0.0)
        d3 = f"库存 {stock} 件"

        # ④ 历史评分风险(4.5 以下线性)
        f4 = _clamp((4.5 - rating) * 40)
        d4 = f"历史评分 {rating:.1f}"

        # ⑤ 销量停滞(新上架中性 40)
        f5 = (40.0 if sales <= 0
              else _clamp(40 - sales * 4))
        d5 = f"月销 {sales} 件"

        # ⑥ 评价稀薄(零验证商品风险偏上)
        f6 = (60.0 if rating_n <= 0
              else _clamp(50 - rating_n * 10))
        d6 = f"评价 {rating_n} 条"

        # ⑦ 档案陈旧
        f7 = _clamp(age_days / 2)
        d7 = f"建档 {age_days:.0f} 天"

        # ⑧ 风险标记
        f8 = _clamp(flags * 30)
        d8 = f"风险标记 {flags} 项"

        factors = [
            _factor("price_deviation", "折扣深度",
                    f1, self.WEIGHTS["price_deviation"], d1),
            _factor("info_completeness", "信息缺口",
                    f2, self.WEIGHTS["info_completeness"], d2),
            _factor("stock_deficit", "库存缺口",
                    f3, self.WEIGHTS["stock_deficit"], d3),
            _factor("rating_risk", "评分风险",
                    f4, self.WEIGHTS["rating_risk"], d4),
            _factor("sales_stagnation", "销量停滞",
                    f5, self.WEIGHTS["sales_stagnation"], d5),
            _factor("review_thin", "评价稀薄",
                    f6, self.WEIGHTS["review_thin"], d6),
            _factor("age_staleness", "档案陈旧",
                    f7, self.WEIGHTS["age_staleness"], d7),
            _factor("flags_risk", "风险标记",
                    f8, self.WEIGHTS["flags_risk"], d8),
        ]
        risk = round(sum(
            f["contribution"] for f in factors), 1)
        level = ("high" if risk >= 60
                 else "medium" if risk >= 30
                 else "low")
        return {
            "success": True,
            "scorer": SCORER_ID,
            "module": "01产品展示",
            "score": risk,
            "level": level,
            "levelName": {
                "low": "低风险(正常上架)",
                "medium": "中风险(转人工复核)",
                "high": "高风险(上架拦截)",
            }[level],
            "action": {
                "low": "商品正常上架",
                "medium": "商品上架转人工复核",
                "high": "商品上架拦截",
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
