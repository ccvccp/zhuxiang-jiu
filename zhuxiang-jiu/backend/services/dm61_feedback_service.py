"""61号·AI智能系统升级决策 RLHF 反馈
(dm61_feedback_service, P3)

计划(docs/61号_AI智能系统升级决策模块实施计划.md
§3.4/§七 P3):
    人类对 AI 建议的反馈记录(决策
    feedbackId 1:1):
        - action 三态: adopted 采纳
          /modified 修改/rejected 拒绝
        - outcome 可选: good/bad
          (采纳后执行结果——供 P4
          decision_accuracy 因子)
        - comment 修正内容/评语

铁律:
    - 反馈不受开关影响(人工铁律)
    - P4 collect 池双写第36档案
      (本期仅本表记录)
"""

import logging

from core.helpers import ts

from repositories.dm61_repository import (
    Dm61Repository,
)

logger = logging.getLogger("dm61_feedback")

MODEL_VERSION = "v1-dm61-feedback"

# 反馈三态域(RLHF——人类对 AI 建议
# 的处置)
FEEDBACK_ACTIONS = (
    "adopted",   # 采纳(AI 建议原样执行)
    "modified",  # 修改(采纳但调整)
    "rejected",  # 拒绝(未采纳)
)

# 结果域(采纳后执行结果——可选)
FEEDBACK_OUTCOMES = ("good", "bad")


class Dm61FeedbackService:
    """61号 RLHF 反馈(P3)"""

    def __init__(self):
        self.repo = Dm61Repository()

    # ============================================================
    # 反馈提交(决策 1:1)
    # ============================================================

    async def submit(self, decision_id: int,
                     action: str,
                     outcome: str = None,
                     comment: str = "",
                     by: str = "admin") -> dict:
        """RLHF 反馈提交(不受开关影响)

        Args:
            decision_id: 决策号(1:1)
            action: 三态(adopted/modified/
                rejected——人类对 AI 建议
                的处置)
            outcome: 执行结果(good/bad
                ——可选)
            comment: 修正内容/评语

        Raises:
            KeyError: 决策不存在
            ValueError: 动作域外/结果
                域外/重复反馈
        """
        decision = await self.repo.get_decision(
            int(decision_id))
        if not decision:
            raise KeyError(
                f"决策记录 {decision_id} 不存在")
        action = str(action or "").strip()
        if action not in FEEDBACK_ACTIONS:
            raise ValueError(
                f"反馈动作 {action} 域外"
                f"(合法: {'/'.join(
                    FEEDBACK_ACTIONS)})")
        if outcome is not None:
            outcome = str(outcome).strip()
            if outcome not in \
                    FEEDBACK_OUTCOMES:
                raise ValueError(
                    f"反馈结果 {outcome} 域外"
                    f"(合法: {'/'.join(
                        FEEDBACK_OUTCOMES)}"
                    f" 或不传)")
        comment = str(comment or "")[:500]
        by = str(by or "admin").strip()

        # 决策 1:1(重复反馈拒绝)
        existing = await self.repo.list_feedback(
            decision_id=int(decision_id))
        if existing:
            existing_id = \
                existing[0].get("feedbackId")
            raise ValueError(
                f"决策 {decision_id} 已有反馈"
                f"(feedbackId={existing_id}"
                f"——1:1 勿重复)")

        feedback_id = await \
            self.repo.next_feedback_id()
        record = {
            "feedbackId": feedback_id,
            "decisionId": int(decision_id),
            "requestId": int(
                decision.get(
                    "requestId") or 0),
            "action": action,
            "outcome": outcome or "",
            "comment": comment,
            "by": by,
            "createdAt": ts(),
        }
        await self.repo.save_feedback(record)

        await self._track({
            "action": "feedback_submit",
            "decisionId": int(decision_id),
            "feedbackAction": action,
            "outcome": outcome or "",
            "by": by,
        })
        return {
            "success": True,
            "feedbackId": feedback_id,
            "decisionId": int(decision_id),
            "action": action,
            "outcome": outcome or "",
            "comment": comment,
            "by": by,
            "note": "RLHF 反馈已记录——"
                    "P4 collect 池双写第36档案",
            "createdAt": record["createdAt"],
        }

    # ============================================================
    # 观测面
    # ============================================================

    async def feedback_view(self,
                            decision_id: int = None
                            ) -> dict:
        """反馈视图(观测面——分布统计)"""
        records = await self.repo.list_feedback(
            decision_id=decision_id)
        by_action: dict = {}
        by_outcome: dict = {}
        for r in records:
            by_action[r.get("action")] = \
                by_action.get(
                    r.get("action"), 0) + 1
            if r.get("outcome"):
                by_outcome[
                    r.get("outcome")] = \
                by_outcome.get(
                    r.get("outcome"), 0) + 1
        return {
            "success": True,
            "total": len(records),
            "byAction": by_action,
            "byOutcome": by_outcome,
            "feedback": records,
            "note": "RLHF 反馈记录——"
                    "决策 1:1(三态+结果)",
        }

    # --------------------------------------------------------
    # 内部
    # --------------------------------------------------------

    async def _track(self, detail: dict) -> None:
        try:
            event_id = await \
                self.repo.next_event_id()
            await self.repo.add_event({
                "eventId": event_id,
                "requestId": 0,
                "eventType": "feedback",
                "detail": detail,
                "createdAt": ts(),
            })
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "dm61_track_failed: %s", exc)
