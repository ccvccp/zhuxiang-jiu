"""62号·AI智能无形资产估值 业务结果验证回流
(av62_learn_service, P4)

计划(docs/62号_AI智能无形资产估值模型实施计划.md
§七 P4):
    ① 估值验证信号(预测贡献度 vs
       实际业务结果)→第37档案池
       双写(assetId 1:1 幂等)
    ② 估值偏差预警(偏差超阈→权重
       复审建议经 46号审批——
       建议人工审批, 不直接生效)
    ③ T+1 调度(衰减批量结算+
       回流——av62_scheduler)

验证信号三档(确定性判定——LLM
不进判定链):
    within_tolerance  偏差≤10%
                      (估值准确, +1)
    moderate_deviation 10%<偏差≤30%
                      (部分偏差, +0.3)
    severe_deviation  偏差>30%
                      (估值偏差, -1)

铁律(计划 §1.3/§六/§八):
    - 回流不受开关影响(回流管理面
      ——60/61号同款 collect 铁律)
    - 幂等: assessId 1:1 双写,
      已池化跳过
    - 偏差预警建议经 46号审批
      (人工终审轨——AI 不可自改
      权重)
    - 数字来自数据层(可溯源)
"""

import logging

from core.helpers import ts

from repositories.av62_repository import (
    Av62Repository,
)

logger = logging.getLogger("av62_learn")

MODEL_VERSION = "v1-av62-learn"

SCORER_ID = "asset_valuation"

# 偏差三档阈值(确定性)
TOLERANCE = 0.10      # 偏差≤10% → 准确
MODERATE_MAX = 0.30   # 10%<偏差≤30% → 部分偏差

# 信号→奖励映射(P2a 连续奖励)
SIGNAL_REWARDS = {
    "within_tolerance": 1.0,
    "moderate_deviation": 0.3,
    "severe_deviation": -1.0,
}

# 偏差预警阈值: severe 占比≥40%
# 且样本≥3 → 权重复审建议
DEVIATION_ALERT_RATIO = 0.40
DEVIATION_ALERT_MIN = 3


def classify_deviation(deviation: float) -> str:
    """偏差三档判定(确定性)"""
    d = abs(float(deviation or 0))
    if d <= TOLERANCE:
        return "within_tolerance"
    if d <= MODERATE_MAX:
        return "moderate_deviation"
    return "severe_deviation"


