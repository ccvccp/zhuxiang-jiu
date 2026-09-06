"""13号·老酒兑换 AI 智能升级 评分器
(recycle_valuation, 全站批次二)

《全站AI智能混合架构升级总计划》批次二:
    老酒兑换模块议价轨升级——第42档案
    recycle_valuation(batch26, 计数 41→42):

    | 因子 | 权重 | 口径(全反向——高分=高议价风险) |
    |------|------|------|
    | deviation_ratio    | 0.22 | 出价偏离度
      (出价 vs AI基准价, 硬规则±10%内
      折算 0-100) |
    | round_pressure    | 0.13 | 轮次压力
      (negotiationRound/maxRounds 正向) |
    | grade_factor      | 0.12 | 品质折损
      (A=0/B=35/C=65/D=90) |
    | age_depth         | 0.10 | 酒龄深度
      (新酒 0-3 年越接近老酒路径越高) |
    | price_volatility  | 0.13 | 议价波动
      (history 价格极差/基准价, ±10%内
      折算 0-100) |
    | trust_deficit     | 0.10 | 信任缺口
      (竹信分 1000 制反向) |
    | bottle_scale      | 0.10 | 规模压力
      (bottleCount/单笔上限 3 瓶正向) |
    | history_bargain   | 0.10 | 历史议价频次
      (用户历史议价单数×20 封顶) |

    → 风险分 0-100 → 三级决策(对齐
      credit_scoring 范式):
        low 放行 / medium 转人工
        review / high 拦截

铁律(对齐 54-65号新范式+批次一):
    - 确定性加权, LLM 不进判定链
    - observe 模式下决策门只评分
      快照不阻断(行为兼容)
    - 议价硬规则(±10%系数/轮次上限)
      保留为合规底线, AI 门为叠加层
    - 决策阈值入册 44号可学习
"""

import logging
from typing import ClassVar

from core.helpers import ts

logger = logging.getLogger("recycle_scorer")

MODEL_VERSION = "v1-recycle-valuation"

SCORER_ID = "recycle_valuation"


def _clamp(value: float, low: float = 0.0,
            high: float = 100.0) -> float:
    return max(low, min(high, float(value)))


def _factor(name: str, label: str, score: float,
            weight: float, detail: str) -> dict:
    return {
        "name": name, "label": label,
        "score": round(_clamp(score), 1),
        "weight": round(float(weight), 4),
        "contribution": round(
            _clamp(score) * float(weight), 2),
        "detail": detail,
    }


