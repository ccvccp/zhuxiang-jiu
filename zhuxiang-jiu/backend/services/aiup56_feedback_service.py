"""56号·AI智能升级管理 决策回流管道
(aiup56_feedback_service, P4)

计划(docs/56号_AI智能升级管理模块实施计划.md §七):
    七类真值信号源(提案终态+审批交互):

    | 信号源                     | reward |
    |---------------------------|--------|
    | 提案批准+交付+灰度达标      | +1.0   |
    | 提案批准+交付+价值达成≥90%  | +0.8   |
    | 提案批准+价值未达预估       | +0.3   |
    | 驳回后同类信号复发          | -0.6   |
    | 交付后灰度异常回滚          | -0.8   |
    | 审计否决被人工推翻          | -0.5   |
    | 预算熔断频繁               | -0.4   |

44号池双写(第31档案 upgrade_orchestration
学习闭环数据源——54/55号 P2 范式全继承):
    - 幂等: proposalId 1:1(labeled 终态跳过)
    - 上下文重构(评分时点近似)
    - _ts_utc 时区统一

collect 采集口径(全链事件驱动——proposal
终态 + reviews 记录):
    approved→delivered 事件 → 灰度达标(+1.0)
    delivered+actualGain/estimatedGain≥0.9(+0.8)
    delivered+actualGain 低(+0.3)
    reject 后同信号提案复发(-0.6)
    rollback 事件(-0.8)
    audit rejected 后 approve(-0.5)
    budget_halted 沙箱记录多现(-0.4)
"""

import logging
from datetime import datetime, timezone

from core.helpers import ts

from repositories.aiup56_repository import (
    Aiup56Repository,
)

logger = logging.getLogger("aiup56_feedback_service")

MODEL_VERSION = "v1-aiup56-feedback"

SCORER_ID = "upgrade_orchestration"

SIGNAL_REWARDS = {
    "deliver_success": 1.0,
    "value_achieved": 0.8,
    "value_missed": 0.3,
    "reject_recurrence": -0.6,
    "rollback_after_deliver": -0.8,
    "veto_overturned": -0.5,
    "budget_halt_frequent": -0.4,
}

SIGNAL_NAMES = {
    "deliver_success": "交付+灰度达标",
    "value_achieved": "价值达成率≥90%",
    "value_missed": "价值未达预估",
    "reject_recurrence": "驳回后同类复发",
    "rollback_after_deliver": "交付后回滚",
    "veto_overturned": "审计否决被推翻",
    "budget_halt_frequent": "预算熔断频繁",
}

# 预算熔断频发阈值(提案历史沙箱中出现次数)
BUDGET_HALT_THRESHOLD = 2

# 价值达成率判定(actualGain/estimatedGain)
VALUE_ACHIEVE_RATE = 0.9


def _ts_utc(value) -> datetime | None:
    try:
        dt = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


