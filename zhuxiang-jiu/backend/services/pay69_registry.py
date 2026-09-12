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


_validate_registry()
