"""64号·信值兑换商品/服务AI智能管理 刚性规则注册表
(xx64_registry)

计划(docs/64号_信值兑换商品服务AI智能管理模块实施计划.md
§二/§八 P0):
    R1-R7 刚性规则宪法封闭注册
    (不可 AI 修改——AI/LLM 无权变更):

        R1 混合支付: 商品/服务价值的 30% 可用
           信值支付, 70% 现付(服务端强制)
        R2 整单互斥: 信值支付订单不再享有
           其他优惠活动
        R3 转移记账: 买扣卖增同事务原子对,
           来源标记 consumption_transfer
        R4 单次限额: 单次兑换额 ≤ 时点
           余额快照 × 20%
        R5 累计限额: 滚动 30 日窗口累计 ≤
           窗口内最大余额快照 × 40%
        R6 积分入口: 100 积分 = 1 信值
           (整数倍; T+1 冻结观察; 日限频 3 次)
        R7 负值禁止: 负信值账户禁止兑换
           (锁值先行+原子扣减)

    限额基准快照机制(本站首创——防拆单
    压低基数绕限): 窗口累计比对基准取
    窗口内最大余额快照, 随流水可审计。

启动自检 _validate_registry()(RuntimeError
宪法级)。
"""

import logging
import os

logger = logging.getLogger("xx64_registry")

MODEL_VERSION = "v1-xx64-registry"

DEFAULT_MODE = "off"

MODE_VALUES = ("off", "shadow", "assist")

# ============================================================
# 刚性规则参数(宪法级——AI 不可修改;
# 窗口/限频等派生参数经 46号审批校准)
# ============================================================

# R1 混合支付结构
TRUST_PORTION = 0.30      # 信值支付占比(30%)
CASH_PORTION = 0.70       # 现付占比(70%)

# R4 单次限额
SINGLE_QUOTA_RATIO = 0.20

# R5 累计限额(滚动窗口)
WINDOW_DAYS = 30
CUMULATIVE_QUOTA_RATIO = 0.40

# R6 积分入口
POINTS_PER_TRUST = 100    # 100 积分 = 1 信值
POINTS_DAILY_LIMIT = 3    # 积分兑换日限频
POINTS_FROZEN_HOURS = 24  # T+1 冻结观察(小时)

# 派生参数校准域(46号审批——P4 阈值)
WINDOW_MIN = 7
WINDOW_MAX = 90

# 订单九态状态机
ORDER_STATES = (
    "initiated",    # 已创建
    "prechecked",   # 预校验通过
    "reserved",     # 已锁值(信值冻结)
    "paid",         # 已支付(原子转移完成)
    "settled",      # 已结算
    "completed",    # 已完成
    "cancelled",    # 已取消(解锁)
    "refunded",     # 已退款(反向转移)
    "disputed",     # 申诉中(P4)
)

# 状态机合法迁移(九态)
ORDER_TRANSITIONS = {
    "initiated": ("prechecked", "cancelled"),
    "prechecked": ("reserved", "cancelled"),
    "reserved": ("paid", "cancelled"),
    "paid": ("settled", "refunded", "disputed"),
    "settled": ("completed", "refunded", "disputed"),
    "completed": ("disputed",),
    "cancelled": (),
    "refunded": ("disputed",),
    "disputed": ("refunded",),
}

# 转移来源标记(R3——防洗钱混淆:
# 消费转移≠铸币)
TRANSFER_SOURCE = "consumption_transfer"

# 预校验四查口径
PRECHECK_CODES = {
    "R1_STRUCT": "30% 信值结构可达",
    "R4_SINGLE": "单次 ≤ 余额×20%",
    "R5_WINDOW": "30 日窗口累计 ≤ 最大快照×40%",
    "R7_NONNEG": "信值余额非负",
}


def current_mode() -> str:
    """模块开关(XX64_MODE, 默认 off——
    决策面关闭: off=仅观测面; shadow=
    兑换观察期(订单留痕不转移); assist=
    辅助结算期(支付开放)"""
    mode = os.environ.get("XX64_MODE") or DEFAULT_MODE
    return mode if mode in MODE_VALUES else DEFAULT_MODE


def trust_portion(price: float) -> float:
    """R1: 信值支付额(价格×30%)"""
    return round(float(price or 0)
                * TRUST_PORTION, 2)


def cash_portion(price: float) -> float:
    """R1: 现付额(价格×70%)"""
    return round(float(price or 0)
                 * CASH_PORTION, 2)


def single_quota(balance: float) -> float:
    """R4: 单次兑换上限(余额×20%)"""
    return round(float(balance or 0)
                 * SINGLE_QUOTA_RATIO, 2)


def cumulative_quota(max_snapshot: float) -> float:
    """R5: 窗口累计上限(窗口内最大
    余额快照×40%)"""
    return round(float(max_snapshot or 0)
                 * CUMULATIVE_QUOTA_RATIO, 2)


