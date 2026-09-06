"""60号·AI智能支付管理 支付数据反哺
(pay60_learn_service, P4)

计划(docs/60号_AI智能支付管理模块实施计划.md
§3.4/§七 P4):
    ① 六类支付事件→44号池双写
       (payId 1:1 幂等——第35档案)
    ② 现金流预测(7 日确定性外推
       ——历史模式线性加权+缺口预警)

六类支付事件(计划 §3.4 信任飞轮):
    compliance_streak   按时履约+合规开票(+1.0)
    intent_positive     支付成功意图联动单(+0.6)
    payment_anomaly     支付失败/异常(-0.5)
    refund_dispute      争议/退款(-0.8)
    fraud_confirmed     洗钱/盗刷确认(-1.0)
    long_term_compliance 长期合规记录(+0.3)

铁律(计划 §六/§3.4):
    - collect 回流不受开关影响
      (人工铁律——与 recon/splits settle
      同款)
    - 回流幂等: payId 1:1
      (pooledFeedbackId 终态跳过)
    - 预测确定性(不发 LLM——线性
      外推)
"""

import logging

from core.helpers import ts

from repositories.pay60_repository import (
    Pay60Repository,
)

logger = logging.getLogger("pay60_learn")

MODEL_VERSION = "v1-pay60-learn"

SCORER_ID = "payment_orchestration"

# 六类支付事件→奖励值(±1 封顶——
# 44号 reward 域)
SIGNAL_REWARDS = {
    "compliance_streak": 1.0,
    "intent_positive": 0.6,
    "payment_anomaly": -0.5,
    "refund_dispute": -0.8,
    "fraud_confirmed": -1.0,
    "long_term_compliance": 0.3,
}

# 回流扫描上限
COLLECT_LIMIT = 500

# 现金流预测窗口(7 日)
FORECAST_DAYS = 7

# 资金缺口预警阈值(预测 7 日净流出
# 占 7 日流入比≥该值触发)
GAP_ALERT_RATIO = 0.5


