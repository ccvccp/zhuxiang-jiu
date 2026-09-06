"""58号·AI智能优化意图识别 意图注册表
(ii58_registry)

计划(docs/58号_AI智能优化意图识别算法模块实施计划.md §二):
    INTENT_REGISTRY 三位一体封闭白名单——每意图绑定
    minRole 最小权限+sandbox 沙箱级+合规模板引用
    +槽位 schema(对齐 48号 COMMANDS 高频 action
    白名单纯读取)。

设计(52号 us52_registry 范式):
    - 封闭注册表: 可断言/可测试/启动自检
    - 四侧覆盖: product/trust/nav/other
    - 三位一体: 意图×权限(minRole+沙箱级)
      ×合规模板×槽位 schema
    - 越界元意图(boundary.unauthorized):
      权限外请求识别→拒绝归因——"识别即合规"

启动自检 _validate_registry()(RuntimeError 宪法级):
    - 12 项数量+四侧覆盖
    - 沙箱级合法(readonly/write/sensitive/deny/none)
    - minRole 合法
    - confusableWith 双向对称
    - status 合法
"""

import logging
import os

logger = logging.getLogger("ii58_registry")

MODEL_VERSION = "v1-ii58-registry"

DEFAULT_MODE = "off"

# 模式三态(off/shadow/assist——计划 §九开关矩阵)
MODE_VALUES = ("off", "shadow", "assist")

# 意图四侧
INTENT_SIDES = ("product", "trust", "nav", "other")

# 沙箱级(48号三级范式+越界 deny+元意图 none)
SANDBOX_LEVELS = (
    "readonly", "write", "sensitive", "deny", "none")

# 最小权限
ROLE_VALUES = ("guest", "member", "staff", "admin")


def current_mode() -> str:
    """模块开关(II58_MODE, 默认 off——决策面关闭:
    off=评估面仅 registry 观测; shadow=观察学习期
    仅评估留痕; assist=辅助优化期语料+标注+反馈
    开放)"""
    mode = os.environ.get("II58_MODE") or DEFAULT_MODE
    return mode if mode in MODE_VALUES else DEFAULT_MODE


# ============================================================
# 意图注册表(12 项——计划 §二三位一体)
# ============================================================

INTENT_REGISTRY: dict = {
    # ---- product 侧 ----
    "product.new_query": {
        "label": "新品咨询",
        "side": "product",
        "minRole": "member", "sandbox": "readonly",
        "complianceTemplate": "product_policy",
        "slotSchema": ["keyword"],
        "confusableWith": ["product.price_query"],
        "status": "active",
    },
    "product.price_query": {
        "label": "价格查询",
        "side": "product",
        "minRole": "member", "sandbox": "readonly",
        "complianceTemplate": "product_policy",
        "slotSchema": ["keyword"],
        "confusableWith": ["product.new_query"],
        "status": "active",
    },
    # ---- trust 侧 ----
    "trust.balance_query": {
        "label": "余额查询",
        "side": "trust",
        "minRole": "member", "sandbox": "readonly",
        "complianceTemplate": "trust_policy",
        "slotSchema": [],
        "confusableWith": ["trust.convert_intent"],
        "status": "active",
    },
    "trust.score_query": {
        "label": "信值分查询",
        "side": "trust",
        "minRole": "member", "sandbox": "readonly",
        "complianceTemplate": "trust_policy",
        "slotSchema": [],
        "confusableWith": ["explanation.report_query"],
        "status": "active",
    },
    "trust.convert_intent": {
        "label": "兑换意图",
        "side": "trust",
        "minRole": "member", "sandbox": "sensitive",
        "complianceTemplate": "trust_convert",
        "slotSchema": ["amount"],
        "confusableWith": ["trust.balance_query"],
        "status": "active",
    },
    # ---- nav 侧 ----
    "nav.page_jump": {
        "label": "页面导航",
        "side": "nav",
        "minRole": "member", "sandbox": "readonly",
        "complianceTemplate": "nav_whitelist",
        "slotSchema": ["page"],
        "confusableWith": [],
        "status": "active",
    },
    # ---- other 侧 ----
    "promo.query": {
        "label": "优惠查询",
        "side": "other",
        "minRole": "member", "sandbox": "readonly",
        "complianceTemplate": "promo_policy",
        "slotSchema": ["keyword"],
        "confusableWith": [],
        "status": "active",
    },
    "chat.human_transfer": {
        "label": "转人工",
        "side": "other",
        "minRole": "member", "sandbox": "readonly",
        "complianceTemplate": "service_sop",
        "slotSchema": [],
        "confusableWith": [],
        "status": "active",
    },
    "explanation.report_query": {
        "label": "解读报告",
        "side": "other",
        "minRole": "member", "sandbox": "readonly",
        "complianceTemplate": "report_policy",
        "slotSchema": [],
        "confusableWith": ["trust.score_query"],
        "status": "active",
    },
    "general.help": {
        "label": "帮助",
        "side": "other",
        "minRole": "guest", "sandbox": "readonly",
        "complianceTemplate": "help_sop",
        "slotSchema": [],
        "confusableWith": [],
        "status": "active",
    },
    # ---- 元意图(特殊域) ----
    "boundary.unauthorized": {
        "label": "越界元意图",
        "side": "other",
        "minRole": "admin", "sandbox": "deny",
        "complianceTemplate": "boundary_reject",
        "slotSchema": [],
        "confusableWith": [],
        "status": "active",
    },
    "unknown.unrecognized": {
        "label": "未识别元意图",
        "side": "other",
        "minRole": "guest", "sandbox": "none",
        "complianceTemplate": "clarify_sop",
        "slotSchema": [],
        "confusableWith": [],
        "status": "active",
    },
}


