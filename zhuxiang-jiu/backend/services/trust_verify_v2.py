"""45号·P7 真伪鉴别引擎 v2
(自适应权重融合 + 时序基线特征 + 风险标签 + 可解释归因
+ 灰色地带 LLM 推理)

参照(docs/45号_Value-UEBA真伪鉴别引擎指南.md)三层混合架构:
    规则兜底(既有三道关) + 模型判别(融合决策) + 大模型推理
    (灰色地带按需调用)。

对照 v1 管线(trust_radar_service.verify_pipeline)的升级点:
    ① 融合升级: min(三分) → 按行为子类型的自适应权重融合
       (模板 §一 WEIGHT_PROFILES 口径):
           donation  重跨源(防虚假票据)
           review    重内容鉴别(防水军团伙——本仓以证据
                     内容质量代理图结构风险)
           volunteer 重跨源+意图
           repair    重时序(防突击刷分)
    ② 时序特征: behavior_burst_ratio(模板 §二 特征工程)
       ——近 7 天事件频次 / 近 90 天日均频次, >5 刷分嫌疑
    ③ 风险标签: risk_tags 输出(模板 verify 返回结构)
    ④ 可解释归因: 每次打分生成结构化组件分 + 中文归因
       (模板"可解释性前置"——支撑申诉与监管审计)
    ⑤ 灰色地带 LLM: 融合分 ∈ (0.3, 0.8) 才调用 LLM 意图
       推理并重融合(模板"LLM 按需调用"——控成本; mock 态
       不触发保持确定性)

工程铁律:
    - v1 零影响: verify_pipeline 不动; v2 由调用方显式
      verify_mode="v2" 激活(默认 v1)
    - 灰色地带给中等分(模板工程忠告): 不武断真假,
      verified 口径不变(≥0.7 且跨源过), 中间地带靠
      risk_tags 表达不确定性
    - 组件分复用 v1 三道关(multimodal/cross_source/intent)
      ——规则兜底层共享, 融合层升级
"""

import logging
import re
from datetime import UTC, datetime, timedelta

logger = logging.getLogger(__name__)

# 灰色地带区间(模板: 0.3 < preliminary < 0.8 才调用 LLM)
GRAY_ZONE = (0.3, 0.8)

# 融合分阈值(与 v1 VERIFY_THRESHOLD 同口径)
VERIFY_THRESHOLD = 0.7

# 47号 P3 信任先验融合权重(计划 §六 ①: 画像信任度作为
# 第 5 分量参与融合——prior×w + 组件融合×(1−w))
TRUST_PRIOR_WEIGHT = 0.3

# ============================================================
# 自适应权重档案(模板 §一 WEIGHT_PROFILES; graph 维度以
# 证据内容质量代理——摆拍/刷单识别本质是内容-团伙特征,
# GNN 图谱轨列入外部待办)
# ============================================================

WEIGHT_PROFILES = {
    # 捐赠类: 重跨源验证(防虚假票据)
    "donation": {"content": 0.3, "temporal": 0.2,
                 "cross_source": 0.4, "intent": 0.1},
    # 评价/平台互动类: 重内容鉴别(防水军团伙)
    "review": {"content": 0.4, "temporal": 0.3,
               "cross_source": 0.2, "intent": 0.1},
    # 志愿/公益类: 重跨源+意图
    "volunteer": {"content": 0.2, "temporal": 0.3,
                  "cross_source": 0.3, "intent": 0.2},
    # 修复行为: 重时序(防突击刷分)
    "repair_action": {"content": 0.2, "temporal": 0.4,
                      "cross_source": 0.3, "intent": 0.1},
}

# 修复行为 kind → 修复档案(P2 REPAIR_KIND_LABELS 键集)
_REPAIR_KINDS = (
    "legal_restitution", "legal_education",
    "regulatory_rectification", "compliance_training",
    "contract_fulfillment", "compensation_paid",
    "public_apology", "platform_rectification",
    "community_service", "charity_donation",
)

