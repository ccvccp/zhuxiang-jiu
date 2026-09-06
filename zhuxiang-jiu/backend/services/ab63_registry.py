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
    # 场景惩罚(域外键 fail-safe 取
    # 最坏惩罚——防伪造上下文降
    # 风险分: RT-01 红队向量防御)
    period = str(period)
    sensitivity = str(sensitivity)
    if (period not in SCENE_PERIODS
            or sensitivity
            not in SENSITIVITY_LEVELS):
        penalty = max(
            SCENE_PENALTY.values())
    else:
        penalty = SCENE_PENALTY.get(
            (period, sensitivity), 0)
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


# ============================================================
# COMPLIANCE_GUARD 合规护航规则库
# (P2——计划 §3.3 编辑态三档干预)
# ============================================================

# 干预档(渐进式三档——tip<warn<block)
GUARD_LEVELS = ("tip", "warn", "block")

# 检测轨(三轨)
GUARD_TRACKS = ("text", "form", "privacy")

# 文本轨: 敏感词表(封闭——阻断级红线)
SENSITIVE_WORDS = (
    "假发票", "赌博", "洗钱通道", "违禁药品",
    "枪支交易")

# 文本轨: 夸大宣传词表(封闭——警告级)
EXAGGERATION_WORDS = (
    "最好", "第一品牌", "国家级", "根治",
    "无效退款", "百分之百", "绝对安全")

# 文本轨: 必要条款(封闭——提示级缺失检测)
REQUIRED_CLAUSES = ("服务有效期", "退改政策")

# 表单轨: 必填域(封闭——警告级)
FORM_REQUIRED_FIELDS = (
    "title", "price", "validityStart",
    "validityEnd", "refundPolicy")

# 表单轨: 超范围采集域(封闭——阻断级红线:
# 基础服务禁采金融敏感域)
OVERCOLLECT_FIELDS = (
    "id_number", "bank_account")

# 规则 ID 域(封闭——每条规则锚定干预档)
GUARD_RULE_LEVELS = {
    # 文本轨
    "GUARD_SENSITIVE_WORD": "block",
    "GUARD_EXAGGERATION": "warn",
    "GUARD_MISSING_CLAUSE": "tip",
    # 表单轨
    "GUARD_FORM_REQUIRED": "warn",
    "GUARD_FORM_LOGIC": "warn",
    "GUARD_OVERCOLLECT": "block",
    # 隐私轨
    "GUARD_PII_LEAK": "block",
    "GUARD_PRIVACY_BUDGET": "tip",
}

# 知识嵌入(每规则 why/regulation/example——
# 易错点旁"为什么需要这个?"——封闭注册)
GUARD_KNOWLEDGE = {
    "GUARD_SENSITIVE_WORD": {
        "why": "违禁内容存在法律风险, "
               "平台与发布者均需担责",
        "regulation": "《网络安全法》第12条"
                      "(禁止传播违法信息)",
        "example": "某商家因发布违禁内容"
                   "被下架并扣减信值",
    },
    "GUARD_EXAGGERATION": {
        "why": "夸大宣传误导消费者, "
               "属高频驳回点",
        "regulation": "《广告法》第9条"
                      "(禁用'国家级''最佳'等"
                      "绝对化用语)",
        "example": "优质案例: 以'近30日评价"
                   "满意度98%'数据代替绝对化"
                   "用语",
    },
    "GUARD_MISSING_CLAUSE": {
        "why": "必要条款缺失易引发服务纠纷, "
               "补充后可提升用户信任",
        "regulation": "《消费者权益保护法》"
                      "第26条(格式条款显著提示)",
        "example": "成功案例: 明示'服务有效期"
                   "90天, 未使用可退'",
    },
    "GUARD_FORM_REQUIRED": {
        "why": "必填信息完整是发布预检的"
               "基础要求",
        "regulation": "平台发布规范§2"
                      "(产品信息完整性)",
        "example": "缺价格字段是高频驳回点",
    },
    "GUARD_FORM_LOGIC": {
        "why": "逻辑矛盾(价格非正/有效期"
               "倒置)将导致无法正常履约",
        "regulation": "平台发布规范§3"
                      "(信息一致性)",
        "example": "成功案例: 起止日期使用"
                   "日期选择器避免倒置",
    },
    "GUARD_OVERCOLLECT": {
        "why": "基础服务采集身份证/银行卡"
               "属超范围收集, 触发个人信息"
               "保护红线",
        "regulation": "《个人信息保护法》"
                      "第6条(最小必要原则)",
        "example": "同类案例: 改为资质认证"
                   "完成后服务端脱敏存证",
    },
    "GUARD_PII_LEAK": {
        "why": "公开内容含个人敏感信息"
               "(身份证/手机号/卡号)将直接"
               "泄露他人隐私",
        "regulation": "《个人信息保护法》"
                      "第51条(防止泄露义务)",
        "example": "替代方案: 使用"
                   "'138****5678'式脱敏展示",
    },
    "GUARD_PRIVACY_BUDGET": {
        "why": "隐私预算超支将限制个性化"
               "能力, 脱敏后零成本",
        "regulation": "49号隐私预算规则"
                      "(会员自主偏好分级)",
        "example": "替代方案: 对内容脱敏后"
                   "预估成本归零",
    },
}

