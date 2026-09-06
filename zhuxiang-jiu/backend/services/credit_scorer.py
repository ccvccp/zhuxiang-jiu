"""23号·信用管理 AI 智能升级 评分器
(credit_scoring, 全站批次一)

《全站AI智能混合架构升级总计划》批次一:
    信值架构信用底座升级——第41档案
    credit_scoring(batch25, 计数 40→41):

    | 因子 | 权重 | 口径(全反向——高分=高风险) |
    |------|------|------|
    | zone_margin        | 0.20 | 分数区间余量
      (距 L3 门槛 550 越近分越高) |
    | quota_usage        | 0.15 | 额度使用率
      (used/quota 正向) |
    | monthly_pressure   | 0.15 | 月度压力
      (month_used/monthly_limit 正向) |
    | overdue_history    | 0.20 | 逾期历史
      (历史逾期单数正向) |
    | zone_stability     | 0.10 | 区间稳定性
      (scoreZoneSince 持续天数反向) |
    | account_status     | 0.10 | 账户状态
      (frozen/blacklist 高危) |
    | amount_ratio       | 0.05 | 单笔占比
      (amount/single_limit 正向) |
    | history_depth      | 0.05 | 履约深度
      (repaid 单数反向) |

    → 风险分 0-100 → 三级决策(对齐
    withdraw_risk 范式):
        low 放行 / medium 转人工
        review / high 拦截

铁律(对齐 54-65号新范式):
    - 确定性加权, LLM 不进判定链
    - observe 模式下决策门只评分
      快照不阻断(行为兼容)
    - 决策阈值入册 44号可学习
"""

import logging
from typing import ClassVar

from core.helpers import ts

logger = logging.getLogger("credit_scorer")

MODEL_VERSION = "v1-credit-scoring"

SCORER_ID = "credit_scoring"


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