# 存证因子 → 行为档案(L2 伦理互动=review / L3 贡献=volunteer)
_FACTOR_PROFILES = {
    "platform_conduct": "review",
    "community_standing": "review",
    "ethics_evidence": "review",
    "contribution_net": "volunteer",
    "impact_radius": "volunteer",
    "longtail_good": "volunteer",
}


def behavior_profile_of(kind: str = "",
                       factor: str = "") -> str:
    """行为子类型 → 融合权重档案

    优先级: kind(修复/捐赠) > factor(存证因子) > review 兜底
    (模板: WEIGHT_PROFILES.get(sub_type, review))
    """
    if kind in _REPAIR_KINDS:
        return ("donation" if kind == "charity_donation"
                else "repair_action")
    if kind == "deposit" and factor in _FACTOR_PROFILES:
        return _FACTOR_PROFILES[factor]
    if factor in _FACTOR_PROFILES:
        return _FACTOR_PROFILES[factor]
    return "review"


# ============================================================
# 时序基线特征(模板 §二: behavior_burst_ratio)
# ============================================================

def burst_ratio(event_timestamps: list,
               now: datetime = None) -> tuple:
    """行为突增比: 近 7 天频次 / 近 90 天日均频次

    模板口径: >5 且无合理触发事件 → 刷分嫌疑。
    观察窗不足(总观察 <14 天)无基线 → ratio=1.0(不判)。

    Args:
        event_timestamps: 历史行为事件时间戳列表
    Returns:
        (ratio, note)
    """
    now = now or datetime.now(UTC)

    def _parse(t):
        try:
            dt = datetime.fromisoformat(str(t))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=UTC)
            return dt
        except (TypeError, ValueError):
            return None

    dts = [d for d in (_parse(t) for t in
                       (event_timestamps or []))
           if d is not None]
    if not dts:
        return 1.0, "无历史事件(时序基线不判)"
    first = min(dts)
    observed_days = max(
        (now - first).total_seconds() / 86400.0, 1.0)
    if observed_days < 14:
        return 1.0, f"观察窗不足{observed_days:.0f}天(基线不判)"
    recent_7d = sum(1 for d in dts if now - d
                    <= timedelta(days=7))
    daily_avg = len(dts) / observed_days
    if daily_avg <= 0:
        return 1.0, ""
    ratio = round(recent_7d / (daily_avg * 7.0), 2)
    note = (f"近7天{recent_7d}次 vs 日均"
            f"{daily_avg:.2f}×7(突增比 {ratio})")
    return ratio, note


def temporal_score_of(ratio: float) -> tuple:
    """时序一致性分(1 - 风险)

    burst 1.0 → 0 风险(满分); 5.0 → 风险顶格(0 分)。
    线性映射: risk = clamp((ratio-1)/4, 0, 1)。
    """
    ratio = float(ratio or 1.0)
    risk = max(0.0, min(1.0, (ratio - 1.0) / 4.0))
    score = round(1.0 - risk, 2)
    note = (f"时序一致性 {score}"
            f"(突增比 {ratio}, 阈值 5)")
    return score, note


# ============================================================
# 融合决策 + 风险标签 + 归因(模板第 4/5 层)
# ============================================================

def fuse_scores(components: dict,
                weights: dict) -> float:
    """加权融合(权重和归一化防御——非标配置不炸)"""
    total_w = sum(weights.values())
    if total_w <= 0:
        return round(sum(components.values())
                     / max(len(components), 1), 4)
    return round(sum(
        components.get(k, 0.0) * (w / total_w)
        for k, w in weights.items()), 4)


def build_risk_tags(components: dict,
                    cross_pass: bool,
                    burst: float) -> list:
    """风险标签(模板 verify 返回 tags 口径)"""
    tags = []
    if not cross_pass:
        tags.append("single_source")
    if components.get("content", 1.0) < 0.5:
        tags.append("content_quality_low")
    if components.get("temporal", 1.0) < 0.5:
        tags.append("behavior_burst")
    if components.get("intent", 1.0) < 0.5:
        tags.append("performative_goodness")
    return tags


