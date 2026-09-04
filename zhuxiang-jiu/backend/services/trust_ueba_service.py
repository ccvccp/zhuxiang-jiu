"""45号·P6 Value-UEBA 行为本体守门层
(对齐 docs/45号_Value-UEBA行为本体对照优化.md)

模板(docs/45号_Value-UEBA行为本体对照优化.md)四大域中
"UEBA 分析要点"的工程化落法——四个确定性守门:

    ① consistency_gate  L2 伪善预警:
       跨平台行为一致性 consistency ∈ [0,1], < 0.3 时
       正向事件 delta ×0.5(伪善折扣)——UEBA 真伪鉴别
       核心输入(模板 §2 网络诚信互动)
    ② self_promotion_gate  L3 作秀降权:
       宣传内容占比 self_promotion ∈ [0,1], > 0.7 时
       正向事件 delta ×0.5——L3 层防刷关键指标
       (模板 §3 社会公益行为)
    ③ recurrence_gate  修复对冲域再犯风险:
       同因子历史违规计数 n → risk = n/(n+2) 平滑;
       risk > 0.7(即 n ≥ 5) 时修复效率 ×0.5——
       状态机动态调整(模板 §4 行为矫正事件)
    ④ voluntary_bonus  自愿披露激励:
       自愿存证(voluntary=True)正向 delta ×1.05——
       鼓励自愿披露, 但需验真前置(模板 §2 voluntary_flag)

设计铁律:
    - 显式参数激活: consistency/selfPromotion/voluntary
      缺省 None → 守门不生效(既有调用零影响)
    - 纯乘性修正: 守门只折损/激励 delta, 不改变
      九因子结构/宪法权重/修复 αβγ 数学
    - 确定性纯函数: 无 IO, 无状态(计数由调用方查库传入)
"""

import logging

logger = logging.getLogger(__name__)

# ============================================================
# 守门阈值(模板口径)
# ============================================================

# ① L2 伪善预警(consistency < 0.3 触发)
CONSISTENCY_THRESHOLD = 0.3
CONSISTENCY_PENALTY = 0.5

# ② L3 作秀降权(self_promotion > 0.7 触发)
SELF_PROMOTION_THRESHOLD = 0.7
SELF_PROMOTION_PENALTY = 0.5

# ③ 修复域再犯风险(risk > 0.7 触发降效)
RECURRENCE_THRESHOLD = 0.7
RECURRENCE_PENALTY = 0.5

# ④ 自愿披露激励(+5%)
VOLUNTARY_BONUS = 1.05


def _clip01(value) -> float:
    """数值夹取 [0,1](非法输入返回 0——保守)"""
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return 0.0


def consistency_gate(consistency) -> tuple:
    """① L2 伪善预警守门

    Args:
        consistency: 跨平台行为一致性 0-1
            (None → 不守门; UEBA 一致性分析输入)
    Returns:
        (multiplier, tag, note):
        multiplier 事件 delta 乘数(1.0 或 0.5)
        tag ""(正常)|"hypocrisy_alert"(伪善预警)
    """
    if consistency is None:
        return 1.0, "", ""
    c = _clip01(consistency)
    if c < CONSISTENCY_THRESHOLD:
        return (CONSISTENCY_PENALTY, "hypocrisy_alert",
                f"UEBA伪善预警: 跨平台一致性 {c} < "
                f"{CONSISTENCY_THRESHOLD}(正向事件折损 "
                f"{int((1 - CONSISTENCY_PENALTY) * 100)}%)")
    return 1.0, "", ""


def self_promotion_gate(self_promotion) -> tuple:
    """② L3 作秀降权守门

    Args:
        self_promotion: 宣传内容占比 0-1
            (None → 不守门; > 0.7 触发"作秀"降权)
    Returns:
        (multiplier, tag, note):
        tag ""|"self_promotion_discount"
    """
    if self_promotion is None:
        return 1.0, "", ""
    r = _clip01(self_promotion)
    if r > SELF_PROMOTION_THRESHOLD:
        return (SELF_PROMOTION_PENALTY,
                "self_promotion_discount",
                f"UEBA作秀降权: 宣传内容占比 {r} > "
                f"{SELF_PROMOTION_THRESHOLD}(正向事件折损 "
                f"{int((1 - SELF_PROMOTION_PENALTY) * 100)}%)")
    return 1.0, "", ""


def recurrence_risk(violation_count: int) -> float:
    """③ 再犯风险预测(UEBA 历史序列平滑)

    risk = n/(n+2): n=0→0, 1→0.33, 2→0.5, 3→0.6,
    4→0.67, 5→0.71(首次越限), 10→0.83——惯犯收敛。
    """
    n = max(0, int(violation_count or 0))
    return round(n / (n + 2), 4)


def recurrence_gate(violation_count: int) -> tuple:
    """③ 修复对冲域再犯风险守门

    Args:
        violation_count: 同因子历史违规事件总数
            (含当前待修复违规——惯犯序列)
    Returns:
        (risk, multiplier, note):
        risk > 0.7 时修复效率 ×0.5
    """
    risk = recurrence_risk(violation_count)
    if risk > RECURRENCE_THRESHOLD:
        return (risk, RECURRENCE_PENALTY,
                f"UEBA再犯风险 {risk} > "
                f"{RECURRENCE_THRESHOLD}(同因子第 "
                f"{violation_count} 次违规, 修复效率折减 "
                f"{int((1 - RECURRENCE_PENALTY) * 100)}%)")
    return risk, 1.0, ""


def voluntary_bonus(voluntary, positive: bool) -> tuple:
    """④ 自愿披露激励守门

    Args:
        voluntary: 是否用户主动提交(None/False → 不激励;
            True → 正向事件 ×1.05)
        positive: 事件是否正向(负向不激励——防"认领扣分")
    Returns:
        (multiplier, note)
    """
    if not voluntary or not positive:
        return 1.0, ""
    return (VOLUNTARY_BONUS,
            f"自愿披露激励: 主动提交 +"
            f"{int((VOLUNTARY_BONUS - 1) * 100)}%"
            f"(验真前置已通过)")


def apply_event_gates(layer: str, delta: float,
                      consistency=None,
                      self_promotion=None) -> dict:
    """事件守门编排(record_event 接入点)

    L2 一致性预警 + L3 作秀降权(仅正向 delta 守门——
    扣分不折损, 伪善只影响加分)。

    Returns:
        {delta(修正后), gates: [{tag, note}], }
    """
    gates = []
    adjusted = float(delta)
    if layer == "L2" and adjusted > 0:
        mult, tag, note = consistency_gate(consistency)
        if tag:
            adjusted = round(adjusted * mult, 1)
            gates.append({"tag": tag, "note": note})
    if layer == "L3" and adjusted > 0:
        mult, tag, note = self_promotion_gate(self_promotion)
        if tag:
            adjusted = round(adjusted * mult, 1)
            gates.append({"tag": tag, "note": note})
    return {"delta": adjusted, "gates": gates}
