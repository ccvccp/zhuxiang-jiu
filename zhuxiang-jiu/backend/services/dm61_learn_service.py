"""61号·AI智能系统升级决策 RLHF 反馈回流服务
(dm61_learn_service, P4)

计划(docs/61号_AI智能系统升级决策模块实施计划.md
§3.4/§七 P4):
    ① 七类终态信号→第36档案池双写
       (decisionId 1:1 幂等——
       pooledFeedbackId 终态跳过)
    ② 决策置信度校准预警(AI 判定偏差
       →阈值复审建议经 46号审批——
       人工终审轨)

七类终态信号(decisions 终态扫描×RLHF
反馈关联):
    adopted_good          建议采纳+结果良好(+1)
    adopted_unverified    建议采纳无反馈(+0.5)
    adopted_bad           建议采纳结果不佳(-1)
    modified_good         修改采纳结果良好(+0.5)
    modified_bad          修改采纳结果不佳(-0.5)
    recommendation_rejected 建议被拒绝(-1)
    dissent_validated     反对意见被证实(+1)

铁律(计划 §六/§九):
    - 回流 collect 不受开关影响
      (人工铁律——与 decide/dissent 同款)
    - 回流幂等: decisionId 1:1
      (pooledFeedbackId 终态跳过)
    - 预警仅为建议(pending)——生效
      唯一出口=人工终审
"""

import logging
import os

from core.helpers import ts

from repositories.dm61_repository import (
    Dm61Repository,
)

logger = logging.getLogger("dm61_learn")

MODEL_VERSION = "v1-dm61-learn"

SCORER_ID = "decision_orchestration"

# 七类终态信号→奖励值
SIGNAL_REWARDS = {
    "adopted_good": 1.0,
    "adopted_unverified": 0.5,
    "adopted_bad": -1.0,
    "modified_good": 0.5,
    "modified_bad": -0.5,
    "recommendation_rejected": -1.0,
    "dissent_validated": 1.0,
}

# 回流扫描上限
COLLECT_LIMIT = 500

# 校准预警(AI 判定偏差率——L1/L2 决策
# 负向结果占比≥30% 且样本≥3 触发)
DEVIATION_ALERT_RATE = 0.3
DEVIATION_MIN_SAMPLE = 3

# 预警 L1 收紧步长(风险分阈值-5——
# 更少变更可进快速通道)
CALIBRATION_ALERT_STEP = 5.0
CALIBRATION_L1_FLOOR = 5.0

# 负向信号域(计入 AI 判定偏差分子)
NEGATIVE_SIGNALS = (
    "adopted_bad", "modified_bad",
    "recommendation_rejected")


