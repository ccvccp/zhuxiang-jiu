"""62号·AI智能无形资产估值 信任要素注册表
(av62_registry)

计划(docs/62号_AI智能无形资产估值模型实施计划.md
§3.1/§七 P0):
    TRUST_ELEMENTS 分角色×资产域
    封闭注册(三角色×九资产域+负资产域)
    ——信任要素结构化注册(51号图谱
    语义——封闭注册表范式)

要素结构七字段:
    elementId/role/domain/label/
    weight 基准权重/evidenceSchema
    证据字段/status

铁律(计划 §1.3/§八):
    - 负资产域(risk): 处罚记录/
      投诉率超阈值——时效衰减
      不适用(不可洗白)
    - 估值绑定角色行为与场景上下文
      (同一资产在不同合规主体手中
      信值不同)
    - 证据采集纯读取(44/45/57/58号
      既有数据)——不新增采集面

启动自检 _validate_registry()(RuntimeError
宪法级): 角色域/资产域/负资产域/要素
结构/证据字段合法性全覆盖。
"""

import logging
import os

logger = logging.getLogger("av62_registry")

MODEL_VERSION = "v1-av62-registry"

DEFAULT_MODE = "off"

MODE_VALUES = ("off", "shadow", "assist")

# ============================================================
# 角色域(三——计划 §3.1)
# ============================================================

ROLE_DOMAINS = (
    "enterprise",     # 企业/事业
    "organization",  # 团体
    "personal",       # 个人
)

# 资产域(九——正资产域×8+负资产域 risk)
DOMAINS = (
    "compliance",    # 合规
    "knowledge",     # 知识
    "behavior",      # 行为
    "social",        # 社会资本
    "culture",       # 文化
    "capability",    # 能力
    "reputation",    # 声誉
    "growth",        # 成长
)

# 负资产域(铁律: 时效衰减不适用)
RISK_DOMAIN = "risk"

ALL_DOMAINS = DOMAINS + (RISK_DOMAIN,)

# 证据字段域(封闭——登记校验依据)
EVIDENCE_FIELDS = (
    "licenseCount",      # 资质证照数
    "auditResults",      # 审计结果
    "penaltyRecords",    # 处罚记录(risk)
    "esgDisclosure",     # ESG 披露
    "sopDocs",           # SOP 文档数
    "techContribs",      # 技术贡献
    "codeCommits",       # 代码提交
    "operationCompliance",  # 操作规范率
    "collabLatency",     # 协作响应时效
    "dataSharing",       # 数据共享意愿
    "memberActivity",    # 成员活跃度
    "eventCompliance",   # 活动合规率
    "externalReviews",   # 外部评价
    "valueAlignment",    # 价值观一致性
    "transparency",      # 透明度评分
    "skillCerts",        # 技能认证
    "deliveryQuality",   # 交付质量
    "knowledgeSharing",  # 知识分享频次
    "peerReviews",       # 同行评价
    "complaintRate",     # 投诉率(risk)
    "privacyBehavior",   # 隐私保护行为
    "learningInvest",    # 学习投入
    "errorCorrection",   # 纠错意愿
    "crossAdapt",        # 跨域适应
)

# ============================================================
# TRUST_ELEMENTS 封闭注册(三角色×资产域)
# ============================================================

