"""69号·AI智能支付大模型 通道认知注册表
(pay69_registry, P0)

规划(docs/69号_AI智能支付大模型_创新规划方案.md
§七 P0):
    七通道注册表(封闭字典——费率/限额/
    到账时效/健康度观测口径)
    +健康度状态域
    +意图标签域(支付方式偏好扩展)
    +模式三态(off/shadow/assist——默认 off)

设计(60号 pay60_registry 封闭注册表
范式平移):
    - 封闭注册: 可断言/可测试/启动自检
    - 69号=支付大模型(路由/熵/授信),
      消费 60号归因链, 永不修改 60号数据
    - 渠道凭证 .env 注入不落盘(铁律)

启动自检 _validate_registry()(RuntimeError
宪法级):
    - 通道字典结构合法(七通道齐备/
      费率区间合法/限额>0)
    - 健康度状态域封闭
    - 意图标签→通道映射封闭(域内通道
      必须在注册表)
"""

import logging
import os

logger = logging.getLogger("pay69_registry")

MODEL_VERSION = "v1-pay69-registry"

DEFAULT_MODE = "off"

MODE_VALUES = ("off", "shadow", "assist")


def current_mode() -> str:
    """模块开关(PAY69_MODE, 默认 off——
    决策面关闭: off=仅观测面; shadow=
    影子学习期(路由/熵只留痕); assist=
    辅助生产期(建议注入收银台)"""
    mode = os.environ.get("PAY69_MODE") or DEFAULT_MODE
    return mode if mode in MODE_VALUES else DEFAULT_MODE


# ============================================================
# 七通道注册表(封闭——69号大模型认知域)
# ============================================================

CHANNEL_IDS = (
    "qr",            # 智能二维码(55号联动)
    "wechat",        # 微信支付
    "alipay",        # 支付宝支付
    "bank",          # 银行卡支付
    "unionpay",      # 云闪付
    "credit_tv",    # 闪银信用(45号 TV 资金源)
    "biometric",     # 生物特征(刷脸/指纹)
)

# 费率单位: 金额乘数(如 0.006 = 千六)
# 限额单位: 元(单笔)
CHANNEL_REGISTRY: dict = {
    "qr": {
        "label": "智能二维码",
        "feeRate": 0.0038,
        "singleLimit": 50000.0,
        "dailyLimit": 100000.0,
        "settleHours": 24,
        "traits": ("offline", "privacy", "universal"),
    },
    "wechat": {
        "label": "微信支付",
        "feeRate": 0.006,
        "singleLimit": 50000.0,
        "dailyLimit": 200000.0,
        "settleHours": 2,
        "traits": ("social", "fast", "universal"),
    },
    "alipay": {
        "label": "支付宝支付",
        "feeRate": 0.0055,
        "singleLimit": 50000.0,
        "dailyLimit": 200000.0,
        "settleHours": 2,
        "traits": ("fast", "universal", "ecosystem"),
    },
    "bank": {
        "label": "银行卡支付",
        "feeRate": 0.0045,
        "singleLimit": 100000.0,
        "dailyLimit": 500000.0,
        "settleHours": 4,
        "traits": ("large_amount", "secure", "slow"),
    },
    "unionpay": {
        "label": "云闪付",
        "feeRate": 0.003,
        "singleLimit": 50000.0,
        "dailyLimit": 200000.0,
        "settleHours": 3,
        "traits": ("bank_card", "promotion", "fast"),
    },
    "credit_tv": {
        "label": "闪银信用(信值抵扣)",
        "feeRate": 0.0,
        "singleLimit": 10000.0,
        "dailyLimit": 30000.0,
        "settleHours": 0,
        "traits": ("internal", "trust_gated", "instant"),
    },
    "biometric": {
        "label": "生物特征(刷脸/指纹)",
        "feeRate": 0.006,
        "singleLimit": 5000.0,
        "dailyLimit": 20000.0,
        "settleHours": 2,
        "traits": ("fast", "strong_auth", "young"),
    },
}


