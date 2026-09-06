"""61号·AI智能系统升级决策 反对意见机制
(dm61_dissent_service, P3)

计划(docs/61号_AI智能系统升级决策模块实施计划.md
§3.2/§七 P3):
    ① 确定性冲突检测(四触发规则——
       AI 检测人类决策与已知风险冲突)
    ② 反对意见发起(raise——AI 说"不";
       决策记录挂 dissentFlag+需二次确认)
    ③ 人类处置(override 驳回必留痕/
       confirm 采纳 AI 意见——决策终止)

铁律(计划 §1.3/§六):
    - AI 可说"不"——dissent 不受开关
      影响(安全机制)
    - 人类可驳回但必留痕(override
      reason 必填)
    - 触发规则确定性——LLM 不进判定链

四触发规则(封闭):
    sim_blocked_ignored   影子沙箱阻断被忽略
    window_unsuitable     窗口不适宜仍推进
    rollback_failed       回滚预案未通过仍推进
    high_risk_fast_track  高危风险走快速通道
"""

import logging

from core.helpers import ts

from repositories.dm61_repository import (
    Dm61Repository,
)

logger = logging.getLogger("dm61_dissent")

MODEL_VERSION = "v1-dm61-dissent"

# 高危快速通道触发线(风险分≥75——
# 无论级别均应人工慎断)
HIGH_RISK_DISSENT_SCORE = 75.0

# 触发规则域(封闭)
DISSENT_TRIGGERS = {
    "sim_blocked_ignored":
        "影子沙箱阻断——变更含未处置红线",
    "window_unsuitable":
        "升级窗口不适宜——高峰叠加"
        "故障/信值波动",
    "rollback_failed":
        "回滚预案校验未通过——"
        "可恢复性存疑",
    "high_risk_fast_track":
        "风险分达高危线——不应快速"
        "通道推进",
}

# 处置动作域
RESOLVE_ACTIONS = ("override", "confirm")

# dissent 生命周期
DISSENT_STATUS = (
    "open",        # 未处置(阻断裁决)
    "overridden",  # 人类驳回(留痕放行)
    "confirmed",   # 人类采纳(AI 意见生效)
)


