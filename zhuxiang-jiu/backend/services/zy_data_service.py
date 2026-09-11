"""智启元·AI智能财务大模型 数据织物层(zy_data_service)

「Data Fabric」——四层架构 L3 数据底座(设计文档 §二):
    订单/凭证/税表/付款 多源只读聚合 → 统一月度财务时序,
    供 P0 问答分析 / P1 预测沙盘 / P2 税务优化 / P3 进化决策
    全部 Agent 共享, 编排式零回写(对齐 68号信值聚合红线)。

口径(与 finance_service 保持一致):
    - 收入: 已支付订单 actualAmount(含税), 退款单冲减
    - 成本: 订单 items 数量 × 商品成本价(product.cost_price,
      缺省按售价 40% 毛利率估算)
    - 税负: 增值税 13% / 消费税从价 20% + 从量 0.5元/斤 / 所得税 25%
    - 月度 key: YYYYMM

铁律: 纯只读聚合 + 全数字来自查询层(数字永不出现在模板拼接层)。
"""

import logging

from repositories.finance_repository import FinanceRepository
from repositories.order_repository import OrderRepository
from repositories.product_repository import ProductRepository

logger = logging.getLogger(__name__)

VAT_RATE = 0.13
CONSUMPTION_AD_VALOREM = 0.20
CONSUMPTION_PER_JIN = 0.5
INCOME_TAX_RATE = 0.25
COST_FALLBACK_RATIO = 0.4   # 无成本价时按售价 60% 毛利估算成本


def _round2(v: float) -> float:
    return round(float(v or 0), 2)


def _month_key(iso_ts: str) -> str:
    """ISO 时间 → YYYYMM(空/异常返回空串, 跳过聚合)"""
    text = str(iso_ts or "")
    digits = text[:10].replace("-", "").replace("/", "")
    return digits[:6] if len(digits) >= 6 else ""


