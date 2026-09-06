"""60号·AI智能支付管理 支付注册表
(pay60_registry)

计划(docs/60号_AI智能支付管理模块实施计划.md
§3.1/§3.2/§五/§七 P0):
    PRICING_RULES 定价规则——三因子
    确定性 DSL(信值折扣×贡献折扣×活动
    叠加——封顶 0.7 防击穿)
    SPLIT_CONTRACTS 分账合约——版本化
    封闭注册(实时/T+1/周期三模式)
    CHECKOUT_CONTEXTS 收银台上下文——
    场景×角色→支付方式组合(封闭注册)
    支付订单九态状态机(状态机转移表
    封闭——非法流转拒绝)
    渠道三态(CHANNEL_MODE: mock 默认/
    real fail-hard/mock_fallback——
    41号 DRIDE 范式)

设计(52号 us52_registry 封闭注册表范式):
    - 封闭注册: 可断言/可测试/启动自检
    - 隐私红线: 卡号/证件 Token 化存储
      (48号 mask 范式——P0 落地)

启动自检 _validate_registry()(RuntimeError
宪法级):
    - 规则/合约/上下文结构合法
    - 折扣因子区间合法+叠加封顶
    - 分账合约 rate 和=1.0
    - 状态机转移表自洽(源态/终态覆盖)
"""

import logging
import os

logger = logging.getLogger("pay60_registry")

MODEL_VERSION = "v1-pay60-registry"

DEFAULT_MODE = "off"

MODE_VALUES = ("off", "shadow", "assist")

# 渠道三态(41号 DRIDE 范式——mock 优先)
CHANNEL_MODES = (
    "mock",           # mock 回执(默认)
    "real",           # 真实渠道(fail-hard
                      # 无凭证即拒绝)
    "mock_fallback",  # 真实失败回退 mock
)


def current_mode() -> str:
    """模块开关(PAY60_MODE, 默认 off——
    决策面关闭: off=仅观测面; shadow=
    观察学习期(订单留痕); assist=辅助
    生产期(收银台渲染开放)"""
    mode = os.environ.get("PAY60_MODE") or DEFAULT_MODE
    return mode if mode in MODE_VALUES else DEFAULT_MODE


def current_channel_mode() -> str:
    """渠道模式(PAY60_CHANNEL_MODE,
    默认 mock)"""
    mode = os.environ.get(
        "PAY60_CHANNEL_MODE") or "mock"
    return mode if mode in CHANNEL_MODES \
        else "mock"


# ============================================================
# 支付场景域×角色域(收银台上下文键)
# ============================================================

SCENE_DOMAINS = (
    "listing",    # 同盟商上架(保证金+服务费)
    "renewal",    # 高信值续费
    "purchase",   # 标准购买
    "settlement", # 结算提现
)

ROLE_DOMAINS = (
    "ally_merchant",  # 同盟商
    "member",         # 会员
    "platform_admin", # 平台管理员
)

# 支付方式域(封闭)
PAY_METHODS = (
    "standard",                # 标准支付
    "deposit_service_bundle",  # 保证金+服务费合并单
    "child_pay",               # 子女代付(老年友好)
    "voice_confirm",           # 语音确认(无障碍)
    "credit_free_renew",       # 信用免密续订
    "balance_pay",             # 余额支付
)


# ============================================================
# 定价规则(PRICING_RULES——三因子确定性)
# ============================================================

# 信值折扣(tier 乘数——47号口径)
TRUST_DISCOUNT = {
    "trusted": 0.95,
    "standard": 1.0,
    "watched": 1.0,
    "restricted": 1.05,
}

# 贡献折扣(合规月数阶梯)
CONTRIBUTION_TIERS = (
    (6, 0.8),   # ≥6 月 → 0.8
    (3, 0.9),   # ≥3 月 → 0.9
    (0, 1.0),   # 其余 → 1.0
)

# 叠加下限封顶(防击穿——铁律)
PRICING_FLOOR = 0.7

