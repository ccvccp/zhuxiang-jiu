"""41号·AI智能代驾模块·智能派单引擎与行程编排(设计文档 §2.3/§2.4/§2.5)

双层派单(物流 smart_route_carrier + LogisticsRoutingScorer 模式平移):
    规则层: 半径内在线司机池 → 硬过滤(评分≥4.0/非在忙/非暂停)
    AI 层:  RideDispatchScorer(第23档案) 五因子逐司机评分
    决策:   最优 ≥70 直接派 / 50-70 次优选派+备选通知 / <50 或池空
            → 升级平台直发(mock 通道, 三态回执结构)

行程状态机(§2.4):
    requested → dispatched → driver_arriving → trip_started
    → trip_completed → settling → settled(终态)
    分支: cancelled(免责窗口判定) / no_driver(全轨无运力, 券退回)

AI 结算(§2.5, 全程无人工):
    计价(起步+超里程+超时+夜间加成) → 券抵扣 min(60, 总额) 本站支付
    → 超出部分乘客补差 → 结算单落库(自营 mock 直付 paid /
    加盟/直发汇总结付 aggregated) → 司机统计回写

异常约定(遵循项目约定):
    - KeyError → 404(行程/司机不存在)
    - ValueError → 409(无券/超市内范围/状态非法/非当班司机等)
"""

import logging
import hashlib
import hmac
import math
import time
from datetime import datetime, UTC
from uuid import uuid4

from core.locks import get_lock
from repositories.ride_repository import (
    RideRepository,
    TRACK_SELF, TRACK_PARTNER, TRACK_PLATFORM, TRACK_NAMES,
    DRIVER_STATUS_ONLINE,
    COUPON_STATUS_GRANTED,
    RIDE_STATUS_REQUESTED, RIDE_STATUS_DISPATCHED, RIDE_STATUS_ARRIVING,
    RIDE_STATUS_STARTED, RIDE_STATUS_COMPLETED, RIDE_STATUS_SETTLING,
    RIDE_STATUS_SETTLED, RIDE_STATUS_CANCELLED, RIDE_STATUS_NO_DRIVER,
    RIDE_ACTIVE_STATUSES,
    CITY_RADIUS_KM, DISPATCH_RADIUS_KM, FREE_CANCEL_SECONDS,
    MIN_DRIVER_RATING,
    RIDE_BASE_FARE, RIDE_BASE_KM, RIDE_PER_KM, RIDE_PER_MIN,
    RIDE_FREE_MINUTES, RIDE_NIGHT_SURGE,
    RIDE_NIGHT_START, RIDE_NIGHT_END,
    DISPATCH_AUTO_SCORE, DISPATCH_BACKUP_SCORE,
    DRIDE_CHANNEL_MODE, DRIDE_PARTNER_URL,
    DRIDE_PARTNER_APP_ID, DRIDE_PARTNER_APP_SECRET, DRIDE_PARTNER_TOKEN,
    RIDE_MILEAGE_ANOMALY_RATIO,
    RISK_EVENT_MILEAGE,
    PARTNER_EVENT_ACCEPTED, PARTNER_EVENT_STARTED,
    PARTNER_EVENT_COMPLETED, PARTNER_EVENT_CANCELLED,
    PARTNER_EVENTS,
)
from services.ai_scoring_service import SCORERS


logger = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def haversine_km(lat1: float, lng1: float, lat2: float,
                 lng2: float) -> float:
    """球面距离(纯函数, 派单距离与行程里程共用)"""
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lng2 - lng1)
    a = (math.sin(dp / 2) ** 2
         + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2)
    return round(r * 2 * math.asin(math.sqrt(a)), 3)


def is_night_hour(hour: int) -> bool:
    """夜间时段判定: [22:00, 次日 06:00)"""
    return hour >= RIDE_NIGHT_START or hour < RIDE_NIGHT_END


def _partner_auth_headers() -> dict:
    """平台鉴权头(待办清单 §3.3, 按配置双风格)

    APP_ID+APP_SECRET → HMAC 签名头(签名串 app_id+timestamp+nonce);
    仅 TOKEN          → Bearer 头; 均未配置 → 空(联调裸跑)。
    """
    if DRIDE_PARTNER_APP_ID and DRIDE_PARTNER_APP_SECRET:
        timestamp = str(int(time.time()))
        nonce = uuid4().hex[:16]
        sign = hmac.new(
            DRIDE_PARTNER_APP_SECRET.encode("utf-8"),
            f"{DRIDE_PARTNER_APP_ID}{timestamp}{nonce}"
            .encode("utf-8"),
            hashlib.sha256).hexdigest()
        return {"X-App-Id": DRIDE_PARTNER_APP_ID,
                "X-Timestamp": timestamp,
                "X-Nonce": nonce,
                "X-Signature": sign}
    if DRIDE_PARTNER_TOKEN:
        return {"Authorization": f"Bearer {DRIDE_PARTNER_TOKEN}"}
    return {}