class CreditScoringScorer:
    """23号信用 AI 评分器(第41档案
    credit_scoring——先享后付审批
    决策门+信用行为风险观测)"""

    # L3 门槛(先享后付准入底线——
    # 对齐 credit_repository LEVEL_THRESHOLDS)
    L3_THRESHOLD = 550

    WEIGHTS: ClassVar[dict] = {
        "zone_margin": 0.20,
        "quota_usage": 0.15,
        "monthly_pressure": 0.15,
        "overdue_history": 0.20,
        "zone_stability": 0.10,
        "account_status": 0.10,
        "amount_ratio": 0.05,
        "history_depth": 0.05,
    }

    # 置信度必填字段(缺失降置信不报错)
    REQUIRED: ClassVar[list] = [
        "bambooScore", "quota", "used"]

    async def score(self, ctx: dict) -> dict:
        """评分入口: 八因子加权 → 风险分
        (0-100, 越高越危险) → 三级决策

        ctx 输入(全缺省容忍):
            bambooScore 竹信分 / quota
            额度 / used 已用 / monthUsed
            月度已用 / monthlyLimit 月度
            上限 / singleLimit 单笔上限 /
            amount 本单金额 /
            overdueOrders 历史逾期单数 /
            zoneDays 当前区间持续天数 /
            accountStatus 账户状态 /
            repaidOrders 历史还款单数
        """
        if not ctx:
            raise ValueError("评分上下文不能为空")

        bamboo = float(ctx.get("bambooScore") or 0)
        quota = float(ctx.get("quota") or 0)
        used = float(ctx.get("used") or 0)
        month_used = float(
            ctx.get("monthUsed") or 0)
        monthly_limit = float(
            ctx.get("monthlyLimit") or 0)
        single_limit = float(
            ctx.get("singleLimit") or 0)
        amount = float(
            ctx.get("amount") or 0)
        overdue_n = int(
            ctx.get("overdueOrders") or 0)
        zone_days = float(
            ctx.get("zoneDays") or 0)
        status = str(
            ctx.get("accountStatus")
            or "normal")
        repaid_n = int(
            ctx.get("repaidOrders") or 0)

        # ① 分数区间余量(距 L3 门槛
        #    越近风险越高; 低于门槛
        #    满分)
        margin = (bamboo
                  - self.L3_THRESHOLD)
        if margin <= 0:
            f1 = 100.0
            d1 = (f"竹信分 {bamboo:.0f} "
                  f"低于 L3 门槛 "
                  f"{self.L3_THRESHOLD}")
        else:
            f1 = _clamp(
                100 - margin / 2.5)
            d1 = (f"竹信分 {bamboo:.0f} "
                  f"距 L3 门槛余量 "
                  f"{margin:.0f}")

        # ② 额度使用率(越高越危险)
        usage = used / quota \
            if quota > 0 else 1.0
        f2 = _clamp(usage * 100)
        d2 = (f"额度使用率 "
              f"{usage:.0%}"
              f"(¥{used:.0f}/"
              f"¥{quota:.0f})")

        # ③ 月度压力(越高越危险)
        pressure = month_used / monthly_limit \
            if monthly_limit > 0 else 1.0
        f3 = _clamp(pressure * 100)
        d3 = (f"月度使用率 "
              f"{pressure:.0%}"
              f"(¥{month_used:.0f}/"
              f"¥{monthly_limit:.0f})")

        # ④ 逾期历史(逾期单数正向,
        #    每单+40 封顶)
        f4 = _clamp(overdue_n * 40)
        d4 = f"历史逾期 {overdue_n} 单"

        # ⑤ 区间稳定性(持续天数
        #    越长越稳, 反向)
        f5 = _clamp(
            60 - zone_days / 3)
        d5 = f"区间持续 {zone_days:.0f} 天"

        # ⑥ 账户状态(normal=0/
        #    frozen=80/blacklist=100)
        status_map = {
            "normal": 0.0,
            "frozen": 80.0,
            "blacklist": 100.0,
        }
        f6 = status_map.get(status, 60.0)
        d6 = f"账户状态 {status}"

        # ⑦ 单笔占比(占单笔上限比例)
        ratio = amount / single_limit \
            if single_limit > 0 else 1.0
        f7 = _clamp(ratio * 100)
        d7 = (f"单笔占比 "
              f"{ratio:.0%}"
              f"(¥{amount:.0f}/"
              f"上限¥{single_limit:.0f})")

        # ⑧ 履约深度(还款单数正向
        #    ——每单-15 下限 10)
        f8 = _clamp(
            70 - repaid_n * 15, 10, 100)
        d8 = f"历史履约 {repaid_n} 单"

        factors = [
            _factor("zone_margin", "区间余量",
                    f1, self.WEIGHTS["zone_margin"], d1),
            _factor("quota_usage", "额度使用",
                    f2, self.WEIGHTS["quota_usage"], d2),
            _factor("monthly_pressure", "月度压力",
                    f3, self.WEIGHTS["monthly_pressure"], d3),
            _factor("overdue_history", "逾期历史",
                    f4, self.WEIGHTS["overdue_history"], d4),
            _factor("zone_stability", "区间稳定",
                    f5, self.WEIGHTS["zone_stability"], d5),
            _factor("account_status", "账户状态",
                    f6, self.WEIGHTS["account_status"], d6),
            _factor("amount_ratio", "单笔占比",
                    f7, self.WEIGHTS["amount_ratio"], d7),
            _factor("history_depth", "履约深度",
                    f8, self.WEIGHTS["history_depth"], d8),
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
            "low": "低风险(自动通过)",
            "medium": "中风险(转人工审批)",
            "high": "高风险(拦截)",
        }[level]
        action = {
            "low": "先享后付自动通过",
            "medium": "先享后付转人工审批",
            "high": "先享后付拦截",
        }[level]

        return {
            "success": True,
            "scorer": SCORER_ID,
            "module": "23信用管理",
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
