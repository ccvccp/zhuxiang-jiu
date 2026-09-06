"""64号·信值兑换管理 价值锚定服务
(xx64_anchor_service, P4)

计划(docs/64号_P4_价值锚定与治理层
详细设计.md §三/§四):
    ① 购买力指数: 单位信值可兑
       商品均价日快照(健康度指标
       ——趋势下行=通胀预警)
         avgPrice        = 近 7 日
                          已支付订单
                          price 算术均值
         purchasingPower = 基准均价/
                           当前均价
       (基准=首期快照; 同日重算
        覆盖更新 anchorDate 幂等;
        冷启动样本 <3 不落快照)
    ② 通胀/通缩预警: 指数连续
       3 日单边且累计波幅 >10%
       ——仅落事件+建议书(人工/
       46号, autoExecute 永不)
    ③ 供需预警: 品类 24h 兑换
       笔数 > 前 7 日日均×3 且
       ≥5 笔(需求激增)——与 P3
       ARB-MA 边界: 看总量趋势
       (运营特征)非账号集中度
       (洗钱特征)
    ④ 兑换率校准建议: 积分获取/
       信值消耗失衡比持续 3 日
       越界(>2.0 获取过快/<0.5
       消耗过快)→ 46号 submit_change
       config 变更(仅建议——人工
       审批后生效; 宪法域 R1-R7
       占比结构永不可校准)

铁律(设计 §十):
    - 校准仅建议——参数变更必经
      46号 submit→人工审批双模
    - pending 冲突 fail-soft 跳过
      (同档案已有待审批变更)
    - 观测面不受 XX64_MODE 影响;
      audit/校准为决策面 off 409
"""

import logging
import os
from datetime import datetime, timedelta, UTC

from core.helpers import ts

from repositories.xx64_repository import (
    Xx64Repository,
)

logger = logging.getLogger("xx64_anchor")

MODEL_VERSION = "v1-xx64-anchor"

SCORER_ID = "value_exchange"

# ============================================================
# 阈值(设计 §三/§四——确定性)
# ============================================================

ANCHOR_WINDOW_DAYS = 7       # 均价窗口
ANCHOR_MIN_SAMPLES = 3       # 冷启动样本下限
ANCHOR_TREND_DAYS = 3        # 预警连续天数
ANCHOR_TREND_DRIFT = 0.10    # 预警累计波幅阈值
DEMAND_SPIKE_MULTIPLE = 3    # 需求激增倍数
DEMAND_SPIKE_MIN = 5         # 需求激增笔数下限
RATE_IMBALANCE_HIGH = 2.0    # 获取过快阈值
RATE_IMBALANCE_LOW = 0.5     # 消耗过快阈值
RATE_SUSTAIN_DAYS = 3        # 失衡持续天数
POINTS_PER_TRUST = 100       # R6 宪法(只读引用)

PAID_STATES = ("paid", "settled",
               "completed")


def current_mode() -> str:
    """模块开关(XX64_MODE——同底座)"""
    return os.environ.get(
        "XX64_MODE", "off")


def _parse_dt(value) -> datetime | None:
    try:
        return datetime.fromisoformat(
            str(value))
    except (TypeError, ValueError):
        return None


def _within_days(value, days: float
                 ) -> bool:
    dt = _parse_dt(value)
    if dt is None:
        return False
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt >= datetime.now(UTC) \
        - timedelta(days=days)


