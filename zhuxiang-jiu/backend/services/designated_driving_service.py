"""免费代驾域业务逻辑层(P2: 合作商模块·第三章代驾服务管理)

核心业务(设计文档 3.1 免费代驾规则):
    - 触发条件: 合作商场所消费≥1瓶竹奕酒(500ml) → 消费凭证核验
    - 代驾距离: ≤30公里全额代付, 超出拒单(触发条件不满足)
    - 代驾时段: 全天 24 小时
    - 代付上限: ¥200/次, 超出部分用户自付
    - 计价规则(3.1.2):
        起步价 ¥30(5公里内) ×(1+夜间50%)×(1+节假日30%)
        里程费 ¥5/公里(超5公里部分) ×(1+夜间)×(1+节假日)×(1+恶劣天气30%)
        平台服务费 ¥5/单(不加成)
        等待费 ¥1/分钟(超10分钟免费等待)
      (公式按设计文档 3.1.3 三示例反推校验: 示例2=196.25/示例3=443.75 全对齐)

状态机: created → arriving → in_trip → completed → paid
        created/arriving → cancelled(凭证退回)

锁保护:
    - 下单: lock:venue:dd:verif:{verification_id}  (凭证核销原子)
    - 代付: lock:venue:dd:order:{order_id}  (幂等代付)
    - 流转: lock:venue:dd:order:{order_id}  (状态机原子更新)

异常约定:
    - KeyError → 404(凭证/订单/合作商不存在)
    - ValueError → 409(距离超限/凭证已用或过期/状态非法等)
"""

import logging
from datetime import datetime, timedelta

from core.locks import get_lock
from core.helpers import ts
from repositories.designated_driving_repository import (
    DesignatedDrivingRepository,
    DD_STATUS_CREATED, DD_STATUS_ARRIVING, DD_STATUS_IN_TRIP,
    DD_STATUS_COMPLETED, DD_STATUS_PAID, DD_STATUS_CANCELLED,
    DD_TRANSITIONS,
    VERIF_STATUS_VALID, VERIF_STATUS_USED, VERIF_STATUS_RETURNED,
)
from repositories.venue_repository import (
    VenueRepository,
    PARTNER_STATUS_ACTIVE, PARTNER_STATUS_SIGNED,
)

logger = logging.getLogger(__name__)


# ============================================================
# 计价规则常量(设计文档 3.1.2)
# ============================================================

DD_BASE_FARE = 30.0        # 起步价(5公里内)
DD_BASE_KM = 5             # 起步里程
DD_PER_KM = 5.0            # 超起步里程费(元/公里)
DD_PLATFORM_FEE = 5.0      # 平台服务费(每单, 不参与加成)
DD_NIGHT_SURGE = 0.50      # 夜间加成 50%(22:00-06:00)
DD_NIGHT_START = 22        # 夜间起始时
DD_NIGHT_END = 6           # 夜间结束时
DD_WEATHER_SURGE = 0.30    # 恶劣天气加成 30%(仅里程费)
DD_HOLIDAY_SURGE = 0.30    # 节假日加成 30%
DD_WAIT_FREE_MINUTES = 10  # 免费等待(分钟)
DD_WAIT_PER_MIN = 1.0      # 超时等待费(元/分钟)
DD_PAY_CAP = 200.0         # 本站代付上限(元/次)
DD_MAX_DISTANCE_KM = 30    # 免费代驾距离上限(公里)
DD_MIN_BOTTLES = 1         # 触发门槛: 竹奕酒≥1瓶(500ml)
VERIF_VALID_HOURS = 24     # 消费凭证有效期(小时)

WEATHER_TYPES = {"normal", "rain", "snow", "wind"}


def is_night_hour(hour: int) -> bool:
    """夜间时段判定(22:00-06:00, 跨零点)"""
    return hour >= DD_NIGHT_START or hour < DD_NIGHT_END