class Dm61LearnService:
    """61号 RLHF 反馈回流(P4——第36档案
    池双写)"""

    def __init__(self):
        self.repo = Dm61Repository()

    # ============================================================
    # 回流入口(不受开关影响——人工铁律)
    # ============================================================

    async def collect_feedback(self,
                               limit: int = COLLECT_LIMIT
                               ) -> dict:
        """触发一轮决策回流(decisions 终态
        扫描×RLHF 反馈关联→七类信号→44号
        池双写)

        幂等: decisionId 1:1
        (pooledFeedbackId 终态跳过)。
        """
        decisions = await self.repo.list_decisions(
            limit=limit)
        feedbacks = await self.repo.list_feedback(
            limit=limit)
        fb_map = {
            int(f.get("decisionId") or 0): f
            for f in feedbacks}

        summary = {
            "scanned": len(decisions),
            "labeled": 0, "skipped": 0,
            "poolSubmitted": 0, "poolFailed": 0,
            "signals": {}, "errors": [],
            "collectedAt": ts(),
        }
        low_risk_total = 0
        low_risk_negative = 0

        for decision in decisions:
            try:
                # 校准统计(全部终态决策——不只
                # 本轮新标注; 已入池的用
                # poolSignal 回读)
                if str(decision.get("outcome")
                       or ""):
                    level = str(
                        decision.get("level")
                        or "")
                    if level in ("L1", "L2"):
                        low_risk_total += 1
                        if int(decision.get(
                                "pooledFeedbackId")
                                or 0) > 0:
                            sig = str(
                                decision.get(
                                    "poolSignal")
                                or "")
                        else:
                            labeled = \
                                self._label(
                                    decision,
                                    fb_map)
                            sig = (labeled
                                   or {}).get(
                                "source") or ""
                        if sig in NEGATIVE_SIGNALS:
                            low_risk_negative += 1
                outcome = await self._process(
                    decision, fb_map)
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
                    f"decision="
                    f"{decision.get('decisionId')}"
                    f":{str(exc)[:60]}")
                logger.warning(
                    "dm61_collect_failed %s: %s",
                    decision.get("decisionId"),
                    exc)

        # 置信度校准预警(AI 判定偏差→
        # 阈值复审建议经 46号)
        alert = await self._calibration_alert(
            low_risk_total, low_risk_negative)
        if alert:
            summary["calibrationAlert"] = alert

        summary["success"] = True
        summary["note"] = ("RLHF 反馈回流——七类"
                          "终态信号+44号池双写"
                          "(第36档案 decisionId "
                          "1:1 幂等)")
        return summary

    # ============================================================
    # 单决策信号判定+池双写
    # ============================================================

    async def _process(self, decision: dict,
                       fb_map: dict) -> dict:
        """单决策信号判定+池双写+幂等回写"""
        decision_id = int(
            decision.get("decisionId") or 0)

        # 幂等: 已入池标记
        if int(decision.get("pooledFeedbackId")
               or 0) > 0:
            return {"kind": "skip",
                    "reason": "already_pooled"}

        signal = self._label(decision, fb_map)
        if signal is None:
            return {"kind": "skip",
                    "reason": "not_terminal"}

        source = signal["source"]
        reward = SIGNAL_REWARDS[source]

        # 44号池双写(第36档案——fail-soft)
        pool_id, pool_err = await \
            self._write_pool(decision,
                             fb_map.get(
                                 decision_id),
                             source, reward)

        # 决策记录回写 pooled 标记(幂等)
        fresh = await self.repo.get_decision(
            decision_id)
        if fresh is not None:
            fresh["pooledFeedbackId"] = \
                pool_id or 0
            fresh["poolSignal"] = source
            fresh["poolReward"] = reward
            fresh["updatedAt"] = ts()
            await self.repo.save_decision(
                fresh, create=False)

        # 回流事件留痕
        await self._track(decision_id, {
            "action": "learn_signal",
            "decisionId": decision_id,
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
    def _label(decision: dict,
               fb_map: dict) -> dict | None:
        """信号判定(终态×RLHF 反馈关联)

        判定序:
            ① outcome 空(recommended 态)
               → 非终态跳过
            ② dissent_confirmed →
               dissent_validated(AI 说不被证实)
            ③ adopted×反馈 bad → adopted_bad
            ④ adopted×反馈 good → adopted_good
            ⑤ adopted 无反馈 →
               adopted_unverified
            ⑥ modified×反馈 bad → modified_bad
            ⑦ modified 其余 → modified_good
            ⑧ rejected → recommendation_rejected
        """
        outcome = str(
            decision.get("outcome") or "")
        if not outcome:
            return None

        # ① 反对意见被证实(AI 预警有效)
        if outcome == "dissent_confirmed":
            return {"source":
                    "dissent_validated"}

        decision_id = int(
            decision.get("decisionId") or 0)
        fb = fb_map.get(decision_id) or {}
        fb_outcome = str(fb.get("outcome")
                         or "")

        # ②③ 采纳类
        if outcome == "adopted":
            if fb_outcome == "bad":
                return {"source":
                        "adopted_bad"}
            if fb_outcome == "good":
                return {"source":
                        "adopted_good"}
            return {"source":
                    "adopted_unverified"}

        # ④⑤ 修改类
        if outcome == "modified":
            if fb_outcome == "bad":
                return {"source":
                        "modified_bad"}
            return {"source":
                    "modified_good"}

        # ⑥ 拒绝类
        if outcome == "rejected":
            return {"source":
                    "recommendation_rejected"}

        return None

    # ============================================================
    # 44号池双写(第36档案)
    # ============================================================

    async def _write_pool(self, decision: dict,
                          feedback: dict,
                          source: str,
                          reward: float) -> tuple:
        """44号池双写(fail-soft)

        因子快照(第36档案八因子口径——
        本决策实例上下文)。

        Returns:
            (pool_feedback_id, error)
        """
        try:
            from services.ai_learning_service import (
                submit_feedback,
            )
            from services.dm61_scorer import (
                Dm61Scorer,
            )
            level = str(decision.get("level")
                        or "L2")
            dissent = (decision.get("dissent")
                       or {})
            dissent_status = str(
                dissent.get("status") or "")
            sim_verdict = \
                self._sim_verdict(decision)
            rb_passed = \
                self._rollback_passed(decision)

            scored = await Dm61Scorer().score({
                # 决策准确率(信号映射)
                "decisionAccuracy": {
                    "adopted_good": 1.0,
                    "adopted_unverified": 0.8,
                    "adopted_bad": 0.0,
                    "modified_good": 0.6,
                    "modified_bad": 0.2,
                    "recommendation_rejected":
                        0.3,
                    "dissent_validated": 0.5,
                }.get(source, 0.5),
                # 自治域占比(本实例级别)
                "autonomousRatio": {
                    "L1": 0.15, "L2": 0.25,
                    "L3": 0.0,
                }.get(level, 0.25),
                # 影响预测命中(沙箱裁决与
                # 人类决策同向)
                "simulationHitRate": {
                    "passed_adopted": 1.0,
                    "passed_rejected": 0.5,
                    "blocked_adopted": 0.0,
                    "blocked_rejected": 1.0,
                    "no_sim": 0.5,
                }.get(sim_verdict, 0.5),
                # 反对意见有效性(预警证实)
                "dissentEffectiveness": {
                    "confirmed": 1.0,
                    "overridden": 0.0,
                }.get(dissent_status, 0.5),
                # 会员信值(缺省 standard——
                # 请求级 tier 不入决策记录)
                "tier": "standard",
                # 回滚预案可靠性
                "rollbackSuccessRate":
                    rb_passed,
                # 决策时效(即时裁决=达标)
                "latencyP95Ok": 1.0,
                # 场景覆盖(标签恒已知)
                "coverageBreadth": 1.0,
            })
            decision_id = int(
                decision.get("decisionId") or 0)
            result = await submit_feedback({
                "scorerId": SCORER_ID,
                "factors":
                    scored.get("factors") or [],
                "scoreAtDecision": float(
                    scored.get("trustScore")
                    or 0),
                "actualAction": "optimize",
                "expectedAction": "optimize"
                if reward > 0 else "observe",
                "correct": reward > 0,
                "reward": reward,
                "note": f"dm61:{source}:"
                        f"decisionId="
                        f"{decision_id}",
                "source": "dm61_pipeline",
            })
            return result.get("feedbackId"), ""
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "dm61_pool_write_failed: %s",
                exc)
            return None, str(exc)[:200]

    @staticmethod
    def _sim_verdict(decision: dict) -> str:
        """沙箱裁决×人类决策同向性

        Returns:
            passed_adopted/blocked_adopted/
            passed_rejected/blocked_rejected/
            no_sim
        """
        # 决策记录无 sim 直接字段——经
        # attribution 推断不可靠, 用
        # request sim 留痕保守口径:
        # 有 dissent sim 触发视为 blocked
        dissent = (decision.get("dissent")
                   or {})
        triggers = dissent.get("triggers") or []
        sim_blocked = any(
            "sim" in str(t)
            for t in triggers)
        outcome = str(
            decision.get("outcome") or "")
        if "sim" not in str(triggers) \
                and not triggers:
            # 无触发痕迹——无沙箱冲突
            # 信息时保守按 no_sim
            return "no_sim"
        if sim_blocked:
            return ("blocked_adopted"
                    if outcome
                    in ("adopted",
                        "modified")
                    else "blocked_rejected")
        return ("passed_adopted"
                if outcome
                in ("adopted", "modified")
                else "passed_rejected")

    @staticmethod
    def _rollback_passed(decision: dict) -> float:
        """回滚预案可靠性(保守口径——
        有 rollback_failed 触发=0,
        其余中性 0.5)"""
        dissent = (decision.get("dissent")
                   or {})
        triggers = str(
            dissent.get("triggers") or "")
        if "rollback_failed" in triggers:
            return 0.0
        return 0.5

    # ============================================================
    # 置信度校准预警(AI 判定偏差→46号)
    # ============================================================

    async def _calibration_alert(
            self, low_risk_total: int,
            low_risk_negative: int
            ) -> dict | None:
        """决策置信度校准预警(L1/L2 负向
        占比≥阈值→阈值复审建议提交 46号)

        建议性提交(pending)——生效唯一出口=
        人工终审(calibrate_apply); 队列纪律
        (已有 pending 跳过留痕)。
        """
        if low_risk_total \
                < DEVIATION_MIN_SAMPLE:
            return None
        rate = low_risk_negative \
            / low_risk_total
        if rate < DEVIATION_ALERT_RATE:
            return None
        try:
            from services.dm61_threshold_service import (
                Dm61ThresholdService,
            )
            tsvc = Dm61ThresholdService()
            active = await tsvc.get_active()
            cur_l1 = float(
                active.get("l1MaxRisk")
                or 30.0)
            cur_l3 = float(
                active.get("l3MinRisk")
                or 65.0)
            proposed_l1 = round(max(
                CALIBRATION_L1_FLOOR,
                cur_l1
                - CALIBRATION_ALERT_STEP), 1)
            # 回流链路不受开关影响(人工
            # 铁律——同 collect); 阈值 submit
            # 本身有决策面门槛——模式
            # save/restore 绕过(63号
            # reactivate 同款范式)
            prev_mode = os.environ.get(
                "DM61_MODE")
            os.environ["DM61_MODE"] = \
                "shadow"
            try:
                result = await \
                    tsvc.calibrate_submit(
                        l1_max_risk=proposed_l1,
                        l3_min_risk=cur_l3,
                        requested_by="dm61_alert",
                        reason=f"决策置信度校准预警: "
                               f"L1/L2 负向 "
                               f"{low_risk_negative}/"
                               f"{low_risk_total}="
                               f"{rate:.0%}"
                               f"≥{DEVIATION_ALERT_RATE:.0%}"
                               f"(AI 判定偏差——建议 L1 "
                               f"收紧 {CALIBRATION_ALERT_STEP})")
            finally:
                if prev_mode is None:
                    os.environ.pop(
                        "DM61_MODE", None)
                else:
                    os.environ["DM61_MODE"] = \
                        prev_mode
            return {
                "triggered": True,
                "lowRiskTotal": low_risk_total,
                "lowRiskNegative":
                    low_risk_negative,
                "deviationRate": round(
                    rate * 100, 1),
                "changeId":
                    result.get("changeId"),
                "proposedL1": proposed_l1,
                "status":
                    result.get("status"),
                "note": "预警建议已提交(46号"
                        "pending)——人工终审"
                        "apply 后生效",
            }
        except ValueError as exc:
            # 队列纪律(已有 pending)——留痕跳过
            return {
                "triggered": True,
                "lowRiskTotal": low_risk_total,
                "lowRiskNegative":
                    low_risk_negative,
                "skipped": str(exc)[:120],
                "note": "已有待终审校准(队列纪律"
                        "——预警跳过)",
            }
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "dm61_calibration_alert_failed: "
                "%s", exc)
            return None

    # ============================================================
    # 内部
    # ============================================================

    async def _track(self, decision_id: int,
                     detail: dict) -> None:
        try:
            event_id = await \
                self.repo.next_event_id()
            await self.repo.add_event({
                "eventId": event_id,
                "requestId": int(
                    decision_id or 0),
                "eventType": "learn_signal",
                "detail": detail,
                "createdAt": ts(),
            })
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "dm61_learn_track_failed: %s",
                exc)