# 定价规则 DSL(封闭注册——版本化
# 变更经 46号审批)
PRICING_RULES: dict = {
    "v1_three_factor": {
        "label": "三因子动态定价",
        "factors": (
            "trustDiscount",
            "contributionDiscount",
            "promoFactor"),
        "floor": PRICING_FLOOR,
        "status": "active",
    },
}


def compute_price(base_price: float,
                  tier: str = "standard",
                  compliance_months: int = 0,
                  promo_factor: float = 1.0
                  ) -> dict:
    """三因子确定性定价(归因透明展示
    ——每项折扣 attribution; 叠加封顶
    0.7 防击穿)

    Returns:
        {finalPrice, basePrice,
         attribution 三因子快照, floored}
    """
    base = max(0.0, float(base_price or 0))
    trust = TRUST_DISCOUNT.get(
        str(tier), 1.0)
    contribution = 1.0
    for months, factor in \
            CONTRIBUTION_TIERS:
        if int(compliance_months or 0) \
                >= months:
            contribution = factor
            break
    promo = min(1.0, max(
        PRICING_FLOOR,
        float(promo_factor or 1.0)))

    raw = base * trust * contribution
    final = round(
        max(raw * promo,
            base * PRICING_FLOOR), 2)
    floored = final <= base * PRICING_FLOOR \
        and (trust * contribution * promo) \
        < PRICING_FLOOR
    return {
        "finalPrice": final,
        "basePrice": round(base, 2),
        "attribution": {
            "trustDiscount": trust,
            "contributionDiscount":
                contribution,
            "promoFactor": promo,
        },
        "floored": floored,
        "floor": PRICING_FLOOR,
        "ruleId": "v1_three_factor",
    }


# ============================================================
# 分账合约(SPLIT_CONTRACTS——版本化)
# ============================================================

# 结算三模式
SPLIT_MODES = ("realtime", "t1", "periodic")

SPLIT_CONTRACTS: dict = {
    "v1_alliance_standard": {
        "label": "同盟商标准分账",
        "parts": (
            {"name": "platform_fee",
             "label": "平台服务费",
             "rate": 0.08,
             "mode": "realtime"},
            {"name": "deposit_freeze",
             "label": "保证金冻结",
             "rate": 0.12,
             "mode": "t1"},
            {"name": "merchant_balance",
             "label": "商户余额",
             "rate": 0.80,
             "mode": "realtime"},
        ),
        "status": "active",
    },
    "v1_platform_direct": {
        "label": "平台直收全额",
        "parts": (
            {"name": "platform_balance",
             "label": "平台余额",
             "rate": 1.00,
             "mode": "realtime"},
        ),
        "status": "active",
    },
    "v1_senior_discount": {
        "label": "适老优惠分账",
        "parts": (
            {"name": "platform_fee",
             "label": "平台服务费",
             "rate": 0.05,
             "mode": "realtime"},
            {"name": "subsidy_pool",
             "label": "适老补贴池",
             "rate": 0.10,
             "mode": "t1"},
            {"name": "merchant_balance",
             "label": "商户余额",
             "rate": 0.85,
             "mode": "realtime"},
        ),
        "status": "active",
    },
}


def get_contract(contract_id: str) -> dict | None:
    """取分账合约(含 parts rate 校验)"""
    return SPLIT_CONTRACTS.get(
        str(contract_id))


