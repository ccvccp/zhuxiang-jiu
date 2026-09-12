"""71号·AI智能支付端口大模型 端口池认知注册表
(pay71_registry, P0)

规划(docs/71号_AI智能支付端口大模型_创新规划方案.md
§七 P0):
    端口池注册表(消费 69号七通道字典——
    只读 import, 71号永不建第二套通道字典)
    +端口态扩展域(healthy/degraded/broken)
    +前兆信号域(失效前兆——确定性权重)
    +四维调配目标域(P3 帕累托口径预注册)
    +模式三态(PAY71_MODE off/shadow/assist
    ——默认 off)

设计(69号 pay69_registry 封闭注册表范式平移):
    - 封闭注册: 可断言/可测试/启动自检
    - 71号=支付端口大模型(端口池生命周期+
      资金流全程编排), 消费 69号 P0 健康度
      视图/P1 routeScore 作为输入, 永不
      修改 69号数据(叠加铁律)
    - 资金路由零重复(69号 P1 唯一负责);
      71号调配结果仅作建议注入

启动自检 _validate_registry()(RuntimeError
宪法级):
    - 端口域与 69号通道域完全一致(消费方
      铁律——不增不减不改)
    - 端口态域封闭
    - 前兆信号域封闭+权重合法
    - 四维调配权重封闭+和=1.0
    - 预警阈值合法
"""

import logging
import os

logger = logging.getLogger("pay71_registry")

MODEL_VERSION = "v1-pay71-registry"

DEFAULT_MODE = "off"

MODE_VALUES = ("off", "shadow", "assist")


def current_mode() -> str:
    """模块开关(PAY71_MODE, 默认 off——
    决策面关闭: off=仅观测面; shadow=
    影子学习期(调配建议只留痕); assist=
    辅助生产期(建议注入 69号路由参考)"""
    mode = os.environ.get("PAY71_MODE") or DEFAULT_MODE
    return mode if mode in MODE_VALUES else DEFAULT_MODE


def is_kill() -> bool:
    """紧急制动(PAY71_KILL——秒级冻结
    一切进化参数, 退回只读基线; 对齐
    PAY69_KILL/QR70_KILL 惯例)"""
    return os.environ.get("PAY71_KILL") == "1"


# ============================================================
# 端口池域(消费 69号七通道——只读铁律)
# ============================================================

# 71号端口池 = 69号通道域(消费方铁律:
# 不建第二套通道字典, 永不修改 69号注册表)
# import 保持与 pay69_registry 完全同源
def _port_ids() -> tuple:
    """端口域(69号 CHANNEL_IDS 只读消费)"""
    from services.pay69_registry import (
        CHANNEL_IDS,
    )
    return tuple(CHANNEL_IDS)


def _port_registry() -> dict:
    """端口注册表(69号 CHANNEL_REGISTRY
    只读消费——费率/限额/时效/特征)"""
    from services.pay69_registry import (
        CHANNEL_REGISTRY,
    )
    return CHANNEL_REGISTRY


# ============================================================
# 端口态扩展域(P1 自愈编排的消费口径)
# ============================================================

# 端口态三态(封闭——规划 §4.1:
# 端口池三态治理)
PORT_STATES = (
    "healthy",    # 健康(正常参与调配)
    "degraded",   # 降级(自动降权建议+留痕
                  # ——保护方向)
    "broken",     # 熔断摘除(新请求不再
                  # 建议; 半开探测恢复)
)

# 端口态权重因子(调配评分的端口态
# 折减系数——确定性; P3 帕累托消费)
PORT_STATE_FACTORS: dict = {
    "healthy": 1.0,
    "degraded": 0.5,
    "broken": 0.0,
}

# ============================================================
# 前兆信号域(P0 观测面——失效前兆
# 确定性权重; 预警仅观测, 阈值变更
# 走慢环 46号审批)
# ============================================================

