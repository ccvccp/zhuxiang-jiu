"""智酿运通 P4b 渠道熔断与切换引擎(zw_circuit_service)

创新规划 §三 P4b: 多源运力的"指挥官"
    - 三指标监测(确定性): 揽收率(下单→揽收转化) / 中转时效(均签收时长)
      / 异常率(四检测器命中数÷非终态单)
    - 熔断状态机(建议态): closed 健康 / half_open 逼近阈值 / open 跌破
    - open 渠道 → 《运力异常报告》: 备选渠道按质量分排序 + 按分值比例
      流量再分配建议(承接比例合计 100%)

铁律: 熔断为观测面状态; 流量切换永不自动执行(建议书模式)。
"""

import logging

from services.zw_fabric_service import (
    _ZwStore, ZwFabricService, _round2, _now_iso)
from services.zw_track_service import ZwTrackService
from services.zw_route_service import ZwRouteService

logger = logging.getLogger(__name__)

# 三指标阈值(确定性)
PICKUP_RATE_LINE = 0.90       # 揽收率低于 90% → 异常
PICKUP_RATE_WARN = 0.95      # 低于 95% → 逼近(半开)
TRANSIT_HOURS_LINE = 96.0    # 均中转时效超过 96h → 异常
TRANSIT_HOURS_WARN = 72.0    # 超过 72h → 逼近
ANOMALY_RATE_LINE = 0.30     # 异常率超过 30% → 异常
ANOMALY_RATE_WARN = 0.20     # 超过 20% → 逼近
MIN_SAMPLE = 3               # 样本不足 → unknown

CIRCUIT_STATE_NAMES = {
    "closed": "健康(闭合)", "half_open": "逼近阈值(半开)",
    "open": "跌破阈值(建议熔断)", "unknown": "样本不足",
}