class Av62LearnService:
    """62号验证回流(P4——验证信号+
    44号池双写+偏差预警)"""

    def __init__(self):
        self.repo = Av62Repository()

    # ============================================================
    # ① 验证提交(业务结果 vs 预测)
    # ============================================================

    async def submit_verification(self,
                                   assess_id: int,
                                   actual_value: float,
                                   verified_by: str = "admin"
                                   ) -> dict:
        """业务结果验证提交(预测 vs 实际
        →偏差三档信号——评估记录挂载)

        Raises:
            KeyError: 评估不存在
            ValueError: 实际值非法
        """
        record = await self.repo.get_assessment(
            int(assess_id))
        if not record:
            raise KeyError(
                f"评估 {assess_id} 不存在")
        try:
            actual = float(actual_value)
        except (TypeError, ValueError):
            raise ValueError(
                "实际结果值须为数值") from None
        if actual < 0:
            raise ValueError(
                "实际结果值不可为负")
        predicted = float(
            record.get("baseValue") or 0)
        if record.get("negative"):
            raise ValueError(
                "负资产无验证语义(仅正资产"
                "贡献度可对实际业务结果)")
        if predicted <= 0:
            raise ValueError(
                f"评估 {assess_id} 预测值非正"
                f"(零值无验证语义)")
        deviation = round(
            abs(actual - predicted)
            / predicted, 4)
        signal = classify_deviation(
            deviation)

        record.update({
            "verification": {
                "actualValue": actual,
                "predictedValue": predicted,
                "deviation": deviation,
                "signal": signal,
                "verifiedBy": str(
                    verified_by or "admin"),
                "verifiedAt": ts(),
            },
            "updatedAt": ts()})
        await self.repo.save_assessment(
            record, create=False)
        await self._track("verify", {
            "assessId": int(assess_id),
            "assetId": int(
                record.get("assetId") or 0),
            "predictedValue": predicted,
            "actualValue": actual,
            "deviation": deviation,
            "signal": signal,
            "verifiedBy": verified_by,
        })
        return {
            "success": True,
            "assessId": int(assess_id),
            "predictedValue": predicted,
            "actualValue": actual,
            "deviation": deviation,
            "signal": signal,
            "reward": SIGNAL_REWARDS[
                signal],
            "note": "业务结果验证已记录——"
                    "回流批处理见 "
                    "collect_verification",
            "verifiedAt": ts(),
        }

    # ============================================================
    # ② 验证回流批处理(44号池双写)
    # ============================================================

    async def collect_verification(self) -> dict:
        """验证回流批处理(已验证未池化
        评估→信号→44号池双写)

        不受开关影响(回流管理面铁律)。
        """
        assessments = await self.repo \
            .list_assessments(limit=500)
        scanned = labeled = skipped = 0
        pool_submitted = 0
        signals: dict = {}

        for record in assessments:
            verification = record.get(
                "verification") or {}
            if not verification:
                continue
            scanned += 1
            if record.get("pooled"):
                skipped += 1
                continue

            signal = str(
                verification.get("signal")
                or "")
            reward = SIGNAL_REWARDS.get(
                signal, 0.0)
            signals[signal] = \
                signals.get(signal, 0) + 1

            pool_id, pool_err = \
                await self._write_pool(
                    record, verification,
                    signal, reward)
            if pool_id is not None:
                pool_submitted += 1
                labeled += 1
                record.update({
                    "pooled": True,
                    "pooledFeedbackId":
                        int(pool_id),
                    "poolSignal": signal,
                    "poolReward": reward,
                    "updatedAt": ts()})
                await self.repo \
                    .save_assessment(
                        record,
                        create=False)
            else:
                logger.warning(
                    "av62_pool_skip assess=%s: %s",
                    record.get("assessId"),
                    pool_err)

        # 偏差预警(建议经 46号)
        alert = await \
            self._deviation_alert(
                signals, scanned - skipped)

        await self._track("learn_collect", {
            "scanned": scanned,
            "labeled": labeled,
            "skipped": skipped,
            "poolSubmitted":
                pool_submitted,
            "signals": signals,
            "deviationAlert": bool(alert),
        })
        return {
            "success": True,
            "scanned": scanned,
            "labeled": labeled,
            "skipped": skipped,
            "poolSubmitted":
                pool_submitted,
            "signals": signals,
            "deviationAlert": alert,
            "note": "验证回流完成——第37档案"
                    "池双写(assessId 1:1 幂等)",
            "collectedAt": ts(),
        }

    # ============================================================
    # ③ 偏差预警(权重复审建议经 46号)
    # ============================================================

    async def _deviation_alert(self,
                               signals: dict,
                               sample: int
                               ) -> dict | None:
        """估值偏差预警(severe 占比超阈
        →权重复审建议 46号 pending——
        人工审批轨)"""
        try:
            severe = int(signals.get(
                "severe_deviation", 0))
            if sample \
                    < DEVIATION_ALERT_MIN:
                return None
            ratio = severe / sample
            if ratio \
                    < DEVIATION_ALERT_RATIO:
                return None

            # 同域已有 pending 建议跳过
            existing = await self.repo \
                .get_threshold(
                    "weight_review")
            if existing \
                    and existing.get(
                        "status") == "pending":
                return {
                    "status": "pending",
                    "note": "权重复审建议已在"
                            "审批中(不重复提交)",
                    "severeRatio": round(
                        ratio, 4)}

            from services.ai_governance_service import (
                AiGovernanceService,
            )
            result = await (
                AiGovernanceService()
                .submit_change(
                    scorer_id=SCORER_ID,
                    kind="config",
                    payload={
                        "recommendation":
                            "weight_review",
                        "severeRatio": round(
                            ratio, 4),
                        "sample": sample,
                    },
                    reason=(
                        f"估值偏差预警: "
                        f"severe 偏差占比 "
                        f"{ratio:.0%}"
                        f"({severe}/{sample})"
                        f"超阈 "
                        f"{DEVIATION_ALERT_RATIO:.0%}"
                        f"——建议第37档案权重"
                        f"复审"),
                    requested_by="av62_learn"))
            change_id = int(
                result.get("changeId") or 0)
            await self.repo.save_threshold({
                "tier": "weight_review",
                "config": {
                    "recommendation":
                        "weight_review",
                    "severeRatio": round(
                        ratio, 4)},
                "status": "pending",
                "changeId": change_id,
                "requestedBy": "av62_learn",
                "reason": "偏差预警自动提交"
                          "(人工终审轨)",
                "appliedBy": "",
                "createdAt": ts(),
                "updatedAt": ts(),
            })
            await self._track(
                "deviation_alert", {
                    "changeId": change_id,
                    "severeRatio": round(
                        ratio, 4),
                    "sample": sample,
                })
            return {
                "status": "pending",
                "changeId": change_id,
                "severeRatio": round(
                    ratio, 4),
                "sample": sample,
                "note": "权重复审建议已提交"
                        "46号审批(人工终审轨"
                        "——AI 不可自改权重)",
            }
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "av62_deviation_alert_failed: %s",
                exc)
            return None

    # ============================================================
    # ④ 衰减批量结算(T+1 调度子任务)
    # ============================================================

    async def settle_decay(self) -> dict:
        """衰减批量结算(全资产流动性
        档案刷新——decaying 惰性标记
        批量触发)

        不受开关影响(结算管理面)。
        """
        assets = await self.repo.list_assets(
            limit=1000)
        refreshed = decaying = 0
        for asset in assets:
            try:
                from services.av62_liquidity_service import (
                    Av62LiquidityService,
                )
                r = await (
                    Av62LiquidityService()
                    .get_profile(int(
                        asset.get("assetId"))))
                refreshed += 1
                if (r.get("profile")
                        or {}).get(
                            "assetStatus") \
                        == "decaying":
                    decaying += 1
            except (KeyError, ValueError):
                continue
        await self._track("decay_settle", {
            "refreshed": refreshed,
            "decaying": decaying,
        })
        return {
            "success": True,
            "refreshed": refreshed,
            "decaying": decaying,
            "note": "衰减批量结算完成——"
                    "流动性档案全量刷新",
            "settledAt": ts(),
        }

    # ============================================================
    # 学习状态观测面
    # ============================================================

    async def learn_status(self) -> dict:
        """回流状态观测面(验证统计+池化
        幂等标记——不受开关影响)"""
        assessments = await self.repo \
            .list_assessments(limit=500)
        verified = [
            a for a in assessments
            if a.get("verification")]
        pooled = [
            a for a in verified
            if a.get("pooled")]
        signals: dict = {}
        for a in verified:
            signal = str(
                (a.get("verification")
                 or {}).get("signal")
                or "")
            if signal:
                signals[signal] = \
                    signals.get(
                        signal, 0) + 1
        return {
            "success": True,
            "totalAssessments":
                len(assessments),
            "verified": len(verified),
            "pooled": len(pooled),
            "pendingCollect":
                len(verified) - len(pooled),
            "signals": signals,
            "thresholds": {
                "tolerance": TOLERANCE,
                "moderateMax": MODERATE_MAX,
                "alertRatio":
                    DEVIATION_ALERT_RATIO,
                "alertMinSamples":
                    DEVIATION_ALERT_MIN,
            },
            "note": "验证回流状态——assessId"
                    " 1:1 幂等(手动触发 "
                    "POST /feedback/collect)",
            "fetchedAt": ts(),
        }

    # --------------------------------------------------------
    # 内部(44号池双写——fail-soft)
    # --------------------------------------------------------

    async def _write_pool(self, record: dict,
                          verification: dict,
                          signal: str,
                          reward: float
                          ) -> tuple:
        """44号池双写(第37档案八因子
        快照——评估实例上下文)

        Returns:
            (pool_feedback_id, error)
        """
        try:
            from services.ai_learning_service import (
                submit_feedback,
            )
            from services.av62_scorer import (
                Av62Scorer,
            )

            # 八因子快照(第37档案口径——
            # 本验证实例上下文, 累计统计)
            stats = await self._factor_stats(
                record, signal)
            scored = await (
                Av62Scorer().score(stats))
            assess_id = int(
                record.get("assessId") or 0)
            result = await submit_feedback({
                "scorerId": SCORER_ID,
                "factors":
                    scored.get("factors")
                    or [],
                "scoreAtDecision": float(
                    scored.get("trustScore")
                    or 0),
                "actualAction": "optimize",
                "expectedAction": "optimize"
                if reward > 0 else "observe",
                "correct": reward > 0,
                "reward": reward,
                "note": f"av62:{signal}:"
                        f"assessId="
                        f"{assess_id}",
                "source": "av62_pipeline",
            })
            return result.get(
                "feedbackId"), ""
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "av62_pool_write_failed: %s",
                exc)
            return None, str(exc)[:200]

    async def _factor_stats(self,
                            record: dict,
                            signal: str
                            ) -> dict:
        """八因子统计上下文(累计口径
        ——数字可溯源数据层)"""
        assessments = await self.repo \
            .list_assessments(limit=500)
        verified = [
            a for a in assessments
            if a.get("verification")]
        # ① 估值准确率(累计)
        accurate = sum(
            1 for a in verified
            if (a.get("verification")
                or {}).get("signal")
            == "within_tolerance")
        va = (accurate / len(verified)
              if verified else 0.5)
        # ② 归因锚定率(绑定规则 ID)
        grounded = sum(
            1 for a in assessments
            if a.get("ruleId"))
        ag = (grounded / len(assessments)
              if assessments else 0.5)
        # ③ 场景折算命中(中性——
        #    折算事件统计 P5 看板口径)
        sf = 0.5
        # ④ 公平性态势(最新报告)
        fp = 0.5
        try:
            reports = await self.repo \
                .list_fairness(limit=1)
            if reports:
                rep = reports[0]
                if rep.get("flagged"):
                    fp = 0.4
                elif not rep.get(
                        "insufficient"):
                    fp = 1.0
        except Exception:  # noqa: BLE001
            pass
        # ⑤ 会员信值(standard 基线)
        # ⑥ 申诉翻转率(反向)
        appeals = await self.repo \
            .list_appeals(limit=200)
        resolved = [
            a for a in appeals
            if a.get("status")
            == "resolved"]
        overturned = [
            a for a in resolved
            if a.get("overturned")]
        ao = (len(overturned)
              / len(resolved)
              if resolved else 0.05)
        # ⑦ 评估时效(即时=达标)
        lp = 1.0
        # ⑧ 要素域覆盖度
        domains = {
            a.get("domain")
            for a in assessments}
        from services.av62_registry import (
            ALL_DOMAINS,
        )
        cb = len(domains & set(
            ALL_DOMAINS)) / len(
            ALL_DOMAINS)
        return {
            "valuationAccuracy": va,
            "attributionGrounded": ag,
            "scenarioFitness": sf,
            "fairnessPosture": fp,
            "tier": "standard",
            "appealOverturnRate": ao,
            "latencyP95Ok": lp,
            "coverageBreadth": cb,
        }

    async def _track(self, event_type: str,
                     detail: dict) -> None:
        try:
            event_id = await \
                self.repo.next_event_id()
            await self.repo.add_event({
                "eventId": event_id,
                "assetId": int(
                    detail.get("assetId")
                    or detail.get(
                        "assessId") or 0),
                "eventType": event_type,
                "detail": detail,
                "createdAt": ts(),
            })
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "av62_track_failed %s: %s",
                event_type, exc)