class RecycleValuationScorer:
    """13号老酒兑换 AI 评分器(第42档案
    recycle_valuation——议价决策门+回收
    估值风险观测)"""

    # 议价系数硬边界(对齐 recycle_service
    # NEGOTIATION_COEFFICIENT_MIN/MAX ±10%)
    COEFF_BOUND = 0.10

    # 单笔回收瓶数上限(对齐 SINGLE_RECYCLE_MAX_BOTTLES)
    MAX_BOTTLES = 3

    WEIGHTS: ClassVar[dict] = {
        "deviation_ratio": 0.22,
        "round_pressure": 0.13,
        "grade_factor": 0.12,
        "age_depth": 0.10,
        "price_volatility": 0.13,
        "trust_deficit": 0.10,
        "bottle_scale": 0.10,
        "history_bargain": 0.10,
    }

    # 置信度必填字段(缺失降置信不报错)
    REQUIRED: ClassVar[list] = [
        "proposedPrice", "aiBasePrice"]

    # 品质等级 → 风险基分(A 最优 D 最差)
    GRADE_RISK = {"A": 0.0, "B": 35.0,
                   "C": 65.0, "D": 90.0}

    async def score(self, ctx: dict) -> dict:
        """评分入口: 八因子加权 → 风险分
        (0-100, 越高越危险) → 三级决策

        ctx 输入(全缺省容忍):
            proposedPrice 本轮出价 /
            aiBasePrice AI基准价 /
            negotiationRound 当前轮次 /
            maxRounds 最大轮次(缺省3) /
            conditionGrade 品质分级(A-D) /
            wineAge 酒龄(年) / bottleCount
            瓶数 / bambooScore 用户竹信分 /
            historyPrices 历史轮次价格列表 /
            userNegotiationCount 用户历史
            议价单数
        """
        if not ctx:
            raise ValueError("评分上下文不能为空")

        proposed = float(
            ctx.get("proposedPrice") or 0)
        base = float(
            ctx.get("aiBasePrice") or 0)
        round_now = int(
            ctx.get("negotiationRound") or 1)
        max_rounds = int(
            ctx.get("maxRounds") or 3)
        grade = str(
            ctx.get("conditionGrade") or "A")
        wine_age = float(
            ctx.get("wineAge") or 0)
        bottles = int(
            ctx.get("bottleCount") or 1)
        bamboo = float(
            ctx.get("bambooScore") or 700)
        history_prices = [
            float(p) for p in
            (ctx.get("historyPrices") or [])
            if isinstance(p, (int, float))]
        neg_count = int(
            ctx.get("userNegotiationCount")
            or 0)

        # ① 出价偏离度(|出价-基准|/基准,
        #    硬规则±10%内折算 0-100)
        if base <= 0:
            f1 = 100.0
            d1 = "AI基准价缺失"
        else:
            deviation = abs(proposed - base) / base
            f1 = _clamp(
                deviation / self.COEFF_BOUND
                * 100)
            d1 = (f"出价偏离基准 "
                  f"{deviation:.1%}"
                  f"(¥{proposed:.0f}/"
                  f"基准¥{base:.0f})")

        # ② 轮次压力(越接近上限越高)
        round_ratio = (round_now / max_rounds
                       if max_rounds > 0 else 1.0)
        f2 = _clamp(round_ratio * 100)
        d2 = f"议价第 {round_now}/{max_rounds} 轮"

        # ③ 品质折损(等级越差估值越不确定)
        f3 = self.GRADE_RISK.get(grade, 60.0)
        d3 = f"品质分级 {grade}"

        # ④ 酒龄深度(新酒越接近 3 年,
        #    越靠近老酒增值路径越难估值)
        f4 = _clamp(wine_age / 3.0 * 100)
        d4 = f"酒龄 {wine_age:.1f} 年"

        # ⑤ 议价波动(history 价格极差/基准)
        if base <= 0 or len(history_prices) < 2:
            f5 = 0.0
            d5 = "议价轮次不足(无波动)"
        else:
            spread = (max(history_prices)
                      - min(history_prices))
            f5 = _clamp(
                spread / base
                / self.COEFF_BOUND * 100)
            d5 = (f"历史议价波动 "
                  f"{spread / base:.1%}")

        # ⑥ 信任缺口(竹信分反向, 1000 制)
        f6 = _clamp((1000.0 - bamboo)
                    / 1000.0 * 100)
        d6 = f"用户竹信分 {bamboo:.0f}"

        # ⑦ 规模压力(瓶数占单笔上限比)
        f7 = _clamp(
            bottles / self.MAX_BOTTLES * 100)
        d7 = (f"回收规模 {bottles}/"
              f"{self.MAX_BOTTLES} 瓶")

        # ⑧ 历史议价频次(频繁议价=压价
        #    操纵嫌疑, 每单+20 封顶)
        f8 = _clamp(neg_count * 20)
        d8 = f"历史议价 {neg_count} 单"

        factors = [
            _factor("deviation_ratio", "出价偏离",
                    f1, self.WEIGHTS["deviation_ratio"], d1),
            _factor("round_pressure", "轮次压力",
                    f2, self.WEIGHTS["round_pressure"], d2),
            _factor("grade_factor", "品质折损",
                    f3, self.WEIGHTS["grade_factor"], d3),
            _factor("age_depth", "酒龄深度",
                    f4, self.WEIGHTS["age_depth"], d4),
            _factor("price_volatility", "议价波动",
                    f5, self.WEIGHTS["price_volatility"], d5),
            _factor("trust_deficit", "信任缺口",
                    f6, self.WEIGHTS["trust_deficit"], d6),
            _factor("bottle_scale", "规模压力",
                    f7, self.WEIGHTS["bottle_scale"], d7),
            _factor("history_bargain", "议价频次",
                    f8, self.WEIGHTS["history_bargain"], d8),
        ]
        risk = round(sum(
            f["contribution"]
            for f in factors), 1)

        if risk >= 60.0:
            level = "high"
        elif risk >= 30.0:
            level = "medium"
        else:
            level = "low"
        level_name = {
            "low": "低风险(正常议价)",
            "medium": "中风险(转人工复核)",
            "high": "高风险(议价拦截)",
        }[level]
        action = {
            "low": "议价放行",
            "medium": "议价转人工复核",
            "high": "议价拦截",
        }[level]

        return {
            "success": True,
            "scorer": SCORER_ID,
            "module": "13老酒兑换",
            "score": risk,
            "level": level,
            "levelName": level_name,
            "action": action,
            "factors": factors,
            "confidence": self._confidence(ctx),
            "modelVersion": MODEL_VERSION,
            "scoredAt": ts(),
        }

    @classmethod
    def _confidence(cls, ctx: dict) -> float:
        """置信度(必填字段缺失降级)"""
        missing = [k for k in cls.REQUIRED
                   if ctx.get(k) is None]
        if not missing:
            return 1.0
        return round(
            1.0 - 0.2 * len(missing), 2)
