"""41号·AI智能代驾模块·满额赠券引擎(设计文档 §2.1)

补齐全站券体系空白的第一块完整落地:
    订单支付成功 → AI 钩子自动赠券(档位梯度/幂等/上限)
    → 券包聚合(用户维度) → 惰性过期 → 退款冲正 → 核销(P1 派单用)

档位梯度(设计文档 §2.1, 环境变量可覆盖):
    500 ≤ 实付 < 1000 → 1 张 / 1000 ≤ 实付 < 3000 → 2 张 / ≥3000 → 3 张
    券面值 60 元, 市内代驾(≤40km), 有效期 90 天, 未核销持有上限 6 张

幂等: 券码 = RIDE{orderId}_{seq}, 同一订单重复触发只发一次
冲正: 订单退款 → 未核销(granted)券作废(revoked), 已核销不追回
"""

import logging
from datetime import datetime, UTC, timedelta

from core.locks import get_lock
from repositories.ride_repository import (
    RideRepository,
    COUPON_THRESHOLD, COUPON_VALUE, COUPON_VALID_DAYS, COUPON_HOLD_CAP,
    grant_tier_count,
    COUPON_STATUS_GRANTED, COUPON_STATUS_USED,
    COUPON_STATUS_EXPIRED, COUPON_STATUS_REVOKED,
)


logger = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


