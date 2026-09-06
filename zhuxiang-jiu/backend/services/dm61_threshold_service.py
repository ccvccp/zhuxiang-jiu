"""61号·AI智能系统升级决策 阈值配置域
(dm61_threshold_service, P2)

计划(docs/61号_AI智能系统升级决策模块实施计划.md
§3.2/§七 P2):
    决策阈值经 46号审批+人工终审
    (58/59/60号阈值域范式复用):
        - calibrate_submit: 提交 46号
          审批(不直接生效)
        - calibrate_apply: 46号 approved
          后人工确认落库(终审模)
        - get_active: 生效阈值读取
          (assess 判定联动——fail-soft
          回落注册表常量)

铁律(计划 §1.3):
    - 阈值变更必须经 46号审批+人工
      终审(AI 不可自改判定边界)
    - 未经裁决不可生效
"""

import logging
import os

from core.helpers import ts

from repositories.dm61_repository import (
    Dm61Repository,
)

logger = logging.getLogger("dm61_threshold")

MODEL_VERSION = "v1-dm61-threshold"

SCORER_ID = "decision_orchestration"

# 注册表常量基线(默认值——dm61_registry
# /dm61_assess_service 同源)
DEFAULT_L1_MAX_RISK = 30.0
DEFAULT_L3_MIN_RISK = 65.0

# 合法域: 0 < L1 < L3 ≤ 100
THRESHOLD_MIN = 0.0
THRESHOLD_MAX = 100.0


def current_mode() -> str:
    """模块开关(DM61_MODE——同底座口径)"""
    return os.environ.get(
        "DM61_MODE", "off")


def require_active_mode() -> None:
    """决策面门槛(off 拒绝)"""
    mode = current_mode()
    if mode == "off":
        raise ValueError(
            f"DM61_MODE={mode}(默认 off——"
            f"决策面关闭, 观测面不受影响)")