# 意图侧→导航建议(58号纯消费——
# 建议性渲染非强制)
INTENT_NAV_MAP = {
    "product": ["产品管理", "新品发布向导",
                "行业模板库"],
    "trust": ["信值面板", "信任流水",
              "申诉中心"],
    "nav": ["首页导航", "快捷入口"],
    "other": ["帮助中心", "合规向导"],
}


def guard_rule_view() -> dict:
    """护航规则视图(观测面)"""
    return {
        "tracks": list(GUARD_TRACKS),
        "levels": list(GUARD_LEVELS),
        "rules": len(GUARD_RULE_LEVELS),
        "sensitiveWords": len(SENSITIVE_WORDS),
        "exaggerationWords": len(
            EXAGGERATION_WORDS),
        "requiredClauses": len(REQUIRED_CLAUSES),
        "formRequiredFields": len(
            FORM_REQUIRED_FIELDS),
        "overcollectFields": len(
            OVERCOLLECT_FIELDS),
        "knowledgeEntries": len(GUARD_KNOWLEDGE),
        "note": "COMPLIANCE_GUARD 三轨检测"
                "——确定性规则(LLM 不进判定链)",
    }


# ============================================================
# 智能审核网关分流规则(P3——计划 §3.4)
# ============================================================

# 分流三级
REVIEW_TIERS = ("L1", "L2", "L3")

# Publish_Score 三因子权重(计划 §二——
# 确定性公式: AI 置信度×0.6+tier 基线×0.3
# +内容风险系数×0.1)
PUBLISH_WEIGHTS = {
    "aiConfidence": 0.6,
    "tierBaseline": 0.3,
    "riskFactor": 0.1,
}

# AI 置信度映射(护航干预档→0-1)
GUARD_CONFIDENCE = {
    "clean": 1.0,
    "tip": 0.9,
    "warn": 0.7,
    "block": 0.0,
}

# tier 基线映射(47号口径→0-1)
TIER_BASELINE = {
    "trusted": 1.0,
    "standard": 0.8,
    "watched": 0.5,
    "restricted": 0.2,
}

# 内容风险系数(敏感度倒转→0-1)
RISK_FACTOR = {
    "low": 1.0,
    "medium": 0.7,
    "high": 0.3,
}

# 分流阈值(默认——可经 46号审批校准)
L1_THRESHOLD = 90.0
L2_THRESHOLD = 70.0

# L1 自动过审附加条件: tier 必须 trusted
L1_MIN_TIER = "trusted"

# L3 高风险域标签(命中即强制 L3——
# 无论分数; 铁律"L3 永不自动")
L3_HIGH_RISK_TAGS = (
    "funds",       # 资金域
    "identity",    # 身份域
    "children",    # 儿童域
    "medical",     # 医疗域
)

# L1 抽检率(5%——自动过审兜底)
L1_SPOT_CHECK_RATE = 0.05

# 灰度建议触发标签(高风险变更——
# 价格/服务范围; 建议域不执行)
GRAYSCALE_TAGS = (
    "priceChange", "scopeChange")

# 灰度方案模板(建议域——实际放量由
# 各模块开关矩阵执行)
GRAYSCALE_PLAN = {
    "stages": [5, 20, 50, 100],
    "metric": "投诉率<0.5%+退款率<1%",
    "rollback": "任一阶段不达标→全量回滚",
    "note": "灰度发布建议(建议域——"
            "不自动执行, 实际放量由模块"
            "开关矩阵执行)",
}


def compute_publish_score(
        guard_level: str,
        tier: str,
        sensitivity: str) -> dict:
    """Publish_Score 确定性计算(计划 §3.4
    公式——0-100 分+因子快照可解释)"""
    conf = GUARD_CONFIDENCE.get(
        str(guard_level), 1.0)
    base = TIER_BASELINE.get(
        str(tier), 0.8)
    risk = RISK_FACTOR.get(
        str(sensitivity), 1.0)
    w = PUBLISH_WEIGHTS
    score = round(
        (conf * w["aiConfidence"]
         + base * w["tierBaseline"]
         + risk * w["riskFactor"]) * 100, 1)
    return {
        "score": score,
        "factors": {
            "aiConfidence": conf,
            "tierBaseline": base,
            "riskFactor": risk,
            "weights": dict(w),
        },
    }