def get_intent(intent_id: str) -> dict | None:
    """取意图定义"""
    return INTENT_REGISTRY.get(intent_id)


def active_intents() -> list[dict]:
    """全部 active 意图(评估可用域)"""
    return [dict(v, intentId=k) for k, v
            in INTENT_REGISTRY.items()
            if v.get("status") == "active"]


def registry_view() -> dict:
    """注册表自描述(观测面)"""
    sides: dict = {}
    by_sandbox: dict = {}
    for intent in INTENT_REGISTRY.values():
        sides[intent["side"]] = \
            sides.get(intent["side"], 0) + 1
        by_sandbox[intent["sandbox"]] = \
            by_sandbox.get(intent["sandbox"], 0) + 1
    # 易混淆对(双向对称对计数)
    confusables = set()
    for k, v in INTENT_REGISTRY.items():
        for target in v.get("confusableWith") or []:
            pair = tuple(sorted([k, target]))
            confusables.add(pair)
    return {
        "success": True,
        "modelVersion": MODEL_VERSION,
        "mode": current_mode(),
        "total": len(INTENT_REGISTRY),
        "bySide": sides,
        "bySandbox": by_sandbox,
        "confusablePairs": sorted(
            [list(p) for p in confusables]),
        "meta": {
            "sandboxLevels":
                list(SANDBOX_LEVELS),
            "roles": list(ROLE_VALUES),
            "sampleTypes": [
                "positive", "negative",
                "adversarial", "boundary"],
            "confidenceStates": [
                "resolved", "partial", "clarify"],
        },
        "intents": [
            {"intentId": k, **v}
            for k, v in INTENT_REGISTRY.items()],
        "modeValues": MODE_VALUES,
        "note": "意图注册表三位一体封闭白名单——"
                "意图×权限×合规模板×槽位(48号高频"
                " action 对齐纯读取)",
    }


def _validate_registry() -> None:
    """启动自检(RuntimeError 宪法级——52号范式)"""
    errors = []
    if len(INTENT_REGISTRY) != 12:
        errors.append(
            f"意图数量应为 12, 实际 "
            f"{len(INTENT_REGISTRY)}")
    # 四侧覆盖
    sides = {v.get("side")
             for v in INTENT_REGISTRY.values()}
    if sides != set(INTENT_SIDES):
        errors.append(f"四侧覆盖不齐: {sorted(sides)}")
    # confusableWith 双向对称+在册
    for iid, intent in INTENT_REGISTRY.items():
        if intent.get("sandbox") not in SANDBOX_LEVELS:
            errors.append(
                f"{iid}: 非法沙箱级 "
                f"{intent.get('sandbox')}")
        if intent.get("minRole") not in ROLE_VALUES:
            errors.append(
                f"{iid}: 非法 minRole "
                f"{intent.get('minRole')}")
        if not str(intent.get(
                "complianceTemplate") or "").strip():
            errors.append(
                f"{iid}: 合规模板为空")
        for target in intent.get(
                "confusableWith") or []:
            if target not in INTENT_REGISTRY:
                errors.append(
                    f"{iid}: 混淆目标 {target} "
                    f"不在册")
            else:
                # 双向对称(易混淆对声明互指)
                reverse = INTENT_REGISTRY[target].get(
                    "confusableWith") or []
                if iid not in reverse:
                    errors.append(
                        f"{iid}: 混淆对 {target} "
                        f"非双向对称")
        if intent.get("status") not in (
                "active", "pending", "retired"):
            errors.append(
                f"{iid}: 非法 status "
                f"{intent.get('status')}")
    if errors:
        raise RuntimeError(
            "ii58 INTENT_REGISTRY 自检失败: "
            + "; ".join(errors))
    logger.info(
        "ii58_registry_validated intents=%s sides=%s "
        "confusablePairs=%s",
        len(INTENT_REGISTRY), len(sides),
        len(registry_view()["confusablePairs"]))


_validate_registry()
