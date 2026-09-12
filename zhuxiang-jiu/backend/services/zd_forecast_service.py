"""智单·AI智能订单大模型 P1 预测沙盘(zd_forecast_service)

「从解释过去到模拟未来」(对齐 zy_forecast_service 范式):
    - 履约 ETA: 加权预测(近 3 单履约均值 × W + 全期均值 × (1−W),
      W 为进化参数 etaRecentWeight, 默认 0.6, 安全阀 [0.4, 0.8];
      样本 0 诚实返回 None + 提示, 不编造数字)
    - What-if 三维推演: 发货延迟 → 履约时效与超时风险 /
      取消率 ± → 有效单量与 GMV / 客单价 ± → GMV(确定性线性口径)
    - 日单量滚动预测: 加权移动平均(0.6 近期 + 0.4 全期)
      + 线性趋势外推(斜率 × 步长 × 0.5 保守衰减)

铁律: 全部确定性数学公式(同输入同输出), LLM 禁入;
结论为建议书, 不自动执行。
"""

import logging
from datetime import datetime, timedelta

from services.zd_fabric_service import (
    _ZdStore, ZdFabricService, _round2, _clamp)

logger = logging.getLogger(__name__)

# 预测加权(对齐 pay60/zy 既有公式铁律: 0.6 近期 + 0.4 全期)
RECENT_WEIGHT = 0.6
FULL_WEIGHT = 0.4
ETA_WEIGHT_DEFAULT = 0.6
ETA_WEIGHT_CLAMP = (0.4, 0.8)
FULFILLMENT_BASELINE_H = 72.0   # 履约 72h 基线(超时风险口径)
TREND_DECAY = 0.5               # 趋势保守衰减(防线性外推发散)
MAX_FORECAST_PERIODS = 24