def compute_fare(distance_km: float, minutes: float = 0,
                 hour: int | None = None) -> dict:
    """市内代驾计价(纯函数, 设计文档 §2.5)

    起步价 ¥35(含 5km) + 超里程 ¥5/km + 超时 ¥1/min(>40min 部分);
    夜间(22:00-06:00)加成 20%。
    """
    km = max(0.0, float(distance_km or 0))
    minutes = max(0.0, float(minutes or 0))
    if hour is None:
        hour = datetime.now(UTC).hour
    night = is_night_hour(int(hour))

    base = RIDE_BASE_FARE
    extra_km = max(0.0, km - RIDE_BASE_KM)
    km_fee = extra_km * RIDE_PER_KM
    extra_min = max(0.0, minutes - RIDE_FREE_MINUTES)
    min_fee = extra_min * RIDE_PER_MIN
    subtotal = base + km_fee + min_fee
    surge = subtotal * RIDE_NIGHT_SURGE if night else 0.0
    total = round(subtotal + surge, 2)
    return {
        "baseFare": base,
        "distanceKm": round(km, 2),
        "extraKmFee": round(km_fee, 2),
        "durationMinutes": round(minutes, 1),
        "extraMinFee": round(min_fee, 2),
        "nightSurge": round(surge, 2),
        "isNight": night,
        "totalAmount": total,
    }


