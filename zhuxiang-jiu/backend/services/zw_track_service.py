"""智运·AI智能物流大模型 P1 轨迹智能(zw_track_service)

创新规划 §二 P1: 从"状态同步展示"到"ETA 预测+延误预警+异常检测"
    - ETA 预测: 承运商历史签收时效均值 × 当前进度外推(确定性)
    - 延误预警: 停滞检测(48h 无轨迹更新, 设计文档 4.4)
    - 异常四检测器(设计文档 4.4): 揽收超时(4h)/运输停滞(48h)/
      派送失败(状态)/签收超时(7 天)

铁律: 全链确定性阈值; 异常处置为预警(创建工单建议, 永不自动改单)。
"""

import logging
from datetime import datetime, UTC

from services.zw_fabric_service import (
    _ZwStore, ZwFabricService, _round2, _now_iso)

logger = logging.getLogger(__name__)

# 异常检测阈值(设计文档 4.4, 确定性)
PICKUP_TIMEOUT_HOURS = 4.0      # 揽收超时: 下单后 4h 未揽收
STAGNATION_HOURS = 48.0         # 运输停滞: 48h 无轨迹更新
SIGN_TIMEOUT_DAYS = 7.0         # 签收超时: 发货后 7 天未签收

# ETA 时效安全系数(预测上浮, 防过度承诺)
ETA_BUFFER_RATIO = 0.15


