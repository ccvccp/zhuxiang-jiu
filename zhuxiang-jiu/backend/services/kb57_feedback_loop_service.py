"""57号·AI智能知识库 决策回流管道
(kb57_feedback_loop_service, P4)

计划(docs/57号_AI智能知识库模块实施计划.md §七):
    六类真值信号源(种子终态+反馈计数+召回):

    | 信号源                     | reward |
    |---------------------------|--------|
    | 种子发布+被有效使用        | +1.0   |
    | 种子高价值(正反馈率≥80%)  | +0.8   |
    | 种子弱满足(有使用但低正)  | +0.3   |
    | 缺口驳回后同类信号复发     | -0.6   |
    | 种子召回(误导下架)        | -0.8   |
    | 合规否决被人工推翻         | -0.5   |
    | 预算熔断频繁              | -0.4   |

44号池双写(第32档案 knowledge_orchestration
学习闭环数据源——55/56号 P2/P4 范式全继承):
    - 幂等: seedId 1:1(pooledFeedbackId 终态跳过)
    - 上下文重构(评分时点近似)
    - _ts_utc 时区统一

误导召回补偿: kb57_review_service.recall
预留接口 → P4 补偿执行器(compensate——
45号 L2 platform_conduct 56号 rollback 范式)。
"""

import logging
from datetime import datetime, timezone

from core.helpers import ts

from repositories.kb57_repository import (
    Kb57Repository,
)

logger = logging.getLogger(
    "kb57_feedback_loop_service")

MODEL_VERSION = "v1-kb57-feedback-loop"

SCORER_ID = "knowledge_orchestration"

SIGNAL_REWARDS = {
    "seed_effective": 1.0,
    "seed_high_value": 0.8,
    "seed_weak": 0.3,
    "gap_reject_recurrence": -0.6,
    "seed_recalled": -0.8,
    "compliance_overturned": -0.5,
    "budget_halt_frequent": -0.4,
}

SIGNAL_NAMES = {
    "seed_effective": "种子被有效使用",
    "seed_high_value": "种子高价值(正反馈≥80%)",
    "seed_weak": "种子弱满足(低正反馈)",
    "gap_reject_recurrence": "缺口驳回后同类复发",
    "seed_recalled": "种子召回(误导下架)",
    "compliance_overturned": "合规否决被推翻",
    "budget_halt_frequent": "预算熔断频繁",
}

# 高价值正反馈率阈值
HIGH_VALUE_RATE = 0.8

# 预算熔断频发阈值(资源域 halted 计数)
BUDGET_HALT_THRESHOLD = 2


def _ts_utc(value) -> datetime | None:
    try:
        dt = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


