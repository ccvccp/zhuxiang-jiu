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


_validate_registry()