TRUST_ELEMENTS = {
    # ---- enterprise 企业/事业 ----
    ("enterprise", "compliance"): {
        "label": "企业合规资产",
        "weight": 0.25,
        "evidenceSchema": [
            "licenseCount", "auditResults",
            "esgDisclosure"],
        "note": "资质证照/审计结果/ESG",
    },
    ("enterprise", "knowledge"): {
        "label": "企业知识资产",
        "weight": 0.20,
        "evidenceSchema": [
            "sopDocs", "techContribs",
            "codeCommits"],
        "note": "SOP 文档/技术贡献/提交",
    },
    ("enterprise", "behavior"): {
        "label": "企业行为资产",
        "weight": 0.15,
        "evidenceSchema": [
            "operationCompliance",
            "collabLatency", "dataSharing"],
        "note": "操作规范/协作/共享",
    },
    # ---- organization 团体 ----
    ("organization", "social"): {
        "label": "团体社会资本",
        "weight": 0.30,
        "evidenceSchema": [
            "memberActivity",
            "eventCompliance",
            "externalReviews"],
        "note": "活跃度/合规率/评价",
    },
    ("organization", "culture"): {
        "label": "团体文化资产",
        "weight": 0.20,
        "evidenceSchema": [
            "valueAlignment",
            "transparency"],
        "note": "价值观一致性/透明度",
    },
    ("organization", "compliance"): {
        "label": "团体合规资产",
        "weight": 0.20,
        "evidenceSchema": [
            "eventCompliance",
            "auditResults"],
        "note": "活动合规/审计",
    },
    # ---- personal 个人 ----
    ("personal", "capability"): {
        "label": "个人能力资产",
        "weight": 0.25,
        "evidenceSchema": [
            "skillCerts",
            "deliveryQuality",
            "knowledgeSharing"],
        "note": "认证/交付质量/分享",
    },
    ("personal", "reputation"): {
        "label": "个人声誉资产",
        "weight": 0.25,
        "evidenceSchema": [
            "peerReviews",
            "privacyBehavior"],
        "note": "同行评价/隐私保护",
    },
    ("personal", "growth"): {
        "label": "个人成长资产",
        "weight": 0.15,
        "evidenceSchema": [
            "learningInvest",
            "errorCorrection", "crossAdapt"],
        "note": "学习/纠错/跨域适应",
    },
    ("personal", "knowledge"): {
        "label": "个人知识资产",
        "weight": 0.20,
        "evidenceSchema": [
            "knowledgeSharing",
            "techContribs"],
        "note": "知识分享/技术贡献",
    },
    # ---- 负资产域(三角色通用) ----
    ("enterprise", "risk"): {
        "label": "企业负资产",
        "weight": -0.30,
        "evidenceSchema": [
            "penaltyRecords",
            "complaintRate"],
        "note": "处罚记录/投诉率——时效"
                "衰减不适用(不可洗白)",
        "negative": True,
    },
    ("organization", "risk"): {
        "label": "团体负资产",
        "weight": -0.25,
        "evidenceSchema": [
            "penaltyRecords",
            "complaintRate"],
        "note": "处罚/投诉——不可清除"
                "(只追加)",
        "negative": True,
    },
    ("personal", "risk"): {
        "label": "个人负资产",
        "weight": -0.20,
        "evidenceSchema": [
            "complaintRate"],
        "note": "投诉率——时效衰减"
                "不适用",
        "negative": True,
    },
}

# 评估状态机(计划 §五——P0 落
# registered/assessing 前置态)
ASSET_STATES = (
    "registered",     # 已登记
    "assessing",      # 评估中(P1)
    "assessed",       # 已评估(P1)
    "active",         # 生效(P1/P2)
    "pending_review",  # low 置信待人工(P1)
    "decaying",       # 闲置衰减(P2)
    "reactivated",    # 激活(P2)
    "disputed",       # 申诉中(P3)
    "adjusted",       # 重估调整(P3)
)

# 铁律: 负资产不可清除(只追加)
RISK_IMMUTABLE = True

# ============================================================
# CAUSAL_RULES 因果规则库封闭注册(P1——§3.2)
# 三元组: 要素(role/domain)→结果(outcome)→强度(strength)
# 版本化: RULES_VERSION; 46号审批: objective 动态
# 权重模式切换(assess_service 双模)
# ============================================================

RULES_VERSION = "v1"

