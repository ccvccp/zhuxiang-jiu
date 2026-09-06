"""62号·AI智能无形资产估值 阈值配置域
(av62_threshold_service, P2)

计划(docs/62号_AI智能无形资产估值模型实施计划.md
§七 P2/§五):
    权重/折算/衰减配置域——46号审批
    +人工终审(58/59/60/61号阈值域
    范式复用):
        - calibrate_submit: 提交 46号
          审批(不直接生效)
        - calibrate_apply: 46号 approved
          后人工确认落库(终审模)
        - thresholds_view: 生效配置+
          46号审批留痕(观测面)

    可校准域(封闭二选一):
        - decay: {halfLifeDays}
          衰减半衰期(30-365 日)
        - scenario: {scenario,
          multiplier} 场景级附加乘子
          (0.5-1.5)

铁律(计划 §1.3):
    - 阈值变更必须经 46号审批+人工
      终审(AI 不可自改判定边界)
    - 未经裁决不可生效
"""

import logging
import os

from core.helpers import ts

from repositories.av62_repository import (
    Av62Repository,
)

logger = logging.getLogger("av62_threshold")

MODEL_VERSION = "v1-av62-threshold"

SCORER_ID = "asset_valuation"


def current_mode() -> str:
    """模块开关(AV62_MODE——同底座口径)"""
    return os.environ.get(
        "AV62_MODE", "off")


def require_active_mode() -> None:
    """决策面门槛(off 拒绝)"""
    mode = current_mode()
    if mode == "off":
        raise ValueError(
            f"AV62_MODE={mode}(默认 off——"
            f"决策面关闭, 观测面不受影响)")


