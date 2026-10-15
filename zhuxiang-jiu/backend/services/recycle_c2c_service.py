"""C2C 老酒交易市场业务逻辑层(P2 Phase1 挂单 + Phase2 资金托管)

核心业务(接入设计稿 §六):
    - 挂单: 生命码归属校验 + 老酒资格(激活且酒龄≥3年) +
      估值锚定(askPrice 须在自动估值 0.5~1.5 倍区间) + 一瓶一挂单
    - 购买: 买家锁定 15 分钟(超时自动回架, 惰性推进)
    - Phase1 线下收款(offline): 卖家确认 → 过户(复用 trace.transfer_life_code,
      激活日延续) + sold 终态 + 平台佣金 5% 记录
    - Phase2 资金托管(escrow): 买家锁定即冻结货款(钱包扣款+流水) →
      卖家确认过户 delivered → 买家确认收货(或 7 天惰性超时自动确认) →
      settled: 货款划转卖家(sellerNet 入账) + 平台抽佣(commission 台账留痕);
      锁定超时回架/买家撤锁 → 托管退款(原路退回买家钱包)
    - 挂单 TTL 30 天自动过期(惰性推进)

状态机: draft→on_sale→locked→sold(offline 终态)
        on_sale→cancelled/expired; locked→on_sale(超时回架)
        escrow: locked→delivered(卖家确认过户)→settled(收货/超时结算, 终态)

锁保护(顺序: listing → escrow → wallet, 单向无环):
    - 挂单/锁定/成交: lock:recycle:c2c:listing:{listing_no} (状态机原子)
    - 在售位: recycle:c2c:active_by_life:{lifeCode} (SETNX 语义, 一瓶一挂单)
    - 托管资金: recycle:c2c:escrow:{escrow_no} (退款/结算幂等)
    - 钱包: wallet:{user_id} (与 wallet_service 同命名空间, 余额原子)

异常约定:
    - KeyError → 404(挂单/生命码不存在)
    - ValueError → 409(资格不符/锚定越界/状态非法/自购/余额不足等)
"""

import logging
from datetime import datetime, timedelta

from core.locks import get_lock
from core.helpers import ts
from repositories.recycle_repository import (
    RecycleC2CRepository,
    GRADE_COEFFICIENTS, GRADE_A,
    C2C_STATUS_DRAFT, C2C_STATUS_ON_SALE, C2C_STATUS_LOCKED,
    C2C_STATUS_SOLD, C2C_STATUS_CANCELLED, C2C_STATUS_EXPIRED,
    C2C_STATUS_DELIVERED, C2C_STATUS_SETTLED,
    C2C_PAY_OFFLINE, C2C_PAY_ESCROW,
    C2C_ESCROW_HOLDING, C2C_ESCROW_SETTLED, C2C_ESCROW_REFUNDED,
)
from repositories.trace_repository import (
    TraceRepository, LIFE_STATUS_ACTIVE, LIFE_STATUS_TRANSFERRED,
)
from repositories.wallet_repository import (
    WalletRepository, STATUS_ACTIVE as WALLET_ACTIVE,
)
from services.recycle_service import MIN_WINE_AGE_YEARS

logger = logging.getLogger("recycle_c2c")

# ============================================================
# C2C 业务常量(设计稿 §六)
# ============================================================

C2C_COMMISSION_RATE = 0.05        # 平台佣金 5%(业务可调)
C2C_LISTING_TTL_DAYS = 30         # 挂单有效期(天)
C2C_LOCK_MINUTES = 15             # 买家锁定时长(分钟)
C2C_ASK_PRICE_BAND = (0.5, 1.5)   # 挂单价锚定区间(相对自动估值)
C2C_RECEIPT_TIMEOUT_DAYS = 7      # Phase2 托管: 过户后买家确认收货时限(天, 超时自动确认结算)