PRECURSOR_SIGNALS = (
    "latency_rising",     # 响应时间渐增
    "errorcode_climb",    # 错误码占比爬升
    "success_decline",    # 成功率缓降
    "callback_delay",     # 回调延迟拉长
)

# 前兆信号权重(封闭——确定性; 叠加
# ≥阈值→预警观测)
PRECURSOR_WEIGHTS: dict = {
    "latency_rising": 0.35,
    "errorcode_climb": 0.35,
    "success_decline": 0.20,
    "callback_delay": 0.10,
}

# 前兆预警阈值(确定性——达到即预警
# 观测留痕; 变更走慢环审批)
PRECURSOR_ALERT_THRESHOLD = 0.50

# ============================================================
# P1 端口自愈编排(保护方向分级动作
# ——熔断/恢复可自动+全留痕铁律)
# ============================================================

# 前兆滚动窗口容量(P1——每端口最近
# N 条信号去重叠加; 变更走慢环审批)
SELFHEAL_WINDOW_SIZE = 10

# 熔断阈值(窗口多信号叠加≥0.80
# →broken 摘除——保护方向自动)
PRECURSOR_FUSE_THRESHOLD = 0.80

# 降级阈值(窗口叠加≥0.50 或 69号
# critical →degraded 降权)
# = P0 PRECURSOR_ALERT_THRESHOLD(同源)

# 半开探测恢复条件(broken 态连续
# N 次探针成功→healthy)
PROBE_REQUIRED_SUCCESSES = 3

# 影子冷启动期(天——新端口接入只观测
# 不参与调配; 达标后转正建议书)
SHADOW_DAYS = 7

# 端口治理生命周期域(封闭——影子
# 冷启动/转正/摘牌全链; 正式启停
# 永远走建议书+admin 终审铁律)
GOVERNANCE_STATES = (
    "ungoverned",   # 未纳管(默认)
    "shadow",       # 影子冷启动期
    "active",       # 转正(参与调配
                    # ——P3 消费)
    "offboarded",   # 摘牌(经建议书
                    # 人工终审)
)

# 转正/摘牌建议书状态机(封闭——端口
# 正式启停永不自动: proposed→approved/
# rejected, admin 终审)
PROPOSAL_STATES = (
    "proposed",   # 已建议(待终审)
    "approved",   # admin 确认生效
    "rejected",   # admin 拒绝留痕
)

# 建议书种类域(封闭)
PROPOSAL_KINDS = (
    "promote",    # 转正(shadow→active)
    "offboard",   # 摘牌(active→
                  # offboarded)
)

# 自愈动作域(封闭——全留痕口径)
SELFHEAL_ACTIONS = (
    "degrade",      # 降级(healthy→
                    # degraded, 保护)
    "fuse",         # 熔断(→broken,
                    # 保护)
    "recover",      # 恢复(→healthy,
                    # 保护)
    "probe_pass",   # 探针通过(计数)
    "probe_fail",   # 探针失败(清零)
    "no_change",    # 评估无变更
)

# ============================================================
# P2 预判式支付(预载可撤销观测域+资金
# 永不自动铁律——用户显式触发)
# ============================================================

# 习惯样本下限(69号 habits 计数——不足
# 则退化为费率优选默认建议)
PREDICT_MIN_SAMPLES = 3

# 大额拆分阈值(元——对齐 69号 AMOUNT_
# BANDS ¥5000 档; ≥阈值可发起拆分建议书)
SPLIT_THRESHOLD = 5000.0

# 拆分份数上限(并行度——对齐 69号
# AMOUNT_BANDS ¥20000 档: 2 万内 2 份
# /2 万以上 3 份)
SPLIT_MAX_PARTS = 3

# 拆分确认令牌 TTL(秒——单次消费口径)
SPLIT_CONFIRM_TTL = 1800

# 拆分建议书状态机(封闭——确认仅生成
# 建议包 executed=False, 资金执行永远
# 60号收银台显式调用铁律)
SPLIT_STATES = (
    "proposed",    # 已建议(待用户确认)
    "confirmed",   # 用户已确认(令牌
                   # 已消费——建议包)
    "rejected",   # 用户已拒绝(留痕)
)