class ZwCircuitService:
    """P4b: 渠道熔断监测 + 流量切换建议 + 运力异常报告"""

    def __init__(self, store: _ZwStore = None,
                 fabric: ZwFabricService = None,
                 track: ZwTrackService = None,
                 route: ZwRouteService = None):
        self.store = store or _ZwStore()
        self.fabric = fabric or ZwFabricService(self.store)
        self.track = track or ZwTrackService(store=self.store)
        self.route = route or ZwRouteService(store=self.store,
                                            fabric=self.fabric)

    # ============================================================
    # 渠道三指标聚合(数据织物+异常检测器实时计算)
    # ============================================================

    async def _carrier_metrics(self) -> dict[str, dict]:
        """按渠道聚合三指标(揽收率/中转时效/异常率)"""
        from repositories.logistics_repository import (
            LogisticsRepository, CARRIER_NAMES)
        repo = LogisticsRepository()
        orders = await repo.list_orders(limit=500)
        anomalies = await self.track.detect_anomalies(limit=500)

        stats: dict[str, dict] = {}
        for carrier in CARRIER_NAMES:
            stats[carrier] = {"name": CARRIER_NAMES[carrier],
                              "total": 0, "picked": 0,
                              "durations": [], "anomalyCount": 0,
                              "nonFinal": 0}
        for o in orders:
            carrier = str(o.get("carrier", "") or "")
            if carrier not in stats:
                continue
            s = stats[carrier]
            s["total"] += 1
            status = o.get("status", "")
            if status != "pending":           # 已下单即计入揽收口径
                s["picked"] += 1
            if status in ("booked", "picked", "transporting",
                          "delivering"):
                s["nonFinal"] += 1
                created = str(o.get("createdAt", "") or "")
                signed_at = str(o.get("signedAt", "") or "")
                if created and signed_at:
                    from datetime import datetime
                    try:
                        c = datetime.fromisoformat(created)
                        st = datetime.fromisoformat(signed_at)
                        hours = (st - c).total_seconds() / 3600
                        if hours > 0:
                            s["durations"].append(hours)
                    except (ValueError, TypeError):
                        pass
        for a in anomalies:
            carrier = str(a.get("carrier", "") or "")
            if carrier in stats:
                stats[carrier]["anomalyCount"] += 1

        metrics = {}
        for carrier, s in stats.items():
            if s["total"] < MIN_SAMPLE:
                metrics[carrier] = {
                    "carrier": carrier, "carrierName": s["name"],
                    "sample": s["total"], "state": "unknown",
                }
                continue
            pickup_rate = _round2(s["picked"] / s["total"])
            avg_hours = _round2(sum(s["durations"]) / len(s["durations"])) \
                if s["durations"] else 0.0
            anomaly_rate = _round2(s["anomalyCount"] / s["total"])
            metrics[carrier] = {
                "carrier": carrier, "carrierName": s["name"],
                "sample": s["total"],
                "pickupRate": pickup_rate,
                "avgTransitHours": avg_hours,
                "anomalyRate": anomaly_rate,
                "nonFinal": s["nonFinal"],
            }
        return metrics

    # ============================================================
    # 熔断状态机(确定性规则)
    # ============================================================

    @staticmethod
    def _circuit_state(m: dict) -> tuple[str, list[str]]:
        """单渠道熔断状态判定

        open: 任一指标跌破阈值; half_open: 任一指标逼近; closed: 健康
        (时效无签收样本时不参与判定——诚实降级, 不以 0 判罪)
        """
        breaches, warns = [], []
        if m.get("pickupRate", 1.0) < PICKUP_RATE_LINE:
            breaches.append(f"揽收率 {m['pickupRate']:.0%}<"
                           f"{PICKUP_RATE_LINE:.0%}")
        elif m.get("pickupRate", 1.0) < PICKUP_RATE_WARN:
            warns.append(f"揽收率 {m['pickupRate']:.0%}逼近"
                        f"{PICKUP_RATE_WARN:.0%}")
        hours = m.get("avgTransitHours") or 0
        if hours > 0:
            if hours > TRANSIT_HOURS_LINE:
                breaches.append(f"中转 {hours}h>{TRANSIT_HOURS_LINE:.0f}h")
            elif hours > TRANSIT_HOURS_WARN:
                warns.append(f"中转 {hours}h>{TRANSIT_HOURS_WARN:.0f}h")
        if m.get("anomalyRate", 0) > ANOMALY_RATE_LINE:
            breaches.append(f"异常率 {m['anomalyRate']:.0%}>"
                           f"{ANOMALY_RATE_LINE:.0%}")
        elif m.get("anomalyRate", 0) > ANOMALY_RATE_WARN:
            warns.append(f"异常率 {m['anomalyRate']:.0%}逼近"
                        f"{ANOMALY_RATE_WARN:.0%}")
        state = "open" if breaches else "half_open" if warns else "closed"
        return state, breaches + warns

    async def circuit_status(self) -> dict:
        """全渠道熔断状态总览(观测面)"""
        metrics = await self._carrier_metrics()
        carriers = []
        for _, m in metrics.items():
            if m.get("state") == "unknown":
                carriers.append({
                    **m, "stateName": CIRCUIT_STATE_NAMES["unknown"],
                    "reasons": [f"样本不足(<{MIN_SAMPLE})"]})
                continue
            state, reasons = self._circuit_state(m)
            carriers.append({**m, "state": state,
                             "stateName": CIRCUIT_STATE_NAMES[state],
                             "reasons": reasons})
        open_count = sum(1 for c in carriers if c["state"] == "open")
        return {
            "carriers": carriers,
            "openCount": open_count,
            "thresholds": {
                "pickupRateLine": PICKUP_RATE_LINE,
                "pickupRateWarn": PICKUP_RATE_WARN,
                "transitHoursLine": TRANSIT_HOURS_LINE,
                "transitHoursWarn": TRANSIT_HOURS_WARN,
                "anomalyRateLine": ANOMALY_RATE_LINE,
                "anomalyRateWarn": ANOMALY_RATE_WARN,
            },
            "disposition": "熔断为观测面状态; 切换永不自动执行(建议书制)",
            "monitoredAt": _now_iso(),
        }

    # ============================================================
    # 流量切换建议(备选渠道按质量分比例分配, 承接合计 100%)
    # ============================================================

    def _switch_suggestion(self, open_carrier: dict,
                            quality_scores: dict) -> dict:
        """open 渠道的流量再分配建议(确定性: 质量分占比)"""
        carrier = open_carrier["carrier"]
        candidates = [
            {"carrier": c, "carrierName": s.get("carrierName", c),
             "qualityScore": s.get("score", 70.0)}
            for c, s in quality_scores.items() if c != carrier
        ]
        candidates.sort(key=lambda x: x["qualityScore"], reverse=True)
        if not candidates:
            return {
                "title": "运力异常报告",
                "targetCarrier": open_carrier["carrierName"],
                "reallocation": [],
                "body": f"{open_carrier['carrierName']} 跌破阈值但无备选渠道, "
                        "建议人工评估注册新运力(register-carrier)",
                "disposition": "建议书模式: 切换须人工确认, 永不自动执行",
            }
        total_score = sum(c["qualityScore"] for c in candidates) or 1.0
        for c in candidates:
            c["sharePct"] = _round2(
                100 * c["qualityScore"] / total_score)
        # 比例修正(合计 100)
        diff = _round2(100 - sum(c["sharePct"] for c in candidates))
        candidates[0]["sharePct"] = _round2(
            candidates[0]["sharePct"] + diff)

        reasons = "; ".join(open_carrier.get("reasons", []))
        return {
            "title": "运力异常报告",
            "targetCarrier": open_carrier["carrierName"],
            "reasons": reasons,
            "reallocation": candidates,
            "body": (f"{open_carrier['carrierName']} 触发熔断阈值"
                     f"({reasons}), 建议按质量分比例切换流量: "
                     + ", ".join(f"{c['carrierName']} {c['sharePct']}%"
                                for c in candidates)),
            "disposition": "建议书模式: 切换须人工确认, 永不自动执行",
        }

    async def generate_report(self) -> dict:
        """生成《运力异常报告》(open 渠道各一份, 落库留痕)"""
        status = await self.circuit_status()
        quality = await self.route.carrier_scores()
        open_carriers = [c for c in status["carriers"]
                         if c.get("state") == "open"]
        suggestions = [self._switch_suggestion(c, quality)
                      for c in open_carriers]
        report = {
            "reportId": await self.store.next_id("capacity_reports"),
            "openCount": len(open_carriers),
            "openCarriers": open_carriers,
            "switchSuggestions": suggestions,
            "healthyCount": sum(1 for c in status["carriers"]
                                if c.get("state") == "closed"),
            "disposition": "《运力异常报告》为建议书; 人工确认后执行切换",
            "generatedAt": _now_iso(),
        }
        await self.store.save("capacity_reports",
                              report["reportId"], report)
        return report

    async def reports(self, limit: int = 20) -> list[dict]:
        rows = await self.store.list("capacity_reports")
        return sorted(rows, key=lambda r: r.get("generatedAt", ""),
                      reverse=True)[:limit]
