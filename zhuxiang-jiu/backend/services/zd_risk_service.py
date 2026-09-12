"""智单·AI智能订单大模型 P2 退款裁决与风险(zd_risk_service)

「从观测到裁决」(建议书模式——裁决永不自动执行):
    - 退款裁决评分: 四因子加权 0-100(金额异常度/退款频次/会员风险/
      商品域囤货风险), 输出分级(low/mid/high)与建议
      (approve/reject/manual) + 推理链 + 建议书 disposition
    - 异常扫描: 三类确定性规则(高频下单: 同会员 24h 内 ≥3 单 /
      大额囤货: 单品数量 ≥10 或单额 ≥万元 / 秒退款: 支付后 24h 内
      申请退款), 风险分级, 建议书模式不拦截, 结果留痕

铁律: 全部确定性规则; LLM 禁入判定链; 订单不存在 KeyError →
路由层转 404; 同输入同输出。
"""

import logging
from datetime import timedelta

from services.zd_fabric_service import (
    _ZdStore, ZdFabricService, _round2, _now_iso, _clamp, _parse_iso)

logger = logging.getLogger(__name__)

# 退款评分四因子权重(固定, 对齐五因子先例)
REFUND_WEIGHTS = {
    "amount": 0.30,       # 金额异常度(本单 vs 会员历史均值倍数)
    "refundFreq": 0.30,   # 退款频次(会员历史退款率)
    "memberRisk": 0.20,   # 会员风险(退货比 + 消费积分扣回风险)
    "productRisk": 0.20,  # 商品域风险(囤货量)
}

LEVEL_NAMES = {"low": "低风险", "mid": "中风险", "high": "高风险"}
SUGGESTION_NAMES = {
    "approve": "建议同意退款(低风险)",
    "manual": "建议人工复核(中风险)",
    "reject": "建议驳回退款(高风险)",
}

# 异常规则阈值(确定性)
INSTANT_REFUND_H = 24.0    # 秒退款: 支付后 24h 内申请
BULK_QTY = 10              # 大额囤货: 单品数量阈值
BULK_AMOUNT = 10000.0      # 大额囤货: 单额阈值(万元)
HIGH_FREQ_COUNT = 3        # 高频下单: 24h 内单量阈值
HIGH_FREQ_WINDOW_H = 24.0  # 高频下单: 滑动窗口

ANOMALY_TYPE_NAMES = {
    "high_frequency": "高频下单",
    "bulk_stockpile": "大额囤货",
    "instant_refund": "秒退款",
}