# 重试退避序列(确定性毫秒——支付失败
# 案例重试策略, 避免无效请求堆积)
RETRY_BACKOFF_MS = (800, 1600, 3200)

# 重试上限(与 60号 P3 补单重试对齐)
RETRY_MAX_ATTEMPTS = 3

# ============================================================
# 四维调配目标域(P3 帕累托口径预注册
# ——确定性向量评分, LLM 禁入)
# ============================================================

ALLOCATION_DIMENSIONS = (
    "cost",         # 成本维(费率越低越优)
    "success",      # 成功率维(健康度)
    "experience",   # 体验维(时效/摩擦感)
    "compliance",   # 合规维(规则表校验)
)

# 四维基准权重(封闭注册——变更走慢环
# 46号审批; 情境权重规则表在 P3 展开)
ALLOCATION_BASE_WEIGHTS: dict = {
    "cost": 0.25,
    "success": 0.35,
    "experience": 0.20,
    "compliance": 0.20,
}

# 情境档位域(P3 帕累托选解的情境
# 权重规则表——P0 预注册口径)
ALLOCATION_CONTEXTS = (
    "price_sensitive",   # 价格敏感商户
                         # (成本维主导)
    "high_value",         # 高价值用户
                          # (体验维主导)
    "large_amount",       # 大额场景
                          # (合规维主导)
    "balanced",           # 均衡(默认)
)


# ============================================================
# 启动自检(宪法级)
# ============================================================

