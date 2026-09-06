"""61号·AI智能系统升级决策 三级决策矩阵
(dm61_assess_service, P1)

计划(docs/61号_AI智能系统升级决策模块实施计划.md
§3.2/§七 P1):
    ① riskScore 四因子(确定性公式):
        riskScore = w1×sensitivity(敏感级)
                  + w2×impactScope(影响面)
                  + w3×historyFailRate(历史失败率)
                  + w4×confidence(信值置信度反向)
                  ± errorBudget(容错预算域调节)
    ② 容错预算域(计划 §二 SRE 范式):
        预算充足≥0.5→风险调节-10;
        预算耗尽<0.1→调节+20 且强制 L3
    ③ L1/L2/L3 三级判定+窗口不适宜
       自动升级
    ④ 先验检索(同标签历史决策成败统计
       ——决策图谱 P3 完整交付, P1 简版
       确定性聚合)

铁律(计划 §1.3/§3.2):
    - L1 仅限建议域(快速通道语义——
      执行永远走 46号总线)
    - L3 永不自治(核心重构/权限/合规
      规则强制 L3+双人复核)
    - LLM 不进判定链(全程确定性计算)
"""

import hashlib
import logging
import os

from core.helpers import ts

from repositories.dm61_repository import (
    Dm61Repository,
)

logger = logging.getLogger("dm61_assess")

MODEL_VERSION = "v1-dm61-assess"

# 四因子权重(和=1.0)
RISK_WEIGHTS = {
    "sensitivity": 0.35,
    "impactScope": 0.25,
    "historyFailRate": 0.25,
    "confidence": 0.15,
}

# 容错预算域调节
ERROR_BUDGET_HEALTHY = 0.5    # 充足(调节 -10)
ERROR_BUDGET_EXHAUSTED = 0.1  # 耗尽(调节 +20 且强制 L3)
BUDGET_HEALTHY_ADJUST = -10.0
BUDGET_EXHAUSTED_ADJUST = 20.0

# 级别阈值(riskScore 判定)
L1_MAX_RISK = 30.0
L3_MIN_RISK = 65.0

# 强制 L3 标签域(计划 §3.2——核心重构/
# 权限变更/合规规则: 高风险人类全程主导)
L3_FORCE_TAGS = (
    "core_refactor", "permission_change",
    "compliance_rule")

# 47号 tier 信值基线(置信度反向口径)
TIER_BASE = {
    "trusted": 90.0,
    "standard": 70.0,
    "watched": 50.0,
    "restricted": 30.0,
}

# 决策级别域
DECISION_LEVELS = ("L1", "L2", "L3")

LEVEL_NAMES = {
    "L1": "自治级(快速通道——仅限"
          "建议域)",
    "L2": "协同级(Top3 方案+人类选择)",
    "L3": "管控级(分析报告+人类全程"
          "主导)",
}

# 窗口不适宜自动升级档数
WINDOW_UPGRADE = {
    "suitable": 0,
    "caution": 1,
    "unsuitable": 2,
}


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


def _fingerprint(*parts) -> str:
    raw = "|".join(str(p) for p in parts)
    return "sha256:" + hashlib.sha256(
        raw.encode("utf-8")).hexdigest()[:32]