class Av62ThresholdService:
    """62号阈值配置域(P2——46号审批
    双模+人工终审轨)"""

    def __init__(self):
        self.repo = Av62Repository()

    # ============================================================
    # 校准提交(管理模)
    # ============================================================

    async def calibrate_submit(
            self, half_life_days: int = None,
            scenario: str = "",
            multiplier: float = None,
            requested_by: str = "admin",
            reason: str = "") -> dict:
        """校准提交(管理模——46号审批
        不直接生效; decay/scenario
        二选一)

        Raises:
            ValueError: off 态/参数
                非法/双选/缺选/已有
                待生效申请
        """
        require_active_mode()
        from services.av62_registry import (
            HALF_LIFE_MAX, HALF_LIFE_MIN,
            SCENARIO_FACTORS,
            SCENARIO_MULT_MAX,
            SCENARIO_MULT_MIN,
        )
        reason = str(reason or "").strip()
        if not reason:
            raise ValueError(
                "校准理由必填(可审计性)")

        has_decay = half_life_days is not None
        has_scenario = bool(
            str(scenario or "").strip())
        if has_decay and has_scenario:
            raise ValueError(
                "decay 与 scenario 不可同时"
                "提交(单域单次)")
        if not has_decay \
                and not has_scenario:
            raise ValueError(
                "须提供 halfLifeDays(decay)"
                "或 scenario+multiplier"
                "(scenario)其一")

        if has_decay:
            tier = "decay"
            days = int(half_life_days)
            if not HALF_LIFE_MIN \
                    <= days <= HALF_LIFE_MAX:
                raise ValueError(
                    f"半衰期 {days} 越界"
                    f"(合法 {HALF_LIFE_MIN}"
                    f"-{HALF_LIFE_MAX} 日)")
            config = {
                "halfLifeDays": days}
            payload = config
        else:
            name = str(scenario).strip()
            if name not in \
                    SCENARIO_FACTORS:
                raise ValueError(
                    f"场景 {name} 域外"
                    f"(合法: {'/'.join(
                        SCENARIO_FACTORS)})")
            m = float(multiplier
                      if multiplier
                      is not None else 0)
            if not SCENARIO_MULT_MIN \
                    <= m <= SCENARIO_MULT_MAX:
                raise ValueError(
                    f"场景乘子 {m} 越界"
                    f"(合法 "
                    f"{SCENARIO_MULT_MIN}"
                    f"-{SCENARIO_MULT_MAX})")
            tier = f"scenario:{name}"
            config = {
                "scenario": name,
                "multiplier": m}
            payload = config

        existing = await self.repo \
            .get_threshold(tier)
        if existing \
                and existing.get(
                    "status") == "pending":
            raise ValueError(
                f"该域已有待生效申请"
                f"(changeId="
                f"{existing.get('changeId')}"
                f"——先处置再提交)")

        from services.ai_governance_service import (
            AiGovernanceService,
        )
        result = await (
            AiGovernanceService()
            .submit_change(
                scorer_id=SCORER_ID,
                kind="config",
                payload=payload,
                reason=reason[:500],
                requested_by=requested_by))
        change_id = int(
            result.get("changeId") or 0)

        await self.repo.save_threshold({
            "tier": tier,
            "config": config,
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
            "tier": tier,
            "changeId": change_id,
            "config": config,
            "requestedBy": requested_by,
        })
        return {
            "success": True,
            "status": "pending",
            "tier": tier,
            "changeId": change_id,
            "config": config,
            "note": "阈值校准已提交 46号审批"
                    "(不直接生效——人工终审轨)",
            "submittedAt": ts(),
        }

    async def calibrate_apply(
            self, change_id: int,
            applied_by: str = "admin"
            ) -> dict:
        """校准生效(终审模——46号
        reviewedBy 留痕; 不受开关
        影响·人工铁律)

        Raises:
            KeyError: 变更不存在
            ValueError: 未裁决/不匹配/
                已生效
        """
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

        # 匹配待生效申请(扫描阈值域)
        matched_tier = None
        rec = None
        for tier in ("decay", "objective",
                     *(f"scenario:{name}"
                       for name in (
                           "bidding",
                           "financing",
                           "partnership",
                           "expedited"))):
            r = await self.repo \
                .get_threshold(tier)
            if r \
                    and r.get("changeId") \
                    == int(change_id) \
                    and r.get("status") \
                    == "pending":
                matched_tier, rec = \
                    tier, r
                break
        if rec is None:
            raise ValueError(
                f"无 changeId={change_id} 的"
                f"待生效阈值申请")

        rec.update({
            "status": "applied",
            "appliedBy": applied_by,
            "updatedAt": ts()})
        await self.repo.save_threshold(rec)
        await self._track({
            "action": "threshold_apply",
            "tier": matched_tier,
            "changeId": int(change_id),
            "appliedBy": applied_by,
        })
        return {
            "success": True,
            "status": "applied",
            "tier": matched_tier,
            "changeId": int(change_id),
            "config": rec.get("config"),
            "appliedBy": applied_by,
            "note": "阈值已生效(46号审批+"
                    "人工终审双模完成)",
            "appliedAt": ts(),
        }

    # ============================================================
    # 观测面
    # ============================================================

    async def thresholds_view(self) -> dict:
        """阈值视图(生效配置+46号审批
        留痕——观测面不受开关影响)"""
        from services.av62_registry import (
            DECAY_HALF_LIFE_DAYS,
        )
        records = await self.repo \
            .list_thresholds(limit=50)
        applied = {
            r.get("tier"): r.get("config")
            for r in records
            if r.get("status") == "applied"}
        return {
            "success": True,
            "active": applied,
            "defaults": {
                "halfLifeDays":
                    DECAY_HALF_LIFE_DAYS,
                "scenarioMultiplier": 1.0,
            },
            "registry": records,
            "note": "阈值配置域——decay 半衰期"
                    "/scenario 场景乘子"
                    "(46号审批+人工终审双模)",
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
                "assetId": 0,
                "eventType": "threshold",
                "detail": detail,
                "createdAt": ts(),
            })
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "av62_threshold_track_failed: %s",
                exc)