class ZdRiskService:
    """P2: 退款裁决评分 + 三类异常订单扫描"""

    def __init__(self, fabric: ZdFabricService = None,
                 store: _ZdStore = None):
        self.store = store or _ZdStore()
        self.fabric = fabric or ZdFabricService(self.store)

    # ============================================================
    # 退款裁决评分(四因子加权, 建议书)
    # ============================================================

    async def refund_score(self, order_id: str) -> dict:
        """退款裁决评分(0-100, 四因子确定性加权)

        因子映射(各 0-100):
            - 金额异常度 = clamp((本单/会员历史均值 − 1) × 50)
            - 退款频次 = clamp(会员历史退款率 × 200)
            - 会员风险 = clamp(退货比 × 100 + min(50, 消费积分/20))
            - 商品域风险 = clamp((单品最大数量 − 3) × 25)

        Raises:
            KeyError: 订单不存在(路由层转 404)
        """
        order = await self.fabric.get_order(order_id)
        orders = await self.fabric.orders(limit=2000)
        member_id = order.get("memberId")
        amount = _round2(
            (order.get("priceDetail") or {}).get("actualAmount"))
        others = [o for o in orders
                  if o.get("memberId") == member_id
                  and o.get("orderId") != order_id]
        # 1. 金额异常度: 本单金额 vs 会员历史均值倍数
        member_amounts = [
            _round2((o.get("priceDetail") or {}).get("actualAmount"))
            for o in others]
        member_mean = (sum(member_amounts) / len(member_amounts)
                       if member_amounts else 0.0)
        ratio = (amount / member_mean) if member_mean > 0 else 1.0
        f_amount = _clamp((ratio - 1.0) * 50)
        # 2. 退款频次: 会员历史退款率
        refunded_n = sum(1 for o in others
                         if o.get("status") == "REFUNDED")
        freq = (refunded_n / len(others)) if others else 0.0
        f_freq = _clamp(freq * 200)
        # 3. 会员风险: 退货比 + 消费积分扣回风险(consumedPoints)
        returning_n = sum(1 for o in others
                          if o.get("status") in ("RETURNING", "REFUNDED"))
        return_ratio = (returning_n / len(others)) if others else 0.0
        consumed = float(order.get("consumedPoints", 0) or 0)
        f_member = _clamp(return_ratio * 100 + min(50.0, consumed / 20))
        # 4. 商品域风险: 囤货量(本单单品最大数量)
        items = order.get("items", []) or []
        max_qty = max((int(i.get("quantity", 0) or 0) for i in items),
                      default=0)
        f_product = _clamp((max_qty - 3) * 25)
        score = _round2(
            REFUND_WEIGHTS["amount"] * f_amount
            + REFUND_WEIGHTS["refundFreq"] * f_freq
            + REFUND_WEIGHTS["memberRisk"] * f_member
            + REFUND_WEIGHTS["productRisk"] * f_product)
        if score < 30:
            level, suggestion = "low", "approve"
        elif score < 60:
            level, suggestion = "mid", "manual"
        else:
            level, suggestion = "high", "reject"
        factors = [
            {"factor": "金额异常度", "code": "amount",
             "raw": _round2(ratio), "score": _round2(f_amount),
             "explain": (f"本单 ¥{amount} vs 会员历史均值 "
                         f"¥{_round2(member_mean)}"
                         f"(倍数 {_round2(ratio)}×, "
                         f"样本 {len(member_amounts)} 单)")},
            {"factor": "退款频次", "code": "refundFreq",
             "raw": _round2(freq), "score": _round2(f_freq),
             "explain": (f"会员历史退款率 {_round2(freq * 100)}%"
                         f"({refunded_n}/{len(others)})")},
            {"factor": "会员风险", "code": "memberRisk",
             "raw": _round2(return_ratio), "score": _round2(f_member),
             "explain": (f"会员退货比 {_round2(return_ratio * 100)}%, "
                         f"本单消费积分 {consumed}(扣回风险)")},
            {"factor": "商品域风险", "code": "productRisk",
             "raw": max_qty, "score": _round2(f_product),
             "explain": f"单品最大数量 {max_qty}"
                        f"(囤货阈值 {BULK_QTY})"},
        ]
        reasoning = [
            f"四因子观测: 金额 {_round2(f_amount)} / "
            f"频次 {_round2(f_freq)} / 会员 {_round2(f_member)} / "
            f"囤货 {_round2(f_product)}(各 0-100, 确定性映射)",
            f"加权总分 {score} → {LEVEL_NAMES[level]} → "
            f"{SUGGESTION_NAMES[suggestion]}",
            "裁决为建议书: 决定权在管理员, 不自动执行退款",
        ]
        return {
            "orderId": order_id,
            "memberId": member_id,
            "amount": amount,
            "factors": factors,
            "weights": dict(REFUND_WEIGHTS),
            "score": score,
            "level": level,
            "levelName": LEVEL_NAMES[level],
            "suggestion": suggestion,
            "suggestionName": SUGGESTION_NAMES[suggestion],
            "reasoning": reasoning,
            "formula": ("退款风险分 = 0.30×金额异常 + 0.30×退款频次 + "
                        "0.20×会员风险 + 0.20×囤货风险"
                        "(映射: 金额 (倍数−1)×50 / 频次 退款率×200 / "
                        "会员 退货比×100+积分/20 / 囤货 (数量−3)×25, "
                        "确定性)"),
            "disposition": "退款裁决为建议书; 决定权在管理员"
                           "(不自动执行退款)",
        }

    # ============================================================
    # 异常订单扫描(三类确定性规则, 建议书不拦截)
    # ============================================================

    async def anomaly_scan(self) -> dict:
        """三类异常订单检测(高频下单/大额囤货/秒退款)

        建议书模式: 只观测留痕, 永不自动拦截订单。
        """
        orders = await self.fabric.orders(limit=2000)
        asc = sorted(orders, key=lambda o: (str(o.get("createdAt", "")),
                                            str(o.get("orderId", ""))))
        detected_at = _now_iso()
        anomalies: list[dict] = []
        # 1. 高频下单: 同会员 24h 滑动窗口内 ≥3 单(取最大窗口)
        member_entries: dict = {}
        for order in asc:
            dt = _parse_iso(order.get("createdAt"))
            if dt is None:
                continue
            member_entries.setdefault(
                order.get("memberId"), []).append((dt, order))
        for member_id, entries in member_entries.items():
            best_count, best_order = 0, None
            for i, (start_dt, start_order) in enumerate(entries):
                limit_dt = start_dt + timedelta(hours=HIGH_FREQ_WINDOW_H)
                count = sum(1 for dt, _ in entries[i:]
                            if dt <= limit_dt)
                if count > best_count:
                    best_count, best_order = count, start_order
            if best_count >= HIGH_FREQ_COUNT and best_order is not None:
                anomalies.append({
                    "anomalyId": await self.store.next_id("anomalies"),
                    "type": "high_frequency",
                    "typeName": ANOMALY_TYPE_NAMES["high_frequency"],
                    "orderId": best_order.get("orderId", ""),
                    "memberId": member_id,
                    "level": "mid",
                    "detail": (f"会员 {member_id} 24 小时内密集下单 "
                               f"{best_count} 单"
                               f"(阈值 ≥{HIGH_FREQ_COUNT})"),
                    "suggestion": "建议人工核查下单行为"
                                  "(刷单/黄牛风险); 建议书不拦截",
                    "detectedAt": detected_at,
                })
        # 2. 大额囤货: 单品数量 ≥10 或单额 ≥ 万元
        for order in asc:
            items = order.get("items", []) or []
            max_qty = max((int(i.get("quantity", 0) or 0)
                           for i in items), default=0)
            amount = _round2(
                (order.get("priceDetail") or {}).get("actualAmount"))
            if max_qty >= BULK_QTY or amount >= BULK_AMOUNT:
                level = "mid" if amount >= BULK_AMOUNT else "low"
                anomalies.append({
                    "anomalyId": await self.store.next_id("anomalies"),
                    "type": "bulk_stockpile",
                    "typeName": ANOMALY_TYPE_NAMES["bulk_stockpile"],
                    "orderId": order.get("orderId", ""),
                    "memberId": order.get("memberId"),
                    "level": level,
                    "detail": (f"单品数量 {max_qty}"
                               f"(阈值 {BULK_QTY}) / "
                               f"单额 ¥{amount}"
                               f"(阈值 ¥{BULK_AMOUNT})"),
                    "suggestion": "建议核查囤货动机与限购策略"
                                  "(建议书不拦截)",
                    "detectedAt": detected_at,
                })
        # 3. 秒退款: 支付后 24h 内申请退款
        for order in asc:
            paid = _parse_iso((order.get("payment") or {}).get("paidAt"))
            if paid is None:
                continue
            refund = order.get("refund") or {}
            applied = (_parse_iso(
                (refund.get("audit") or {}).get("appliedAt"))
                or _parse_iso(refund.get("refundedAt")))
            if applied is None:
                continue
            gap_h = (applied - paid).total_seconds() / 3600
            if 0 <= gap_h <= INSTANT_REFUND_H:
                anomalies.append({
                    "anomalyId": await self.store.next_id("anomalies"),
                    "type": "instant_refund",
                    "typeName": ANOMALY_TYPE_NAMES["instant_refund"],
                    "orderId": order.get("orderId", ""),
                    "memberId": order.get("memberId"),
                    "level": "high",
                    "detail": (f"支付后 {_round2(gap_h)} 小时即申请退款"
                               f"(阈值 {INSTANT_REFUND_H}h)"),
                    "suggestion": "建议核查会员退款历史与商品质量"
                                  "(高风险单; 建议书不拦截)",
                    "detectedAt": detected_at,
                })
        for a in anomalies:
            await self.store.save("anomalies", a["anomalyId"], a)
        return {
            "scannedAt": detected_at,
            "totalOrders": len(orders),
            "anomalyCount": len(anomalies),
            "anomalies": anomalies,
            "rules": [
                f"高频下单: 同会员 24h 内 ≥{HIGH_FREQ_COUNT} 单",
                f"大额囤货: 单品数量 ≥{BULK_QTY} 或单额 ≥ ¥{BULK_AMOUNT}",
                f"秒退款: 支付后 {INSTANT_REFUND_H}h 内申请退款",
            ],
            "disposition": "异常处置为建议书; 不自动拦截任何订单",
            "note": "三类规则确定性检测(同输入同输出, LLM 禁入)",
        }

    async def anomalies(self, limit: int = 50) -> list[dict]:
        """历史异常记录列表(detectedAt 降序留痕)"""
        rows = await self.store.list("anomalies")
        rows = sorted(rows, key=lambda r: (
            str(r.get("detectedAt", "")), int(r.get("anomalyId", 0))),
            reverse=True)
        return rows[:max(1, int(limit))]
