"""63号·AI智能后台管理 后台注册表
(ab63_registry)

计划(docs/63号_AI智能后台管理模块实施计划.md
§3.1/§3.2):
    PERMISSION_RULES 四轴封闭规则库——
    角色×信值×场景×风险→权限分(动态
    授权 ABAC 确定性规则域)
    WORKBENCH_TEMPLATES 五角色工作台
    模板——情境化呈现(封闭注册)

设计(52号 us52_registry 封闭注册表范式):
    - 封闭注册: 可断言/可测试/启动自检
    - 角色域: ally_merchant同盟商/
      ops_operator内容运营/compliance_auditor
      合规审核员/platform_admin平台管理员
    - 权限动作域: 基础 CRUD/批量操作/
      免审额度/审核裁决/规则下发
    - tier 联动: trusted/standard/watched/
      restricted 四档(47号口径对齐)

启动自检 _validate_registry()(RuntimeError
宪法级):
    - 规则/模板结构合法
    - 角色域覆盖
    - tier 档合法
    - 动作域合法+风险级合法
"""

import logging
import os

logger = logging.getLogger("ab63_registry")

MODEL_VERSION = "v1-ab63-registry"

DEFAULT_MODE = "off"

MODE_VALUES = ("off", "shadow", "assist")

# 后台角色域(四类——计划 §3.1 身份轴)
ROLE_DOMAINS = (
    "ally_merchant",      # 同盟加入商
    "ops_operator",       # 内容运营
    "compliance_auditor", # 合规审核员
    "platform_admin",     # 平台管理员
)

# 权限动作域
ACTION_DOMAINS = (
    "basic_crud",       # 基础产品增删改查
    "batch_ops",        # 批量操作
    "whitelist_quota",  # 免审白名单额度
    "review_decide",    # 审核裁决
    "rule_broadcast",   # 规则下发
)

# 47号 tier 四档(对齐)
TIER_VALUES = (
    "trusted", "standard",
    "watched", "restricted")

# 场景时段域
SCENE_PERIODS = ("normal", "peak")

# 内容敏感域
SENSITIVITY_LEVELS = ("low", "medium", "high")


def current_mode() -> str:
    """模块开关(AB63_MODE, 默认 off——
    决策面关闭: off=仅观测面; shadow=
    观察学习期(权限裁决+护航留痕);
    assist=辅助生产期(工作台渲染开放)"""
    mode = os.environ.get("AB63_MODE") or DEFAULT_MODE
    return mode if mode in MODE_VALUES else DEFAULT_MODE


# ============================================================
# 权限规则库(四轴——计划 §3.1)
# ============================================================

# 角色×动作 → 基线权限分(0-100)
ROLE_ACTION_BASE: dict = {
    # 同盟商: 基础 CRUD 基线; 批量/
    # 免审需信值加持
    ("ally_merchant", "basic_crud"): 70,
    ("ally_merchant", "batch_ops"): 40,
    ("ally_merchant", "whitelist_quota"): 30,
    ("ally_merchant", "review_decide"): 0,
    ("ally_merchant", "rule_broadcast"): 0,
    # 内容运营: 批量强; 审核不可
    ("ops_operator", "basic_crud"): 80,
    ("ops_operator", "batch_ops"): 70,
    ("ops_operator", "whitelist_quota"): 50,
    ("ops_operator", "review_decide"): 0,
    ("ops_operator", "rule_broadcast"): 0,
    # 合规审核员: 裁决强; 增删弱
    ("compliance_auditor", "basic_crud"): 30,
    ("compliance_auditor", "batch_ops"): 40,
    ("compliance_auditor", "whitelist_quota"): 30,
    ("compliance_auditor", "review_decide"): 90,
    ("compliance_auditor", "rule_broadcast"): 30,
    # 平台管理员: 全域
    ("platform_admin", "basic_crud"): 90,
    ("platform_admin", "batch_ops"): 90,
    ("platform_admin", "whitelist_quota"): 80,
    ("platform_admin", "review_decide"): 70,
    ("platform_admin", "rule_broadcast"): 90,
}

# tier 修正(计划 §3.1——信值锚定)
TIER_BONUS = {
    "trusted": 20,
    "standard": 0,
    "watched": -15,
    "restricted": -30,
}