class ZyDataService:
    """智启元数据织物: 多源只读 → 月度财务时序"""

    def __init__(self):
        self.order_repo = OrderRepository()
        self.finance_repo = FinanceRepository()
        self.product_repo = ProductRepository()

    # ============================================================
    # 核心聚合: 月度财务时序
    # ============================================================

    async def monthly_series(self, months: int = 12) -> list[dict]:
        """近 N 月财务时序(收入/退款/净收入/成本/税负/净利/订单数/销量)

        Returns:
            按 YYYYMM 升序的月度行; 空月份跳过(无业务月份不造零行,
            与既有报表口径一致——避免冷启动月拉低预测基线)。
        """
        months = max(1, min(24, int(months)))
        products = await self._product_cost_map()
        orders = await self.order_repo.list_all()
        rows: dict[str, dict] = {}
        for order in orders:
            status = order.get("status", "")
            created = _month_key(order.get("createdAt", ""))
            if not created:
                continue
            paid = (order.get("payment") or {}).get("paidAt", "")
            items = order.get("items", []) or []
            quantity = sum(int(i.get("quantity", 0) or 0)
                            for i in items)
            cost = self._order_cost(items, products)
            row = rows.setdefault(created, self._blank_row(created))
            if status == "REFUNDED":
                refund_at = _month_key(
                    (order.get("refund") or {}).get("refundedAt", ""))
                refund_amount = _round2(
                    (order.get("refund") or {}).get(
                        "refundedAmount", 0)
                    or order.get("priceDetail", {}).get("actualAmount", 0))
                if refund_at:
                    rrow = rows.setdefault(refund_at,
                                           self._blank_row(refund_at))
                    rrow["refundAmount"] = _round2(
                        rrow["refundAmount"] + refund_amount)
                    rrow["refundCount"] += 1
                # 退款冲减当月净额(收入发生月)
                row["netAmount"] = _round2(
                    row["netAmount"] - refund_amount)
                continue
            if not paid and status not in ("PAID", "COMPLETED",
                                           "SHIPPED", "DELIVERED"):
                continue   # 未支付订单不计收入(对齐财务口径)
            amount = _round2(
                (order.get("priceDetail") or {}).get("actualAmount", 0))
            row["salesAmount"] = _round2(row["salesAmount"] + amount)
            row["netAmount"] = _round2(row["netAmount"] + amount)
            row["orderCount"] += 1
            row["quantity"] += quantity
            row["costAmount"] = _round2(row["costAmount"] + cost)
        # 税负(按月净收入不含税口径)
        for _key, row in rows.items():
            row["taxAmount"] = self._tax_burden(row["netAmount"],
                                                row["quantity"])
            row["netProfit"] = _round2(
                row["netAmount"] - row["costAmount"] - row["taxAmount"])
        series = [rows[k] for k in sorted(rows)][-months:]
        return series

    # ============================================================
    # 快照: 最新期汇总(问答/健康度用)
    # ============================================================

    async def snapshot(self) -> dict:
        """最新月度快照 + 环比上月(无历史时环比全 0)"""
        series = await self.monthly_series(months=24)
        if not series:
            return {"month": "", "hasData": False}
        current = series[-1]
        previous = series[-2] if len(series) >= 2 else self._blank_row("")
        return {
            "hasData": True,
            "month": current["month"],
            "prevMonth": previous.get("month", ""),
            "salesAmount": current["salesAmount"],
            "netAmount": current["netAmount"],
            "costAmount": current["costAmount"],
            "taxAmount": current["taxAmount"],
            "netProfit": current["netProfit"],
            "orderCount": current["orderCount"],
            "quantity": current["quantity"],
            "refundAmount": current["refundAmount"],
            "mom": self._mom(current, previous),
            "series": series,
        }

    # ============================================================
    # 内部工具
    # ============================================================

    @staticmethod
    def _blank_row(month: str) -> dict:
        return {
            "month": month, "salesAmount": 0.0, "refundAmount": 0.0,
            "netAmount": 0.0, "costAmount": 0.0, "taxAmount": 0.0,
            "netProfit": 0.0, "orderCount": 0, "quantity": 0,
            "refundCount": 0,
        }

    @staticmethod
    def _mom(current: dict, previous: dict) -> dict:
        """环比(上月为 0 时返回 0.0 防除零)"""
        def ratio(cur: float, prev: float) -> float:
            return _round2((cur - prev) / prev) if prev else 0.0
        return {
            "netAmount": ratio(current["netAmount"],
                              previous.get("netAmount", 0)),
            "netProfit": ratio(current["netProfit"],
                                previous.get("netProfit", 0)),
            "orderCount": ratio(float(current["orderCount"]),
                                float(previous.get("orderCount", 0))),
        }

    @staticmethod
    def _tax_burden(net_amount: float, quantity: int) -> float:
        """税负估算(与 calc_tax 同口径: 增值+消费+所得税)"""
        without_tax = net_amount / (1 + VAT_RATE)
        vat = without_tax * VAT_RATE
        consumption = (without_tax * CONSUMPTION_AD_VALOREM
                       + quantity * CONSUMPTION_PER_JIN)
        taxable = max(0.0, net_amount - without_tax * COST_FALLBACK_RATIO
                      - vat - consumption)
        income_tax = taxable * INCOME_TAX_RATE
        return _round2(vat + consumption + income_tax)

    async def _product_cost_map(self) -> dict:
        """商品成本价映射(product_id → cost_price)"""
        try:
            products = await self.product_repo.list_all()
            return {
                str(p.get("product_id") or p.get("id") or ""):
                    float(p.get("cost_price") or 0)
                for p in products
            }
        except Exception as exc:
            logger.warning("zy_product_cost_unavailable: %s", exc)
            return {}

    @staticmethod
    def _order_cost(items: list, cost_map: dict) -> float:
        """订单成本(items 数量 × 成本价; 缺成本价按 40% 售价估算)"""
        total = 0.0
        for item in items or []:
            quantity = int(item.get("quantity", 0) or 0)
            if not quantity:
                continue
            price = float(item.get("price", 0) or 0)
            pid = str(item.get("productId") or item.get("product_id")
                      or "")
            cost = cost_map.get(pid, 0)
            if not cost:
                cost = price * COST_FALLBACK_RATIO
            total += quantity * cost
        return _round2(total)
