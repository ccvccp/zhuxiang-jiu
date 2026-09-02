"""41号·AI智能代驾模块·安全监控(设计文档 §2.4)

酒后场景底线, 全 AI 自动, 三道防线:
    行前: 上车点 POI 校验——非餐厅/酒吧类 POI 的高频叫单
          (24h 内 ≥3 次)产生风控信号(不阻断, 降档留痕)
    行中: 行程超时预警——市内行程 > 3h 未结束(扫描发现, 幂等)
    行后: 里程异常——实际里程超预估 2 倍(结算时比对)

风险事件统一落 ride_risk_events 表, 面板聚合供管理端处置。
"""

import logging
from datetime import datetime, UTC, timedelta

from repositories.ride_repository import (
    RideRepository,
    RIDE_POI_DRINKING_WORDS, RIDE_POI_FREQ_WINDOW_HOURS,
    RIDE_POI_FREQ_THRESHOLD, RIDE_TIMEOUT_HOURS,
    RISK_EVENT_POI, RISK_EVENT_TIMEOUT, RISK_EVENT_TYPES,
    RIDE_STATUS_STARTED,
)


logger = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def classify_poi(address: str) -> dict:
    """上车点 POI 场景分类(纯函数)

    饮酒场景词命中 → drinking(合规叫单场景);
    地址为空 → unknown(无信号); 其余 → neutral(记入高频统计)。
    """
    text = str(address or "")
    if not text:
        return {"category": "unknown", "matchedWords": []}
    hits = [w for w in RIDE_POI_DRINKING_WORDS if w in text]
    if hits:
        return {"category": "drinking", "matchedWords": hits}
    return {"category": "neutral", "matchedWords": []}


class RideSafetyService:
    """安全监控: 行前 POI / 行中超时 / 面板聚合"""

    def __init__(self):
        self.repo = RideRepository()

    # --------------------------------------------------------
    # 行前: POI 高频叫单风控(叫单时调用)
    # --------------------------------------------------------

    async def pre_ride_check(self, member_id: int,
                             pickup: dict) -> dict:
        """上车点 POI 校验 + 24h 高频统计

        Returns:
            {poiCategory, matchedWords, neutralCalls24h, highFrequency,
             riskFlag, riskEventId}
        """
        member_id = int(member_id)
        poi = classify_poi(str((pickup or {}).get("address") or ""))

        neutral_24h = 0
        if poi["category"] == "neutral":
            rides = await self.repo.list_rides(member_id=member_id,
                                               limit=500)
            cutoff = datetime.now(UTC) - timedelta(
                hours=RIDE_POI_FREQ_WINDOW_HOURS)
            for r in rides:
                if r.get("poiCategory") != "neutral":
                    continue
                try:
                    requested = datetime.fromisoformat(
                        r.get("requestedAt") or "")
                except ValueError:
                    continue
                if requested >= cutoff:
                    neutral_24h += 1

        # 含当次: 24h 内非饮酒 POI 叫单达阈值 → 风控信号(不阻断)
        high = (poi["category"] == "neutral"
                and (neutral_24h + 1) >= RIDE_POI_FREQ_THRESHOLD)
        risk_event_id = None
        if high:
            risk_event_id = await self._save_event(
                type=RISK_EVENT_POI, member_id=member_id, ride_id=None,
                detail=f"24h内非饮酒POI叫单{neutral_24h + 1}次"
                       f"(阈值{RIDE_POI_FREQ_THRESHOLD})")
        return {
            "poiCategory": poi["category"],
            "matchedWords": poi["matchedWords"],
            "neutralCalls24h": neutral_24h,
            "highFrequency": high,
            "riskFlag": RISK_EVENT_POI if high else "",
            "riskEventId": risk_event_id,
        }

    # --------------------------------------------------------
    # 行中: 行程超时扫描(幂等)
    # --------------------------------------------------------

    async def scan_active(self) -> dict:
        """扫描进行中超时行程: trip_started > 3h 未结束 → 预警事件

        幂等: 同行程同类型的未处置事件不重复落。
        """
        rides = await self.repo.list_rides(limit=2000)
        cutoff = datetime.now(UTC) - timedelta(hours=RIDE_TIMEOUT_HOURS)
        warnings = []
        scanned = 0
        for ride in rides:
            if ride.get("status") != RIDE_STATUS_STARTED:
                continue
            scanned += 1
            try:
                started = datetime.fromisoformat(ride.get("startedAt")
                                                  or "")
            except ValueError:
                continue
            if started >= cutoff:
                continue
            # 幂等: 已有未处置的超时事件则跳过
            existing = await self.repo.list_risk_events(
                ride_id=ride["rideId"], type=RISK_EVENT_TIMEOUT,
                resolved=False, limit=5)
            if existing:
                continue
            event = await self._save_event(
                type=RISK_EVENT_TIMEOUT,
                member_id=int(ride.get("memberId") or 0),
                ride_id=ride["rideId"],
                detail=f"行程开始 {RIDE_TIMEOUT_HOURS}h 未结束(市内口径)")
            warnings.append(event)
            logger.warning("ride_timeout ride=%s startedAt=%s",
                           ride["rideId"], ride.get("startedAt"))
        return {"scanned": scanned, "warnings": warnings,
                "timeoutHours": RIDE_TIMEOUT_HOURS}

    # --------------------------------------------------------
    # 风险面板(管理端)
    # --------------------------------------------------------

    async def risk_panel(self) -> dict:
        events = await self.repo.list_risk_events(limit=1000)
        by_type = {t: 0 for t in RISK_EVENT_TYPES}
        for e in events:
            if e.get("type") in by_type:
                by_type[e["type"]] += 1
        unresolved = [e for e in events if not e.get("resolved")]
        return {
            "success": True,
            "total": len(events),
            "byType": by_type,
            "unresolved": len(unresolved),
            "events": sorted(events,
                             key=lambda e: (e.get("createdAt") or ""),
                             reverse=True)[:50],
        }

    async def resolve_event(self, risk_id: int, note: str = "") -> dict:
        """处置风险事件(管理端)

        Raises:
            KeyError: 事件不存在
        """
        event = await self.repo._get(self.repo.TABLE_RISK, int(risk_id))
        if event is None:
            raise KeyError(f"风险事件 {risk_id} 不存在")
        event["resolved"] = True
        event["resolvedNote"] = note
        event["resolvedAt"] = _now_iso()
        await self.repo.save_risk_event(event)
        return event

    # --------------------------------------------------------
    # 内部
    # --------------------------------------------------------

    async def _save_event(self, type: str, member_id: int,
                          ride_id: str | None, detail: str) -> dict:
        risk_id = await self.repo.next_risk_id()
        event = {
            "riskId": risk_id,
            "type": type,
            "memberId": member_id,
            "rideId": ride_id,
            "detail": detail,
            "resolved": False,
            "resolvedNote": "",
            "resolvedAt": None,
            "createdAt": _now_iso(),
        }
        await self.repo.save_risk_event(event)
        return event