def build_attribution(score: float, components: dict,
                      weights: dict,
                      risk_tags: list) -> str:
    """中文归因(模板"可解释性前置"——支撑申诉与审计)"""
    parts = [f"融合分 {score}"]
    detail = " + ".join(
        f"{_PROFILE_LABELS.get(k, k)}"
        f"{components.get(k, 0.0)}×{weights.get(k, 0.0)}"
        for k in ("content", "temporal", "cross_source",
                  "intent"))
    parts.append(detail)
    if risk_tags:
        parts.append("风险标签: "
                     + ", ".join(_TAG_LABELS.get(t, t)
                                 for t in risk_tags))
    else:
        parts.append("无风险标签")
    return "; ".join(parts)


_PROFILE_LABELS = {
    "content": "内容鉴别", "temporal": "时序一致",
    "cross_source": "跨源验证", "intent": "意图推理",
}

_TAG_LABELS = {
    "single_source": "单源孤证",
    "content_quality_low": "证据质量低",
    "behavior_burst": "行为突增(疑似刷分)",
    "performative_goodness": "表演式向善",
}


# ============================================================
# 灰色地带 LLM 推理(模板第 3d 层: 按需调用控成本)
# ============================================================

def _gray_zone_llm(summary: str) -> tuple:
    """灰色地带 LLM 意图推理(real 轨; 失败回退 None)

    Returns:
        (intent_score 或 None, note)
    """
    text = (summary or "").strip()
    if not text:
        return None, ""
    try:
        from services.llm_client import (
            provider_client, llm_enabled,
        )
        if not llm_enabled():
            return None, ""
        reply = provider_client().chat(
            system="你是行为真伪鉴别员。综合行为描述与"
                   "已知风险信号, 推断该行为是真实向善还是"
                   "表演式/刷分行为。只回答 JSON: "
                   '{"score": 0到1, "reason": "一句话"}',
            user=f"行为描述: {text}")
        if not reply:
            return None, ""
        import json
        data = json.loads(
            re.search(r"\{.*\}", reply, re.S).group())
        return (float(data.get("score") or 0.5),
                f"灰色地带LLM推理: "
                f"{data.get('reason', '')}")
    except Exception as exc:
        logger.warning("trust45_verify_v2_llm_skip: %s", exc)
        return None, ""


# ============================================================
# 主入口: 验真管线 v2
# ============================================================

def _blend_trust_prior(fusion: float,
                       trust_prior: float) -> float:
    """47号 P3 信任先验融合(计划 §六 ①)

    preliminary = prior×w + 组件融合×(1−w)  (w=0.3)

    折扣是乘性不是禁入: restricted(0.3)+满分证据 → 0.79
    仍可过验真(风险角色靠证据质量爬回来); trusted 天然
    高起点(信任加速)。
    """
    prior = max(0.0, min(1.0, float(trust_prior)))
    return round(prior * TRUST_PRIOR_WEIGHT
                 + float(fusion) * (1 - TRUST_PRIOR_WEIGHT), 4)


