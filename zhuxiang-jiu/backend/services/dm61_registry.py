"""61号·AI智能系统升级决策 决策注册表
(dm61_registry)

计划(docs/61号_AI智能系统升级决策模块实施计划.md
§3.1/§七 P0):
    SEMANTIC_TAGS 六类语义标签域——
    确定性关键词规则→结构化变更意图
    标签(LLM 不进判定链——assist 仅
    P3 报告润色)
    DEPENDENCY_MAP 模块依赖封闭注册
    ——变更类型×受影响角色/功能/信值
    要素+预估影响幅度
    WINDOW_CHECK 环境适宜性——业务
    高峰标记/近期故障率/信值分布稳定性
    →升级窗口适宜度

设计(52/63号封闭注册表范式):
    - 封闭注册: 可断言/可测试/启动自检
    - 标签域六类+敏感级四档
    - 三权分立: 56号生产资产→61号
      裁决决策→46号审批总线(本模块
      仅参谋部——不执行变更)

启动自检 _validate_registry()(RuntimeError
宪法级): 标签/依赖/窗口结构合法+域
覆盖+敏感级合法+影响幅度口径一致。
"""

import logging
import os

logger = logging.getLogger("dm61_registry")

MODEL_VERSION = "v1-dm61-registry"

DEFAULT_MODE = "off"

MODE_VALUES = ("off", "shadow", "assist")

# ============================================================
# 语义标签域(六类——计划 §3.1 变更语义解析)
# ============================================================

SEMANTIC_TAGS = (
    "payment_opt",      # 支付链路优化
    "ui_adapt",         # 界面适配
    "compliance_rule",  # 合规规则
    "algo_param",       # 算法参数
    "core_refactor",    # 核心重构
    "permission_change",  # 权限变更
)

# 敏感级四档(标签属性——驱动 riskScore
# 敏感因子)
SENSITIVITY_LEVELS = (
    "observe",   # 观测类(低)
    "routine",   # 常规类(中低)
    "sensitive", # 敏感类(中高)
    "critical",  # 关键类(高)
)

# 标签→属性(敏感级/默认影响幅度/描述)
# 敏感级铁律(计划 §3.2):
#   core_refactor/permission_change 域
#   →critical(L3 管控级候选——人类全程
#   主导); compliance_rule→sensitive
TAG_META = {
    "payment_opt": {
        "sensitivity": "sensitive",
        "impactPct": 3.0,
        "label": "支付链路优化",
        "note": "支付路由/费率/结算口径调整"
                "——涉资金域默认敏感",
    },
    "ui_adapt": {
        "sensitivity": "routine",
        "impactPct": 1.0,
        "label": "界面适配",
        "note": "界面渲染/无障碍/多端适配"
                "——用户可见但不触数据口径",
    },
    "compliance_rule": {
        "sensitivity": "sensitive",
        "impactPct": 5.0,
        "label": "合规规则",
        "note": "审核规则/禁令词表/税务口径"
                "——合规红线必须人工终审",
    },
    "algo_param": {
        "sensitivity": "routine",
        "impactPct": 2.0,
        "label": "算法参数",
        "note": "评分权重/阈值/排序因子"
                "——可回滚可观测",
    },
    "core_refactor": {
        "sensitivity": "critical",
        "impactPct": 8.0,
        "label": "核心重构",
        "note": "核心链路/数据模型/状态机重构"
                "——L3 管控级候选铁律",
    },
    "permission_change": {
        "sensitivity": "critical",
        "impactPct": 10.0,
        "label": "权限变更",
        "note": "角色/动作域/门槛调整"
                "——权限域 L3 铁律+46号审批",
    },
}

# 确定性关键词规则(语义标签轨——计划 §二:
# LLM 变更语义解析→本站化"确定性标签轨优先")
# 有序规则: 首命中即定(规则顺序=优先级)
SEMANTIC_RULES = (
    ("permission_change", (
        "权限", "角色", "白名单", "免审",
        "审批门槛", "授权域")),
    ("compliance_rule", (
        "合规", "禁令", "词表", "税务",
        "法规", "红线", "审核规则")),
    ("core_refactor", (
        "重构", "数据模型", "状态机", "架构",
        "核心链路", "迁移")),
    ("payment_opt", (
        "支付", "结算", "费率", "退款",
        "分账", "收银")),
    ("algo_param", (
        "权重", "阈值", "评分", "参数",
        "排序因子", "因子")),
    ("ui_adapt", (
        "界面", "渲染", "适配", "无障碍",
        "样式", "布局")),
)