# ============================================================
# 健康度状态域(观测面口径)
# ============================================================

HEALTH_STATES = (
    "healthy",     # 健康(成功率 ≥ 0.99)
    "degraded",    # 降级(0.90 ≤ 成功率 < 0.99)
    "critical",    # 危急(成功率 < 0.90)
    "frozen",      # 冻结(人工/制动——路由永不选中)
)

# 健康度分级阈值(确定性——快环统计
# 滚动窗口口径, 阈值变更走慢环审批)
HEALTH_THRESHOLDS = {
    "healthy": 0.99,
    "degraded": 0.90,
}


def health_of(success_rate: float) -> str:
    """成功率 → 健康度状态(确定性映射)

    frozen 不可由成功率导出(仅人工/
    PAY69_KILL 制动)——铁律: 冻结通道
    路由永不选中
    """
    rate = float(success_rate or 0)
    if rate >= HEALTH_THRESHOLDS["healthy"]:
        return "healthy"
    if rate >= HEALTH_THRESHOLDS["degraded"]:
        return "degraded"
    return "critical"


# ============================================================
# 意图标签域(60号归因链消费——P0 扩展)
# ============================================================

# 意图标签(封闭八类——LLM 仅澄清,
# 归属判定为规则轨)
INTENT_TAGS = (
    "fast_needed",      # 求快(时效优先)
    "large_amount",     # 大额(安全优先)
    "privacy_needed",   # 求隐私(二维码优先)
    "social_sharing",   # 社交(微信生态)
    "promo_hunting",    # 捡漏(优惠优先)
    "credit_preference",  # 信用偏好(闪银)
    "biometric_habit",   # 生物习惯(年轻)
    "default",          # 无显式偏好
)

# 意图标签 → 候选通道亲和(封闭映射——
# 值域必须在 CHANNEL_IDS 注册表内)
INTENT_AFFINITY: dict = {
    "fast_needed": ("wechat", "alipay", "biometric"),
    "large_amount": ("bank", "unionpay"),
    "privacy_needed": ("qr",),
    "social_sharing": ("wechat",),
    "promo_hunting": ("unionpay", "qr"),
    "credit_preference": ("credit_tv",),
    "biometric_habit": ("biometric",),
    "default": ("qr", "wechat", "alipay"),
}


# ============================================================
# 路由评分权重(P1——封闭注册, 变更走
# 慢环 46号审批)
# ============================================================

ROUTE_WEIGHTS: dict = {
    "fee": 0.15,       # 费率因子(越低越优)
    "health": 0.35,    # 健康度因子(滚动窗口)
    "affinity": 0.35,  # 意图亲和因子(对齐主导)
    "habit": 0.15,     # 会员习惯因子
}

# 滚动窗口容量(快环——每通道最近 N 次执行)
ROUTE_WINDOW_SIZE = 50

# 费率归一化上限(注册表最高费率 0.006)
MAX_FEE_RATE = 0.006


# ============================================================
# P2 风险熵引擎(六轴确定性——LLM 禁入)
# ============================================================

# 六轴域(封闭——熵计算确定性查表)
ENTROPY_AXES = (
    "amount",       # 金额轴(越大熵越高)
    "trust",        # 信值轴(45号等级——越高熵越低)
    "behavior",     # 行为轴(基线偏离度)
    "environment",  # 环境轴(设备/时段/地点)
    "channel",      # 通道轴(通道风险等级)
    "history",      # 历史轴(支付异常史)
)

# 六轴权重(封闭注册——变更走慢环 46号
# 审批; 历史误拦截回流→轴权重建议书)
ENTROPY_WEIGHTS: dict = {
    "amount": 0.20,
    "trust": 0.25,
    "behavior": 0.20,
    "environment": 0.15,
    "channel": 0.10,
    "history": 0.10,
}