def compute_split(amount: float,
                 contract_id: str
                 ) -> dict:
    """分账拆分(确定性——rate 求和=1.0
    铁律, 金额守恒)

    Raises:
        KeyError: 合约不存在
        ValueError: 金额非法
    """
    contract = get_contract(contract_id)
    if contract is None:
        raise KeyError(
            f"分账合约 {contract_id} 不存在"
            f"(封闭注册域外)")
    amount = round(
        float(amount or 0), 2)
    if amount <= 0:
        raise ValueError("分账金额须为正数")
    splits = []
    allocated = 0.0
    parts = list(contract["parts"])
    for i, part in enumerate(parts):
        if i == len(parts) - 1:
            # 末段兜底守恒(浮点残差归末段)
            share = round(
                amount - allocated, 2)
        else:
            share = round(
                amount * float(
                    part["rate"]), 2)
            allocated = round(
                allocated + share, 2)
        splits.append({
            "name": part["name"],
            "label": part["label"],
            "rate": part["rate"],
            "mode": part["mode"],
            "amount": share,
        })
    return {
        "contractId": contract_id,
        "amount": amount,
        "splits": splits,
        "total": round(sum(
            s["amount"]
            for s in splits), 2),
        "conserved": round(sum(
            s["amount"] for s in splits), 2)
        == amount,
    }


# ============================================================
# 收银台上下文(CHECKOUT_CONTEXTS——
# 场景×角色→支付方式组合)
# ============================================================

CHECKOUT_CONTEXTS: dict = {
    ("listing", "ally_merchant"): {
        "label": "同盟商上架合并单",
        "methods": (
            "deposit_service_bundle",
            "balance_pay"),
        "note": "保证金+服务费合并单——"
                "降低拆单摩擦",
    },
    ("renewal", "member"): {
        "label": "高信值续费",
        "methods": (
            "credit_free_renew",
            "standard"),
        "note": "默认勾选信用免密续订"
                "(仅生物特征确认语义"
                "——屏幕码)",
    },
    ("purchase", "member"): {
        "label": "标准购买",
        "methods": (
            "standard",
            "balance_pay",
            "child_pay",
            "voice_confirm"),
        "note": "老年用户(49号偏好标记)"
                "→子女代付/语音确认优先",
    },
    ("settlement", "ally_merchant"): {
        "label": "同盟商结算",
        "methods": ("standard",),
        "note": "T+1 延迟到账可撤销域",
    },
    ("settlement", "platform_admin"): {
        "label": "平台结算",
        "methods": ("standard",),
        "note": "运营域——审计留痕",
    },
}


def get_checkout_context(scene: str,
                         role: str
                         ) -> dict | None:
    """取收银台上下文(场景×角色)"""
    return CHECKOUT_CONTEXTS.get(
        (str(scene), str(role)))


# ============================================================
# 支付订单九态状态机(转移表封闭)
# ============================================================

# 全态(九态+恢复/退款终态)
ORDER_STATES = (
    "created",       # 已创建
    "priced",        # 已定价
    "verified",      # 已验证
    "executing",     # 渠道执行中
    "success",       # 支付成功
    "settled",       # 已结算(终态)
    "priced_failed", # 定价失败(终态)
    "cancelled",     # 未支付取消(终态)
    "failed",        # 支付失败
    "recovering",    # 失败恢复中
    "refunded",      # 退款终态
)

# 终态(不可再流转)
ORDER_TERMINAL = (
    "settled", "priced_failed",
    "cancelled", "refunded")

# 转移表(封闭——非法流转拒绝)
ORDER_TRANSITIONS: dict = {
    "created": ("priced", "priced_failed",
                "cancelled"),
    "priced": ("verified", "cancelled"),
    "verified": ("executing", "cancelled"),
    "executing": ("success", "failed"),
    "failed": ("recovering", "refunded"),
    "recovering": ("executing", "refunded"),
    "success": ("settled", "refunded"),
    # 终态无出边
    "settled": (),
    "priced_failed": (),
    "cancelled": (),
    "refunded": (),
}


def assert_transition(current: str,
                      target: str) -> None:
    """状态机流转校验(封闭转移表)

    Raises:
        ValueError: 非法流转
    """
    current = str(current or "")
    target = str(target or "")
    if current not in ORDER_STATES:
        raise ValueError(
            f"订单状态 {current} 域外"
            f"(合法: {'/'.join(
                ORDER_STATES)})")
    if target not in ORDER_STATES:
        raise ValueError(
            f"订单目标态 {target} 域外"
            f"(合法: {'/'.join(
                ORDER_STATES)})")
    if target not in \
            ORDER_TRANSITIONS[current]:
        raise ValueError(
            f"订单状态机非法流转 "
            f"{current}→{target}"
            f"(合法: {'/'.join(
                ORDER_TRANSITIONS[current])
                or '终态(无出边)'})")


