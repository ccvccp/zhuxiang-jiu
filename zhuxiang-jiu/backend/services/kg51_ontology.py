"""51号·小竹可信知识图谱 本体注册表(kg51_ontology)

计划(docs/51号_小竹可信知识图谱实施计划.md §四):
    SOP《Value-UEBA 网站知识图谱构建》六阶流程之阶段1(本体设计)
    本站落地——OWL/RDF-S → Python 注册表(50号 VOICE_RULES
    /49号 TOOL_REGISTRY 同范式: 可断言/可测试/启动自检)。

本体构成(计划 §三):
    9 实体: Member / VoiceBehaviorEvent / Evidence / TrustFactor
            / PolicyClause / Product / RepairAction / Institution
            / VoiceAnswer
    9 关系: performed_by / attested_by / verified_with
            / contributes_to_credit / governed_by / triggers_repair
            / grounds_on / references / operated_by

红线(SOP §一 红线原则 + 本站强化):
    - 图谱不存原始敏感数据——实体标识一律 digest,
      属性白名单 allowedAttrs, PII 禁入基线全实体强制
    - attested_by minCard=1: 无证据链即 unverified,
      不参与信值计算(证据链铁律的结构化表达)
    - 隐私成本与敏感度层级严格对齐(L0 公开零成本——
      grounding 零成本红线; L3 最高 0.02)

启动自检 _validate_ontology():
    - 9 实体/9 关系数量断言
    - idPattern 唯一且含占位符
    - 敏感度分级合法 + 隐私成本对齐
    - PII 禁入基线覆盖全部实体 + 白黑名单互斥
    - 关系 domain/range 全部指向已注册实体
    - attested_by 强制下限(证据链)
    - 信值计算字段覆盖 ≥90%(动态对照 45号九因子
      /50号 14 行为——懒惰 import, 与 voice50_rules
      自检同范式)
"""

import logging
import os

logger = logging.getLogger("kg51_ontology")

# 总开关(默认 off——P0 无数据面; 采集/查询语义随各期完整化:
# off=采集停+查询降级空态, fail-soft 直通)
DEFAULT_MODE = "off"


def current_mode() -> str:
    """模块总开关(动态读取——测试可切换)"""
    return os.environ.get("KG_MODE") or DEFAULT_MODE


# 敏感度分级与默认隐私成本(查询面织入 49号 check_and_spend;
# 预算与信值等级零挂钩——公平性红线继承)
SENSITIVITY_TIERS = {
    "L0": 0.0,     # 公开零成本(grounding 零成本红线)
    "L1": 0.005,
    "L2": 0.01,
    "L3": 0.02,    # 主体类最高
}

# PII 禁入基线(48号 mask_pii 覆盖的敏感类别 + 实名)——
# 每个实体的 forbiddenAttrs 必须 ⊇ 本基线(digest-only 铁律)
PII_FORBIDDEN_BASE = ("phone", "idCard", "bankCard", "realName")

# 数据源三分级(计划 §五 阶段2: 系统源/权威源/用户自报源)
SOURCE_TYPE_VALUES = ("authority", "system", "user")

# 冲突解决优先级(SOP 阶段4: 权威源 > 系统源 > 用户自报源)
SOURCE_PRIORITY = {"authority": 3, "system": 2, "user": 1}

# 三元组状态(unverified 物理隔离于计分路径——P1 断言)
TRIPLE_STATUS_VALUES = ("verified", "unverified", "retired")

# 证据链铁律: 无 evidence_bundle 的三元组一律 unverified,
# 不参与信值计算(SOP §三-1)
EVIDENCE_REQUIRED_FOR_VERIFIED = True


# ============================================================
# 本体注册表(9 实体/9 关系——计划 §三-1/§三-2)
# ============================================================

