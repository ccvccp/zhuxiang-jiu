"""订单结算业务:9 阶段事务(P4.4 对齐前端 checkout-service.js mock 契约)

事务阶段(对齐前端):
    preflight: 购物车校验 + 价格计算(只读) —— 空车/价格失败直接中止
    阶段2 开启事务
    阶段3 订单创建 + 发货方路由(region 已认领→该代理商, 否则厂家直供)
    阶段4 库存扣减(先全量校验再执行, 锁内)
    阶段5 优惠券核销(无效/已用抛错回滚, preflight 不拦截以覆盖失败场景)
    阶段6 积分扣减(不足抛错回滚, skip if 0)
    阶段7 积分入账(等级加成 L3=1.02/L4=1.05/L5=1.08)
    阶段8 分润计算(平台80%/酒店20%) + 厂家→代理商 5% 同品服务费计提
    阶段9 支付确认(订单状态→已付款)
    阶段10 提交事务(攒批记录统一落库)

并发: 锁 order:next + coupon:{code} + points:{memberLevel} 升序获取;
失败逆序补偿(库存恢复/券恢复/积分恢复)。

响应契约(对齐前端):
    成功: {success, orderNo, details:{originalTotal, totalDiscount, shipping,
        finalAmount, pointsUsed, pointsEarned, paymentMethod, couponCode,
        region, shipperType, shipperAgentName, manufacturerServiceFee},
        logs, asyncOps} + 旧兼容字段 orderId/status/message

存储域(supply_chain_repository, 注意 checkout_points 不能叫
points_accounts —— 与积分模块惰性初始化探针键冲突):
    checkout_coupons / checkout_points / checkout_orders /
    profit_records / service_fees
"""

import logging
from typing import ClassVar

from repositories.inventory_repository import InventoryRepository
from repositories.order_repository import OrderRepository
from repositories.supply_chain_repository import SupplyChainRepository
from services.shipping_service import ShippingClaimService
from services.tx_utils import (
    StageError, TxLog, acquire_locks, gen_no, now_iso, result_abort,
    result_failure, result_success,
)

logger = logging.getLogger(__name__)


class CheckoutConfig:
    """结算配置(对齐前端 checkout CONFIG + order-pricing DISCOUNT_CONFIG)"""

    POINTS_RATE = 0.01            # 100 竹叶 = 1 元
    POINTS_DEDUCT_MAX = 0.30      # 积分抵扣上限 30%
    EARN_RATE = 0.1               # 每消费 10 元 = 1 竹叶
    LEVEL_BOOST: ClassVar[dict] = {"L1": 1.0, "L2": 1.0, "L3": 1.02, "L4": 1.05, "L5": 1.08}
    MEMBER_DISCOUNT: ClassVar[dict] = {"L1": 1.00, "L2": 0.95, "L3": 0.92, "L4": 0.90, "L5": 0.85}
    FULL_REDUCTION: ClassVar[list] = [(500, 30), (1000, 80), (3000, 300)]   # 满 threshold 减 reduction
    PROFIT_SPLIT: ClassVar[dict] = {"platform": 0.80, "hotel": 0.20}
    SHIPPING_FREE_QTY = 2         # 两瓶免运费
    SHIPPING_BASE = 15


def _round2(v: float) -> float:
    return round(v * 100) / 100