# 熵值→认证步进梯度(封闭四档——
# 摩擦感与信任等级成反比铁律, 60号
# P2 惯例继承)
STEP_LADDER = (
    ("free", 0.30, "免密支付", "light"),
    ("otp", 0.50, "短信验证码+滑动验证", "standard"),
    ("biometric", 0.70, "生物特征强验证", "strong"),
    ("dual", 1.01, "双人复核(高安全)", "enhanced"),
)

# 60号 riskTier 语义对齐声明(纯映射
# 消费——60号代码零改动)
# free↔light / otp↔standard /
# biometric↔strong / dual↔enhanced

# 金额轴分档(封闭——确定性查表)
AMOUNT_BANDS = (
    (100, 0.10),      # ≤¥100 → 0.10
    (1000, 0.30),     # ≤¥1000 → 0.30
    (5000, 0.50),     # ≤¥5000 → 0.50
    (20000, 0.70),    # ≤¥20000 → 0.70
    (float("inf"), 0.95),  # >¥20000 → 0.95
)

# 信值轴分档(45号 tier 口径)
TRUST_BANDS = {
    "S": 0.10, "A": 0.30, "B": 0.60,
    "C": 0.60, "D": 0.90,
    "trusted": 0.10, "standard": 0.30,
    "watched": 0.60, "restricted": 0.90,
}

# 通道轴风险等级(封闭映射)
CHANNEL_RISK = {
    "biometric": 0.20,
    "bank": 0.30,
    "wechat": 0.40,
    "alipay": 0.40,
    "unionpay": 0.40,
    "qr": 0.50,
    "credit_tv": 0.60,
}


# ============================================================
# P3 交易级授信(确定性规则表——LLM 禁定价)
# ============================================================

# 授信评级域(封闭五档)
CREDIT_GRADES = (
    "excellent",  # 优秀(0.80+)
    "good",       # 良好(0.65-0.80)
    "fair",       # 一般(0.50-0.65)
    "cautious",   # 谨慎(0.35-0.50)
    "rejected",   # 拒绝(<0.35)
)

# 授信因子域(封闭四轴——评估确定性查表)
CREDIT_FACTORS = (
    "trust",       # 信值轴(45号等级)
    "cashflow",    # 现金流轴(60号预测/统计)
    "repayment",   # 履约轴(还款回流统计)
    "pressure",    # 金额压力轴(交易级)
)

# 四轴权重(封闭注册——变更走慢环审批)
CREDIT_WEIGHTS: dict = {
    "trust": 0.30,
    "cashflow": 0.25,
    "repayment": 0.25,
    "pressure": 0.20,
}

# 信值等级→基础授信额度(封闭映射)
CREDIT_BASE_LIMITS = {
    "S": 50000.0, "A": 30000.0,
    "B": 15000.0, "C": 8000.0, "D": 0.0,
    "trusted": 50000.0, "standard": 15000.0,
    "watched": 5000.0, "restricted": 0.0,
}

# 分期利率规则表(封闭——期数×利率,
# 确定性查表; 变更走慢环审批)
INSTALLMENT_RATES: dict = {
    3: 0.030,    # 3 期 3.0%
    6: 0.045,    # 6 期 4.5%
    12: 0.060,   # 12 期 6.0%
}

# 评级→可分期上限(封闭)
GRADE_INSTALLMENTS = {
    "excellent": (3, 6, 12),
    "good": (3, 6),
    "fair": (3,),
    "cautious": (),
    "rejected": (),
}

# 调额建议书状态机(封闭——资金域
# 永不自动: proposed→approved/rejected)
ADJUSTMENT_STATES = (
    "proposed",   # 已建议(待终审)
    "approved",   # admin 确认生效
    "rejected",   # admin 拒绝留痕
)


# ============================================================
# P4 生物特征(活体意图验证——原始特征
# 永不上传铁律)
# ============================================================

# 生物验证方式域(封闭)
BIOMETRIC_METHODS = (
    "face",       # 刷脸
    "fingerprint",  # 指纹
)

