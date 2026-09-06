"""62号·AI智能无形资产估值 因果估值引擎
(av62_assess_service, P1)

计划(docs/62号_AI智能无形资产估值模型实施计划.md
§3.2/§七 P1):
    ① CAUSAL_RULES 因果规则库
       (av62_registry 封闭注册——要素→
       结果→强度三元组+版本化+46号
       objective 审批)
    ② 贡献度计算:
        contribution = Σ(要素得分 ×
            因果权重 × 置信度系数)
        riskDeduction = Σ(负向要素 ×
            惩罚系数)
        netContribution = contribution
            - riskDeduction
    ③ 置信度三档(证据完整度):
        high ≥0.8 直接生效(active)
        / medium ≥0.5 抽检复核
        (assessed+spotCheck——P2 抽检)
        / low <0.5 强制人工
        (pending_review 不自动生效)
    ④ 归因报告(规则 ID 锚定+证据
       引用——无锚点标记"未验证")
    ⑤ objective 动态权重模式切换
       (stability/growth——46号审批
       双模 submit/apply)

铁律(计划 §1.3/§八):
    - LLM 不进判定链(纯确定性
      计算——AV62_LLM_MODE 仅 P3+
      文案润色)
    - 负资产不因置信度折扣减免
      (risk 域 coef 恒 1.0——防洗白)
    - objective 不可调 risk 域乘子
      (负资产不随目标模式减免)
    - 模式切换未经 46号审批+人工
      终审不可生效
"""

import logging
import os

from core.helpers import ts

from repositories.av62_repository import (
    Av62Repository,
)

logger = logging.getLogger("av62_assess")

MODEL_VERSION = "v1-av62-assess"

SCORER_ID = "asset_valuation"

# 状态机: 评估允许源态(assessing 容留
# ——中断重试幂等语义)
ASSESS_FROM_STATES = (
    "registered", "assessing", "assessed",
    "active", "pending_review")

# 置信度三档(证据完整度阈值+系数)
CONFIDENCE_HIGH = 0.8
CONFIDENCE_MEDIUM = 0.5
CONFIDENCE_COEF = {
    "high": 1.0, "medium": 0.8, "low": 0.5}

# ============================================================
# 证据字段评分元数据(封闭——类型+参照)
#   rate:    0-1 比率 → 直接×100
#   scale:   计数/评分 → min(v, cap)/cap×100
#   verdict: 文本判定 → 否定词 0/肯定词 100/其他 50
# ============================================================

FIELD_META = {
    "licenseCount": ("scale", 10),
    "auditResults": ("verdict", None),
    "esgDisclosure": ("verdict", None),
    "sopDocs": ("scale", 50),
    "techContribs": ("scale", 30),
    "codeCommits": ("scale", 200),
    "operationCompliance": ("rate", None),
    "collabLatency": ("rate", None),
    "dataSharing": ("rate", None),
    "memberActivity": ("rate", None),
    "eventCompliance": ("rate", None),
    "externalReviews": ("scale", 5),
    "valueAlignment": ("rate", None),
    "transparency": ("rate", None),
    "skillCerts": ("scale", 10),
    "deliveryQuality": ("rate", None),
    "knowledgeSharing": ("scale", 30),
    "peerReviews": ("scale", 5),
    "privacyBehavior": ("rate", None),
    "learningInvest": ("rate", None),
    "errorCorrection": ("rate", None),
    "crossAdapt": ("rate", None),
    "penaltyRecords": ("scale", 5),
    "complaintRate": ("rate", None),
}

VERDICT_NEGATIVE = ("不", "未", "无",
                    "no", "fail", "reject")
VERDICT_POSITIVE = ("通过", "已", "合格",
                    "yes", "pass", "good")

DEFAULT_OBJECTIVE = "stability"


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


def _clamp(value: float, low: float,
           high: float) -> float:
    return max(low, min(high, value))


def _score_field(field: str, value) -> float:
    """单字段确定性评分(0-100)"""
    meta = FIELD_META.get(field)
    if meta is None:
        return 0.0
    ftype, cap = meta
    if value is None or value == "":
        return 0.0
    if ftype == "rate":
        try:
            return _clamp(
                float(value), 0.0, 1.0) * 100
        except (TypeError, ValueError):
            return 0.0
    if ftype == "scale":
        try:
            v = float(value)
        except (TypeError, ValueError):
            return 0.0
        return _clamp(
            min(v, float(cap)), 0.0,
            float(cap)) / float(cap) * 100
    # verdict 文本判定
    text = str(value).strip().lower()
    if not text:
        return 0.0
    for kw in VERDICT_NEGATIVE:
        if kw in text:
            return 0.0
    for kw in VERDICT_POSITIVE:
        if kw in text:
            return 100.0
    return 50.0