class ZdForecastService:
    """P1: 履约 ETA + What-if 沙盘 + 日单量滚动预测"""

    def __init__(self, fabric: ZdFabricService = None,
                 store: _ZdStore = None):
        self.store = store or _ZdStore()
        self.fabric = fabric or ZdFabricService(self.store)

    # ============================================================
    # 履约 ETA 加权预测
    # ============================================================

    async def eta_forecast(self) -> dict:
        """履约 ETA: 近 3 单履约均值 × W + 全期均值 × (1−W)

        W 读取进化参数(反馈闭环可调, clamp [0.4, 0.8]);
        样本 0 诚实返回 None + 提示。
        """
        durations = await self.fabric.fulfillment_durations()
        weight = await self._eta_recent_weight()
        if not durations:
            return {
                "etaHours": None, "sampleSize": 0,
                "recentAvg": None, "fullAvg": None,
                "recentWeight": weight,
                "formula": ("ETA = W×近3单履约均值 + (1−W)×全期均值"
                            f"(小时; W 为进化参数, 默认 "
                            f"{ETA_WEIGHT_DEFAULT}, clamp [0.4, 0.8])"),
                "note": "暂无履约样本(无支付→签收订单), "
                        "产生履约数据后即可预测(诚实不编造)",
            }
        recent = durations[-3:]
        recent_avg = _round2(
            sum(r["hours"] for r in recent) / len(recent))
        full_avg = _round2(
            sum(r["hours"] for r in durations) / len(durations))
        eta = _round2(weight * recent_avg + (1 - weight) * full_avg)
        return {
            "etaHours": eta,
            "sampleSize": len(durations),
            "recentAvg": recent_avg,
            "fullAvg": full_avg,
            "recentWeight": weight,
            "formula": (f"ETA = {weight}×近3单履约均值({recent_avg}h) + "
                        f"{_round2(1 - weight)}×全期均值({full_avg}h)"
                        "(确定性加权, 同输入同输出)"),
            "note": "预测为确定性公式; 实际调度决策由管理员裁定",
        }

    async def _eta_recent_weight(self) -> float:
        """ETA 近期权重(进化参数, 默认 0.6, 安全阀 [0.4, 0.8])"""
        params = await self.store.get_params()
        weight = float((params or {}).get("etaRecentWeight",
                                          ETA_WEIGHT_DEFAULT))
        return _round2(max(ETA_WEIGHT_CLAMP[0],
                           min(ETA_WEIGHT_CLAMP[1], weight)))

    # ============================================================
    # What-if 三维情景推演(确定性线性口径, 建议书)
    # ============================================================

    async def whatif(self, ship_delay_days: float = 0.0,
                     cancel_rate_delta: float = 0.0,
                     aov_delta: float = 0.0) -> dict:
        """三维 What-if: 发货延迟 / 取消率 / 客单价 → 量化影响

        弹性口径(确定性):
            履约' = 平均履约 + 延迟天数 × 24h
            超时风险占比 = clamp((履约' − 72h) / 72h, 0, 1)
            有效单量 = 已支付单量 × (1 − 取消率变动)
            GMV' = GMV × (1 − 取消率变动) × (1 + 客单价变动)
        """
        if not 0 <= ship_delay_days <= 30:
            raise ValueError(
                f"发货延迟天数须在 [0, 30](当前 {ship_delay_days})")
        if not -0.5 <= cancel_rate_delta <= 0.5:
            raise ValueError(
                f"取消率变动须在 ±50% 内(当前 {cancel_rate_delta})")
        if not -0.5 <= aov_delta <= 0.5:
            raise ValueError(
                f"客单价变动须在 ±50% 内(当前 {aov_delta})")
        ov = await self.fabric.overview()
        if not ov.get("hasData"):
            raise ValueError("暂无订单数据, 无法推演")
        baseline = {
            "paidOrders": ov["paidOrders"],
            "gmv": ov["gmv"],
            "avgOrderValue": ov["avgOrderValue"],
            "avgFulfillmentHours": ov["avgFulfillmentHours"],
            "fulfillmentSamples": ov["fulfillmentSamples"],
        }
        projected_hours = _round2(
            baseline["avgFulfillmentHours"] + ship_delay_days * 24)
        overtime_ratio = _clamp(
            (projected_hours - FULFILLMENT_BASELINE_H)
            / FULFILLMENT_BASELINE_H, 0.0, 1.0)
        est_overtime_orders = _round2(
            baseline["paidOrders"] * overtime_ratio)
        effective_orders = _round2(
            baseline["paidOrders"] * (1 - cancel_rate_delta))
        new_gmv = _round2(baseline["gmv"] * (1 - cancel_rate_delta)
                          * (1 + aov_delta))
        new_aov = (_round2(new_gmv / effective_orders)
                   if effective_orders > 0 else 0.0)
        scenario = {
            "projectedFulfillmentHours": projected_hours,
            "overtimeRiskRatio": _round2(overtime_ratio),
            "estOvertimeOrders": est_overtime_orders,
            "effectiveOrders": effective_orders,
            "gmv": new_gmv,
            "avgOrderValue": new_aov,
        }
        impacts = {
            "gmvDelta": _round2(new_gmv - baseline["gmv"]),
            "ordersDelta": _round2(
                effective_orders - baseline["paidOrders"]),
            "fulfillmentHoursDelta": _round2(
                projected_hours - baseline["avgFulfillmentHours"]),
        }
        # 应对预案(确定性模板, 建议书模式不自动执行)
        mitigations = []
        if projected_hours > FULFILLMENT_BASELINE_H:
            mitigations.append(
                f"履约预计 {projected_hours} 小时超 72h 基线"
                f"(约 {est_overtime_orders} 单超时风险): "
                "建议加急排产/增补运力预案")
        if cancel_rate_delta > 0:
            mitigations.append("取消率上升: 建议核查支付超时策略"
                               "与外呼挽回")
        if aov_delta < 0:
            mitigations.append("客单价下降: 建议评估满减/组合装"
                               "与积分撬动")
        if not mitigations:
            mitigations.append("情景向好或无不利变动, 无需特别预案")
        return {
            "baseline": baseline,
            "assumptions": {
                "shipDelayDays": ship_delay_days,
                "cancelRateDelta": cancel_rate_delta,
                "aovDelta": aov_delta,
            },
            "scenario": scenario,
            "impacts": impacts,
            "mitigations": mitigations,
            "formula": ("GMV' = GMV×(1−取消率变动)×(1+客单价变动); "
                        "履约' = 平均履约+延迟天数×24h; "
                        "超时风险 = clamp((履约'−72h)/72h, 0, 1)"
                        "(线性口径, 确定性)"),
            "disposition": "推演结论为建议书; 不自动执行任何策略调整",
        }

    # ============================================================
    # 日单量滚动预测(加权移动平均 + 线性趋势外推)
    # ============================================================

    async def volume_forecast(self, periods: int = 12) -> dict:
        """日单量滚动预测(基线 = 0.6×近3日均值 + 0.4×全期均值)

        趋势 = 全期单量对期序的线性回归斜率(× 步长 × 0.5 保守叠加);
        预测值非负 clamp; 空时序抛 ValueError(诚实不编造)。
        """
        periods = max(1, min(MAX_FORECAST_PERIODS, int(periods)))
        ov = await self.fabric.overview()
        series = ov.get("dailySeries") or []
        values = [int(r.get("orders", 0) or 0) for r in series]
        if not values:
            raise ValueError("暂无订单日时序, 无法预测")
        recent = values[-3:]
        recent_avg = sum(recent) / len(recent)
        full_avg = sum(values) / len(values)
        base = RECENT_WEIGHT * recent_avg + FULL_WEIGHT * full_avg
        slope = 0.0
        if len(values) >= 3:
            n = len(values)
            xs = list(range(n))
            mean_x = sum(xs) / n
            denom = sum((x - mean_x) ** 2 for x in xs)
            if denom > 0:
                slope = (sum((xs[i] - mean_x) * (values[i] - full_avg)
                             for i in range(n)) / denom)
        last_date = str(series[-1].get("date", ""))
        rows = []
        for step in range(1, periods + 1):
            forecast = max(0.0, base + slope * step * TREND_DECAY)
            rows.append({
                "step": step,
                "date": self._next_date(last_date, step),
                "forecastVolume": _round2(forecast),
            })
        return {
            "periods": periods,
            "rows": rows,
            "basis": {
                "recentAvg": _round2(recent_avg),
                "fullAvg": _round2(full_avg),
                "weightScheme": f"{RECENT_WEIGHT} 近期 + {FULL_WEIGHT} 全期",
                "trendSlope": _round2(slope),
                "trendDecay": TREND_DECAY,
                "historyDays": len(values),
            },
            "formula": ("基线 = 0.6×近3日单量均值 + 0.4×全期均值; "
                        "预测 = max(0, 基线 + 斜率×步长×0.5)"
                        "(加权移动平均+趋势外推, 确定性)"),
            "determinismNote": "同输入同输出(确定性公式, LLM 禁入)",
        }

    @staticmethod
    def _next_date(date_text: str, days: int) -> str:
        """基准日 + N 天(解析失败返回空串)"""
        try:
            dt = (datetime.strptime(date_text, "%Y-%m-%d")
                  + timedelta(days=days))
            return dt.strftime("%Y-%m-%d")
        except ValueError:
            return ""