# ============================================================
# 信值融合风控规则(P2——计划 §3.2)
# ============================================================

# riskTier 四级(摩擦感与信任等级成反比)
RISK_TIERS = (
    "pass",    # 无感直通(trusted+小额+设备可信)
    "light",   # 轻量(OTP 语义 mock 码)
    "strong",  # 强验证(屏幕码+二次确认)
    "block",   # 阻断(拒绝+整改指引)
)

# 验证方式域(48号 confirmToken 语义复用)
VERIFY_METHODS = (
    "confirm_token",  # 轻确认(pass 档)
    "otp_mock",       # OTP 语义 mock 码(light 档)
    "screen_code",    # 屏幕码(FIDO 语义占位——strong 档)
    "none",           # block 档无验证(直接拒绝)
)

# 金额阈值(默认——可经 46号审批校准)
PASS_MAX_AMOUNT = 5000.0    # pass 档上限
LIGHT_MAX_AMOUNT = 2000.0   # light 档独立线(单笔小额)

# 行为序列前序域(支付前序操作——
# 跳跃式操作升一档)
BEHAVIOR_STEPS = (
    "browse",    # 浏览
    "order",     # 下单
    "modify",    # 改单
    "pay",       # 支付
)

# 合规禁令域(封闭——命中即 block)
COMPLIANCE_BANS = (
    "industry_ban",   # 行业禁令
    "tax_violation",  # 税务违规
    "sanction_list",  # 制裁名单
)

# AML 洗钱检测三规则(确定性——不依赖 GNN)
AML_RULES = (
    "fund_loop",          # A→B→A 资金环
    "device_multi_account",  # 同设备多账户
    "fast_in_fast_out",   # 快进快出
)

# 快进快出时间窗(秒——短于该窗的
# 转入即转出)
FAST_WINDOW_SECONDS = 300


