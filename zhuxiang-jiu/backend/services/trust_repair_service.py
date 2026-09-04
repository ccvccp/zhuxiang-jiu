"""45号·P2 即时修复引擎("放下屠刀, 立地成佛"算法)

计划(docs/45号_信值模块实施计划.md §五):
    工程化转译: "立地成佛" ≠ 瞬间清零, 而是违规确认的那一刻,
    修复通道即时开启 + 高效激励窗口打开——系统给改过者一条
    清晰、可执行、有希望的路径, 而非机械惩罚。

    修复值数学模型:
        ΔV_repair = α × Σ(β_i × V_i × γ(t_i)) × AI验真分
        α  修复上限系数: general=1.0 / severe=0.3 / criminal=0
        β_i 行为关联度权重(针对性修复 > 通用修复):
            酒驾→交通安全宣讲 β=1.5; 泛泛捐款 β=0.3
        γ(t) 时效衰减: e^(-λt), λ=0.1
            → 24h 内修复效率 ≈ 30 天后的 18~20 倍
        V_i 修复行为自身验证价值(验真管线置信度折算)

    三重校验(防洗白):
        1. 上限保护: 单次修复回分不超过 违规扣分×α
           (天花板——永远追不回的部分就是"代价")
        2. 针对性加权: β 由 违规类型×修复行为 语义关联度决定
           (mock 态确定性规则映射表; real 轨 LLM 归类可回退)
        3. 验真前置: 修复证据走 P1 验真管线, 表演式修复
           (摆拍/代打卡)被多模态鉴别拦截

    即时性: 违规确认(事件落库)即开通道——查询修复计划时
    违规事件即列即建议, 无等待期。

存储:
    表 trust45_repairs(修复留痕, §5.4):
    {repairId, trustId, violationEventId, repairs[{kind,
    beta, value, evidence, verified}], gain, applied,
    beforeScore, afterScore, ts}
"""

import logging
import math

from core.helpers import ts

from repositories.trust_value_repository import (
    TrustValue45Repository,
)
from services.trust_scoring_service import (
    TrustProfileService, FUSE_ALPHA, TrustValueScorer,
)
from services.trust_radar_service import verify_pipeline

logger = logging.getLogger(__name__)

# 时效衰减系数 λ(计划 §五 5.2)
LAMBDA = 0.1

# 修复行为价值上限(单次修复行为 V_i, 0-100)
REPAIR_VALUE_MAX = 100.0

# ============================================================
# β 关联度映射表(mock 态确定性规则; real 轨 LLM 归类可回退)
# ============================================================

# 违规因子 → 针对性修复行为(高 β) / 通用修复行为(低 β)
BETA_MAP = {
    # L1 违规
    "legal_record": {
        "targeted": [
            ("legal_restitution", 1.5, "司法履行/执行和解"),
            ("legal_education", 1.3, "法律学习与合规培训"),
        ],
        "generic": [
            ("charity_donation", 0.3, "公益捐赠"),
            ("community_service", 0.5, "社区服务"),
        ],
    },
    "regulatory": {
        "targeted": [
            ("regulatory_rectification", 1.5, "监管整改验收"),
            ("compliance_training", 1.3, "合规体系建设"),
        ],
        "generic": [
            ("charity_donation", 0.3, "公益捐赠"),
            ("community_service", 0.5, "社区服务"),
        ],
    },
    "asset_integrity": {
        "targeted": [
            ("contract_fulfillment", 1.5, "合同重新履约"),
            ("compensation_paid", 1.4, "违约赔偿支付"),
        ],
        "generic": [
            ("charity_donation", 0.3, "公益捐赠"),
        ],
    },
    # L2 违规
    "platform_conduct": {
        "targeted": [
            ("public_apology", 1.2, "公开道歉与更正"),
            ("platform_rectification", 1.4, "平台违规整改"),
        ],
        "generic": [
            ("charity_donation", 0.3, "公益捐赠"),
        ],
    },
    "community_standing": {
        "targeted": [
            ("community_service", 1.5, "社区公益服务"),
            ("public_apology", 1.2, "公开道歉与更正"),
        ],
        "generic": [
            ("charity_donation", 0.3, "公益捐赠"),
        ],
    },
    # L3(极少违规; 通用兜底)
    "_default": {
        "targeted": [
            ("community_service", 1.3, "社区公益服务"),
        ],
        "generic": [
            ("charity_donation", 0.3, "公益捐赠"),
        ],
    },
}