# 生物验证结果域(封闭)
BIOMETRIC_RESULTS = (
    "verified",    # 通过(特征匹配+无胁迫)
    "degraded",    # 降级(胁迫线索→密码验证)
    "failed",      # 失败(特征不匹配)
    "challenge_expired",  # 挑战过期
)

# 胁迫语义线索域(封闭——确定性特征表,
# 每项含权重; 命中≥阈值→degraded)
COERCION_SIGNS = {
    "facial_stiffness": 0.45,   # 面部僵硬
    "voice_tremor": 0.40,        # 语音颤抖
    "avoid_eyeball": 0.35,       # 眼神回避
    "abnormal_blink": 0.30,      # 异常眨眼频率
    "delayed_response": 0.25,   # 响应迟滞
}
COERCION_THRESHOLD = 0.60

# 模板版本上限(端侧增量——防无限膨胀)
TEMPLATE_VERSION_MAX = 65535

# FIDO 挑战 TTL(秒——48号 CONFIRM_TTL
# 同款口径)
BIOMETRIC_CHALLENGE_TTL = 120


# ============================================================
# 启动自检(宪法级)
# ============================================================

def _validate_registry() -> None:
    """注册表封闭自检(导入即验——
    RuntimeError 宪法级)"""
    # ① 七通道齐备
    registered = set(CHANNEL_REGISTRY)
    expected = set(CHANNEL_IDS)
    if registered != expected:
        raise RuntimeError(
            f"pay69 通道注册表不齐备: "
            f"缺 {expected - registered}, "
            f"多 {registered - expected}")
    for cid, meta in CHANNEL_REGISTRY.items():
        # ② 费率区间合法
        rate = meta.get("feeRate")
        if rate is None or not (0 <= rate <= 0.05):
            raise RuntimeError(
                f"pay69 通道 {cid} 费率域外: {rate}")
        # ③ 限额合法
        for lk in ("singleLimit", "dailyLimit"):
            lv = meta.get(lk)
            if not lv or float(lv) <= 0:
                raise RuntimeError(
                    f"pay69 通道 {cid} {lk} 非法: {lv}")
        # ④ 到账时效合法
        sh = meta.get("settleHours")
        if sh is None or int(sh) < 0:
            raise RuntimeError(
                f"pay69 通道 {cid} 到账时效非法: {sh}")
        # ⑤ 特征域非空
        if not meta.get("traits"):
            raise RuntimeError(
                f"pay69 通道 {cid} 特征域为空")
    # ⑥ 健康度状态域封闭
    if set(HEALTH_STATES) != {
            "healthy", "degraded",
            "critical", "frozen"}:
        raise RuntimeError("pay69 健康度状态域非法")
    # ⑦ 意图标签→亲和映射封闭(域内
    #    通道必须在注册表)
    if set(INTENT_AFFINITY) != set(INTENT_TAGS):
        raise RuntimeError(
            "pay69 意图标签与亲和映射不闭合")
    for tag, channels in INTENT_AFFINITY.items():
        unknown = set(channels) - expected
        if unknown:
            raise RuntimeError(
                f"pay69 意图 {tag} 亲和通道域外: "
                f"{unknown}")
    # ⑧ 路由权重键域封闭+和=1.0
    if set(ROUTE_WEIGHTS) != {
            "fee", "health", "affinity", "habit"}:
        raise RuntimeError("pay69 路由权重键域非法")
    total = sum(ROUTE_WEIGHTS.values())
    if abs(total - 1.0) > 1e-9:
        raise RuntimeError(
            f"pay69 路由权重和≠1.0: {total}")
    # ⑨ 熵六轴权重封闭+和=1.0
    if set(ENTROPY_WEIGHTS) != set(ENTROPY_AXES):
        raise RuntimeError("pay69 熵轴与权重不闭合")
    etotal = sum(ENTROPY_WEIGHTS.values())
    if abs(etotal - 1.0) > 1e-9:
        raise RuntimeError(
            f"pay69 熵权重和≠1.0: {etotal}")
    # ⑩ 步进梯度四档封闭+阈值递增+
    #    riskTier 语义对齐(60号四级)
    steps = tuple(s[0] for s in STEP_LADDER)
    if steps != ("free", "otp", "biometric",
                 "dual"):
        raise RuntimeError(f"pay69 步进梯度非法: {steps}")
    tiers = tuple(s[3] for s in STEP_LADDER)
    if tiers != ("light", "standard",
                 "strong", "enhanced"):
        raise RuntimeError(
            f"pay69 riskTier 对齐非法: {tiers}")
    for i in range(1, len(STEP_LADDER)):
        if STEP_LADDER[i][1] \
                <= STEP_LADDER[i - 1][1]:
            raise RuntimeError("pay69 步进阈值非递增")
    # ⑪ 通道轴风险映射覆盖全通道
    if set(CHANNEL_RISK) != expected:
        raise RuntimeError("pay69 通道风险映射不齐备")
    # ⑫ 金额分档递增+信值分档域合法
    for i in range(1, len(AMOUNT_BANDS)):
        if AMOUNT_BANDS[i][1] \
                <= AMOUNT_BANDS[i - 1][1]:
            raise RuntimeError("pay69 金额分档非递增")
    for tier, v in TRUST_BANDS.items():
        if not (0 <= v <= 1):
            raise RuntimeError(
                f"pay69 信值分档域外: {tier}={v}")
    # ⑬ P3 授信: 因子权重封闭+和=1.0
    if set(CREDIT_WEIGHTS) != set(CREDIT_FACTORS):
        raise RuntimeError("pay69 授信因子与权重不闭合")
    ctotal = sum(CREDIT_WEIGHTS.values())
    if abs(ctotal - 1.0) > 1e-9:
        raise RuntimeError(
            f"pay69 授信权重和≠1.0: {ctotal}")
    # ⑭ 分期利率表键在分期上限域内+利率递增
    for grade, inst in GRADE_INSTALLMENTS.items():
        if grade not in CREDIT_GRADES:
            raise RuntimeError(
                f"pay69 分期上限评级域外: {grade}")
        for n in inst:
            if n not in INSTALLMENT_RATES:
                raise RuntimeError(
                    f"pay69 评级 {grade} 分期 {n}"
                    f"期不在利率表")
    for i in (3, 6, 12):
        if i not in INSTALLMENT_RATES:
            raise RuntimeError(f"pay69 利率表缺 {i} 期")
    rates = [INSTALLMENT_RATES[k]
             for k in (3, 6, 12)]
    if not (rates[0] < rates[1] < rates[2]):
        raise RuntimeError("pay69 分期利率非递增")
    # ⑮ 额度映射非负+调额状态机封闭
    for tier, lv in CREDIT_BASE_LIMITS.items():
        if lv < 0:
            raise RuntimeError(
                f"pay69 额度映射为负: {tier}={lv}")
    if set(ADJUSTMENT_STATES) != {
            "proposed", "approved",
            "rejected"}:
        raise RuntimeError("pay69 调额状态机非法")
    # ⑯ P4 生物: 方式/结果域封闭+胁迫
    #     线索阈值合法
    if set(BIOMETRIC_METHODS) != {
            "face", "fingerprint"}:
        raise RuntimeError("pay69 生物方式域非法")
    if set(BIOMETRIC_RESULTS) != {
            "verified", "degraded",
            "failed", "challenge_expired"}:
        raise RuntimeError("pay69 生物结果域非法")
    if not (0 < COERCION_THRESHOLD <= 1):
        raise RuntimeError(
            "pay69 胁迫阈值域外")
    for sign, w in COERCION_SIGNS.items():
        if not (0 < w <= 1):
            raise RuntimeError(
                f"pay69 胁迫线索权重域外: "
                f"{sign}={w}")


_validate_registry()