class Dm61AssessService:
    """61号三级决策矩阵(P1——风险评估)"""

    def __init__(self):
        self.repo = Dm61Repository()

    # ============================================================
    # 风险评估(assess)
    # ============================================================

    async def assess(self, request_id: int,
                    tier: str = None,
                    error_budget: float = None,
                    history_fail_rate: float = None
                    ) -> dict:
        """风险评估+先验检索+L1/L2/L3 判定

        状态机: tagged→assessed(P2 后允许
        simulated 进——本期仅 tagged)

        Args:
            request_id: 决策请求号
            tier: 47号 tier(缺省纯读取
                 fail-soft standard)
            error_budget: 容错预算 0-1
                (缺省环境感知推导——56号
                提案失败率反向)
            history_fail_rate: 历史失败率
                (缺省先验检索——同标签历史
                决策失败占比)

        Raises:
            KeyError: 请求不存在
            ValueError: off 态/状态机非法
        """
        require_active_mode()
        request = await self.repo.get_request(
            int(request_id))
        if not request:
            raise KeyError(
                f"决策请求 {request_id} 不存在")
        status = str(request.get("status"))
        if status != "tagged":
            raise ValueError(
                f"请求 {request_id} 状态 "
                f"{status} 不可评估"
                f"(需 tagged 态)")

        # ---- 因子采集(确定性) ----
        semantic = request.get(
            "semantic") or {}
        impact = request.get("impact") or {}
        environment = request.get(
            "environment") or {}

        # ① 敏感级(P0 注册表风险基线)
        from services.dm61_registry import (
            SENSITIVITY_RISK_BASE,
        )
        sensitivity = str(
            semantic.get("sensitivity")
            or "observe")
        s_sens = float(
            SENSITIVITY_RISK_BASE.get(
                sensitivity, 10.0))

        # ② 影响面(impactPct→0-100)
        impact_pct = float(
            impact.get("impactPct") or 0.0)
        s_impact = round(
            min(100.0,
                impact_pct * 10.0), 1)

        # ③ 历史失败率(缺省先验检索)
        prior = None
        if history_fail_rate is None:
            prior, history_fail_rate = \
                await self._retrieve_prior(
                    str(semantic.get("tag")
                        or ""))
        s_fail = round(
            min(100.0,
                float(history_fail_rate
                      or 0.0) * 100.0), 1)

        # ④ 信值置信度反向(tier 基线倒扣)
        if tier is None:
            tier = await self._member_tier(
                request.get("requestedBy"))
        tier = str(tier or "standard")
        tier_base = float(
            TIER_BASE.get(tier, 70.0))
        s_conf = round(
            max(0.0, 100.0 - tier_base), 1)

        # ⑤ 容错预算域(缺省环境感知推导)
        budget_source = "input"
        if error_budget is None:
            error_budget = \
                await self._derive_budget()
            budget_source = "derived"
        error_budget = round(max(
            0.0, min(1.0,
                     float(error_budget
                           or 0.0))), 4)

        # ---- riskScore 加权 ----
        w = RISK_WEIGHTS
        raw = (s_sens * w["sensitivity"]
               + s_impact * w["impactScope"]
               + s_fail * w["historyFailRate"]
               + s_conf * w["confidence"])

        # 容错预算调节
        budget_forced_l3 = False
        if error_budget < \
                ERROR_BUDGET_EXHAUSTED:
            adjust = BUDGET_EXHAUSTED_ADJUST
            budget_forced_l3 = True
        elif error_budget >= \
                ERROR_BUDGET_HEALTHY:
            adjust = BUDGET_HEALTHY_ADJUST
        else:
            adjust = 0.0
        risk_score = round(
            max(0.0, min(100.0,
                         raw + adjust)), 1)

        # ---- L1/L2/L3 判定 ----
        tag = str(semantic.get("tag") or "")
        forced_l3_tag = tag in L3_FORCE_TAGS
        # 生效阈值读取(P2 阈值域联动——
        # 46号审批+人工终审后可校准;
        # fail-soft 回落注册表常量)
        from services.dm61_threshold_service import (
            Dm61ThresholdService,
        )
        active_thresholds = \
            await Dm61ThresholdService(
            ).get_active()
        l1_max_risk = float(
            active_thresholds.get(
                "l1MaxRisk")
            or L1_MAX_RISK)
        l3_min_risk = float(
            active_thresholds.get(
                "l3MinRisk")
            or L3_MIN_RISK)
        # L1 判定(计划 §3.2 示例口径——
        # 观测类+影响≤1%+历史失败≤1%
        # +预算充足+低风险分)
        if (sensitivity == "observe"
                and impact_pct <= 1.0
                and s_fail <= 1.0
                and error_budget
                >= ERROR_BUDGET_HEALTHY
                and risk_score
                < l1_max_risk
                and not forced_l3_tag):
            level = "L1"
        elif (forced_l3_tag
              or budget_forced_l3
              or risk_score >= l3_min_risk):
            level = "L3"
        else:
            level = "L2"

        # 窗口不适宜自动升一级
        window_level = str(
            environment.get("level")
            or "suitable")
        upgrades = WINDOW_UPGRADE.get(
            window_level, 0)
        upgraded_by_window = False
        if upgrades:
            idx = DECISION_LEVELS.index(
                level)
            new_idx = min(
                len(DECISION_LEVELS) - 1,
                idx + upgrades)
            if new_idx > idx:
                level = DECISION_LEVELS[
                    new_idx]
                upgraded_by_window = True

        factors = {
            "sensitivity": {
                "score": s_sens,
                "weight": w["sensitivity"],
                "detail": f"敏感级 {sensitivity}"
                          f" 基线 {s_sens}"},
            "impactScope": {
                "score": s_impact,
                "weight": w["impactScope"],
                "detail": f"影响面 {impact_pct}%"
                          f"→{s_impact}"},
            "historyFailRate": {
                "score": s_fail,
                "weight":
                    w["historyFailRate"],
                "detail": f"历史失败率 "
                          f"{s_fail / 100:.0%}"},
            "confidence": {
                "score": s_conf,
                "weight": w["confidence"],
                "detail": f"tier {tier} 置信"
                          f"反向 {s_conf}"},
            "errorBudget": {
                "value": error_budget,
                "source": budget_source,
                "adjust": adjust,
                "detail": f"容错预算 "
                          f"{error_budget:.0%}"
                          f"(调节{adjust:+.0f})"},
        }

        # ---- 评估落库 ----
        assess_id = await \
            self.repo.next_assess_id()
        fingerprint = _fingerprint(
            assess_id, request_id,
            risk_score, level)
        record = {
            "assessId": assess_id,
            "requestId": int(request_id),
            "riskScore": risk_score,
            "level": level,
            "tag": tag,
            "sensitivity": sensitivity,
            "impactPct": impact_pct,
            "tier": tier,
            "errorBudget": error_budget,
            "historyFailRate":
                round(float(
                    history_fail_rate
                    or 0.0), 4),
            "factors": factors,
            "prior": prior,
            "windowLevel": window_level,
            "upgradedByWindow":
                upgraded_by_window,
            "forcedL3Tag": forced_l3_tag,
            "budgetForcedL3":
                budget_forced_l3,
            "fingerprint": fingerprint,
            "createdAt": ts(),
        }
        await self.repo.save_assessment(
            record)

        # 请求状态推进 tagged→assessed
        request["status"] = "assessed"
        request["assessId"] = assess_id
        request["updatedAt"] = ts()
        await self.repo.save_request(
            request, create=False)

        await self._track(
            assess_id, "assess", {
                "requestId": int(request_id),
                "riskScore": risk_score,
                "level": level,
                "upgradedByWindow":
                    upgraded_by_window,
            })
        return {
            "success": True,
            "assessId": assess_id,
            "requestId": int(request_id),
            "riskScore": risk_score,
            "level": level,
            "levelName": LEVEL_NAMES[level],
            "factors": factors,
            "prior": prior,
            "windowLevel": window_level,
            "upgradedByWindow":
                upgraded_by_window,
            "forcedL3Tag": forced_l3_tag,
            "budgetForcedL3":
                budget_forced_l3,
            "fingerprint": fingerprint,
            "note": "风险评估——四因子加权+"
                    "容错预算调节+三级判定"
                    "(L1 仅限建议域; L3 永不"
                    "自治铁律)",
            "assessedAt": record["createdAt"],
        }

    # --------------------------------------------------------
    # 先验检索(简版决策图谱——确定性聚合)
    # --------------------------------------------------------

    async def _retrieve_prior(self,
                              tag: str) -> tuple:
        """同标签历史决策先验(成败统计)

        Returns:
            (prior_dict, fail_rate)——
            无历史时 (None, 0.0)
        """
        decisions = await (
            self.repo.list_decisions(
                limit=500))
        same = [d for d in decisions
                if str(d.get("tag") or "")
                == tag
                and d.get("outcome")]
        if not same:
            return None, 0.0
        outcomes = [str(d.get("outcome"))
                    for d in same]
        failed = sum(
            1 for o in outcomes
            if o == "rejected")
        fail_rate = round(
            failed / len(outcomes), 4)
        prior = {
            "tag": tag,
            "sampleSize": len(same),
            "failed": failed,
            "failRate": fail_rate,
            "source": "dm61_decisions 聚合"
                      "(决策图谱 P3 完整交付)",
        }
        return prior, fail_rate

    # --------------------------------------------------------
    # 内部(感知——纯读取 fail-soft)
    # --------------------------------------------------------

    @staticmethod
    async def _member_tier(ref) -> str:
        """47号 tier 纯读取(fail-soft
        standard)"""
        try:
            member_id = int(ref) \
                if str(ref).isdigit() else 0
            if member_id <= 0:
                return "standard"
            from services.trust_risk_profile_service import (
                TrustRiskProfileService,
            )
            profile = await (
                TrustRiskProfileService()
                .get_profile(member_id))
            return str(profile.get("tier")
                       or "standard")
        except Exception:  # noqa: BLE001
            return "standard"

    @staticmethod
    async def _derive_budget() -> float:
        """容错预算推导(56号提案失败率
        反向——纯读取 fail-soft 中性 0.5)

        失败率 0→预算 1.0; 失败率 100%
        →预算 0.0(线性)。
        """
        try:
            from repositories.aiup56_repository import (
                Aiup56Repository,
            )
            proposals = await (
                Aiup56Repository()
                .list_proposals(limit=100))
            if not proposals:
                return 0.5
            failed = sum(
                1 for p in proposals
                if str(p.get("status") or "")
                in ("failed", "rejected",
                    "aborted"))
            return round(
                1.0 - failed
                / len(proposals), 4)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "dm61_budget_failsoft: %s",
                exc)
            return 0.5

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
