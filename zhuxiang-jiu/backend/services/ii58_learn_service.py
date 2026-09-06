"""58号·决策回流+信值联动
(ii58_learn_service, P4)

计划(docs/58号_AI智能优化意图识别算法模块实施计划.md
§6.3/§九 P4):
    六类真值信号(44号池双写——第33档案):
        识别记录(evaluations)终态判定→
        信号源+reward→44号 submit_feedback
        (intent_orchestration)→幂等回写
        pooledFeedbackId(evaluationId 1:1)
    高置信错误预警:
        单轮回流 high_conf_error ≥3 →
        阈值调整建议自动提交(upper+0.02——
        建议性; 经 46号留痕+镜像 pending)
        ——生效唯一出口仍是人工终审
        (优化永不自动生效铁律)

回流轨不受 II58_MODE 影响(管理面——
57号 feedback/collect 范式)。

铁律(QC):
    - 回流幂等: evaluationId 1:1
      (pooledFeedbackId 终态跳过)
    - 校准建议人工审批: 预警仅提交建议
      (pending), 生效走 review_calibration
"""

import logging

from core.helpers import ts

from repositories.ii58_repository import (
    Ii58Repository,
)

logger = logging.getLogger("ii58_learn_service")

MODEL_VERSION = "v1-ii58-learn"

SCORER_ID = "intent_orchestration"

# 六类真值信号 reward 表(计划 §6.3)
SIGNAL_REWARDS = {
    # 识别正确+执行成功(置信度分级正确)
    "correct_executed": 1.0,
    # 识别正确+负反馈(高置信错误——弱满足,
    # 阈值偏松预警)
    "weak_negative": 0.3,
    # 澄清后完成(partial 域——澄清策略正确)
    "clarify_completed": 0.8,
    # 高置信识别错误(置信度校准失准)
    "high_conf_error": -0.8,
    # 澄清拒绝/转人工(语料覆盖缺口)
    "coverage_gap": -0.5,
    # 越界拦截正确(边界识别正确)
    "boundary_correct": 0.6,
    # 对抗混淆命中(易混淆域降权失效)
    "adversarial_confusion": -0.6,
}

# 高置信门槛(高置信错误判定——upper 基线)
HIGH_CONFIDENCE = 0.9

# 单轮回流高置信错误预警阈值
CALIBRATION_ALERT_THRESHOLD = 3

# 校准预警步长(上限收紧)
CALIBRATION_ALERT_STEP = 0.02

# 单轮回流扫描上限
COLLECT_LIMIT = 500


