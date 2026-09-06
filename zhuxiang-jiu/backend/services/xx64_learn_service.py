"""64号·信值兑换管理 价值回流服务
(xx64_learn_service, P4)

计划(docs/64号_P4_价值锚定与治理层
详细设计.md §六):
    订单终态扫描 → 八因子信号 →
    44号 submit_feedback 双写

信号判定(设计 §六.1):
    exchange_ok        completed/
                       settled →
                       correct=true
                       (兑换健康+)
    exchange_refunded  refunded →
                       correct=false
                       (结算偏差——供
                       appeal_overturn
                       归因)
    exchange_disputed  disputed →
                       correct=false
                       (风控冻结——供
                       arbitrage_blocked
                       归因)
    (cancelled 中性跳过
     ——不计入反馈)

幂等(QC 铁律——orderId 1:1):
    订单回写 pooledFeedbackId
    (>0 即已入池——跳过);
    双轮 collect 断言第二轮
    labeled=0。

44号 fail-soft(异常不阻塞订单
——仅 poolFailed 计数)。

观测面 learn_status: 回流状态+
因子聚合(八因子观测口径实时算)
+调度状态。
"""

import logging
import os

from datetime import datetime, timedelta, UTC

from core.helpers import ts

from repositories.xx64_repository import (
    Xx64Repository,
)

logger = logging.getLogger("xx64_learn")

MODEL_VERSION = "v1-xx64-learn"

SCORER_ID = "value_exchange"

COLLECT_LIMIT = 200

# 信号→44号反馈标注
SIGNAL_MAP = {
    "exchange_ok": {
        "source": "exchange_ok",
        "correct": True,
    },
    "exchange_refunded": {
        "source":
            "exchange_refunded",
        "correct": False,
    },
    "exchange_disputed": {
        "source":
            "exchange_disputed",
        "correct": False,
    },
}

# 因子聚合窗口(观测面)
FACTOR_WINDOW_DAYS = 30
P95_BUDGET_MS = 500.0


def learn_mode() -> str:
    """回流调度开关
    (XX64_LEARN_MODE——三开关
    铁律, 默认 off)"""
    return os.environ.get(
        "XX64_LEARN_MODE", "off")


def _parse_dt(value) -> datetime | None:
    try:
        return datetime.fromisoformat(
            str(value))
    except (TypeError, ValueError):
        return None