ONTOLOGY_REGISTRY = {
    "entities": {
        "Member": {
            "label": "会员(信值主体)",
            "idPattern": "member:sha256:{digest}",
            "sensitivity": "L3",
            "privacyCost": 0.02,
            "allowedAttrs": ["digest", "trustTier",
                             "memberSinceDay"],
            "forbiddenAttrs": ["phone", "idCard",
                               "bankCard", "realName"],
            "ingestPhase": "P1",
        },
        "VoiceBehaviorEvent": {
            "label": "语音行为事件(50号台账)",
            "idPattern": "ev:voice50:{evId}",
            "sensitivity": "L2",
            "privacyCost": 0.01,
            "allowedAttrs": ["behaviorKey", "layer", "value",
                             "status"],
            "forbiddenAttrs": ["phone", "idCard",
                               "bankCard", "realName"],
            "ingestPhase": "P1",
        },
        "Evidence": {
            "label": "证据(哈希锚定)",
            "idPattern": "evid:sha256:{evSha}",
            "sensitivity": "L2",
            "privacyCost": 0.01,
            "allowedAttrs": ["evSha", "bcHash", "kind",
                             "sourceRef"],
            "forbiddenAttrs": ["phone", "idCard",
                               "bankCard", "realName"],
            "ingestPhase": "P1",
        },
        "TrustFactor": {
            "label": "信值因子(45号九因子只读引用)",
            "idPattern": "factor:trust45:{factorKey}",
            "sensitivity": "L1",
            "privacyCost": 0.005,
            "allowedAttrs": ["factorKey", "layer", "weight"],
            "forbiddenAttrs": ["phone", "idCard",
                               "bankCard", "realName"],
            "ingestPhase": "P1",
        },
        "PolicyClause": {
            "label": "政策条款(18号条款协议)",
            "idPattern": "clause:agr18:{clauseId}",
            "sensitivity": "L0",
            "privacyCost": 0.0,
            "allowedAttrs": ["clauseId", "title", "version"],
            "forbiddenAttrs": ["phone", "idCard",
                               "bankCard", "realName"],
            "ingestPhase": "P2",
        },
        "Product": {
            "label": "产品(01号产品展示)",
            "idPattern": "product:sku:{productId}",
            "sensitivity": "L0",
            "privacyCost": 0.0,
            "allowedAttrs": ["productId", "name", "sku"],
            "forbiddenAttrs": ["phone", "idCard",
                               "bankCard", "realName"],
            "ingestPhase": "P2",
        },
        "VoiceAnswer": {
            "label": "问答锚定节点(48号轮次)",
            "idPattern": "answer:voice48:{turnId}",
            "sensitivity": "L0",
            "privacyCost": 0.0,
            "allowedAttrs": ["turnId", "intent", "confidence"],
            "forbiddenAttrs": ["phone", "idCard",
                               "bankCard", "realName"],
            "ingestPhase": "P2",
        },
        "RepairAction": {
            "label": "修复动作(45号修复通道)",
            "idPattern": "repair:trust45:{repairId}",
            "sensitivity": "L1",
            "privacyCost": 0.005,
            "allowedAttrs": ["repairId", "channel", "status"],
            "forbiddenAttrs": ["phone", "idCard",
                               "bankCard", "realName"],
            "ingestPhase": "P3",
        },
        "Institution": {
            "label": "合作机构(16/21号)",
            "idPattern": "org:partner:{orgId}",
            "sensitivity": "L1",
            "privacyCost": 0.005,
            "allowedAttrs": ["orgId", "type", "region"],
            "forbiddenAttrs": ["phone", "idCard",
                               "bankCard", "realName"],
            "ingestPhase": "P4",
        },
    },
    "relations": {
        "performed_by": {
            "label": "事件归属",
            "domain": ["VoiceBehaviorEvent"],
            "range": ["Member"],
            "minCard": 1, "maxCard": 1,
        },
        "attested_by": {
            "label": "证据链绑定",
            "domain": ["VoiceBehaviorEvent"],
            "range": ["Evidence"],
            "minCard": 1, "maxCard": 8,
            # minCard=1: 无证据即 unverified——强制下限
        },
        "verified_with": {
            "label": "双源互证(47号互证对)",
            "domain": ["Evidence"],
            "range": ["Evidence"],
            "minCard": 0, "maxCard": 4,
        },
        "contributes_to_credit": {
            "label": "信值贡献映射(50号→45号)",
            "domain": ["VoiceBehaviorEvent"],
            "range": ["TrustFactor"],
            "minCard": 0, "maxCard": 1,
        },
        "governed_by": {
            "label": "条款治理(18号)",
            "domain": ["VoiceBehaviorEvent", "PolicyClause"],
            "range": ["PolicyClause"],
            "minCard": 0, "maxCard": 4,
        },
        "triggers_repair": {
            "label": "触发修复(45号)",
            "domain": ["VoiceBehaviorEvent"],
            "range": ["RepairAction"],
            "minCard": 0, "maxCard": 2,
        },
        "grounds_on": {
            "label": "问答锚定(小竹 grounding)",
            "domain": ["VoiceAnswer"],
            "range": ["Product", "PolicyClause"],
            "minCard": 0, "maxCard": 4,
        },
        "references": {
            "label": "条款引用",
            "domain": ["Product"],
            "range": ["PolicyClause"],
            "minCard": 0, "maxCard": 8,
        },
        "operated_by": {
            "label": "验证机构(P4 接入)",
            "domain": ["Evidence"],
            "range": ["Institution"],
            "minCard": 0, "maxCard": 2,
        },
    },
}