CAUSAL_RULES = {
    # ---- 正资产规则(10) ----
    "CR-001": {
        "role": "enterprise", "domain": "compliance",
        "outcome": "audit_pass_rate", "strength": 0.90,
        "label": "合规完备→审计通过与准入",
    },
    "CR-002": {
        "role": "enterprise", "domain": "knowledge",
        "outcome": "ops_efficiency", "strength": 0.80,
        "label": "知识沉淀→运营效率(客服工单↓)",
    },
    "CR-003": {
        "role": "enterprise", "domain": "behavior",
        "outcome": "collab_quality", "strength": 0.70,
        "label": "行为规范→协作质量",
    },
    "CR-004": {
        "role": "organization", "domain": "social",
        "outcome": "reach_trust", "strength": 0.85,
        "label": "社会资本→触达与信任",
    },
    "CR-005": {
        "role": "organization", "domain": "culture",
        "outcome": "member_retention", "strength": 0.60,
        "label": "文化一致→成员留存",
    },
    "CR-006": {
        "role": "organization", "domain": "compliance",
        "outcome": "event_safety", "strength": 0.80,
        "label": "活动合规→安全零事故",
    },
    "CR-007": {
        "role": "personal", "domain": "capability",
        "outcome": "delivery_reliability", "strength": 0.85,
        "label": "能力认证→交付可靠",
    },
    "CR-008": {
        "role": "personal", "domain": "reputation",
        "outcome": "peer_trust", "strength": 0.80,
        "label": "声誉同行→背书信任",
    },
    "CR-009": {
        "role": "personal", "domain": "growth",
        "outcome": "adaptability", "strength": 0.65,
        "label": "成长学习→跨域适应",
    },
    "CR-010": {
        "role": "personal", "domain": "knowledge",
        "outcome": "knowledge_spillover", "strength": 0.75,
        "label": "知识分享→外溢贡献",
    },
    # ---- 负资产规则(3——强度高=传导快) ----
    "CR-011": {
        "role": "enterprise", "domain": "risk",
        "outcome": "penalty_exposure", "strength": 0.95,
        "label": "处罚记录→合规风险暴露",
    },
    "CR-012": {
        "role": "organization", "domain": "risk",
        "outcome": "complaint_burden", "strength": 0.90,
        "label": "投诉负担→信任损耗",
    },
    "CR-013": {
        "role": "personal", "domain": "risk",
        "outcome": "conduct_risk", "strength": 0.85,
        "label": "行为投诉→行为风险",
    },
}

# objective 动态权重乘子(§3.2——模式切换经 46号审批)
# 铁律: risk 域乘子恒 1.0(负资产不随目标模式减免)
OBJECTIVE_VALUES = ("stability", "growth")

OBJECTIVE_MULTIPLIERS = {
    "stability": {"compliance": 1.2},
    "growth": {"knowledge": 1.2, "growth": 1.2},
}

# ============================================================
# 流动性评级三档(P2——§3.3)
# high  : 标准化可验证(认证类)——使用
#         限频+场景校验
# medium: 需上下文解释(项目经验)
# low   : 个性化/敏感(内部人脉)——仅
#         自证不可流转
# 铁律: risk 域(负资产)不可流转
#       (none 档——不参与场景折算)
# ============================================================

LIQUIDITY_TIERS = (
    "high", "medium", "low", "none")

LIQUIDITY_META = {
    "high": {
        "label": "高流动(标准化可验证)",
        "usage": "使用限频+场景校验",
        "convertible": True,
        "frequencyCap": 10,  # 次/日
    },
    "medium": {
        "label": "中流动(需上下文解释)",
        "usage": "上下文解释后可用",
        "convertible": True,
        "frequencyCap": 5,
    },
    "low": {
        "label": "低流动(个性化/敏感)",
        "usage": "仅自证不可流转",
        "convertible": False,
        "frequencyCap": 0,
    },
    "none": {
        "label": "不可流转(负资产)",
        "usage": "负资产仅扣减不参与折算",
        "convertible": False,
        "frequencyCap": 0,
    },
}

# 资产域→流动性档(封闭映射——认证类
# high/经验类 medium/关系类 low/risk none)
DOMAIN_LIQUIDITY = {
    "compliance": "high",
    "capability": "high",
    "knowledge": "medium",
    "behavior": "medium",
    "growth": "medium",
    "social": "low",
    "culture": "low",
    "reputation": "low",
    "risk": "none",
}