# 兜底标签(无关键词命中——观测类最低敏)
FALLBACK_TAG = "ui_adapt"
FALLBACK_SENSITIVITY = "observe"

# 敏感级→风险分基线(0-100——riskScore
# w1 敏感因子口径, P1 完整四因子)
SENSITIVITY_RISK_BASE = {
    "observe": 10.0,
    "routine": 30.0,
    "sensitive": 60.0,
    "critical": 85.0,
}

# ============================================================
# 模块依赖表(封闭注册——影响面预测)
# ============================================================

# 信值要素域(45号口径)
TRUST_ELEMENTS = (
    "behavior",    # 行为评级
    "compliance",  # 合规记录
    "circulation", # 流通处置
    "asset",       # 资产估值
)

# 角色域(63号四角色口径对齐)
AFFECTED_ROLES = (
    "member",            # 会员
    "ally_merchant",     # 同盟商
    "ops_operator",      # 内容运营
    "compliance_auditor",  # 合规审核员
    "platform_admin",    # 平台管理员
)

# 标签→受影响面(角色×功能×信值要素——
# 影响面预测 DEPENDENCY_MAP 封闭注册)
DEPENDENCY_MAP = {
    "payment_opt": {
        "roles": ("member", "ally_merchant"),
        "features": ("支付收银", "分账结算",
                     "退款处置"),
        "trustElements": ("behavior",
                          "compliance"),
        "modules": ("60智能支付", "45信值"),
    },
    "ui_adapt": {
        "roles": ("member",),
        "features": ("界面渲染", "无障碍"),
        "trustElements": (),
        "modules": ("前端适配",),
    },
    "compliance_rule": {
        "roles": ("ally_merchant",
                  "ops_operator",
                  "compliance_auditor"),
        "features": ("内容审核", "发布预检",
                     "申诉处置"),
        "trustElements": ("compliance",),
        "modules": ("63智能后台", "18条款规则"),
    },
    "algo_param": {
        "roles": ("member", "ally_merchant"),
        "features": ("评分排序", "推荐分流"),
        "trustElements": ("behavior",),
        "modules": ("44API管理", "36智能推广"),
    },
    "core_refactor": {
        "roles": AFFECTED_ROLES,
        "features": ("核心链路", "数据模型",
                     "状态机"),
        "trustElements": TRUST_ELEMENTS,
        "modules": ("全站核心",),
    },
    "permission_change": {
        "roles": ("ally_merchant",
                  "ops_operator",
                  "compliance_auditor",
                  "platform_admin"),
        "features": ("权限裁决", "工作台",
                     "审核队列"),
        "trustElements": ("compliance",
                          "circulation"),
        "modules": ("63智能后台", "46治理中枢"),
    },
}

# ============================================================
# 环境适宜性(WINDOW_CHECK——计划 §3.1)
# ============================================================

# 业务高峰时段域(封闭——命中即窗口降档)
PEAK_HOURS = tuple(range(19, 23))  # 19-22 点

# 窗口适宜度三档(适宜/收紧/不适宜)
WINDOW_LEVELS = (
    "suitable",   # 适宜(可正常推进)
    "caution",    # 收紧(自动升一级——P1)
    "unsuitable", # 不适宜(建议改期)
)

# 环境因子扣分口径(0-100 汇总——
# ≥40 unsuitable / ≥20 caution)
RECENT_FAILURE_PENALTY = 25.0   # 近期故障率≥5%
TRUST_VOLATILITY_PENALTY = 20.0  # 信值分布波动
PEAK_PENALTY = 30.0             # 业务高峰时段

WINDOW_CAUTION_THRESHOLD = 20.0
WINDOW_UNSUITABLE_THRESHOLD = 40.0


def current_mode() -> str:
    """模块开关(DM61_MODE, 默认 off——
    决策面关闭: off=仅观测面; shadow=
    影子推演期(评估留痕不联动); assist=
    辅助参谋期(方案生成开放)"""
    mode = os.environ.get("DM61_MODE") or DEFAULT_MODE
    return mode if mode in MODE_VALUES else DEFAULT_MODE


