"""55号·二维码AI智能管理 治理联动
(qr55_governance_service, P4)

计划(docs/55号_二维码AI智能管理模块实施计划.md §六 P4):
    - 46号三检测器接入(停滞/枯竭/漂移高)——
      第30档案 qr_orchestration 专项健康视图
    - 冻结守卫联动(46号 is_frozen——学习/晋升
      门卫, P3 已在 44号引擎内建 fail-soft 消费)

设计(46号零改动——纯调用式只读消费):
    - governance_health: 单档案视角提取 46号
      live_health 中 qr_orchestration 条目 +
      55号域内治理事实(冻结态/模型事件/回流统计)
      ——三检测器结果+冻结态+55号专属动作建议
    - freeze_guard: 冻结守卫观测(is_frozen +
      冻结原因追溯 46号 changes 队列)

QC(计划 §六 P4): 冻结期间学习跳过(P3 已由
44号引擎内建——此处提供观测面断言口径)。
"""

import logging

from core.helpers import ts

from repositories.qr55_repository import (
    Qr55Repository,
)

logger = logging.getLogger("qr55_governance_service")

MODEL_VERSION = "v1-qr55-governance"

SCORER_ID = "qr_orchestration"

# 三检测器信号(46号口径)
SIGNAL_LABELS = {
    "stagnation": "学习停滞",
    "depletion": "反馈枯竭",
    "drift_high": "漂移高",
}

# 信号 → 55号域动作建议
SIGNAL_ACTIONS = {
    "stagnation": "生码决策模型长期未演进——"
                  "建议触发一轮学习并复核反馈管道",
    "depletion": "回流反馈不足——建议检查埋点链路"
                 "(生成→扫码→完成→回流)",
    "drift_high": "因子分布显著偏离基线——建议"
                  "复核近期生码场景分布(服务模板/"
                  "受众/信值等级结构变化)",
    "frozen": "档案治理冻结中——学习/晋升已守卫"
              "跳过(人工复核 46号审批队列解锁)",
}


class Qr55GovernanceService:
    """55号治理联动(46号三检测器+冻结守卫——只读)"""

    def __init__(self):
        self.repo = Qr55Repository()

    # ============================================================
    # 治理健康视图(46号 live_health 单档案提取)
    # ============================================================

    async def governance_health(self) -> dict:
        """第30档案治理健康(46号三检测器结果+
        55号域治理事实+动作建议)"""
        entry, gov_error = await self._gov_entry()

        frozen = await self._is_frozen()

        # 55号域治理事实
        model_events = await self.repo.list_model_events(
            limit=100)
        feedback = await self.repo.list_feedback(
            limit=1000)
        labeled = [f for f in feedback
                   if f.get("status") == "labeled"]

        # 三检测器信号(46号口径; 失败 → 未知)
        signals = (entry or {}).get("signals") or []
        health_score = (entry or {}).get("healthScore")
        health_level = (entry or {}).get("healthLevel")

        actions = [SIGNAL_ACTIONS[s]
                   for s in signals
                   if s in SIGNAL_ACTIONS]
        if frozen:
            actions.append(SIGNAL_ACTIONS["frozen"])

        return {
            "success": True,
            "scorerId": SCORER_ID,
            "module": "qr55",
            "governance": {
                "healthScore": health_score,
                "healthLevel": health_level,
                "signals": signals,
                "signalNames": {
                    s: SIGNAL_LABELS.get(s, s)
                    for s in signals},
                "details": (entry or {}).get("details"),
                "govStatus": (entry or {}).get("govStatus"),
                "error": gov_error or "",
            },
            "freezeGuard": {
                "frozen": frozen,
                "note": "冻结期间学习/晋升守卫跳过"
                        "(44号引擎内建 fail-soft)"
                if frozen else "学习/晋升通道开放",
            },
            "domain": {
                "modelEvents": len(model_events),
                "labeledFeedback": len(labeled),
                "pendingFeedback": len(feedback)
                - len(labeled),
                "eventTypes": sorted({
                    e.get("eventType")
                    for e in model_events}),
            },
            "actions": actions,
            "note": "46号三检测器只读消费——零改动红线",
            "checkedAt": ts(),
        }

    # ============================================================
    # 冻结守卫观测(学习跳过断言口径)
    # ============================================================

    async def freeze_guard(self) -> dict:
        """冻结守卫观测(46号 is_frozen + 冻结原因
        追溯 changes 队列最近 freeze 记录)"""
        frozen = await self._is_frozen()
        reason = ""
        change_id = 0
        if frozen:
            reason, change_id = await \
                self._freeze_reason()
        return {
            "success": True,
            "scorerId": SCORER_ID,
            "frozen": frozen,
            "freezeChangeId": change_id,
            "reason": reason,
            "effect": "学习轮次/晋升触发将被守卫跳过"
                      "(ValueError——P3 QC 口径)"
            if frozen else "学习/晋升通道开放",
            "checkedAt": ts(),
        }

    # ============================================================
    # 46号只读消费(fail-soft)
    # ============================================================

    async def _gov_entry(self) -> tuple:
        """46号 live_health 中第30档案条目"""
        try:
            from services.ai_governance_health import (
                AiGovernanceHealthService,
            )
            health = await (
                AiGovernanceHealthService().live_health())
            for e in health.get("entries") or []:
                if e.get("scorerId") == SCORER_ID:
                    return e, ""
            return None, "档案未在 46号健康视图中呈现"
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "qr55_gov_entry_failed: %s", exc)
            return None, str(exc)[:80]

    @staticmethod
    async def _is_frozen() -> bool:
        """46号 is_frozen(第30档案)"""
        try:
            from services.ai_governance_service import (
                AiGovernanceService,
            )
            return await AiGovernanceService(
            ).is_frozen(SCORER_ID)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "qr55_gov_frozen_check_failed: %s", exc)
            return False

    @staticmethod
    async def _freeze_reason() -> tuple:
        """冻结原因追溯(46号 changes 最近 freeze)"""
        try:
            from repositories.ai_governance_repository \
                import AiGovernance46Repository
            changes = await (
                AiGovernance46Repository().list_changes(
                    status="approved", limit=100))
            for c in changes:
                if c.get("scorerId") == SCORER_ID \
                        and c.get("kind") == "freeze":
                    return ((c.get("reason")
                             or c.get("note") or ""),
                            int(c.get("changeId") or 0))
            return "", 0
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "qr55_freeze_reason_failed: %s", exc)
            return "", 0
