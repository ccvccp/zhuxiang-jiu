"""56号·AI智能升级管理 人类审批面板
(aiup56_review_service, P3)

计划(docs/56号_AI智能升级管理模块实施计划.md §三/§九 P3):
    人类审批面板(唯一交付出口——终审人工铁律):
    - 关键项强制确认清单(缺勾选 → 拒绝受理)
    - escalate 级提案双人复核(第二审批人)
    - 审批留痕(结论/勾选清单/审批人链)
    - 驳回 → 回 planned 重规划

QC(计划 §九 P3): 无审批不可交付(架构断言
——deliver 端点 P4 仅接受 approved 状态)。

设计(参照方案 §三-5 人类监督失效防护):
    - 强制确认清单(防审批形式化):
        ① 已阅读审计报告
        ② 已复核沙箱三关结论
        ③ 知悉回滚预案
        ④ 知悉预算消耗
    - escalate 级: 双人复核(reviewer + second)
      ——两人均批准方可 approved
"""

import logging

from core.helpers import ts

from repositories.aiup56_repository import (
    Aiup56Repository,
)

logger = logging.getLogger("aiup56_review_service")

MODEL_VERSION = "v1-aiup56-review"

# 强制确认清单(关键项——全勾选方可受理)
REQUIRED_CONFIRMATIONS = (
    "readAuditReport",      # 已阅读审计报告
    "reviewedSandbox",      # 已复核沙箱三关
    "acknowledgedRollback",  # 知悉回滚预案
    "acknowledgedBudget",   # 知悉预算消耗
)

CONFIRMATION_LABELS = {
    "readAuditReport": "已阅读审计报告",
    "reviewedSandbox": "已复核沙箱三关结论",
    "acknowledgedRollback": "知悉回滚预案",
    "acknowledgedBudget": "知悉预算消耗",
}