# ============================================================
# 衰减模型(P2——§3.3)
# decay = base × exp(-λ×idleDays)
# 90 日半衰期: λ = ln2/90 ≈ 0.0077
# (计划 λ=0.001 为笔误——按"90 日
#  半衰期"语义口径实现; 半衰期经
#  46号审批可校准 30-365)
# ============================================================

import math  # noqa: E402

DECAY_HALF_LIFE_DAYS = 90
DECAY_LAMBDA = round(
    math.log(2) / DECAY_HALF_LIFE_DAYS, 6)

# 半衰期校准域(46号审批——P2 阈值)
HALF_LIFE_MIN = 30
HALF_LIFE_MAX = 365

# decaying 态门槛(衰减因子跌破
# 该值→状态 decaying)
DECAYING_THRESHOLD = 0.5


def decay_factor(idle_days: int,
                 half_life: int = None) -> float:
    """衰减因子 exp(-λ×idleDays)

    半衰期可校准(46号审批——默认
    90 日: idle 90 天因子=0.5)
    """
    half_life = int(half_life
                    or DECAY_HALF_LIFE_DAYS)
    if half_life <= 0:
        half_life = DECAY_HALF_LIFE_DAYS
    lam = math.log(2) / half_life
    idle = max(int(idle_days or 0), 0)
    return round(
        math.exp(-lam * idle), 6)


def liquidity_of(domain: str) -> str:
    """资产域→流动性档(域外 none)"""
    return DOMAIN_LIQUIDITY.get(
        str(domain), "none")


# ============================================================
# SCENARIO_FACTORS 场景折算表(P2——§3.3)
# 场景×资产域系数(投标/融资/合作
# 准入/免审接入)——未列域默认 1.0
# 铁律: risk 域不参与折算(恒排除)
# ============================================================

SCENARIO_FACTORS = {
    "bidding": {
        "label": "投标",
        "factors": {"compliance": 1.2,
                    "capability": 1.1},
    },
    "financing": {
        "label": "融资",
        "factors": {"compliance": 1.1,
                    "growth": 1.1},
    },
    "partnership": {
        "label": "合作准入",
        "factors": {"behavior": 1.2,
                    "social": 1.1},
    },
    "expedited": {
        "label": "免审接入",
        "factors": {"compliance": 1.3,
                    "capability": 1.15},
    },
}

# 场景乘子校准域(46号审批——场景级
# 附加乘子 0.5-1.5, 默认 1.0)
SCENARIO_MULT_MIN = 0.5
SCENARIO_MULT_MAX = 1.5


def scenario_factor(scenario: str,
                     domain: str) -> float:
    """场景×域系数(域外场景/未列域
    默认 1.0; risk 域恒排除)"""
    entry = SCENARIO_FACTORS.get(
        str(scenario)) or {}
    return float(
        (entry.get("factors")
         or {}).get(str(domain), 1.0))



def get_rule(role: str, domain: str) -> dict | None:
    """取要素因果规则(要素→结果→强度三元组)"""
    for rule_id, rule in CAUSAL_RULES.items():
        if rule.get("role") == str(role) \
                and rule.get("domain") == str(domain):
            rule = dict(rule)
            rule["ruleId"] = rule_id
            return rule
    return None


def get_objective_multiplier(objective: str,
                             domain: str) -> float:
    """objective 动态权重乘子(risk 恒 1.0 铁律)"""
    if domain == RISK_DOMAIN:
        return 1.0
    mult = (OBJECTIVE_MULTIPLIERS.get(
        str(objective)) or {}).get(str(domain))
    return float(mult) if mult else 1.0