# 场景风险惩罚(时段×敏感度)
SCENE_PENALTY = {
    ("peak", "high"): 25,
    ("peak", "medium"): 15,
    ("peak", "low"): 5,
    ("normal", "high"): 20,
    ("normal", "medium"): 5,
    ("normal", "low"): 0,
}

# 历史合规率 bonus(90 日窗口——
# compliance_rate 0-1 → 0-15 分)
COMPLIANCE_MAX_BONUS = 15

# 权限生效门槛(≥60 授权;<60 拒绝)
PERMISSION_THRESHOLD = 60

# 高危动作额外门槛(批量/免审/规则下发≥70)
HIGH_RISK_THRESHOLD = 70

# 高危动作域
HIGH_RISK_ACTIONS = (
    "batch_ops", "whitelist_quota",
    "rule_broadcast")

# 权限闲置衰减(90 日未用高危权限回收)
IDLE_DECAY_DAYS = 90


def evaluate_permission(role: str, action: str,
                        tier: str = "standard",
                        compliance_rate: float = 0.8,
                        period: str = "normal",
                        sensitivity: str = "low"
                        ) -> dict:
    """权限裁决(确定性四轴计算——P1 完整
    可解释链: text+ruleId+recoveryPath)

    Returns:
        {granted, score, threshold,
         reason 结构化可解释链
         {text, ruleId, recoveryPath,
          factors}, factors 因子快照}
    """
    base = ROLE_ACTION_BASE.get(
        (str(role), str(action)))
    if base is None:
        return {
            "granted": False,
            "score": 0,
            "threshold": PERMISSION_THRESHOLD,
            "reason": {
                "text": f"角色 {role} 无动作 "
                        f"{action} 基线定义"
                        f"(角色×动作域外)",
                "ruleId": "DOMAIN_OUT",
                "recoveryPath":
                    "使用合法角色/动作域",
                "factors": {},
            },
            "factors": {},
        }
    bonus = TIER_BONUS.get(
        str(tier), 0)
    cr = max(0.0, min(1.0,
                      float(compliance_rate
                            or 0.0)))
    compliance = round(
        cr * COMPLIANCE_MAX_BONUS, 1)
    penalty = SCENE_PENALTY.get(
        (str(period), str(sensitivity)), 0)
    score = round(
        base + bonus + compliance
        - penalty, 1)
    threshold = PERMISSION_THRESHOLD
    if action in HIGH_RISK_ACTIONS:
        threshold = HIGH_RISK_THRESHOLD
    granted = score >= threshold

    # 恢复路径(未达标时的指引)
    recovery_path = ""
    if not granted:
        parts = []
        if bonus <= 0:
            parts.append(
                "提升信值等级(合规操作积累)")
        if compliance < COMPLIANCE_MAX_BONUS:
            parts.append(
                "提高历史合规率(90 日窗口)")
        if penalty > 0:
            parts.append(
                "避开业务高峰/降低内容"
                "敏感度")
        recovery_path = "; ".join(parts) \
            or "积累合规行为后重试"

    reason_text = (
        f"基线{base}+信值tier{bonus:+d}"
        f"+合规{compliance:+.1f}"
        f"-场景风险{penalty}={score}"
        f"(门槛{threshold}"
        f"{'达标' if granted else '未达标'})")
    return {
        "granted": granted,
        "score": score,
        "threshold": threshold,
        "reason": {
            "text": reason_text,
            "ruleId": "PERM_4AXIS",
            "recoveryPath": recovery_path
            or "已达标(无需恢复)",
            "factors": {
                "base": base,
                "tierBonus": bonus,
                "complianceBonus":
                    compliance,
                "scenePenalty": penalty,
            },
        },
        "factors": {
            "base": base,
            "tierBonus": bonus,
            "complianceBonus": compliance,
            "scenePenalty": penalty,
        },
    }


# ============================================================
# 工作台模板(五角色——计划 §3.2)
# ============================================================

