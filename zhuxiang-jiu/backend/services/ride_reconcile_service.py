"""41号·AI智能代驾模块·日结对账(设计文档 §2.5, 物流结算单模式平移)

三方对平: 本站结算单(ride_settlements) vs 合作平台账单(channel_bills)
          vs 券核销记录(ride_coupons)

状态机(物流结算单模式平移):
    主链: pending → reconciling → confirmed → paid
    差异分支: diff → investigating → resolved → confirmed

差异类型:
    amount_mismatch   金额不符(|本站-平台| > 0.01)
    order_missing     单据缺失(本站有, 平台无)
    extra_order        多余单据(平台有, 本站无)
    coupon_unredeemed 结算单的券未核销

单号: RCN{YYYYMMDD}{track}; 锁: ride:recon:{period}:{track}

异常约定(遵循项目约定):
    - KeyError → 404(对账单不存在)
    - ValueError → 409(状态非法/单号重复/并发对账)
"""

import logging
from datetime import datetime, UTC

from core.locks import get_lock
from repositories.ride_repository import (
    RideRepository,
    TRACK_SELF, TRACK_PARTNER, TRACK_PLATFORM,
    RECON_STATUS_PENDING, RECON_STATUS_RECONCILING,
    RECON_STATUS_CONFIRMED, RECON_STATUS_PAID,
    RECON_STATUS_DIFF, RECON_STATUS_INVESTIGATING,
    RECON_STATUS_RESOLVED,
    RECON_DIFF_AMOUNT, RECON_DIFF_MISSING,
    RECON_DIFF_EXTRA, RECON_DIFF_COUPON,
    COUPON_STATUS_USED,
)


logger = logging.getLogger(__name__)


# 对账轨道(自营直付本站, 不与外部平台对账; 加盟/直发与平台账单对)
RECON_TRACKS = (TRACK_PARTNER, TRACK_PLATFORM)

# 金额容差(元)
AMOUNT_TOLERANCE = 0.01


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def gen_recon_no(period: str, track: str) -> str:
    """对账单号 RCN{YYYYMMDD}{track}"""
    return f"RCN{period}{track}"


