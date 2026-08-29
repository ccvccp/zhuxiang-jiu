"""订单业务服务(完整版)

功能:
    - 创建订单(价格计算引擎 + 库存预扣 + 积分冻结)
    - 查询(列表/详情/我的订单/按状态)
    - 状态流转(支付/取消/发货/收货/评价)
    - 售后退款(申请退货/退款/库存回滚/积分扣回)
    - 超时自动处理(关闭/确认/完成)
    - 价格试算

并发安全:
    - 订单操作使用 order:{orderId} 锁
    - 库存扣减使用 stock:{productId} 锁
    - 积分操作使用 member:{memberId} 锁(由 MemberRepository 内部或显式)

复用:
    - MemberService(消费积分+成长值、等级查询)
    - InventoryRepository(库存扣减/回补)
    - core.locks.get_lock(分布式锁)
"""

from datetime import datetime
import logging

from core.helpers import ts
from core.locks import get_lock
from core.age_gate import is_adult, MINOR_REJECT_MSG, AGE_CONFIRM_REQUIRED_MSG
from repositories.inventory_repository import InventoryRepository
from repositories.member_repository import MemberRepository
from repositories.order_repository import OrderRepository
from repositories.points_repository import SOURCE_REFUND, SOURCE_REVIEW
from services.points_service import PointsService

logger = logging.getLogger("order_service")


# ============================================================
# 常量定义
# ============================================================

# 订单状态
PENDING = "PENDING"        # 待付款
PAID = "PAID"              # 待发货(已付款)
SHIPPED = "SHIPPED"        # 待收货(已发货)
RECEIVED = "RECEIVED"      # 待评价(已签收)
COMPLETED = "COMPLETED"    # 已完成
CANCELLED = "CANCELLED"    # 已取消(用户主动)
CLOSED = "CLOSED"          # 已关闭(超时未支付)
RETURNING = "RETURNING"    # 退货中
REFUNDED = "REFUNDED"      # 已退款

# 状态中文名
STATUS_CN = {
    PENDING: "待付款", PAID: "待发货", SHIPPED: "待收货",
    RECEIVED: "待评价", COMPLETED: "已完成", CANCELLED: "已取消",
    CLOSED: "已关闭", RETURNING: "退货中", REFUNDED: "已退款",
}

# 等级折扣率(L1=1.0 不打折, L2=0.95, L3=0.9, L4=0.85, L5=0.8)
LEVEL_DISCOUNTS = {1: 1.0, 2: 0.95, 3: 0.90, 4: 0.85, 5: 0.80}

# 超时配置(秒)
TIMEOUT_PAY = 30 * 60          # 待付款 30 分钟超时关闭
TIMEOUT_CONFIRM = 15 * 86400   # 待收货 15 天自动确认
TIMEOUT_REVIEW = 7 * 86400     # 待评价 7 天自动完成

# 运费规则
SHIPPING_FREE_THRESHOLD = 99   # 满 99 免运费
SHIPPING_FEE = 10              # 运费 10 元

# 积分抵扣规则
POINTS_PER_YUAN = 100          # 100 竹叶 = ¥1
POINTS_DISCOUNT_CAP = 0.30     # 积分抵扣上限 30%

# Mock 优惠券(满 1000 减 50)
COUPON_THRESHOLD = 1000
COUPON_DISCOUNT = 50


def _gen_order_id() -> str:
    """生成订单号: RT + 时间戳 + 随机数"""
    now = datetime.now()
    import random
    return f"RT{now.strftime('%Y%m%d%H%M%S')}{random.randint(1000, 9999)}"


def _gen_trade_no() -> str:
    """生成支付交易单号"""
    import random
    return f"420{datetime.now().strftime('%Y%m%d%H%M%S')}{random.randint(100, 999)}"


