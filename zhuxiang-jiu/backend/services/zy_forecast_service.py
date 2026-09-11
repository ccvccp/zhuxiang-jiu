"""智启元·AI智能财务大模型 P1 预测与情景沙盘(zy_forecast_service)

「从解释过去到模拟未来」(设计文档 §3 财务分析与预测):
    - 滚动预测: 12 期月度预测(加权移动平均 0.6 近期 + 0.4 全期,
      对齐 pay60 现金流预测既有公式铁律) + 预测偏差自动归因留痕
    - What-if 情景沙盘: 售价/销量/成本三维假设 → 秒级多维影响推演
      (收入/成本/税负/净利) + 应对预案建议(建议书模式)
    - 驱动因素建模: 收入与候选因子(订单数/客单价/销量/退款)的
      确定性相关性(Pearson)识别 + 敏感性排序

铁律: 全部确定性数学公式(同输入同输出), LLM 禁入。
"""

import logging
import math
from datetime import datetime, UTC

from services.zy_data_service import ZyDataService, _round2
from services.zy_tax_service import ZyTaxService

logger = logging.getLogger(__name__)

# 预测加权(对齐 PAY60 铁律: 0.6 近期 + 0.4 全期)
RECENT_WEIGHT = 0.6
FULL_WEIGHT = 0.4
FORECAST_HORIZON = 12


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