class RideDispatchService:
    """智能派单引擎 + 行程生命周期 + AI 结算"""

    def __init__(self):
        self.repo = RideRepository()

    # --------------------------------------------------------
    # 叫代驾(乘客入口: 选券 FEFO + 双层派单)
    # --------------------------------------------------------

    async def call(self, member_id: int, pickup: dict,
                   dropoff: dict, distance_km: float = None) -> dict:
        """叫代驾: 自动选最早过期券 → 规则过滤 → AI 评分 → 三轨派单

        Raises:
            KeyError: 会员不存在
            ValueError: 无可用券/起终点缺失/超市内范围
        """
        from repositories.member_repository import MemberRepository

        member_id = int(member_id)
        member = await MemberRepository().get_by_id(member_id)
        if not member:
            raise KeyError(f"会员 {member_id} 不存在")

        if not (pickup or {}).get("lat") or not (dropoff or {}).get("lat"):
            raise ValueError("起终点坐标(lat/lng)缺失")

        # 行程里程: 显式 distanceKm 优先(确定性测试口径), 否则 haversine
        if distance_km is not None:
            km = float(distance_km)
        else:
            km = haversine_km(float(pickup["lat"]), float(pickup["lng"]),
                              float(dropoff["lat"]), float(dropoff["lng"]))
        if km <= 0:
            raise ValueError("起终点相同, 无需代驾")
        if km > CITY_RADIUS_KM:
            raise ValueError(f"行程 {km:.1f}km 超市内范围"
                             f"({CITY_RADIUS_KM:.0f}km), 券不可用")

        # 选券: granted 且未被活跃行程占用, 最早过期优先(FEFO)
        async with get_lock(f"ride:call:{member_id}"):
            coupon = await self._select_coupon(member_id)
            if coupon is None:
                raise ValueError("无可用代驾券(买竹香酒满 ¥500 "
                                 "支付后自动赠券)")

            ride_id = await self.repo.next_ride_id()
            # 行前安全预检: POI 场景分类 + 高频叫单风控(不阻断, 信号留痕)
            from services.ride_safety_service import RideSafetyService
            safety = await RideSafetyService().pre_ride_check(
                member_id, pickup)
            ride = {
                "rideId": ride_id,
                "memberId": member_id,
                "couponCode": coupon["code"],
                "couponValue": coupon.get("value"),
                "status": RIDE_STATUS_REQUESTED,
                "pickup": {
                    "lat": float(pickup["lat"]),
                    "lng": float(pickup["lng"]),
                    "address": str(pickup.get("address") or ""),
                },
                "dropoff": {
                    "lat": float(dropoff["lat"]),
                    "lng": float(dropoff["lng"]),
                    "address": str(dropoff.get("address") or ""),
                },
                "distanceKm": km,
                "driverId": None,
                "driverSnapshot": {},
                "dispatchMode": "",      # ai | platform
                "dispatchScore": None,
                "candidates": [],
                "pricing": {},
                "settlementId": None,
                "cancelReason": "",
                "cancelWindowFree": None,
                # P2 安全监控字段
                "poiCategory": safety.get("poiCategory"),
                "riskFlag": safety.get("riskFlag") or "",
                "actualKm": None,
                "mileageAnomaly": False,
                "partnerTrace": {},
                "platformChannel": "",
                "requestedAt": _now_iso(),
                "dispatchedAt": None,
                "arrivingAt": None,
                "startedAt": None,
                "completedAt": None,
                "settledAt": None,
            }
            await self.repo.save_ride(ride)

        # 派单(锁外执行, 派单失败 → no_driver 不占锁)
        ride = await self._dispatch(ride)
        await self.repo.save_ride(ride)
        return self._ride_detail(ride)

    async def _select_coupon(self, member_id: int) -> dict | None:
        """FEFO 选券: granted + 未被活跃行程占用, 最早过期优先"""
        coupons = await self.repo.list_coupons(
            member_id=member_id, status=COUPON_STATUS_GRANTED, limit=200)
        busy_codes = set()
        active_rides = await self.repo.list_rides(limit=2000)
        for r in active_rides:
            if r.get("status") in RIDE_ACTIVE_STATUSES:
                code = r.get("couponCode")
                if code:
                    busy_codes.add(code)
        available = [c for c in coupons if c["code"] not in busy_codes]
        if not available:
            return None
        available.sort(key=lambda c: (c.get("expiresAt") or "",
                                       c.get("code") or ""))
        return available[0]

    # --------------------------------------------------------
    # 双层派单(规则层 + AI 层 + 三轨溢出)
    # --------------------------------------------------------

    async def _dispatch(self, ride: dict) -> dict:
        """对 requested 行程执行派单, 返回更新后的行程"""
        pickup = ride["pickup"]
        # 规则层: 自营+加盟在线司机, 半径内, 评分达标, 非在忙
        pool = await self.repo.list_drivers(limit=1000)
        candidates = []
        for d in pool:
            if d.get("track") == TRACK_PLATFORM:
                continue        # 直发轨道不进半径池, 仅溢出兜底
            if d.get("status") != DRIVER_STATUS_ONLINE:
                continue
            if d.get("currentRideId"):
                continue        # 在忙
            if float(d.get("rating") or 0) < MIN_DRIVER_RATING:
                continue
            dist = haversine_km(pickup["lat"], pickup["lng"],
                                float(d.get("lat") or 0),
                                float(d.get("lng") or 0))
            if dist > DISPATCH_RADIUS_KM:
                continue
            candidates.append((d, dist))

        scored = []
        for d, dist in candidates:
            ctx = {
                "driverId": d["driverId"],
                "track": d["track"],
                "distanceKm": dist,
                "rating": d.get("rating"),
                "acceptRate": d.get("acceptRate"),
                "cancelRate": d.get("cancelRate"),
                "todayOrders": d.get("todayOrders"),
                "dispatchRadiusKm": DISPATCH_RADIUS_KM,
            }
            scoring = await SCORERS["ride_dispatch"].score(ctx)
            scored.append((d, dist, scoring))
        scored.sort(key=lambda x: x[2]["score"], reverse=True)

        ride["candidates"] = [
            {"driverId": d["driverId"], "name": d.get("name"),
             "track": d["track"], "distanceKm": dist,
             "score": s["score"], "action": s["action"]}
            for d, dist, s in scored[:5]
        ]

        if scored and scored[0][2]["score"] >= DISPATCH_BACKUP_SCORE:
            # 最优 ≥50: 直接派(≥70 常规派 / 50-70 次优选派+备选通知)
            driver, dist, scoring = scored[0]
            await self._assign(ride, driver, dist, scoring, mode="ai")
        else:
            # 池空或全部 <50 → 升级平台直发(三轨兜底, 永不拒单)
            await self._escalate_platform(ride)
        return ride

    async def _assign(self, ride: dict, driver: dict, dist: float,
                      scoring: dict, mode: str) -> None:
        """派单落库: 司机占用 + 行程 dispatched"""
        ride.update({
            "status": RIDE_STATUS_DISPATCHED,
            "driverId": driver["driverId"],
            "driverSnapshot": {
                "driverId": driver["driverId"],
                "track": driver["track"],
                "trackName": TRACK_NAMES.get(driver["track"], ""),
                "platform": driver.get("platform", ""),
                "name": driver.get("name", ""),
                "phone": driver.get("phone", ""),
                "plateNo": driver.get("plateNo", ""),
                "rating": driver.get("rating"),
                "pickupDistanceKm": dist,
            },
            "dispatchMode": mode,
            "dispatchScore": scoring["score"] if mode == "ai" else None,
            # P4: 派单评分快照(factors 供 Hedge 回流)
            "dispatchScoring": {
                "factors": scoring.get("factors") or [],
                "action": scoring.get("action"),
            } if mode == "ai" else {},
            "dispatchFed": False,   # P4: 学习回流幂等标记
            "dispatchedAt": _now_iso(),
        })
        driver["currentRideId"] = ride["rideId"]
        driver["todayOrders"] = int(driver.get("todayOrders") or 0) + 1
        driver["updatedAt"] = _now_iso()
        await self.repo.save_driver(driver)
        logger.info("ride_dispatched ride=%s driver=%s track=%s "
                    "score=%s mode=%s", ride["rideId"],
                    driver["driverId"], driver["track"],
                    scoring.get("score"), mode)

    async def _escalate_platform(self, ride: dict) -> None:
        """平台直发(三轨兜底): 三态通道

        mock: 确定性模拟回执(默认);
        real: 真实平台调用, 失败抛错(fail-hard, 不回退);
        mock_fallback: 先走真实轨, 失败回退 mock(回执标记来源)。
        """
        mode = DRIDE_CHANNEL_MODE
        result = None
        real_error = None
        if mode in ("real", "mock_fallback") and DRIDE_PARTNER_URL:
            try:
                result = await self._platform_real(ride)
            except Exception as exc:
                real_error = exc
                logger.warning("ride_platform_real_failed: %s", exc)
                result = None
        if result is None:
            if mode == "real":
                raise ValueError(
                    "平台直发通道不可用(real 模式无回退): "
                    + (repr(real_error) if real_error else "未配置 DRIDE_PARTNER_URL"))
            # mock 直发 / mock_fallback 回退 → 确定性模拟回执
            result = self._platform_mock(ride)
            if real_error is not None:
                result["mode"] = "mock_fallback"   # 真实轨失败回退标记
                ride["platformChannel"] = "mock_fallback"
            else:
                ride["platformChannel"] = "mock"
        else:
            ride["platformChannel"] = "real"

        if not result.get("accepted"):
            # 全轨无运力(罕见): 券退回留痕
            ride.update({
                "status": RIDE_STATUS_NO_DRIVER,
                "dispatchMode": "platform",
                "cancelReason": "平台直发被拒, 全轨无运力",
            })
            logger.warning("ride_no_driver ride=%s", ride["rideId"])
            return

        driver_info = result.get("driver") or {}
        ride.update({
            "status": RIDE_STATUS_DISPATCHED,
            "driverId": None,
            "driverSnapshot": {
                "driverId": None,
                "track": TRACK_PLATFORM,
                "trackName": TRACK_NAMES[TRACK_PLATFORM],
                "platform": "e代驾mock",
                "name": driver_info.get("name", "平台司机"),
                "phone": driver_info.get("phone", ""),
                "plateNo": driver_info.get("plateNo", ""),
                "rating": driver_info.get("rating"),
                "partnerOrderId": result.get("partnerOrderId"),
                "etaSeconds": result.get("etaSeconds"),
            },
            "dispatchMode": "platform",
            "dispatchScore": None,
            "dispatchedAt": _now_iso(),
        })
        logger.info("ride_escalated_platform ride=%s partnerOrder=%s "
                    "channel=%s", ride["rideId"],
                    result.get("partnerOrderId"), ride["platformChannel"])

    async def _platform_real(self, ride: dict) -> dict | None:
        """平台直发真实轨(冻结契约 + 平台鉴权头, 需 DRIDE_PARTNER_URL)

        鉴权(待办清单 §3.3, 双风格按配置自动选择):
            APP_ID+APP_SECRET → HMAC-SHA256 签名头(X-App-Id/X-Timestamp/
            X-Nonce/X-Signature, 签名串 app_id+timestamp+nonce);
            仅 TOKEN           → Authorization: Bearer 头;
            均未配置            → 无鉴权头(联调裸跑口径)。
        幂等: X-Request-Id 携带 rideId; 传输层错误重试 1 次
        (HTTP 4xx/5xx 为业务响应, 不重试直接失败)。
        """
        import httpx
        payload = {
            "rideId": ride["rideId"],
            "pickup": ride["pickup"],
            "dropoff": ride["dropoff"],
            "couponValue": ride.get("couponValue"),
            "estimatedKm": ride.get("distanceKm"),
        }
        headers = {"Content-Type": "application/json",
                   "X-Request-Id": ride["rideId"],
                   **_partner_auth_headers()}
        url = f"{DRIDE_PARTNER_URL}/dispatch"
        for attempt in (1, 2):
            try:
                async with httpx.AsyncClient(timeout=10) as client:
                    resp = await client.post(url, json=payload,
                                             headers=headers)
                    resp.raise_for_status()
                    return resp.json()
            except httpx.TransportError:
                if attempt == 2:
                    raise
                logger.warning("ride_platform_real_retry ride=%s",
                               ride["rideId"])
        return None

    @staticmethod
    def _platform_mock(ride: dict) -> dict:
        """平台直发确定性模拟回执(同 ride 同结果)"""
        return {
            "accepted": True,
            "partnerOrderId": f"PD{ride['rideId'][2:]}",
            "driver": {
                "name": "平台司机丙",
                "phone": "13900000009",
                "plateNo": "鲁J30009",
                "rating": 4.3,
            },
            "etaSeconds": 420,
            "mode": "mock",
        }

    # --------------------------------------------------------
    # 行程生命周期(司机操作 + 取消)
    # --------------------------------------------------------

    async def _require_driver_by_member(self, member_id: int) -> dict:
        """会员 → 司机身份(X-Member-Id 鉴权后置校验)"""
        driver = await self.repo.get_driver_by_member(int(member_id))
        if driver is None:
            raise KeyError(f"会员 {member_id} 无代驾员资格")
        return driver

    def _assert_assigned(self, ride: dict, driver: dict) -> None:
        """司机与行程的当班校验(平台直发行程无本站司机, 拒绝操作)"""
        snapshot = ride.get("driverSnapshot") or {}
        if snapshot.get("track") == TRACK_PLATFORM:
            raise ValueError("平台直发行程由合作平台侧操作, 本站司机无权限")
        if int(ride.get("driverId") or 0) != int(driver["driverId"]):
            raise ValueError("非当班司机, 无权操作该行程")

    async def driver_accept(self, member_id: int, ride_id: str) -> dict:
        """司机确认接单 dispatched → driver_arriving"""
        driver = await self._require_driver_by_member(member_id)
        ride = await self._get_ride_or_404(ride_id)
        if ride.get("status") != RIDE_STATUS_DISPATCHED:
            raise ValueError(f"行程状态 {ride.get('status')}, 不可确认接单")
        self._assert_assigned(ride, driver)
        ride["status"] = RIDE_STATUS_ARRIVING
        ride["arrivingAt"] = _now_iso()
        await self.repo.save_ride(ride)
        return self._ride_detail(ride)

    async def driver_start(self, member_id: int, ride_id: str) -> dict:
        """行程开始 driver_arriving → trip_started(乘客上车)"""
        driver = await self._require_driver_by_member(member_id)
        ride = await self._get_ride_or_404(ride_id)
        if ride.get("status") != RIDE_STATUS_ARRIVING:
            raise ValueError(f"行程状态 {ride.get('status')}, 不可开始行程")
        self._assert_assigned(ride, driver)
        ride["status"] = RIDE_STATUS_STARTED
        ride["startedAt"] = _now_iso()
        await self.repo.save_ride(ride)
        return self._ride_detail(ride)

    async def driver_complete(self, member_id: int, ride_id: str,
                              duration_minutes: float = None,
                              pricing_hour: int = None,
                              actual_km: float = None) -> dict:
        """行程结束 → AI 自动结算(计价/核券/拆分/结算单)→ settled

        duration_minutes / pricing_hour / actual_km 为 Mock-first
        确定性测试口径: 缺省时按行程实际起止时间推算时长、按当前小时
        计价、按预估里程计价; actual_km 传入时参与里程异常比对。
        """
        driver = await self._require_driver_by_member(member_id)
        ride = await self._get_ride_or_404(ride_id)
        if ride.get("status") != RIDE_STATUS_STARTED:
            raise ValueError(f"行程状态 {ride.get('status')}, 不可结束行程")
        self._assert_assigned(ride, driver)

        ride["status"] = RIDE_STATUS_COMPLETED
        ride["completedAt"] = _now_iso()
        await self.repo.save_ride(ride)

        # AI 结算(全程无人工)
        ride = await self._settle(ride, driver,
                                  duration_minutes=duration_minutes,
                                  pricing_hour=pricing_hour,
                                  actual_km=actual_km)
        return self._ride_detail(ride)

    async def cancel(self, member_id: int, ride_id: str,
                      reason: str = "") -> dict:
        """乘客取消(免责窗口判定: 派单后 FREE_CANCEL_SECONDS 内券退回)

        仅 trip_started 前可取消; 窗口外取消 → 券作废(used, 计损失留痕)。
        """
        ride = await self._get_ride_or_404(ride_id)
        if int(ride.get("memberId") or 0) != int(member_id):
            raise ValueError("仅乘客本人可取消行程")
        if ride.get("status") not in (RIDE_STATUS_REQUESTED,
                                      RIDE_STATUS_DISPATCHED,
                                      RIDE_STATUS_ARRIVING):
            raise ValueError(f"行程状态 {ride.get('status')}, 不可取消")

        free = True
        dispatched_at = ride.get("dispatchedAt")
        if ride.get("status") != RIDE_STATUS_REQUESTED and dispatched_at:
            try:
                dt = datetime.fromisoformat(dispatched_at)
                elapsed = (datetime.now(UTC) - dt).total_seconds()
                free = elapsed <= FREE_CANCEL_SECONDS
            except ValueError:
                free = True

        # 释放当班司机(平台直发无本站司机)
        driver = None
        if ride.get("driverId"):
            driver = await self.repo.get_driver(int(ride["driverId"]))
            if driver is not None:
                driver["currentRideId"] = ""
                driver["updatedAt"] = _now_iso()
                await self.repo.save_driver(driver)

        # 券处置: 窗口内退回留用(granted 不动) / 窗口外作废(used 计损失)
        if not free:
            from services.ride_coupon_service import RideCouponService
            try:
                await RideCouponService().redeem(
                    ride.get("couponCode") or "",
                    ride_id=ride["rideId"])
            except (KeyError, ValueError):
                pass    # 券已失效(过期/作废)则不再处置

        ride.update({
            "status": RIDE_STATUS_CANCELLED,
            "cancelReason": reason or ("免责窗口内取消" if free
                                       else "超免责窗口取消, 券作废"),
            "cancelWindowFree": free,
        })
        await self.repo.save_ride(ride)
        logger.info("ride_cancelled ride=%s free=%s", ride_id, free)
        return self._ride_detail(ride)

    # --------------------------------------------------------
    # 平台直发回调(P2 三态: accepted/started/completed/cancelled)
    # --------------------------------------------------------

    async def partner_callback(self, event: dict) -> dict:
        """合作平台直发回执处理(设计文档 §2.3 平台直发契约)

        事件流转:
            accepted → 幂等确认 dispatched
            started → trip_started
            completed → trip_completed + AI 结算(aggregated)
            cancelled → cancelled(平台侧取消, 券退回)

        trace 摘要留痕(actualKm/durationMinutes), 供里程异常比对与审计。

        Raises:
            KeyError: 平台单号无对应行程
            ValueError: 事件非法/非平台直发行程/状态不可流转
        """
        partner_order_id = str(event.get("partnerOrderId") or "")
        ev = str(event.get("event") or "")
        if not partner_order_id:
            raise ValueError("回调缺少 partnerOrderId")
        if ev not in PARTNER_EVENTS:
            raise ValueError(f"未知平台回调事件: {ev}"
                             f"(允许: {'/'.join(PARTNER_EVENTS)})")

        ride = await self.repo.get_ride_by_partner_order(partner_order_id)
        if ride is None:
            raise KeyError(f"平台单号 {partner_order_id} 无对应行程")
        snapshot = ride.get("driverSnapshot") or {}
        if snapshot.get("track") != TRACK_PLATFORM:
            raise ValueError("非平台直发行程, 不接受平台回调")

        trace = event.get("trace") or {}
        status = ride.get("status")

        if ev == PARTNER_EVENT_ACCEPTED:
            if status == RIDE_STATUS_REQUESTED:
                ride["status"] = RIDE_STATUS_DISPATCHED
                ride["dispatchedAt"] = _now_iso()

        elif ev == PARTNER_EVENT_STARTED:
            if status not in (RIDE_STATUS_DISPATCHED,
                              RIDE_STATUS_ARRIVING):
                raise ValueError(f"行程状态 {status}, 不可开始(平台回调)")
            ride["status"] = RIDE_STATUS_STARTED
            ride["startedAt"] = _now_iso()

        elif ev == PARTNER_EVENT_COMPLETED:
            if status not in (RIDE_STATUS_STARTED, RIDE_STATUS_DISPATCHED,
                              RIDE_STATUS_ARRIVING):
                raise ValueError(f"行程状态 {status}, 不可完成(平台回调)")
            if not ride.get("startedAt"):
                ride["startedAt"] = _now_iso()
            ride["status"] = RIDE_STATUS_COMPLETED
            ride["completedAt"] = _now_iso()
            # 轨迹摘要留痕(设计文档: 起终点+里程+时长, 不做实时轨迹流)
            ride["partnerTrace"] = {
                "actualKm": trace.get("actualKm"),
                "durationMinutes": trace.get("durationMinutes"),
                "completedEvent": True,
            }
            await self.repo.save_ride(ride)
            ride = await self._settle(
                ride, driver=None,
                duration_minutes=trace.get("durationMinutes"),
                pricing_hour=trace.get("pricingHour"),
                actual_km=trace.get("actualKm"))
            return {"success": True,
                    "ride": self._ride_detail(ride)}

        elif ev == PARTNER_EVENT_CANCELLED:
            if status in (RIDE_STATUS_SETTLED, RIDE_STATUS_CANCELLED):
                raise ValueError(f"行程状态 {status}, 不可取消(平台回调)")
            ride["status"] = RIDE_STATUS_CANCELLED
            ride["cancelReason"] = "平台侧取消"
            ride["cancelWindowFree"] = True   # 平台取消非乘客责任, 券退回

        ride["partnerTrace"] = {**(ride.get("partnerTrace") or {}),
                                 "lastEvent": ev}
        await self.repo.save_ride(ride)
        logger.info("ride_partner_callback ride=%s event=%s",
                    ride["rideId"], ev)
        return {"success": True, "ride": self._ride_detail(ride)}

    # --------------------------------------------------------
    # AI 结算(计价 → 券抵扣 → 拆分 → 结算单)
    # --------------------------------------------------------

    async def _settle(self, ride: dict, driver: dict = None,
                      duration_minutes: float = None,
                      pricing_hour: int = None,
                      actual_km: float = None) -> dict:
        """trip_completed → settling → settled(AI 全自动, 设计文档 §2.5)

        计价 → 券抵扣 min(面值, 总额)(本站支付) → 超出部分乘客补差
        → 结算单落库(自营 mock 直付 paid / 加盟·直发汇总结付 aggregated)
        → 司机统计回写 + 释放占用

        P2: actual_km(实际里程, 平台回执/司机上报)优先参与计价,
        且与预估里程比对, 超 2 倍 → 里程异常风险事件留痕。
        """
        ride["status"] = RIDE_STATUS_SETTLING
        await self.repo.save_ride(ride)

        # 时长: 显式口径优先, 否则按 started→completed 实际时长
        if duration_minutes is not None:
            minutes = float(duration_minutes)
        else:
            minutes = 0.0
            try:
                t0 = datetime.fromisoformat(ride.get("startedAt") or "")
                t1 = datetime.fromisoformat(ride.get("completedAt") or "")
                minutes = max(0.0, (t1 - t0).total_seconds() / 60)
            except ValueError:
                minutes = 0.0

        # 里程: 实际里程优先计价 + 异常比对(设计文档 §2.4 行后防线)
        estimated_km = float(ride.get("distanceKm") or 0)
        if actual_km is not None:
            actual_km = float(actual_km)
            ride["actualKm"] = actual_km
            if (estimated_km > 0
                    and actual_km > RIDE_MILEAGE_ANOMALY_RATIO
                    * estimated_km):
                ride["mileageAnomaly"] = True
                from services.ride_safety_service import RideSafetyService
                safety = RideSafetyService()
                risk_id = await safety.repo.next_risk_id()
                await safety.repo.save_risk_event({
                    "riskId": risk_id,
                    "type": RISK_EVENT_MILEAGE,
                    "memberId": int(ride.get("memberId") or 0),
                    "rideId": ride["rideId"],
                    "detail": (f"实际{actual_km:.1f}km vs 预估"
                               f"{estimated_km:.1f}km(超"
                               f"{RIDE_MILEAGE_ANOMALY_RATIO}倍)"),
                    "resolved": False, "resolvedNote": "",
                    "resolvedAt": None, "createdAt": _now_iso(),
                })
                logger.warning("ride_mileage_anomaly ride=%s "
                               "actual=%.1f estimated=%.1f",
                               ride["rideId"], actual_km, estimated_km)
            km_for_pricing = actual_km
        else:
            km_for_pricing = estimated_km

        fare = compute_fare(km_for_pricing, minutes, hour=pricing_hour)
        total = fare["totalAmount"]
        coupon_value = float(ride.get("couponValue") or 0)
        deduction = round(min(coupon_value, total), 2)   # 本站支付部分
        extra = round(total - deduction, 2)               # 乘客补差部分

        # 券核销(一单一券; 平台/券异常不阻断结算主流程, 差额记营销成本)
        coupon_result = None
        from services.ride_coupon_service import RideCouponService
        try:
            coupon_result = await RideCouponService().redeem(
                ride.get("couponCode") or "", ride_id=ride["rideId"])
        except (KeyError, ValueError) as exc:
            logger.warning("ride_settle_coupon_failed ride=%s: %s",
                           ride["rideId"], exc)
            if deduction > 0:
                # 券不可用 → 本站不垫付, 全额转乘客补差
                extra = total
                deduction = 0.0

        snapshot = ride.get("driverSnapshot") or {}
        track = snapshot.get("track") or TRACK_PLATFORM
        settlement_id = await self.repo.next_settlement_id()
        settlement = {
            "settlementId": settlement_id,
            "rideId": ride["rideId"],
            "memberId": ride.get("memberId"),
            "driverId": ride.get("driverId"),
            "driverName": snapshot.get("name", ""),
            "track": track,
            "totalAmount": total,
            "couponDeduction": deduction,
            "couponCode": ride.get("couponCode") or "",   # P4 对账用
            "extraCharge": extra,
            "payoutAmount": total,     # 司机/平台应收全额
            "payoutStatus": "paid" if track == TRACK_SELF else "aggregated",
            "pricingDetail": fare,
            "partnerOrderId": snapshot.get("partnerOrderId"),
            "createdAt": _now_iso(),
        }
        await self.repo.save_settlement(settlement)

        # 司机统计回写 + 释放占用(自营/加盟; 平台直发无本站司机记录)
        if driver is not None:
            driver["completedOrders"] = (int(driver.get("completedOrders")
                                            or 0) + 1)
            driver["currentRideId"] = ""
            driver["updatedAt"] = _now_iso()
            await self.repo.save_driver(driver)

        ride.update({
            "status": RIDE_STATUS_SETTLED,
            "pricing": {
                **fare,
                "couponDeduction": deduction,
                "extraCharge": extra,
                "couponRedeemed": coupon_result is not None,
                "couponCode": ride.get("couponCode"),
            },
            "settlementId": settlement_id,
            "settledAt": _now_iso(),
        })
        await self.repo.save_ride(ride)
        logger.info("ride_settled ride=%s total=%s deduction=%s extra=%s "
                    "payoutStatus=%s", ride["rideId"], total, deduction,
                    extra, settlement["payoutStatus"])
        return ride

    # --------------------------------------------------------
    # 查询
    # --------------------------------------------------------

    async def _get_ride_or_404(self, ride_id: str) -> dict:
        ride = await self.repo.get_ride(str(ride_id))
        if ride is None:
            raise KeyError(f"行程 {ride_id} 不存在")
        return ride

    async def get_ride(self, member_id: int, ride_id: str) -> dict:
        """乘客查行程详情(本人校验)"""
        ride = await self._get_ride_or_404(ride_id)
        if int(ride.get("memberId") or 0) != int(member_id):
            raise ValueError("仅乘客本人可查看该行程")
        return self._ride_detail(ride)

    async def list_my_rides(self, member_id: int,
                           status: str = None) -> list[dict]:
        return [self._ride_detail(r) for r in
                await self.repo.list_rides(member_id=member_id,
                                           status=status)]

    async def list_driver_rides(self, member_id: int,
                                status: str = None) -> list[dict]:
        driver = await self._require_driver_by_member(member_id)
        return await self.repo.list_rides(
            driver_id=driver["driverId"], status=status)

    async def list_driver_settlements(self, member_id: int) -> list[dict]:
        driver = await self._require_driver_by_member(member_id)
        return await self.repo.list_settlements(
            driver_id=driver["driverId"])

    async def admin_rides(self, status: str = None,
                          limit: int = 200) -> list[dict]:
        return await self.repo.list_rides(status=status, limit=limit)

    async def admin_settlements(self, track: str = None,
                                payout_status: str = None,
                                limit: int = 200) -> list[dict]:
        return await self.repo.list_settlements(
            track=track, payout_status=payout_status, limit=limit)

    @staticmethod
    def _ride_detail(ride: dict) -> dict:
        out = dict(ride)
        out.pop("candidates", None)   # 候选列表仅派单决策留痕, 不外泄
        return out

    # --------------------------------------------------------
    # P4 Hedge 学习回流(第23档案 ride_dispatch)
    # --------------------------------------------------------

    async def collect_learning_feedback(self) -> dict:
        """批量回流: 已结算且有乘客评价的 AI 派单行程 → 派单决策反馈

        真值口径(乘客评司机星级 → 期望动作):
            4-5 星 → dispatch(优, 应直接派)
            3 星   → dispatch_backup(中, 次优选派合理)
            1-2 星 → escalate(差, 不如直发)

        只回流 dispatchScoring 存在(AI 派单)且未 dispatchFed 的行程;
        单条失败不阻断批量。

        Returns:
            {submitted, skipped, results}
        """
        rides = await self.repo.list_rides(
            status=RIDE_STATUS_SETTLED, limit=1000)
        from repositories.ride_repository import REVIEW_BY_PASSENGER
        submitted, skipped, results = 0, 0, []
        for ride in rides:
            if ride.get("dispatchFed"):
                skipped += 1
                continue
            scoring = ride.get("dispatchScoring") or {}
            factors = scoring.get("factors") or []
            if not factors:
                skipped += 1     # 平台直发/旧数据无 AI 评分快照
                continue
            review = await self.repo.get_review_by_ride(
                ride["rideId"], REVIEW_BY_PASSENGER)
            if review is None:
                skipped += 1     # 无评价 → 无真值
                continue
            try:
                results.append(await self._submit_dispatch_feedback(
                    ride, review))
                submitted += 1
            except (KeyError, ValueError) as exc:
                skipped += 1
                logger.warning("ride_dispatch_feed_skip ride=%s: %s",
                               ride["rideId"], exc)
        return {"submitted": submitted, "skipped": skipped,
                "results": results}

    async def _submit_dispatch_feedback(self, ride: dict,
                                         review: dict) -> dict:
        """单条派单决策回流(submit_feedback 第23档案)"""
        stars = int(review.get("reviewScore") or 3)
        expected = ("dispatch" if stars >= 4
                   else ("dispatch_backup" if stars == 3
                         else "escalate"))
        actual = (ride.get("dispatchScoring") or {}).get("action") \
            or "dispatch"
        reward = round((stars - 3) / 2, 2)   # 1星-1.0 … 5星+1.0
        from services.ai_learning_service import submit_feedback
        result = await submit_feedback({
            "scorerId": "ride_dispatch",
            "factors": (ride.get("dispatchScoring")
                        or {}).get("factors") or [],
            "scoreAtDecision": float(ride.get("dispatchScore") or 0),
            "actualAction": actual,
            "expectedAction": expected,
            "correct": actual == expected,
            "reward": reward,
            "note": f"rideId={ride['rideId']} stars={stars}",
            "source": "ride",
        })
        ride["dispatchFed"] = True     # 幂等标记
        await self.repo.save_ride(ride)
        return result

    async def run_learning(self) -> dict:
        """触发第23档案一轮 Hedge 学习(反馈不足抛 ValueError)"""
        from services.ai_learning_service import run_learning_cycle
        return await run_learning_cycle("ride_dispatch")