class Aiup56FeedbackService:
    """56号决策回流管道(七类信号→44号池双写)"""

    def __init__(self):
        self.repo = Aiup56Repository()

    # ============================================================
    # 回流入口
    # ============================================================

    async def collect_feedback(self,
                               limit: int = 100
                               ) -> dict:
        """触发一轮回流标注(提案终态+审批交互扫描
        →七类信号→44号池双写)

        幂等: proposalId 1:1(已入池终态跳过)。
        注: 回流不依赖 AIUP56_MODE(管理面)。
        """
        proposals = await self.repo.list_proposals(
            limit=limit)

        summary = {
            "scanned": len(proposals),
            "labeled": 0, "skipped": 0,
            "poolSubmitted": 0, "poolFailed": 0,
            "signals": {}, "errors": [],
            "collectedAt": ts(),
        }

        for proposal in proposals:
            try:
                outcome = await \
                    self._process_proposal(proposal)
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
                    f"proposal={proposal.get(
                        'proposalId')}:"
                    f"{str(exc)[:60]}")
                logger.warning(
                    "aiup56_label_failed %s: %s",
                    proposal.get("proposalId"), exc)

        summary["success"] = True
        summary["note"] = ("决策回流——七类信号真值"
                           "标注+44号池双写")
        return summary

    # ============================================================
    # 单提案信号判定
    # ============================================================

    async def _process_proposal(self,
                                proposal: dict
                                ) -> dict:
        pid = int(proposal.get("proposalId") or 0)
        status = str(proposal.get("status") or "")

        # 幂等: 已入池标记(pooledFeedbackId>0)
        if int(proposal.get("pooledFeedbackId")
               or 0) > 0:
            return {"kind": "skip",
                    "reason": "already_pooled"}

        signal = self._label(proposal)
        if signal is None:
            return {"kind": "skip",
                    "reason": f"no_signal:{status}"}

        source = signal["source"]
        reward = SIGNAL_REWARDS[source]

        # 44号池双写(上下文+八因子快照)
        pool_id, pool_err = await \
            self._write_pool(proposal, signal, reward)

        # 提案回写 pooled 标记(幂等)
        proposal["pooledFeedbackId"] = \
            pool_id or 0
        proposal["poolSignal"] = source
        proposal["poolReward"] = reward
        await self.repo.save_proposal(
            proposal, create=False)

        # 回流事件留痕
        await self._track(pid, "learn_signal", {
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
    def _label(proposal: dict) -> dict | None:
        """信号判定(状态机+数值口径)"""
        status = str(proposal.get("status") or "")

        if status == "delivered":
            # 灰度达标(交付未回滚——观察期末态近拟)
            actual = float(
                proposal.get("actualGain") or 0)
            estimated = float(
                proposal.get("estimatedGain") or 0)
            if estimated > 0 \
                    and actual / estimated \
                    >= VALUE_ACHIEVE_RATE:
                return {"source": "value_achieved"}
            if actual > 0:
                return {"source": "value_missed"}
            return {"source": "deliver_success"}

        if status == "rolled_back":
            return {"source":
                    "rollback_after_deliver"}

        if status == "approved":
            # 审计否决被推翻(auditVerdict rejected
            # 但仍走到 approved——人工推翻)
            if str(proposal.get("auditVerdict")
                   or "") == "rejected":
                return {"source": "veto_overturned"}
            return None

        if status == "planned" \
                and str(proposal.get(
                    "reviewVerdict") or "") \
                == "rejected":
            return {"source": "reject_recurrence"}

        # 预算熔断频发(沙箱历史)
        sandbox_verdict = str(
            proposal.get("testVerdict") or "")
        if sandbox_verdict == "budget_halted":
            return {"source": "budget_halt_frequent"}

        return None

    # ============================================================
    # 44号池双写(第31档案——54/55号范式)
    # ============================================================

    async def _write_pool(self, proposal: dict,
                          signal: dict,
                          reward: float) -> tuple:
        try:
            from services.ai_learning_service import (
                submit_feedback,
            )
            ctx = await self._build_ctx(proposal)
            from services.aiup56_scorer import (
                Aiup56Scorer,
            )
            scored = await Aiup56Scorer().score(ctx)
            result = await submit_feedback({
                "scorerId": SCORER_ID,
                "factors":
                    scored.get("factors") or [],
                "scoreAtDecision": float(
                    scored.get("trustScore") or 0),
                "actualAction": "deliver",
                "expectedAction": "deliver"
                if reward > 0 else "defer",
                "correct": reward > 0,
                "reward": reward,
                "note": f"aiup56:{signal['source']}"
                        f":proposalId="
                        f"{proposal.get('proposalId')}",
                "source": "aiup56_pipeline",
            })
            return result.get("feedbackId"), ""
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "aiup56_pool_write_failed: %s", exc)
            return None, str(exc)[:80]

    @staticmethod
    async def _build_ctx(proposal: dict) -> dict:
        """回流上下文重构(标注时点近似)"""
        snapshot = (proposal.get("signalSnapshot")
                     or {})
        return {
            "signalHits": len(
                snapshot.get("hits") or []),
            "sideCoverage":
                snapshot.get("sideCoverage"),
            "necessityScore":
                proposal.get("necessityScore"),
        }

    # --------------------------------------------------------
    # 内部
    # --------------------------------------------------------

    async def _track(self, proposal_id: int,
                     event_type: str,
                     detail: dict) -> None:
        try:
            event_id = await self.repo.next_event_id()
            await self.repo.add_event({
                "eventId": event_id,
                "proposalId": int(proposal_id),
                "eventType": event_type,
                "detail": detail,
                "createdAt": ts(),
            })
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "aiup56_fb_track_failed: %s", exc)

    # --------------------------------------------------------
    # 回流统计(观测面)
    # --------------------------------------------------------

    async def feedback_stats(self) -> dict:
        """回流统计(信号分布/池双写)"""
        proposals = await self.repo.list_proposals(
            limit=1000)
        by_signal: dict = {}
        pooled = 0
        for p in proposals:
            src = str(p.get("poolSignal") or "")
            if src:
                by_signal[src] = \
                    by_signal.get(src, 0) + 1
            if int(p.get("pooledFeedbackId") or 0) > 0:
                pooled += 1
        return {
            "success": True,
            "total": len(proposals),
            "bySignal": by_signal,
            "poolSubmitted": pooled,
            "signalRewards": SIGNAL_REWARDS,
            "note": "七类信号真值标注——44号池双写"
                    "(第31档案学习闭环)",
            "generatedAt": ts(),
        }