class RideCouponService:
    """代驾券引擎(发放/查询/过期/冲正/核销)"""

    def __init__(self):
        self.repo = RideRepository()

    # --------------------------------------------------------
    # 满额赠券(订单支付钩子入口)
    # --------------------------------------------------------

    async def grant_for_order(self, member_id: int, order_id: str,
                              paid_amount: float) -> dict:
        """订单支付成功 → 按档位梯度自动赠券入券包

        幂等: 锁 + 订单维度已发检查(同订单重复触发只发一次);
        上限: 券包未核销持有数达 COUPON_HOLD_CAP 不再发放(留痕)。

        Returns:
            {granted, skipped, codes, tier, reason}
        """
        member_id = int(member_id)
        amount = float(paid_amount or 0)
        count = grant_tier_count(amount)
        if count == 0:
            return {"granted": 0, "skipped": 0, "codes": [],
                    "tier": 0,
                    "reason": f"实付 ¥{amount:.0f} 未达 ¥{COUPON_THRESHOLD:.0f} 赠券门槛"}

        async with get_lock(f"ride:coupon:grant:{order_id}"):
            # 幂等: 同订单已发过 → 直接返回
            existing = await self.repo.list_coupons(order_id=order_id,
                                                    limit=10)
            if existing:
                return {"granted": 0, "skipped": count,
                        "codes": [c["code"] for c in existing],
                        "tier": count, "reason": "该订单已赠券(幂等跳过)"}

            package = await self.repo.ensure_package(member_id)
            hold = int(package.get("holdCount") or 0)
            remaining = max(0, COUPON_HOLD_CAP - hold)
            grant_n = min(remaining, count)
            skipped = count - grant_n
            if grant_n == 0:
                return {"granted": 0, "skipped": skipped, "codes": [],
                        "tier": count,
                        "reason": f"券包持有已达上限 {COUPON_HOLD_CAP} 张(防囤积)"}

            now = datetime.now(UTC)
            expires_at = (now + timedelta(days=COUPON_VALID_DAYS)).isoformat()
            codes = []
            for seq in range(1, grant_n + 1):
                code = f"RIDE{order_id}_{seq}"
                coupon = {
                    "code": code,
                    "memberId": member_id,
                    "orderId": order_id,
                    "value": COUPON_VALUE,
                    "status": COUPON_STATUS_GRANTED,
                    "cityRadiusKm": 40,   # 仅限市内代驾
                    "grantedAt": now.isoformat(),
                    "expiresAt": expires_at,
                    "usedAt": None,
                    "usedRideId": None,
                    "revokedAt": None,
                }
                await self.repo.save_coupon(coupon)
                codes.append(code)

            package.update({
                "holdCount": hold + grant_n,
                "totalGranted": int(package.get("totalGranted") or 0) + grant_n,
                "createdAt": package.get("createdAt") or now.isoformat(),
                "updatedAt": now.isoformat(),
            })
            await self.repo.save_package(package)

            logger.info("ride_coupon_granted member=%s order=%s "
                        "granted=%d skipped=%d codes=%s",
                        member_id, order_id, grant_n, skipped, codes)
            return {"granted": grant_n, "skipped": skipped, "codes": codes,
                    "tier": count,
                    "reason": "" if skipped == 0
                    else f"达持有上限, 实发{grant_n}张跳过{skipped}张"}

    # --------------------------------------------------------
    # 券包查询(惰性过期)
    # --------------------------------------------------------

    async def _expire_stale(self, member_id: int) -> int:
        """惰性过期: granted 且过期时间已过 → expired, 回减持有数"""
        coupons = await self.repo.list_coupons(member_id=member_id,
                                               status=COUPON_STATUS_GRANTED,
                                               limit=200)
        now = datetime.now(UTC)
        expired_n = 0
        for coupon in coupons:
            try:
                exp = datetime.fromisoformat(coupon.get("expiresAt") or "")
            except ValueError:
                continue
            if exp < now:
                coupon["status"] = COUPON_STATUS_EXPIRED
                await self.repo.save_coupon(coupon)
                expired_n += 1
        if expired_n:
            package = await self.repo.ensure_package(member_id)
            package["holdCount"] = max(
                0, int(package.get("holdCount") or 0) - expired_n)
            package["updatedAt"] = _now_iso()
            await self.repo.save_package(package)
        return expired_n

    async def get_package(self, member_id: int) -> dict:
        """我的券包(含即将过期提醒)"""
        expired_n = await self._expire_stale(int(member_id))
        package = await self.repo.ensure_package(int(member_id))
        coupons = await self.repo.list_coupons(member_id=int(member_id),
                                               limit=200)
        now = datetime.now(UTC)
        expiring_soon = []
        for coupon in coupons:
            if coupon.get("status") != COUPON_STATUS_GRANTED:
                continue
            try:
                exp = datetime.fromisoformat(coupon.get("expiresAt") or "")
            except ValueError:
                continue
            if timedelta(0) < exp - now < timedelta(days=7):
                expiring_soon.append(coupon["code"])
        return {
            "success": True,
            "memberId": int(member_id),
            "holdCount": int(package.get("holdCount") or 0),
            "totalGranted": int(package.get("totalGranted") or 0),
            "totalUsed": int(package.get("totalUsed") or 0),
            "totalRevoked": int(package.get("totalRevoked") or 0),
            "holdCap": COUPON_HOLD_CAP,
            "expiredJustNow": expired_n,
            "expiringSoon": expiring_soon,
            "coupons": sorted(coupons,
                              key=lambda c: (c.get("status") or "",
                                             c.get("expiresAt") or "")),
        }

    # --------------------------------------------------------
    # 冲正(订单退款钩子)
    # --------------------------------------------------------

    async def revoke_for_order(self, order_id: str) -> dict:
        """订单退款 → 未核销券作废(已核销不追回, 计入营销成本)

        Returns:
            {revoked, keptUsed, total}
        """
        coupons = await self.repo.list_coupons(order_id=order_id, limit=20)
        revoked = kept = 0
        now = _now_iso()
        member_ids = set()
        for coupon in coupons:
            member_ids.add(int(coupon.get("memberId") or 0))
            if coupon.get("status") == COUPON_STATUS_GRANTED:
                coupon["status"] = COUPON_STATUS_REVOKED
                coupon["revokedAt"] = now
                await self.repo.save_coupon(coupon)
                revoked += 1
            elif coupon.get("status") == COUPON_STATUS_USED:
                kept += 1
        # 回减券包持有数
        for member_id in member_ids:
            if member_id <= 0:
                continue
            package = await self.repo.ensure_package(member_id)
            package["holdCount"] = max(
                0, int(package.get("holdCount") or 0) - revoked)
            package["totalRevoked"] = (int(package.get("totalRevoked") or 0)
                                       + revoked)
            package["updatedAt"] = now
            await self.repo.save_package(package)
        if revoked:
            logger.info("ride_coupon_revoked order=%s revoked=%d keptUsed=%d",
                        order_id, revoked, kept)
        return {"revoked": revoked, "keptUsed": kept, "total": len(coupons)}

    # --------------------------------------------------------
    # 核销(P1 派单结算入口, P0 先行提供口径)
    # --------------------------------------------------------

    async def redeem(self, code: str, ride_id: str = "") -> dict:
        """核销代驾券(一单一券, 最早过期者优先在派单层选券)

        Raises:
            KeyError: 券不存在
            ValueError: 券状态非法(已核销/过期/作废)
        """
        coupon = await self.repo.get_coupon(str(code))
        if coupon is None:
            raise KeyError(f"代驾券 {code} 不存在")
        status = coupon.get("status")
        if status == COUPON_STATUS_USED:
            raise ValueError(f"券 {code} 已核销")
        if status == COUPON_STATUS_EXPIRED:
            raise ValueError(f"券 {code} 已过期")
        if status == COUPON_STATUS_REVOKED:
            raise ValueError(f"券 {code} 已作废(订单退款冲正)")
        # 惰性过期兜底
        try:
            exp = datetime.fromisoformat(coupon.get("expiresAt") or "")
        except (ValueError, TypeError):
            exp = None
        if exp is not None and exp < datetime.now(UTC):
            coupon["status"] = COUPON_STATUS_EXPIRED
            await self.repo.save_coupon(coupon)
            package = await self.repo.ensure_package(
                int(coupon.get("memberId") or 0))
            package["holdCount"] = max(
                0, int(package.get("holdCount") or 0) - 1)
            await self.repo.save_package(package)
            raise ValueError(f"券 {code} 已过期")

        coupon["status"] = COUPON_STATUS_USED
        coupon["usedAt"] = _now_iso()
        coupon["usedRideId"] = ride_id or None
        await self.repo.save_coupon(coupon)
        package = await self.repo.ensure_package(
            int(coupon.get("memberId") or 0))
        package["holdCount"] = max(0, int(package.get("holdCount") or 0) - 1)
        package["totalUsed"] = int(package.get("totalUsed") or 0) + 1
        package["updatedAt"] = _now_iso()
        await self.repo.save_package(package)
        return {"success": True, "code": code,
                "value": coupon.get("value"),
                "usedRideId": coupon.get("usedRideId")}

    # --------------------------------------------------------
    # P5: 会员营销 ROI 报表(设计文档 §7 营销 ROI 回流)
    # --------------------------------------------------------

    async def admin_roi(self) -> dict:
        """赠券营销 ROI: 发放 → 核销率 → 复购链路统计

        口径:
            - 发放/核销/过期/作废/持有: 全量券状态分布
            - 核销率 = used / totalGranted
            - 复购会员: 同一会员有 ≥2 个不同来源订单赠券
              (再次满额下单才会产生新赠券, 为复购代理口径)
            - 营销成本 = 已核销券面值合计(本站支付部分)
            - 券均拉动: 每张核销券对应的行程总费用均值
        """
        coupons = await self.repo.list_coupons(limit=5000)
        granted = len(coupons)
        by_status = {s: 0 for s in ("granted", "used",
                                    "expired", "revoked")}
        used_value = 0.0
        member_orders = {}   # memberId → set(orderId)
        for c in coupons:
            st = c.get("status")
            if st in by_status:
                by_status[st] += 1
            if st == "used":
                used_value += float(c.get("value") or 0)
            mid = int(c.get("memberId") or 0)
            oid = c.get("orderId")
            if mid and oid:
                member_orders.setdefault(mid, set()).add(oid)

        used = by_status["used"]
        used_rate = round(used / granted, 4) if granted else 0.0
        repeat_members = sum(1 for orders in member_orders.values()
                            if len(orders) >= 2)
        distinct_members = len(member_orders)
        repeat_rate = (round(repeat_members / distinct_members, 4)
                       if distinct_members else 0.0)

        # 核销券拉动的行程费用(券 → 行程结算总额)
        rides_total = 0.0
        settled_with_coupon = 0
        rides = await self.repo.list_rides(limit=5000)
        for r in rides:
            if r.get("status") != "settled":
                continue
            pricing = r.get("pricing") or {}
            if pricing.get("couponRedeemed"):
                settled_with_coupon += 1
                rides_total += float(pricing.get("totalAmount") or 0)
        avg_ride = round(rides_total / settled_with_coupon, 2) \
            if settled_with_coupon else 0.0

        return {
            "success": True,
            "totalGranted": granted,
            "byStatus": by_status,
            "usedRate": used_rate,
            "distinctMembers": distinct_members,
            "repeatMembers": repeat_members,
            "repeatRate": repeat_rate,
            "marketingCost": round(used_value, 2),
            "settledRidesWithCoupon": settled_with_coupon,
            "avgRideAmountPerUsedCoupon": avg_ride,
            "couponValue": COUPON_VALUE,
        }
