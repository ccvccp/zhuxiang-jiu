"""58号·识别即合规前置校验
(ii58_compliance, P2)

计划(docs/58号_AI智能优化意图识别算法模块实施计划.md
§4.4/§九 P2):
    意图-权限映射前置校验(识别即合规——
    48号执行器前置语义平移):
        ① 角色校验: ROLE_RANK(member_role ≥ minRole)
        ② 沙箱五级裁决:
           deny      → 拦截(越界元意图域)
           sensitive → 二次确认(屏幕码——48号
                       confirmToken 流语义标记,
                       58号只标记不执行)
           readonly/write/none → 放行
    合规模板关联(57号种子 valueTags 只读联动语义):
        12 意图 complianceTemplate →
        COMPLIANCE_TEMPLATES 确定性模板
        (valueTags 信值标签+guardrails 护栏口径)

三态纯度铁律(计划 §九 P2 QC):
    权限裁决不污染置信度三态——仅 resolved 态
    (意图交付域)触发前置校验; clarify/partial
    无意图交付无权限语义。

归因铁律: 越界拦截 boundaryIntercepted 留痕
+归因保留原始意图(无归因不计入有效服务)。
"""

import logging

logger = logging.getLogger("ii58_compliance")

MODEL_VERSION = "v1-ii58-compliance"

# 角色等级(最小权限校验序——48号三级范式+guest)
ROLE_RANK = {
    "guest": 0,
    "member": 1,
    "staff": 2,
    "admin": 3,
}

# 合规模板域(12 意图 → 10 模板; 57号 valueTags
# 只读联动语义——确定性 mock 域不依赖运行态)
COMPLIANCE_TEMPLATES: dict = {
    # ---- product 侧(product_policy ×2) ----
    "product_policy": {
        "label": "产品侧政策",
        "valueTags": ["商品合规", "信息真实"],
        "guardrails": [
            "商品信息以库存档案为准",
            "不承诺绝对功效表述",
        ],
    },
    # ---- trust 侧 ----
    "trust_policy": {
        "label": "信值查询政策",
        "valueTags": ["数据透明", "隐私保护"],
        "guardrails": [
            "仅展示本人档案",
            "余额与信值分以账本为准",
        ],
    },
    "trust_convert": {
        "label": "信值兑换政策",
        "valueTags": ["资金安全", "二次确认"],
        "guardrails": [
            "金额上限校验",
            "屏幕码核销(48号 confirmToken 流)",
            "兑换前余额复核",
        ],
    },
    # ---- nav 侧 ----
    "nav_whitelist": {
        "label": "导航白名单",
        "valueTags": ["路径安全"],
        "guardrails": ["仅白名单页面可跳转"],
    },
    # ---- other 侧 ----
    "promo_policy": {
        "label": "优惠活动政策",
        "valueTags": ["活动合规", "规则明示"],
        "guardrails": [
            "优惠以在期活动为准",
            "不可叠加时须明示",
        ],
    },
    "service_sop": {
        "label": "转人工服务SOP",
        "valueTags": ["服务兜底"],
        "guardrails": [
            "排队状态透明",
            "上下文移交须脱敏",
        ],
    },
    "report_policy": {
        "label": "解读报告政策",
        "valueTags": ["可解释性"],
        "guardrails": [
            "报告引用归因链",
            "数字一律来自执行层",
        ],
    },
    "help_sop": {
        "label": "帮助SOP",
        "valueTags": ["引导友好"],
        "guardrails": ["指令集范围内引导"],
    },
    # ---- 元意图域 ----
    "boundary_reject": {
        "label": "越界拒绝话术",
        "valueTags": ["越界拦截"],
        "guardrails": [
            "拒绝并归因留痕",
            "高危操作永不执行",
        ],
        "refusal": "该请求超出可用意图范围——"
                   "已拒绝并留痕(识别即合规)",
    },
    "clarify_sop": {
        "label": "澄清SOP",
        "valueTags": ["澄清优先"],
        "guardrails": ["追问而非猜测"],
    },
}

# 沙箱级 → 合规裁决动作
DENIED = "denied"
CONFIRM_REQUIRED = "confirm_required"
ALLOW = "allow"