class Dm61ThresholdService:
    """61号阈值配置域(P2——46号审批
    双模+人工终审轨)"""

    def __init__(self):
        self.repo = Dm61Repository()

    # ============================================================
    # 阈值校准(管理+终审双模)
    # ============================================================

    async def calibrate_submit(
            self, l1_max_risk: float,
            l3_min_risk: float,
            requested_by: str = "admin",
            reason: str = "") -> dict:
        """阈值校准提交(管理模——46号审批
        不直接生效)

        Raises:
            ValueError: 非法阈值/已有
                待生效申请
        """
        require_active_mode()
        l1 = float(l1_max_risk or 0)
        l3 = float(l3_min_risk or 0)
        if not THRESHOLD_MIN < l1 < l3 \
                <= THRESHOLD_MAX:
            raise ValueError(
                f"非法阈值(须满足 "
                f"0 < L1({l1}) < L3({l3}) "
                f"≤ 100)")
        reason = str(reason or "").strip()
        if not reason:
            raise ValueError(
                "校准理由必填(可审计性)")

        # 同域已有待生效申请拒绝
        # (46号 pending 唯一性语义)
        existing = await self.repo.get_threshold(
            "default")
        if existing \
                and existing.get(
                    "status") == "pending":
            raise ValueError(
                f"已有待生效阈值申请"
                f"(changeId="
                f"{existing.get('changeId')}"
                f"——先处置再提交)")

        # 46号总线提交
        from services.ai_governance_service import (
            AiGovernanceService,
        )
        result = await (
            AiGovernanceService()
            .submit_change(
                scorer_id=SCORER_ID,
                kind="config",
                payload={
                    "l1MaxRisk": l1,
                    "l3MinRisk": l3,
                },
                reason=reason[:500],
                requested_by=requested_by))
        change_id = int(
            result.get("changeId") or 0)

        await self.repo.save_threshold({
            "tier": "default",
            "config": {
                "l1MaxRisk": l1,
                "l3MinRisk": l3,
            },
            "status": "pending",
            "changeId": change_id,
            "requestedBy": str(
                requested_by or "admin"),
            "reason": reason[:500],
            "appliedBy": "",
            "createdAt": ts(),
            "updatedAt": ts(),
        })
        await self._track({
            "action": "threshold_submit",
            "changeId": change_id,
            "l1MaxRisk": l1,
            "l3MinRisk": l3,
            "requestedBy": requested_by,
        })
        return {
            "success": True,
            "status": "pending",
            "changeId": change_id,
            "config": {
                "l1MaxRisk": l1,
                "l3MinRisk": l3,
            },
            "note": "阈值校准已提交 46号审批"
                    "(不直接生效——人工终审轨)",
            "submittedAt": ts(),
        }

    async def calibrate_apply(
            self, change_id: int,
            applied_by: str = "admin"
            ) -> dict:
        """阈值校准生效(终审模——46号
        reviewedBy 留痕+pending 匹配)

        Raises:
            KeyError: 变更不存在
            ValueError: 未裁决/不匹配/
                已生效
        """
        # 终审不受开关影响(人工铁律)
        from repositories.ai_governance_repository import (
            AiGovernance46Repository,
        )
        change = await (
            AiGovernance46Repository()
            .get_change(int(change_id)))
        if change is None:
            raise KeyError(
                f"46号变更 {change_id} 不存在")
        if not change.get("reviewedBy"):
            raise ValueError(
                f"46号变更 {change_id} 未经"
                f"人工裁决(先完成审批)")
        rec = await self.repo.get_threshold(
            "default")
        if not rec \
                or rec.get("changeId") \
                != int(change_id):
            raise ValueError(
                f"无 changeId={change_id} 的"
                f"待生效阈值申请")
        if rec.get("status") != "pending":
            raise ValueError(
                f"阈值申请已 "
                f"{rec.get('status')}"
                f"(勿重复生效)")

        rec.update({
            "status": "applied",
            "appliedBy": applied_by,
            "updatedAt": ts()})
        await self.repo.save_threshold(rec)
        await self._track({
            "action": "threshold_apply",
            "changeId": int(change_id),
            "appliedBy": applied_by,
        })
        return {
            "success": True,
            "status": "applied",
            "changeId": int(change_id),
            "config": rec.get("config"),
            "appliedBy": applied_by,
            "note": "阈值已生效(46号审批+"
                    "人工终审双模完成)",
            "appliedAt": ts(),
        }

    # ============================================================
    # 生效阈值读取(assess 判定联动)
    # ============================================================

    async def get_active(self) -> dict:
        """生效阈值读取(fail-soft 回落
        注册表常量)

        Returns:
            {l1MaxRisk, l3MinRisk,
             source: "applied"|"default"}
        """
        try:
            rec = await self.repo.get_threshold(
                "default")
            if rec \
                    and rec.get("status") \
                    == "applied":
                config = rec.get(
                    "config") or {}
                l1 = float(
                    config.get(
                        "l1MaxRisk")
                    or DEFAULT_L1_MAX_RISK)
                l3 = float(
                    config.get(
                        "l3MinRisk")
                    or DEFAULT_L3_MIN_RISK)
                if 0 < l1 < l3 <= 100:
                    return {
                        "l1MaxRisk": l1,
                        "l3MinRisk": l3,
                        "source": "applied",
                    }
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "dm61_threshold_failsoft: %s",
                exc)
        return {
            "l1MaxRisk": DEFAULT_L1_MAX_RISK,
            "l3MinRisk": DEFAULT_L3_MIN_RISK,
            "source": "default",
        }

    # ============================================================
    # 观测面
    # ============================================================

    async def thresholds_view(self) -> dict:
        """阈值视图(当前生效值+46号审批
        留痕——观测面不受开关影响)"""
        active = await self.get_active()
        rec = await self.repo.get_threshold(
            "default")
        return {
            "success": True,
            "active": active,
            "registry": rec or None,
            "defaults": {
                "l1MaxRisk":
                    DEFAULT_L1_MAX_RISK,
                "l3MinRisk":
                    DEFAULT_L3_MIN_RISK,
            },
            "note": "决策阈值配置域——46号审批"
                    "+人工终审双模(未经裁决"
                    "不可生效)",
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
                "eventType": "threshold",
                "detail": detail,
                "createdAt": ts(),
            })
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "dm61_track_failed: %s", exc)
