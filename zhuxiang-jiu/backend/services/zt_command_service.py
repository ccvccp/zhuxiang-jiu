"""智图·P2 全域调度引擎(zt_command_service)

创新规划 §三 P2: 全域调度+代理驾驶舱+风险雷达
    - 态势一张图: POI 负载+在途运力+异常聚合(按区域/类型下钻)
    - 异常派单: 阈值检测(排队/满座/缺货/物流失败)→责任岗位→
      《处置预案工单》建议(人工 ack 闭环)
    - 资源弹性调度: 瓶颈识别→调配方案建议书
    - 代理商驾驶舱: 辖区 POI 分布/热力代理/合规风险点
    - 管理层风险雷达: 指标分级告警+归因

铁律: 派单/调度全部建议书(ack 闭环); 确定性阈值; LLM 禁入。
"""

import logging

from services.zt_fabric_service import (
    _ZtStore, ZtFabricService, POI_TYPE_NAMES, _round2, _now_iso)

logger = logging.getLogger(__name__)

# 调度阈值(确定性)
QUEUE_WARN = 4            # 月台/门店排队 ≥4 触发
SEATS_LOW = 0.15          # 座位余量 <15% 触发(满座风险)
STOCK_LOW = 0.50          # 库存 <50% 触发(缺货风险)
FAILED_LINE = 2           # 渠道失败单 ≥2 触发

SEVERITY_NAMES = {"high": "高", "medium": "中", "low": "低"}

RESPONSIBLE_ROLES = {
    "queue_surge": ("warehouse", "仓储主管", "增开月台/引导分流"),
    "seats_full": ("operations", "运营专员", "开放预留座/引导错峰"),
    "stock_low": ("operations", "运营专员", "调拨补货/切换推荐"),
    "logistics_failed": ("service", "客服主管", "客诉安抚+换仓补发"),
}