def load_template(intent_id: str) -> dict | None:
    """合规模板加载(fail-soft——缺失仅省略)"""
    from services.ii58_registry import (
        INTENT_REGISTRY,
    )
    meta = INTENT_REGISTRY.get(intent_id) or {}
    name = meta.get("complianceTemplate")
    template = COMPLIANCE_TEMPLATES.get(name)
    return dict(template) if template else None


def templates_covered() -> dict:
    """模板覆盖度(启动自检/测试断言域)"""
    from services.ii58_registry import (
        INTENT_REGISTRY,
    )
    missing = [
        f"{iid}:{meta.get('complianceTemplate')}"
        for iid, meta in INTENT_REGISTRY.items()
        if meta.get("complianceTemplate")
        not in COMPLIANCE_TEMPLATES]
    return {
        "intents": len(INTENT_REGISTRY),
        "templates": len(COMPLIANCE_TEMPLATES),
        "missing": missing,
        "covered": not missing,
    }


def judge(intent_id: str,
          member_role: str) -> dict:
    """识别即合规前置校验(计划 §4.4)

    裁决序(先沙箱后角色——deny 元意图域对
    任何角色均拦截):
        deny → 拦截 / 角色不足 → 越界拦截 /
        sensitive → 二次确认 / 其余 → 放行

    Args:
        intent_id: 识别输出意图(resolved 态
                   交付域)
        member_role: 会员角色(未知角色
                    fail-soft 走 member 基线)

    Returns:
        compliance 块:
        {decision, minRole, memberRole, sandbox,
         template, requireConfirm, refusalNote,
         originalIntentId(仅 denied)}
    """
    from services.ii58_registry import (
        INTENT_REGISTRY,
    )
    role = str(member_role or "member").strip().lower()
    if role not in ROLE_RANK:
        role = "member"   # fail-soft 未知角色
    meta = INTENT_REGISTRY.get(intent_id)
    if meta is None:
        # 未在册(unknown 域)——三态已兜底
        # clarify, 无权限语义直接放行
        return {
            "decision": ALLOW,
            "minRole": "",
            "memberRole": role,
            "sandbox": "none",
            "template": None,
            "requireConfirm": False,
            "refusalNote": "",
        }

    sandbox = meta.get("sandbox") or "none"
    min_role = meta.get("minRole") or "member"
    template = load_template(intent_id)
    base = {
        "minRole": min_role,
        "memberRole": role,
        "sandbox": sandbox,
        "template": template,
        "requireConfirm": False,
        "refusalNote": "",
    }

    # ① deny 沙箱: 越界元意图域——任何角色拦截
    if sandbox == "deny":
        refusal = (template or {}).get("refusal") \
            or "该意图在拒绝域——已拦截并留痕"
        return {
            **base,
            "decision": DENIED,
            "originalIntentId": intent_id,
            "refusalNote": refusal,
        }

    # ② 角色不足: 越界拦截(归因保留原始意图)
    if ROLE_RANK.get(role, 1) < \
            ROLE_RANK.get(min_role, 1):
        return {
            **base,
            "decision": DENIED,
            "originalIntentId": intent_id,
            "refusalNote": (
                f"权限不足: 意图「{meta.get('label')}"
                f"」需 {min_role} 及以上角色"
                f"(识别即合规——越界拦截)"),
        }

    # ③ sensitive 沙箱: 二次确认(屏幕码语义标记)
    if sandbox == "sensitive":
        return {
            **base,
            "decision": CONFIRM_REQUIRED,
            "requireConfirm": True,
        }

    # ④ readonly/write/none: 放行
    return {**base, "decision": ALLOW}


def validate_templates() -> None:
    """启动自检(12 意图全覆盖——RuntimeError 宪法级)"""
    coverage = templates_covered()
    if not coverage["covered"]:
        raise RuntimeError(
            "ii58 COMPLIANCE_TEMPLATES 自检失败: "
            f"意图模板缺失 {coverage['missing']}")
    logger.info(
        "ii58_compliance_validated intents=%s "
        "templates=%s",
        coverage["intents"], coverage["templates"])


validate_templates()