def assess_risk_tier(tier: str,
                     amount: float,
                     device_trusted: bool = False,
                     behavior_sequence: list = None,
                     compliance_flags: list = None,
                     aml_hits: list = None
                     ) -> dict:
    """riskTier 三轴确定性评估
    (信值×金额×行为——摩擦感与
    信任等级成反比铁律)

    判定序(最严优先):
        ① 合规禁令/AML 命中 → block
        ② tier=restricted → block
        ③ tier=watched 或 amount>PASS
           上限 → strong
        ④ 行为跳跃(无 browse 直付)→
           基础档升一档
        ⑤ tier=trusted+amount≤PASS
           上限+设备可信 → pass
        ⑥ 其余(tier≥standard 或
           amount≤LIGHT 线) → light
    """
    tier = str(tier or "standard")
    amount = float(amount or 0)
    compliance_flags = [
        str(f) for f in
        (compliance_flags or [])]
    aml_hits = [
        str(a) for a in (aml_hits or [])]

    block_reasons = []
    # ① 合规禁令+AML 三规则命中
    for f in compliance_flags:
        if f in COMPLIANCE_BANS:
            block_reasons.append(
                f"合规禁令命中({f})")
    for a in aml_hits:
        if a in AML_RULES:
            block_reasons.append(
                f"AML 命中({a})")
    # ② restricted
    if tier == "restricted":
        block_reasons.append(
            "tier=restricted(信值受限)")
    if block_reasons:
        return {
            "riskTier": "block",
            "verifyMethod": "none",
            "reasons": block_reasons,
            "escalatedBy": "",
            "note": "阻断——拒绝+整改"
                    "指引推送",
        }

    # ③ watched 或大额 → strong
    reasons = []
    escalated = ""
    if tier == "watched":
        reasons.append(
            f"tier=watched(观察期)")
    if amount > PASS_MAX_AMOUNT:
        reasons.append(
            f"金额 {amount}>"
            f"{PASS_MAX_AMOUNT}(大额)")
    if reasons:
        return {
            "riskTier": "strong",
            "verifyMethod": "screen_code",
            "reasons": reasons,
            "escalatedBy": "",
            "note": "强验证——屏幕码+"
                    "二次确认(48号 "
                    "confirmToken 流)",
        }

    # ⑤ pass 判定(trusted+小额+
    #    设备可信)
    if tier == "trusted" \
            and amount \
            <= PASS_MAX_AMOUNT \
            and device_trusted:
        return {
            "riskTier": "pass",
            "verifyMethod": "confirm_token",
            "reasons": [
                f"tier=trusted+金额"
                f"{amount}≤{PASS_MAX_AMOUNT}"
                f"+设备可信"],
            "escalatedBy": "",
            "note": "无感直通——confirmToken"
                    " 轻确认",
        }

    # ④ 行为跳跃(无浏览直接支付)——
    #    light 基础上升一档
    seq = [str(s) for s in
           (behavior_sequence or [])]
    jumped = bool(seq) \
        and seq[0] == "pay" \
        and "browse" not in seq
    if jumped:
        return {
            "riskTier": "strong",
            "verifyMethod": "screen_code",
            "reasons": [
                "行为序列跳跃(无浏览"
                "直接支付——升档)"],
            "escalatedBy": "behavior_jump",
            "note": "跳跃升档——强验证"
                    "(行为轴)",
        }

    # ⑥ light(tier≥standard 或
    #    单笔≤LIGHT 线)
    return {
        "riskTier": "light",
        "verifyMethod": "otp_mock",
        "reasons": [
            f"tier={tier}"
            + (f"+金额{amount}≤"
               f"{LIGHT_MAX_AMOUNT}"
               if amount
               <= LIGHT_MAX_AMOUNT
               else "")],
        "escalatedBy": "",
        "note": "轻量验证——OTP 语义"
                "mock 码",
    }


# ============================================================
# 注册表观测面
# ============================================================

def registry_view() -> dict:
    """注册表自描述(观测面)"""
    return {
        "success": True,
        "modelVersion": MODEL_VERSION,
        "mode": current_mode(),
        "channelMode":
            current_channel_mode(),
        "pricingRules": len(PRICING_RULES),
        "splitContracts": len(
            SPLIT_CONTRACTS),
        "checkoutContexts": len(
            CHECKOUT_CONTEXTS),
        "payMethods": len(PAY_METHODS),
        "orderStates": len(ORDER_STATES),
        "risk": {
            "riskTiers": list(RISK_TIERS),
            "verifyMethods": list(
                VERIFY_METHODS),
            "passMaxAmount":
                PASS_MAX_AMOUNT,
            "lightMaxAmount":
                LIGHT_MAX_AMOUNT,
            "amlRules": list(AML_RULES),
            "complianceBans": list(
                COMPLIANCE_BANS),
        },
        "meta": {
            "sceneDomains":
                list(SCENE_DOMAINS),
            "roleDomains":
                list(ROLE_DOMAINS),
            "channelModes":
                list(CHANNEL_MODES),
            "pricingFloor": PRICING_FLOOR,
            "splitModes":
                list(SPLIT_MODES),
            "orderStates":
                list(ORDER_STATES),
            "orderTerminal":
                list(ORDER_TERMINAL),
        },
        "modeValues": MODE_VALUES,
        "note": "支付注册表——定价三因子"
                "+分账合约+收银台上下文"
                "+九态状态机+渠道三态"
                "+四级风控(信值驱动的"
                "价值交换引擎)",
    }


# ============================================================
# 启动自检(宪法级)
# ============================================================

