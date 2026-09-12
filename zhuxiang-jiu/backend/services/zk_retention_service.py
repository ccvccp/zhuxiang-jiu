"""智客·AI智能会员大模型 P1 流失预警与 LTV(zk_retention_service)

「从解释画像到预测未来」:
    - 三信号流失预警: 登录间隔拉长 / 消费衰减 / 等级下滑 加权评分,
      红(≥0.7)/黄(≥0.4)/绿 三级(确定性公式 + formula 留痕)
    - LTV 预测: 历史月均消费 × 留存系数(等级权重 L1-L5 × 活跃权重
      × 可学习 ltvRetainFactor) × 12 预期月数(假设全部标注)
    - 等级生命周期沙盘: 未来 12 月消费±% → 成长值累计 → 保级/升降级
      推演(What-if, 确定性 + formula)

铁律: 全部确定性数学公式(同输入同输出), LLM 禁入; 沙盘为建议书。
"""

import logging
from datetime import datetime, UTC

from services.zk_fabric_service import (
    _ZkStore, ZkFabricService, _round2, _now_iso, _days_since)
from services.zk_insight_service import (
    order_paid_of, _amount_of, level_of_growth, _months_since)
from services.member_service import (
    LEVEL_THRESHOLDS, KEEP_LEVEL_CONSUME, LEVEL_NAMES)

logger = logging.getLogger(__name__)

# ============================================================
# P1 常量(确定性)
# ============================================================

# 三信号权重(合计 1.0)
SIGNAL_WEIGHTS = {"login": 0.4, "consume": 0.4, "level": 0.2}

# 分级阈值
CHURN_RED = 0.7      # 红: 高预警
CHURN_YELLOW = 0.4   # 黄: 中预警

# 无消费记录会员的消费衰减信号(确定性假设)
NO_CONSUME_SIGNAL = 0.3

# LTV 等级留存权重(L1-L5)
LTV_LEVEL_WEIGHTS = {1: 0.3, 2: 0.4, 3: 0.5, 4: 0.7, 5: 0.9}

# LTV 预期月数
LTV_HORIZON_MONTHS = 12

# ltvRetainFactor 默认值与安全阀(P3 反馈学习, clamp 区间)
LTV_RETAIN_FACTOR_DEFAULT = 0.6
LTV_RETAIN_FACTOR_CLAMP = (0.4, 0.8)

# 沙盘参数边界
SANDBOX_CONSUME_DELTA_RANGE = (-0.9, 3.0)   # 未来月消费变动 -90%~+300%
SANDBOX_GROWTH_DELTA_MAX = 99_999           # 一次性成长值加成上限


def _clamp01(v: float) -> float:
    """截断到 [0, 1]"""
    return max(0.0, min(1.0, float(v)))