def route_review_tier(
        publish_score: float,
        tier: str,
        tags: list) -> dict:
    """三级分流裁决(确定性)

    L1 自动过审: score≥L1 阈值+无高危
    标签+tier=trusted(秒级发布+5% 抽检)
    L2 AI 辅助: score≥L2 阈值
    L3 深度复核: score<L2 或命中高危域
    (双人独立审核+合规官终审——
    永不自动铁律)
    """
    tags = [str(t) for t in (tags or [])]
    high_risk = [t for t in tags
                 if t in L3_HIGH_RISK_TAGS]
    if high_risk:
        return {
            "tier": "L3",
            "autoPublished": False,
            "forcedBy": "highRiskTag",
            "highRiskTags": high_risk,
            "reason": f"命中高风险域"
                      f"({'/'.join(high_risk)})"
                      f"——强制深度复核"
                      f"(双人+合规官终审铁律)",
        }
    if publish_score >= L1_THRESHOLD \
            and tier == L1_MIN_TIER:
        return {
            "tier": "L1",
            "autoPublished": True,
            "forcedBy": "",
            "highRiskTags": [],
            "reason": f"Publish_Score "
                      f"{publish_score}≥{L1_THRESHOLD}"
                      f"+tier={tier}——自动过审"
                      f"(5% 抽样复检兜底)",
        }
    if publish_score >= L2_THRESHOLD:
        return {
            "tier": "L2",
            "autoPublished": False,
            "forcedBy": "",
            "highRiskTags": [],
            "reason": f"Publish_Score "
                      f"{publish_score} 在 "
                      f"{L2_THRESHOLD}-"
                      f"{L1_THRESHOLD}——AI 辅助"
                      f"预审+人工确认",
        }
    return {
        "tier": "L3",
        "autoPublished": False,
        "forcedBy": "lowScore",
        "highRiskTags": [],
        "reason": f"Publish_Score "
                  f"{publish_score}<{L2_THRESHOLD}"
                  f"——深度复核(双人+"
                  f"合规官终审铁律)",
    }


def review_rule_view() -> dict:
    """审核网关规则视图(观测面)"""
    return {
        "tiers": list(REVIEW_TIERS),
        "weights": dict(PUBLISH_WEIGHTS),
        "l1Threshold": L1_THRESHOLD,
        "l2Threshold": L2_THRESHOLD,
        "l1MinTier": L1_MIN_TIER,
        "l3HighRiskTags": list(
            L3_HIGH_RISK_TAGS),
        "l1SpotCheckRate":
            L1_SPOT_CHECK_RATE,
        "grayscaleTags": list(
            GRAYSCALE_TAGS),
        "note": "Publish_Score 三因子确定性"
                "公式+三级分流(L3 永不自动; "
                "L1 5% 抽检)",
    }


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
        "guard": guard_rule_view(),
        "review": review_rule_view(),
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
                "+五角色工作台模板+三轨护航"
                "规则(动态信任协作网络)",
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
    # 护航规则域(P2)
    if len(GUARD_RULE_LEVELS) != 8:
        errors.append(
            f"护航规则数应为 8, 实际 "
            f"{len(GUARD_RULE_LEVELS)}")
    for rid, lv in GUARD_RULE_LEVELS.items():
        if lv not in GUARD_LEVELS:
            errors.append(
                f"护航规则 {rid} 干预档 "
                f"域外 {lv}")
        if rid not in GUARD_KNOWLEDGE:
            errors.append(
                f"护航规则 {rid} 缺知识嵌入")
    if len(GUARD_KNOWLEDGE) != len(
            GUARD_RULE_LEVELS):
        errors.append(
            "知识嵌入与规则数不一致"
            f"({len(GUARD_KNOWLEDGE)}/"
            f"{len(GUARD_RULE_LEVELS)})")
    if not SENSITIVE_WORDS or not \
            EXAGGERATION_WORDS:
        errors.append("词表为空(封闭注册违规)")
    for side in INTENT_NAV_MAP:
        if side not in ("product", "trust",
                        "nav", "other"):
            errors.append(
                f"意图导航侧 {side} 域外")
    # 审核网关规则域(P3)
    if not (0 < L2_THRESHOLD
            < L1_THRESHOLD <= 100):
        errors.append(
            f"分流阈值非法 "
            f"L1={L1_THRESHOLD}/"
            f"L2={L2_THRESHOLD}")
    if abs(sum(PUBLISH_WEIGHTS.values())
           - 1.0) > 1e-9:
        errors.append(
            "Publish_Score 权重和≠1.0")
    if set(GUARD_CONFIDENCE) != set(
            GUARD_LEVELS) | {"clean"}:
        errors.append(
            "AI 置信度映射与干预档"
            "不一致")
    if set(TIER_BASELINE) != set(TIER_VALUES):
        errors.append(
            "tier 基线映射与 tier 域"
            "不一致")
    if set(RISK_FACTOR) != set(
            SENSITIVITY_LEVELS):
        errors.append(
            "风险系数映射与敏感度域"
            "不一致")
    if not L3_HIGH_RISK_TAGS:
        errors.append(
            "L3 高风险域标签为空")
    if not 0 < L1_SPOT_CHECK_RATE < 1:
        errors.append(
            "L1 抽检率域外(0-1 开区间)")
    if errors:
        raise RuntimeError(
            "ab63 registry 自检失败: "
            + "; ".join(errors))
    logger.info(
        "ab63_registry_validated roles=%s "
        "rules=%s templates=%s guards=%s",
        len(ROLE_DOMAINS),
        len(ROLE_ACTION_BASE),
        len(WORKBENCH_TEMPLATES),
        len(GUARD_RULE_LEVELS))


_validate_registry()