WORKBENCH_TEMPLATES: dict = {
    "ally_merchant": {
        "label": "同盟商工作台",
        "noviceView": {
            "hideAdvanced": True,
            "highlightGuide": "合规向导",
            "industryTemplates": [
                "养老", "文创", "通用"],
            "actions": ["basic_crud"],
        },
        "matureView": {
            "hideAdvanced": False,
            "batchToolbar": True,
            "whitelistQuotaHint": True,
            "actions": ["basic_crud",
                        "batch_ops",
                        "whitelist_quota"],
        },
        "accessibility": {
            "largeFont": "auto",
            "voiceAssist": "auto",
        },
        "status": "active",
    },
    "ops_operator": {
        "label": "内容运营工作台",
        "noviceView": {
            "hideAdvanced": False,
            "batchToolbar": False,
            "forcedPreview": "逐条预览",
            "actions": ["basic_crud"],
        },
        "matureView": {
            "hideAdvanced": False,
            "batchToolbar": True,
            "forcedPreview": None,
            "actions": ["basic_crud", "batch_ops"],
        },
        "accessibility": {},
        "status": "active",
    },
    "compliance_auditor": {
        "label": "合规审核工作台",
        "noviceView": {
            "queuePriority": "风险降序",
            "aiPrelabels": True,
            "legalBasis": True,
            "similarCases": True,
            "actions": ["review_decide"],
        },
        "matureView": {
            "queuePriority": "专长优先",
            "aiPrelabels": True,
            "legalBasis": True,
            "similarCases": True,
            "actions": ["review_decide",
                        "batch_ops"],
        },
        "accessibility": {},
        "status": "active",
    },
    "platform_admin": {
        "label": "平台管理驾驶舱",
        "noviceView": {
            "dashboard": ["风险热点",
                          "信任赤字"],
            "ruleBroadcast": True,
            "actions": ["rule_broadcast"],
        },
        "matureView": {
            "dashboard": ["风险热点",
                          "信任赤字",
                          "信值健康度趋势"],
            "ruleBroadcast": True,
            "actions": [
                "rule_broadcast", "review_decide",
                "whitelist_quota"],
        },
        "accessibility": {},
        "status": "active",
    },
}


def get_template(role: str) -> dict | None:
    """取角色工作台模板"""
    return WORKBENCH_TEMPLATES.get(
        str(role))


def registry_view() -> dict:
    """注册表自描述(观测面)"""
    return {
        "success": True,
        "modelVersion": MODEL_VERSION,
        "mode": current_mode(),
        "roles": len(ROLE_DOMAINS),
        "actions": len(ACTION_DOMAINS),
        "ruleEntries": len(ROLE_ACTION_BASE),
        "templates": len(
            WORKBENCH_TEMPLATES),
        "meta": {
            "roleDomains":
                list(ROLE_DOMAINS),
            "actionDomains":
                list(ACTION_DOMAINS),
            "tierValues":
                list(TIER_VALUES),
            "permissionThreshold":
                PERMISSION_THRESHOLD,
            "highRiskThreshold":
                HIGH_RISK_THRESHOLD,
        },
        "modeValues": MODE_VALUES,
        "note": "后台注册表——权限四轴规则"
                "+五角色工作台模板(动态信任"
                "协作网络)",
    }


def _validate_registry() -> None:
    """启动自检(RuntimeError 宪法级)"""
    errors = []
    # 角色域全覆盖
    for role in ROLE_DOMAINS:
        has = any(k[0] == role
                  for k in ROLE_ACTION_BASE)
        if not has:
            errors.append(
                f"角色 {role} 无权限基线")
        if role not in \
                WORKBENCH_TEMPLATES:
            errors.append(
                f"角色 {role} 无工作台模板")
    for (role, action), base in \
            ROLE_ACTION_BASE.items():
        if role not in ROLE_DOMAINS:
            errors.append(
                f"规则角色 {role} 域外")
        if action not in ACTION_DOMAINS:
            errors.append(
                f"规则动作 {action} 域外")
        if not 0 <= base <= 100:
            errors.append(
                f"基线 {role}/{action}"
                f" 越界 {base}")
    if len(ROLE_ACTION_BASE) != 20:
        errors.append(
            f"规则数应为 20, 实际 "
            f"{len(ROLE_ACTION_BASE)}")
    if len(WORKBENCH_TEMPLATES) != 4:
        errors.append(
            f"模板数应为 4, 实际 "
            f"{len(WORKBENCH_TEMPLATES)}")
    if errors:
        raise RuntimeError(
            "ab63 registry 自检失败: "
            + "; ".join(errors))
    logger.info(
        "ab63_registry_validated roles=%s "
        "rules=%s templates=%s",
        len(ROLE_DOMAINS),
        len(ROLE_ACTION_BASE),
        len(WORKBENCH_TEMPLATES))


_validate_registry()