class ZkRetentionService:
    """P1: 三信号流失预警 + LTV 预测 + 等级生命周期沙盘"""

    def __init__(self, fabric: ZkFabricService = None,
                 store: _ZkStore = None):
        self.fabric = fabric or ZkFabricService()
        self.store = store or self.fabric.store

    # ============================================================
    # 可学习参数读取(P3 反馈闭环共用, 确定性)
    # ============================================================

    async def get_ltv_retain_factor(self) -> float:
        """当前 ltvRetainFactor(默认 0.6, P3 反馈可调)"""
        params = await self.store.get("zk_params", "params")
        if params and "ltvRetainFactor" in params:
            return float(params["ltvRetainFactor"])
        return LTV_RETAIN_FACTOR_DEFAULT

    # ============================================================
    # 三信号流失预警
    # ============================================================

    async def churn_scan(self) -> dict:
        """全量会员三信号流失预警扫描(确定性公式)

        信号口径:
            s1 登录间隔拉长: (距上次登录天数 − 全体均值)/全体均值, 截断 [0,1]
            s2 消费衰减: 近 30 天消费 vs 前 30 天, 1−近/前, 截断 [0,1]
               (前 30 天为 0 且近 30 天为 0 → 0.3 中度; 前 0 近>0 → 0)
            s3 等级下滑: 成长值 < 当前等级门槛×80% → 1, 否则 0(L1 恒 0)
            score = 0.4×s1 + 0.4×s2 + 0.2×s3
        """
        members = await self.fabric.members()
        login_days_list = [
            _days_since(m.get("last_login_at", ""))
            for m in members if (m.get("last_login_at") or "").strip()]
        avg_login_days = (sum(login_days_list) / len(login_days_list)
                          if login_days_list else 30.0)

        items = []
        counts = {"red": 0, "yellow": 0, "green": 0}
        for m in members:
            record = await self._churn_of_member(m, avg_login_days)
            counts[record["riskLevel"]] += 1
            items.append(record)
            await self.store.save("zk_churns", m["id"], record)
        items.sort(key=lambda r: (-r["churnScore"], r["memberId"]))
        return {
            "scanned": len(members),
            "riskCounts": counts,
            "items": items,
            "formula": (f"score = {SIGNAL_WEIGHTS['login']}×登录拉长 + "
                        f"{SIGNAL_WEIGHTS['consume']}×消费衰减 + "
                        f"{SIGNAL_WEIGHTS['level']}×等级下滑; "
                        f"红≥{CHURN_RED} / 黄≥{CHURN_YELLOW} / 绿其余"
                        "(确定性, 无 LLM)"),
            "scannedAt": _now_iso(),
        }

    async def _churn_of_member(self, member: dict,
                               avg_login_days: float) -> dict:
        """单会员三信号评分(确定性)"""
        mid = member["id"]
        orders = self.fabric.valid_orders(
            await self.fabric.member_orders(mid))
        level = int(member.get("level", 1) or 1)
        growth = int(member.get("growth_value", 0) or 0)

        # s1 登录间隔拉长
        login_days = _days_since(member.get("last_login_at", ""))
        s1 = (_clamp01((login_days - avg_login_days) / avg_login_days)
              if avg_login_days > 0 else 0.0)

        # s2 消费衰减(近 30 天 vs 前 30 天)
        now = datetime.now(UTC)
        recent30 = sum(_amount_of(o) for o in orders
                       if (now - _parse_ts(order_paid_of(o))).days <= 30)
        prev30 = sum(_amount_of(o) for o in orders
                     if 30 < (now - _parse_ts(order_paid_of(o))).days <= 60)
        if prev30 > 0:
            s2 = _clamp01(1.0 - recent30 / prev30)
        elif recent30 > 0:
            s2 = 0.0
        else:
            s2 = NO_CONSUME_SIGNAL

        # s3 等级下滑(成长值低于当前等级门槛 80%; L1 门槛 0 恒不触发)
        threshold = LEVEL_THRESHOLDS.get(level, 0)
        s3 = 1.0 if (level >= 2 and threshold > 0
                     and growth < threshold * 0.8) else 0.0

        score = _round2(SIGNAL_WEIGHTS["login"] * s1
                        + SIGNAL_WEIGHTS["consume"] * s2
                        + SIGNAL_WEIGHTS["level"] * s3)
        risk = ("red" if score >= CHURN_RED
                else "yellow" if score >= CHURN_YELLOW else "green")
        risk_name = {"red": "红色高预警", "yellow": "黄色中预警",
                     "green": "绿色健康"}[risk]
        return {
            "memberId": mid,
            "nickname": member.get("nickname", ""),
            "level": level,
            "signals": {
                "loginGap": {"daysSinceLogin": _round2(login_days),
                             "cohortAvg": _round2(avg_login_days),
                             "value": _round2(s1)},
                "consumeDecay": {"recent30": _round2(recent30),
                                 "prev30": _round2(prev30),
                                 "value": _round2(s2)},
                "levelSlide": {"growth": growth,
                               "threshold": threshold,
                               "value": _round2(s3)},
            },
            "churnScore": score,
            "riskLevel": risk,
            "riskLevelName": risk_name,
            "computedAt": _now_iso(),
        }

    async def churns(self, limit: int = 50) -> list[dict]:
        """已留痕预警列表(按风险分降序)"""
        rows = await self.store.list("zk_churns")
        rows.sort(key=lambda r: (-float(r.get("churnScore", 0)),
                                 r.get("memberId", 0)))
        return rows[:max(1, int(limit))]

    # ============================================================
    # LTV 预测(确定性 + 假设标注)
    # ============================================================

    async def ltv(self, member_id) -> dict:
        """会员 LTV 预测

        LTV = 历史月均消费 × 等级权重(L1-L5: 0.3/0.4/0.5/0.7/0.9)
              × 活跃权重(0.5+0.5×min(1, 近90天单量/6))
              × ltvRetainFactor(可学习, 默认 0.6) × 12 个月

        Raises:
            KeyError: 会员不存在
        """
        member = await self.fabric.get_member(member_id)
        orders = self.fabric.valid_orders(
            await self.fabric.member_orders(member_id))
        months = _months_since(member.get("created_at", ""))
        level = int(member.get("level", 1) or 1)

        monthly_avg = sum(_amount_of(o) for o in orders) / months
        level_weight = LTV_LEVEL_WEIGHTS.get(level, 0.3)
        orders_90d = sum(1 for o in orders
                         if _days_since(order_paid_of(o)) <= 90)
        active_weight = _round2(0.5 + 0.5 * min(1.0, orders_90d / 6.0))
        retain = await self.get_ltv_retain_factor()
        ltv_value = _round2(
            monthly_avg * level_weight * active_weight * retain
            * LTV_HORIZON_MONTHS)

        record = {
            "memberId": member_id,
            "nickname": member.get("nickname", ""),
            "level": level,
            "levelName": LEVEL_NAMES.get(level, ""),
            "basis": {
                "monthlyAvgConsume": _round2(monthly_avg),
                "historyMonths": _round2(months),
                "ordersIn90d": orders_90d,
            },
            "factors": {
                "levelWeight": level_weight,
                "activeWeight": active_weight,
                "retainFactor": retain,
                "horizonMonths": LTV_HORIZON_MONTHS,
            },
            "ltv": ltv_value,
            "formula": (f"LTV = 月均消费 ¥{_round2(monthly_avg)} × 等级权重"
                        f" {level_weight} × 活跃权重 {active_weight} × "
                        f"留存系数 {retain}(可学习) × "
                        f"{LTV_HORIZON_MONTHS} 个月 = ¥{ltv_value}"),
            "assumptions": [
                "等级留存权重按当前等级取值(升级/降级会改变 LTV)",
                "活跃权重以近 90 天有效单量/6 为上限归一",
                "ltvRetainFactor 为全局校准参数(默认 0.6, "
                "P3 反馈闭环在 [0.4, 0.8] 内学习)",
                "预期存续 12 个月(行业口径假设, 非个体预测)",
            ],
            "predictedAt": _now_iso(),
        }
        await self.store.save("zk_ltvs", member_id, record)
        return record

    # ============================================================
    # 等级生命周期沙盘(What-if 推演, 建议书)
    # ============================================================

    async def sandbox(self, member_id, consume_delta: float = 0.0,
                      growth_delta: int = 0) -> dict:
        """等级生命周期 What-if: 未来 12 月消费±% → 升降级推演

        口径(确定性):
            未来月消费 = 历史月均 × (1 + consumeDelta)
            新成长值 = 当前成长值 + 未来月消费×12(每元 1 成长值)
                      + growthDelta(一次性加成)
            新等级 = 按门槛表推算; 保级判定对照 12 月累计消费

        Raises:
            KeyError: 会员不存在
            ValueError: 参数越界
        """
        lo, hi = SANDBOX_CONSUME_DELTA_RANGE
        if not (lo <= consume_delta <= hi):
            raise ValueError(
                f"消费变动须在 [{lo}, {hi}](-90%~+300%)")
        if not (0 <= int(growth_delta) <= SANDBOX_GROWTH_DELTA_MAX):
            raise ValueError(
                f"成长值加成须在 [0, {SANDBOX_GROWTH_DELTA_MAX}]")

        member = await self.fabric.get_member(member_id)
        orders = self.fabric.valid_orders(
            await self.fabric.member_orders(member_id))
        months = _months_since(member.get("created_at", ""))
        growth = int(member.get("growth_value", 0) or 0)
        level_now = int(member.get("level", 1) or 1)
        level_by_growth = level_of_growth(growth)

        monthly_avg = sum(_amount_of(o) for o in orders) / months
        future_monthly = _round2(monthly_avg * (1.0 + consume_delta))
        growth_gain = int(future_monthly * LTV_HORIZON_MONTHS)
        new_growth = growth + growth_gain + int(growth_delta)
        new_level = level_of_growth(new_growth)
        keep_need = KEEP_LEVEL_CONSUME.get(new_level, 0)
        future_consume = _round2(future_monthly
                                 * LTV_HORIZON_MONTHS)
        keep_ok = future_consume >= keep_need

        next_level = new_level + 1 if new_level < 5 else None
        gap_to_next = (LEVEL_THRESHOLDS[next_level] - new_growth
                       if next_level else 0)
        direction = ("升级" if new_level > level_by_growth
                     else "降级" if new_level < level_by_growth else "持平")
        record = {
            "sandboxId": await self.store.next_id("zk_sandboxes"),
            "memberId": member_id,
            "nickname": member.get("nickname", ""),
            "assumption": {
                "consumeDelta": _round2(consume_delta),
                "growthDelta": int(growth_delta),
                "horizonMonths": LTV_HORIZON_MONTHS,
            },
            "current": {
                "level": level_now,
                "levelName": LEVEL_NAMES.get(level_now, ""),
                "growth": growth,
                "levelByGrowth": level_by_growth,
                "monthlyAvgConsume": _round2(monthly_avg),
                "threshold": LEVEL_THRESHOLDS.get(level_by_growth, 0),
            },
            "projected": {
                "futureMonthlyConsume": future_monthly,
                "future12mConsume": future_consume,
                "growthGain": growth_gain,
                "newGrowth": new_growth,
                "newLevel": new_level,
                "newLevelName": LEVEL_NAMES.get(new_level, ""),
                "keepRequirement": keep_need,
                "keepVerdict": "保级" if keep_ok else "降级风险",
                "nextLevel": next_level,
                "gapToNextLevel": gap_to_next,
                "direction": direction,
            },
            "formula": (f"未来月消费 = 月均 ¥{_round2(monthly_avg)} × "
                        f"(1{_round2(consume_delta):+}); 新成长值 = "
                        f"{growth} + {growth_gain} + {int(growth_delta)} "
                        f"= {new_growth}(每元 1 成长值); 新等级按门槛表 "
                        f"0/500/3000/6999/9999 推算; 保级对照 "
                        f"{keep_need} 元/12月"),
            "disposition": "推演为建议书; 任何等级/权益调整须管理员确认",
            "simulatedAt": _now_iso(),
        }
        await self.store.save("zk_sandboxes", record["sandboxId"], record)
        return record


def _parse_ts(iso: str) -> datetime:
    """解析时间戳(空/非法 → epoch, 保证确定性不抛错)"""
    if not (iso or "").strip():
        return datetime.fromtimestamp(0, tz=UTC)
    try:
        dt = datetime.fromisoformat(str(iso))
        return dt if dt.tzinfo else dt.replace(tzinfo=UTC)
    except (TypeError, ValueError):
        return datetime.fromtimestamp(0, tz=UTC)