class ZtCommandService:
    """P2: 态势一张图 + 异常派单 + 弹性调度 + 驾驶舱"""

    def __init__(self, store: _ZtStore = None,
                 fabric: ZtFabricService = None):
        self.store = store or _ZtStore()
        self.fabric = fabric or ZtFabricService(self.store)

    # ============================================================
    # 态势一张图(只读聚合)
    # ============================================================

    async def situation(self) -> dict:
        """全域态势: POI 负载/异常计数/在途运力(确定性聚合)"""
        pois = await self.fabric.list_pois()
        by_type: dict[str, dict] = {}
        for p in pois:
            t = by_type.setdefault(p["poiType"], {
                "count": 0, "open": 0, "anomalies": 0})
            t["count"] += 1
            if p.get("open"):
                t["open"] += 1
            q = p.get("queue", {})
            if q.get("waiting", 0) >= QUEUE_WARN:
                t["anomalies"] += 1
            seats = p.get("seats")
            if (isinstance(seats, dict) and seats.get("total")
                    and seats["available"] / seats["total"] < SEATS_LOW):
                t["anomalies"] += 1
            if float(p.get("stockLevel", 1) or 0) < STOCK_LOW:
                t["anomalies"] += 1

        from repositories.logistics_repository import (
            LogisticsRepository, CARRIER_NAMES)
        repo = LogisticsRepository()
        orders = await repo.list_orders(limit=200)
        in_transit = sum(1 for o in orders
                         if o.get("status") in ("booked", "picked",
                                                "transporting",
                                                "delivering"))
        failed_by_carrier: dict[str, int] = {}
        for o in orders:
            if o.get("status") == "failed":
                c = o.get("carrier", "")
                failed_by_carrier[c] = failed_by_carrier.get(c, 0) + 1

        return {
            "poiLayers": [
                {"poiType": t, "poiTypeName": POI_TYPE_NAMES.get(t, t),
                 **stats}
                for t, stats in sorted(by_type.items())],
            "logistics": {
                "inTransit": in_transit,
                "failedByCarrier": failed_by_carrier,
                "carrierNames": CARRIER_NAMES,
            },
            "thresholds": {
                "queueWarn": QUEUE_WARN, "seatsLow": SEATS_LOW,
                "stockLow": STOCK_LOW, "failedLine": FAILED_LINE,
            },
            "note": "态势一张图为只读聚合; 下钻经 tickets 派单",
            "generatedAt": _now_iso(),
        }

    # ============================================================
    # 异常扫描 + 派单工单(建议书, ack 闭环)
    # ============================================================

    async def scan(self) -> dict:
        """全域异常扫描→《处置预案工单》草稿(永不自动执行)"""
        pois = await self.fabric.list_pois()
        tickets = []

        for p in pois:
            q = p.get("queue", {})
            if q.get("waiting", 0) >= QUEUE_WARN:
                tickets.append(await self._ticket(
                    "queue_surge", p,
                    f"{p['name']} 排队 {q['waiting']} 人/车"
                    f"(阈值 {QUEUE_WARN})"))
            seats = p.get("seats")
            if (isinstance(seats, dict) and seats.get("total")
                    and seats["available"] / seats["total"] < SEATS_LOW):
                tickets.append(await self._ticket(
                    "seats_full", p,
                    f"{p['name']} 座位余量 "
                    f"{seats['available']}/{seats['total']}"
                    f"(阈值 {SEATS_LOW:.0%})"))
            if float(p.get("stockLevel", 1) or 0) < STOCK_LOW:
                tickets.append(await self._ticket(
                    "stock_low", p,
                    f"{p['name']} 库存水位 "
                    f"{p.get('stockLevel', 0):.0%}"
                    f"(阈值 {STOCK_LOW:.0%})"))

        from repositories.logistics_repository import (
            LogisticsRepository, CARRIER_NAMES)
        orders = await LogisticsRepository().list_orders(limit=200)
        failed_by_carrier: dict[str, list] = {}
        for o in orders:
            if o.get("status") == "failed":
                failed_by_carrier.setdefault(o.get("carrier", ""),
                                              []).append(
                    o.get("waybillNo", ""))
        for carrier, waybills in failed_by_carrier.items():
            if len(waybills) >= FAILED_LINE:
                name = CARRIER_NAMES.get(carrier, carrier)
                tickets.append(await self._ticket(
                    "logistics_failed", None,
                    f"{name} 渠道失败单 {len(waybills)} 条"
                    f"(阈值 {FAILED_LINE})",
                    waybills=waybills[:5]))

        by_sev = {}
        for t in tickets:
            by_sev[t["severity"]] = by_sev.get(t["severity"], 0) + 1
        return {
            "generated": len(tickets), "bySeverity": by_sev,
            "tickets": tickets,
            "note": "工单为预案建议; ack 确认后执行, 永不自动",
            "scannedAt": _now_iso(),
        }

    async def _ticket(self, anomaly_type: str, poi: dict | None,
                      detail: str, waybills: list = None) -> dict:
        role, duty, action = RESPONSIBLE_ROLES[anomaly_type]
        severity = "high" if anomaly_type in ("queue_surge",
                                              "logistics_failed") \
            else "medium"
        record = {
            "ticketId": await self.store.next_id("zt_tickets"),
            "anomalyType": anomaly_type,
            "severity": severity,
            "severityName": SEVERITY_NAMES[severity],
            "poi": ({"poiCode": poi["poiCode"], "name": poi["name"],
                     "longitude": poi["longitude"],
                     "latitude": poi["latitude"]}
                    if poi else None),
            "detail": detail, "waybills": waybills or [],
            "responsible": {"role": role, "duty": duty},
            "disposition": {"action": action,
                            "mode": "建议书: 人工确认后执行"},
            "status": "pending",
            "raisedAt": _now_iso(),
        }
        await self.store.save("zt_tickets",
                              record["ticketId"], record)
        return record

    async def tickets(self, status: str = None) -> list[dict]:
        rows = await self.store.list("zt_tickets")
        if status:
            rows = [r for r in rows if r.get("status") == status]
        return sorted(rows, key=lambda r: r.get("raisedAt", ""),
                      reverse=True)

    async def ack_ticket(self, ticket_id: int,
                         disposition: str) -> dict:
        """工单确认闭环(acked 确认 / dismissed 驳回=负样本)"""
        record = await self.store.get("zt_tickets", ticket_id)
        if record is None:
            raise KeyError(f"工单不存在(ticketId={ticket_id})")
        if disposition not in ("acked", "dismissed"):
            raise ValueError("处置无效(acked/dismissed)")
        if record.get("status") != "pending":
            raise ValueError(f"工单已处置({record['status']})")
        record["status"] = disposition
        record["acknowledgedAt"] = _now_iso()
        if disposition == "dismissed":
            record["negativeSample"] = (
                "驳回记负样本: 阈值/预案校准观测(决策权在人工)")
        await self.store.save("zt_tickets", ticket_id, record)
        return record

    # ============================================================
    # 代理商驾驶舱 / 管理层风险雷达(确定性)
    # ============================================================

    async def agent_cockpit(self, region: str = "成都") -> dict:
        """辖区驾驶舱: POI 分布/热力代理(评分×库存)/合规风险点"""
        pois = [p for p in await self.fabric.list_pois()
                if p.get("city") == region]
        if not pois:
            raise KeyError(f"辖区无 POI(region={region})")
        heat = []
        for p in pois:
            seats = p.get("seats")
            load = (seats["available"] / seats["total"]
                    if isinstance(seats, dict) and seats.get("total")
                    else float(p.get("stockLevel", 0) or 0))
            heat.append({
                "poiCode": p["poiCode"], "name": p["name"],
                "poiType": p["poiType"],
                "heat": _round2(float(p.get("rating", 0)) * (1 - load)
                                * 100),
                "risk": (not p.get("open"))
                        or float(p.get("stockLevel", 1)) < STOCK_LOW,
            })
        heat.sort(key=lambda h: -h["heat"])
        risk_points = [h for h in heat if h["risk"]]
        return {
            "region": region, "poiCount": len(pois),
            "heatTop": heat[:5],
            "complianceRisks": risk_points,
            "complianceNote": "风险=停业/低库存(确定性口径); "
                              "窜货/价格异常接入需风控模块数据",
            "note": "驾驶舱为观测面; 营销决策须人工",
            "generatedAt": _now_iso(),
        }

    async def risk_radar(self) -> dict:
        """管理层风险雷达: 三域指标分级(确定性阈值)"""
        pois = await self.fabric.list_pois()
        closed = [p for p in pois if not p.get("open")]
        low_stock = [p for p in pois
                     if float(p.get("stockLevel", 1) or 0) < STOCK_LOW]
        from repositories.logistics_repository import (
            LogisticsRepository)
        orders = await LogisticsRepository().list_orders(limit=200)
        failed = [o for o in orders if o.get("status") == "failed"]
        signed = [o for o in orders if o.get("status") == "signed"]

        items = []
        if len(closed) >= 2:
            items.append({"domain": "运营", "level": "medium",
                          "detail": f"{len(closed)} 个 POI 停业"})
        if low_stock:
            items.append({"domain": "供应链", "level": "high",
                          "detail": f"{len(low_stock)} 个 POI 库存低于 "
                          f"{STOCK_LOW:.0%}"})
        if failed:
            rate = _round2(len(failed) / max(1, len(failed) + len(signed)))
            level = "high" if rate > 0.2 else "medium" if rate > 0.1 \
                else "low"
            items.append({"domain": "物流", "level": level,
                          "detail": f"失败率 {rate:.0%}"
                                     f"({len(failed)} 单)"})
        if not items:
            items.append({"domain": "全域", "level": "low",
                          "detail": "各域指标均在阈值内"})
        return {
            "items": items,
            "signed": len(signed), "failed": len(failed),
            "disposition": "雷达为观测面; 处置走派单工单(人工确认)",
            "scannedAt": _now_iso(),
        }