def estimate_fare(distance_km: float, hour: int = None,
                  weather: str = "normal", holiday: bool = False,
                  wait_minutes: int = 0) -> dict:
    """代驾费用预估(纯函数, 设计文档 3.1.2 公式)

    加成口径(按 3.1.3 示例反推):
        - 夜间/节假日: 起步价与里程费均加成
        - 恶劣天气: 仅里程费加成
        - 平台服务费/等待费: 不加成
    """
    if distance_km < 0:
        raise ValueError(f"距离非法({distance_km}km)")
    if weather not in WEATHER_TYPES:
        raise ValueError(f"天气参数非法({weather}), 应为 {'/'.join(WEATHER_TYPES)}")
    if hour is None:
        hour = datetime.now().hour

    night = is_night_hour(int(hour))
    weather_on = weather != "normal"
    night_f = 1 + (DD_NIGHT_SURGE if night else 0)
    holiday_f = 1 + (DD_HOLIDAY_SURGE if holiday else 0)
    weather_f = 1 + (DD_WEATHER_SURGE if weather_on else 0)

    base = DD_BASE_FARE * night_f * holiday_f
    extra_km = max(0.0, distance_km - DD_BASE_KM)
    mileage = DD_PER_KM * extra_km * night_f * holiday_f * weather_f
    wait = DD_WAIT_PER_MIN * max(0, int(wait_minutes) - DD_WAIT_FREE_MINUTES)
    total = round(base + mileage + DD_PLATFORM_FEE + wait, 2)

    return {
        "distanceKm": round(distance_km, 2),
        "hour": int(hour),
        "night": night,
        "weather": weather,
        "holiday": bool(holiday),
        "baseFare": round(base, 2),
        "mileageFee": round(mileage, 2),
        "platformFee": DD_PLATFORM_FEE,
        "waitFee": round(wait, 2),
        "totalFee": total,
        "eligibleFreeRide": distance_km <= DD_MAX_DISTANCE_KM,
        "platformPayPreview": round(min(total, DD_PAY_CAP), 2),
        "userPayPreview": round(max(0.0, total - DD_PAY_CAP), 2),
    }