class Pay60LearnService:
    """60号支付数据反哺(P4——信任飞轮)"""

    def __init__(self):
        self.repo = Pay60Repository()

    # ============================================================
    # ① 六类事件回流(44号池双写)
    # ============================================================

    async def collect_feedback(self,
                                limit: int = COLLECT_LIMIT
                                ) -> dict:
        """触发一轮支付事件回流(订单终态
        扫描→六类信号→44号池双写)

        幂等: payId 1:1(pooledFeedbackId
        终态跳过)。
        """
        orders = await self.repo.list_orders(
            limit=limit)

        summary = {
            "scanned": len(orders),
            "labeled": 0, "skipped": 0,
            "poolSubmitted": 0, "poolFailed": 0,
            "signals": {}, "errors": [],
            "collectedAt": ts(),
        }

        for order in orders:
            try:
                outcome = await \
                    self._process(order)
                if outcome.get("kind") != "labeled":
                    summary["skipped"] += 1
                    continue
                summary["labeled"] += 1
                source = outcome["source"]
                summary["signals"][source] = \
                    summary["signals"].get(
                        source, 0) + 1
                if outcome.get("poolSubmitted"):
                    summary["poolSubmitted"] += 1
                elif outcome.get("poolFailed"):
                    summary["poolFailed"] += 1
            except Exception as exc:  # noqa: BLE001
                summary["errors"].append(
                    f"pay={order.get('payId')}"
                    f":{str(exc)[:60]}")
                logger.warning(
                    "pay60_collect_failed %s: %s",
                    order.get("payId"), exc)

        summary["success"] = True
        summary["note"] = ("支付事件回流——六类信号"
                          "+44号池双写(第35档案"
                          " payId 1:1 幂等)")
        return summary

    async def _process(self, order: dict) -> dict:
        """单订单信号判定+池双写+幂等回写"""
        pay_id = int(order.get("payId") or 0)

        # 幂等: 已入池标记(pooledFeedbackId>0)
        if int(order.get(
                "pooledFeedbackId")
                or 0) > 0:
            return {"kind": "skip",
                    "reason": "already_pooled"}

        signal = self._label(order)
        if signal is None:
            return {"kind": "skip",
                    "reason": "not_terminal"}

        source = signal["source"]
        reward = SIGNAL_REWARDS[source]

        # 44号池双写(第35档案——fail-soft)
        pool_id, pool_err = await \
            self._write_pool(order, source,
                             reward)

        # 订单回写 pooled 标记(幂等)
        fresh = await self.repo.get_order(
            pay_id)
        if fresh is not None:
            fresh["pooledFeedbackId"] = \
                pool_id or 0
            fresh["poolSignal"] = source
            fresh["poolReward"] = reward
            fresh["updatedAt"] = ts()
            await self.repo.save_order(
                fresh, create=False)

        # 回流事件留痕
        await self._track(pay_id, {
            "action": "learn_signal",
            "payId": pay_id,
            "source": source,
            "reward": reward,
            "poolFeedbackId": pool_id or 0,
        })

        return {
            "kind": "labeled", "source": source,
            "reward": reward,
            "poolSubmitted": pool_id is not None,
            "poolFailed": pool_id is None,
            "poolError": pool_err,
        }

    @staticmethod
    def _label(order: dict) -> dict | None:
        """信号判定(终态优先序)

        判定序:
            ① fraud_confirmed——AML/欺诈
               命中阻断(reversal 无——
               风控 block 态订单)
            ② refund_dispute——refunded
               终态(冲正/退款)
            ③ payment_anomaly——failed/
               recovering 失败域
            ④ compliance_streak——settled
               完美履约(结算完成)
            ⑤ intent_positive——success
               (意图联动单——intentId>0
               强化+0.6; 普通成功弱信号
               并入 compliance 0.3)
            ⑥ 其余(created/priced/
               verified/executing)非终态
               跳过

        注意: poolReward 与计划表对齐
        (intent_positive +0.6; 其余成功
        单归 long_term_compliance +0.3
        意图联动增益语义)。
        """
        status = str(order.get("status") or "")
        attribution = order.get(
            "attribution") or {}
        # fraud_confirmed: block 态
        # (风控阻断——AML/合规命中)
        verifications = order.get(
            "_riskTiers") or []
        risk_blocked = attribution.get(
            "riskTier") == "block" \
            or "block" in verifications

        # ① 欺诈确认(最强负向)
        if risk_blocked \
                and status in (
                    "priced",):
            return {"source":
                    "fraud_confirmed"}

        # ② 退款/争议终态
        if status == "refunded":
            return {"source":
                    "refund_dispute"}

        # ③ 支付失败域
        if status in ("failed",
                     "recovering"):
            return {"source":
                    "payment_anomaly"}

        # ④ 完美履约(settled)
        if status == "settled":
            return {"source":
                    "compliance_streak"}

        # ⑤ 意图联动成功单
        if status == "success":
            intent_id = attribution.get(
                "intentId") or 0
            if intent_id:
                return {"source":
                        "intent_positive"}
            return {"source":
                    "long_term_compliance"}

        # ⑥ 非终态
        return None

    # ============================================================
    # 44号池双写(第35档案)
    # ============================================================

    async def _write_pool(self, order: dict,
                          source: str,
                          reward: float) -> tuple:
        """44号池双写(fail-soft)

        Returns:
            (pool_feedback_id, error)
        """
        try:
            from services.ai_learning_service import (
                submit_feedback,
            )
            from services.pay60_scorer import (
                Pay60Scorer,
            )
            attribution = order.get(
                "attribution") or {}
            # 因子快照(第35档案口径)
            success_like = source in (
                "compliance_streak",
                "intent_positive",
                "long_term_compliance")
            scored = await (
                Pay60Scorer().score({
                    # 支付成功率: 本实例
                    "paymentSuccessRate":
                        1.0 if success_like
                        else 0.0,
                    # 验证摩擦: riskTier
                    # 直通率
                    "verificationFriction":
                        1.0 if attribution
                        .get("riskTier")
                        == "pass" else 0.8,
                    # 会员信值 tier
                    "tier": str(
                        attribution.get(
                            "tier")
                        or "standard"),
                    # 争议率(本实例)
                    "disputeRate":
                        1.0 if source
                        == "refund_dispute"
                        else 0.0,
                    # 时效达标
                    "latencyP95Ok": 1.0,
                }))
            result = await submit_feedback({
                "scorerId": SCORER_ID,
                "factors":
                    scored.get("factors") or [],
                "scoreAtDecision": float(
                    scored.get("trustScore")
                    or 0),
                "actualAction": "pay",
                "expectedAction": "pay"
                if reward > 0 else "block",
                "correct": reward > 0,
                "reward": reward,
                "note": f"pay60:{source}:"
                        f"payId="
                        f"{order.get('payId')}",
                "source": "pay60_pipeline",
            })
            return result.get("feedbackId"), ""
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "pay60_pool_write_failed: %s",
                exc)
            return None, str(exc)[:200]

    # ============================================================
    # ② 现金流预测(7 日确定性外推)
    # ============================================================

    async def forecast(self) -> dict:
        """现金流预测(7 日窗口确定性外推
        ——历史模式线性加权+缺口预警)

        方法(确定性——不发 LLM):
            历史日均净流(成功流入-退款
            流出)×7 日线性外推;
            近期权重加倍(近 3 日均值
            ×0.6+全期均值×0.4)

        缺口预警: 预测净流出(负净流)
        达流入的 GAP_ALERT_RATIO
        触发(资金安全预警)。
        """
        orders = await self.repo.list_orders(
            limit=1000)

        # 终态现金流统计
        inflows = []   # 成功流入
        outflows = []  # 退款流出
        for o in orders:
            amount = float(
                o.get("finalPrice") or 0)
            status = o.get("status")
            if status in ("success",
                          "settled"):
                inflows.append(amount)
            elif status == "refunded":
                outflows.append(amount)

        total_in = round(sum(inflows), 2)
        total_out = round(
            sum(outflows), 2)
        n = max(1, len(orders))

        # 日均(全期)
        avg_in = round(total_in / n, 2)
        avg_out = round(total_out / n, 2)

        # 近期权重(近 3 单均值——
        # 简化滑动窗确定性)
        recent_n = min(3, len(orders))
        recent = orders[:recent_n]
        recent_in = sum(
            float(o.get("finalPrice") or 0)
            for o in recent
            if o.get("status") in (
                "success", "settled"))
        recent_out = sum(
            float(o.get("finalPrice") or 0)
            for o in recent
            if o.get("status")
            == "refunded")
        recent_avg_in = round(
            recent_in / max(1, recent_n), 2)
        recent_avg_out = round(
            recent_out / max(1, recent_n), 2)

        # 线性加权(近期 0.6+全期 0.4)
        daily_in = round(
            recent_avg_in * 0.6
            + avg_in * 0.4, 2)
        daily_out = round(
            recent_avg_out * 0.6
            + avg_out * 0.4, 2)

        forecast_in = round(
            daily_in * FORECAST_DAYS, 2)
        forecast_out = round(
            daily_out * FORECAST_DAYS, 2)
        forecast_net = round(
            forecast_in - forecast_out, 2)

        # 缺口预警(净流出达流入
        # 预警比)
        gap_alert = (
            forecast_in > 0
            and forecast_out
            / forecast_in
            >= GAP_ALERT_RATIO)

        return {
            "success": True,
            "window": FORECAST_DAYS,
            "history": {
                "totalInflow": total_in,
                "totalOutflow": total_out,
                "orders": len(orders),
                "dailyAvgIn": avg_in,
                "dailyAvgOut": avg_out,
            },
            "forecast": {
                "dailyInflow": daily_in,
                "dailyOutflow": daily_out,
                "inflow":
                    forecast_in,
                "outflow":
                    forecast_out,
                "net": forecast_net,
            },
            "gapAlert": gap_alert,
            "gapAlertRatio":
                GAP_ALERT_RATIO,
            "gapAdvice":
                "资金缺口预警: 预测退款"
                "流出达流入 "
                f"{GAP_ALERT_RATIO:.0%}+"
                "——建议冻结大额出账"
                "并人工复核退款源头"
                if gap_alert else None,
            "note": "现金流预测——7 日"
                    "确定性线性外推"
                    "(近期加权 0.6/全期 0.4; "
                    "不发 LLM)",
            "forecastedAt": ts(),
        }

    # ============================================================
    # 内部
    # ============================================================

    async def _track(self, pay_id: int,
                     detail: dict) -> None:
        try:
            event_id = await \
                self.repo.next_event_id()
            await self.repo.add_event({
                "eventId": event_id,
                "payId": int(pay_id or 0),
                "eventType": "learn_signal",
                "detail": detail,
                "createdAt": ts(),
            })
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "pay60_learn_track_failed: %s",
                exc)
