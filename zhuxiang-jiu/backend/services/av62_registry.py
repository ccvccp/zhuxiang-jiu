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