class Ii58LearnService:
    """58号决策回流+信值联动(P4)"""

    def __init__(self):
        self.repo = Ii58Repository()

    # ============================================================
    # 回流入口(管理面——不受开关影响)
    # ============================================================

    async def collect_feedback(self,
                               limit: int = COLLECT_LIMIT
                               ) -> dict:
        """触发一轮决策回流(识别记录终态扫描
        →六类真值信号→44号池双写)

        幂等: evaluationId 1:1(pooledFeedbackId
        终态跳过)。
        """
        evaluations = await self.repo.list_evaluations(
            limit=limit)

        # 显式反馈映射(evalId→最新一条)
        explicit = {}
        for fb in await self.repo.list_feedback(
                kind="explicit", limit=10000):
            explicit[int(fb.get("evalId") or 0)] = fb

        # approved 标注映射(evalId→label)
        approved = {}
        for lb in await self.repo.list_labels(
                limit=10000):
            if lb.get("status") == "approved":
                approved[int(lb.get("evalId") or 0)] = lb

        summary = {
            "scanned": len(evaluations),
            "labeled": 0, "skipped": 0,
            "poolSubmitted": 0, "poolFailed": 0,
            "signals": {}, "errors": [],
            "collectedAt": ts(),
        }
        high_conf_errors = 0

        for evaluation in evaluations:
            try:
                outcome = await self._process(
                    evaluation, explicit, approved)
                if outcome.get("kind") != "labeled":
                    summary["skipped"] += 1
                    continue
                summary["labeled"] += 1
                source = outcome["source"]
                summary["signals"][source] = \
                    summary["signals"].get(
                        source, 0) + 1
                if source == "high_conf_error":
                    high_conf_errors += 1
                if outcome.get("poolSubmitted"):
                    summary["poolSubmitted"] += 1
                elif outcome.get("poolFailed"):
                    summary["poolFailed"] += 1
            except Exception as exc:  # noqa: BLE001
                summary["errors"].append(
                    f"eval={evaluation.get('evalId')}"
                    f":{str(exc)[:60]}")
                logger.warning(
                    "ii58_collect_failed %s: %s",
                    evaluation.get("evalId"), exc)

        # 高置信错误预警(阈值调整建议——
        # 经 46号留痕+镜像 pending, 人工终审生效)
        alert = await self._calibration_alert(
            high_conf_errors)
        if alert:
            summary["calibrationAlert"] = alert

        summary["success"] = True
        summary["note"] = ("决策回流——六类真值信号"
                          "标注+44号池双写(第33档案)")
        return summary

    # ============================================================
    # 单记录信号判定
    # ============================================================

    async def _process(self, evaluation: dict,
                       explicit: dict,
                       approved: dict) -> dict:
        """单条识别记录信号判定+池双写+幂等回写"""
        eval_id = int(evaluation.get("evalId") or 0)

        # 幂等: 已入池标记(pooledFeedbackId>0)
        if int(evaluation.get("pooledFeedbackId")
               or 0) > 0:
            return {"kind": "skip",
                    "reason": "already_pooled"}

        signal = self._label(evaluation, explicit,
                             approved)
        if signal is None:
            return {"kind": "skip",
                    "reason": "not_terminal"}

        source = signal["source"]
        reward = SIGNAL_REWARDS[source]

        # 44号池双写(第33档案)
        pool_id, pool_err = await \
            self._write_pool(evaluation, source,
                             reward)

        # 识别记录回写 pooled 标记(幂等)
        fresh = await self.repo.get_evaluation(
            eval_id)
        if fresh is not None:
            fresh["pooledFeedbackId"] = pool_id or 0
            fresh["poolSignal"] = source
            fresh["poolReward"] = reward
            fresh["evalCount"] = int(
                fresh.get("evalCount") or 0) + 1
            fresh["updatedAt"] = ts()
            await self.repo.save_evaluation(
                fresh, create=False)

        # 回流事件留痕
        await self._track(eval_id, "learn_signal", {
            "evalId": eval_id,
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
    def _label(evaluation: dict,
               explicit: dict,
               approved: dict) -> dict | None:
        """信号判定(终态优先序)

        判定序:
            ① boundaryIntercepted →
               boundary_correct(边界识别正确)
            ② 显式反馈+纠正意图(会员真值
               最强): 高置信(≥0.9)→
               high_conf_error / 中低置信→
               weak_negative
            ③ 对抗降权命中 →
               adversarial_confusion(输入命中
               对抗文本——易混淆域事件)
            ④ clarify → coverage_gap
               (语料覆盖缺口)
            ⑤ partial+approved 标注 →
               clarify_completed / 无标注
               跳过(非终态)
            ⑥ resolved → correct_executed
        """
        state = str(evaluation.get("state") or "")
        confidence = float(
            evaluation.get("confidence") or 0)
        attr = evaluation.get("attribution") or {}
        eval_id = int(evaluation.get("evalId") or 0)

        # ① 越界拦截正确
        if evaluation.get("boundaryIntercepted"):
            return {"source": "boundary_correct"}

        # ② 显式反馈(会员纠正——真值最强)
        fb = explicit.get(eval_id)
        if fb is not None:
            corrected = str(
                fb.get("correctedIntentId") or "")
            original = str(
                evaluation.get("intentId") or "")
            if corrected and corrected != original:
                if confidence >= HIGH_CONFIDENCE:
                    return {"source":
                            "high_conf_error"}
                return {"source":
                        "weak_negative"}

        # ③ 对抗混淆命中(输入命中对抗文本
        #    ——降权生效 clarify 亦为混淆事件)
        if attr.get("adversarialPenalty"):
            return {"source":
                    "adversarial_confusion"}

        # ④ 澄清拒绝(语料覆盖缺口)
        if state == "clarify":
            return {"source": "coverage_gap"}

        # ⑤ partial: 标注 approved → 澄清后完成;
        #    无标注非终态跳过(等待人工回流)
        if state == "partial":
            if eval_id in approved:
                return {"source":
                        "clarify_completed"}
            return None

        # ⑥ 识别正确+执行成功
        if state == "resolved":
            return {"source":
                    "correct_executed"}
        return None

    # ============================================================
    # 44号池双写(第33档案)
    # ============================================================

    async def _write_pool(self, evaluation: dict,
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
            from services.ii58_scorer import (
                Ii58Scorer,
            )
            # 因子快照(第33档案口径——
            # 识别上下文评分)
            scored = await Ii58Scorer().score({
                "intentId":
                    evaluation.get("intentId"),
                "state":
                    evaluation.get("state"),
                "confidence":
                    evaluation.get("confidence"),
                "boundaryIntercepted":
                    evaluation.get(
                        "boundaryIntercepted"),
                "corpusHits":
                    evaluation.get("corpusHits"),
            })
            result = await submit_feedback({
                "scorerId": SCORER_ID,
                "factors":
                    scored.get("factors") or [],
                "scoreAtDecision": float(
                    scored.get("trustScore") or 0),
                "actualAction": "evaluate",
                "expectedAction": "evaluate"
                if reward > 0 else "clarify",
                "correct": reward > 0,
                "reward": reward,
                "note": f"ii58:{source}:evalId="
                        f"{evaluation.get('evalId')}",
                "source": "ii58_pipeline",
            })
            return result.get("feedbackId"), ""
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "ii58_pool_write_failed: %s", exc)
            return None, str(exc)[:80]

    # ============================================================
    # 高置信错误校准预警
    # ============================================================

    async def _calibration_alert(
            self, high_conf_errors: int) -> dict | None:
        """高置信错误预警(high_conf_error≥阈值 →
        阈值调整建议自动提交)

        建议性提交(pending)——生效唯一出口=
        人工终审(review_calibration); 队列纪律
        (已有 pending 跳过留痕)。
        """
        if high_conf_errors < \
                CALIBRATION_ALERT_THRESHOLD:
            return None
        try:
            from services.ii58_service import (
                Ii58Service,
            )
            svc = Ii58Service()
            upper, lower, _ = await \
                svc._effective_baseline()
            proposed_upper = round(min(
                0.99, upper
                + CALIBRATION_ALERT_STEP), 4)
            result = await \
                svc.submit_calibration_proposal(
                    proposed_upper, lower,
                    reason=f"高置信错误预警: 单轮回流"
                    f"high_conf_error={high_conf_errors}"
                    f"≥{CALIBRATION_ALERT_THRESHOLD}"
                    f"(阈值偏松——建议上限收紧 "
                    f"{CALIBRATION_ALERT_STEP})",
                    requested_by="ii58_alert")
            return {
                "triggered": True,
                "highConfErrors": high_conf_errors,
                "changeId": result.get("changeId"),
                "proposedUpper": proposed_upper,
                "status": result.get("status"),
                "note": "预警建议已提交(pending)——"
                        "人工终审 review 后生效",
            }
        except ValueError as exc:
            # 队列纪律(已有 pending)——留痕跳过
            return {
                "triggered": True,
                "highConfErrors": high_conf_errors,
                "skipped": str(exc)[:120],
                "note": "已有待终审校准(队列纪律"
                        "——预警跳过)",
            }
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "ii58_calibration_alert_failed: %s",
                exc)
            return None

    # ============================================================
    # 内部
    # ============================================================

    async def _track(self, eval_id: int,
                     event_type: str,
                     detail: dict) -> None:
        try:
            event_id = await \
                self.repo.next_event_id()
            await self.repo.add_event({
                "eventId": event_id,
                "evalId": int(eval_id or 0),
                "eventType": event_type,
                "detail": detail,
                "createdAt": ts(),
            })
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "ii58_learn_track_failed: %s", exc)
