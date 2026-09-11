"""智图·P1 资源聚合引擎(zt_resource_service)

创新规划 §三 P1: 全业务资源聚合
    - POI 统一时空底座: 种子+配置化注册(零代码接入新 POI)
    - 供货商月台预约: 时段预约→排队最短月台分配建议+收货状态可视
    - B 端多仓货源匹配: 库存/距离(haversine)/时效→推荐仓组合+成本测算
    - 履约看板: 多仓在途/异常聚合视图(数据源: 既有物流订单只读)

铁律: 分配为建议书(人工确认); 全部确定性公式; LLM 禁入。
"""

import logging

from repositories.location_repository import haversine_km
from services.zt_fabric_service import (
    _ZtStore, ZtFabricService, POI_TYPE_NAMES, _round2, _now_iso)

logger = logging.getLogger(__name__)

# 月台预约(确定性常量)
DOCK_SLOTS = ["08:00", "10:00", "14:00", "16:00"]
DOCK_MAX_PER_SLOT = 3

# 多仓匹配(确定性常量)
WAREHOUSE_TIMELINESS_BASE_H = 48.0   # 时效基准(小时)
SHIP_COST_PER_KM = 0.6               # 运费(元/km, 测算口径)
SHIP_COST_BASE = 8.0                 # 基础运费(元)