def parse_semantic_tag(title: str,
                       description: str = "") -> dict:
    """变更语义解析(确定性关键词轨——
    LLM 不进判定链)

    有序规则首命中即定; 无命中兜底
    观测类(ui_adapt+observe 敏感降档)。

    Returns:
        {tag, sensitivity, matchedRule,
         matchedKeyword, impactPct,
         source: "rule"| "fallback"}
    """
    text = f"{title or ''} {description or ''}"
    for tag, keywords in SEMANTIC_RULES:
        for kw in keywords:
            if kw in text:
                meta = TAG_META[tag]
                return {
                    "tag": tag,
                    "sensitivity": meta[
                        "sensitivity"],
                    "matchedRule": tag,
                    "matchedKeyword": kw,
                    "impactPct": meta[
                        "impactPct"],
                    "source": "rule",
                }
    # 兜底: 观测类(无关键词命中——
    # 敏感降档 observe 防误判高敏)
    return {
        "tag": FALLBACK_TAG,
        "sensitivity": FALLBACK_SENSITIVITY,
        "matchedRule": "",
        "matchedKeyword": "",
        "impactPct": 0.5,
        "source": "fallback",
    }


def predict_impact(tag: str) -> dict:
    """影响面预测(标签×DEPENDENCY_MAP
    封闭注册→受影响清单+预估幅度)

    域外标签 fail-safe 取全量最大影响
    (防伪造标签缩小影响面——红队 RT-01
    标签伪造防御)。

    Returns:
        {roles, features, trustElements,
         modules, impactPct, sensitivity,
         roleCount, trustElementCount}
    """
    tag = str(tag)
    dep = DEPENDENCY_MAP.get(tag)
    if dep is None:
        # fail-safe: 域外标签按核心重构
        # 最大影响面处理(不缩小)
        dep = DEPENDENCY_MAP["core_refactor"]
        impact_pct = TAG_META[
            "core_refactor"]["impactPct"]
        sensitivity = "critical"
    else:
        impact_pct = TAG_META[tag][
            "impactPct"]
        sensitivity = TAG_META[tag][
            "sensitivity"]
    return {
        "roles": list(dep["roles"]),
        "features": list(dep["features"]),
        "trustElements": list(
            dep["trustElements"]),
        "modules": list(dep["modules"]),
        "impactPct": float(impact_pct),
        "sensitivity": sensitivity,
        "roleCount": len(dep["roles"]),
        "trustElementCount": len(
            dep["trustElements"]),
    }


def check_window(hour: int,
                 recent_failure_rate: float = 0.0,
                 trust_volatility: float = 0.0
                 ) -> dict:
    """环境适宜性检查(WINDOW_CHECK——确定性)

    三因子扣分: 业务高峰时段+近期故障率
    (≥5%)+信值分布波动(≥0.3)

    Args:
        hour: 当前时点(0-23)
        recent_failure_rate: 近 7 日变更
            失败率(0-1)
        trust_volatility: 信值分布稳定性
            (波动系数 0-1)

    Returns:
        {level, penalties, score, advice}
    """
    try:
        hour = int(hour) % 24
    except (TypeError, ValueError):
        hour = 12
    failure_rate = max(0.0, min(1.0,
                                float(recent_failure_rate
                                      or 0.0)))
    volatility = max(0.0, min(1.0,
                               float(
                                   trust_volatility
                                   or 0.0)))

    penalties = {}
    if hour in PEAK_HOURS:
        penalties["peakHour"] = PEAK_PENALTY
    if failure_rate >= 0.05:
        penalties["recentFailure"] = \
            RECENT_FAILURE_PENALTY
    if volatility >= 0.3:
        penalties["trustVolatility"] = \
            TRUST_VOLATILITY_PENALTY

    score = round(sum(penalties.values()), 1)
    if score >= WINDOW_UNSUITABLE_THRESHOLD:
        level = "unsuitable"
        advice = ("窗口不适宜——建议改期"
                  "(高峰+故障/波动叠加)")
    elif score >= WINDOW_CAUTION_THRESHOLD:
        level = "caution"
        advice = ("窗口收紧——决策级自动"
                  "升一级(P1 联动)")
    else:
        level = "suitable"
        advice = "窗口适宜——可正常推进"
    return {
        "level": level,
        "hour": hour,
        "penalties": penalties,
        "score": score,
        "advice": advice,
    }