class ZyForecastService:
    """P1: 滚动预测 + What-if 沙盘 + 驱动因素"""

    def __init__(self, data: ZyDataService = None,
                 tax: ZyTaxService = None):
        self.data = data or ZyDataService()
        self.tax = tax or ZyTaxService()

    # ============================================================
    # 滚动预测(加权移动平均 + 线性趋势外推)
    # ============================================================

    async def rolling_forecast(self, horizon: int = FORECAST_HORIZON,
                               use_trend: bool = True) -> dict:
        """未来 N 期月度预测(收入/成本/税/净利/现金)

        算法(确定性):
            基线 = 0.6 × 近 3 月均值 + 0.4 × 全期均值
            趋势 = 全期净收入对期序的线性回归斜率(可选叠加)
        """
        horizon = max(1, min(12, int(horizon)))
        series = (await self.data.monthly_series(months=24))
        if not series:
            raise ValueError("暂无财务数据, 无法预测")
        metrics = ("netAmount", "costAmount", "taxAmount",
                   "netProfit")

        def forecast_metric(key: str, use_trend_flag: bool) -> float:
            values = [r[key] for r in series]
            recent = (sum(values[-3:]) / len(values[-3:])
                      if values else 0.0)
            full = sum(values) / len(values)
            base = RECENT_WEIGHT * recent + FULL_WEIGHT * full
            if use_trend_flag and len(values) >= 3:
                n = len(values)
                xs = list(range(n))
                mean_x = sum(xs) / n
                mean_y = full
                denom = sum((x - mean_x) ** 2 for x in xs)
                if denom > 0:
                    slope = (sum((xs[i] - mean_x)
                                 * (values[i] - mean_y)
                                 for i in range(n)) / denom)
                    # 趋势衰减 50% 保守叠加, 防线性外推发散
                    base += slope * horizon * 0.5
            return _round2(base)

        rows = []
        for step in range(1, horizon + 1):
            row = {"step": step}
            for key in metrics:
                row[key] = forecast_metric(key, use_trend)
            rows.append(row)
        basis = {
            "recentAvg": {m: _round2(
                sum(r[m] for r in series[-3:]) / len(series[-3:]))
                for m in metrics},
            "fullAvg": {m: _round2(
                sum(r[m] for r in series) / len(series))
                for m in metrics},
            "weightScheme": f"{RECENT_WEIGHT} 近期 + {FULL_WEIGHT} 全期",
            "trendApplied": use_trend,
            "historyMonths": len(series),
        }
        return {
            "horizon": horizon,
            "rows": rows,
            "basis": basis,
            "determinismNote": "同输入同输出(加权移动平均+线性趋势, "
                                "确定性公式)",
        }

    # ============================================================
    # What-if 情景沙盘
    # ============================================================

    async def sandbox(self, price_delta: float = 0.0,
                      volume_delta: float = 0.0,
                      cost_delta: float = 0.0) -> dict:
        """情景推演: 三维假设 → 收入/成本/税负/净利影响

        基线取最近月度; 变动为小数(0.1=+10%, -0.05=-5%)。
        弹性口径(确定性):
            收入 = 基线收入 × (1+价) × (1+量)
            成本 = 基线成本 × (1+量) × (1+本)
            税负 = 模拟器按新净收入重算
            净利 = 收入 - 成本 - 税负
        """
        for name, value in (("售价", price_delta), ("销量", volume_delta),
                            ("成本", cost_delta)):
            if not -0.5 <= value <= 0.5:
                raise ValueError(
                    f"{name}变动幅度须在 ±50% 内(当前 {value})")
        snap = await self.data.snapshot()
        if not snap.get("hasData"):
            raise ValueError("暂无财务数据, 无法沙盘推演")
        cur = snap["series"][-1]
        base = {
            "revenue": cur["netAmount"],
            "cost": cur["costAmount"],
            "quantity": cur["quantity"],
            "netProfit": cur["netProfit"],
            "taxAmount": cur["taxAmount"],
        }
        new_quantity = base["quantity"] * (1 + volume_delta)
        new_revenue = _round2(base["revenue"] * (1 + price_delta)
                              * (1 + volume_delta))
        new_cost = _round2(base["cost"] * (1 + volume_delta)
                           * (1 + cost_delta))
        new_tax = self.tax.simulate_burden(new_revenue,
                                           int(new_quantity))
        new_profit = _round2(new_revenue - new_cost - new_tax)
        scenario = {
            "revenue": new_revenue, "cost": new_cost,
            "tax": new_tax, "netProfit": new_profit,
        }
        impacts = {
            "revenue": _round2(new_revenue - base["revenue"]),
            "cost": _round2(new_cost - base["cost"]),
            "tax": _round2(new_tax - base["taxAmount"]),
            "netProfit": _round2(new_profit - base["netProfit"]),
        }
        # 应对预案(确定性模板, 建议书模式不自动执行)
        mitigations = []
        if new_profit < base["netProfit"]:
            mitigations.append(
                "净利下降: 可评估 /api/zy/tax/simulate 交易结构优化"
                "或成本端压缩预案")
        if cost_delta > 0.1:
            mitigations.append(
                "成本上升超 10%: 建议核查采购价格与库存周转")
        if price_delta < 0 and volume_delta < 0:
            mitigations.append(
                "量价齐跌: 建议关注驱动因素排序(/api/zy/drivers)"
                "与营销投入弹性")
        if not mitigations:
            mitigations.append("情景向好, 无需特别预案")
        return {
            "baseline": {"period": cur["month"], **base,
                         "tax": cur["taxAmount"]},
            "assumptions": {
                "priceDelta": price_delta,
                "volumeDelta": volume_delta,
                "costDelta": cost_delta,
            },
            "scenario": scenario,
            "impacts": impacts,
            "mitigations": mitigations,
            "note": "确定性弹性推演; 预案为建议书, 不自动执行",
        }

    # ============================================================
    # 驱动因素建模(确定性相关性)
    # ============================================================

    async def drivers(self) -> dict:
        """收入关键驱动因子识别(Pearson 相关 + 敏感性排序)

        候选因子: 订单数 / 销量 / 客单价 / 退款率(反向)
        """
        series = await self.data.monthly_series(months=24)
        if len(series) < 3:
            raise ValueError("样本不足(需 ≥3 月), 无法驱动建模")
        revenues = [r["netAmount"] for r in series]

        def pearson(xs: list, ys: list) -> float:
            n = len(xs)
            mean_x, mean_y = sum(xs) / n, sum(ys) / n
            cov = sum((xs[i] - mean_x) * (ys[i] - mean_y)
                      for i in range(n))
            var_x = sum((x - mean_x) ** 2 for x in xs)
            var_y = sum((y - mean_y) ** 2 for y in ys)
            denom = math.sqrt(var_x * var_y)
            return _round2(cov / denom) if denom > 0 else 0.0

        candidates = []
        order_counts = [float(r["orderCount"]) for r in series]
        quantities = [float(r["quantity"]) for r in series]
        unit_prices = [(r["netAmount"] / r["orderCount"]
                        if r["orderCount"] else 0.0)
                       for r in series]
        refund_rates = [(r["refundAmount"] / r["salesAmount"]
                        if r["salesAmount"] else 0.0)
                        for r in series]
        for name, series_values, direction in (
                ("订单数", order_counts, +1),
                ("销量", quantities, +1),
                ("客单价", unit_prices, +1),
                ("退款率", refund_rates, -1)):
            corr = pearson(series_values, revenues) * direction
            candidates.append({
                "factor": name,
                "correlation": _round2(
                    pearson(series_values, revenues)),
                "effectiveCorrelation": corr,
                "direction": "正向" if direction > 0 else "反向",
                "sensitivity": abs(corr),
            })
        candidates.sort(key=lambda c: (-c["sensitivity"], c["factor"]))
        return {
            "months": len(series),
            "drivers": candidates,
            "note": "确定性 Pearson 相关; 排序=|有效相关性|",
        }
