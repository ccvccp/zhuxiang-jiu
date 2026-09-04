"""49号·小竹可信函数调用深化 工具注册表 v2 + 降级话术表

计划(docs/49号_小竹可信函数调用深化实施计划.md §五 ①):
    Tool Registry v2——每个工具的注册结构对齐 OpenAPI 3.1 语义:
    - description 内嵌行为禁令(❌)与隐私成本(🔒)——注入 LLM
      System Prompt 后, 模型在推理阶段即感知约束(约束内化铁律)
    - 三级分级继承 48号沙箱白名单(readonly/write/sensitive)
    - privacy_cost: 只读 0-0.02 / 写 0.02-0.05 / 高敏 0.08
    - 失败安全降级: 每工具预定义 safe_message(不暴露内部细节)
      + 人工转接选项(chat.human 绑定——48号既有指令复用)

工具全集(13 = 48号 12 指令 + 新增修复执行):
    只读(10): product.new/product.price/trust.score/trust.balance/
              trust.exchange/trust.repair(=repair.plan)/promo.query/
              nav.page/chat.human/xiaozhu.help
    写(2): trust.bind/cart.submit
    高敏(2): trust.convert/repair.execute

设计红线(计划 §八, 48号六条全继承 + 新增三条):
    - 约束内化: 描述即护栏(不靠后端硬拦独自兜底)
    - 预算均等: privacy_cost 只按工具性质分级, 绝不与
      会员信值等级挂钩(公平性红线)
    - LLM 不产数字推广为不编结果: 失败只走 safe_message
"""

import logging

logger = logging.getLogger("xiaozhu_fc_registry")

# 三级分级(与 xiaozhu_executor 沙箱白名单一一对应)
TIER_READONLY = "readonly"
TIER_WRITE = "write"
TIER_SENSITIVE = "sensitive"

# 人工转接选项(失败安全降级标配——48号 chat.human 复用)
_HUMAN_ESCORT = ("如需协助, 可说「转人工客服」"
                 "由真人客服为您处理")

# 统一安全兜底话术(内部错误——不暴露实现细节)
SAFE_GENERIC = ("这项操作暂时未能完成, 已记录您的问题。"
                + _HUMAN_ESCORT)


# ============================================================
# 工具注册表 v2(计划 §五 ①——描述内嵌禁令+隐私成本)
# ============================================================