class Dm61DissentService:
    """61号反对意见机制(P3——AI 可说"不")"""

    def __init__(self):
        self.repo = Dm61Repository()

    # ============================================================
    # 冲突检测(确定性——只读)
    # ============================================================

    async def evaluate(self, decision_id: int
                       ) -> dict:
        """确定性冲突检测(只读——不落库)

        Raises:
            KeyError: 决策不存在
        """
        decision, request, sim = \
            await self._load(decision_id)
        triggers = self._evaluate_triggers(
            decision, request, sim)
        return {
            "success": True,
            "decisionId": int(decision_id),
            "triggers": triggers,
            "triggerCount": len(triggers),
            "descriptions": {
                t: DISSENT_TRIGGERS[t]
                for t in triggers},
            "note": "确定性冲突检测——"
                    "四触发规则封闭域"
                    "(LLM 不进判定链)",
        }

    # ============================================================
    # 发起反对意见(raise——AI 说"不")
    # ============================================================

    async def raise_dissent(
            self, decision_id: int,
            raised_by: str = "ai",
            reason: str = "") -> dict:
        """发起反对意见(决策记录挂
        dissentFlag+需二次确认)

        不受开关影响(AI 安全机制)。

        Raises:
            KeyError: 决策不存在
            ValueError: 状态非法/已有
                未处置反对意见/无触发依据
        """
        decision, request, sim = \
            await self._load(decision_id)
        status = str(decision.get("status"))
        if status not in ("recommended",
                          "decided",
                          "executed_track"):
            raise ValueError(
                f"决策 {decision_id} 状态 "
                f"{status} 不可发起反对意见"
                f"(需 recommended/decided/"
                f"executed_track)")

        existing = decision.get(
            "dissent") or {}
        if existing.get("status") == "open":
            raise ValueError(
                f"决策 {decision_id} 已有"
                f"未处置反对意见"
                f"(先 override/confirm)")
        if existing.get("status") in \
                ("overridden", "confirmed"):
            raise ValueError(
                f"决策 {decision_id} 反对意见"
                f"已处置({existing.get('status')}"
                f"——勿重复发起)")

        triggers = self._evaluate_triggers(
            decision, request, sim)
        manual_note = str(
            reason or "").strip()
        if not triggers and not manual_note:
            raise ValueError(
                "无冲突触发依据且无人工理由"
                "(发起反对意见需触发规则命中"
                "或显式理由)")

        dissent = {
            "status": "open",
            "raisedBy": str(
                raised_by or "ai"),
            "raisedAt": ts(),
            "triggers": triggers,
            "descriptions": {
                t: DISSENT_TRIGGERS[t]
                for t in triggers},
            "manualReason": manual_note,
            "resolvedAt": "",
            "resolvedBy": "",
            "resolutionReason": "",
        }
        decision["dissent"] = dissent
        decision["dissentFlag"] = True
        decision["updatedAt"] = ts()
        await self.repo.save_decision(
            decision, create=False)

        await self._track(
            int(decision_id), "dissent", {
                "requestId": int(
                    decision.get(
                        "requestId") or 0),
                "action": "raise",
                "triggers": triggers,
                "raisedBy": raised_by,
            })
        return {
            "success": True,
            "decisionId": int(decision_id),
            "dissentFlag": True,
            "dissent": dissent,
            "requiresSecondConfirm": True,
            "note": "反对意见已发起——"
                    "需二次确认(override 驳回"
                    "必留痕/confirm 采纳)",
            "raisedAt": dissent["raisedAt"],
        }

    # ============================================================
    # 人类处置(override 驳回必留痕/
    # confirm 采纳 AI 意见)
    # ============================================================

    async def resolve(self, decision_id: int,
                      action: str,
                      reason: str,
                      resolved_by: str = "admin"
                      ) -> dict:
        """处置反对意见(终审——不受
        开关影响)

        Args:
            action: override(人类驳回 AI
                意见——reason 必填留痕)/
                confirm(采纳 AI 意见——
                recommended 态决策终止)
            reason: 处置理由(必填——
                驳回必留痕铁律)

        Raises:
            KeyError: 决策不存在
            ValueError: 动作域外/无未处置
                反对意见/缺理由
        """
        decision, _request, _sim = \
            await self._load(decision_id)
        action = str(action or "").strip()
        if action not in RESOLVE_ACTIONS:
            raise ValueError(
                f"处置动作 {action} 域外"
                f"(合法: {'/'.join(
                    RESOLVE_ACTIONS)})")
        reason = str(reason or "").strip()
        if not reason:
            raise ValueError(
                "处置理由必填(人类可驳回"
                "但必留痕铁律)")

        dissent = decision.get(
            "dissent") or {}
        if dissent.get("status") != "open":
            raise ValueError(
                f"决策 {decision_id} 无未处置"
                f"反对意见(当前 "
                f"{dissent.get('status') or '无'})")

        dissent.update({
            "status":
                "overridden"
                if action == "override"
                else "confirmed",
            "resolvedAt": ts(),
            "resolvedBy": str(
                resolved_by or "admin"),
            "resolutionReason":
                reason[:500],
        })
        decision["dissent"] = dissent
        decision["updatedAt"] = ts()

        # confirm(采纳 AI 意见):
        # recommended 态决策终止——
        # 人类放弃推进
        request_closed = False
        if action == "confirm" \
                and str(decision.get(
                    "status")) == "recommended":
            decision["status"] = "decided"
            decision["outcome"] = \
                "dissent_confirmed"
            request_closed = True

        await self.repo.save_decision(
            decision, create=False)

        # 请求状态联动(confirm 终止)
        if request_closed:
            request = await \
                self.repo.get_request(int(
                    decision.get(
                        "requestId") or 0))
            if request:
                request["status"] = "closed"
                request["outcome"] = \
                    "dissent_confirmed"
                request["updatedAt"] = ts()
                await self.repo.save_request(
                    request, create=False)

        await self._track(
            int(decision_id), "dissent", {
                "requestId": int(
                    decision.get(
                        "requestId") or 0),
                "action": action,
                "resolvedBy": resolved_by,
                "reason": reason[:200],
            })
        return {
            "success": True,
            "decisionId": int(decision_id),
            "action": action,
            "dissent": dissent,
            "decisionStatus":
                decision.get("status"),
            "outcome":
                decision.get("outcome"),
            "note": "反对意见已处置——"
                    + ("人类驳回(AI 意见不采"
                       "纳, 理由留痕)"
                       if action == "override"
                       else "人类采纳 AI 意见"
                            "(决策终止/复核)"),
            "resolvedAt":
                dissent["resolvedAt"],
        }

    # ============================================================
    # 内部
    # ============================================================

    async def _load(self, decision_id: int
                    ) -> tuple:
        """载入决策+请求+最新推演"""
        decision = await self.repo.get_decision(
            int(decision_id))
        if not decision:
            raise KeyError(
                f"决策记录 {decision_id} 不存在")
        request = await self.repo.get_request(
            int(decision.get("requestId")
                or 0))
        sims = await self.repo.list_simulations(
            request_id=int(
                decision.get("requestId")
                or 0))
        sim = sims[0] if sims else None
        return decision, request, sim

    @staticmethod
    def _evaluate_triggers(decision: dict,
                           request: dict,
                           sim: dict) -> list:
        """确定性触发评估(四规则封闭)"""
        triggers = []
        # ① 沙箱阻断被忽略
        if sim and str(sim.get(
                "verdict")) == "blocked":
            triggers.append(
                "sim_blocked_ignored")
        # ② 窗口不适宜
        env = (request
               or {}).get(
            "environment") or {}
        if str(env.get("level")) == \
                "unsuitable":
            triggers.append(
                "window_unsuitable")
        # ③ 回滚预案未通过
        rb = (sim or {}).get(
            "rollback") or {}
        if rb.get("required") \
                is True \
                and rb.get("passed") \
                is not True:
            triggers.append(
                "rollback_failed")
        # ④ 高危快速通道
        if float(decision.get(
                "riskScore") or 0) \
                >= HIGH_RISK_DISSENT_SCORE:
            triggers.append(
                "high_risk_fast_track")
        return triggers

    async def _track(self, ref_id: int,
                     event_type: str,
                     detail: dict) -> None:
        try:
            event_id = await \
                self.repo.next_event_id()
            await self.repo.add_event({
                "eventId": event_id,
                "requestId": int(ref_id or 0),
                "eventType": event_type,
                "detail": detail,
                "createdAt": ts(),
            })
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "dm61_track_failed %s: %s",
                event_type, exc)