def _parse_iso(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(str(value or ""))
    except (ValueError, TypeError):
        return None


def _hours_between(a: datetime, b: datetime) -> float:
    return (b - a).total_seconds() / 3600


class ZwTrackService:
    """P1: ETA 预测 + 延误预警 + 异常四检测器"""

    def __init__(self, store: _ZwStore = None,
                 fabric: ZwFabricService = None):
        self.store = store or _ZwStore()
        self.fabric = fabric or ZwFabricService(self.store)

    # ============================================================
    # ETA 预测(确定性外推)
    # ============================================================

    async def eta_predict(self, waybill_no: str) -> dict:
        """运单 ETA 预测

        口径(确定性):
            均时效 = 该物流商历史签收订单 下单→签收 均值(小时)
            已耗时 = 下单→现在
            剩余 = max(0, 均时效×(1+安全系数) - 已耗时)
            无历史样本 → 按服务类型给保守基准(特快 48h/其他 72h)
        已签收单 → 剩余 0。
        """
        from repositories.logistics_repository import LogisticsRepository
        repo = LogisticsRepository()
        order = await repo.get_order(waybill_no)
        if not order:
            raise KeyError(waybill_no)

        if order.get("status") == "signed":
            return {
                "waybillNo": waybill_no,
                "carrier": order.get("carrier", ""),
                "status": "signed",
                "remainingHours": 0.0,
                "eta": order.get("signedAt", ""),
                "explain": "已签收",
            }

        performance = await self.fabric.carrier_performance()
        carrier = str(order.get("carrier", "") or "")
        p = performance.get(carrier, {})
        avg_hours = p.get("avgSignHours") or 0.0
        if avg_hours <= 0:
            # 冷启动保守基准
            service = str(order.get("serviceType", "") or "")
            avg_hours = 48.0 if service == "express" else 72.0
            basis = "冷启动保守基准(无历史样本)"
        else:
            basis = f"{carrier} 历史均时效 {avg_hours}h"

        created = _parse_iso(order.get("createdAt", ""))
        if not created:
            raise ValueError("运单缺 createdAt, 无法预测")
        now = datetime.now(UTC)
        elapsed = _hours_between(created, now)
        planned = avg_hours * (1 + ETA_BUFFER_RATIO)
        remaining = max(0.0, planned - elapsed)
        from datetime import timedelta
        eta_time = (now + timedelta(hours=remaining)).isoformat()

        record_id = await self.store.next_id("eta")
        record = {
            "etaId": record_id, "waybillNo": waybill_no,
            "carrier": carrier, "status": order.get("status"),
            "avgHours": _round2(avg_hours),
            "elapsedHours": _round2(elapsed),
            "plannedHours": _round2(planned),
            "remainingHours": _round2(remaining),
            "eta": eta_time, "basis": basis,
            "formula": (f"剩余 = max(0, 均时效×(1+{ETA_BUFFER_RATIO})"
                        f" - 已耗时)"),
            "note": "确定性外推; ETA 为承诺参考非保证",
            "predictedAt": _now_iso(),
        }
        await self.store.save("eta_records", record_id, record)
        return record

    # ============================================================
    # 异常四检测器(确定性阈值, 设计文档 4.4)
    # ============================================================

    async def detect_anomalies(self, limit: int = 100) -> list[dict]:
        """全量在途运单异常扫描(四检测器)

        - 揽收超时: 下单后 4h 仍未揽收
        - 运输停滞: 48h 无轨迹更新
        - 派送失败: 状态 failed
        - 签收超时: 发货后 7 天未签收
        """
        from repositories.logistics_repository import LogisticsRepository
        repo = LogisticsRepository()
        orders = await repo.list_orders(limit=limit)
        now = datetime.now(UTC)
        alerts = []

        for order in orders:
            status = order.get("status", "")
            waybill = order.get("waybillNo", "")
            carrier = order.get("carrier", "")
            # 已终态跳过(签收/退回)
            if status in ("signed", "returned"):
                continue
            created = _parse_iso(order.get("createdAt", ""))
            picked = _parse_iso(order.get("pickedAt", ""))
            if not created:
                continue

            # 1) 揽收超时(未揽收状态)
            if (status in ("pending", "booked") and not picked
                    and _hours_between(created, now)
                    > PICKUP_TIMEOUT_HOURS):
                alerts.append({
                    "waybillNo": waybill, "carrier": carrier,
                    "type": "pickup_timeout", "severity": "medium",
                    "detail": (f"下单后 "
                               f"{_hours_between(created, now):.0f}h "
                               f"未揽收(阈值 "
                               f"{PICKUP_TIMEOUT_HOURS:.0f}h)"),
                    "action": "告警仓库+催促物流商(工单建议)",
                })
            # 2) 派送失败
            if status == "failed":
                alerts.append({
                    "waybillNo": waybill, "carrier": carrier,
                    "type": "deliver_failed", "severity": "high",
                    "detail": "派送失败(状态 failed)",
                    "action": "通知用户+预约重新派送(工单建议)",
                })
                continue
            # 3) 签收超时(揽收后 7 天)
            if (picked and status != "failed"
                    and _hours_between(picked, now)
                    > SIGN_TIMEOUT_DAYS * 24):
                alerts.append({
                    "waybillNo": waybill, "carrier": carrier,
                    "type": "sign_timeout", "severity": "high",
                    "detail": (f"发货后 "
                               f"{_hours_between(picked, now) / 24:.1f}"
                               f" 天未签收(阈值 "
                               f"{SIGN_TIMEOUT_DAYS:.0f} 天)"),
                    "action": "升级工单+人工介入",
                })

        # 4) 运输停滞(48h 无轨迹更新)——需查最近轨迹
        in_transit = [o for o in orders
                      if o.get("status") in ("picked", "transporting",
                                             "delivering")]
        for order in in_transit[:limit]:
            waybill = order.get("waybillNo", "")
            carrier = order.get("carrier", "")
            tracks = await repo.list_tracks(waybill, limit=1)
            if not tracks:
                continue
            last_track = tracks[0]
            last_time = _parse_iso(last_track.get("trackTime", ""))
            if not last_time:
                continue
            silent = _hours_between(last_time, now)
            if silent > STAGNATION_HOURS:
                alerts.append({
                    "waybillNo": waybill, "carrier": carrier,
                    "type": "stagnation", "severity": "high",
                    "detail": (f"轨迹 {silent:.0f}h 无更新(阈值 "
                               f"{STAGNATION_HOURS:.0f}h)"),
                    "action": "创建工单+客服跟进",
                })

        if alerts:
            for a in alerts:
                a["disposition"] = "预警+工单建议, 处置须人工(永不自动)"
                a["detectedAt"] = _now_iso()
        return alerts

    # ============================================================
    # 延误预警视图(异常子集+ETA 联动)
    # ============================================================

    async def delay_warnings(self, limit: int = 100) -> dict:
        """延误预警总览(异常四检测器 + ETA 超期)"""
        anomalies = await self.detect_anomalies(limit=limit)
        delay_types = ("pickup_timeout", "stagnation", "sign_timeout")
        delays = [a for a in anomalies if a["type"] in delay_types]
        by_type = {}
        for d in delays:
            by_type[d["type"]] = by_type.get(d["type"], 0) + 1
        return {
            "total": len(delays),
            "byType": by_type,
            "warnings": delays,
            "thresholds": {
                "pickupTimeoutHours": PICKUP_TIMEOUT_HOURS,
                "stagnationHours": STAGNATION_HOURS,
                "signTimeoutDays": SIGN_TIMEOUT_DAYS,
            },
            "note": "确定性阈值检测; 处置须人工(永不自动)",
            "generatedAt": _now_iso(),
        }