TOOL_REGISTRY = {
    # ---------- 只读(10) ----------
    "product.new": {
        "operationId": "list_new_products",
        "summary": "查看新上架产品",
        "description": "【只读】当用户说「看新品/有什么新货」"
                      "时调用。❌ 不接受任何写参数。",
        "tier": TIER_READONLY,
        "privacyCost": 0.01,
        "requiresConsent": False,
        "safeMessage": "新品列表暂时没取到(数据源波动), "
                      "请稍后再试。",
    },
    "product.price": {
        "operationId": "query_product_price",
        "summary": "查询产品价格",
        "description": "【只读】当用户询问某商品价格时调用。"
                      "❌ 价格数字必须来自本工具返回, 禁止自行"
                      "估算。",
        "tier": TIER_READONLY,
        "privacyCost": 0.01,
        "requiresConsent": False,
        "safeMessage": "价格暂时没查到, 请稍后再试。",
    },
    "trust.score": {
        "operationId": "get_trust_score",
        "summary": "查询信值总分及三层明细",
        "description": "【只读】当用户询问「我的信值/信用"
                      "怎么样」时调用, 返回三层明细与 7 日"
                      "趋势。❌ 不可用于金融授信决策参考。"
                      "🔒 privacy_cost: 0.02",
        "tier": TIER_READONLY,
        "privacyCost": 0.02,
        "requiresConsent": False,
        "safeMessage": "信值档案暂时读取失败, 请稍后再试。",
    },
    "trust.balance": {
        "operationId": "get_trust_balance",
        "summary": "查询信值资产余额",
        "description": "【只读】当用户询问「信值余额/还剩"
                      "多少信值」时调用。🔒 privacy_cost: 0.02",
        "tier": TIER_READONLY,
        "privacyCost": 0.02,
        "requiresConsent": False,
        "safeMessage": "余额暂时读取失败, 请稍后再试。",
    },
    "trust.exchange": {
        "operationId": "get_exchange_rate",
        "summary": "信值兑换换算(只算不换)",
        "description": "【只读】当用户问「能换吗/信值够吗」"
                      "时调用。❌ 本工具只做换算展示, 不执行"
                      "兑换——执行须走 convert_credit_to_"
                      "trust 双因子确认。",
        "tier": TIER_READONLY,
        "privacyCost": 0.01,
        "requiresConsent": False,
        "safeMessage": "汇率暂时没取到, 请稍后再试。",
    },
    "trust.repair": {
        "operationId": "get_repair_window",
        "summary": "查询修复窗口与 β 最优修复推荐",
        "description": "【只读】当用户问「怎么修复/修复窗口」"
                      "时调用, 返回违规项与高 β 针对性修复"
                      "建议。❌ 只展示计划, 执行修复须走 "
                      "execute_repair_action 双因子确认。"
                      "🔒 privacy_cost: 0.02",
        "tier": TIER_READONLY,
        "privacyCost": 0.02,
        "requiresConsent": False,
        "safeMessage": "修复计划暂时读取失败, 请稍后再试。",
    },
    "promo.query": {
        "operationId": "query_promotions",
        "summary": "查询当前优惠活动",
        "description": "【只读】当用户询问「优惠/活动/折扣」"
                      "时调用。",
        "tier": TIER_READONLY,
        "privacyCost": 0.01,
        "requiresConsent": False,
        "safeMessage": "优惠信息暂时没取到, 请稍后再试。",
    },
    "nav.page": {
        "operationId": "navigate_page",
        "summary": "页面导航",
        "description": "【只读】当用户说「打开购物车/去个人"
                      "中心」时调用, 返回跳转路径。"
                      "❌ 只导航既有页面, 不执行页面内操作。",
        "tier": TIER_READONLY,
        "privacyCost": 0.01,
        "requiresConsent": False,
        "safeMessage": "没听清要去哪个页面——支持: 购物车/"
                      "订单/个人中心/产品列表/信值看板。",
    },
    "chat.human": {
        "operationId": "transfer_human_agent",
        "summary": "转接人工客服",
        "description": "【只读】当用户要求人工服务, 或任何"
                      "工具失败后用户需要协助时调用。"
                      "🔒 privacy_cost: 0(兜底出口零成本——"
                      "失败降级红线)。",
        "tier": TIER_READONLY,
        "privacyCost": 0.0,
        "requiresConsent": False,
        "safeMessage": "转接暂时不可用, 请稍后再试。",
    },
    "xiaozhu.help": {
        "operationId": "describe_capabilities",
        "summary": "能力自描述",
        "description": "【只读】当用户问「你能干什么」时调用。"
                      "🔒 privacy_cost: 0。",
        "tier": TIER_READONLY,
        "privacyCost": 0.0,
        "requiresConsent": False,
        "safeMessage": "帮助信息暂时不可用。",
    },
    "privacy.budget": {
        "operationId": "get_privacy_budget",
        "summary": "查询隐私预算余额与偏好",
        "description": "【只读】当用户问「我的隐私预算/隐私"
                      "偏好」时调用, 返回余额/偏好/近 7 日"
                      "消耗。❌ 预算只按用户自主偏好分级, "
                      "禁止与信值等级挂钩(公平性红线)。"
                      "🔒 privacy_cost: 0(知情权零成本——"
                      "永不降级)。",
        "tier": TIER_READONLY,
        "privacyCost": 0.0,
        "requiresConsent": False,
        "safeMessage": "预算信息暂时读取失败, 请稍后再试。",
    },
    # ---------- 写(2) ----------
    "trust.bind": {
        "operationId": "bind_trust_profile",
        "summary": "绑定会员与信值档案",
        "description": "【写操作(48号沙箱归只读通道——绑定走"
                      "专用会话流)】当用户说「绑定信值档案 N」"
                      "时调用。❌ 绑定须会员本人发起, "
                      "禁止跨会员代理绑定。🔒 privacy_cost: 0.02",
        "tier": TIER_READONLY,   # 沙箱口径对齐(SAFE_READONLY)
        "privacyCost": 0.02,
        "requiresConsent": False,   # 绑定非高敏(P1 口径)
        "safeMessage": "绑定暂时未能完成, 请确认档案号后"
                       "重试。" + _HUMAN_ESCORT,
    },
    "cart.submit": {
        "operationId": "submit_checkout",
        "summary": "结算下单",
        "description": "【写操作】当用户确认购买说「结算/"
                      "下单」时调用。❌ 禁止无商品语境时"
                      "调用; 金额数字须来自结算引擎返回。"
                      "🔒 privacy_cost: 0.05",
        "tier": TIER_WRITE,
        "privacyCost": 0.05,
        "requiresConsent": False,   # 一般写: 执行+播报(P2 口径)
        "safeMessage": "结算暂时未能完成, 购物车未受影响, "
                       "请稍后再试。" + _HUMAN_ESCORT,
    },
    # ---------- 高敏(2) ----------
    "trust.convert": {
        "operationId": "convert_credit_to_trust",
        "summary": "信用分兑换信值",
        "description": "【写操作·高危】仅在用户明确说"
                      "「确认兑换」并通过双因子确认"
                      "(语音确认词+屏幕码)获得有效 "
                      "consent_token 时调用。❌ 禁止自动"
                      "触发; ❌ 禁止在无 token 时调用; "
                      "❌ 禁止自动串联多笔。汇率与到账数字"
                      "必须来自工具返回。"
                      "🔒 privacy_cost: 0.08",
        "tier": TIER_SENSITIVE,
        "privacyCost": 0.08,
        "requiresConsent": True,
        "consentPhrase": "确认兑换信用分",
        "safeMessage": "兑换暂时未能完成, 信用分未扣除, "
                       "请稍后再试。" + _HUMAN_ESCORT,
    },
    "repair.execute": {
        "operationId": "execute_repair_action",
        "summary": "执行信值修复对冲操作",
        "description": "【写操作·高危】仅在用户明确确认修复"
                      "并双因子核验(语音确认词+屏幕码)获得"
                      "有效 consent_token 时调用。❌ 禁止自动"
                      "触发; ❌ 禁止在无 token 时调用; "
                      "❌ 永久熔断(criminal)档案不可修复。"
                      "修复值/新信值数字必须来自工具返回。"
                      "🔒 privacy_cost: 0.08",
        "tier": TIER_SENSITIVE,
        "privacyCost": 0.08,
        "requiresConsent": True,
        "consentPhrase": "确认执行修复",
        "safeMessage": "修复操作暂时未能完成, 信值未变动, "
                       "已记录您的问题。" + _HUMAN_ESCORT,
    },
}


