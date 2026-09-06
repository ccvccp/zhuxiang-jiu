"""63号·AI智能后台管理 决策回流服务
(ab63_learn_service, P4)

计划(docs/63号_AI智能后台管理模块实施计划.md
§四/§九 P4):
    ① 六类后台事件→第38档案池双写
       (subId 1:1 幂等——pooledFeedbackId
       终态跳过)
    ② 权限/分流阈值校准预警
       (自动过审错误率超阈→经 46号
       审批留痕——人工终审轨)

六类终态信号(submissions 终态扫描):
    l1_auto_clean    L1 自动过审未抽检(正确+1)
    spot_confirmed   L1 抽检复检通过(+1)
    auto_error       L1 抽检驳回(自动过审错误-1)
    human_approved   L2/L3 人工裁决发布(+1)
    human_rejected   人工驳回无翻转(+1——拦截)
    appeal_overturn  申诉翻转(原裁决错误-1)

铁律(计划 §六/§九):
    - 回流 collect 不受开关影响
      (人工铁律——与 review/appeal 同款)
    - 回流幂等: subId 1:1
      (pooledFeedbackId 终态跳过)
    - 预警仅为建议(pending)——
      生效唯一出口=人工终审
"""

import logging

from core.helpers import ts

from repositories.ab63_repository import (
    Ab63Repository,
)

logger = logging.getLogger("ab63_learn")

MODEL_VERSION = "v1-ab63-learn"

SCORER_ID = "admin_orchestration"

# 六类终态信号→奖励值(±1 二元)
SIGNAL_REWARDS = {
    "l1_auto_clean": 1.0,
    "spot_confirmed": 1.0,
    "auto_error": -1.0,
    "human_approved": 1.0,
    "human_rejected": 1.0,
    "appeal_overturn": -1.0,
}

# 回流扫描上限
COLLECT_LIMIT = 500

# 校准预警阈值(自动过审错误率——
# auto_error/(l1 信号总数)≥20% 触发)
AUTO_ERROR_ALERT_RATE = 0.2

# 预警阈值收紧步长(L1 阈值+2)
CALIBRATION_ALERT_STEP = 2.0