# 修复行为 kind → 中文说明(修复计划展示用)
REPAIR_KIND_LABELS = {
    "legal_restitution": "司法履行/执行和解",
    "legal_education": "法律学习与合规培训",
    "regulatory_rectification": "监管整改验收",
    "compliance_training": "合规体系建设",
    "contract_fulfillment": "合同重新履约",
    "compensation_paid": "违约赔偿支付",
    "public_apology": "公开道歉与更正",
    "platform_rectification": "平台违规整改",
    "community_service": "社区公益服务",
    "charity_donation": "公益捐赠",
}


def beta_of(violation_factor: str, repair_kind: str) -> float:
    """β 关联度(违规因子 × 修复行为语义关联度)

    针对性修复(映射表 targeted)按表值; 通用按表值;
    未知修复行为按通用兜底 0.3(宁保守勿高估)。
    """
    table = BETA_MAP.get(violation_factor) or BETA_MAP["_default"]
    for kind, beta, _label in (table["targeted"]
                               + table["generic"]):
        if kind == repair_kind:
            return beta
    return 0.3   # 未知行为兜底(通用折扣)


def gamma_of(days_since: float) -> float:
    """γ(t) 时效衰减: e^(-λt)

    24h 内(1 天) γ≈0.905; 30 天后 γ≈0.0498——
    早期修复效率约为 30 天后的 18 倍(高效激励窗口)。
    """
    return math.exp(-LAMBDA * max(0.0, float(days_since or 0)))


def repair_gain(alpha: float, items: list,
                 authenticity: float) -> float:
    """修复值计算(§五 5.2 数学模型)

    Args:
        alpha: 熔断分级修复上限系数
        items: [{kind, beta, value, daysSince}] 已验真修复项
        authenticity: AI 验真分均值(0-1)
    Returns:
        ΔV_repair(修复值, ≥0)
    """
    if alpha <= 0:
        return 0.0
    total = sum(
        float(it.get("beta") or 0)
        * min(REPAIR_VALUE_MAX,
              float(it.get("value") or 0))
        * gamma_of(it.get("daysSince"))
        for it in items)
    return round(alpha * total
                 * max(0.0, min(1.0, float(authenticity))), 1)


# ============================================================
# 修复引擎服务
# ============================================================