def registry_view() -> dict:
    """注册表自描述(观测面)"""
    return {
        "success": True,
        "modelVersion": MODEL_VERSION,
        "mode": current_mode(),
        "semanticTags": len(SEMANTIC_TAGS),
        "sensitivityLevels": len(
            SENSITIVITY_LEVELS),
        "dependencyEntries": len(
            DEPENDENCY_MAP),
        "semanticRules": len(SEMANTIC_RULES),
        "meta": {
            "tagDomains": list(
                SEMANTIC_TAGS),
            "sensitivityDomain": list(
                SENSITIVITY_LEVELS),
            "windowLevels": list(
                WINDOW_LEVELS),
            "affectedRoles": list(
                AFFECTED_ROLES),
            "trustElements": list(
                TRUST_ELEMENTS),
            "tagMeta": {
                t: {
                    "sensitivity":
                        TAG_META[t][
                            "sensitivity"],
                    "impactPct":
                        TAG_META[t][
                            "impactPct"],
                    "label": TAG_META[t][
                        "label"],
                }
                for t in SEMANTIC_TAGS},
        },
        "modeValues": MODE_VALUES,
        "note": "决策注册表——语义标签六类"
                "+模块依赖封闭注册+环境适宜性"
                "(确定性规则 LLM 不进判定链; "
                "三权分立: 56生产→61参谋→"
                "46审批)",
    }


def _validate_registry() -> None:
    """启动自检(RuntimeError 宪法级)"""
    errors = []
    # 标签域结构
    if len(SEMANTIC_TAGS) != 6:
        errors.append(
            f"语义标签应六类, 实际 "
            f"{len(SEMANTIC_TAGS)}")
    for tag in SEMANTIC_TAGS:
        if tag not in TAG_META:
            errors.append(
                f"标签 {tag} 无属性注册")
        if tag not in DEPENDENCY_MAP:
            errors.append(
                f"标签 {tag} 无依赖映射")
    for tag in TAG_META:
        if tag not in SEMANTIC_TAGS:
            errors.append(
                f"属性注册 {tag} 域外")
    for tag in DEPENDENCY_MAP:
        if tag not in SEMANTIC_TAGS:
            errors.append(
                f"依赖映射 {tag} 域外")
    # 敏感级域
    for tag, meta in TAG_META.items():
        sens = meta.get("sensitivity")
        if sens not in SENSITIVITY_LEVELS:
            errors.append(
                f"标签 {tag} 敏感级域外 "
                f"{sens}")
        pct = meta.get("impactPct")
        if not 0 < float(pct) <= 100:
            errors.append(
                f"标签 {tag} 影响幅度越界 "
                f"{pct}")
    # 铁律: 核心重构/权限变更必须 critical
    for must_critical in ("core_refactor",
                          "permission_change"):
        if TAG_META[must_critical][
                "sensitivity"] != "critical":
            errors.append(
                f"标签 {must_critical} "
                f"敏感级必须 critical")
    # 敏感级风险分基线全覆盖
    if set(SENSITIVITY_RISK_BASE) != set(
            SENSITIVITY_LEVELS):
        errors.append(
            "敏感级风险基线与敏感级域不一致")
    # 关键词规则域
    for tag, kws in SEMANTIC_RULES:
        if tag not in SEMANTIC_TAGS:
            errors.append(
                f"关键词规则标签 {tag} 域外")
        if not kws:
            errors.append(
                f"标签 {tag} 关键词为空")
    # 兜底标签合法
    if FALLBACK_TAG not in SEMANTIC_TAGS:
        errors.append("兜底标签域外")
    if FALLBACK_SENSITIVITY not in \
            SENSITIVITY_LEVELS:
        errors.append("兜底敏感级域外")
    # 依赖映射角色/要素域
    for tag, dep in DEPENDENCY_MAP.items():
        for role in dep.get("roles", ()):
            if role not in AFFECTED_ROLES:
                errors.append(
                    f"{tag} 受影响角色 "
                    f"{role} 域外")
        for elem in dep.get(
                "trustElements", ()):
            if elem not in TRUST_ELEMENTS:
                errors.append(
                    f"{tag} 信值要素 "
                    f"{elem} 域外")
        if not dep.get("features"):
            errors.append(
                f"{tag} 无受影响功能")
        if not dep.get("modules"):
            errors.append(
                f"{tag} 无关联模块")
    # 窗口域
    if len(PEAK_HOURS) == 0 or len(
            PEAK_HOURS) >= 24:
        errors.append("高峰时段域非法")
    if not 0 < WINDOW_CAUTION_THRESHOLD \
            < WINDOW_UNSUITABLE_THRESHOLD:
        errors.append("窗口阈值序非法")
    if set(WINDOW_LEVELS) != {
            "suitable", "caution",
            "unsuitable"}:
        errors.append("窗口档域非法")
    if errors:
        raise RuntimeError(
            "dm61 registry 自检失败: "
            + "; ".join(errors))
    logger.info(
        "dm61_registry_validated tags=%s "
        "dependencies=%s rules=%s",
        len(SEMANTIC_TAGS),
        len(DEPENDENCY_MAP),
        len(SEMANTIC_RULES))


_validate_registry()