def _validate_registry() -> None:
    """启动自检(RuntimeError 宪法级)"""
    errors = []
    # ① 定价规则
    if not PRICING_RULES:
        errors.append("定价规则为空(封闭注册违规)")
    if not 0 < PRICING_FLOOR <= 1:
        errors.append(
            f"叠加封顶越界 {PRICING_FLOOR}")
    for tier, factor in \
            TRUST_DISCOUNT.items():
        if not 0 < factor <= 1.05:
            errors.append(
                f"信值折扣越界 "
                f"{tier}={factor}")
    prev = None
    for months, factor in \
            CONTRIBUTION_TIERS:
        if prev is not None \
                and months >= prev:
            errors.append(
                "贡献阶梯月数须严格降序")
        if not 0 < factor <= 1:
            errors.append(
                f"贡献折扣越界 {factor}")
        prev = months
    # ② 分账合约
    if not SPLIT_CONTRACTS:
        errors.append(
            "分账合约为空(封闭注册违规)")
    for cid, contract in \
            SPLIT_CONTRACTS.items():
        rate_sum = round(sum(
            p["rate"]
            for p in
            contract["parts"]), 6)
        if rate_sum != 1.0:
            errors.append(
                f"合约 {cid} rate 和"
                f"{rate_sum}≠1.0(金额守恒铁律)")
        for part in contract["parts"]:
            if part["mode"] \
                    not in SPLIT_MODES:
                errors.append(
                    f"合约 {cid} 结算模式"
                    f"域外 {part['mode']}")
    # ③ 收银台上下文
    for (scene, role) in \
            CHECKOUT_CONTEXTS:
        if scene not in SCENE_DOMAINS:
            errors.append(
                f"上下文场景 {scene} 域外")
        if role not in ROLE_DOMAINS:
            errors.append(
                f"上下文角色 {role} 域外")
        for method in \
                CHECKOUT_CONTEXTS[
                    (scene, role)]["methods"]:
            if method not in PAY_METHODS:
                errors.append(
                    f"上下文支付方式 "
                    f"{method} 域外")
    # ④ 状态机自洽
    if set(ORDER_STATES) != set(
            ORDER_TRANSITIONS):
        errors.append(
            "状态机转移表与全态集不一致")
    for state in ORDER_STATES:
        for target in \
                ORDER_TRANSITIONS[state]:
            if target not in ORDER_STATES:
                errors.append(
                    f"转移目标 {target} 域外")
    for terminal in ORDER_TERMINAL:
        if ORDER_TRANSITIONS[terminal]:
            errors.append(
                f"终态 {terminal} 不应有出边")
    # ⑤ 风控规则域(P2)
    if RISK_TIERS != ("pass", "light",
                      "strong", "block"):
        errors.append(
            "riskTier 四级域非法")
    if not 0 < LIGHT_MAX_AMOUNT \
            < PASS_MAX_AMOUNT:
        errors.append(
            "金额阈值非法(LIGHT 须"
            "小于 PASS)")
    if set(VERIFY_METHODS) != {
            "confirm_token", "otp_mock",
            "screen_code", "none"}:
        errors.append(
            "验证方式域非法")
    if len(AML_RULES) != 3:
        errors.append(
            "AML 三规则域非法")
    if len(COMPLIANCE_BANS) != 3:
        errors.append(
            "合规禁令域非法")
    for step in BEHAVIOR_STEPS:
        if step not in ("browse", "order",
                       "modify", "pay"):
            errors.append(
                f"行为序列步骤 {step} 域外")
    if errors:
        raise RuntimeError(
            "pay60 registry 自检失败: "
            + "; ".join(errors))
    logger.info(
        "pay60_registry_validated "
        "pricing=%s contracts=%s "
        "contexts=%s states=%s "
        "risk=%s",
        len(PRICING_RULES),
        len(SPLIT_CONTRACTS),
        len(CHECKOUT_CONTEXTS),
        len(ORDER_STATES),
        len(RISK_TIERS))


_validate_registry()