class RideReconcileService:
    """日结对账单: 生成/差异检测/状态机流转"""

    def __init__(self):
        self.repo = RideRepository()

    # --------------------------------------------------------
    # 生成对账单(三方比对)
    # --------------------------------------------------------

    async def generate(self, period: str, track: str,
                       channel_bills: list = None) -> dict:
        """生成日结对账单

        Args:
            period: 账期日(YYYY-MM-DD)
            track: partner/platform(自营直付不与外部对账)
            channel_bills: 平台账单 [{rideId|partnerOrderId,
                            totalAmount}]——Mock 口径由调用方
                            提供或测试注入; 缺省按本站数据镜像生成
                            (零差异, 验证主链)

        Raises:
            ValueError: 轨道非法/单号重复/并发对账
        """
        if track not in RECON_TRACKS:
            raise ValueError(f"对账轨道非法: {track}"
                             f"(仅 {'/'.join(RECON_TRACKS)} 与外部平台对账)")
        recon_no = gen_recon_no(period, track)

        async with get_lock(f"ride:recon:{period}:{track}"):
            existing = await self.repo.get_reconciliation(recon_no)
            if existing is not None:
                raise ValueError(f"对账单 {recon_no} 已存在"
                                f"(状态 {existing.get('status')})")

            # 本站侧: 该账期该轨道的结算单
            settlements = await self.repo.list_settlements(
                track=track, limit=1000)
            mine = [s for s in settlements
                    if str(s.get("createdAt") or "")
                    .startswith(period)]

            total_orders = len(mine)
            site_total = round(sum(float(s.get("totalAmount") or 0)
                                   for s in mine), 2)
            coupon_total = round(sum(float(s.get("couponDeduction") or 0)
                                     for s in mine), 2)

            # 平台侧: 缺省按本站镜像(Mock 口径); 注入则逐单比对
            if channel_bills is None:
                channel_bills = [
                    {"rideId": s.get("rideId"),
                     "partnerOrderId": (s.get("partnerOrderId") or ""),
                     "totalAmount": float(s.get("totalAmount") or 0)}
                    for s in mine]
            channel_total = round(sum(
                float(c.get("totalAmount") or 0) for c in channel_bills), 2)

            # ---- 三方差异检测 ----
            diff_details = []
            site_map = {str(s.get("rideId")): s for s in mine}
            bill_map = {}
            for c in channel_bills:
                key = str(c.get("rideId")
                          or c.get("partnerOrderId") or "")
                if key:
                    bill_map[key] = c

            # ① 单据缺失(本站有, 平台无)
            for rid, s in site_map.items():
                if rid not in bill_map:
                    diff_details.append({
                        "type": RECON_DIFF_MISSING, "rideId": rid,
                        "siteAmount": float(s.get("totalAmount") or 0),
                        "channelAmount": 0, "suggestion": "supplement"})
            # ② 多余单据(平台有, 本站无)
            for rid, c in bill_map.items():
                if rid not in site_map:
                    diff_details.append({
                        "type": RECON_DIFF_EXTRA, "rideId": rid,
                        "siteAmount": 0,
                        "channelAmount": float(c.get("totalAmount") or 0),
                        "suggestion": "ignore"})
            # ③ 金额不符(容差 0.01)
            for rid, s in site_map.items():
                if rid in bill_map:
                    sf = float(s.get("totalAmount") or 0)
                    cf = float(bill_map[rid].get("totalAmount") or 0)
                    if abs(sf - cf) > AMOUNT_TOLERANCE:
                        diff_details.append({
                            "type": RECON_DIFF_AMOUNT, "rideId": rid,
                            "siteAmount": sf, "channelAmount": cf,
                            "suggestion": ("refund" if cf < sf
                                           else "supplement")})
            # ④ 券核销比对(本站结算单 vs 券记录)
            for s in mine:
                code = s.get("couponCode") or ""
                pricing = s.get("pricingDetail") or {}
                if not code and not (s.get("couponDeduction") or 0):
                    continue
                coupon = await self.repo.get_coupon(code) if code \
                    else None
                if (coupon is None
                        or coupon.get("status") != COUPON_STATUS_USED):
                    diff_details.append({
                        "type": RECON_DIFF_COUPON,
                        "rideId": s.get("rideId"), "couponCode": code,
                        "siteAmount": float(
                            s.get("couponDeduction") or 0),
                        "channelAmount": 0,
                        "suggestion": "supplement"})

            diff_count = len(diff_details)
            status = (RECON_STATUS_DIFF if diff_count > 0
                      else RECON_STATUS_RECONCILING)
            recon = {
                "reconNo": recon_no,
                "period": period,
                "track": track,
                "totalOrders": total_orders,
                "siteTotal": site_total,
                "channelTotal": channel_total,
                "couponTotal": coupon_total,
                "diffCount": diff_count,
                "diffDetails": diff_details,
                "status": status,
                "investigator": "",
                "resolution": "",
                "confirmAt": None,
                "payAt": None,
                "createdAt": _now_iso(),
            }
            await self.repo.save_reconciliation(recon)
            logger.info("ride_reconciliation_generated recon=%s "
                        "orders=%s siteTotal=%s channelTotal=%s diff=%s",
                        recon_no, total_orders, site_total,
                        channel_total, diff_count)
            return recon

    # --------------------------------------------------------
    # 状态机流转(锁→读→校验前状态→写, 物流模式平移)
    # --------------------------------------------------------

    async def _transition(self, recon_no: str, expect_statuses: tuple,
                          target: str, extra: dict = None) -> dict:
        async with get_lock(f"ride:recon:recon:{recon_no}"):
            recon = await self.repo.get_reconciliation(recon_no)
            if recon is None:
                raise KeyError(f"对账单 {recon_no} 不存在")
            if recon.get("status") not in expect_statuses:
                raise ValueError(f"对账单状态 {recon.get('status')}, "
                                 f"不可流转至 {target}")
            recon["status"] = target
            recon.update(extra or {})
            await self.repo.save_reconciliation(recon)
            return recon

    async def investigate(self, recon_no: str,
                          investigator: str = "admin") -> dict:
        """diff → investigating(介入调查)"""
        return await self._transition(
            recon_no, (RECON_STATUS_DIFF,),
            RECON_STATUS_INVESTIGATING,
            {"investigator": investigator})

    async def resolve(self, recon_no: str, resolution: str = "") -> dict:
        """investigating → resolved(差异处理完毕)"""
        return await self._transition(
            recon_no, (RECON_STATUS_INVESTIGATING,),
            RECON_STATUS_RESOLVED, {"resolution": resolution})

    async def confirm(self, recon_no: str) -> dict:
        """reconciling/resolved → confirmed"""
        return await self._transition(
            recon_no, (RECON_STATUS_RECONCILING, RECON_STATUS_RESOLVED),
            RECON_STATUS_CONFIRMED, {"confirmAt": _now_iso()})

    async def pay(self, recon_no: str) -> dict:
        """confirmed → paid(付款完成, 终态)"""
        return await self._transition(
            recon_no, (RECON_STATUS_CONFIRMED,), RECON_STATUS_PAID,
            {"payAt": _now_iso()})

    # --------------------------------------------------------
    # 查询
    # --------------------------------------------------------

    async def get(self, recon_no: str) -> dict:
        recon = await self.repo.get_reconciliation(recon_no)
        if recon is None:
            raise KeyError(f"对账单 {recon_no} 不存在")
        return recon

    async def list_all(self, track: str = None, status: str = None,
                       period: str = None, limit: int = 100) -> list[dict]:
        """对账单列表(方法名避开内置 list, 否则类型注解 list[dict] 被遮蔽)"""
        return await self.repo.list_reconciliations(
            track=track, status=status, period=period, limit=limit)

    async def pending_list(self) -> list[dict]:
        """待处理对账单(reconciling/diff/investigating)"""
        out = []
        for st in (RECON_STATUS_RECONCILING, RECON_STATUS_DIFF,
                   RECON_STATUS_INVESTIGATING):
            out.extend(await self.repo.list_reconciliations(
                status=st, limit=100))
        return out