def get_tool(action: str) -> dict | None:
    """取工具定义(action 同名对齐 48号指令集)"""
    return TOOL_REGISTRY.get(action)


def tool_actions() -> tuple:
    """全部工具 action 元组(与 48号 COMMAND_ACTIONS 对齐基线)"""
    return tuple(TOOL_REGISTRY.keys())


def build_tool_prompt() -> str:
    """构建注入 LLM System Prompt 的工具描述块

    约束内化铁律(计划 §八 7): 禁令(❌)与隐私成本(🔒)随
    描述注入——模型在推理阶段即感知约束, 而非仅靠后端拦截。
    """
    lines = ["你可以调用以下工具(严格按 description 约束使用):"]
    for action, t in TOOL_REGISTRY.items():
        lines.append(
            f"- {t['operationId']}(action={action}, "
            f"分级={t['tier']}, "
            f"privacy_cost={t['privacyCost']}): "
            f"{t['description']}")
    lines.append(
        "规则: ①只从上述工具中选择, 回答 JSON "
        '{"action": "..."} 或 null; '
        "②requiresConsent=true 的工具在未确认时不可选, "
        "回复需要确认的提示; ③禁止编造工具结果。")
    return "\n".join(lines)


def safe_message_of(action: str) -> str:
    """取工具的安全兜底话术(失败降级铁律——不编结果)"""
    t = TOOL_REGISTRY.get(action)
    return (t or {}).get("safeMessage") or SAFE_GENERIC


def audit_fields(action: str) -> dict:
    """工具的审计静态字段(FC 审计流水用)"""
    t = TOOL_REGISTRY.get(action) or {}
    return {"toolName": t.get("operationId") or action,
            "tier": t.get("tier") or "readonly",
            "privacyCost": t.get("privacyCost") or 0.0}


# ============================================================
# 一致性校验(注册表 ↔ 48号指令集沙箱对齐)
# ============================================================

def _validate_registry() -> None:
    """启动期自检: 注册表三级分级须与 48号沙箱白名单一致

    readonly ⊆ SAFE_READONLY / write ⊆ SAFE_WRITE /
    sensitive ⊆ SENSITIVE(方向性: 沙箱是执行边界, 注册表
    是认知边界, 执行边界不得窄于认知边界)。
    """
    from services.xiaozhu_executor import (
        SAFE_READONLY, SAFE_WRITE, SENSITIVE,
    )
    for action, t in TOOL_REGISTRY.items():
        tier = t["tier"]
        if tier == TIER_READONLY and action not in \
                SAFE_READONLY:
            raise RuntimeError(
                f"FC 注册表不一致: {action} 声明 readonly "
                f"但不在沙箱只读白名单")
        if tier == TIER_WRITE and action not in SAFE_WRITE:
            raise RuntimeError(
                f"FC 注册表不一致: {action} 声明 write "
                f"但不在沙箱写白名单")
        if tier == TIER_SENSITIVE and action \
                not in SENSITIVE:
            raise RuntimeError(
                f"FC 注册表不一致: {action} 声明 sensitive "
                f"但不在沙箱高敏白名单")


_validate_registry()
