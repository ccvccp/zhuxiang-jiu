"""56号·AI智能升级管理 信号注册表
(aiup56_registry)

计划(docs/56号_AI智能升级管理模块实施计划.md §二):
    升级信号封闭白名单 SIGNAL_REGISTRY——全站既有
    观测面纯读取(46号三检测器/52号五维/55号六指标
    /44号池), 零侵入零改动。

设计(52号 us52_registry 范式):
    - 封闭注册表: 可断言/可测试/启动自检
    - 四侧覆盖: 模型侧/用户侧/系统侧/合规侧
    - 权重和=1.0(必要性评分归一基础)
    - 采集口径 fail-soft(单源异常跳过留痕)

启动自检 _validate_registry()(RuntimeError 宪法级):
    - 10 项数量+四侧覆盖
    - 权重和=1.0
    - direction 合法(positive/negative)
    - status 合法(active/pending/retired)
    - 来源标识非空
"""

import logging
import os

logger = logging.getLogger("aiup56_registry")

MODEL_VERSION = "v1-aiup56-registry"

DEFAULT_MODE = "off"

# 模式三态(off/shadow/assist——计划 §九开关矩阵)
MODE_VALUES = ("off", "shadow", "assist")

# 信号四侧
SIGNAL_SIDES = ("model", "user", "system", "compliance")

# 信号方向(命中→推动/抑制升级)
DIRECTION_VALUES = ("positive", "negative")

# 信号状态
SIGNAL_STATUS_VALUES = ("active", "pending", "retired")


def current_mode() -> str:
    """模块开关(AIUP56_MODE, 默认 off——决策面关闭:
    off=信号面仅 registry 观测; shadow=观察学习期
    仅决策+提案; assist=辅助开发期资产生产+审批交付)"""
    mode = os.environ.get("AIUP56_MODE") or DEFAULT_MODE
    return mode if mode in MODE_VALUES else DEFAULT_MODE


# ============================================================
# 升级信号注册表(10 项——计划 §二四侧)
# ============================================================

SIGNAL_REGISTRY: dict = {
    # ---- 模型侧(0.525) ----
    "gov46_stagnation": {
        "label": "学习停滞",
        "side": "model", "source": "ai_governance_health",
        "direction": "positive", "weight": 0.20,
        "threshold": 1, "status": "active",
    },
    "gov46_drift_high": {
        "label": "因子漂移高",
        "side": "model", "source": "ai_governance_health",
        "direction": "positive", "weight": 0.15,
        "threshold": 1, "status": "active",
    },
    "pool44_alignment": {
        "label": "回流对齐下降",
        "side": "model", "source": "ai_learning_pool",
        "direction": "positive", "weight": 0.15,
        "threshold": 0.05, "status": "active",
    },
    "scorer_frozen": {
        "label": "档案冻结中",
        "side": "model", "source": "ai_governance",
        "direction": "positive", "weight": 0.025,
        "threshold": 1, "status": "active",
    },
    # ---- 用户侧(0.25) ----
    "us52_usability_drop": {
        "label": "可用性下降",
        "side": "user", "source": "us52_metrics",
        "direction": "positive", "weight": 0.15,
        "threshold": 0.1, "status": "active",
    },
    "qr55_satisfaction_drop": {
        "label": "满意度下降",
        "side": "user", "source": "qr55_metrics",
        "direction": "positive", "weight": 0.10,
        "threshold": 0.1, "status": "active",
    },
    # ---- 系统侧(0.15) ----
    "qr55_clarify_bloat": {
        "label": "澄清效率劣化",
        "side": "system", "source": "qr55_metrics",
        "direction": "positive", "weight": 0.10,
        "threshold": 0.2, "status": "active",
    },
    "qr55_generation_waste": {
        "label": "生成过剩",
        "side": "system", "source": "qr55_feedback",
        "direction": "positive", "weight": 0.05,
        "threshold": 0.3, "status": "active",
    },
    # ---- 合规侧(0.075) ----
    "gov46_alert_open": {
        "label": "合规告警未决",
        "side": "compliance",
        "source": "ai_governance_alerts",
        "direction": "negative", "weight": 0.05,
        "threshold": 1, "status": "active",
    },
    "registry_pending": {
        "label": "挂起待审批项",
        "side": "compliance",
        "source": "ai_governance_changes",
        "direction": "negative", "weight": 0.025,
        "threshold": 1, "status": "active",
    },
}


def get_signal(signal_id: str) -> dict | None:
    """取信号定义"""
    return SIGNAL_REGISTRY.get(signal_id)


def active_signals() -> list[dict]:
    """全部 active 信号(扫描可用域)"""
    return [dict(v, signalId=k) for k, v
            in SIGNAL_REGISTRY.items()
            if v.get("status") == "active"]


def registry_view() -> dict:
    """注册表自描述(观测面)"""
    sides: dict = {}
    for sig in SIGNAL_REGISTRY.values():
        sides[sig["side"]] = \
            sides.get(sig["side"], 0) + 1
    return {
        "success": True,
        "modelVersion": MODEL_VERSION,
        "mode": current_mode(),
        "total": len(SIGNAL_REGISTRY),
        "bySide": sides,
        "weightSum": round(sum(
            s["weight"] for s
            in SIGNAL_REGISTRY.values()
            if s.get("status") == "active"), 4),
        "signals": [
            {"signalId": k, **v}
            for k, v in SIGNAL_REGISTRY.items()],
        "modeValues": MODE_VALUES,
        "note": "升级信号封闭白名单——四侧纯读取"
                "(46/52/55号+44号池零侵入)",
    }


def _validate_registry() -> None:
    """启动自检(RuntimeError 宪法级——52号范式)"""
    errors = []
    if len(SIGNAL_REGISTRY) != 10:
        errors.append(
            f"信号数量应为 10, 实际 "
            f"{len(SIGNAL_REGISTRY)}")
    # 四侧覆盖
    sides = {v.get("side")
             for v in SIGNAL_REGISTRY.values()}
    if sides != set(SIGNAL_SIDES):
        errors.append(f"四侧覆盖不齐: {sorted(sides)}")
    # 权重和(active 域)=1.0
    weight_sum = sum(
        v.get("weight") or 0
        for v in SIGNAL_REGISTRY.values()
        if v.get("status") == "active")
    if abs(weight_sum - 1.0) > 1e-9:
        errors.append(
            f"active 权重和应为 1.0, 实际 {weight_sum}")
    for sid, sig in SIGNAL_REGISTRY.items():
        if sig.get("direction") not in DIRECTION_VALUES:
            errors.append(
                f"{sid}: 非法 direction "
                f"{sig.get('direction')}")
        if sig.get("status") not in \
                SIGNAL_STATUS_VALUES:
            errors.append(
                f"{sid}: 非法 status {sig.get('status')}")
        if not str(sig.get("source") or "").strip():
            errors.append(f"{sid}: 来源标识为空")
        w = sig.get("weight")
        if not isinstance(w, (int, float)) \
                or not 0 < float(w) <= 1:
            errors.append(f"{sid}: 权重越界 {w}")
    if errors:
        raise RuntimeError(
            "aiup56 SIGNAL_REGISTRY 自检失败: "
            + "; ".join(errors))
    logger.info(
        "aiup56_registry_validated signals=%s "
        "sides=%s weightSum=1.0",
        len(SIGNAL_REGISTRY), len(sides))


_validate_registry()