class DesignatedDrivingService:
    """免费代驾业务逻辑(双模式存储, 锁保护 RMW)"""

    def __init__(self,
                 repo: DesignatedDrivingRepository = None,
                 venue_repo: VenueRepository = None):
        self.repo = repo or DesignatedDrivingRepository()
        self.venue_repo = venue_repo or VenueRepository()

    # ============================================================
    # 1. 消费验证(免费代驾资格)
    # ============================================================

    async def verify_consumption(self, user_id: int, partner_id: int,
                                 bottles: int,
                                 consumption_ref: str = "") -> dict:
        """验证合作商场所消费(≥1瓶竹奕酒 → 发 24h 消费凭证)

        Raises:
            KeyError: 合作商不存在
            ValueError: 消费数量不足 / 合作商非合作状态
        """
        if bottles < DD_MIN_BOTTLES:
            raise ValueError(
                f"免费代驾须消费≥{DD_MIN_BOTTLES}瓶竹奕酒, 当前 {bottles} 瓶")
        partner = await self.venue_repo.get_partner(partner_id)
        if partner is None:
            raise KeyError(f"合作商不存在(id={partner_id})")
        if partner.get("status") not in (PARTNER_STATUS_ACTIVE,
                                         PARTNER_STATUS_SIGNED):
            raise ValueError(
                f"合作商非合作状态({partner.get('status')}), 不可验证消费")

        now = datetime.utcnow()
        verif = {
            "id": await self.repo.next_verification_id(),
            "userId": user_id,
            "partnerId": partner_id,
            "partnerName": partner.get("partnerName", ""),
            "bottles": bottles,
            "consumptionRef": consumption_ref,
            "status": VERIF_STATUS_VALID,
            "createdAt": now.isoformat(),
            "expiresAt": (now + timedelta(hours=VERIF_VALID_HOURS)).isoformat(),
        }
        await self.repo.save_verification(verif)
        return verif

    def _check_verif_usable(self, verif: dict, user_id: int) -> None:
        """凭证可用性校验(归属/状态/过期)"""
        if verif is None:
            raise KeyError("消费凭证不存在")
        if verif.get("userId") != user_id:
            raise ValueError("消费凭证不属于当前会员")
        if verif.get("status") not in (VERIF_STATUS_VALID,
                                       VERIF_STATUS_RETURNED):
            raise ValueError(f"消费凭证不可用(状态={verif.get('status')})")
        if verif.get("expiresAt", "") <= ts():
            raise ValueError("消费凭证已过期(24 小时有效)")

    # ============================================================
    # 2. 代驾下单 / 查询 / 取消
    # ============================================================

    async def create_order(self, user_id: int, verification_id: int,
                           pickup_address: str, dropoff_address: str,
                           distance_km: float,
                           weather: str = "normal",
                           wait_minutes: int = 0) -> dict:
        """创建代驾订单(凭证核销 + 距离门槛 + 费用预估)

        Raises:
            KeyError: 凭证不存在
            ValueError: 距离>30km / 凭证已用或过期 / 非本人凭证
        """
        if distance_km > DD_MAX_DISTANCE_KM:
            raise ValueError(
                f"免费代驾距离上限 {DD_MAX_DISTANCE_KM} 公里, "
                f"当前 {distance_km} 公里(超出部分不适用免费代驾)")
        fare = estimate_fare(distance_km, weather=weather,
                             wait_minutes=wait_minutes)

        async with get_lock(f"venue:dd:verif:{verification_id}"):
            verif = await self.repo.get_verification(verification_id)
            self._check_verif_usable(verif, user_id)
            verif["status"] = VERIF_STATUS_USED
            verif["usedAt"] = ts()
            await self.repo.save_verification(verif)

            order = {
                "id": await self.repo.next_order_id(),
                "userId": user_id,
                "verificationId": verification_id,
                "partnerId": verif.get("partnerId"),
                "partnerName": verif.get("partnerName", ""),
                "pickupAddress": pickup_address,
                "dropoffAddress": dropoff_address,
                "distanceKm": round(distance_km, 2),
                "weather": weather,
                "waitMinutes": int(wait_minutes),
                "estimatedFee": fare["totalFee"],
                "feeDetail": fare,
                "platformPaid": 0.0,
                "userPaid": 0.0,
                "status": DD_STATUS_CREATED,
                "rating": None,
                "createdAt": ts(),
            }
            await self.repo.save_order(order)
        return order

    async def get_order(self, order_id: int) -> dict:
        """订单详情

        Raises:
            KeyError: 订单不存在
        """
        order = await self.repo.get_order(order_id)
        if order is None:
            raise KeyError(f"代驾订单不存在(id={order_id})")
        return order

    async def get_order_status(self, order_id: int) -> dict:
        """订单状态查询(轻量)"""
        order = await self.get_order(order_id)
        return {
            "orderId": order["id"],
            "userId": order.get("userId"),
            "status": order.get("status"),
            "createdAt": order.get("createdAt"),
            "paidAt": order.get("paidAt"),
        }

    async def cancel_order(self, order_id: int, user_id: int,
                           reason: str = "") -> dict:
        """取消代驾订单(仅 created/arriving 可取消, 凭证退回)

        Raises:
            KeyError: 订单不存在
            ValueError: 非本人 / 状态不可取消
        """
        async with get_lock(f"venue:dd:order:{order_id}"):
            order = await self.repo.get_order(order_id)
            if order is None:
                raise KeyError(f"代驾订单不存在(id={order_id})")
            if order.get("userId") != user_id:
                raise ValueError("仅订单所有者可取消")
            if order.get("status") not in (DD_STATUS_CREATED,
                                           DD_STATUS_ARRIVING):
                raise ValueError(
                    f"当前状态({order.get('status')})不可取消, "
                    f"仅行程开始前可取消")

            order["status"] = DD_STATUS_CANCELLED
            order["cancelReason"] = reason
            order["cancelledAt"] = ts()
            await self.repo.save_order(order)

            # 凭证退回(未过期则恢复可用)
            verif = await self.repo.get_verification(
                order.get("verificationId"))
            if verif and verif.get("status") == VERIF_STATUS_USED:
                if verif.get("expiresAt", "") > ts():
                    verif["status"] = VERIF_STATUS_RETURNED
                await self.repo.save_verification(verif)
        return order

    async def list_records(self, user_id: int, limit: int = 50) -> list[dict]:
        """会员代驾记录(按创建时间倒序)"""
        return await self.repo.list_orders(user_id=user_id, limit=limit)

    # ============================================================
    # 3. 状态推进(司机/合作商上报侧, 服务层供调度或测试驱动)
    # ============================================================

    async def advance_order(self, order_id: int, to_status: str) -> dict:
        """推进订单状态(状态机校验)

        Raises:
            KeyError: 订单不存在
            ValueError: 状态流转非法
        """
        async with get_lock(f"venue:dd:order:{order_id}"):
            order = await self.repo.get_order(order_id)
            if order is None:
                raise KeyError(f"代驾订单不存在(id={order_id})")
            allowed = DD_TRANSITIONS.get(order.get("status"), set())
            if to_status not in allowed:
                raise ValueError(
                    f"状态流转非法({order.get('status')} → {to_status})")
            order["status"] = to_status
            order["updatedAt"] = ts()
            await self.repo.save_order(order)
        return order

    # ============================================================
    # 4. 本站代付
    # ============================================================

    async def pay_order(self, order_id: int,
                        final_fee: float = None,
                        wait_minutes: int = None) -> dict:
        """本站代付代驾费(completed 后, ≤¥200 全额代付, 超出用户自付)

        Args:
            final_fee: 实际费用(缺省用预估; 行程计价以实际为准)
            wait_minutes: 实际等待分钟(用于按实际重算)

        Raises:
            KeyError: 订单不存在
            ValueError: 订单未完成 / 已代付
        """
        async with get_lock(f"venue:dd:order:{order_id}"):
            order = await self.repo.get_order(order_id)
            if order is None:
                raise KeyError(f"代驾订单不存在(id={order_id})")
            if order.get("status") == DD_STATUS_PAID:
                raise ValueError("订单已代付(幂等拒绝)")
            if order.get("status") != DD_STATUS_COMPLETED:
                raise ValueError(
                    f"订单未完成(状态={order.get('status')}), "
                    f"完成行程后才可代付")

            # 实际费用: 显式指定 > 按实际等待重算 > 预估
            if final_fee is None:
                if wait_minutes is not None:
                    final_fee = estimate_fare(
                        order.get("distanceKm", 0),
                        weather=order.get("weather", "normal"),
                        wait_minutes=wait_minutes)["totalFee"]
                else:
                    final_fee = order.get("estimatedFee", 0)
            final_fee = round(float(final_fee), 2)

            platform_paid = round(min(final_fee, DD_PAY_CAP), 2)
            user_paid = round(max(0.0, final_fee - DD_PAY_CAP), 2)
            order["finalFee"] = final_fee
            order["platformPaid"] = platform_paid
            order["userPaid"] = user_paid
            order["payCap"] = DD_PAY_CAP
            order["status"] = DD_STATUS_PAID
            order["paidAt"] = ts()
            await self.repo.save_order(order)
        return order

    # ============================================================
    # 5. 评价 / 合作商查询 / 统计
    # ============================================================

    async def rate_order(self, order_id: int, user_id: int,
                         rating: int) -> dict:
        """评价代驾服务(1-5 星, 已代付订单)

        Raises:
            KeyError: 订单不存在
            ValueError: 非本人 / 状态未代付 / 评分越界 / 重复评价
        """
        if not 1 <= int(rating) <= 5:
            raise ValueError(f"评分须在 1-5 星, 当前 {rating}")
        async with get_lock(f"venue:dd:order:{order_id}"):
            order = await self.repo.get_order(order_id)
            if order is None:
                raise KeyError(f"代驾订单不存在(id={order_id})")
            if order.get("userId") != user_id:
                raise ValueError("仅订单所有者可评价")
            if order.get("status") != DD_STATUS_PAID:
                raise ValueError("仅已代付订单可评价")
            if order.get("rating"):
                raise ValueError("订单已评价(幂等拒绝)")
            order["rating"] = int(rating)
            order["ratedAt"] = ts()
            await self.repo.save_order(order)
        return order

    async def get_partner(self, partner_id: int) -> dict:
        """合作商查询(代驾域视角, 复用 venue 仓储)

        Raises:
            KeyError: 合作商不存在
        """
        partner = await self.venue_repo.get_partner(partner_id)
        if partner is None:
            raise KeyError(f"合作商不存在(id={partner_id})")
        return partner

    async def stats(self) -> dict:
        """代驾统计"""
        return await self.repo.stats()