class Aiup56ReviewService:
    """56号人类审批面板(强制确认+双人复核)"""

    def __init__(self):
        self.repo = Aiup56Repository()

    # ============================================================
    # 审批面板视图(观测面)
    # ============================================================

    async def panel(self, proposal_id: int) -> dict:
        """审批面板(待审批提案的完整审阅材料——
        信号/沙箱/审计报告/确认清单)"""
        proposal = await self.repo.get_proposal(
            int(proposal_id))
        if proposal is None:
            raise KeyError(f"提案 {proposal_id} 不存在")
        sandboxes = await self.repo.list_sandboxes(
            proposal_id=int(proposal_id))
        reviews = await self._list_reviews(
            int(proposal_id))
        return {
            "success": True,
            "proposalId": int(proposal_id),
            "proposalStatus": proposal.get("status"),
            "escalated": bool(
                proposal.get("escalated")),
            "dualReview": bool(
                proposal.get("dualReview")),
            "summary": proposal.get("summary"),
            "auditReport":
                proposal.get("auditReport"),
            "auditLayers":
                proposal.get("auditLayers"),
            "sandbox": (sandboxes or [None])[0],
            "budget": {
                "spent": proposal.get("budgetSpent"),
                "cap": proposal.get("budgetCap"),
            },
            "requiredConfirmations": [
                {"key": k,
                 "label": CONFIRMATION_LABELS[k]}
                for k in REQUIRED_CONFIRMATIONS],
            "reviews": reviews,
            "note": "人类审批面板——终审人工铁律"
                    "(全链唯一交付出口)",
            "renderedAt": ts(),
        }

    # ============================================================
    # 审批提交
    # ============================================================

    async def review(self, proposal_id: int,
                     reviewer: str,
                     approved: bool,
                     confirmations: list,
                     note: str = "",
                     second_reviewer: str = ""
                     ) -> dict:
        """提交审批(强制确认校验→双人复核判定→
        状态 audited→approved/rejected(回 planned))

        Args:
            reviewer: 审批人(第一)
            approved: 结论(True 批准/False 驳回)
            confirmations: 勾选清单(REQUIRED 子集)
            note: 审批意见
            second_reviewer: 第二审批人(escalate
                级双人复核——批准时必填且异于第一)

        Raises:
            KeyError: 提案不存在
            ValueError: 状态机非法/确认清单不齐/
                双人复核缺失或同人
        """
        proposal = await self.repo.get_proposal(
            int(proposal_id))
        if proposal is None:
            raise KeyError(f"提案 {proposal_id} 不存在")
        if proposal.get("status") != "audited":
            raise ValueError(
                f"提案状态 {proposal.get('status')}"
                f"(需 audited 方可审批——先触发 audit)")

        # ① 强制确认校验(防审批形式化)
        if approved:
            missing = [k for k
                       in REQUIRED_CONFIRMATIONS
                       if k not in (confirmations or [])]
            if missing:
                raise ValueError(
                    "关键项确认清单不齐"
                    f"(缺: {[CONFIRMATION_LABELS[k]
                            for k in missing]})——"
                    "禁止形式化审批")

        # ② 审批留痕
        review_id = await self._next_review_id()
        review_record = {
            "reviewId": review_id,
            "proposalId": int(proposal_id),
            "reviewer": reviewer,
            "secondReviewer": second_reviewer,
            "approved": bool(approved),
            "confirmations": [
                k for k in (confirmations or [])
                if k in REQUIRED_CONFIRMATIONS],
            "note": note,
            "createdAt": ts(),
        }
        await self._save_review(review_record)

        # ③ 判定
        result = {
            "success": True,
            "proposalId": int(proposal_id),
            "reviewId": review_id,
            "reviewer": reviewer,
        }

        if not approved:
            # 驳回 → 回 planned 重规划
            proposal["status"] = "planned"
            proposal["reviewVerdict"] = "rejected"
            proposal["reviewedBy"] = reviewer
            proposal["updatedAt"] = ts()
            await self.repo.save_proposal(
                proposal, create=False)
            await self._track(proposal_id,
                               "reject", {
                "reviewer": reviewer,
                "reviewId": review_id,
                "note": note,
            })
            result.update({
                "verdict": "rejected",
                "status": "planned",
                "note": "驳回——提案已回退 planned "
                        "重新规划",
            })
            return result

        # ④ escalate 双人复核
        if proposal.get("dualReview"):
            if not second_reviewer:
                raise ValueError(
                    "escalate 级提案需双人复核——"
                    "第二审批人必填(second_reviewer)")
            if second_reviewer == reviewer:
                raise ValueError(
                    "双人复核不可同人"
                    f"(均为 {reviewer})")
            result["secondReviewer"] = \
                second_reviewer

        # ⑤ 批准 → approved
        proposal["status"] = "approved"
        proposal["reviewVerdict"] = "approved"
        proposal["reviewedBy"] = reviewer
        if proposal.get("dualReview"):
            proposal["secondReviewedBy"] = \
                second_reviewer
        proposal["reviewId"] = review_id
        proposal["updatedAt"] = ts()
        await self.repo.save_proposal(
            proposal, create=False)
        await self._track(proposal_id, "approve", {
            "reviewer": reviewer,
            "secondReviewer": second_reviewer,
            "reviewId": review_id,
            "dualReview":
                bool(proposal.get("dualReview")),
        })
        result.update({
            "verdict": "approved",
            "status": "approved",
            "note": "批准——P4 交付出口开放"
                    "(deliver 端点)",
        })
        return result

    # --------------------------------------------------------
    # 审批记录仓储(reviews 表)
    # --------------------------------------------------------

    async def _save_review(self, record: dict,
                            *, create: bool = True
                            ) -> dict:
        if self.repo and await self._is_redis():
            client = await self._redis_client()
            pipe = client.pipeline(
                transaction=False)
            import json as _json
            mapping = {}
            for k, v in record.items():
                if isinstance(v, (dict, list)):
                    mapping[k] = _json.dumps(
                        v, ensure_ascii=False)
                elif v is None:
                    mapping[k] = ""
                elif isinstance(v, bool):
                    mapping[k] = 1 if v else 0
                else:
                    mapping[k] = v
            key = (f"zhuxiang:aiup56:"
                   f"aiup56_reviews:"
                   f"{record['reviewId']}")
            pipe.hset(key, mapping=mapping)
            if create:
                pipe.lpush(
                    "zhuxiang:aiup56:reviews_all",
                    record["reviewId"])
            await pipe.execute()
            return record
        # 内存态
        store = self.repo.store
        store.setdefault("aiup56_reviews", {})[
            record["reviewId"]] = dict(record)
        store.setdefault(
            "_aiup56_reviews_all", []).insert(
            0, record["reviewId"])
        return record

    async def _list_reviews(self,
                            proposal_id: int
                            ) -> list:
        """审批记录列表(最新在前)"""
        if await self._is_redis():
            client = await self._redis_client()
            ids = await client.lrange(
                "zhuxiang:aiup56:reviews_all",
                0, -1)
            import json as _json
            records = []
            for rid in ids:
                data = await client.hgetall(
                    f"zhuxiang:aiup56:"
                    f"aiup56_reviews:{int(rid)}")
                if not data:
                    continue
                rec = {}
                for k, v in data.items():
                    if k == "confirmations":
                        try:
                            rec[k] = _json.loads(
                                v) if v else []
                        except ValueError:
                            rec[k] = []
                    elif k == "approved":
                        rec[k] = str(v) in (
                            "1", "True", "true")
                    else:
                        rec[k] = v
                records.append(rec)
            records = [
                r for r in records
                if int(r.get("proposalId") or 0)
                == int(proposal_id)]
            records.sort(key=lambda r: -int(
                r.get("reviewId") or 0))
            return records
        store = self.repo.store
        records = [
            dict(r) for r in
            (store.get("aiup56_reviews")
             or {}).values()]
        records = [
            r for r in records
            if int(r.get("proposalId") or 0)
            == int(proposal_id)]
        records.sort(key=lambda r: -int(
            r.get("reviewId") or 0))
        return records

    async def _next_review_id(self) -> int:
        return await self.repo._next_seq("reviews")

    @staticmethod
    async def _is_redis() -> bool:
        from repositories.backend import is_redis_mode
        return is_redis_mode()

    @staticmethod
    async def _redis_client():
        from repositories.backend import (
            get_redis_client,
        )
        return await get_redis_client()

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
                "aiup56_review_track_failed: %s", exc)