def verify_pipeline_v2(kind: str, evidence: str,
                       sources: list, summary: str = "",
                       event_timestamps: list = None,
                       factor: str = "",
                       trust_prior: float = None) -> dict:
    """真伪鉴别引擎 v2(自适应权重融合)

    组件分复用 v1 三道关(规则兜底层共享):
        content      ← multimodal_check(证据内容质量)
        cross_source ← cross_source_check(源独立性)
        intent       ← intent_check(意图推理)
        temporal     ← burst_ratio(时序基线, 新增)

    融合: 按行为子类型自适应权重(模板 §一)。
    灰色地带(0.3, 0.8): real 轨 LLM 重推理意图分量
    (mock 态不触发, 确定性可测)。

    47号 P3 信任先验(计划 §六 ①, 显式激活零影响):
        trust_prior != None 时画像信任度作为第 5 分量
        参与融合(起点折扣/信任加速); 默认 None 完全
        走既有融合——P7 既有调用与断言零改动。

    Returns:
        {verified, confidence, riskTags, components,
         fusionWeights, burstRatio, llmUsed, attribution,
         checks, fingerprint, trustPrior}
    """
    from services.trust_radar_service import (
        multimodal_check, cross_source_check, intent_check,
        _evidence_fingerprint, radar_mode,
    )

    # --- 组件分提取(规则兜底层=v1 三道关) ---
    content_score, content_note = multimodal_check(
        kind, evidence)
    cross_pass, cross_score, cross_note = \
        cross_source_check(sources)
    intent_score, intent_note = intent_check(summary)

    # --- 时序特征(事件流基线) ---
    burst, burst_note = burst_ratio(event_timestamps)
    temporal_score, temporal_note = \
        temporal_score_of(burst)

    profile = behavior_profile_of(kind, factor)
    weights = WEIGHT_PROFILES[profile]
    components = {
        "content": content_score,
        "temporal": temporal_score,
        "cross_source": cross_score,
        "intent": intent_score,
    }

    # --- 初步融合 ---
    preliminary = fuse_scores(components, weights)

    # --- 47号 P3 信任先验融合(显式激活; None 零影响) ---
    prior_used = trust_prior is not None
    if prior_used:
        preliminary = _blend_trust_prior(
            preliminary, trust_prior)

    # --- 灰色地带 LLM(模板: 0.3 < score < 0.8 才调用) ---
    llm_used = False
    if GRAY_ZONE[0] < preliminary < GRAY_ZONE[1] \
            and radar_mode() == "real":
        llm_score, llm_note = _gray_zone_llm(summary)
        if llm_score is not None:
            components["intent"] = llm_score
            intent_note = (f"{intent_note}; {llm_note}"
                           if llm_note else intent_note)
            llm_used = True
            preliminary = fuse_scores(components, weights)
            if prior_used:
                preliminary = _blend_trust_prior(
                    preliminary, trust_prior)

    score = preliminary
    risk_tags = build_risk_tags(
        components, cross_pass, burst)
    attribution = build_attribution(
        score, components, weights, risk_tags)
    if prior_used:
        prior_clamped = max(0.0, min(
            1.0, float(trust_prior)))
        attribution += (f"; 信任先验 {prior_clamped}×"
                        f"{TRUST_PRIOR_WEIGHT} 融合(47号P3)")
    verified = (score >= VERIFY_THRESHOLD
                and cross_pass)

    return {
        "verified": verified,
        "confidence": score,
        "engine": "v2",
        "profile": profile,
        "riskTags": risk_tags,
        "components": components,
        "fusionWeights": dict(weights),
        "burstRatio": burst,
        "burstNote": burst_note,
        "llmUsed": llm_used,
        "attribution": attribution,
        "temporalNote": temporal_note,
        "checks": [
            {"stage": "multimodal", "pass":
             content_score >= 0.7,
             "confidence": content_score,
             "note": content_note},
            {"stage": "cross_source", "pass": cross_pass,
             "confidence": cross_score,
             "note": cross_note},
            {"stage": "intent", "pass":
             components["intent"] >= 0.7,
             "confidence": components["intent"],
             "note": intent_note},
            {"stage": "temporal", "pass":
             temporal_score >= 0.5,
             "confidence": temporal_score,
             "note": temporal_note},
        ],
        "fingerprint": _evidence_fingerprint(evidence),
        "trustPrior": {
            "applied": prior_used,
            "value": (round(max(0.0, min(
                1.0, float(trust_prior))), 4)
                if prior_used else None),
            "weight": TRUST_PRIOR_WEIGHT,
        },
    }