def rules_view() -> dict:
    """因果规则库视图(观测面)"""
    by_element = {}
    for rule_id, rule in CAUSAL_RULES.items():
        by_element.setdefault(
            f"{rule.get('role')}/{rule.get('domain')}",
            []).append(rule_id)
    return {
        "success": True,
        "version": RULES_VERSION,
        "rules": len(CAUSAL_RULES),
        "elementsCovered": len(by_element),
        "byElement": by_element,
        "objective": {
            "values": list(OBJECTIVE_VALUES),
            "multipliers": OBJECTIVE_MULTIPLIERS,
            "riskMultiplierLocked": 1.0,
            "switch": "46号审批(双模——submit/apply)",
        },
        "note": "因果规则库——要素→结果→强度"
                "三元组封闭注册(归因强制"
                "绑定规则 ID)",
    }


def liquidity_view() -> dict:
    """流动性+场景+衰减视图(P2 观测面)"""
    return {
        "success": True,
        "liquidity": {
            "tiers": list(LIQUIDITY_TIERS),
            "meta": LIQUIDITY_META,
            "domainMapping": DOMAIN_LIQUIDITY,
        },
        "decay": {
            "halfLifeDays":
                DECAY_HALF_LIFE_DAYS,
            "lambda": DECAY_LAMBDA,
            "formula": "decay = base × "
                       "exp(-λ×idleDays)",
            "decayingThreshold":
                DECAYING_THRESHOLD,
            "calibratable":
                f"{HALF_LIFE_MIN}-{HALF_LIFE_MAX}"
                f" 日(46号审批)",
        },
        "scenarios": {
            name: {
                "label": entry.get("label"),
                "factors": entry.get(
                    "factors"),
            }
            for name, entry in
            SCENARIO_FACTORS.items()},
        "note": "转化层——流动性三档+"
                "衰减模型(90 日半衰期)+"
                "场景折算表(四场景)",
    }


def _validate_causal_rules() -> None:
    """规则库自检(RuntimeError 宪法级)"""
    errors = []
    if len(CAUSAL_RULES) < len(TRUST_ELEMENTS):
        errors.append(
            f"规则 {len(CAUSAL_RULES)} 条不足"
            f"要素 {len(TRUST_ELEMENTS)} 个"
            f"(全覆盖铁律)")
    for rule_id, rule in CAUSAL_RULES.items():
        if not rule_id.startswith("CR-"):
            errors.append(
                f"规则号 {rule_id} 格式非法"
                f"(CR-NNN)")
        for field in ("role", "domain",
                      "outcome", "strength"):
            if field not in rule:
                errors.append(
                    f"规则 {rule_id} 缺字段"
                    f" {field}(三元组残缺)")
        strength = rule.get("strength")
        if not isinstance(strength, (int, float)) \
                or not 0.0 < float(strength) <= 1.0:
            errors.append(
                f"规则 {rule_id} 强度越界"
                f" {strength}((0,1])")
    # 要素全覆盖(每个 TRUST_ELEMENT 至少一条规则)
    for (role, domain) in TRUST_ELEMENTS:
        if get_rule(role, domain) is None:
            errors.append(
                f"要素 {role}/{domain} 无因果"
                f"规则锚点(归因强制绑定铁律)")
    # objective 乘子域
    for obj, mults in OBJECTIVE_MULTIPLIERS.items():
        if obj not in OBJECTIVE_VALUES:
            errors.append(
                f"objective {obj} 域外")
        for dom, m in mults.items():
            if dom == RISK_DOMAIN:
                errors.append(
                    f"objective {obj} 不可调 "
                    f"risk 域乘子(防洗白铁律)")
            if not 0.5 <= float(m) <= 1.5:
                errors.append(
                    f"objective {obj}/{dom} "
                    f"乘子越界 {m}")
    if errors:
        raise RuntimeError(
            "av62 causal rules 自检失败: "
            + "; ".join(errors))
    logger.info(
        "av62_causal_rules_validated rules=%s "
        "version=%s", len(CAUSAL_RULES),
        RULES_VERSION)