def ontology_view() -> dict:
    """本体注册表视图(管理端/自描述)"""
    entities = ONTOLOGY_REGISTRY["entities"]
    relations = ONTOLOGY_REGISTRY["relations"]
    by_tier: dict = {}
    for meta in entities.values():
        tier = meta["sensitivity"]
        by_tier[tier] = by_tier.get(tier, 0) + 1
    return {
        "module": "kg51",
        "mode": current_mode(),
        "entityCount": len(entities),
        "relationCount": len(relations),
        "bySensitivity": by_tier,
        "entities": {k: {
            "label": v["label"],
            "idPattern": v["idPattern"],
            "sensitivity": v["sensitivity"],
            "privacyCost": v["privacyCost"],
            "allowedAttrs": v["allowedAttrs"],
            "forbiddenAttrs": v["forbiddenAttrs"],
            "ingestPhase": v["ingestPhase"],
        } for k, v in entities.items()},
        "relations": {k: {
            "label": v["label"],
            "domain": v["domain"],
            "range": v["range"],
            "minCard": v["minCard"],
            "maxCard": v["maxCard"],
        } for k, v in relations.items()},
        "sensitivityTiers": SENSITIVITY_TIERS,
        "sourceTypes": {
            "values": list(SOURCE_TYPE_VALUES),
            "priority": SOURCE_PRIORITY,
        },
        "tripleStatusValues": list(TRIPLE_STATUS_VALUES),
        "piiForbiddenBase": list(PII_FORBIDDEN_BASE),
        "evidenceRule": "无 evidence_bundle 的三元组一律 "
                        "unverified, 不参与信值计算",
        "coverage": coverage_report(),
    }


def coverage_report() -> dict:
    """信值计算字段覆盖报告(QC: ≥90%——计划 §四)

    动态对照 45号九因子/50号 14 行为: 本体容量
    (实体属性白名单)须能承载全量计算字段。
    """
    required = _required_fields()
    entities = ONTOLOGY_REGISTRY["entities"]
    missing = []
    for f in required:
        meta = entities.get(f["entity"]) or {}
        if f["attr"] not in (meta.get("allowedAttrs") or []):
            missing.append(f["field"])
    total = len(required)
    covered = total - len(missing)
    ratio = round(covered / total, 4) if total else 1.0
    return {
        "total": total,
        "covered": covered,
        "missing": missing,
        "ratio": ratio,
        "qc": ">=0.9",
    }


def _required_fields() -> list:
    """信值计算所需字段清单(45号因子 + 50号行为 + 主体/证据)"""
    from services.trust_scoring_service import TrustValueScorer
    from services.xiaozhu_voice50_rules import VOICE_RULES
    fields = []
    for factor in TrustValueScorer.LAYER_OF:
        fields.append({"field": f"factor:{factor}",
                       "entity": "TrustFactor",
                       "attr": "factorKey"})
    for behavior in VOICE_RULES:
        fields.append({"field": f"behavior:{behavior}",
                       "entity": "VoiceBehaviorEvent",
                       "attr": "behaviorKey"})
    fields.append({"field": "subject:digest",
                   "entity": "Member", "attr": "digest"})
    fields.append({"field": "evidence:evSha",
                   "entity": "Evidence", "attr": "evSha"})
    fields.append({"field": "evidence:bcHash",
                   "entity": "Evidence", "attr": "bcHash"})
    return fields