def points_to_trust(points: int) -> float:
    """R6: 积分→信值换算(100:1 整数倍)"""
    points = int(points or 0)
    if points < POINTS_PER_TRUST \
            or points % POINTS_PER_TRUST != 0:
        raise ValueError(
            f"积分兑换须为 {POINTS_PER_TRUST} 的"
            f"整数倍(实际 {points})")
    return round(points
                 / POINTS_PER_TRUST, 2)


def registry_view() -> dict:
    """刚性规则自描述(观测面——
    不受开关影响)"""
    return {
        "success": True,
        "modelVersion": MODEL_VERSION,
        "mode": current_mode(),
        "rigidRules": {
            "R1": {
                "label": "混合支付结构",
                "trustPortion":
                    TRUST_PORTION,
                "cashPortion":
                    CASH_PORTION,
                "note": "商品/服务价值的 "
                        "30% 信值+70% 现付"
                        "(服务端强制)",
            },
            "R2": {
                "label": "整单互斥",
                "note": "信值支付订单不享有"
                        "其他优惠活动",
            },
            "R3": {
                "label": "转移记账",
                "source":
                    TRANSFER_SOURCE,
                "note": "买扣卖增同事务"
                        "原子对",
            },
            "R4": {
                "label": "单次限额",
                "ratio":
                    SINGLE_QUOTA_RATIO,
                "note": "≤ 时点余额快照×20%",
            },
            "R5": {
                "label": "累计限额",
                "windowDays":
                    WINDOW_DAYS,
                "ratio":
                    CUMULATIVE_QUOTA_RATIO,
                "note": "滚动 30 日窗口累计 ≤"
                        " 窗口内最大快照×40%"
                        "(基准快照机制——"
                        "防拆单压基数)",
            },
            "R6": {
                "label": "积分入口",
                "pointsPerTrust":
                    POINTS_PER_TRUST,
                "dailyLimit":
                    POINTS_DAILY_LIMIT,
                "frozenHours":
                    POINTS_FROZEN_HOURS,
                "note": "100 积分=1 信值"
                        "(整数倍; T+1 冻结"
                        "观察)",
            },
            "R7": {
                "label": "负值禁止",
                "note": "负信值账户禁止兑换"
                        "(锁值先行)",
            },
        },
        "orderStates":
            list(ORDER_STATES),
        "precheckCodes": dict(
            PRECHECK_CODES),
        "modeValues": MODE_VALUES,
        "note": "刚性规则宪法 R1-R7——"
                "AI 不可修改(派生参数经"
                "46号审批校准)",
    }


def _validate_registry() -> None:
    """启动自检(RuntimeError 宪法级)"""
    errors = []
    # R1 结构
    if abs(TRUST_PORTION
           + CASH_PORTION - 1.0) > 1e-9:
        errors.append(
            f"R1 混合支付占比和≠1.0"
            f"({TRUST_PORTION}+"
            f"{CASH_PORTION})")
    if not 0 < TRUST_PORTION < 1:
        errors.append(
            f"R1 信值占比越界 "
            f"{TRUST_PORTION}")
    # R4/R5 限额域
    if not 0 < SINGLE_QUOTA_RATIO < 1:
        errors.append(
            f"R4 单次限额比越界 "
            f"{SINGLE_QUOTA_RATIO}")
    if not 0 < CUMULATIVE_QUOTA_RATIO < 1:
        errors.append(
            f"R5 累计限额比越界 "
            f"{CUMULATIVE_QUOTA_RATIO}")
    if CUMULATIVE_QUOTA_RATIO \
            <= SINGLE_QUOTA_RATIO:
        errors.append(
            "R5 累计限额比须大于"
            "单次限额比(累计≥单次)")
    # R5 窗口
    if not WINDOW_MIN <= WINDOW_DAYS \
            <= WINDOW_MAX:
        errors.append(
            f"R5 窗口 {WINDOW_DAYS} 越界"
            f"({WINDOW_MIN}-{WINDOW_MAX})")
    # R6 积分
    if POINTS_PER_TRUST <= 0:
        errors.append(
            "R6 积分兑换比须为正")
    if POINTS_DAILY_LIMIT < 1:
        errors.append(
            "R6 日限频须≥1")
    # 状态机九态
    if len(ORDER_STATES) != 9:
        errors.append(
            f"订单状态机应九态, 实际 "
            f"{len(ORDER_STATES)}")
    reachable = {"initiated"}
    changed = True
    while changed:
        changed = False
        for src, dsts in \
                ORDER_TRANSITIONS.items():
            if src in reachable:
                for d in dsts:
                    if d not in reachable:
                        reachable.add(d)
                        changed = True
    unreachable = set(ORDER_STATES) \
        - reachable
    if unreachable:
        errors.append(
            f"订单状态机存在不可达态: "
            f"{sorted(unreachable)}")
    if errors:
        raise RuntimeError(
            "xx64 registry 自检失败: "
            + "; ".join(errors))
    logger.info(
        "xx64_registry_validated rules=R1-R7 "
        "states=%s window=%s ratio=%s/%s",
        len(ORDER_STATES), WINDOW_DAYS,
        SINGLE_QUOTA_RATIO,
        CUMULATIVE_QUOTA_RATIO)


_validate_registry()