class ZtResourceService:
    """P1: POI 底座 + 月台预约 + 多仓匹配"""

    def __init__(self, store: _ZtStore = None,
                 fabric: ZtFabricService = None):
        self.store = store or _ZtStore()
        self.fabric = fabric or ZtFabricService(self.store)

    # ============================================================
    # POI 底座(注册/查询)
    # ============================================================

    async def register_poi(self, poi_code: str, name: str, poi_type: str,
                           longitude: float, latitude: float,
                           capabilities: list, rating: float = 4.0,
                           stock_level: float = 1.0, seats_total: int = None,
                           seats_available: int = None,
                           open: bool = True) -> dict:
        """配置化 POI 注册(零代码接入; 建议书制: 投产须人工确认)"""
        if not poi_code or len(poi_code) > 20:
            raise ValueError("POI 编码无效(1-20字符)")
        if not name or len(name) > 40:
            raise ValueError("POI 名称无效")
        if poi_type not in POI_TYPE_NAMES:
            raise ValueError(f"POI 类型无效({poi_type}: "
                             f"{'/'.join(POI_TYPE_NAMES)})")
        if not (-180 <= longitude <= 180 and -90 <= latitude <= 90):
            raise ValueError("坐标非法")
        if await self.fabric.get_poi(poi_code):
            raise ValueError(f"POI 已存在({poi_code})")

        record = {
            "poiCode": poi_code, "name": name, "poiType": poi_type,
            "poiTypeName": POI_TYPE_NAMES[poi_type],
            "longitude": longitude, "latitude": latitude,
            "capabilities": capabilities or [],
            "rating": rating, "stockLevel": stock_level,
            "seats": ({"total": seats_total,
                       "available": seats_available}
                      if seats_total else None),
            "queue": {"waiting": 0, "avgMinutes": 0},
            "open": open,
            "disposition": "注册即入底座; 投产须人工确认(建议书制)",
            "registeredAt": _now_iso(),
        }
        await self.store.save("zt_pois", poi_code, record)
        return record

    async def nearby(self, longitude: float, latitude: float,
                     capability: str = None, radius_km: float = 10.0,
                     limit: int = 20) -> dict:
        """附近 POI(距离排序; 复合能力过滤)"""
        if not (-180 <= longitude <= 180 and -90 <= latitude <= 90):
            raise ValueError("坐标非法")
        pois = await self.fabric.list_pois(capability=capability)
        rows = []
        for p in pois:
            dist = haversine_km(longitude, latitude,
                                float(p["longitude"]),
                                float(p["latitude"]))
            if dist > radius_km:
                continue
            rows.append({
                "poiCode": p["poiCode"], "name": p["name"],
                "poiType": p["poiType"],
                "poiTypeName": POI_TYPE_NAMES.get(
                    p["poiType"], p["poiType"]),
                "distance": _round2(dist),
                "capabilities": p.get("capabilities", []),
                "open": p.get("open", False),
            })
        rows.sort(key=lambda r: r["distance"])
        return {
            "center": {"longitude": longitude, "latitude": latitude},
            "radiusKm": radius_km, "total": len(rows),
            "pois": rows[:limit],
            "note": "haversine 球面距离(确定性)",
        }

    # ============================================================
    # 供货商月台预约(排队最短分配建议)
    # ============================================================

    async def dock_booking(self, supplier_name: str, slot: str,
                            goods_type: str = "高粱",
                            truck_count: int = 1) -> dict:
        """月台预约: 分配建议=同时段占用最少月台(确定性)"""
        if not supplier_name or len(supplier_name) > 40:
            raise ValueError("供货商名称无效")
        if slot not in DOCK_SLOTS:
            raise ValueError(f"时段无效({slot}: {'/'.join(DOCK_SLOTS)})")
        if not (1 <= truck_count <= 5):
            raise ValueError("车辆数须在 [1,5]")

        docks = await self.fabric.list_pois(poi_type="dock")
        if not docks:
            raise ValueError("无可用月台(先注册 POI)")
        bookings = await self.store.list("zt_dock_bookings")
        slot_load = {d["poiCode"]: 0 for d in docks}
        for b in bookings:
            if b.get("slot") == slot:
                slot_load[b["poiCode"]] = slot_load.get(
                    b["poiCode"], 0) + b.get("truckCount", 1)

        # 分配: 同时段占用升序 → 排队 waiting 升序 → 编码稳定序
        ranked = sorted(
            docks,
            key=lambda d: (slot_load.get(d["poiCode"], 0),
                          d.get("queue", {}).get("waiting", 0),
                          d["poiCode"]))
        target = ranked[0]
        if slot_load.get(target["poiCode"], 0) >= DOCK_MAX_PER_SLOT:
            raise ValueError(f"时段 {slot} 全月台已满"
                             f"(每时段上限 {DOCK_MAX_PER_SLOT} 车)")

        record = {
            "bookingId": await self.store.next_id("zt_dock_bookings"),
            "supplierName": supplier_name, "slot": slot,
            "goodsType": goods_type, "truckCount": truck_count,
            "assignedDock": {
                "poiCode": target["poiCode"], "name": target["name"],
                "longitude": target["longitude"],
                "latitude": target["latitude"],
            },
            "navHints": ["场内导航: 入厂后按'月台指示灯'通行",
                         "排队叫号: 抵达后扫码取号",
                         "异常: 质检不合格推送补料/返工指引"],
            "settleNote": "交货完成自动关联批次/重量/质检→电子结算单",
            "status": "suggested",
            "disposition": "月台分配为建议; 人工确认后生效, 永不自动",
            "bookedAt": _now_iso(),
        }
        await self.store.save("zt_dock_bookings",
                              record["bookingId"], record)
        return record

    async def dock_status(self) -> dict:
        """各月台实时收货状态(排队/占用/验收口径)"""
        docks = await self.fabric.list_pois(poi_type="dock")
        bookings = await self.store.list("zt_dock_bookings")
        today = _now_iso()[:10]
        rows = []
        for d in docks:
            today_b = [b for b in bookings
                       if b.get("assignedDock", {}).get("poiCode")
                       == d["poiCode"]
                       and str(b.get("bookedAt", ""))[:10] == today]
            queue = d.get("queue", {})
            rows.append({
                "poiCode": d["poiCode"], "name": d["name"],
                "waiting": queue.get("waiting", 0),
                "avgMinutes": queue.get("avgMinutes", 0),
                "todayBookings": len(today_b),
                "todayTrucks": sum(b.get("truckCount", 1)
                                   for b in today_b),
                "capacityPerSlot": DOCK_MAX_PER_SLOT,
            })
        return {
            "docks": rows, "slots": DOCK_SLOTS,
            "note": "收货状态实时可视(只读聚合)",
            "observedAt": _now_iso(),
        }

    # ============================================================
    # B 端多仓货源匹配(确定性成本测算)
    # ============================================================

    async def warehouse_match(self, longitude: float, latitude: float,
                              quantity: int) -> dict:
        """多仓货源匹配: 按库存/距离/时效推荐仓组合+成本测算

        排序分 = 0.4×库存充足率 + 0.4×距离分 + 0.2×时效分
        运费测算 = 基础8元 + 0.6元/km(确定性)
        """
        if not (-180 <= longitude <= 180 and -90 <= latitude <= 90):
            raise ValueError("坐标非法")
        if not (1 <= quantity <= 5000):
            raise ValueError("订货量须在 [1, 5000]")

        warehouses = await self.fabric.list_pois(poi_type="warehouse")
        if not warehouses:
            raise ValueError("无可用仓(先注册 POI)")
        candidates = []
        for w in warehouses:
            stock = float(w.get("stockLevel", 0) or 0)
            if stock * 1000 < quantity:
                continue            # 库存不足(千瓶口径)
            dist = haversine_km(longitude, latitude,
                                float(w["longitude"]),
                                float(w["latitude"]))
            dist_score = min(1.0, 10.0 / dist) if dist > 0 else 1.0
            timeliness = min(1.0, WAREHOUSE_TIMELINESS_BASE_H
                             / (dist * 2 + 8)) if dist >= 0 else 0.5
            score = _round2(100 * (0.4 * min(1.0, stock)
                                   + 0.4 * dist_score
                                   + 0.2 * timeliness))
            candidates.append({
                "poiCode": w["poiCode"], "name": w["name"],
                "stockLevel": stock, "distance": _round2(dist),
                "estFee": _round2(SHIP_COST_BASE
                                 + SHIP_COST_PER_KM * dist),
                "estHours": _round2(dist * 2 + 8),
                "score": score,
            })
        candidates.sort(key=lambda c: -c["score"])
        if not candidates:
            raise ValueError("全仓库存不足(须先补货或扩仓)")
        return {
            "quantity": quantity,
            "candidates": candidates,
            "recommended": candidates[0] if candidates else None,
            "formula": ("排序 = 0.4×库存 + 0.4×距离分 + 0.2×时效分; "
                        f"运费 = {SHIP_COST_BASE} + "
                        f"{SHIP_COST_PER_KM}元/km(确定性测算)"),
            "disposition": "推荐仓组合为建议; B 端确认后下单",
            "matchedAt": _now_iso(),
        }

    async def fulfillment_board(self) -> dict:
        """B 端履约看板: 在途单/异常聚合(既有物流订单只读)"""
        from repositories.logistics_repository import LogisticsRepository
        repo = LogisticsRepository()
        orders = await repo.list_orders(limit=200)
        in_transit = [o for o in orders
                     if o.get("status") in ("booked", "picked",
                                            "transporting", "delivering")]
        by_carrier: dict[str, int] = {}
        for o in in_transit:
            c = o.get("carrier", "")
            by_carrier[c] = by_carrier.get(c, 0) + 1
        anomalies = [o for o in orders
                     if o.get("status") == "failed"]
        return {
            "inTransit": len(in_transit),
            "byCarrier": by_carrier,
            "failed": len(anomalies),
            "failedWaybills": [o.get("waybillNo", "")
                               for o in anomalies[:5]],
            "note": "履约看板为只读聚合; 异常处置见调度引擎",
            "generatedAt": _now_iso(),
        }