def _validate_liquidity() -> None:
    """P2 转化层自检(RuntimeError 宪法级)"""
    errors = []
    # 流动性映射域全覆盖
    for domain in ALL_DOMAINS:
        tier = DOMAIN_LIQUIDITY.get(domain)
        if tier is None:
            errors.append(
                f"资产域 {domain} 缺流动性"
                f"档映射")
        elif tier not in LIQUIDITY_TIERS:
            errors.append(
                f"资产域 {domain} 流动性档"
                f" {tier} 域外")
    if DOMAIN_LIQUIDITY.get(
            RISK_DOMAIN) != "none":
        errors.append(
            "risk 域流动性档必须 none"
            "(负资产不可流转铁律)")
    # 三档元数据
    for tier in LIQUIDITY_TIERS:
        if tier not in LIQUIDITY_META:
            errors.append(
                f"流动性档 {tier} 缺元数据")
    # 衰减模型
    if abs(decay_factor(90) - 0.5) > 0.01:
        errors.append(
            "衰减模型失效(90 日应半衰"
            "——因子 0.5)")
    if not HALF_LIFE_MIN \
            <= DECAY_HALF_LIFE_DAYS \
            <= HALF_LIFE_MAX:
        errors.append(
            "默认半衰期越出校准域")
    # 场景折算表
    for name, entry in \
            SCENARIO_FACTORS.items():
        for dom, f in (entry.get(
                "factors") or {}).items():
            if dom == RISK_DOMAIN:
                errors.append(
                    f"场景 {name} 不可含 risk"
                    f" 域系数(恒排除铁律)")
            if not 0.5 <= float(f) <= 1.5:
                errors.append(
                    f"场景 {name}/{dom} 系数"
                    f"越界 {f}")
    if errors:
        raise RuntimeError(
            "av62 liquidity 自检失败: "
            + "; ".join(errors))
    logger.info(
        "av62_liquidity_validated tiers=%s "
        "scenarios=%s halfLife=%s",
        len(LIQUIDITY_TIERS),
        len(SCENARIO_FACTORS),
        DECAY_HALF_LIFE_DAYS)


def current_mode() -> str:
    """模块开关(AV62_MODE, 默认 off——
    决策面关闭: off=仅观测面; shadow=
    影子估值期(评估留痕); assist=
    辅助估值期(登记开放)"""
    mode = os.environ.get("AV62_MODE") or DEFAULT_MODE
    return mode if mode in MODE_VALUES else DEFAULT_MODE


def get_element(role: str, domain: str
                ) -> dict | None:
    """取信任要素定义(域外 None)"""
    return TRUST_ELEMENTS.get(
        (str(role), str(domain)))


def is_negative(role: str, domain: str
                ) -> bool:
    """负资产域判定(risk 域恒负)"""
    return str(domain) == RISK_DOMAIN


def validate_evidence(role: str, domain: str,
                      evidence: dict) -> dict:
    """证据校验(封闭字段域——
    域外字段拒绝; 负资产字段
    不可缺省)

    Returns:
        {valid, cleaned, missing,
         rejectedFields}
    """
    element = get_element(role, domain)
    if element is None:
        return {
            "valid": False,
            "cleaned": {},
            "missing": [],
            "rejectedFields":
                list((evidence or {})
                     .keys()),
            "error": "要素域外(角色×"
                     "资产域未注册)",
        }
    schema = set(
        element.get("evidenceSchema") or [])
    cleaned = {}
    rejected = []
    for k, v in (evidence or {}).items():
        if k in schema:
            cleaned[k] = v
        else:
            rejected.append(k)
    # 负资产域证据必填(处罚记录
    # 不可缺省——防漏报洗白)
    if is_negative(role, domain):
        if "penaltyRecords" in schema \
                and "penaltyRecords" \
                not in cleaned \
                and "complaintRate" \
                not in cleaned:
            return {
                "valid": False,
                "cleaned": cleaned,
                "missing":
                    ["penaltyRecords"],
                "rejectedFields":
                    rejected,
                "error": "负资产域证据"
                         "必填(处罚/投诉"
                         "不可缺省)",
            }
    return {
        "valid": not rejected,
        "cleaned": cleaned,
        "missing": [],
        "rejectedFields": rejected,
    }