def _validate_registry() -> None:
    """注册表封闭自检(导入即验——
    RuntimeError 宪法级)"""
    from services.pay69_registry import (
        CHANNEL_IDS,
    )
    expected = set(CHANNEL_IDS)
    # ① 端口域与 69号通道域一致(消费方
    #    铁律——不增不减)
    ports = _port_ids()
    if set(ports) != expected or len(ports) != 7:
        raise RuntimeError(
            f"pay71 端口域与 69号通道域不一致: "
            f"{set(ports) ^ expected}")
    # ② 端口注册表同源只读(键集一致)
    if set(_port_registry()) != expected:
        raise RuntimeError(
            "pay71 端口注册表与 69号源不闭合")
    # ③ 端口态域封闭
    if set(PORT_STATES) != {
            "healthy", "degraded", "broken"}:
        raise RuntimeError(
            "pay71 端口态域非法")
    # ④ 端口态折减系数单调递减+域合法
    if PORT_STATE_FACTORS["healthy"] \
            <= PORT_STATE_FACTORS["degraded"] \
            or PORT_STATE_FACTORS["degraded"] \
            <= PORT_STATE_FACTORS["broken"]:
        raise RuntimeError(
            "pay71 端口态折减系数非单调递减")
    for state, factor in PORT_STATE_FACTORS.items():
        if not (0 <= factor <= 1):
            raise RuntimeError(
                f"pay71 端口态折减系数域外: "
                f"{state}={factor}")
    if set(PORT_STATE_FACTORS) != set(PORT_STATES):
        raise RuntimeError(
            "pay71 端口态与折减系数不闭合")
    # ⑤ 前兆信号域封闭+权重合法
    if set(PRECURSOR_WEIGHTS) \
            != set(PRECURSOR_SIGNALS):
        raise RuntimeError(
            "pay71 前兆信号与权重不闭合")
    for signal, w in PRECURSOR_WEIGHTS.items():
        if not (0 < w <= 1):
            raise RuntimeError(
                f"pay71 前兆信号权重域外: "
                f"{signal}={w}")
    # ⑥ 前兆预警阈值合法
    if not (0 < PRECURSOR_ALERT_THRESHOLD
            <= 1):
        raise RuntimeError(
            "pay71 前兆预警阈值域外")
    # ⑦ 四维调配域封闭+权重和=1.0
    if set(ALLOCATION_BASE_WEIGHTS) \
            != set(ALLOCATION_DIMENSIONS):
        raise RuntimeError(
            "pay71 四维调配维度与权重不闭合")
    atotal = sum(
        ALLOCATION_BASE_WEIGHTS.values())
    if abs(atotal - 1.0) > 1e-9:
        raise RuntimeError(
            f"pay71 四维调配权重和≠1.0: "
            f"{atotal}")
    # ⑧ 情境档位域封闭+非空
    if not ALLOCATION_CONTEXTS \
            or "balanced" not in \
            ALLOCATION_CONTEXTS:
        raise RuntimeError(
            "pay71 情境档位域非法(缺 balanced"
            " 默认档)")
    # ⑨ 模式三态封闭
    if set(MODE_VALUES) != {
            "off", "shadow", "assist"}:
        raise RuntimeError("pay71 模式三态域非法")
    # ⑩ P1 自愈: 熔断阈值>预警阈值
    #     (分级单调)+窗口/探测/影子参数
    #     合法+治理/建议书/动作域封闭
    if not (PRECURSOR_ALERT_THRESHOLD
            < PRECURSOR_FUSE_THRESHOLD <= 1):
        raise RuntimeError(
            "pay71 自愈阈值分级非单调递增")
    if not (1 <= SELFHEAL_WINDOW_SIZE <= 100):
        raise RuntimeError(
            "pay71 前兆滚动窗口容量域外")
    if not (1 <= PROBE_REQUIRED_SUCCESSES <= 10):
        raise RuntimeError(
            "pay71 半开探测次数域外")
    if not (1 <= SHADOW_DAYS <= 30):
        raise RuntimeError(
            "pay71 影子冷启动天数域外")
    if set(GOVERNANCE_STATES) != {
            "ungoverned", "shadow",
            "active", "offboarded"}:
        raise RuntimeError(
            "pay71 端口治理生命周期域非法")
    if set(PROPOSAL_STATES) != {
            "proposed", "approved",
            "rejected"}:
        raise RuntimeError(
            "pay71 建议书状态机非法")
    if set(PROPOSAL_KINDS) != {
            "promote", "offboard"}:
        raise RuntimeError(
            "pay71 建议书种类域非法")
    if set(SELFHEAL_ACTIONS) != {
            "degrade", "fuse", "recover",
            "probe_pass", "probe_fail",
            "no_change"}:
        raise RuntimeError(
            "pay71 自愈动作域非法")
    # ⑪ P2 预判: 拆分参数/状态机/退避
    #     序列/习惯样本下限合法
    if SPLIT_THRESHOLD <= 0:
        raise RuntimeError(
            "pay71 拆分阈值域外")
    if not (2 <= SPLIT_MAX_PARTS <= 5):
        raise RuntimeError(
            "pay71 拆分份数上限域外")
    if not (60 <= SPLIT_CONFIRM_TTL
            <= 86400):
        raise RuntimeError(
            "pay71 拆分确认 TTL 域外")
    if set(SPLIT_STATES) != {
            "proposed", "confirmed",
            "rejected"}:
        raise RuntimeError(
            "pay71 拆分状态机非法")
    if not RETRY_BACKOFF_MS or any(
            b <= 0 for b in
            RETRY_BACKOFF_MS):
        raise RuntimeError(
            "pay71 重试退避序列非法")
    for i in range(1, len(RETRY_BACKOFF_MS)):
        if RETRY_BACKOFF_MS[i] \
                <= RETRY_BACKOFF_MS[i - 1]:
            raise RuntimeError(
                "pay71 重试退避序列非递增")
    if RETRY_MAX_ATTEMPTS < 1:
        raise RuntimeError(
            "pay71 重试上限域外")
    if PREDICT_MIN_SAMPLES < 1:
        raise RuntimeError(
            "pay71 习惯样本下限域外")


_validate_registry()