class Kb57FeedbackLoopService:
    """57号决策回流管道(六类信号→44号池双写)"""

    def __init__(self):
        self.repo = Kb57Repository()

    # ============================================================
    # 回流入口
    # ============================================================

    async def collect_feedback(self,
                                limit: int = 100
                                ) -> dict:
        """触发一轮回流标注(种子终态+反馈计数
        扫描→六类信号→44号池双写)

        幂等: seedId 1:1(pooledFeedbackId 终态跳过)。
        注: 回流不依赖 KB57_MODE(管理面)。
        """
        seeds = await self.repo.list_seeds(
            limit=limit)

        summary = {
            "scanned": len(seeds),
            "labeled": 0, "skipped": 0,
            "poolSubmitted": 0, "poolFailed": 0,
            "signals": {}, "errors": [],
            "collectedAt": ts(),
        }

        for seed in seeds:
            try:
                outcome = await \
                    self._process_seed(seed)
                if outcome.get("kind") == "labeled":
                    summary["labeled"] += 1
                    source = outcome["source"]
                    summary["signals"][source] = \
                        summary["signals"].get(
                            source, 0) + 1
                    if outcome.get("poolSubmitted"):
                        summary["poolSubmitted"] += 1
                    elif outcome.get("poolFailed"):
                        summary["poolFailed"] += 1
                else:
                    summary["skipped"] += 1
            except Exception as exc:  # noqa: BLE001
                summary["errors"].append(
                    f"seed={seed.get('seedId')}:"
                    f"{str(exc)[:60]}")
                logger.warning(
                    "kb57_label_failed %s: %s",
                    seed.get("seedId"), exc)

        # 缺口复发域(独立于种子——gap 级扫描)
        gap_outcomes = await \
            self._process_gap_recurrences()
        for outcome in gap_outcomes:
            summary["labeled"] += 1
            source = outcome["source"]
            summary["signals"][source] = \
                summary["signals"].get(
                    source, 0) + 1
            if outcome.get("poolSubmitted"):
                summary["poolSubmitted"] += 1
            elif outcome.get("poolFailed"):
                summary["poolFailed"] += 1

        summary["success"] = True
        summary["note"] = ("决策回流——六类信号真值"
                           "标注+44号池双写")
        return summary

    # ============================================================
    # 单种子信号判定
    # ============================================================

    async def _process_seed(self,
                            seed: dict) -> dict:
        seed_id = int(seed.get("seedId") or 0)
        status = str(seed.get("status") or "")

        # 幂等: 已入池标记(pooledFeedbackId>0)
        if int(seed.get("pooledFeedbackId")
               or 0) > 0:
            return {"kind": "skip",
                    "reason": "already_pooled"}

        signal = self._label(seed)
        if signal is None:
            return {"kind": "skip",
                    "reason": f"no_signal:{status}"}

        source = signal["source"]
        reward = SIGNAL_REWARDS[source]

        # 44号池双写(上下文+八因子快照)
        pool_id, pool_err = await \
            self._write_pool(seed, signal, reward)

        # 种子回写 pooled 标记(幂等)
        fresh = await self.repo.get_seed(seed_id)
        if fresh is not None:
            fresh["pooledFeedbackId"] = \
                pool_id or 0
            fresh["poolSignal"] = source
            fresh["poolReward"] = reward
            fresh["updatedAt"] = ts()
            await self.repo.save_seed(
                fresh, create=False)

        # 回流事件留痕
        await self._track(
            int(seed.get("gapId") or 0),
            "learn_signal", {
                "seedId": seed_id,
                "source": source,
                "reward": reward,
                "poolFeedbackId": pool_id or 0,
            })

        return {
            "kind": "labeled", "source": source,
            "reward": reward,
            "poolSubmitted": pool_id is not None,
            "poolFailed": pool_id is None,
        }

    @staticmethod
    def _label(seed: dict) -> dict | None:
        """种子信号判定(状态机+反馈计数口径)"""
        status = str(seed.get("status") or "")
        positive = int(
            seed.get("positiveCount") or 0)
        negative = int(
            seed.get("negativeCount") or 0)
        views = int(seed.get("viewCount") or 0)

        # 召回终态(误导下架)
        if status == "recalled":
            return {"source": "seed_recalled"}

        # 发布态被有效使用
        if status in ("published", "boosted",
                      "downgraded", "retired"):
            total_fb = positive + negative
            if views >= 3 and total_fb >= 2:
                if total_fb \
                        and positive / total_fb \
                        >= HIGH_VALUE_RATE:
                    return {"source":
                            "seed_high_value"}
                return {"source": "seed_weak"}
            if views >= 3:
                return {"source":
                        "seed_effective"}
            return None

        # 驳回态(缺 effective 证据——
        # 弱满足口径: 驳回即预估偏差)
        if status == "rejected":
            return None

        return None

    # ============================================================
    # 缺口复发域(gap 级——驳回后同类信号复发)
    # ============================================================

    async def _process_gap_recurrences(self
                                       ) -> list:
        """缺口驳回复发扫描(resolved→reopened
        的同类信号域——gap 无 pooled 标记, 用
        gapRecurrence 标记幂等)"""
        outcomes = []
        gaps = await self.repo.list_gaps(
            limit=200)
        for gap in gaps:
            if gap.get("gapRecurrence"):
                continue   # 已标注
            snap = gap.get("signalSnapshot") or {}
            hits = snap.get("hits") or []
            if not hits:
                continue
            # 仅对 resolved 缺口标注复发风险
            # (观察 open 态不标)
            if gap.get("status") != "resolved":
                continue
            gap_id = int(gap.get("gapId") or 0)
            source = "gap_reject_recurrence"
            reward = SIGNAL_REWARDS[source]
            pool_id, _ = await self._write_gap_pool(
                gap, reward)
            gap["gapRecurrence"] = True
            gap["poolReward"] = reward
            gap["updatedAt"] = ts()
            await self.repo.save_gap(
                gap, create=False)
            await self._track(
                gap_id, "learn_signal", {
                    "gapId": gap_id,
                    "source": source,
                    "reward": reward,
                    "poolFeedbackId":
                        pool_id or 0,
                })
            outcomes.append({
                "kind": "labeled",
                "source": source,
                "reward": reward,
                "poolSubmitted":
                    pool_id is not None,
                "poolFailed":
                    pool_id is None,
            })
        return outcomes

    # ============================================================
    # 44号池双写(第32档案——55/56号范式)
    # ============================================================

    async def _write_pool(self, seed: dict,
                          signal: dict,
                          reward: float) -> tuple:
        try:
            from services.ai_learning_service import (
                submit_feedback,
            )
            ctx = await self._build_ctx(seed)
            from services.kb57_scorer import (
                Kb57Scorer,
            )
            scored = await Kb57Scorer().score(ctx)
            result = await submit_feedback({
                "scorerId": SCORER_ID,
                "factors":
                    scored.get("factors") or [],
                "scoreAtDecision": float(
                    scored.get("trustScore") or 0),
                "actualAction": "collect",
                "expectedAction": "collect"
                if reward > 0 else "defer",
                "correct": reward > 0,
                "reward": reward,
                "note": f"kb57:{signal['source']}"
                        f":seedId="
                        f"{seed.get('seedId')}",
                "source": "kb57_pipeline",
            })
            return result.get("feedbackId"), ""
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "kb57_pool_write_failed: %s", exc)
            return None, str(exc)[:80]

    async def _write_gap_pool(self, gap: dict,
                              reward: float
                              ) -> tuple:
        try:
            from services.ai_learning_service import (
                submit_feedback,
            )
            ctx = {
                "signalHits": len(
                    (gap.get("signalSnapshot")
                     or {}).get("hits") or []),
                "necessityScore":
                    gap.get("necessityScore"),
            }
            from services.kb57_scorer import (
                Kb57Scorer,
            )
            scored = await Kb57Scorer().score(ctx)
            result = await submit_feedback({
                "scorerId": SCORER_ID,
                "factors":
                    scored.get("factors") or [],
                "scoreAtDecision": float(
                    scored.get("trustScore") or 0),
                "actualAction": "collect",
                "expectedAction": "collect"
                if reward > 0 else "defer",
                "correct": reward > 0,
                "reward": reward,
                "note": f"kb57:gap_reject_recurrence"
                        f":gapId="
                        f"{gap.get('gapId')}",
                "source": "kb57_pipeline",
            })
            return result.get("feedbackId"), ""
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "kb57_gap_pool_failed: %s", exc)
            return None, str(exc)[:80]

    @staticmethod
    async def _build_ctx(seed: dict) -> dict:
        """回流上下文重构(标注时点近似)"""
        return {
            "signalHits": 1,
            "sourceHealth": float(
                seed.get("sourceCredibility")
                or 0),
        }

    # ============================================================
    # 误导召回补偿执行器(45号 L2——P4 联动)
    # ============================================================

    async def compensate_recall(
            self, member_ids: list,
            seed_id: int,
            reason: str = "") -> dict:
        """误导召回受影响用户信值补偿
        (45号 L2 platform_conduct 正向抚慰
        ——56号 rollback 补偿范式)"""
        results = []
        compensated = 0
        for member_id in (member_ids or []):
            ok = await self._compensate(
                int(member_id), int(seed_id),
                reason)
            results.append({
                "memberId": int(member_id),
                "compensated": ok})
            if ok:
                compensated += 1
        result = {
            "success": True,
            "seedId": int(seed_id),
            "attempted": len(member_ids or []),
            "compensated": compensated,
            "results": results,
            "note": "误导召回补偿——45号 L2 正向"
                    "抚慰(56号 rollback 范式)",
            "compensatedAt": ts(),
        }
        if member_ids:
            await self._track(
                0, "recall_compensate", {
                    "seedId": int(seed_id),
                    "attempted":
                        result["attempted"],
                    "compensated": compensated,
                })
        return result

    @staticmethod
    async def _compensate(member_id: int,
                          seed_id: int,
                          reason: str) -> bool:
        """单用户补偿(45号 deposit 验真; fail-soft)"""
        try:
            from services.trust_radar_service import (
                TrustRadarService,
            )
            evidence = (
                f"kb57 误导召回受害者补偿(种子 "
                f"{seed_id}, 会员 {member_id}"
                f"——{reason or '内容误导风险'}, "
                f"平台抚慰口径)")
            deposit = await TrustRadarService(
            ).submit_deposit(
                int(member_id), "L2",
                "platform_conduct",
                observed=1.0,
                peer_baseline=0.0,
                evidence=evidence,
                summary="误导召回受影响用户补偿"
                        "(57号知识库)",
                sources=["kb57_pipeline",
                         "event_audit"],
                voluntary=False,
                verify_mode="v1")
            return bool(deposit.get("verified"))
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "kb57_compensate_failed %s: %s",
                member_id, exc)
            return False

    # --------------------------------------------------------
    # 内部
    # --------------------------------------------------------

    async def _track(self, gap_id: int,
                     event_type: str,
                     detail: dict) -> None:
        try:
            event_id = await self.repo.next_event_id()
            await self.repo.add_event({
                "eventId": event_id,
                "gapId": int(gap_id or 0),
                "eventType": event_type,
                "detail": detail,
                "createdAt": ts(),
            })
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "kb57_fb_track_failed: %s", exc)

    # --------------------------------------------------------
    # 回流统计(观测面)
    # --------------------------------------------------------

    async def feedback_stats(self) -> dict:
        """回流统计(信号分布/池双写)"""
        seeds = await self.repo.list_seeds(
            limit=1000)
        by_signal: dict = {}
        pooled = 0
        for s in seeds:
            src = str(s.get("poolSignal") or "")
            if src:
                by_signal[src] = \
                    by_signal.get(src, 0) + 1
            if int(s.get("pooledFeedbackId")
                   or 0) > 0:
                pooled += 1
        gaps = await self.repo.list_gaps(limit=1000)
        gap_pooled = sum(
            1 for g in gaps
            if g.get("gapRecurrence"))
        return {
            "success": True,
            "seedTotal": len(seeds),
            "bySignal": by_signal,
            "poolSubmitted": pooled + gap_pooled,
            "signalRewards": SIGNAL_REWARDS,
            "note": "六类信号真值标注——44号池双写"
                    "(第32档案学习闭环)",
            "generatedAt": ts(),
        }