def _validate_ontology() -> None:
    """启动自检: 本体结构完整性 + 覆盖率红线

    Raises:
        RuntimeError: 任一断言失败(启动即阻断——宪法级)
    """
    entities = ONTOLOGY_REGISTRY["entities"]
    relations = ONTOLOGY_REGISTRY["relations"]

    # ① 数量断言(9 实体/9 关系——计划 §三)
    if len(entities) != 9:
        raise RuntimeError(
            f"kg51 本体不一致: 实体数 {len(entities)} != 9")
    if len(relations) != 9:
        raise RuntimeError(
            f"kg51 本体不一致: 关系数 {len(relations)} != 9")

    # ② 实体结构断言
    patterns = set()
    for name, meta in entities.items():
        pattern = meta.get("idPattern") or ""
        if "{" not in pattern or "}" not in pattern:
            raise RuntimeError(
                f"kg51 本体不一致: 实体 {name} idPattern "
                f"缺占位符({pattern})")
        if pattern in patterns:
            raise RuntimeError(
                f"kg51 本体不一致: idPattern 重复({pattern})")
        patterns.add(pattern)

        tier = meta.get("sensitivity")
        if tier not in SENSITIVITY_TIERS:
            raise RuntimeError(
                f"kg51 本体不一致: 实体 {name} 敏感度非法"
                f"({tier})")
        cost = meta.get("privacyCost")
        if cost != SENSITIVITY_TIERS[tier]:
            raise RuntimeError(
                f"kg51 本体不一致: 实体 {name} 隐私成本 "
                f"{cost} 与层级 {tier}"
                f"({SENSITIVITY_TIERS[tier]})不对齐")

        allowed = meta.get("allowedAttrs") or []
        forbidden = meta.get("forbiddenAttrs") or []
        if not allowed:
            raise RuntimeError(
                f"kg51 本体不一致: 实体 {name} 属性白名单为空")
        overlap = set(allowed) & set(forbidden)
        if overlap:
            raise RuntimeError(
                f"kg51 本体不一致: 实体 {name} 白黑名单重叠"
                f"({sorted(overlap)})")
        # PII 禁入基线(全实体强制——digest-only 铁律)
        pii_gap = set(PII_FORBIDDEN_BASE) - set(forbidden)
        if pii_gap:
            raise RuntimeError(
                f"kg51 本体不一致: 实体 {name} PII 禁入基线"
                f"缺失({sorted(pii_gap)})")

    # ③ 关系结构断言(domain/range 指向已注册实体)
    for name, meta in relations.items():
        for side in ("domain", "range"):
            types = meta.get(side) or []
            if not types:
                raise RuntimeError(
                    f"kg51 本体不一致: 关系 {name} {side} 为空")
            for t in types:
                if t not in entities:
                    raise RuntimeError(
                        f"kg51 本体不一致: 关系 {name} {side} "
                        f"类型 {t} 未注册")
        lo, hi = meta.get("minCard"), meta.get("maxCard")
        if not isinstance(lo, int) or lo < 0:
            raise RuntimeError(
                f"kg51 本体不一致: 关系 {name} minCard 非法")
        if hi is not None and lo > hi:
            raise RuntimeError(
                f"kg51 本体不一致: 关系 {name} minCard({lo}) "
                f"> maxCard({hi})")

    # ④ 证据链强制下限(无证据即 unverified 的结构化表达)
    attested = relations.get("attested_by") or {}
    if (attested.get("minCard") or 0) < 1:
        raise RuntimeError(
            "kg51 本体不一致: attested_by minCard 必须 ≥1 "
            "(证据链铁律——无证据即 unverified)")

    # ⑤ 覆盖率红线(≥90% 信值计算所需字段)
    report = coverage_report()
    if report["ratio"] < 0.9:
        raise RuntimeError(
            f"kg51 本体不一致: 计算字段覆盖率 "
            f"{report['ratio']} < 0.9 "
            f"(缺失: {report['missing']})")


_validate_ontology()