class RecycleC2CService:
    """C2C 老酒交易市场业务逻辑"""

    def __init__(self,
                 repo: RecycleC2CRepository = None,
                 trace_repo: TraceRepository = None,
                 wallet_repo: WalletRepository = None):
        self.repo = repo or RecycleC2CRepository()
        self.trace_repo = trace_repo or TraceRepository()
        self.wallet_repo = wallet_repo or WalletRepository()

    # ============================================================
    # 1. 挂单
    # ============================================================

    def _anchor_value(self, purchase_price: float, wine_age: int,
                      condition_grade: str = GRADE_A) -> float:
        """估值锚定(复用回收模块增值率/品质系数口径)"""
        from services.recycle_service import (
            BASE_APPRECIATION_RATE, YEARLY_RATE_INCREMENT,
            MAX_APPRECIATION_RATE,
        )
        rate = BASE_APPRECIATION_RATE + YEARLY_RATE_INCREMENT * (
            wine_age - MIN_WINE_AGE_YEARS)
        rate = min(rate, MAX_APPRECIATION_RATE)
        coeff = GRADE_COEFFICIENTS.get(condition_grade, 1.0)
        return round(purchase_price * (1 + rate) * coeff, 2)

    def _wine_age(self, first_activation_date: str) -> int:
        """酒龄(以生命码激活日为基准, 对齐回收模块口径)"""
        if not first_activation_date:
            return 0
        try:
            activated = datetime.fromisoformat(
                str(first_activation_date)[:10])
        except ValueError:
            return 0
        delta = datetime.utcnow().year - activated.year
        now = datetime.utcnow()
        if (now.month, now.day) < (activated.month, activated.day):
            delta -= 1
        return max(0, delta)

    async def create_listing(self, seller_id: int, life_code: str,
                             ask_price: float,
                             condition_grade: str = GRADE_A,
                             pay_channel: str = C2C_PAY_OFFLINE) -> dict:
        """卖家挂单(老酒资格 + 估值锚定 + 一瓶一挂单)

        Args:
            pay_channel: offline=线下收款(Phase1) / escrow=平台托管(Phase2,
                挂单前置要求卖家已开通钱包——货款结算入账账户)

        Raises:
            KeyError: 生命码不存在
            ValueError: 未激活/未满3年/非本人/在售中/锚定越界/通道非法/未开通钱包
        """
        if ask_price <= 0:
            raise ValueError(f"挂单价非法({ask_price})")
        if condition_grade not in GRADE_COEFFICIENTS:
            raise ValueError(
                f"品质分级非法({condition_grade}), 应为 A/B/C/D")
        if pay_channel not in (C2C_PAY_OFFLINE, C2C_PAY_ESCROW):
            raise ValueError(
                f"结算通道非法({pay_channel}), 应为 offline/escrow")

        if pay_channel == C2C_PAY_ESCROW:
            account = await self.wallet_repo.get_account(seller_id)
            if account is None:
                raise ValueError(
                    f"托管挂单须先开通钱包(卖家收款账户, userId={seller_id})")
            if account.get("status") != WALLET_ACTIVE:
                raise ValueError(
                    f"卖家钱包状态异常({account.get('status')}), 不可托管挂单")

        life = await self.trace_repo.get_life_by_code(life_code)
        if life is None:
            raise KeyError(f"生命码不存在(lifeCode={life_code})")
        if life.get("status") not in (LIFE_STATUS_ACTIVE,
                                      LIFE_STATUS_TRANSFERRED):
            raise ValueError(
                f"生命码状态不可交易({life.get('status')}), "
                f"须为已激活/已转让")
        if life.get("userId") != seller_id:
            raise ValueError("仅生命码持有人可挂单")
        if not life.get("firstActivationDate"):
            raise ValueError("生命码未激活(老酒交易须以激活日起算酒龄)")

        wine_age = self._wine_age(life.get("firstActivationDate"))
        if wine_age < MIN_WINE_AGE_YEARS:
            raise ValueError(
                f"酒龄未满 {MIN_WINE_AGE_YEARS} 年({wine_age} 年), "
                f"不可参与老酒交易(未满期请走新酒议价回收)")

        purchase_price = float(life.get("purchasePrice") or 0)
        anchor = self._anchor_value(purchase_price, wine_age,
                                    condition_grade)
        low = round(anchor * C2C_ASK_PRICE_BAND[0], 2)
        high = round(anchor * C2C_ASK_PRICE_BAND[1], 2)
        if not low <= ask_price <= high:
            raise ValueError(
                f"挂单价须在自动估值 ¥{low}~¥{high} 区间"
                f"(估值 ¥{anchor}, 允许 0.5~1.5 倍), 当前 ¥{ask_price}")

        async with get_lock(f"recycle:c2c:life:{life_code}"):
            if not await self.repo.try_set_c2c_active_life(life_code):
                raise ValueError("该酒已在挂单中(一瓶一挂单)")

            listing = {
                "sellerId": seller_id,
                "sellerName": life.get("holderName", ""),
                "lifeCode": life_code,
                "productId": life.get("productId"),
                "productName": life.get("productName"),
                "batchNo": life.get("batchNo"),
                "wineAge": wine_age,
                "conditionGrade": condition_grade,
                "anchorValue": anchor,
                "askPrice": round(float(ask_price), 2),
                "payChannel": pay_channel,
                "escrowNo": None,
                "commissionRate": C2C_COMMISSION_RATE,
                "status": C2C_STATUS_ON_SALE,
                "buyerId": None,
                "buyerName": "",
                "lockedAt": None,
                "deliveredAt": None,
                "soldAt": None,
                "price": None,
                "commission": None,
                "sellerNet": None,
                "expiresAt": (datetime.utcnow()
                              + timedelta(days=C2C_LISTING_TTL_DAYS)
                              ).isoformat(),
            }
            listing_id = await self.repo.create_c2c_listing(listing)
            listing["id"] = listing_id
        return listing

    # ============================================================
    # 2. 查询(含惰性推进)
    # ============================================================

    async def _lazy_advance(self, listing: dict) -> dict:
        """惰性推进:
            locked 超 15 分钟未确认 → 回架(escrow 托管同步退款)
            on_sale 超 TTL → 过期
            delivered(escrow) 超 7 天未确认收货 → 自动确认结算
        """
        changed = False
        now = datetime.utcnow()
        if listing.get("status") == C2C_STATUS_LOCKED \
                and listing.get("lockedAt"):
            try:
                locked = datetime.fromisoformat(listing["lockedAt"])
                if locked.tzinfo is not None:
                    locked = locked.replace(tzinfo=None)
            except ValueError:
                locked = None
            if locked and now > locked + timedelta(
                    minutes=C2C_LOCK_MINUTES):
                # escrow 托管: 回架前先退款(幂等, 失败阻断回架保资金)
                if listing.get("payChannel") == C2C_PAY_ESCROW:
                    await self._escrow_refund(listing, "锁定超时自动回架")
                listing["status"] = C2C_STATUS_ON_SALE
                listing["buyerId"] = None
                listing["buyerName"] = ""
                listing["lockedAt"] = None
                listing["rollbackAt"] = ts()
                changed = True
        if listing.get("status") == C2C_STATUS_DELIVERED \
                and listing.get("deliveredAt"):
            try:
                delivered = datetime.fromisoformat(listing["deliveredAt"])
                if delivered.tzinfo is not None:
                    delivered = delivered.replace(tzinfo=None)
            except ValueError:
                delivered = None
            if delivered and now > delivered + timedelta(
                    days=C2C_RECEIPT_TIMEOUT_DAYS):
                # 超时自动确认收货: 货款结算卖家+平台抽佣
                await self._settle_escrow(listing, auto=True)
                changed = True
        if listing.get("status") == C2C_STATUS_ON_SALE \
                and listing.get("expiresAt", "") <= ts():
            listing["status"] = C2C_STATUS_EXPIRED
            await self.repo.clear_c2c_active_life(
                listing.get("lifeCode", ""))
            changed = True
        if changed:
            await self.repo.update_c2c_listing(listing["id"], listing)
        return listing

    async def get_listing(self, listing_no: str) -> dict:
        """挂单详情(惰性推进)

        Raises:
            KeyError: 挂单不存在
        """
        listing = await self.repo.get_c2c_by_no(listing_no)
        if listing is None:
            raise KeyError(f"挂单不存在(listingNo={listing_no})")
        return await self._lazy_advance(listing)

    async def market_list(self, status: str = None, limit: int = 50,
                          seller_id: int = None) -> list[dict]:
        """市场列表/我的挂单(惰性推进)"""
        listings = await self.repo.list_c2c_listings(
            seller_id=seller_id, status=status, limit=limit)
        return [await self._lazy_advance(l) for l in listings]

    async def my_buys(self, buyer_id: int, limit: int = 50) -> list[dict]:
        """我的购买(买家视角)"""
        return await self.repo.list_c2c_listings(
            buyer_id=buyer_id, limit=limit)

    # ============================================================
    # 3. 购买/成交/撤单/重挂
    # ============================================================

    async def buy_listing(self, listing_no: str, buyer_id: int,
                          buyer_name: str = "") -> dict:
        """买家锁定挂单(15 分钟内卖家确认)

        escrow 通道: 锁定即冻结货款(买家钱包扣款, 失败则不锁定);
        offline 通道: 仅锁定(Phase1 线下收款语义)

        Raises:
            KeyError: 挂单不存在
            ValueError: 已下架/自购/被他人锁定/钱包未开通/余额不足
        """
        async with get_lock(f"recycle:c2c:listing:{listing_no}"):
            listing = await self.repo.get_c2c_by_no(listing_no)
            if listing is None:
                raise KeyError(f"挂单不存在(listingNo={listing_no})")
            listing = await self._lazy_advance(listing)
            if listing.get("status") != C2C_STATUS_ON_SALE:
                raise ValueError(
                    f"挂单不可购买(状态={listing.get('status')})")
            if listing.get("sellerId") == buyer_id:
                raise ValueError("不可购买自己的挂单")

            escrow_no = None
            if listing.get("payChannel") == C2C_PAY_ESCROW:
                escrow_no = await self._escrow_freeze(listing, buyer_id)

            listing["status"] = C2C_STATUS_LOCKED
            listing["buyerId"] = buyer_id
            listing["buyerName"] = buyer_name
            listing["lockedAt"] = ts()
            listing["escrowNo"] = escrow_no
            await self.repo.update_c2c_listing(listing["id"], listing)
        return listing

    async def confirm_sale(self, listing_no: str, operator_id: int) -> dict:
        """卖家确认(过户 + 佣金计算)

        offline(Phase1): 确认线下收款 → 过户 + sold 终态
        escrow(Phase2):  确认发货过户 → delivered(货款仍托管,
                         待买家确认收货或 7 天超时自动结算)

        Raises:
            KeyError: 挂单不存在
            ValueError: 非卖家操作 / 状态非法(未锁定/已超时回架)
        """
        from services.trace_service import TraceService, TRANSFER_TYPE_TRADE
        async with get_lock(f"recycle:c2c:listing:{listing_no}"):
            listing = await self.repo.get_c2c_by_no(listing_no)
            if listing is None:
                raise KeyError(f"挂单不存在(listingNo={listing_no})")
            listing = await self._lazy_advance(listing)
            if listing.get("sellerId") != operator_id:
                raise ValueError("仅卖家可确认成交")
            if listing.get("status") != C2C_STATUS_LOCKED:
                raise ValueError(
                    f"挂单状态不可成交(当前{listing.get('status')}, "
                    f"须买家锁定中; 锁定超 15 分钟已自动回架)")

            price = listing["askPrice"]
            commission = round(price * C2C_COMMISSION_RATE, 2)
            # 生命码过户(激活日延续; 失败回滚挂单状态)
            try:
                await TraceService().transfer_life_code(
                    listing["lifeCode"], listing["sellerId"],
                    listing["buyerId"],
                    to_name=listing.get("buyerName") or None,
                    transfer_type=TRANSFER_TYPE_TRADE)
            except (KeyError, ValueError) as exc:
                raise ValueError(f"过户失败: {exc}") from exc

            if listing.get("payChannel") == C2C_PAY_ESCROW:
                # Phase2: 过户完成进入待收货, 货款托管中(收货时结算)
                listing.update({
                    "status": C2C_STATUS_DELIVERED,
                    "price": price,
                    "commission": commission,
                    "sellerNet": round(price - commission, 2),
                    "deliveredAt": ts(),
                })
            else:
                # Phase1: 线下收款即时成交
                listing.update({
                    "status": C2C_STATUS_SOLD,
                    "price": price,
                    "commission": commission,
                    "sellerNet": round(price - commission, 2),
                    "soldAt": ts(),
                })
                # 竹信分 C7 守约成交 +2(offline 通道, 双方各记; best-effort)
                try:
                    from services.role_service import (
                        RoleService, BEHAVIOR_C2C_TRADE_SETTLED,
                    )
                    role_svc = RoleService()
                    lno = listing.get("listingNo")
                    await role_svc.publish_member_credit_event(
                        int(listing.get("sellerId") or 0),
                        BEHAVIOR_C2C_TRADE_SETTLED,
                        "recycle", ref_id=f"c2c:{lno}:seller")
                    await role_svc.publish_member_credit_event(
                        int(listing.get("buyerId") or 0),
                        BEHAVIOR_C2C_TRADE_SETTLED,
                        "recycle", ref_id=f"c2c:{lno}:buyer")
                except Exception:
                    pass
            await self.repo.update_c2c_listing(listing["id"], listing)
            if listing["status"] in (C2C_STATUS_SOLD, C2C_STATUS_SETTLED):
                await self.repo.clear_c2c_active_life(listing["lifeCode"])
        return listing

    async def cancel_listing(self, listing_no: str, seller_id: int) -> dict:
        """卖家撤单(仅 on_sale 可撤; locked 须先等回架或成交)

        Raises:
            KeyError: 挂单不存在
            ValueError: 非卖家 / 状态不可撤
        """
        async with get_lock(f"recycle:c2c:listing:{listing_no}"):
            listing = await self.repo.get_c2c_by_no(listing_no)
            if listing is None:
                raise KeyError(f"挂单不存在(listingNo={listing_no})")
            listing = await self._lazy_advance(listing)
            if listing.get("sellerId") != seller_id:
                raise ValueError("仅卖家可撤单")
            if listing.get("status") != C2C_STATUS_ON_SALE:
                raise ValueError(
                    f"当前状态({listing.get('status')})不可撤单, "
                    f"仅在售中可撤(锁定中请等待超时回架或确认成交)")

            listing["status"] = C2C_STATUS_CANCELLED
            listing["cancelledAt"] = ts()
            await self.repo.update_c2c_listing(listing["id"], listing)
            await self.repo.clear_c2c_active_life(listing["lifeCode"])
        return listing

    async def relist_listing(self, listing_no: str, seller_id: int,
                             ask_price: float = None) -> dict:
        """重新上架(仅 cancelled/expired 可重挂; 价格可选更新仍受锚定)

        Raises:
            KeyError: 挂单不存在
            ValueError: 非卖家 / 状态不可重挂 / 在售位被占/价格越界
        """
        async with get_lock(f"recycle:c2c:listing:{listing_no}"):
            listing = await self.repo.get_c2c_by_no(listing_no)
            if listing is None:
                raise KeyError(f"挂单不存在(listingNo={listing_no})")
            if listing.get("sellerId") != seller_id:
                raise ValueError("仅卖家可重新上架")
            if listing.get("status") not in (C2C_STATUS_CANCELLED,
                                             C2C_STATUS_EXPIRED):
                raise ValueError(
                    f"当前状态({listing.get('status')})不可重新上架, "
                    f"仅已撤单/已过期可重挂")

            if ask_price is not None:
                ask_price = round(float(ask_price), 2)
                anchor = listing.get("anchorValue", 0)
                low = round(anchor * C2C_ASK_PRICE_BAND[0], 2)
                high = round(anchor * C2C_ASK_PRICE_BAND[1], 2)
                if not low <= ask_price <= high:
                    raise ValueError(
                        f"挂单价须在估值 ¥{low}~¥{high} 区间, "
                        f"当前 ¥{ask_price}")
                listing["askPrice"] = ask_price

            async with get_lock(
                    f"recycle:c2c:life:{listing['lifeCode']}"):
                if not await self.repo.try_set_c2c_active_life(
                        listing["lifeCode"]):
                    raise ValueError("该酒已在其他挂单中(一瓶一挂单)")

            listing["status"] = C2C_STATUS_ON_SALE
            listing["expiresAt"] = (datetime.utcnow()
                                    + timedelta(days=C2C_LISTING_TTL_DAYS)
                                    ).isoformat()
            listing["relistedAt"] = ts()
            await self.repo.update_c2c_listing(listing["id"], listing)
        return listing

    # ============================================================
    # 4. Phase2 资金托管(escrow)
    # ============================================================

    async def _escrow_freeze(self, listing: dict, buyer_id: int) -> str:
        """托管冻结: 买家钱包扣款+流水, 建托管单(holding)

        调用上下文: 已持 listing 锁; 内部持 wallet:{buyer} 锁
        Raises:
            ValueError: 钱包未开通/状态异常/余额不足
        """
        amount = listing["askPrice"]
        async with get_lock(f"wallet:{buyer_id}"):
            account = await self.wallet_repo.get_account(buyer_id)
            if account is None:
                raise ValueError(
                    f"托管购买须先开通钱包(userId={buyer_id})")
            if account.get("status") != WALLET_ACTIVE:
                raise ValueError(
                    f"钱包状态异常({account.get('status')}), 无法托管支付")
            balance = float(account.get("balance", 0))
            if balance < amount:
                raise ValueError(
                    f"钱包余额不足: 当前 ¥{balance:.2f}, "
                    f"需托管 ¥{amount:.2f}")

            new_balance = await self.wallet_repo.add_balance(
                buyer_id, -amount)
            tx_no = await self.wallet_repo.next_tx_no()
            await self.wallet_repo.save_transaction({
                "txNo": tx_no,
                "userId": buyer_id,
                "type": "c2c_escrow",
                "direction": "OUT",
                "amount": amount,
                "balanceAfter": new_balance,
                "payChannel": "wallet",
                "orderId": listing.get("listingNo", ""),
                "depositNo": "",
                "withdrawNo": "",
                "status": "success",
                "description": f"C2C 老酒托管冻结 ¥{amount:.2f}"
                                f"(挂单 {listing.get('listingNo')})",
                "createdAt": ts(),
            })

        price = listing["askPrice"]
        commission = round(price * C2C_COMMISSION_RATE, 2)
        escrow = {
            "escrowNo": None,           # repo 生成 ESC+日期+3位
            "listingNo": listing.get("listingNo"),
            "lifeCode": listing.get("lifeCode"),
            "sellerId": listing.get("sellerId"),
            "buyerId": buyer_id,
            "amount": price,
            "commission": commission,
            "sellerNet": round(price - commission, 2),
            "status": C2C_ESCROW_HOLDING,
            "freezeTxNo": tx_no,
            "refundTxNo": None,
            "settleTxNo": None,
            "refundReason": "",
            "autoSettled": False,
            "holdingAt": ts(),
        }
        escrow_no = await self.repo.create_c2c_escrow(escrow)
        logger.info("c2c_escrow_frozen escrowNo=%s listing=%s buyer=%s "
                    "amount=%.2f tx=%s",
                    escrow_no, listing.get("listingNo"), buyer_id,
                    amount, tx_no)
        return escrow_no

    async def _escrow_refund(self, listing: dict, reason: str) -> None:
        """托管退款: holding → refunded, 原路退回买家钱包(幂等, escrow 锁内防重)"""
        escrow_no = listing.get("escrowNo")
        if not escrow_no:
            return
        async with get_lock(f"recycle:c2c:escrow:{escrow_no}"):
            escrow = await self.repo.get_c2c_escrow(escrow_no)
            if escrow is None \
                    or escrow.get("status") != C2C_ESCROW_HOLDING:
                return  # 已退款/已结算(幂等)
            buyer_id = escrow["buyerId"]
            amount = escrow["amount"]
            async with get_lock(f"wallet:{buyer_id}"):
                new_balance = await self.wallet_repo.add_balance(
                    buyer_id, amount)
                tx_no = await self.wallet_repo.next_tx_no()
                await self.wallet_repo.save_transaction({
                    "txNo": tx_no,
                    "userId": buyer_id,
                    "type": "c2c_escrow_refund",
                    "direction": "IN",
                    "amount": amount,
                    "balanceAfter": new_balance,
                    "payChannel": "wallet",
                    "orderId": escrow.get("listingNo", ""),
                    "depositNo": "",
                    "withdrawNo": "",
                    "status": "success",
                    "description": f"C2C 托管退款 ¥{amount:.2f}"
                                    f"({reason})",
                    "createdAt": ts(),
                })
            await self.repo.update_c2c_escrow(escrow_no, {
                "status": C2C_ESCROW_REFUNDED,
                "refundTxNo": tx_no,
                "refundReason": reason,
                "refundedAt": ts(),
            })
            listing["escrowNo"] = None
            logger.info("c2c_escrow_refunded escrowNo=%s listing=%s "
                        "buyer=%s amount=%.2f reason=%s",
                        escrow_no, listing.get("listingNo"), buyer_id,
                        amount, reason)

    async def _settle_escrow(self, listing: dict, auto: bool = False) -> None:
        """托管结算: holding → settled, 货款划转卖家钱包+平台抽佣台账
        (幂等, escrow 锁内防重; 调用后置 listing settled 终态)"""
        escrow_no = listing.get("escrowNo")
        if not escrow_no:
            return
        async with get_lock(f"recycle:c2c:escrow:{escrow_no}"):
            escrow = await self.repo.get_c2c_escrow(escrow_no)
            if escrow is None \
                    or escrow.get("status") != C2C_ESCROW_HOLDING:
                return  # 已结算/已退款(幂等)
            seller_id = escrow["sellerId"]
            seller_net = escrow["sellerNet"]
            async with get_lock(f"wallet:{seller_id}"):
                new_balance = await self.wallet_repo.add_balance(
                    seller_id, seller_net)
                tx_no = await self.wallet_repo.next_tx_no()
                await self.wallet_repo.save_transaction({
                    "txNo": tx_no,
                    "userId": seller_id,
                    "type": "c2c_settle",
                    "direction": "IN",
                    "amount": seller_net,
                    "balanceAfter": new_balance,
                    "payChannel": "wallet",
                    "orderId": escrow.get("listingNo", ""),
                    "depositNo": "",
                    "withdrawNo": "",
                    "status": "success",
                    "description": f"C2C 老酒托管结算 ¥{seller_net:.2f}"
                                    f"(货款 {escrow['amount']:.2f} - "
                                    f"佣金 {escrow['commission']:.2f})",
                    "createdAt": ts(),
                })
            await self.repo.update_c2c_escrow(escrow_no, {
                "status": C2C_ESCROW_SETTLED,
                "settleTxNo": tx_no,
                "autoSettled": auto,
                "settledAt": ts(),
            })
            logger.info("c2c_escrow_settled escrowNo=%s listing=%s "
                        "seller=%s net=%.2f commission=%.2f auto=%s",
                        escrow_no, listing.get("listingNo"), seller_id,
                        seller_net, escrow["commission"], auto)

            # 竹信分 C7 C2C 守约成交 +2(best-effort, 买卖双方各记一次)
            try:
                from services.role_service import (
                    RoleService, BEHAVIOR_C2C_TRADE_SETTLED,
                )
                role_svc = RoleService()
                lno = listing.get("listingNo")
                await role_svc.publish_member_credit_event(
                    int(escrow.get("sellerId") or 0),
                    BEHAVIOR_C2C_TRADE_SETTLED,
                    "recycle", ref_id=f"c2c:{lno}:seller")
                await role_svc.publish_member_credit_event(
                    int(escrow.get("buyerId") or 0),
                    BEHAVIOR_C2C_TRADE_SETTLED,
                    "recycle", ref_id=f"c2c:{lno}:buyer")
            except Exception:
                pass

        listing.update({
            "status": C2C_STATUS_SETTLED,
            "settledAt": ts(),
            "autoSettled": auto,
        })
        await self.repo.clear_c2c_active_life(listing.get("lifeCode", ""))

    async def release_lock(self, listing_no: str, buyer_id: int) -> dict:
        """买家撤销锁定(仅 escrow 托管: 退款+回架; offline 请等超时回架)

        Raises:
            KeyError: 挂单不存在
            ValueError: 非买家操作 / 状态不可撤 / 非托管通道
        """
        async with get_lock(f"recycle:c2c:listing:{listing_no}"):
            listing = await self.repo.get_c2c_by_no(listing_no)
            if listing is None:
                raise KeyError(f"挂单不存在(listingNo={listing_no})")
            if listing.get("buyerId") != buyer_id:
                raise ValueError("仅锁定买家可撤销购买")
            if listing.get("status") != C2C_STATUS_LOCKED:
                raise ValueError(
                    f"当前状态({listing.get('status')})不可撤销, "
                    f"仅锁定中可撤(卖家确认后请走确认收货)")
            if listing.get("payChannel") != C2C_PAY_ESCROW:
                raise ValueError(
                    "线下收款挂单不支持主动撤销(锁定 15 分钟超时自动回架)")

            await self._escrow_refund(listing, "买家撤销购买")
            listing["status"] = C2C_STATUS_ON_SALE
            listing["buyerId"] = None
            listing["buyerName"] = ""
            listing["lockedAt"] = None
            listing["releasedAt"] = ts()
            await self.repo.update_c2c_listing(listing["id"], listing)
        return listing

    async def confirm_receipt(self, listing_no: str, buyer_id: int) -> dict:
        """买家确认收货(仅 escrow: delivered → settled, 货款结算+平台抽佣)

        Raises:
            KeyError: 挂单不存在
            ValueError: 非买家操作 / 状态不可确认(未过户/已结算)
        """
        async with get_lock(f"recycle:c2c:listing:{listing_no}"):
            listing = await self.repo.get_c2c_by_no(listing_no)
            if listing is None:
                raise KeyError(f"挂单不存在(listingNo={listing_no})")
            if listing.get("buyerId") != buyer_id:
                raise ValueError("仅购买买家可确认收货")
            if listing.get("status") != C2C_STATUS_DELIVERED:
                raise ValueError(
                    f"当前状态({listing.get('status')})不可确认收货, "
                    f"须卖家确认过户后(delivered)")

            await self._settle_escrow(listing, auto=False)
            await self.repo.update_c2c_listing(listing["id"], listing)
        return listing

    async def get_escrow(self, escrow_no: str) -> dict:
        """托管单详情

        Raises:
            KeyError: 托管单不存在
        """
        escrow = await self.repo.get_c2c_escrow(escrow_no)
        if escrow is None:
            raise KeyError(f"托管单不存在(escrowNo={escrow_no})")
        return escrow

    async def list_escrows(self, user_id: int, status: str = None,
                           limit: int = 50) -> list[dict]:
        """托管单列表(买家/卖家视角)"""
        return await self.repo.list_c2c_escrows(
            user_id=user_id, status=status, limit=limit)

    # ============================================================
    # 5. Phase3 行情撮合(估值行情聚合 + 定价建议 + 买卖撮合推荐)
    # ============================================================

    @staticmethod
    def _median(values: list[float]) -> float:
        """中位数(空列表返回 0)"""
        if not values:
            return 0.0
        vs = sorted(values)
        mid = len(vs) // 2
        if len(vs) % 2:
            return vs[mid]
        return (vs[mid - 1] + vs[mid]) / 2

    @staticmethod
    def _age_band(wine_age: int) -> str:
        """酒龄档(行情分组口径: 3-5/6-10/11+)"""
        if wine_age <= 5:
            return "3-5"
        if wine_age <= 10:
            return "6-10"
        return "11+"

    async def _recent_deals(self, days: int) -> list[dict]:
        """近 N 天成交单(settled/sold, 以成交时间过滤)"""
        cutoff = (datetime.utcnow()
                  - timedelta(days=days)).isoformat()
        deals = await self.repo.list_c2c_listings(limit=500)
        result = []
        for d in deals:
            if d.get("status") not in (C2C_STATUS_SETTLED,
                                       C2C_STATUS_SOLD):
                continue
            closed = d.get("settledAt") or d.get("soldAt") or ""
            if closed >= cutoff:
                result.append(d)
        return result

    async def market_stats(self, days: int = 30) -> dict:
        """C2C 市场行情看板(公开)

        口径: 溢价率 = 成交价 / 锚定估值(>1 行情高于模型估值)
        """
        on_sales = await self.repo.list_c2c_listings(
            status=C2C_STATUS_ON_SALE, limit=200)
        on_sales = [await self._lazy_advance(l) for l in on_sales]
        on_sales = [l for l in on_sales
                    if l.get("status") == C2C_STATUS_ON_SALE]
        deals = await self._recent_deals(days)
        prices = [float(d.get("price") or 0) for d in deals
                  if d.get("price")]
        premiums = [float(d["price"]) / float(d.get("anchorValue") or 0)
                    for d in deals
                    if d.get("price") and d.get("anchorValue")]

        # 在售要价分布(按酒龄档)
        band_ask = {}
        for l in on_sales:
            band = self._age_band(int(l.get("wineAge") or 0))
            band_ask.setdefault(band, []).append(l.get("askPrice", 0))

        return {
            "days": days,
            "onSaleCount": len(on_sales),
            "onSaleAsk": {
                "min": min((l["askPrice"] for l in on_sales), default=0),
                "max": max((l["askPrice"] for l in on_sales), default=0),
                "byAgeBand": {b: len(v) for b, v in band_ask.items()},
            },
            "deals": {
                "count": len(deals),
                "avgPrice": round(sum(prices) / len(prices), 2)
                if prices else 0.0,
                "minPrice": min(prices) if prices else 0.0,
                "maxPrice": max(prices) if prices else 0.0,
                "medianPremium": round(self._median(premiums), 3)
                if premiums else None,
            },
        }

    async def price_suggestion(self, life_code: str,
                               condition_grade: str = GRADE_A) -> dict:
        """定价建议(挂单前决策辅助, 公开)

        三档建议(均 clamp 在锚定 0.5~1.5 倍合法挂单区间):
            quick    快速出手: 平衡档 × 0.85
            balanced 行情平衡: 锚定 × 同档近 90 天成交溢价率中位数
                              (无行情数据回退 1.0)
            premium  惜售高位: 平衡档 × 1.25

        Raises:
            KeyError: 生命码不存在
            ValueError: 未激活/品质分级非法/原价缺失(无法锚定)
        """
        if condition_grade not in GRADE_COEFFICIENTS:
            raise ValueError(
                f"品质分级非法({condition_grade}), 应为 A/B/C/D")
        life = await self.trace_repo.get_life_by_code(life_code)
        if life is None:
            raise KeyError(f"生命码不存在(lifeCode={life_code})")
        if not life.get("firstActivationDate"):
            raise ValueError("生命码未激活(老酒交易须以激活日起算酒龄)")

        wine_age = self._wine_age(life.get("firstActivationDate"))
        purchase_price = float(life.get("purchasePrice") or 0)
        if purchase_price <= 0:
            raise ValueError("生命码缺少购买原价, 无法估值锚定")
        anchor = self._anchor_value(purchase_price, wine_age,
                                    condition_grade)
        low = round(anchor * C2C_ASK_PRICE_BAND[0], 2)
        high = round(anchor * C2C_ASK_PRICE_BAND[1], 2)

        def _clamp(price: float) -> float:
            return round(min(max(price, low), high), 2)

        # 同酒龄档近 90 天行情溢价
        band = self._age_band(wine_age)
        deals = await self._recent_deals(90)
        band_deals = [d for d in deals
                      if self._age_band(int(d.get("wineAge") or 0)) == band
                      and d.get("price") and d.get("anchorValue")]
        market_rate = self._median(
            [float(d["price"]) / float(d["anchorValue"])
             for d in band_deals]) if band_deals else 1.0
        balanced = _clamp(anchor * market_rate)
        quick = _clamp(balanced * 0.85)
        premium = _clamp(balanced * 1.25)

        return {
            "lifeCode": life_code,
            "wineAge": wine_age,
            "ageBand": band,
            "conditionGrade": condition_grade,
            "anchorValue": anchor,
            "legalBand": {"min": low, "max": high},
            "suggestions": [
                {"tier": "quick", "label": "快速出手",
                 "price": quick,
                 "note": "低于行情参考约 15%, 适合尽快变现"},
                {"tier": "balanced", "label": "行情平衡",
                 "price": balanced,
                 "note": ("贴合同档近 90 天成交溢价率 "
                          f"{market_rate:.2f}({len(band_deals)} 笔)"
                          if band_deals else "暂无同档行情, 取锚定估值")},
                {"tier": "premium", "label": "惜售高位",
                 "price": premium,
                 "note": "高于行情参考约 25%, 适合不急出售"},
            ],
            "marketReference": {
                "band": band,
                "dealCount90d": len(band_deals),
                "avgPrice90d": round(sum(
                    float(d["price"]) for d in band_deals)
                    / len(band_deals), 2) if band_deals else None,
                "medianPremium90d": round(market_rate, 3)
                if band_deals else None,
            },
        }

    async def recommendations(self, budget: float = None,
                              max_price: float = None,
                              wine_age_min: int = None,
                              limit: int = 10) -> list[dict]:
        """买家撮合推荐(公开): 在售单按性价比(锚定/要价)降序

        Args:
            budget: 买家预算(上限, 过滤 askPrice ≤ budget)
            max_price: 同 budget(别名, 兼容)
            wine_age_min: 最低酒龄过滤
        """
        cap = budget if budget is not None else max_price
        listings = await self.repo.list_c2c_listings(
            status=C2C_STATUS_ON_SALE, limit=200)
        listings = [await self._lazy_advance(l) for l in listings]
        items = []
        for l in listings:
            if l.get("status") != C2C_STATUS_ON_SALE:
                continue
            if cap is not None and l.get("askPrice", 0) > float(cap):
                continue
            if wine_age_min is not None \
                    and int(l.get("wineAge") or 0) < int(wine_age_min):
                continue
            ask = float(l.get("askPrice") or 0)
            anchor = float(l.get("anchorValue") or 0)
            score = round(anchor / ask, 3) if ask > 0 else 0.0
            items.append({
                "listingNo": l.get("listingNo"),
                "lifeCode": l.get("lifeCode"),
                "productName": l.get("productName"),
                "sellerId": l.get("sellerId"),
                "wineAge": l.get("wineAge"),
                "conditionGrade": l.get("conditionGrade"),
                "askPrice": l.get("askPrice"),
                "anchorValue": l.get("anchorValue"),
                "payChannel": l.get("payChannel"),
                "valueScore": score,
                "discountRate": round(ask / anchor - 1, 3)
                if anchor > 0 else None,
                "expiresAt": l.get("expiresAt"),
            })
        items.sort(key=lambda x: x["valueScore"], reverse=True)
        return items[:limit]