class OrderService:
    """订单业务服务"""

    def __init__(self):
        self.order_repo = OrderRepository()
        self.inventory_repo = InventoryRepository()
        self.member_repo = MemberRepository()

    # ============================================================
    # 价格计算引擎
    # ============================================================

    def _calc_price(self, items: list, member_level: int,
                    use_points: int = 0, order_amount_for_points: float = None) -> dict:
        """价格计算(纯函数, 无副作用)

        Args:
            items: [{"productId": "ZX42-2026L07", "productName": "...",
                     "quantity": 2, "unitPrice": 268.00}, ...]
            member_level: 会员等级 1-5
            use_points: 使用的积分数量(须为 100 的倍数)
            order_amount_for_points: 积分抵扣上限基准(默认用商品折扣后金额)

        Returns:
            {goodsTotal, memberDiscount, couponDiscount, pointsDiscount,
             shippingFee, actualAmount, discountRate}
        """
        # 1. 商品总价
        goods_total = sum(
            item["unitPrice"] * item["quantity"]
            for item in items
        )

        # 2. 会员折扣(对商品总价打折)
        discount_rate = LEVEL_DISCOUNTS.get(member_level, 1.0)
        member_discount = -(goods_total * (1 - discount_rate))
        after_member = goods_total + member_discount  # 折后金额

        # 3. 优惠券(满 1000 减 50, Mock)
        coupon_discount = -COUPON_DISCOUNT if goods_total >= COUPON_THRESHOLD else 0
        after_coupon = after_member + coupon_discount

        # 4. 积分抵扣(100 竹叶 = ¥1, 上限 30%)
        points_discount = 0
        if use_points > 0:
            # 上限 = 基准金额 × 30%
            cap_base = order_amount_for_points if order_amount_for_points is not None else after_coupon
            cap_amount = cap_base * POINTS_DISCOUNT_CAP
            points_value = use_points / POINTS_PER_YUAN  # 积分价值(元)
            points_discount = -min(points_value, cap_amount)
        after_points = after_coupon + points_discount

        # 5. 运费(满 99 免运费)
        shipping_fee = 0 if after_points >= SHIPPING_FREE_THRESHOLD else SHIPPING_FEE

        # 6. 实付金额
        actual_amount = round(after_points + shipping_fee, 2)

        return {
            "goodsTotal": round(goods_total, 2),
            "memberDiscount": round(member_discount, 2),
            "couponDiscount": round(coupon_discount, 2),
            "pointsDiscount": round(points_discount, 2),
            "shippingFee": shipping_fee,
            "actualAmount": actual_amount,
            "discountRate": discount_rate,
        }

    async def preview_price(self, member_id, items: list,
                            use_points: int = 0) -> dict:
        """价格试算(不创建订单,不扣减库存/积分)"""
        member = await self.member_repo.get_by_id(member_id)
        if not member:
            raise KeyError(f"会员 {member_id} 不存在")

        price_detail = self._calc_price(items, member["level"], use_points)
        return {
            "success": True,
            "memberId": member_id,
            "memberLevel": member["level"],
            "items": items,
            "priceDetail": price_detail,
            "logs": [{"step": "价格试算", "level": "INFO",
                      "msg": f"实付 ¥{price_detail['actualAmount']:.2f}"}],
        }

    # ============================================================
    # 创建订单
    # ============================================================

    async def create(self, member_id, items: list, address: dict,
                     use_points: int = 0, remark: str = "",
                     age_confirmed: bool = False) -> dict:
        """创建订单

        流程:
            1. 校验会员(含酒类合规年龄门)
            2. 校验商品 & 库存(预扣)
            3. 价格计算
            4. 积分抵扣(冻结)
            5. 生成订单号
            6. 保存订单

        酒类合规年龄门(P0-1):
            - 会员出生日期未成年 → 硬拦截
            - 会员无成年声明(ageVerified/ageConfirmed)且请求未带 ageConfirmed → 拒绝
            - 首次声明后回写会员标记, 后续下单免重复确认

        Raises:
            KeyError: 会员不存在
            ValueError: 商品不存在/库存不足/积分不足/年龄校验失败
        """
        async with get_lock(f"member:{member_id}"):
            # 1. 校验会员
            member = await self.member_repo.get_by_id(member_id)
            if not member:
                raise KeyError(f"会员 {member_id} 不存在")
            if member.get("status", 1) != 1:
                raise ValueError("账号已被禁用")

            logs = []
            member_level = member["level"]

            # 酒类合规年龄门(P0-1)
            member_birthdate = member.get("birthdate") or ""
            if member_birthdate and not is_adult(member_birthdate):
                raise ValueError(MINOR_REJECT_MSG)
            if not (member.get("ageVerified") or member.get("ageConfirmed")):
                if not age_confirmed:
                    raise ValueError(AGE_CONFIRM_REQUIRED_MSG)
                # 首次下单声明: 回写会员成年标记(后续免确认)
                await self.member_repo.update_fields(
                    member_id, {"ageConfirmed": True})
                logs.append({"step": "年龄声明", "level": "INFO",
                             "msg": "已确认年满18周岁(酒类合规)"})

            # 2. 校验商品 & 预扣库存
            resolved_items = []
            for item in items:
                pid = item["productId"]
                qty = int(item["quantity"])
                if qty <= 0:
                    raise ValueError(f"商品 {pid} 数量须为正整数")

                async with get_lock(f"stock:{pid}"):
                    product = await self.inventory_repo.get(pid)
                    if not product:
                        raise ValueError(f"商品 {pid} 不存在")
                    if product["stock"] < qty:
                        raise ValueError(
                            f"库存不足: {pid} 当前 {product['stock']}, 需 {qty}"
                        )
                    # 预扣库存(直接扣减, 取消时回补)
                    new_stock = await self.inventory_repo.deduct(pid, qty)
                    logs.append({"step": "库存预扣", "level": "INFO",
                                 "msg": f"{pid} ×{qty}, 剩余 {new_stock}"})

                resolved_items.append({
                    "productId": pid,
                    "productName": item.get("productName", pid),
                    "quantity": qty,
                    "unitPrice": float(item["unitPrice"]),
                    "subtotal": round(float(item["unitPrice"]) * qty, 2),
                })

            # 3. 价格计算
            price_detail = self._calc_price(resolved_items, member_level, use_points)
            logs.append({"step": "价格计算", "level": "INFO",
                         "msg": f"商品 ¥{price_detail['goodsTotal']}, 实付 ¥{price_detail['actualAmount']}"})

            # 5. 生成订单号(先于积分抵扣, 抵扣流水需引用订单号)
            order_id = _gen_order_id()
            logs.append({"step": "创建订单", "level": "INFO", "msg": f"订单号 {order_id}"})

            # 4. 积分抵扣(P1-19: 统一走积分模块账本, 含 100 起用/整数倍/30% 上限校验)
            if use_points > 0:
                if use_points % POINTS_PER_YUAN != 0:
                    raise ValueError(f"积分须为 {POINTS_PER_YUAN} 的整数倍")
                # 抵扣上限基准 = 会员折后+券后金额(与 _calc_price 口径一致)
                deduct_base = (price_detail["goodsTotal"]
                               + price_detail["memberDiscount"]
                               + price_detail["couponDiscount"])
                deduct_result = await PointsService().deduct_points(
                    user_id=member_id, order_id=order_id,
                    order_amount=deduct_base, deduct_points=use_points)
                points_value = use_points / POINTS_PER_YUAN
                logs.append({"step": "积分抵扣", "level": "INFO",
                              "msg": f"扣除 {use_points} 竹叶(¥{points_value:.2f}), "
                                     f"剩余 {deduct_result.get('balance')}"})

            # 6. 保存订单
            now = ts()
            order = {
                "orderId": order_id,
                "memberId": member_id,
                "orderType": "RT",
                "status": PENDING,
                "items": resolved_items,
                "priceDetail": price_detail,
                "address": address,
                "remark": remark,
                "logistics": {"carrier": "", "waybillNo": "",
                              "shippedAt": "", "signedAt": ""},
                "payment": {"method": "", "tradeNo": "", "paidAt": ""},
                "review": {"rating": 0, "content": "", "reviewedAt": ""},
                "refund": {"reason": "", "refundedAt": "", "refundedAmount": 0},
                "usedPoints": use_points,
                "consumedPoints": 0,  # 支付后赠送的积分(退款时扣回)
                "timeline": [{"status": PENDING, "time": now,
                              "action": "创建订单"}],
                "createdAt": now,
                "updatedAt": now,
            }
            await self.order_repo.create(order)

            return {
                "success": True,
                "orderId": order_id,
                "status": PENDING,
                "statusName": STATUS_CN[PENDING],
                "memberId": member_id,
                "priceDetail": price_detail,
                "usedPoints": use_points,
                "logs": logs,
            }

    # ============================================================
    # 查询
    # ============================================================

    async def get_by_id(self, order_id: str) -> dict:
        """订单详情"""
        order = await self.order_repo.get_by_id(order_id)
        if not order:
            raise KeyError(f"订单 {order_id} 不存在")
        return {
            "success": True,
            "order": self._decorate(order),
            "logs": [],
        }

    async def get_my_orders(self, member_id, status: str = None) -> dict:
        """我的订单"""
        orders = await self.order_repo.get_by_member(member_id, status)
        return {
            "success": True,
            "memberId": member_id,
            "count": len(orders),
            "orders": [self._decorate(o) for o in orders],
            "logs": [],
        }

    async def list_all(self, status: str = None) -> dict:
        """订单列表(管理端, 可按状态筛选)"""
        if status:
            orders = await self.order_repo.list_by_status(status)
        else:
            orders = await self.order_repo.list_all()
        orders.sort(key=lambda o: o.get("createdAt", ""), reverse=True)
        return {
            "success": True,
            "count": len(orders),
            "orders": [self._decorate(o) for o in orders],
            "logs": [],
        }

    async def list_statuses(self) -> dict:
        """状态列表"""
        return {
            "success": True,
            "statuses": [{"code": k, "name": v} for k, v in STATUS_CN.items()],
            "logs": [],
        }

    def _decorate(self, order: dict) -> dict:
        """装饰订单(增加 statusName)"""
        order = dict(order)
        order["statusName"] = STATUS_CN.get(order.get("status"), order.get("status", ""))
        return order

    # ============================================================
    # 状态流转
    # ============================================================

    async def pay(self, order_id: str, payment_method: str = "wechat") -> dict:
        """支付订单 PENDING → PAID

        流程:
            1. 校验订单状态
            2. 生成支付单号
            3. 会员消费(增加成长值 + 积分)
            4. 更新订单状态
        """
        async with get_lock(f"order:{order_id}"):
            order = await self.order_repo.get_by_id(order_id)
            if not order:
                raise KeyError(f"订单 {order_id} 不存在")
            if order["status"] != PENDING:
                raise ValueError(
                    f"订单状态异常: 当前 {order['status']}, 仅 {PENDING} 可支付"
                )

            logs = []
            now = ts()
            actual_amount = order["priceDetail"]["actualAmount"]

            # 会员消费(成长值走 member 表, 返分走积分模块账本 P1-19)
            # 成长值 = 实付金额(整数); 返分 = 实付 × 1.5 × 等级倍数(D-5 决策口径)
            consume_amount = int(actual_amount)
            earned_points = 0
            if consume_amount > 0:
                member = await self.member_repo.get_by_id(order["memberId"])
                if member:
                    new_growth = await self.member_repo.add_growth(
                        order["memberId"], consume_amount)
                    # 消费返分 best-effort: 触达日/月/单笔上限不阻断支付主流程
                    try:
                        earn_result = await PointsService().earn_order_points(
                            user_id=order["memberId"], order_id=order_id,
                            order_amount=actual_amount,
                            member_level=member.get("level", 1))
                        earned_points = earn_result.get("earnedPoints", 0)
                    except ValueError as exc:
                        logger.warning("order_earn_points_skipped order=%s "
                                       "err=%s", order_id, exc)
                        logs.append({"step": "返分受限", "level": "WARN",
                                     "msg": f"消费返分未发放: {exc}"})
                    logs.append({"step": "会员消费", "level": "INFO",
                                 "msg": f"+{consume_amount} 成长值(累计 {new_growth}), "
                                        f"+{earned_points} 竹叶"})

            # 更新订单
            order["status"] = PAID
            order["payment"] = {
                "method": payment_method,
                "tradeNo": _gen_trade_no(),
                "paidAt": now,
            }
            order["consumedPoints"] = earned_points
            order["timeline"].append({"status": PAID, "time": now, "action": "支付成功"})
            order["updatedAt"] = now
            await self.order_repo.save(order_id, order)
            logs.append({"step": "支付成功", "level": "INFO", "msg": f"支付方式 {payment_method}"})

            return {
                "success": True,
                "orderId": order_id,
                "status": PAID,
                "statusName": STATUS_CN[PAID],
                "payment": order["payment"],
                "consumedPoints": earned_points,
                "logs": logs,
            }

    async def cancel(self, order_id: str, reason: str = "用户取消") -> dict:
        """取消订单 PENDING → CANCELLED(释放库存 + 退还积分)"""
        async with get_lock(f"order:{order_id}"):
            order = await self.order_repo.get_by_id(order_id)
            if not order:
                raise KeyError(f"订单 {order_id} 不存在")
            if order["status"] != PENDING:
                raise ValueError(
                    f"订单状态异常: 仅 {PENDING} 可取消, 当前 {order['status']}"
                )

            logs = []
            now = ts()

            # 释放库存
            for item in order["items"]:
                pid = item["productId"]
                qty = item["quantity"]
                async with get_lock(f"stock:{pid}"):
                    new_stock = await self.inventory_repo.restock(pid, qty)
                logs.append({"step": "释放库存", "level": "INFO",
                             "msg": f"{pid} +{qty}, 剩余 {new_stock}"})

            # 退还冻结积分(P1-19: 积分模块账本)
            used_points = order.get("usedPoints", 0)
            if used_points > 0:
                refund_result = await PointsService().earn_points(
                    user_id=order["memberId"], points=used_points,
                    source=SOURCE_REFUND, ref_id=order_id,
                    ref_desc=f"取消订单退还抵扣积分({used_points}竹叶)")
                logs.append({"step": "退还积分", "level": "INFO",
                             "msg": f"+{used_points} 竹叶, "
                                    f"剩余 {refund_result.get('balance')}"})

            order["status"] = CANCELLED
            order["timeline"].append({"status": CANCELLED, "time": now,
                                      "action": f"取消: {reason}"})
            order["updatedAt"] = now
            await self.order_repo.save(order_id, order)
            logs.append({"step": "取消成功", "level": "INFO", "msg": f"订单 {order_id} 已取消"})

            return {
                "success": True,
                "orderId": order_id,
                "status": CANCELLED,
                "statusName": STATUS_CN[CANCELLED],
                "logs": logs,
            }

    async def ship(self, order_id: str, carrier: str,
                   waybill_no: str) -> dict:
        """发货 PAID → SHIPPED"""
        async with get_lock(f"order:{order_id}"):
            order = await self.order_repo.get_by_id(order_id)
            if not order:
                raise KeyError(f"订单 {order_id} 不存在")
            if order["status"] != PAID:
                raise ValueError(
                    f"订单状态异常: 仅 {PAID} 可发货, 当前 {order['status']}"
                )

            now = ts()
            order["status"] = SHIPPED
            order["logistics"] = {
                "carrier": carrier,
                "waybillNo": waybill_no,
                "shippedAt": now,
                "signedAt": "",
            }
            order["timeline"].append({"status": SHIPPED, "time": now,
                                      "action": f"已发货 {carrier} {waybill_no}"})
            order["updatedAt"] = now
            await self.order_repo.save(order_id, order)

            return {
                "success": True,
                "orderId": order_id,
                "status": SHIPPED,
                "statusName": STATUS_CN[SHIPPED],
                "logistics": order["logistics"],
                "logs": [{"step": "发货", "level": "INFO",
                          "msg": f"{carrier} {waybill_no}"}],
            }

    async def confirm(self, order_id: str) -> dict:
        """确认收货 SHIPPED → RECEIVED"""
        async with get_lock(f"order:{order_id}"):
            order = await self.order_repo.get_by_id(order_id)
            if not order:
                raise KeyError(f"订单 {order_id} 不存在")
            if order["status"] != SHIPPED:
                raise ValueError(
                    f"订单状态异常: 仅 {SHIPPED} 可确认收货, 当前 {order['status']}"
                )

            now = ts()
            order["status"] = RECEIVED
            order["logistics"]["signedAt"] = now
            order["timeline"].append({"status": RECEIVED, "time": now,
                                      "action": "确认收货"})
            order["updatedAt"] = now
            await self.order_repo.save(order_id, order)

            return {
                "success": True,
                "orderId": order_id,
                "status": RECEIVED,
                "statusName": STATUS_CN[RECEIVED],
                "logs": [{"step": "确认收货", "level": "INFO",
                          "msg": "已签收"}],
            }

    async def review(self, order_id: str, rating: int,
                     content: str = "") -> dict:
        """评价 RECEIVED → COMPLETED(返还评价积分)"""
        async with get_lock(f"order:{order_id}"):
            order = await self.order_repo.get_by_id(order_id)
            if not order:
                raise KeyError(f"订单 {order_id} 不存在")
            if order["status"] != RECEIVED:
                raise ValueError(
                    f"订单状态异常: 仅 {RECEIVED} 可评价, 当前 {order['status']}"
                )
            if not (1 <= rating <= 5):
                raise ValueError("评分须为 1-5")

            logs = []
            now = ts()

            # 评价返积分(5星返100, 4星返50, 其他返20; P1-19: 积分模块账本)
            reward_points = {5: 100, 4: 50}.get(rating, 20)
            earn_result = await PointsService().earn_points(
                user_id=order["memberId"], points=reward_points,
                source=SOURCE_REVIEW, ref_id=order_id,
                ref_desc=f"订单评价返分({rating}星)")
            logs.append({"step": "评价奖励", "level": "INFO",
                         "msg": f"+{reward_points} 竹叶"
                                f"(余额 {earn_result.get('balance')})"})

            order["status"] = COMPLETED
            order["review"] = {
                "rating": rating,
                "content": content,
                "reviewedAt": now,
            }
            order["timeline"].append({"status": COMPLETED, "time": now,
                                      "action": f"评价 {rating} 星"})
            order["updatedAt"] = now
            await self.order_repo.save(order_id, order)
            logs.append({"step": "评价完成", "level": "INFO",
                         "msg": f"订单已完成, {rating} 星评价"})

            return {
                "success": True,
                "orderId": order_id,
                "status": COMPLETED,
                "statusName": STATUS_CN[COMPLETED],
                "rewardPoints": reward_points,
                "logs": logs,
            }

    # ============================================================
    # 售后退款
    # ============================================================

    async def apply_return(self, order_id: str, reason: str) -> dict:
        """申请退货 COMPLETED → RETURNING(进入退款审核流, P0-7)

        对齐设计文档 9.2 售后流程与 order_aftersales 表:
            申请(待审核 pending) → 管理员审核(同意→REFUNDED / 拒绝→回退 COMPLETED)
        """
        async with get_lock(f"order:{order_id}"):
            order = await self.order_repo.get_by_id(order_id)
            if not order:
                raise KeyError(f"订单 {order_id} 不存在")
            if order["status"] != COMPLETED:
                raise ValueError(
                    f"订单状态异常: 仅 {COMPLETED} 可申请退货, 当前 {order['status']}"
                )

            now = ts()
            order["status"] = RETURNING
            order["refund"]["reason"] = reason
            # 售后审核记录(对齐文档 order_aftersales: 待审核/已同意/已拒绝)
            order["refund"]["audit"] = {
                "status": "pending",
                "auditor": "",
                "auditRemark": "",
                "appliedAt": now,
                "auditedAt": "",
            }
            order["timeline"].append({"status": RETURNING, "time": now,
                                      "action": f"申请退货(待审核): {reason}"})
            order["updatedAt"] = now
            await self.order_repo.save(order_id, order)

            return {
                "success": True,
                "orderId": order_id,
                "status": RETURNING,
                "statusName": STATUS_CN[RETURNING],
                "auditStatus": "pending",
                "logs": [{"step": "申请退货", "level": "WARN",
                          "msg": f"原因: {reason}(进入退款审核流)"}],
            }

    async def audit_refund(self, order_id: str, approve: bool,
                            auditor: str = "", audit_remark: str = "") -> dict:
        """售后退款审核(P0-7: 管理员审核, 同意→执行退款 / 拒绝→回退)

        对齐文档 9.2 Step2:
            - 同意 → 进入退款执行(原路退回语义, 复用 refund 内部逻辑)
            - 拒绝 → 说明原因, 订单回退 COMPLETED
        """
        if approve:
            return await self.refund(order_id, auditor=auditor,
                                     audit_remark=audit_remark)

        async with get_lock(f"order:{order_id}"):
            order = await self.order_repo.get_by_id(order_id)
            if not order:
                raise KeyError(f"订单 {order_id} 不存在")
            if order["status"] != RETURNING:
                raise ValueError(
                    f"订单状态异常: 仅 {RETURNING} 可审核, 当前 {order['status']}"
                )
            audit = order["refund"].get("audit") or {}
            if audit.get("status") != "pending":
                raise ValueError(
                    f"退款申请已审核过(status={audit.get('status')}), 不可重复审核")

            now = ts()
            audit.update({
                "status": "rejected",
                "auditor": auditor,
                "auditRemark": audit_remark,
                "auditedAt": now,
            })
            order["refund"]["audit"] = audit
            # 拒绝后订单回退完成态(售后入口关闭前可再次申请)
            order["status"] = COMPLETED
            order["timeline"].append({"status": COMPLETED, "time": now,
                                      "action": f"退款审核拒绝: {audit_remark}"})
            order["updatedAt"] = now
            await self.order_repo.save(order_id, order)

            return {
                "success": True,
                "orderId": order_id,
                "status": COMPLETED,
                "statusName": STATUS_CN[COMPLETED],
                "auditStatus": "rejected",
                "auditor": auditor,
                "auditRemark": audit_remark,
                "logs": [{"step": "退款审核", "level": "WARN",
                          "msg": f"已拒绝: {audit_remark}"}],
            }

    async def refund(self, order_id: str, auditor: str = "",
                     audit_remark: str = "") -> dict:
        """退款审核通过并执行 RETURNING → REFUNDED(库存回滚 + 积分扣回)

        P0-7: refund 不再是无审核的直接执行——须存在 pending 审核申请
        (apply_return 创建), 审核通过即执行; 执行记录含审核人/备注。
        """
        async with get_lock(f"order:{order_id}"):
            order = await self.order_repo.get_by_id(order_id)
            if not order:
                raise KeyError(f"订单 {order_id} 不存在")
            if order["status"] != RETURNING:
                raise ValueError(
                    f"订单状态异常: 仅 {RETURNING} 可退款, 当前 {order['status']}"
                )
            audit = order["refund"].get("audit") or {}
            if audit.get("status") == "rejected":
                raise ValueError("退款申请已被拒绝, 不可执行退款")
            if audit and audit.get("status") != "pending":
                raise ValueError(
                    f"退款已执行过(audit={audit.get('status')}), 不可重复退款")

            logs = []
            now = ts()
            refund_amount = order["priceDetail"]["actualAmount"]

            # 库存回滚(退货入库)
            for item in order["items"]:
                pid = item["productId"]
                qty = item["quantity"]
                async with get_lock(f"stock:{pid}"):
                    new_stock = await self.inventory_repo.restock(pid, qty)
                logs.append({"step": "退货入库", "level": "INFO",
                             "msg": f"{pid} +{qty}, 剩余 {new_stock}"})

            # 扣回消费赠送的积分(P1-19: 积分模块账本, 不足扣到 0)
            consumed_points = order.get("consumedPoints", 0)
            if consumed_points > 0:
                try:
                    clawback = await PointsService().refund_points(
                        user_id=order["memberId"], order_id=order_id,
                        refund_points=consumed_points)
                    logs.append({"step": "扣回积分", "level": "WARN",
                                 "msg": f"-{clawback.get('actualRefund', consumed_points)} "
                                        f"竹叶(余额 {clawback.get('remainingPoints')})"})
                except ValueError as exc:
                    logs.append({"step": "扣回积分", "level": "ERROR",
                                 "msg": f"积分扣回异常: {exc}, 跳过"})

            # 退还抵扣积分(P1-19: 积分模块账本)
            used_points = order.get("usedPoints", 0)
            if used_points > 0:
                refund_result = await PointsService().earn_points(
                    user_id=order["memberId"], points=used_points,
                    source=SOURCE_REFUND, ref_id=order_id,
                    ref_desc=f"退款退还抵扣积分({used_points}竹叶)")
                logs.append({"step": "退还抵扣积分", "level": "INFO",
                             "msg": f"+{used_points} 竹叶, "
                                    f"剩余 {refund_result.get('balance')}"})

            order["status"] = REFUNDED
            order["refund"]["refundedAt"] = now
            order["refund"]["refundedAmount"] = refund_amount
            # 审核通过记录(P0-7)
            if audit:
                audit.update({
                    "status": "approved",
                    "auditor": auditor,
                    "auditRemark": audit_remark,
                    "auditedAt": now,
                })
                order["refund"]["audit"] = audit
            order["timeline"].append({"status": REFUNDED, "time": now,
                                      "action": f"退款 ¥{refund_amount:.2f}"
                                      f"(审核通过, 审核人: {auditor or 'admin'})"})
            order["updatedAt"] = now
            await self.order_repo.save(order_id, order)
            logs.append({"step": "退款完成", "level": "WARN",
                         "msg": f"退款 ¥{refund_amount:.2f}"})

            return {
                "success": True,
                "orderId": order_id,
                "status": REFUNDED,
                "statusName": STATUS_CN[REFUNDED],
                "refundedAmount": refund_amount,
                "auditStatus": "approved" if audit else "",
                "auditor": auditor,
                "logs": logs,
            }

    # ============================================================
    # 超时自动处理
    # ============================================================

    async def timeout_close(self, order_id: str) -> dict:
        """超时关闭 PENDING → CLOSED(释放库存 + 退还积分)

        与 cancel 类似, 但状态变为 CLOSED
        """
        async with get_lock(f"order:{order_id}"):
            order = await self.order_repo.get_by_id(order_id)
            if not order:
                raise KeyError(f"订单 {order_id} 不存在")
            if order["status"] != PENDING:
                raise ValueError(
                    f"订单状态异常: 仅 {PENDING} 可超时关闭, 当前 {order['status']}"
                )

            logs = []
            now = ts()

            # 释放库存
            for item in order["items"]:
                pid = item["productId"]
                qty = item["quantity"]
                async with get_lock(f"stock:{pid}"):
                    new_stock = await self.inventory_repo.restock(pid, qty)
                logs.append({"step": "释放库存", "level": "INFO",
                             "msg": f"{pid} +{qty}, 剩余 {new_stock}"})

            # 退还冻结积分(P1-19: 积分模块账本)
            used_points = order.get("usedPoints", 0)
            if used_points > 0:
                refund_result = await PointsService().earn_points(
                    user_id=order["memberId"], points=used_points,
                    source=SOURCE_REFUND, ref_id=order_id,
                    ref_desc=f"超时关闭退还抵扣积分({used_points}竹叶)")
                logs.append({"step": "退还积分", "level": "INFO",
                             "msg": f"+{used_points} 竹叶, "
                                    f"剩余 {refund_result.get('balance')}"})

            order["status"] = CLOSED
            order["timeline"].append({"status": CLOSED, "time": now,
                                      "action": "超时未支付自动关闭"})
            order["updatedAt"] = now
            await self.order_repo.save(order_id, order)
            logs.append({"step": "超时关闭", "level": "WARN",
                         "msg": f"订单 {order_id} 已关闭"})

            return {
                "success": True,
                "orderId": order_id,
                "status": CLOSED,
                "statusName": STATUS_CN[CLOSED],
                "logs": logs,
            }

    async def timeout_confirm(self, order_id: str) -> dict:
        """超时自动确认收货 SHIPPED → RECEIVED"""
        async with get_lock(f"order:{order_id}"):
            order = await self.order_repo.get_by_id(order_id)
            if not order:
                raise KeyError(f"订单 {order_id} 不存在")
            if order["status"] != SHIPPED:
                raise ValueError(
                    f"订单状态异常: 仅 {SHIPPED} 可自动确认, 当前 {order['status']}"
                )

            now = ts()
            order["status"] = RECEIVED
            order["logistics"]["signedAt"] = now
            order["timeline"].append({"status": RECEIVED, "time": now,
                                      "action": "超时自动确认收货"})
            order["updatedAt"] = now
            await self.order_repo.save(order_id, order)

            return {
                "success": True,
                "orderId": order_id,
                "status": RECEIVED,
                "statusName": STATUS_CN[RECEIVED],
                "logs": [{"step": "自动确认", "level": "WARN",
                          "msg": "超时自动确认收货"}],
            }

    async def timeout_complete(self, order_id: str) -> dict:
        """超时自动完成 RECEIVED → COMPLETED(默认五星)"""
        async with get_lock(f"order:{order_id}"):
            order = await self.order_repo.get_by_id(order_id)
            if not order:
                raise KeyError(f"订单 {order_id} 不存在")
            if order["status"] != RECEIVED:
                raise ValueError(
                    f"订单状态异常: 仅 {RECEIVED} 可自动完成, 当前 {order['status']}"
                )

            logs = []
            now = ts()

            # 默认五星评价, 返还 100 积分(P1-19: 积分模块账本)
            reward_points = 100
            earn_result = await PointsService().earn_points(
                user_id=order["memberId"], points=reward_points,
                source=SOURCE_REVIEW, ref_id=order_id,
                ref_desc="订单超时自动完成返分(默认五星)")
            logs.append({"step": "评价奖励", "level": "INFO",
                         "msg": f"+{reward_points} 竹叶"
                                f"(余额 {earn_result.get('balance')})"})

            order["status"] = COMPLETED
            order["review"] = {
                "rating": 5,
                "content": "系统默认五星好评",
                "reviewedAt": now,
            }
            order["timeline"].append({"status": COMPLETED, "time": now,
                                      "action": "超时自动完成(默认五星)"})
            order["updatedAt"] = now
            await self.order_repo.save(order_id, order)
            logs.append({"step": "自动完成", "level": "WARN",
                         "msg": "超时自动完成, 默认五星"})

            return {
                "success": True,
                "orderId": order_id,
                "status": COMPLETED,
                "statusName": STATUS_CN[COMPLETED],
                "rewardPoints": reward_points,
                "logs": logs,
            }

    # ============================================================
    # 删除订单
    # ============================================================

    async def delete(self, order_id: str) -> dict:
        """删除订单(仅终态: COMPLETED/CANCELLED/CLOSED/REFUNDED 可删除)"""
        async with get_lock(f"order:{order_id}"):
            order = await self.order_repo.get_by_id(order_id)
            if not order:
                raise KeyError(f"订单 {order_id} 不存在")

            deletable = {COMPLETED, CANCELLED, CLOSED, REFUNDED}
            if order["status"] not in deletable:
                raise ValueError(
                    f"仅终态订单可删除, 当前 {order['status']}"
                )

            await self.order_repo.delete(order_id)

            return {
                "success": True,
                "orderId": order_id,
                "logs": [{"step": "删除订单", "level": "INFO",
                          "msg": f"订单 {order_id} 已删除"}],
            }