class Ab63LearnService:
    """63号决策回流(P4——第38档案池双写)"""

    def __init__(self):
        self.repo = Ab63Repository()

    # ============================================================
    # 回流入口(不受开关影响——人工铁律)
    # ============================================================

    async def collect_feedback(self,
                               limit: int = COLLECT_LIMIT
                               ) -> dict:
        """触发一轮决策回流(submissions
        终态扫描→六类信号→44号池双写)

        幂等: subId 1:1(pooledFeedbackId
        终态跳过)。
        """
        subs = await self.repo.list_submissions(
            limit=limit)

        summary = {
            "scanned": len(subs),
            "labeled": 0, "skipped": 0,
            "poolSubmitted": 0, "poolFailed": 0,
            "signals": {}, "errors": [],
            "collectedAt": ts(),
        }
        auto_signals = 0
        auto_errors = 0

        for sub in subs:
            try:
                outcome = await self._process(sub)
                if outcome.get("kind") != "labeled":
                    summary["skipped"] += 1
                    continue
                summary["labeled"] += 1
                source = outcome["source"]
                summary["signals"][source] = \
                    summary["signals"].get(
                        source, 0) + 1
                if source in ("l1_auto_clean",
                              "spot_confirmed",
                              "auto_error"):
                    auto_signals += 1
                    if source == "auto_error":
                        auto_errors += 1
                if outcome.get("poolSubmitted"):
                    summary["poolSubmitted"] += 1
                elif outcome.get("poolFailed"):
                    summary["poolFailed"] += 1
            except Exception as exc:  # noqa: BLE001
                summary["errors"].append(
                    f"sub={sub.get('subId')}"
                    f":{str(exc)[:60]}")
                logger.warning(
                    "ab63_collect_failed %s: %s",
                    sub.get("subId"), exc)

        # 校准预警(自动过审错误率超阈——
        # 经 46号审批留痕+人工终审)
        alert = await self._calibration_alert(
            auto_signals, auto_errors)
        if alert:
            summary["calibrationAlert"] = alert

        summary["success"] = True
        summary["note"] = ("决策回流——六类终态信号"
                          "+44号池双写(第38档案"
                          " subId 1:1 幂等)")
        return summary

    # ============================================================
    # 单提交信号判定+池双写
    # ============================================================

    async def _process(self, sub: dict) -> dict:
        """单提交信号判定+池双写+幂等回写"""
        sub_id = int(sub.get("subId") or 0)

        # 幂等: 已入池标记(pooledFeedbackId>0)
        if int(sub.get("pooledFeedbackId")
               or 0) > 0:
            return {"kind": "skip",
                    "reason": "already_pooled"}

        signal = self._label(sub)
        if signal is None:
            return {"kind": "skip",
                    "reason": "not_terminal"}

        source = signal["source"]
        reward = SIGNAL_REWARDS[source]

        # 44号池双写(第38档案——fail-soft)
        pool_id, pool_err = await \
            self._write_pool(sub, source, reward)

        # 提交记录回写 pooled 标记(幂等)
        fresh = await self.repo.get_submission(
            sub_id)
        if fresh is not None:
            fresh["pooledFeedbackId"] = \
                pool_id or 0
            fresh["poolSignal"] = source
            fresh["poolReward"] = reward
            fresh["updatedAt"] = ts()
            await self.repo.save_submission(
                fresh, create=False)

        # 回流事件留痕
        await self._track(sub_id, {
            "action": "learn_signal",
            "subId": sub_id,
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
    def _label(sub: dict) -> dict | None:
        """信号判定(终态优先序)

        判定序:
            ① adjusted(申诉翻转) →
               appeal_overturn(原裁决错误)
            ② auto_published 未抽检 →
               l1_auto_clean
            ③ rejected+抽检驳回 →
               auto_error(自动过审错误)
            ④ published+抽检通过 →
               spot_confirmed
            ⑤ published(人工裁决) →
               human_approved
            ⑥ rejected(人工驳回) →
               human_rejected
            ⑦ 其余(抽检待复检/
               pending/disputed/
               deep_review)非终态跳过
        """
        status = str(sub.get("status") or "")
        spot_result = str(sub.get(
            "spotCheckResult") or "")

        # ① 申诉翻转(原裁决错误——负向)
        if status == "adjusted":
            return {"source": "appeal_overturn"}

        # ② L1 自动过审(未抽检)
        if status == "auto_published":
            return {"source": "l1_auto_clean"}

        # ③ 抽检驳回(自动过审错误)
        if status == "rejected" \
                and spot_result == "rejected":
            return {"source": "auto_error"}

        # ④ 抽检通过(维持发布)
        if status == "published" \
                and spot_result == "approved":
            return {"source": "spot_confirmed"}

        # ⑤ 人工发布
        if status == "published":
            return {"source": "human_approved"}

        # ⑥ 人工驳回
        if status == "rejected":
            return {"source": "human_rejected"}

        # ⑦ 非终态(含抽检待复检的
        #    auto_published+spotCheck)
        return None

    # ============================================================
    # 44号池双写(第38档案)
    # ============================================================

    async def _write_pool(self, sub: dict,
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
            from services.ab63_scorer import (
                Ab63Scorer,
            )
            # 因子快照(第38档案口径——
            # 提交上下文评分)
            scored = await Ab63Scorer().score({
                # 护航有效性: 该提交的
                # 护航干预质量(block 拦截
                # = 编辑态正确拦截)
                "guardEffectiveness": {
                    "clean": 1.0,
                    "tip": 0.95,
                    "warn": 0.8,
                    "block": 1.0,
                }.get(str(
                    (sub.get("evidence")
                     or {}).get(
                        "guardLevel")
                    or ""), 0.9),
                # 自动过审准确: 信号映射
                "autoReviewAccuracy": {
                    "l1_auto_clean": 1.0,
                    "spot_confirmed": 1.0,
                    "auto_error": 0.0,
                }.get(source),
                # 审核一致性: AI 预审与人工
                # 同向(驳回有 findings/发布
                # 无 findings=一致)
                "reviewConsistency": {
                    "human_rejected": 1.0,
                    "human_approved": 0.9,
                    "appeal_overturn": 0.0,
                }.get(source),
                # 会员信值 tier 基线
                "tier": str(sub.get("tier")
                            or "standard"),
                # 申诉翻转(本实例翻转=1)
                "appealOverturnRate":
                    1.0 if source
                    == "appeal_overturn"
                    else 0.0,
                # 审核时效(即时裁决=达标)
                "latencyP95Ok": 1.0,
                # 角色域覆盖(角色恒已知)
                "roleCoverage": 1.0,
            })
            result = await submit_feedback({
                "scorerId": SCORER_ID,
                "factors":
                    scored.get("factors") or [],
                "scoreAtDecision": float(
                    scored.get("trustScore")
                    or 0),
                "actualAction": "review",
                "expectedAction": "review"
                if reward > 0 else "escalate",
                "correct": reward > 0,
                "reward": reward,
                "note": f"ab63:{source}:subId="
                        f"{sub.get('subId')}",
                "source": "ab63_pipeline",
            })
            return result.get("feedbackId"), ""
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "ab63_pool_write_failed: %s", exc)
            return None, str(exc)[:200]

    # ============================================================
    # 校准预警(自动过审错误率——经 46号)
    # ============================================================

    async def _calibration_alert(
            self, auto_signals: int,
            auto_errors: int) -> dict | None:
        """自动过审错误率预警(错误率≥阈值
        →分流阈值收紧建议提交 46号审批)

        建议性提交(pending)——生效唯一出口=
        人工终审(calibrate_apply); 队列纪律
        (已有 pending 跳过留痕)。
        """
        if auto_signals <= 0:
            return None
        error_rate = auto_errors / auto_signals
        if error_rate < AUTO_ERROR_ALERT_RATE:
            return None
        try:
            from services.ab63_registry import (
                L1_THRESHOLD, L2_THRESHOLD,
            )
            from services.ab63_submission_service import (
                Ab63SubmissionService,
            )
            proposed_l1 = round(min(
                99.0, L1_THRESHOLD
                + CALIBRATION_ALERT_STEP), 1)
            result = await (
                Ab63SubmissionService()
                .calibrate_submit(
                    l1_threshold=proposed_l1,
                    l2_threshold=L2_THRESHOLD,
                    requested_by="ab63_alert",
                    reason=f"自动过审错误率预警: "
                           f"错误 {auto_errors}/"
                           f"{auto_signals}="
                           f"{error_rate:.0%}"
                           f"≥{AUTO_ERROR_ALERT_RATE:.0%}"
                           f"(阈值偏松——建议 L1 收紧 "
                           f"{CALIBRATION_ALERT_STEP})"))
            return {
                "triggered": True,
                "autoSignals": auto_signals,
                "autoErrors": auto_errors,
                "errorRate": round(
                    error_rate * 100, 1),
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
                "autoSignals": auto_signals,
                "autoErrors": auto_errors,
                "skipped": str(exc)[:120],
                "note": "已有待终审校准(队列纪律"
                        "——预警跳过)",
            }
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "ab63_calibration_alert_failed: %s",
                exc)
            return None

    # ============================================================
    # 内部
    # ============================================================

    async def _track(self, sub_id: int,
                     detail: dict) -> None:
        try:
            event_id = await \
                self.repo.next_event_id()
            await self.repo.add_event({
                "eventId": event_id,
                "grantId": int(sub_id or 0),
                "eventType": "learn_signal",
                "detail": detail,
                "createdAt": ts(),
            })
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "ab63_learn_track_failed: %s",
                exc)
