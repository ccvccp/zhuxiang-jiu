"""56号·AI智能升级管理 交付+语义回滚+信值补偿
(aiup56_deliver_service, P4)

计划(docs/56号_AI智能升级管理模块实施计划.md §九 P4):
    - 资产包交付(versioned 出口): approved 提案
      的完整资产包(代码草稿+测试计划+回滚预案+
      审计报告)出口——人工下载后走既有 CI 部署
      (AI 永不直接落盘生产代码铁律)
    - 灰度跟踪窗口: delivered→观察期(事件留痕,
      价值达成率计算入口)
    - 语义回滚: 回滚预案分步执行留痕 +
      状态回退 + 45号 L2 受影响用户信值补偿
    - 决策回流: delivered/rolled_back 终态
      → aiup56_feedback_service(本文件分离)
"""

import logging

from core.helpers import ts

from repositories.aiup56_repository import (
    Aiup56Repository,
)

logger = logging.getLogger("aiup56_deliver_service")

MODEL_VERSION = "v1-aiup56-deliver"

SCORER_ID = "upgrade_orchestration"


class Aiup56DeliverService:
    """56号交付+语义回滚+信值补偿"""

    def __init__(self):
        self.repo = Aiup56Repository()

    # ============================================================
    # 资产包交付(versioned 出口)
    # ============================================================

    async def deliver(self, proposal_id: int) -> dict:
        """交付资产包(approved→delivered——完整资产
        包出口, 人工下载后走既有 CI 部署)

        Raises:
            KeyError: 提案不存在
            ValueError: 状态机非法(非 approved——
                无审批不可交付架构断言)
        """
        proposal = await self.repo.get_proposal(
            int(proposal_id))
        if proposal is None:
            raise KeyError(f"提案 {proposal_id} 不存在")
        if proposal.get("status") != "approved":
            raise ValueError(
                f"提案状态 {proposal.get('status')}"
                f"(需 approved 方可交付——无审批"
                f"不可交付铁律)")

        # 资产包组装(versioned)
        assets = await self.repo.list_assets(
            proposal_id=int(proposal_id))
        asset = assets[0] if assets else None
        sandboxes = await self.repo.list_sandboxes(
            proposal_id=int(proposal_id))
        sandbox = sandboxes[0] if sandboxes else None

        package = {
            "proposalId": int(proposal_id),
            "assetId": (asset or {}).get("assetId"),
            "assetVersion":
                (asset or {}).get("assetVersion"),
            "drafts": (asset or {}).get("drafts") or [],
            "testPlans":
                (asset or {}).get("testPlans") or [],
            "VALUE_REASONs":
                (asset or {}).get("VALUE_REASONs") or [],
            "rollbackPlans": [
                t.get("rollbackPlan")
                for t in proposal.get("tasks") or []],
            "auditReport":
                proposal.get("auditReport"),
            "sandboxVerdict":
                (sandbox or {}).get("verdict"),
            "approval": {
                "reviewedBy":
                    proposal.get("reviewedBy"),
                "secondReviewedBy":
                    proposal.get("secondReviewedBy"),
                "reviewId":
                    proposal.get("reviewId"),
                "confirmations": "见 reviews 表",
            },
            "budget": {
                "spent": proposal.get("budgetSpent"),
                "cap": proposal.get("budgetCap"),
            },
            "deploymentNote": (
                "AI 永不落盘生产代码——本资产包"
                "供人工下载后走既有 CI 部署"
                "(pre-commit 钩子链)"),
        }

        # 状态翻转+灰度跟踪窗口开启
        proposal["status"] = "delivered"
        proposal["deliveredAt"] = ts()
        proposal["deliveryPackage"] = package
        proposal["updatedAt"] = ts()
        await self.repo.save_proposal(
            proposal, create=False)

        await self._track(proposal_id, "deliver", {
            "assetId": package.get("assetId"),
            "assetVersion":
                package.get("assetVersion"),
            "drafts": len(package["drafts"]),
            "sandboxVerdict":
                package.get("sandboxVerdict"),
        })

        return {
            "success": True,
            "proposalId": int(proposal_id),
            "status": "delivered",
            "package": package,
            "note": "资产包交付(versioned 出口)——"
                    "灰度跟踪窗口开启, 回流 P4 接管",
            "deliveredAt": ts(),
        }

    # ============================================================
    # 语义回滚(预案分步执行+留痕)
    # ============================================================

    async def rollback(self, proposal_id: int,
                       reason: str = "",
                       affected_members: list = None
                       ) -> dict:
        """语义回滚(delivered→rolled_back——
        回滚预案分步执行留痕+45号 L2 补偿)

        Args:
            reason: 回滚原因
            affected_members: 受影响用户(补偿对象
                ——灰度观察期内反馈受损的用户)

        Raises:
            KeyError: 提案不存在
            ValueError: 状态机非法(非 delivered)
        """
        proposal = await self.repo.get_proposal(
            int(proposal_id))
        if proposal is None:
            raise KeyError(f"提案 {proposal_id} 不存在")
        if proposal.get("status") != "delivered":
            raise ValueError(
                f"提案状态 {proposal.get('status')}"
                f"(需 delivered 方可回滚)")

        # ① 回滚预案分步执行留痕
        steps = []
        for i, task in enumerate(
                proposal.get("tasks") or []):
            plan = (task.get("rollbackPlan")
                    or {})
            step = {
                "step": i + 1,
                "task": task.get("title"),
                "strategy": plan.get("strategy")
                or "无预案(人工处置)",
                "steps": plan.get("steps") or [],
                "dataCleanup":
                    plan.get("dataCleanup") or "",
                "executed": True,
                "executedAt": ts(),
            }
            steps.append(step)

        # ② 45号 L2 受影响用户信值补偿
        compensation = {
            "attempted": len(affected_members or []),
            "compensated": 0,
            "skipped": 0,
            "results": [],
        }
        for member_id in (affected_members or []):
            comp = await self._compensate(
                member_id, proposal_id, reason)
            if comp:
                compensation["compensated"] += 1
            else:
                compensation["skipped"] += 1
            compensation["results"].append(
                {"memberId": member_id,
                 "compensated": comp})

        # ③ 状态翻转+留痕
        proposal["status"] = "rolled_back"
        proposal["rollbackReason"] = reason \
            or "灰度异常——自动/人工回滚"
        proposal["rollbackSteps"] = steps
        proposal["rollbackAt"] = ts()
        proposal["compensation"] = compensation
        proposal["updatedAt"] = ts()
        await self.repo.save_proposal(
            proposal, create=False)

        await self._track(proposal_id, "rollback", {
            "reason": reason,
            "steps": len(steps),
            "compensated":
                compensation["compensated"],
        })

        return {
            "success": True,
            "proposalId": int(proposal_id),
            "status": "rolled_back",
            "steps": steps,
            "compensation": compensation,
            "note": "语义回滚——预案分步执行+受影响"
                    "用户信值补偿(决策回流负修正)",
            "rolledBackAt": ts(),
        }

    # --------------------------------------------------------
    # 45号 L2 信值补偿(55号 compensate 范式)
    # --------------------------------------------------------

    @staticmethod
    async def _compensate(member_id: int,
                          proposal_id: int,
                          reason: str) -> bool:
        """受影响用户信值补偿(45号 L2 platform_conduct
        正向抚慰——deposit 验真; fail-soft)"""
        try:
            from services.trust_radar_service import (
                TrustRadarService,
            )
            evidence = (
                f"aiup56 升级回滚受害者补偿(提案 "
                f"{proposal_id}, 会员 {member_id}——"
                f"灰度异常受影响, 平台抚慰口径)")
            deposit = await TrustRadarService(
            ).submit_deposit(
                int(member_id), "L2",
                "platform_conduct",
                observed=1.0,
                peer_baseline=0.0,
                evidence=evidence,
                summary="升级回滚受影响用户补偿"
                        "(56号交付管道)",
                sources=["aiup56_pipeline",
                         "event_audit"],
                voluntary=False,
                verify_mode="v1")
            return bool(deposit.get("verified"))
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "aiup56_compensate_failed %s: %s",
                member_id, exc)
            return False

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
                "aiup56_deliver_track_failed: %s", exc)