def calculate_order_price(main_price: float, total_qty: int,
                          member_level: str, points: int,
                          coupon_value: float) -> dict:
    """P3 零售多维叠加价格计算(对齐 js/order-pricing.js calculateOrderPrice)

    顺序: 会员折扣 → 满减 → 优惠券 → 积分抵扣(≤原价30%) → 运费
    """
    original_total = _round2(main_price * total_qty)
    # 3a 会员折扣
    member_rate = CheckoutConfig.MEMBER_DISCOUNT.get(member_level, 1.00)
    member_discount = _round2(original_total * (1 - member_rate))
    discounted = _round2(original_total - member_discount)
    # 3b 满减(取满足门槛的最大减免)
    reduction = 0
    for threshold, amount in CheckoutConfig.FULL_REDUCTION:
        if discounted >= threshold:
            reduction = max(reduction, amount)
    discounted = _round2(discounted - reduction)
    # 3c 优惠券
    coupon_applied = _round2(min(coupon_value, discounted))
    discounted = _round2(discounted - coupon_applied)
    # 3d 积分抵扣(上限 = 原价 × 30%)
    points_value = _round2(points * CheckoutConfig.POINTS_RATE)
    max_deduct = _round2(original_total * CheckoutConfig.POINTS_DEDUCT_MAX)
    points_applied = _round2(min(points_value, max_deduct, discounted))
    discounted = _round2(discounted - points_applied)
    # 运费 + 实付
    shipping = 0 if total_qty >= CheckoutConfig.SHIPPING_FREE_QTY else CheckoutConfig.SHIPPING_BASE
    final_amount = _round2(discounted + shipping)
    return {
        "originalTotal": original_total,
        "totalDiscount": _round2(original_total - discounted),
        "discountDetail": {
            "type": "P3多维叠加", "memberDiscount": member_discount,
            "reductionAmount": reduction, "couponApplied": coupon_applied,
            "pointsApplied": points_applied,
        },
        "shipping": shipping,
        "finalAmount": final_amount,
    }


