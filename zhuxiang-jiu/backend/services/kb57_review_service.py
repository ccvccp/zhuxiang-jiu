"""57号·AI智能知识库 人类终审+召回
(kb57_review_service, P2)

计划(docs/57号_AI智能知识库模块实施计划.md §十一 P2):
    - 发布终审(review——published 唯一出口):
      sandbox 态种子→人工审批→published 入推荐池;
      驳回→rejected 回工坊重制
    - 召回(recall): 误导/过期种子紧急下架
      recalled+45号受影响用户补偿(P4 联动接口
      预留——compensate 55/56号范式)

设计(56号审批面板范式——终审人工铁律):
    - review 不受 KB57_MODE 影响
      (发布链人工动作——off 亦可用)
    - 审批留痕(kb57_events: publish/reject)
    - 版本化联动: 发布时旧版自动降权不删除
      (seed 工坊 _demote_prior_versions)
"""

import logging
import os

from core.helpers import ts

from repositories.kb57_repository import (
    Kb57Repository,
)

logger = logging.getLogger("kb57_review_service")

MODEL_VERSION = "v1-kb57-review"


class Kb57ReviewService:
    """57号人类终审+召回(P2)"""

    def __init__(self):
        self.repo = Kb57Repository()

    # ============================================================
    # 发布终审(published 唯一出口)
    # ============================================================

    async def review(self, seed_id: int,
                     reviewer: str,
                     approved: bool,
                     note: str = ""
                     ) -> dict:
        """种子发布终审(sandbox/review 态→
        published 入推荐池 或 rejected 回工坊)

        终审人工铁律: 不受 KB57_MODE 影响。

        Args:
            seed_id: 种子
            reviewer: 审批人
            approved: 结论(True 发布/False 驳回)
            note: 审批意见

        Raises:
            KeyError: 种子不存在
            ValueError: 状态机非法/审批人为空
        """
        if not str(reviewer or "").strip():
            raise ValueError("审批人(reviewer)必填")

        seed = await self.repo.get_seed(int(seed_id))
        if seed is None:
            raise KeyError(
                f"种子 {seed_id} 不存在")
        if seed.get("status") not in ("sandbox",
                                      "review"):
            raise ValueError(
                f"种子状态 {seed.get('status')}"
                f"(需 sandbox/review 方可终审)")

        result = {
            "success": True,
            "seedId": int(seed_id),
            "reviewer": reviewer,
            "reviewedAt": ts(),
        }

        if not approved:
            # 驳回 → rejected 回工坊重制
            seed["status"] = "rejected"
            seed["humanVerified"] = False
            seed["updatedAt"] = ts()
            await self.repo.save_seed(
                seed, create=False)
            await self._track(
                int(seed.get("gapId") or 0),
                "seed_reject", {
                    "seedId": int(seed_id),
                    "reviewer": reviewer,
                    "note": note,
                })
            result.update({
                "verdict": "rejected",
                "status": "rejected",
                "note": "驳回——种子回工坊重制"
                        "(craft 重新锻造)",
            })
            return result

        # 发布 → published 入推荐池
        seed["status"] = "published"
        seed["humanVerified"] = True
        seed["updatedAt"] = ts()
        await self.repo.save_seed(
            seed, create=False)

        # 版本化联动: 旧版自动降权不删除
        from services.kb57_seed_service import (
            Kb57SeedService,
        )
        demoted = await (
            Kb57SeedService()
            ._demote_prior_versions(
                int(seed.get("gapId") or 0),
                seed.get("type"),
                int(seed_id)))

        await self._track(
            int(seed.get("gapId") or 0),
            "seed_publish", {
                "seedId": int(seed_id),
                "reviewer": reviewer,
                "seedVersion":
                    seed.get("seedVersion"),
                "demotedPrior": demoted,
                "note": note,
            })
        result.update({
            "verdict": "approved",
            "status": "published",
            "demotedPrior": demoted,
            "note": "发布——种子入推荐池"
                    "(P3 角色植入接管)",
        })
        return result

    # ============================================================
    # 紧急召回(误导/过期种子下架)
    # ============================================================

    async def recall(self, seed_id: int,
                     reason: str = "",
                     affected_members: list = None
                     ) -> dict:
        """种子紧急召回(published/boosted→recalled)

        误导下架+受影响用户补偿(P4 联动——
        55/56号 compensate 范式, 本期留接口)。

        Raises:
            KeyError: 种子不存在
            ValueError: 状态机非法(非发布态)
        """
        seed = await self.repo.get_seed(int(seed_id))
        if seed is None:
            raise KeyError(
                f"种子 {seed_id} 不存在")
        if seed.get("status") not in ("published",
                                       "boosted"):
            raise ValueError(
                f"种子状态 {seed.get('status')}"
                f"(需 published/boosted 方可召回)")

        seed["status"] = "recalled"
        seed["recallReason"] = reason \
            or "内容误导风险——紧急下架"
        seed["updatedAt"] = ts()
        await self.repo.save_seed(
            seed, create=False)

        # 受影响用户补偿(P4 联动接口预留——
        # 45号 deposit 范式)
        compensation = {
            "attempted": len(affected_members or []),
            "compensated": 0,
            "note": "受影响用户补偿——P4 信值联动"
                    "接入(45号 L2 deposit)",
        }

        await self._track(
            int(seed.get("gapId") or 0),
            "seed_recall", {
                "seedId": int(seed_id),
                "reason": seed["recallReason"],
                "viewCount":
                    seed.get("viewCount"),
                "compensation":
                    compensation["attempted"],
            })

        return {
            "success": True,
            "seedId": int(seed_id),
            "status": "recalled",
            "reason": seed["recallReason"],
            "compensation": compensation,
            "note": "紧急召回完成——种子退出推荐池"
                    "(回流负修正 P4 接管)",
            "recalledAt": ts(),
        }

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
                "kb57_review_track_failed: %s", exc)