class Xx64AnchorService:
    """64号价值锚定(P4——指数+
    供需预警+校准建议)"""

    def __init__(self):
        self.repo = Xx64Repository()

    # ============================================================
    # ① 购买力指数(日快照)
    # ============================================================

    async def snapshot(self) -> dict:
        """计算并落当日指数快照
        (同日重算覆盖更新——
        anchorDate 幂等)

        Returns:
            {anchorId, anchorDate,
             avgPrice, purchasingPower,
             samples, created/updated}
        """
        orders = await self.repo.list_orders(
            limit=500)
        recent = [
            o for o in orders
            if o.get("status") in PAID_STATES
            and _within_days(
                o.get("paidAt")
                or o.get("createdAt"),
                ANCHOR_WINDOW_DAYS)]
        samples = len(recent)
        if samples < ANCHOR_MIN_SAMPLES:
            return {
                "success": True,
                "skipped": True,
                "samples": samples,
                "note": f"样本不足"
                        f"({samples}<{ANCHOR_MIN_SAMPLES}"
                        f"——冷启动不落快照)",
            }
        avg_price = round(sum(
            float(o.get("price") or 0)
            for o in recent)
            / samples, 2)
        # 基准=历史首期快照均价
        history = await self.repo \
            .list_anchors(limit=90)
        baseline = None
        if history:
            first = history[-1]
            baseline = float(
                first.get("avgPrice")
                or 0)
        if not baseline \
                or baseline <= 0:
            baseline = avg_price  # 首期自锚
        power = round(
            baseline / avg_price, 4) \
            if avg_price > 0 else 1.0
        today = datetime.now(UTC) \
            .strftime("%Y-%m-%d")
        # 同日幂等(重算覆盖)
        existing = next(
            (a for a in history
             if a.get("anchorDate")
             == today), None)
        if existing:
            anchor_id = int(
                existing.get("anchorId"))
            existing.update({
                "avgPrice": avg_price,
                "purchasingPower":
                    power,
                "samples": samples,
                "baseline": baseline,
                "updatedAt": ts(),
            })
            await self.repo.save_anchor(
                existing, create=False)
        else:
            anchor_id = await \
                self.repo.next_anchor_id()
            await self.repo.save_anchor({
                "anchorId": anchor_id,
                "anchorDate": today,
                "avgPrice": avg_price,
                "purchasingPower": power,
                "samples": samples,
                "baseline": baseline,
                "trend": "",
                "alarms": [],
                "createdAt": ts(),
                "updatedAt": ts(),
            })
        return {
            "success": True,
            "skipped": False,
            "anchorId": anchor_id,
            "anchorDate": today,
            "avgPrice": avg_price,
            "purchasingPower": power,
            "samples": samples,
            "baseline": baseline,
            "note": "购买力指数日快照——"
                    f"均价 {avg_price}/基准 "
                    f"{baseline}="
                    f"指数 {power}",
            "generatedAt": ts(),
        }

    async def anchors_view(
            self, limit: int = 30
    ) -> dict:
        """指数序列+趋势预警
        (观测面——不受开关影响)"""
        history = await self.repo \
            .list_anchors(limit=limit)
        seq = sorted(
            history,
            key=lambda a: str(
                a.get("anchorDate")))
        alarms = self._trend_alarms(seq)
        latest = seq[-1] if seq else {}
        return {
            "success": True,
            "total": len(seq),
            "latest": {
                "anchorDate": latest.get(
                    "anchorDate"),
                "avgPrice": latest.get(
                    "avgPrice"),
                "purchasingPower":
                    latest.get(
                        "purchasingPower"),
                "samples": latest.get(
                    "samples"),
            },
            "series": [
                {"anchorDate": a.get(
                    "anchorDate"),
                 "avgPrice": a.get(
                    "avgPrice"),
                 "purchasingPower":
                    a.get(
                        "purchasingPower"),
                 "samples": a.get(
                    "samples")}
                for a in seq],
            "alarms": alarms,
            "note": "购买力指数序列——"
                    "趋势预警仅建议"
                    "(人工/46号)",
            "generatedAt": ts(),
        }

    @staticmethod
    def _trend_alarms(seq: list
                      ) -> list:
        """通胀/通缩预警(连续 3 日
        单边且累计波幅 >10%)"""
        if len(seq) < ANCHOR_TREND_DAYS:
            return []
        tail = seq[-ANCHOR_TREND_DAYS:]
        powers = [
            float(a.get(
                "purchasingPower")
                or 0) for a in tail]
        if any(p <= 0
               for p in powers):
            return []
        drops = all(
            powers[i] > powers[i + 1]
            for i in range(
                len(powers) - 1))
        rises = all(
            powers[i] < powers[i + 1]
            for i in range(
                len(powers) - 1))
        drift = round(
            abs(powers[-1] - powers[0])
            / powers[0], 4)
        if drift <= ANCHOR_TREND_DRIFT:
            return []
        if drops:
            return [{
                "type": "inflation",
                "severity": "medium",
                "drift": drift,
                "note": f"指数连续 "
                        f"{ANCHOR_TREND_DAYS}"
                        f" 日下行且累计 "
                        f"{drift:.1%}"
                        f"(通胀预警——"
                        f"建议: 消耗场景扩充/"
                        f"兑换率下调经 46号)",
                "autoExecute": False,
            }]
        if rises:
            return [{
                "type": "deflation",
                "severity": "medium",
                "drift": drift,
                "note": f"指数连续 "
                        f"{ANCHOR_TREND_DAYS}"
                        f" 日上行且累计 "
                        f"{drift:.1%}"
                        f"(通缩预警——"
                        f"建议: 消耗激励经 "
                        f"46号)",
                "autoExecute": False,
            }]
        return []

    # ============================================================
    # ② 供需预警(品类维度)
    # ============================================================

    async def supply_demand_scan(
            self) -> dict:
        """供需扫描(需求激增——
        仅建议+事件留痕)"""
        orders = await self.repo.list_orders(
            limit=500)
        paid = [
            o for o in orders
            if o.get("status") in PAID_STATES]
        now = datetime.now(UTC)
        by_product = {}
        for o in paid:
            p = o.get("product") or ""
            if not p:
                continue
            dt = _parse_dt(
                o.get("paidAt")
                or o.get("createdAt"))
            if dt is None:
                continue
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=UTC)
            by_product.setdefault(
                p, []).append(
                (o, dt))
        alerts = []
        for p, items in sorted(
                by_product.items()):
            last24 = [
                o for o, dt in items
                if now - dt
                <= timedelta(hours=24)]
            prior = [
                o for o, dt in items
                if timedelta(hours=24)
                < now - dt
                <= timedelta(
                    days=ANCHOR_WINDOW_DAYS)]
            daily_avg = round(
                len(prior)
                / ANCHOR_WINDOW_DAYS, 2)
            if len(last24) \
                    >= DEMAND_SPIKE_MIN \
                    and daily_avg > 0 \
                    and len(last24) \
                    > daily_avg \
                    * DEMAND_SPIKE_MULTIPLE:
                alerts.append({
                    "product": p,
                    "count24h": len(last24),
                    "priorDailyAvg":
                        daily_avg,
                    "multiple": round(
                        len(last24)
                        / daily_avg, 2),
                    "suggestion":
                        "需求激增——运营建议"
                        "(补货/限购; 人工决策)",
                    "autoExecute": False,
                })
        if alerts:
            try:
                await self.repo.add_event({
                    "eventId": await
                    self.repo.next_event_id(),
                    "orderId": 0,
                    "eventType":
                        "supply_demand",
                    "detail": {
                        "alerts": alerts},
                    "createdAt": ts(),
                })
            except Exception as exc:
                logger.warning(
                    "xx64_sd_event_failed: %s",
                    exc)
        return {
            "success": True,
            "scannedProducts": len(
                by_product),
            "alerts": alerts,
            "note": "供需预警——仅运营"
                    "建议(与 P3 ARB-MA 边界: "
                    "总量趋势非账号集中度)",
            "generatedAt": ts(),
        }

    # ============================================================
    # ③ 兑换率校准建议(46号双模)
    # ============================================================

    async def rate_check(self
                         ) -> dict:
        """兑换率失衡检查(积分获取/
        信值消耗——持续越界提交
        46号 config 变更建议)"""
        # 近 N 日失衡序列
        days = await self \
            ._imbalance_series(
                RATE_SUSTAIN_DAYS)
        sustained_high = (
            len(days) == RATE_SUSTAIN_DAYS
            and all(
                d["ratio"]
                > RATE_IMBALANCE_HIGH
                for d in days))
        sustained_low = (
            len(days) == RATE_SUSTAIN_DAYS
            and all(
                d["ratio"]
                < RATE_IMBALANCE_LOW
                for d in days))
        result = {
            "success": True,
            "series": days,
            "status": "balanced",
            "submitted": False,
            "generatedAt": ts(),
        }
        if not (sustained_high
                or sustained_low):
            result["note"] = (
                "兑换率均衡"
                f"(失衡比 {RATE_IMBALANCE_LOW}"
                f"-{RATE_IMBALANCE_HIGH})")
            return result
        direction = ("earn_fast"
                     if sustained_high
                     else "burn_fast")
        ratio = days[-1]["ratio"]
        result["status"] = direction
        # 46号提交轨(仅建议)
        submitted, change_id, err = \
            await self._submit_to_gov(
                direction, ratio, days)
        result["submitted"] = submitted
        result["changeId"] = change_id
        result["submitError"] = err
        result["note"] = (
            f"失衡持续 "
            f"{RATE_SUSTAIN_DAYS} 日"
            f"(ratio={ratio}——"
            + ("积分获取过快"
               if sustained_high
               else "信值消耗过快")
            + "; 建议" + ("调低兑换率/"
                          "增加消耗场景"
                          if sustained_high
                          else "调高兑换率/"
                               "缩减消耗")
            + "经 46号审批)")
        return result

    async def _imbalance_series(
            self, days: int) -> list:
        """逐日失衡比序列(近 N 日——
        pointsEarned/100/消耗)"""
        out = []
        now = datetime.now(UTC)
        # 积分获取流水(全站——
        # source≠xx64_exchange 为入账)
        from repositories.points_repository import (
            PointsRepository,
        )
        logs = await (
            PointsRepository()
            .list_logs(user_id=None,
                       limit=500))
        # 信值消耗(ledger debit)
        entries = await self.repo \
            .list_ledger(limit=500)
        for d in range(days):
            day = (now
                   - timedelta(days=d)
                   ).strftime("%Y-%m-%d")
            earned = sum(
                int(l.get("points") or 0)
                for l in logs
                if str(l.get("createdAt")
                       or "").startswith(day)
                and l.get("source")
                != "xx64_exchange"
                and int(l.get("points")
                        or 0) > 0)
            consumed = round(sum(
                abs(float(e.get("amount")
                           or 0))
                for e in entries
                if e.get("direction")
                == "debit"
                and str(e.get(
                    "createdAt") or ""
                ).startswith(day)), 2)
            earned_trust = round(
                earned / POINTS_PER_TRUST,
                2)
            ratio = round(
                earned_trust
                / consumed, 4) \
                if consumed > 0 else (
                99.0 if earned_trust > 0
                else 0.0)
            out.append({
                "date": day,
                "pointsEarned": earned,
                "earnedTrust":
                    earned_trust,
                "trustConsumed":
                    consumed,
                "ratio": ratio,
            })
        return out

    async def _submit_to_gov(
            self, direction: str,
            ratio: float,
            series: list
    ) -> tuple:
        """46号 submit_change 提交轨
        (pending 冲突/异常 fail-soft
        跳过——不重复提交)"""
        try:
            from services.ai_governance_service import (
                AiGovernanceService,
            )
            change = await (
                AiGovernanceService()
                .submit_change(
                    scorer_id=SCORER_ID,
                    kind="config",
                    payload={
                        "suggestion": {
                            "direction":
                                direction,
                            "ratio": ratio,
                            "sustainedDays":
                                len(series),
                        },
                        "before": {
                            "pointsPerTrust":
                                POINTS_PER_TRUST,
                        },
                        "after": {
                            "note": "建议值由"
                                    "人工评估"
                                    "确定(宪法域"
                                    "R1-R7 占比"
                                    "结构永不变)",
                        },
                    },
                    reason=f"兑换率失衡 "
                           f"ratio={ratio} "
                           f"持续 {len(series)}"
                           f" 日({direction}"
                           f"——64号 P4 校准建议)",
                    requested_by="xx64-p4"))
            return (True,
                    change.get("changeId"),
                    "")
        except (ValueError, KeyError,
                Exception) as exc:
            logger.info(
                "xx64_gov_submit_skip: %s",
                exc)
            return (False, 0,
                    str(exc)[:120])

    # ============================================================
    # ④ 阈值域观测(46号落地参数)
    # ============================================================

    async def thresholds_view(
            self) -> dict:
        """当前阈值域+46号 pending
        变更视图(观测面)"""
        thresholds = await self.repo \
            .list_thresholds(limit=10)
        pending = []
        try:
            from services.ai_governance_service import (
                AiGovernanceService,
            )
            changes = await (
                AiGovernanceService()
                .list_changes(
                    status="pending",
                    scorer_id=SCORER_ID))
            pending = changes.get(
                "changes") or []
        except Exception as exc:
            logger.info(
                "xx64_gov_view_skip: %s",
                exc)
        from services.xx64_registry import (
            CUMULATIVE_QUOTA_RATIO,
            POINTS_DAILY_LIMIT,
            POINTS_FROZEN_HOURS,
            SINGLE_QUOTA_RATIO,
            WINDOW_DAYS,
        )
        return {
            "success": True,
            "constitution": {
                "trustPortion": "30%(R1 永久"
                                "不可校准)",
                "cashPortion": "70%(R1)",
                "pointsPerTrust":
                    "100:1(R6 宪法域)",
            },
            "calibratable": {
                "singleQuotaRatio":
                    SINGLE_QUOTA_RATIO,
                "cumulativeQuotaRatio":
                    CUMULATIVE_QUOTA_RATIO,
                "windowDays": WINDOW_DAYS,
                "pointsDailyLimit":
                    POINTS_DAILY_LIMIT,
                "pointsFrozenHours":
                    POINTS_FROZEN_HOURS,
            },
            "overrides": thresholds,
            "govPending": [
                {"changeId": c.get(
                    "changeId"),
                 "kind": c.get("kind"),
                 "reason": c.get("reason"),
                 "requestedAt": c.get(
                    "requestedAt")}
                for c in pending],
            "note": "阈值域——校准仅经"
                    "46号审批(宪法域占比"
                    "结构永不可变)",
            "generatedAt": ts(),
        }