def registry_view() -> dict:
    """注册表自描述(观测面)"""
    by_role: dict = {}
    for (role, domain), el in \
            TRUST_ELEMENTS.items():
        by_role.setdefault(
            role, []).append(domain)
    return {
        "success": True,
        "modelVersion": MODEL_VERSION,
        "mode": current_mode(),
        "roles": len(ROLE_DOMAINS),
        "positiveDomains":
            len(DOMAINS),
        "riskDomain": RISK_DOMAIN,
        "elements": len(TRUST_ELEMENTS),
        "meta": {
            "roleDomains":
                list(ROLE_DOMAINS),
            "domains": list(DOMAINS),
            "allDomains":
                list(ALL_DOMAINS),
            "states":
                list(ASSET_STATES),
            "evidenceFields":
                len(EVIDENCE_FIELDS),
            "riskImmutable":
                RISK_IMMUTABLE,
            "byRole": {
                r: sorted(d)
                for r, d in
                by_role.items()},
        },
        "modeValues": MODE_VALUES,
        "note": "信任要素注册表——三角色"
                "×九资产域+负资产域封闭"
                "注册(铸币-流通-评级"
                "三权分立: 62估值→45流通"
                "→47评级)",
    }


def _validate_registry() -> None:
    """启动自检(RuntimeError 宪法级)"""
    errors = []
    # 角色域全覆盖(每角色至少 3 域)
    for role in ROLE_DOMAINS:
        domains = [d for (r, d)
                   in TRUST_ELEMENTS
                   if r == role]
        if len(domains) < 3:
            errors.append(
                f"角色 {role} 资产域"
                f"不足 3(实际 "
                f"{len(domains)})")
        if RISK_DOMAIN not in domains:
            errors.append(
                f"角色 {role} 缺负资产域"
                f"(risk——不可洗白铁律)")
    # 负资产域结构
    for (role, domain), el in \
            TRUST_ELEMENTS.items():
        if role not in ROLE_DOMAINS:
            errors.append(
                f"要素角色 {role} 域外")
        if domain not in ALL_DOMAINS:
            errors.append(
                f"要素资产域 {domain} 域外")
        w = el.get("weight")
        if not -1.0 <= float(w) <= 1.0:
            errors.append(
                f"要素 {role}/{domain} "
                f"权重越界 {w}")
        # 负域权重必为负
        if domain == RISK_DOMAIN:
            if float(w) >= 0:
                errors.append(
                    f"负资产 {role} 权重"
                    f"须为负({w})")
            if not el.get("negative"):
                errors.append(
                    f"负资产 {role} 缺"
                    f"negative 标记")
        elif float(w) < 0:
            errors.append(
                f"正资产 {role}/{domain} "
                f"权重不可为负({w})")
        # 证据字段域
        for f in el.get(
                "evidenceSchema") or []:
            if f not in EVIDENCE_FIELDS:
                errors.append(
                    f"要素 {role}/{domain} "
                    f"证据字段 {f} 域外")
        if not el.get(
                "evidenceSchema"):
            errors.append(
                f"要素 {role}/{domain} "
                f"证据字段为空")
    # 状态机
    if len(ASSET_STATES) != 9:
        errors.append(
            f"评估状态机应九态, 实际 "
            f"{len(ASSET_STATES)}")
    if "risk" not in ALL_DOMAINS:
        errors.append("负资产域缺失")
    if errors:
        raise RuntimeError(
            "av62 registry 自检失败: "
            + "; ".join(errors))
    logger.info(
        "av62_registry_validated roles=%s "
        "elements=%s risk=%s",
        len(ROLE_DOMAINS),
        len(TRUST_ELEMENTS),
        sum(1 for (_, d) in
            TRUST_ELEMENTS
            if d == RISK_DOMAIN))


_validate_registry()
_validate_causal_rules()
_validate_liquidity()