class TrustRepairService:
    """即时修复引擎(P2; 提交→验真→计算→天花板→入分→留痕)"""

    def __init__(self,
                 repo: TrustValue45Repository =
                 TrustValue45Repository()):
        self.repo = repo

    async def repair_plan(self, trust_id: int) -> dict:
        """修复建议路径(即时性——违规即列, 无等待期)

        针对当前熔断态/最近违规事件, 给出 β 加权最优的
        修复行为清单(高 β 针对性优先展示)。

        Raises:
            KeyError: 档案不存在
        """
        profile = await self.repo.get_profile(trust_id)
        if profile is None:
            raise KeyError(f"信值档案 {trust_id} 不存在")

        events = await self.repo.list_events_by_trust(trust_id)
        # 违规事件 = 负向 delta 事件(即时性: 全部历史违规可修)
        violations = [e for e in events
                      if (e.get("delta") or 0) < 0]
        plans = []
        for v in violations:
            factor = v.get("factor") or ""
            table = BETA_MAP.get(factor) or BETA_MAP["_default"]
            items = [
                {"kind": kind, "beta": beta, "label": label,
                 "targeted": i < len(table["targeted"])}
                for i, (kind, beta, label) in enumerate(
                    table["targeted"] + table["generic"])]
            # β 降序(最优路径在前)
            items.sort(key=lambda x: -x["beta"])
            days = _days_since(v.get("ts"))
            plans.append({
                "violationEventId": v.get("eventId"),
                "violationFactor": factor,
                "violationLayer": v.get("layer"),
                "severity": v.get("severity"),
                "violationDelta": v.get("delta"),
                "daysSince": round(days, 1),
                "gammaNow": round(gamma_of(days), 3),
                "alpha": FUSE_ALPHA.get(
                    profile.get("fusedLevel") or "", 1.0),
                "recommendedRepairs": items,
            })
        # 时间近的违规优先(时效窗口激励)
        plans.sort(key=lambda p: p["daysSince"])
        return {
            "success": True, "trustId": trust_id,
            "fused": profile.get("fused"),
            "fusedLevel": profile.get("fusedLevel"),
            "fuseAlpha": FUSE_ALPHA.get(
                profile.get("fusedLevel") or "", 1.0),
            "violationsRepairable": len(plans),
            "note": ("修复通道已开启(即时生效)——24 小时内修复"
                     "效率约为 30 天后的 18 倍, 越早修复回报越高"
                     if plans else "当前无可修复违规项"),
            "plans": plans,
        }

    async def submit_repair(self, trust_id: int,
                            violation_event_id: int,
                            repairs: list,
                            sources: list = None,
                            verify_mode: str = "v1") -> dict:
        """提交修复证据包(验真 → 修复值计算 → 天花板 → 入分)

        Args:
            repairs: [{kind, value(0-100), evidence,
            daysSince?(缺省按提交时刻 0——即时修复)}]
            verify_mode: "v1"(默认既有管线)/"v2"(P7 真伪
                鉴别引擎——自适应权重融合, 修复类重时序)
        Raises:
            KeyError: 档案/违规事件不存在
            ValueError: 参数非法/criminal 不可修复
        """
        profile = await self.repo.get_profile(trust_id)
        if profile is None:
            raise KeyError(f"信值档案 {trust_id} 不存在")
        alpha = FUSE_ALPHA.get(
            profile.get("fusedLevel") or "", 1.0)
        if alpha <= 0:
            raise ValueError(
                "永久熔断(criminal)不可修复——修复通道关闭")

        # 定位违规事件
        violation = await self._find_event(violation_event_id)
        if violation is None or \
                violation.get("trustId") != trust_id:
            raise KeyError(
                f"违规事件 {violation_event_id} 不存在"
                f"(或不属于该档案)")
        v_delta = float(violation.get("delta") or 0)
        if v_delta >= 0:
            raise ValueError(
                f"事件 {violation_event_id} 非违规事件"
                f"(delta ≥ 0)")
        violation_factor = violation.get("factor") or ""

        # 逐项验真(P1 管线; P7 verify_mode="v2" 走增强
        # 引擎——修复类重时序, 防突击刷分)+ β/γ 计算
        if not repairs or not isinstance(repairs, list):
            raise ValueError("修复项列表不能为空")
        use_v2 = (verify_mode or "v1").lower() == "v2"
        if use_v2:
            history = await self.repo.list_events_by_trust(
                trust_id)
            event_ts = [e.get("ts") for e in history
                        if e.get("ts")]
        checked = []
        authenticity_scores = []
        for it in repairs:
            kind = str(it.get("kind") or "")
            value = float(it.get("value") or 0)
            evidence = str(it.get("evidence") or "")
            if kind not in REPAIR_KIND_LABELS:
                raise ValueError(f"非法修复行为: {kind}")
            if not 0 < value <= REPAIR_VALUE_MAX:
                raise ValueError(
                    f"value 需在 (0, {REPAIR_VALUE_MAX:.0f}]")
            if len(evidence.strip()) < 8:
                raise ValueError("证据内容必填(≥8 字符)")
            if use_v2:
                from services.trust_verify_v2 import (
                    verify_pipeline_v2,
                )
                v = verify_pipeline_v2(
                    kind, evidence,
                    sources or ["self_deposit"],
                    REPAIR_KIND_LABELS.get(kind, kind),
                    event_timestamps=event_ts)
            else:
                v = verify_pipeline(
                    "repair", evidence,
                    sources or ["self_deposit"],
                    REPAIR_KIND_LABELS.get(kind, kind))
            beta = beta_of(violation_factor, kind)
            days = float(it.get("daysSince") or 0)
            checked.append({
                "kind": kind,
                "label": REPAIR_KIND_LABELS[kind],
                "beta": beta, "value": value,
                "daysSince": days,
                "gamma": round(gamma_of(days), 3),
                "evidence": evidence,
                "verified": v["verified"],
                "confidence": v["confidence"],
                "checks": v["checks"],
                "verifyEngine": v.get("engine", "v1"),
                "riskTags": v.get("riskTags"),
                "attribution": v.get("attribution"),
            })
            if v["verified"]:
                authenticity_scores.append(v["confidence"])

        if not authenticity_scores:
            # 全部验真失败: 留痕不入分
            repair_id = await self.repo.next_event_id()
            await self.repo.save_event({
                "eventId": repair_id, "trustId": trust_id,
                "layer": violation.get("layer") or "L1",
                "factor": violation_factor, "delta": 0,
                "severity": "general", "source": "repair_rejected",
                "summary": f"[修复拒] 事件{violation_event_id}"
                           f" 证据验真未通过(全项)",
                "ts": ts(),
            })
            return {"success": True, "repairId": repair_id,
                    "applied": False, "gain": 0.0,
                    "note": "修复证据验真未通过(孤证/摆拍/"
                            "表演式), 不入分——可补独立源重提",
                    "items": checked}

        # 修复值计算(§五 5.2)
        authenticity = (sum(authenticity_scores)
                        / len(authenticity_scores))
        verified_items = [c for c in checked if c["verified"]]
        raw_gain = repair_gain(alpha, verified_items,
                               authenticity)

        # P6 UEBA 再犯风险守门(Value-UEBA 本体对齐):
        # 同因子历史违规序列(含当前)→ risk=n/(n+2);
        # risk>0.7(第 5 次起)修复效率 ×0.5——惯犯通道收窄
        from services.trust_ueba_service import recurrence_gate
        same_factor_violations = sum(
            1 for e in await self.repo.list_events_by_trust(
                trust_id)
            if e.get("factor") == violation_factor
            and (e.get("delta") or 0) < 0)
        recurrence_risk, eff, eff_note = recurrence_gate(
            same_factor_violations)
        if eff_note:
            raw_gain = round(raw_gain * eff, 1)
            logger.info("trust45_repair_recurrence "
                        "trustId=%s factor=%s risk=%s "
                        "eff=%s", trust_id,
                        violation_factor, recurrence_risk, eff)

        # 三重校验①: 天花板 = |违规扣分| × α
        cap = abs(v_delta) * alpha
        gain = round(min(raw_gain, cap), 1)

        # 47号风险画像回流(fail-soft——再犯命中+修复项验真
        # 组件沉淀; P0 纯观察不干预)
        try:
            from services.trust_risk_profile_service import (
                TrustRiskProfileService,
            )
            repair_signals = (["recurrence"]
                              if eff_note else [])
            await TrustRiskProfileService(
            ).record_risk_event(
                trust_id, "repair",
                signals=repair_signals,
                components={
                    "content": min(
                        (it.get("confidence") or 1.0
                         for it in checked), default=1.0)},
                detail=(f"violation={violation_event_id} "
                        f"gain={gain}"))
        except Exception as exc:
            logger.debug("trust47_repair_backflow_skip: %s",
                         exc)

        # 入分: 修复值转正作用于违规因子(回分)
        before_score = profile.get("score")
        svc = TrustProfileService(repo=self.repo)
        # 修复回分幅度: gain 映射到因子增量(保守 1:1 折半——
        # 修复回的是"信用", 不是"资产", 折半防高频小额刷修复)
        factor_delta = min(gain / 2.0,
                           TrustValueScorer.WEIGHTS.get(
                               violation_factor, 0.1) * 100)
        result = await svc.record_event(
            trust_id, violation.get("layer") or "L1",
            violation_factor, factor_delta,
            source="repair",
            summary=f"[修复] 事件{violation_event_id} "
                   f"{violation_factor} +{factor_delta}"
                   f"(修复值 {gain}, 天花板 {round(cap, 1)}, "
                   f"α={alpha})")

        # 修复留痕(§五 5.4)
        repair_id = await self.repo.next_event_id()
        await self.repo.save_event({
            "eventId": repair_id, "trustId": trust_id,
            "layer": violation.get("layer") or "L1",
            "factor": violation_factor,
            "delta": round(factor_delta, 1),
            "severity": "general", "source": "repair",
            "summary": f"[修复留痕] violation={violation_event_id}"
                       f" gain={gain} cap={round(cap, 1)} "
                       f"alpha={alpha} items={len(verified_items)}",
            "ts": ts(),
        })

        logger.info("trust45_repair trustId=%s violation=%s "
                    "gain=%s(raw=%s cap=%s alpha=%s items=%s)",
                    trust_id, violation_event_id, gain, raw_gain,
                    cap, alpha, len(verified_items))

        # P3 联动: 修复值折半发行 TV(准备金锚定——验真通过
        # 的修复即准备金资产; "修复回信用折半"同口径)
        tv_issued = 0.0
        if gain > 0 and not result.get("fused"):
            try:
                from services.trust_asset_service import (
                    TrustAssetService,
                )
                issue_r = await TrustAssetService(
                    repo=self.repo).issue(
                    trust_id, round(gain / 2.0, 2),
                    reserve_ref=f"repair:{repair_id}",
                    memo=f"修复值发行(修复值 {gain} 折半)")
                tv_issued = round(gain / 2.0, 2)
            except ValueError as exc:
                logger.info("trust45_repair_issue_skip "
                            "trustId=%s: %s", trust_id, exc)
        return {
            "success": True, "repairId": repair_id,
            "violationEventId": violation_event_id,
            "applied": True, "gain": gain,
            "rawGain": raw_gain,
            "cap": round(cap, 1), "alpha": alpha,
            "recurrenceRisk": recurrence_risk,
            "repairEfficiency": eff,
            "recurrenceNote": eff_note,
            "factorDelta": round(factor_delta, 1),
            "beforeScore": before_score,
            "afterScore": result.get("score"),
            "fused": result.get("fused"),
            "fusedLevel": result.get("fusedLevel"),
            "tvIssued": tv_issued,
            "note": (f"修复生效: 回分 +{factor_delta}"
                     f"(修复值 {gain}, 天花板 {round(cap, 1)})"
                     if gain > 0 else
                     "修复值经天花板折减后为 0(修复上限已用尽)"),
            "items": checked,
        }

    async def repair_detail(self, repair_id: int) -> dict:
        """修复明细查询(归因回放)"""
        event = await self._find_event(repair_id)
        if event is None or event.get("source") not in (
                "repair", "repair_rejected"):
            raise KeyError(f"修复记录 {repair_id} 不存在")
        return {
            "success": True, "repairId": repair_id,
            "trustId": event.get("trustId"),
            "status": ("rejected"
                       if event.get("source") == "repair_rejected"
                       else "applied"),
            "layer": event.get("layer"),
            "factor": event.get("factor"),
            "delta": event.get("delta"),
            "summary": event.get("summary"),
            "ts": event.get("ts"),
        }

    async def trigger_verify(self, repair_id: int) -> dict:
        """触发验真(异步验真回调口径——留痕事件的复核查询)

        P1 验真是提交时同步完成的(确定性规则); 本端点保留
        为验真明细查询(每项修复的三道关明细回放)。
        """
        detail = await self.repair_detail(repair_id)
        # 留痕事件的 checks 明细在 summary 中(回放口径)
        return {"success": True, **detail,
                "verifyNote": "验真于提交时同步完成"
                              "(三道关明细见提交响应 items)"}

    async def _find_event(self, event_id: int) -> dict | None:
        """按 eventId 直查事件"""
        from services.trust_radar_service import is_redis_mode
        if is_redis_mode():
            from services.trust_radar_service import _redis, _k
            client = await _redis()
            data = await client.hgetall(_k(
                "trust45", "trust45_events", event_id))
            return self.repo._deserialize(data) if data else None
        self.repo._ensure_store()
        ev = self.repo.store.get("trust45_events", {}).get(
            event_id)
        return dict(ev) if ev else None


def _days_since(ts_str: str) -> float:
    """事件时间距今天数(缺省 0)"""
    if not ts_str:
        return 0.0
    from datetime import datetime, UTC
    try:
        then = datetime.fromisoformat(str(ts_str))
        now = datetime.now(UTC)
        return max(0.0, (now - then).total_seconds() / 86400)
    except (TypeError, ValueError):
        return 0.0