def _confidence_tier(completeness: float
                    ) -> tuple[str, float]:
    """置信度三档(证据完整度→档位+系数)"""
    if completeness >= CONFIDENCE_HIGH:
        return "high", CONFIDENCE_COEF["high"]
    if completeness >= CONFIDENCE_MEDIUM:
        return "medium", CONFIDENCE_COEF[
            "medium"]
    return "low", CONFIDENCE_COEF["low"]


class Av62AssessService:
    """62号因果估值引擎(P1——纯确定性
    计算, LLM 不进判定链)"""

    def __init__(self):
        self.repo = Av62Repository()

    # ============================================================
    # 评估入口(资产级)
    # ============================================================

    async def assess_asset(self,
                           asset_id: int,
                           assessed_by: str = "admin"
                           ) -> dict:
        """资产估值(贡献度+置信度+归因)

        状态机: registered/assessed/active/
        pending_review → assessing →
        active(high)/assessed+spotCheck
        (medium)/pending_review(low)

        Raises:
            KeyError: 资产不存在
            ValueError: off 态/状态机拒绝
        """
        require_active_mode()
        asset = await self.repo.get_asset(
            int(asset_id))
        if not asset:
            raise KeyError(
                f"资产 {asset_id} 不存在")
        if asset.get("status") \
                not in ASSESS_FROM_STATES:
            raise ValueError(
                f"资产状态 {asset.get('status')}"
                f" 不可评估(合法源态: "
                f"{'/'.join(
                    ASSESS_FROM_STATES)})")

        from services.av62_registry import (
            RISK_DOMAIN, get_element,
            get_objective_multiplier,
            get_rule,
        )
        role = asset.get("role")
        domain = asset.get("domain")
        element = get_element(role, domain) or {}
        schema = list(element.get(
            "evidenceSchema") or [])
        evidence = asset.get("evidence") or {}

        # ① 因果规则锚定(归因强制绑定)
        rule = get_rule(role, domain)

        # ② 要素得分(逐字段+完整度)
        factors = []
        for field in schema:
            value = evidence.get(field)
            factors.append({
                "field": field,
                "value": value,
                "type": FIELD_META.get(
                    field, ("?", None))[0],
                "score": round(_score_field(
                    field, value), 1),
            })
        filled = sum(
            1 for f in factors
            if f["value"] not in (None, ""))
        completeness = (filled / len(schema)
                       if schema else 0.0)
        # 要素得分: 正资产=均值(缺证
        # 自然低); 负资产=最大值
        # (fail-safe——严重度不因
        # 证据不全减免)
        negative = domain == RISK_DOMAIN
        if negative:
            element_score = round(
                max((f["score"]
                     for f in factors),
                    default=0.0), 1)
        else:
            element_score = round(
                sum(f["score"]
                    for f in factors)
                / len(factors), 1) \
                if factors else 0.0

        # ③ 置信度三档(risk 域系数恒
        #    1.0——负资产不因证据不全减免)
        tier, coef = _confidence_tier(
            completeness)
        if negative:
            coef = 1.0

        # ④ 因果权重(objective 动态乘子
        #    经 46号审批生效)
        objective = await \
            self.get_active_objective()
        strength = float(
            (rule or {}).get("strength")
            or 0.0)
        obj_mult = get_objective_multiplier(
            objective, domain)
        base_weight = float(
            element.get("weight") or 0)
        causal_weight = round(
            base_weight * strength * obj_mult,
            4)

        # ⑤ 贡献度公式(计划 §3.2)
        weighted = round(
            element_score / 100.0
            * causal_weight * coef, 4)
        contribution = (weighted
                        if weighted > 0 else 0.0)
        risk_deduction = (abs(weighted)
                          if weighted < 0 else 0.0)
        net_contribution = weighted

        # ⑥ 终态(high 直接生效/medium
        #    抽检/low 强制人工)
        if negative:
            asset_status = "active"
            spot_check = False
        elif tier == "high":
            asset_status = "active"
            spot_check = False
        elif tier == "medium":
            asset_status = "assessed"
            spot_check = True
        else:
            asset_status = "pending_review"
            spot_check = False

        # ⑦ 版本链(重估递增)
        version = await \
            self._next_version(int(asset_id))

        # ⑧ 归因链(规则 ID 锚定+证据引用)
        attribution = self._attribute(
            asset=asset, rule=rule,
            element_score=element_score,
            causal_weight=causal_weight,
            tier=tier, coef=coef,
            contribution=contribution,
            risk_deduction=risk_deduction,
            net_contribution=net_contribution,
            factors=factors)

        # ⑨ 落库(资产态迁移+评估记录)
        asset.update({
            "status": "assessing",
            "updatedAt": ts()})
        await self.repo.save_asset(
            asset, create=False)

        assess_id = await \
            self.repo.next_assess_id()
        record = {
            "assessId": assess_id,
            "assetId": int(asset_id),
            "subjectId": int(
                asset.get("subjectId") or 0),
            "role": role, "domain": domain,
            "negative": negative,
            "version": version,
            "elementScore": element_score,
            "causalWeight": causal_weight,
            "confidenceTier": tier,
            "confidenceCoef": coef,
            "completeness": round(
                completeness, 4),
            "objective": objective,
            "spotCheck": spot_check,
            "contribution": round(
                contribution, 4),
            "riskDeduction": round(
                risk_deduction, 4),
            "netContribution": net_contribution,
            "baseValue": round(_clamp(
                element_score * coef
                if not negative
                else element_score,
                0, 100), 1),
            "ruleId": (rule or {}).get(
                "ruleId", ""),
            "outcome": (rule or {}).get(
                "outcome", ""),
            "factors": factors,
            "attribution": attribution,
            "status": "assessed",
            "assetStatus": asset_status,
            "assessedBy": str(
                assessed_by or "admin"),
            "createdAt": ts(),
        }
        await self.repo.save_assessment(record)

        asset.update({
            "status": asset_status,
            "updatedAt": ts()})
        await self.repo.save_asset(
            asset, create=False)

        await self._track("assess", {
            "assessId": assess_id,
            "assetId": int(asset_id),
            "version": version,
            "confidenceTier": tier,
            "netContribution": net_contribution,
            "objective": objective,
            "assetStatus": asset_status,
            "assessedBy": assessed_by,
        })
        return {
            "success": True,
            "assessId": assess_id,
            "assetId": int(asset_id),
            "version": version,
            "negative": negative,
            "objective": objective,
            "elementScore": element_score,
            "causalWeight": causal_weight,
            "confidenceTier": tier,
            "confidenceCoef": coef,
            "completeness": round(
                completeness, 4),
            "contribution": round(
                contribution, 4),
            "riskDeduction": round(
                risk_deduction, 4),
            "netContribution": net_contribution,
            "baseValue": record["baseValue"],
            "assetStatus": asset_status,
            "spotCheck": spot_check,
            "attribution": attribution,
            "note": "因果估值完成——规则 "
                    f"{(rule or {}).get('ruleId')}"
                    " 锚定(置信度 "
                    f"{tier}·系数 {coef})",
            "createdAt": record["createdAt"],
        }

    # ============================================================
    # 评估入口(主体级聚合)
    # ============================================================

    async def assess_subject(self,
                             subject_id: int,
                             assessed_by: str = "admin"
                             ) -> dict:
        """主体估值(全部资产聚合——
        主体净贡献度+归因汇总)

        Raises:
            ValueError: off 态/主体无资产
        """
        require_active_mode()
        subject_id = int(subject_id or 0)
        assets = await self.repo.list_assets(
            subject_id=subject_id)
        if not assets:
            raise ValueError(
                f"主体 {subject_id} 无登记"
                f"资产(先登记再评估)")

        results = []
        for asset in assets:
            try:
                results.append(
                    await self.assess_asset(
                        int(asset.get("assetId")),
                        assessed_by=assessed_by))
            except ValueError as exc:
                # 状态机不可评估资产跳过
                # (disputed 等)——留痕
                logger.warning(
                    "av62_subject_skip %s: %s",
                    asset.get("assetId"), exc)

        contribution = round(sum(
            r["contribution"]
            for r in results), 4)
        risk_deduction = round(sum(
            r["riskDeduction"]
            for r in results), 4)
        net = round(contribution
                    - risk_deduction, 4)

        # 归因汇总(anchored/unverified 分列)
        anchored, unverified = [], []
        for r in results:
            entry = dict(
                r.get("attribution") or {})
            entry["assessId"] = \
                r.get("assessId")
            if entry.get("verified"):
                anchored.append(entry)
            else:
                unverified.append(entry)
        total_n = len(anchored) \
            + len(unverified)
        grounded_rate = round(
            len(anchored) / total_n, 4) \
            if total_n else 1.0

        # 基础信值贡献度(0-100 归一化:
        # 正贡献/正权重理论值-risk 扣减)
        pos_weight_sum = sum(
            abs(r["causalWeight"])
            for r in results
            if not r.get("negative"))
        pos_contrib = sum(
            r["contribution"]
            for r in results
            if not r.get("negative"))
        base_value = round(_clamp(
            (pos_contrib / pos_weight_sum
             * 100) if pos_weight_sum
            else 0.0, 0, 100), 1)

        objective = await \
            self.get_active_objective()
        return {
            "success": True,
            "subjectId": subject_id,
            "objective": objective,
            "assetsAssessed": len(results),
            "contribution": contribution,
            "riskDeduction": risk_deduction,
            "netContribution": net,
            "baseValue": base_value,
            "confidenceBreakdown": {
                tier: sum(1 for r in results
                          if r.get(
                              "confidenceTier")
                          == tier)
                for tier in ("high", "medium",
                             "low")},
            "attribution": {
                "anchored": anchored,
                "unverified": unverified,
                "groundedRate": grounded_rate,
            },
            "assessments": [
                r["assessId"]
                for r in results],
            "note": "主体净贡献度——Σ(要素"
                    "得分×因果权重×置信度)"
                    "-Σ(负向×惩罚系数)"
                    "(归因规则 ID 锚定)",
            "assessedAt": ts(),
        }

    # ============================================================
    # 归因报告(规则 ID 锚定+证据引用)
    # ============================================================

    @staticmethod
    def _attribute(*, asset: dict,
                   rule: dict | None,
                   element_score: float,
                   causal_weight: float,
                   tier: str, coef: float,
                   contribution: float,
                   risk_deduction: float,
                   net_contribution: float,
                   factors: list) -> dict:
        """归因链构造(无规则锚点→
        标记"未验证"——归因幻觉防线)"""
        evidence_refs = {
            f["field"]: f["value"]
            for f in factors
            if f["value"] not in (None, "")}
        entry = {
            "assetId": asset.get("assetId"),
            "subjectId": asset.get(
                "subjectId"),
            "role": asset.get("role"),
            "domain": asset.get("domain"),
            "label": asset.get("label"),
            "negative": asset.get(
                "negative"),
            "elementScore": element_score,
            "causalWeight": causal_weight,
            "confidenceTier": tier,
            "confidenceCoef": coef,
            "contribution": round(
                contribution, 4),
            "riskDeduction": round(
                risk_deduction, 4),
            "netContribution":
                net_contribution,
            "evidenceRefs": evidence_refs,
            "factorScores": [
                {"field": f["field"],
                 "score": f["score"]}
                for f in factors],
        }
        if rule:
            entry.update({
                "ruleId": rule.get("ruleId"),
                "outcome": rule.get("outcome"),
                "strength": rule.get(
                    "strength"),
                "verified": True,
            })
        else:
            entry.update({
                "ruleId": "",
                "outcome": "",
                "strength": 0.0,
                "verified": False,
                "note": "未验证——无因果规则"
                        "锚点(解释不可信)",
            })
        return entry

    # ============================================================
    # objective 动态权重(46号审批双模)
    # ============================================================

    async def objective_submit(
            self, objective: str,
            requested_by: str = "admin",
            reason: str = "") -> dict:
        """objective 模式提交(管理模——
        46号审批不直接生效)

        Raises:
            ValueError: off 态/域外/
                缺理由/已有待生效
        """
        require_active_mode()
        from services.av62_registry import (
            OBJECTIVE_VALUES,
        )
        objective = str(
            objective or "").strip()
        if objective not in OBJECTIVE_VALUES:
            raise ValueError(
                f"objective {objective} 域外"
                f"(合法: {'/'.join(
                    OBJECTIVE_VALUES)})")
        reason = str(reason or "").strip()
        if not reason:
            raise ValueError(
                "模式切换理由必填(可审计性)")

        existing = await self.repo.get_threshold(
            "objective")
        if existing \
                and existing.get("status") \
                == "pending":
            raise ValueError(
                f"已有待生效模式申请"
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
                payload={
                    "objective": objective},
                reason=reason[:500],
                requested_by=requested_by))
        change_id = int(
            result.get("changeId") or 0)

        await self.repo.save_threshold({
            "tier": "objective",
            "config": {
                "objective": objective},
            "status": "pending",
            "changeId": change_id,
            "requestedBy": str(
                requested_by or "admin"),
            "reason": reason[:500],
            "appliedBy": "",
            "createdAt": ts(),
            "updatedAt": ts(),
        })
        await self._track("objective_submit", {
            "changeId": change_id,
            "objective": objective,
            "requestedBy": requested_by,
        })
        return {
            "success": True,
            "status": "pending",
            "changeId": change_id,
            "objective": objective,
            "note": "objective 模式已提交 "
                    "46号审批(不直接生效——"
                    "人工终审轨)",
            "submittedAt": ts(),
        }

    async def objective_apply(
            self, change_id: int,
            applied_by: str = "admin"
            ) -> dict:
        """objective 模式生效(终审模——
        46号 reviewedBy 留痕; 不受开关
        影响·人工铁律)

        Raises:
            KeyError: 变更不存在
            ValueError: 未裁决/不匹配/已生效
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
        rec = await self.repo.get_threshold(
            "objective")
        if not rec \
                or rec.get("changeId") \
                != int(change_id):
            raise ValueError(
                f"无 changeId={change_id} 的"
                f"待生效模式申请")
        if rec.get("status") != "pending":
            raise ValueError(
                f"模式申请已 "
                f"{rec.get('status')}"
                f"(勿重复生效)")

        rec.update({
            "status": "applied",
            "appliedBy": applied_by,
            "updatedAt": ts()})
        await self.repo.save_threshold(rec)
        await self._track("objective_apply", {
            "changeId": int(change_id),
            "objective": (rec.get("config")
                          or {}).get(
                              "objective"),
            "appliedBy": applied_by,
        })
        return {
            "success": True,
            "status": "applied",
            "changeId": int(change_id),
            "objective": (rec.get("config")
                          or {}).get(
                              "objective"),
            "appliedBy": applied_by,
            "note": "objective 模式已生效"
                    "(46号审批+人工终审"
                    "双模完成)",
            "appliedAt": ts(),
        }

    async def get_active_objective(self) -> str:
        """生效 objective 读取(fail-soft
        回落默认 stability)"""
        try:
            rec = await self.repo.get_threshold(
                "objective")
            if rec \
                    and rec.get("status") \
                    == "applied":
                obj = (rec.get("config")
                       or {}).get("objective")
                from services.av62_registry import (
                    OBJECTIVE_VALUES,
                )
                if obj in OBJECTIVE_VALUES:
                    return str(obj)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "av62_objective_failsoft: %s",
                exc)
        return DEFAULT_OBJECTIVE

    # ============================================================
    # 观测面
    # ============================================================

    async def get_assessment(self,
                             assess_id: int
                             ) -> dict:
        """评估详情(评估记录+归因链——
        观测面)

        Raises:
            KeyError: 评估不存在
        """
        record = await \
            self.repo.get_assessment(
                int(assess_id))
        if not record:
            raise KeyError(
                f"评估 {assess_id} 不存在")
        return {
            "success": True,
            "assessment": record,
            "note": "评估记录——贡献度+因子"
                    "快照+置信度+归因链",
        }

    async def list_assessments(self,
                              asset_id: int = None,
                              limit: int = 100
                              ) -> dict:
        """评估列表(观测面)"""
        records = await \
            self.repo.list_assessments(
                asset_id=asset_id,
                limit=int(limit or 100))
        return {
            "success": True,
            "total": len(records),
            "assessments": records,
            "note": "评估列表——重估版本链"
                    "(assessId 倒序)",
        }

    # --------------------------------------------------------
    # 内部
    # --------------------------------------------------------

    async def _next_version(self,
                            asset_id: int) -> int:
        """资产评估版本链(重估递增)"""
        latest = await self.repo \
            .list_assessments(
                asset_id=asset_id, limit=1)
        return int((latest[0].get("version")
                    if latest else 0)) + 1

    async def _track(self, event_type: str,
                     detail: dict) -> None:
        try:
            event_id = await \
                self.repo.next_event_id()
            await self.repo.add_event({
                "eventId": event_id,
                "assetId": int(
                    detail.get("assetId")
                    or 0),
                "eventType": event_type,
                "detail": detail,
                "createdAt": ts(),
            })
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "av62_track_failed %s: %s",
                event_type, exc)