class Xx64LearnService:
    """64号价值回流(P4——第38档案
    信任飞轮)"""

    def __init__(self):
        self.repo = Xx64Repository()

    # ============================================================
    # ① 终态回流(orderId 1:1 幂等)
    # ============================================================

    async def collect_feedback(
            self, limit: int = COLLECT_LIMIT
    ) -> dict:
        """触发一轮订单回流
        (终态扫描→三信号→
        44号池双写)"""
        orders = await self.repo.list_orders(
            limit=limit)
        summary = {
            "scanned": len(orders),
            "labeled": 0,
            "skipped": 0,
            "poolSubmitted": 0,
            "poolFailed": 0,
            "signals": {},
            "errors": [],
            "collectedAt": ts(),
        }
        for order in orders:
            try:
                outcome = await \
                    self._process(order)
                if outcome.get("kind") \
                        != "labeled":
                    summary["skipped"] += 1
                    continue
                summary["labeled"] += 1
                source = outcome["source"]
                summary["signals"][
                    source] = \
                    summary["signals"].get(
                        source, 0) + 1
                if outcome.get(
                        "poolSubmitted"):
                    summary[
                        "poolSubmitted"] += 1
                elif outcome.get(
                        "poolFailed"):
                    summary[
                        "poolFailed"] += 1
            except Exception as exc:
                oid = order.get(
                    "orderId")
                summary["errors"].append(
                    f"order={oid}:"
                    f"{str(exc)[:60]}")
                logger.warning(
                    "xx64_collect_failed %s: "
                    "%s",
                    order.get("orderId"), exc)
        summary["success"] = True
        summary["note"] = (
            "订单终态回流——三信号+"
            "44号池双写(orderId 1:1 "
            "幂等)")
        return summary

    async def _process(self,
                       order: dict) -> dict:
        """单订单信号判定+池双写
        +幂等回写"""
        order_id = int(
            order.get("orderId") or 0)
        # 幂等: 已入池标记
        if int(order.get(
                "pooledFeedbackId")
                or 0) > 0:
            return {"kind": "skip",
                    "reason":
                        "already_pooled"}
        status = order.get("status")
        signal_key = None
        if status in ("completed",
                      "settled"):
            signal_key = "exchange_ok"
        elif status == "refunded":
            signal_key = \
                "exchange_refunded"
        elif status == "disputed":
            signal_key = \
                "exchange_disputed"
        else:
            return {"kind": "skip",
                    "reason":
                        "not_terminal"}
        signal = SIGNAL_MAP[
            signal_key]
        # 44号池双写(fail-soft)
        pool_id, pool_err = await \
            self._write_pool(
                order, signal)
        # 订单回写 pooled 标记
        fresh = await self.repo \
            .get_order(order_id)
        if fresh is not None:
            fresh["pooledFeedbackId"] = \
                pool_id or 0
            fresh["poolSignal"] = \
                signal["source"]
            fresh["poolReward"] = (
                1.0 if signal["correct"]
                else -1.0)
            fresh["updatedAt"] = ts()
            await self.repo.save_order(
                fresh, create=False)
        return {
            "kind": "labeled",
            "source": signal["source"],
            "poolSubmitted":
                bool(pool_id),
            "poolFailed":
                bool(pool_err),
        }

    async def _write_pool(
            self, order: dict,
            signal: dict
    ) -> tuple:
        """44号 submit_feedback
        (fail-soft——异常返回
        (0, err))"""
        try:
            from services.ai_learning_service import (
                submit_feedback,
            )
            result = await submit_feedback({
                "scorerId":
                    SCORER_ID,
                "factors": [
                    {"name": "exchange_health",
                     "score": 1.0
                     if signal["correct"]
                     else 0.0,
                     "weight": 0.20},
                ],
                "scoreAtDecision": 80.0,
                "actualAction":
                    order.get("status"),
                "correct":
                    signal["correct"],
                "reward": 1.0
                if signal["correct"]
                else -1.0,
                "note": "64号回流 "
                        f"orderId="
                        f"{order.get('orderId')}",
                "source":
                    "xx64_exchange",
            })
            return (result.get(
                        "feedbackId")
                    or 0, "")
        except Exception as exc:
            logger.warning(
                "xx64_pool_failed %s: %s",
                order.get("orderId"), exc)
            return (0, str(exc)[:100])

    # ============================================================
    # ② 因子聚合(观测面实时算)
    # ============================================================

    async def factor_aggregates(
            self) -> dict:
        """八因子观测口径(聚合
        ——第38档案回流数据面)"""
        since = datetime.now(UTC) \
            - timedelta(
                days=FACTOR_WINDOW_DAYS)

        def _in_window(rec, key):
            dt = _parse_dt(
                rec.get(key))
            if dt is None:
                return False
            if dt.tzinfo is None:
                dt = dt.replace(
                    tzinfo=UTC)
            return dt >= since

        orders = await self.repo \
            .list_orders(limit=500)
        recent = [
            o for o in orders
            if _in_window(
                o, "createdAt")]
        total = len(recent)
        ok = sum(
            1 for o in recent
            if o.get("status") in (
                "completed", "settled"))
        refunded = sum(
            1 for o in recent
            if o.get("status")
            == "refunded")
        # 申诉翻转率
        appeals = await self.repo \
            .list_appeals(limit=200)
        appeal_total = len(appeals)
        approved = sum(
            1 for a in appeals
            if a.get("status")
            == "approved")
        # 指数波动率(anchor 序列)
        anchors = await self.repo \
            .list_anchors(limit=30)
        powers = [
            float(a.get(
                "purchasingPower")
                or 0)
            for a in anchors
            if float(a.get(
                "purchasingPower")
                or 0) > 0]
        volatility = 0.0
        if len(powers) >= 2:
            mean = sum(powers) \
                / len(powers)
            if mean > 0:
                variance = sum(
                    (p - mean) ** 2
                    for p in powers) \
                    / len(powers)
                volatility = round(
                    (variance ** 0.5)
                    / mean, 4)
        # 结算时效(reserved→paid
        # 时差 P95)
        latencies = []
        for o in recent:
            created = _parse_dt(
                o.get("createdAt"))
            paid = _parse_dt(
                o.get("paidAt"))
            if created and paid:
                if created.tzinfo is None:
                    created = created \
                        .replace(tzinfo=UTC)
                if paid.tzinfo is None:
                    paid = paid.replace(
                        tzinfo=UTC)
                latencies.append(
                    (paid - created)
                    .total_seconds()
                    * 1000)
        latencies.sort()
        p95 = latencies[
            int(len(latencies) * 0.95)] \
            if latencies else 0.0
        return {
            "windowDays":
                FACTOR_WINDOW_DAYS,
            "exchangeHealth":
                round(ok / total, 4)
            if total else None,
            "refundRate":
                round(refunded / total, 4)
            if total else None,
            "appealOverturnRate":
                round(approved
                      / appeal_total, 4)
            if appeal_total else None,
            "anchorVolatility":
                volatility,
            "latencyP95Ms": round(
                p95, 1),
            "latencyP95Ok":
                p95 <= P95_BUDGET_MS
                if latencies else None,
            "orders": total,
            "appeals": appeal_total,
            "note": "八因子观测口径"
                    "(供第38档案/终审参考"
                    "——实时聚合)",
            "generatedAt": ts(),
        }

    # ============================================================
    # ③ 回流状态(观测面)
    # ============================================================

    async def learn_status(
            self) -> dict:
        """回流状态+因子聚合
        +调度状态(观测面)"""
        aggregates = await \
            self.factor_aggregates()
        pooled = await self.repo \
            .list_orders(limit=500)
        pooled_count = sum(
            1 for o in pooled
            if int(o.get(
                "pooledFeedbackId")
                or 0) > 0)
        return {
            "success": True,
            "learnMode": learn_mode(),
            "pooledOrders":
                pooled_count,
            "scorer": {
                "scorerId":
                    SCORER_ID,
                "modelVersion":
                    MODEL_VERSION,
            },
            "factors": aggregates,
            "note": "回流状态——"
                    "XX64_LEARN_MODE 控制"
                    "调度(默认 off); "
                    "collect 可手动触发",
            "generatedAt": ts(),
        }