class CheckoutService:
    def __init__(self, order_repo: OrderRepository = OrderRepository(),
                 inventory_repo: InventoryRepository = InventoryRepository(),
                 sc_repo: SupplyChainRepository = SupplyChainRepository(),
                 shipping_service: ShippingClaimService = ShippingClaimService()):
        self.order_repo = order_repo
        self.inventory_repo = inventory_repo
        self.sc_repo = sc_repo
        self.shipping = shipping_service

    async def submit(self, items: list, consignee=None, payment=None,
                     member_level: str = "L1", points: int = 0,
                     coupon_code: str | None = None,
                     payment_method: str = "wechat",
                     region: str | None = None) -> dict:
        """订单结算提交(9 阶段事务, 对齐前端契约)"""
        log = TxLog()

        # ---- preflight(锁外只读) ----
        log.info("阶段1-购物车校验", f"开始校验购物车: {len(items)} 项")
        if not items:
            log.error("阶段1-购物车校验", "购物车为空,中止流程")
            return result_abort("购物车为空", log)
        main_item = items[0]
        total_qty = sum(int(i.get("qty") or i.get("quantity") or 0) for i in items)
        main_price = float(main_item.get("price") or 0)
        # 优惠券折扣识别(无效券不拦截, 留给阶段5抛错回滚)
        coupon_value = 0.0
        if coupon_code:
            coupon = await self.sc_repo.hget("checkout_coupons", coupon_code)
            if coupon and coupon.get("status") == "未使用":
                coupon_value = _round2(main_price * total_qty * coupon["discount"])
                log.info("阶段1-优惠券校验", f"优惠券折扣已识别: {coupon_code}")
            else:
                log.warn("阶段1-优惠券校验", f"优惠券未找到或已使用, 阶段5将抛错: {coupon_code}")
        price = calculate_order_price(main_price, total_qty, member_level,
                                      points, coupon_value)
        log.info("阶段1-价格计算",
                 f"原价 {price['originalTotal']} / 折扣 {price['totalDiscount']}"
                 f" / 运费 {price['shipping']} / 实付 {price['finalAmount']}")

        # ---- 多锁升序获取(order/coupon/points) ----
        lock_keys = ["order:next"]
        if coupon_code:
            lock_keys.append(f"coupon:{coupon_code}")
        if points and points > 0:
            lock_keys.append(f"points:{member_level or 'default'}")
        async with acquire_locks(lock_keys):
            return await self._submit_locked(
                items, consignee, payment, member_level, points,
                coupon_code, coupon_value, payment_method, region,
                price, total_qty, log)

    async def _submit_locked(self, items, consignee, payment, member_level,
                             points, coupon_code, coupon_value, payment_method,
                             region, price, total_qty, log) -> dict:
        """锁内执行 9 阶段(失败逆序补偿)"""
        log.info("阶段2-开启事务", "订单结算事务已开启")
        # 补偿栈: (描述, 恢复协程)
        rollbacks: list[tuple[str, object]] = []
        order_no = "ZX" + gen_no("")
        order_record: dict | None = None
        profit_record: dict | None = None
        fee_record: dict | None = None
        shipper_type, shipper_agent_name = "manufacturer", "厂家直供"
        service_fee = 0.0
        points_used, points_earned = 0, 0
        try:
            # ---- 阶段3: 订单创建 + 发货方路由 ----
            shipper = await self.shipping.resolve_shipper(region)
            shipper_type = shipper["shipper"]
            shipper_agent_name = shipper["agentName"]
            log.info("阶段3-订单创建",
                     f"订单 {order_no} 发货方: {shipper_type}({shipper_agent_name})")
            order_record = {
                "order_no": order_no,
                "member_level": member_level,
                "items": [{"id": i.get("id") or i.get("productId"),
                           "name": i.get("name"), "price": i.get("price"),
                           "qty": int(i.get("qty") or i.get("quantity") or 0)}
                          for i in items],
                "original_total": price["originalTotal"],
                "total_discount": price["totalDiscount"],
                "discount_detail": price["discountDetail"],
                "shipping": price["shipping"],
                "final_amount": price["finalAmount"],
                "coupon_code": coupon_code,
                "points_used": 0, "points_earned": 0,
                "payment_method": payment_method,
                "ship_region": region,
                "shipper_type": shipper_type,
                "shipper_agent_id": shipper.get("agentId"),
                "shipper_agent_name": shipper_agent_name,
                "consignee": consignee, "payment": payment,
                "status": "待付款", "created_at": now_iso(),
            }
            log.enter("阶段3-订单创建")

            # ---- 阶段4: 库存扣减(先全量校验再执行) ----
            for item in items:
                pid = item.get("id") or item.get("productId")
                product = await self.inventory_repo.get(pid)
                qty = int(item.get("qty") or item.get("quantity") or 0)
                if not product:
                    raise StageError("阶段4-库存扣减", f"商品不存在: id={pid}")
                if product["stock"] < qty:
                    raise StageError(
                        "阶段4-库存扣减",
                        f"库存不足: {item.get('name') or pid} 需要{qty}现有{product['stock']}")
            for item in items:
                pid = item.get("id") or item.get("productId")
                qty = int(item.get("qty") or item.get("quantity") or 0)
                before = await self.inventory_repo.get_stock(pid)
                await self.inventory_repo.deduct(pid, qty)
                rollbacks.append((
                    f"恢复库存 {pid}",
                    self.inventory_repo.set_stock(pid, before)))
            log.info("阶段4-库存扣减", f"库存已扣减 {len(items)} 行")
            log.enter("阶段4-库存扣减")

            # ---- 阶段5: 优惠券核销 ----
            if coupon_code:
                coupon = await self.sc_repo.hget("checkout_coupons", coupon_code)
                if not coupon or coupon.get("status") != "未使用":
                    raise StageError("阶段5-优惠券核销",
                                     f"优惠券无效或已使用: {coupon_code}")
                coupon["status"] = "已使用"
                await self.sc_repo.hset("checkout_coupons", coupon_code, coupon)
                rollbacks.append((
                    f"恢复优惠券 {coupon_code}",
                    self.sc_repo.hset("checkout_coupons", coupon_code,
                                      {**coupon, "status": "未使用"})))
                log.info("阶段5-优惠券核销", f"优惠券已核销: {coupon_code}")
            log.enter("阶段5-优惠券核销")

            # ---- 阶段6: 积分扣减 ----
            if points and points > 0:
                balance = await self.sc_repo.hget_int(
                    "checkout_points", member_level, 0)
                if balance < points:
                    raise StageError(
                        "阶段6-积分扣减", f"积分不足: 需要{points}现有{balance}")
                await self.sc_repo.hset(
                    "checkout_points", member_level, balance - points)
                points_used = points
                rollbacks.append((
                    f"恢复积分 {member_level}",
                    self.sc_repo.hset("checkout_points", member_level, balance)))
                log.info("阶段6-积分扣减", f"已扣减 {points}(剩余 {balance - points})")
            log.enter("阶段6-积分扣减")

            # ---- 阶段7: 积分入账(等级加成) ----
            base_earn = int((price["finalAmount"]
                             - points_used * CheckoutConfig.POINTS_RATE)
                            * CheckoutConfig.EARN_RATE)
            boost = CheckoutConfig.LEVEL_BOOST.get(member_level, 1.0)
            points_earned = int(base_earn * boost)
            if points_earned > 0:
                balance = await self.sc_repo.hget_int(
                    "checkout_points", member_level, 0)
                await self.sc_repo.hset(
                    "checkout_points", member_level, balance + points_earned)
                rollbacks.append((
                    f"回退入账积分 {member_level}",
                    self.sc_repo.hset("checkout_points", member_level, balance)))
            if order_record is not None:
                order_record["points_used"] = points_used
                order_record["points_earned"] = points_earned
            log.info("阶段7-积分入账", f"入账 {points_earned}(加成 x{boost})")
            log.enter("阶段7-积分入账")

            # ---- 阶段8: 分润 + 服务费计提 ----
            final_amount = price["finalAmount"]
            platform_share = _round2(final_amount * CheckoutConfig.PROFIT_SPLIT["platform"])
            hotel_share = _round2(final_amount * CheckoutConfig.PROFIT_SPLIT["hotel"])
            if shipper_type == "agent":
                fee = await self.shipping.accrue_service_fee({
                    "agentId": shipper.get("agentId"),
                    "agentName": shipper_agent_name, "region": region,
                    "orderNo": order_no, "shippedQty": total_qty,
                    "orderAmount": final_amount,
                }, log)
                service_fee = fee["serviceFee"]
                fee_record = fee["record"]
            profit_record = {
                "order_no": order_no, "total_amount": final_amount,
                "platform_share": platform_share, "hotel_share": hotel_share,
                "agent_share": 0, "manufacturer_service_fee": service_fee,
                "shipper_type": shipper_type,
                "shipper_agent_name": shipper_agent_name,
                "created_at": now_iso(),
            }
            log.info("阶段8-分润计算",
                     f"平台 {platform_share} / 酒店 {hotel_share} / 服务费 {service_fee}")
            log.enter("阶段8-分润计算")

            # ---- 阶段9: 支付确认 ----
            if order_record is not None:
                order_record["status"] = "已付款"
                order_record["paid_at"] = now_iso()
            log.info("阶段9-支付确认", f"订单 {order_no} 状态→已付款")
            log.enter("阶段9-支付确认")

            # ---- 阶段10: 攒批统一落库 ----
            if order_record is not None:
                await self.sc_repo.append("checkout_orders", order_record)
            if profit_record is not None:
                await self.sc_repo.append("profit_records", profit_record)
            if fee_record is not None:
                await self.sc_repo.append("service_fees", fee_record)
            log.info("阶段10-提交事务", f"事务提交成功: {order_no}")
            # 成功路径: 关闭未使用的补偿协程(避免 coroutine 泄漏告警)
            for _, rollback in rollbacks:
                if hasattr(rollback, "close"):
                    rollback.close()

        except StageError as err:
            # 逆序补偿回滚
            log.error("回滚", f"事务已回滚: {err}")
            for _, rollback in reversed(rollbacks):
                try:
                    await rollback
                except Exception as exc:   # 补偿失败不阻断后续回滚
                    logger.warning("checkout_rollback_failed: %s", exc)
            return result_failure(err, log)

        return result_success({
            "orderNo": order_no,
            "details": {
                "originalTotal": price["originalTotal"],
                "totalDiscount": price["totalDiscount"],
                "shipping": price["shipping"],
                "finalAmount": price["finalAmount"],
                "pointsUsed": points_used,
                "pointsEarned": points_earned,
                "paymentMethod": payment_method,
                "couponCode": coupon_code,
                "region": region,
                "shipperType": shipper_type,
                "shipperAgentName": shipper_agent_name,
                "manufacturerServiceFee": service_fee,
            },
            # ---- 旧契约兼容字段 ----
            "orderId": order_no,
            "status": "已付款",
            "message": f"订单 {order_no} 提交成功",
        }, log, ["order_notify", "blockchain_notarize"])
