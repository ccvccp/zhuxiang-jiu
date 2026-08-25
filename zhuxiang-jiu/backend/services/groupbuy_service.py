"""团购模块业务逻辑层

核心业务:
    - 阶梯折扣计算(T1-T4 自动匹配)
    - 团购申请(资格校验 + 门槛校验 + 频次限制 + 年度限额)
    - 审核流程(通过/驳回 + 状态流转)
    - 订单状态流转(pending → approved → paying → in_production → shipped → completed)
    - 取消订单(仅活跃状态可取消)

锁保护:
    - 申请: lock:groupbuy:apply:{userId}  (防重复提交)
    - 状态流转: lock:groupbuy:order:{orderNo}  (防并发状态变更)
    - 审核: lock:groupbuy:audit:{orderNo}  (防重复审核)
    - 频次检查: lock:groupbuy:freq:{userId}  (月度频次计数)

异常约定:
    - KeyError → 404(资源不存在)
    - ValueError → 409(业务冲突: 状态非法/参数非法/重复申请等)
"""

import json
from datetime import datetime, timezone

from core.locks import get_lock
from core.helpers import ts
from repositories.groupbuy_repository import (
    GroupBuyRepository,
    # 订单状态
    ORDER_STATUS_PENDING, ORDER_STATUS_APPROVED, ORDER_STATUS_PAYING,
    ORDER_STATUS_IN_PRODUCTION, ORDER_STATUS_SHIPPED, ORDER_STATUS_COMPLETED,
    ORDER_STATUS_CANCELLED, ORDER_STATUS_REJECTED,
    ORDER_STATUS_NAMES, ORDER_STATUS_FLOW,
    ORDER_ACTIVE_STATUSES, ORDER_TERMINAL_STATUSES,
    # 审核结果
    AUDIT_RESULT_APPROVED, AUDIT_RESULT_REJECTED,
    AUDIT_LEVEL_STAFF, AUDIT_LEVEL_SUPERVISOR,
    AUDIT_LEVEL_DIRECTOR, AUDIT_LEVEL_GENERAL_MANAGER,
    # 阶梯
    TIER_T1, TIER_T2, TIER_T3, TIER_T4,
    TIER_DEFINITIONS, TIER_NAMES,
    # 团购类型
    GROUP_TYPE_ENTERPRISE, GROUP_TYPE_WEDDING, GROUP_TYPE_FESTIVAL, GROUP_TYPE_CUSTOM,
    GROUP_TYPE_NAMES, SUPPORTED_GROUP_TYPES,
    # 门槛
    MIN_AMOUNT, MIN_AMOUNT_WEDDING, MIN_AMOUNT_CUSTOM,
    MIN_QUANTITY, MAX_AMOUNT, ANNUAL_LIMIT, MONTHLY_FREQ_LIMIT,
    # 函数
    match_tier,
)
from repositories.product_repository import ProductRepository


class GroupBuyService:
    """团购业务逻辑(双模式存储, 锁保护 RMW)"""

    def __init__(self, repo: GroupBuyRepository = GroupBuyRepository()):
        self.repo = repo
        self.product_repo = ProductRepository()

    # ============================================================
    # 团购产品列表
    # ============================================================

    async def list_products(self) -> dict:
        """获取可团购产品列表(全系列竹奕酒产品)"""
        products = await self.product_repo.list_all()
        # 团购产品仅返回关键字段(避免泄露会员价等)
        result = []
        for p in products:
            images = p.get("images") or {}
            image = ""
            if isinstance(images, dict):
                image = images.get("main", "")
            elif isinstance(images, list) and images:
                image = images[0] if isinstance(images[0], str) else images[0].get("url", "")
            result.append({
                "productId": p.get("product_id"),
                "name": p.get("name"),
                "subtitle": p.get("subtitle", ""),
                "series": p.get("series", ""),
                "alcohol": p.get("alcohol"),
                "volume": p.get("volume", ""),
                "price": p.get("price"),
                "originalPrice": p.get("original_price"),
                "stock": p.get("stock", 0),
                "image": image,
            })
        return {"products": result, "count": len(result)}

    # ============================================================
    # 阶梯折扣查询
    # ============================================================

    async def get_tiers(self) -> dict:
        """获取阶梯折扣表"""
        tiers = []
        for tier, min_amt, max_amt, discount in TIER_DEFINITIONS:
            tiers.append({
                "tier": tier,
                "name": TIER_NAMES.get(tier, ""),
                "minAmount": min_amt,
                "maxAmount": max_amt if max_amt else None,
                "discount": discount,
                "discountRate": f"{int((1 - discount) * 100)}% off",
            })
        return {
            "tiers": tiers,
            "rules": {
                "minAmount": MIN_AMOUNT,
                "minAmountWedding": MIN_AMOUNT_WEDDING,
                "minAmountCustom": MIN_AMOUNT_CUSTOM,
                "minQuantity": MIN_QUANTITY,
                "maxAmount": MAX_AMOUNT,
                "annualLimit": ANNUAL_LIMIT,
                "monthlyFreqLimit": MONTHLY_FREQ_LIMIT,
            },
        }

    # ============================================================
    # 团购价计算
    # ============================================================

    async def calculate_price(self, items: list[dict]) -> dict:
        """计算团购价(输入产品+数量, 输出阶梯/折扣/团购价/节省金额)

        Args:
            items: [{"productId": "ZX42-2026L07", "quantity": 200}, ...]

        Returns:
            {
                "originalTotal": 53600.0,
                "tier": "T1",
                "discount": 0.80,
                "groupPrice": 42880.0,
                "savedAmount": 10720.0,
                "items": [...],  # 含产品详情
                "meetsThreshold": True,
                "suggestions": [...]  # 凑单建议(未达门槛时)
            }
        """
        if not items:
            raise ValueError("产品列表不能为空")

        # 查询产品信息并计算原价合计
        detail_items = []
        original_total = 0.0
        total_quantity = 0

        for item in items:
            product_id = item.get("productId")
            quantity = item.get("quantity", 0)
            if not product_id:
                raise ValueError("产品ID不能为空")
            if quantity <= 0:
                raise ValueError(f"产品 {product_id} 数量必须大于 0")

            product = await self.product_repo.get_by_id(product_id)
            if product is None:
                raise KeyError(f"产品不存在: {product_id}")

            original_price = float(product.get("price", 0))
            subtotal = original_price * quantity
            original_total += subtotal
            total_quantity += quantity

            detail_items.append({
                "productId": product_id,
                "productName": product.get("name", ""),
                "productSpec": f"{product.get('alcohol', '')}° {product.get('volume', '')}",
                "quantity": quantity,
                "originalPrice": original_price,
                "subtotal": subtotal,
            })

        # 匹配阶梯
        tier, discount = match_tier(original_total)
        meets_threshold = tier is not None

        if meets_threshold:
            group_price = round(original_total * discount, 2)
            saved_amount = round(original_total - group_price, 2)
            suggestions = []
        else:
            # 未达门槛, 提供凑单建议
            group_price = original_total
            saved_amount = 0.0
            suggestions = self._build_suggestions(original_total, total_quantity, detail_items)

        return {
            "originalTotal": round(original_total, 2),
            "tier": tier,
            "discount": discount,
            "groupPrice": round(group_price, 2),
            "savedAmount": round(saved_amount, 2),
            "totalQuantity": total_quantity,
            "items": detail_items,
            "meetsThreshold": meets_threshold,
            "suggestions": suggestions,
        }

    def _build_suggestions(self, original_total: float, total_quantity: int,
                            detail_items: list[dict]) -> list[dict]:
        """构建凑单建议(未达门槛时推荐加量到下一阶梯)"""
        suggestions = []
        for tier, min_amt, max_amt, discount in TIER_DEFINITIONS:
            if original_total < min_amt:
                diff = min_amt - original_total
                # 估算需要增加的瓶数(以最便宜产品为参考)
                cheapest_price = min(
                    (i["originalPrice"] for i in detail_items), default=268
                )
                extra_qty = max(1, int(diff / cheapest_price) + 1)
                suggestions.append({
                    "tier": tier,
                    "discount": discount,
                    "targetAmount": min_amt,
                    "diffAmount": round(diff, 2),
                    "estimatedExtraQuantity": extra_qty,
                    "estimatedSavingAfterUpgrade": round(min_amt * discount * (1 - discount), 2),
                })
        return suggestions[:2]  # 仅返回最近的 2 个阶梯建议

    # ============================================================
    # 团购申请
    # ============================================================

    async def apply(self, user_id: int, user_level: int, group_type: str,
                    items: list[dict], purpose: str = "",
                    custom_needs: dict = None, invoice_info: dict = None,
                    addresses: list = None) -> dict:
        """提交团购申请(含资格校验 + 门槛校验 + 频次限制)

        Args:
            user_id: 用户ID
            user_level: 会员等级(必须为 5 = SVIP)
            group_type: 团购类型 enterprise/wedding/festival/custom
            items: [{"productId": "ZX42-2026L07", "quantity": 200}, ...]
            purpose: 用途说明
            custom_needs: 定制需求(JSON)
            invoice_info: 发票信息(JSON)
            addresses: 收货地址(JSON 数组)

        Returns:
            团购订单详情(含 orderNo)

        Raises:
            ValueError: 资格不符/门槛不足/频次超限/年度限额超限
        """
        # 1. 资格校验: 仅 SVIP(L5) 可申请
        if user_level != 5:
            raise ValueError("团购为 SVIP 专属权益, 请先开通 SVIP 会员")

        # 2. 团购类型校验
        if group_type not in SUPPORTED_GROUP_TYPES:
            raise ValueError(f"团购类型非法: {group_type}")

        # 3. 防重复提交(同一用户有活跃订单时禁止再次申请)
        async with get_lock(f"groupbuy:apply:{user_id}"):
            active_orders = await self.repo.find_active_by_user(user_id)
            if active_orders:
                raise ValueError(
                    f"您有 {len(active_orders)} 笔未完结的团购订单, 请先完成或取消后再申请"
                )

            # 4. 计算团购价
            calc = await self.calculate_price(items)
            if not calc["meetsThreshold"]:
                # 婚宴团购门槛略低
                min_amount = MIN_AMOUNT_WEDDING if group_type == GROUP_TYPE_WEDDING else (
                    MIN_AMOUNT_CUSTOM if group_type == GROUP_TYPE_CUSTOM else MIN_AMOUNT
                )
                if calc["originalTotal"] < min_amount:
                    raise ValueError(
                        f"团购金额未达门槛: 当前 ¥{calc['originalTotal']:.2f}, "
                        f"需 ≥ ¥{min_amount:.2f}"
                    )

            # 5. 门槛校验
            self._validate_threshold(group_type, calc["originalTotal"], calc["totalQuantity"])

            # 6. 频次限制(月度 ≤ 4 次)
            await self._check_frequency(user_id)

            # 7. 年度限额校验
            await self._check_annual_limit(user_id, calc["groupPrice"])

            # 8. 生成订单
            order_no = await self.repo.next_order_no()
            now = ts()
            order = {
                "orderNo": order_no,
                "userId": user_id,
                "userLevel": user_level,
                "groupType": group_type,
                "purpose": purpose,
                "originalTotal": calc["originalTotal"],
                "tier": calc["tier"],
                "discount": calc["discount"],
                "groupPrice": calc["groupPrice"],
                "savedAmount": calc["savedAmount"],
                "status": ORDER_STATUS_PENDING,
                "customNeeds": json.dumps(custom_needs, ensure_ascii=False) if custom_needs else None,
                "invoiceInfo": json.dumps(invoice_info, ensure_ascii=False) if invoice_info else None,
                "addresses": json.dumps(addresses, ensure_ascii=False) if addresses else None,
                "applyTime": now,
                "auditTime": None,
                "auditUser": None,
                "payTime": None,
                "shipTime": None,
                "completeTime": None,
                "createdAt": now,
                "updatedAt": now,
            }
            await self.repo.save_order(order)

            # 9. 保存明细
            await self.repo.save_items(order_no, calc["items"])

            # 10. 返回完整订单(含明细)
            order["items"] = calc["items"]
            return order

    def _validate_threshold(self, group_type: str, original_total: float,
                              total_quantity: int) -> None:
        """门槛校验"""
        # 婚宴团购门槛略低
        if group_type == GROUP_TYPE_WEDDING:
            if original_total < MIN_AMOUNT_WEDDING:
                raise ValueError(
                    f"婚宴团购金额未达门槛: 当前 ¥{original_total:.2f}, 需 ≥ ¥{MIN_AMOUNT_WEDDING:.2f}"
                )
        elif group_type == GROUP_TYPE_CUSTOM:
            if original_total < MIN_AMOUNT_CUSTOM:
                raise ValueError(
                    f"定制团购金额未达门槛: 当前 ¥{original_total:.2f}, 需 ≥ ¥{MIN_AMOUNT_CUSTOM:.2f}"
                )
        else:
            if original_total < MIN_AMOUNT:
                raise ValueError(
                    f"团购金额未达门槛: 当前 ¥{original_total:.2f}, 需 ≥ ¥{MIN_AMOUNT:.2f}"
                )

        # 数量校验
        if total_quantity < MIN_QUANTITY:
            raise ValueError(
                f"团购数量未达门槛: 当前 {total_quantity} 瓶, 需 ≥ {MIN_QUANTITY} 瓶"
            )

        # 单次上限
        if original_total > MAX_AMOUNT:
            raise ValueError(
                f"团购金额超出单次上限: 当前 ¥{original_total:.2f}, 上限 ¥{MAX_AMOUNT:.2f}"
            )

    async def _check_frequency(self, user_id: int) -> None:
        """频次限制检查(月度 ≤ 4 次)"""
        now = datetime.now(timezone.utc)
        start_date = now.strftime("%Y-%m-01")
        # 月末
        if now.month == 12:
            end_date = f"{now.year}-12-31"
        else:
            import calendar
            last_day = calendar.monthrange(now.year, now.month)[1]
            end_date = f"{now.year}-{now.month:02d}-{last_day}"

        count = await self.repo.count_user_orders_in_period(user_id, start_date, end_date)
        if count >= MONTHLY_FREQ_LIMIT:
            raise ValueError(
                f"本月团购申请已达上限({MONTHLY_FREQ_LIMIT} 次/月), 请下月再试"
            )

    async def _check_annual_limit(self, user_id: int, new_amount: float) -> None:
        """年度限额校验"""
        year = datetime.now(timezone.utc).year
        used = await self.repo.sum_user_annual_amount(user_id, year)
        if used + new_amount > ANNUAL_LIMIT:
            raise ValueError(
                f"年度团购限额将超限: 已用 ¥{used:.2f} + 本次 ¥{new_amount:.2f} > 上限 ¥{ANNUAL_LIMIT:.2f}"
            )

    # ============================================================
    # 订单查询
    # ============================================================

    async def get_order_detail(self, order_no: str) -> dict:
        """查询团购订单详情(含明细 + 审核流水)"""
        order = await self.repo.get_order(order_no)
        if order is None:
            raise KeyError(f"团购订单不存在: {order_no}")

        items = await self.repo.get_items(order_no)
        audits = await self.repo.list_audits(order_no)

        # 反序列化 JSON 字段
        result = dict(order)
        for field in ("customNeeds", "invoiceInfo", "addresses"):
            if result.get(field) and isinstance(result[field], str):
                try:
                    result[field] = json.loads(result[field])
                except (json.JSONDecodeError, TypeError):
                    pass
        result["items"] = items
        result["audits"] = audits
        result["statusName"] = ORDER_STATUS_NAMES.get(order["status"], "")
        return result

    async def list_orders(self, user_id: int, status: str = None,
                          limit: int = 50) -> dict:
        """查询用户团购订单列表"""
        orders = await self.repo.list_orders(user_id=user_id, status=status, limit=limit)
        # 补充状态名称
        for o in orders:
            o["statusName"] = ORDER_STATUS_NAMES.get(o.get("status", ""), "")
        return {
            "orders": orders,
            "count": len(orders),
        }

    # ============================================================
    # 审核流程
    # ============================================================

    async def audit_order(self, order_no: str, auditor: str,
                           audit_result: str, audit_remark: str = "",
                           audit_level: str = AUDIT_LEVEL_STAFF) -> dict:
        """审核团购申请(待审核 → 审核通过/已驳回)

        Args:
            order_no: 团购订单号
            auditor: 审核人
            audit_result: 审核结果 approved/rejected
            audit_remark: 审核备注
            audit_level: 审批层级 staff/supervisor/director/general_manager

        Returns:
            审核后的订单详情

        Raises:
            KeyError: 订单不存在
            ValueError: 状态非法(非待审核)/审核结果非法
        """
        if audit_result not in (AUDIT_RESULT_APPROVED, AUDIT_RESULT_REJECTED):
            raise ValueError(f"审核结果非法: {audit_result}")

        async with get_lock(f"groupbuy:audit:{order_no}"):
            order = await self.repo.get_order(order_no)
            if order is None:
                raise KeyError(f"团购订单不存在: {order_no}")

            if order["status"] != ORDER_STATUS_PENDING:
                raise ValueError(
                    f"订单状态非法, 当前 {ORDER_STATUS_NAMES.get(order['status'], '')}, 仅待审核订单可审核"
                )

            # 确定新状态
            new_status = ORDER_STATUS_APPROVED if audit_result == AUDIT_RESULT_APPROVED else ORDER_STATUS_REJECTED

            # 记录审核流水
            now = ts()
            audit = {
                "orderNo": order_no,
                "auditLevel": audit_level,
                "auditor": auditor,
                "auditResult": audit_result,
                "auditRemark": audit_remark,
                "auditTime": now,
                "createdAt": now,
            }
            await self.repo.add_audit(audit)

            # 更新订单状态
            order["status"] = new_status
            order["auditTime"] = now
            order["auditUser"] = auditor
            order["updatedAt"] = now
            await self.repo.save_order(order)

            return await self.get_order_detail(order_no)

    # ============================================================
    # 订单状态流转
    # ============================================================

    async def update_status(self, order_no: str, new_status: str,
                             operator: str = "") -> dict:
        """更新订单状态(含状态机校验)

        Args:
            order_no: 团购订单号
            new_status: 新状态
            operator: 操作人

        Returns:
            更新后的订单详情

        Raises:
            KeyError: 订单不存在
            ValueError: 状态流转非法
        """
        async with get_lock(f"groupbuy:order:{order_no}"):
            order = await self.repo.get_order(order_no)
            if order is None:
                raise KeyError(f"团购订单不存在: {order_no}")

            current = order["status"]
            self._validate_status_transition(current, new_status)

            # 更新时间字段
            now = ts()
            if new_status == ORDER_STATUS_PAYING:
                order["payTime"] = now
            elif new_status == ORDER_STATUS_SHIPPED:
                order["shipTime"] = now
            elif new_status == ORDER_STATUS_COMPLETED:
                order["completeTime"] = now

            order["status"] = new_status
            order["updatedAt"] = now
            await self.repo.save_order(order)

            return await self.get_order_detail(order_no)

    def _validate_status_transition(self, current: str, new_status: str) -> None:
        """状态机校验"""
        if current not in ORDER_STATUS_FLOW:
            raise ValueError(f"未知状态: {current}")

        allowed = ORDER_STATUS_FLOW[current]
        if new_status not in allowed:
            if not allowed:
                raise ValueError(
                    f"当前状态 {ORDER_STATUS_NAMES.get(current, current)} 为终态, 不可变更"
                )
            allowed_names = "、".join(
                ORDER_STATUS_NAMES.get(s, s) for s in allowed
            )
            raise ValueError(
                f"状态流转非法: {ORDER_STATUS_NAMES.get(current, current)} 不可直接流转到 "
                f"{ORDER_STATUS_NAMES.get(new_status, new_status)}, 允许: {allowed_names}"
            )

    # ============================================================
    # 取消订单
    # ============================================================

    async def cancel_order(self, order_no: str, user_id: int,
                            reason: str = "") -> dict:
        """取消团购申请(仅活跃状态可取消)

        Args:
            order_no: 团购订单号
            user_id: 取消人ID(校验是否为订单所有者)
            reason: 取消原因

        Returns:
            取消后的订单详情

        Raises:
            KeyError: 订单不存在
            ValueError: 状态非活跃/无权操作
        """
        async with get_lock(f"groupbuy:order:{order_no}"):
            order = await self.repo.get_order(order_no)
            if order is None:
                raise KeyError(f"团购订单不存在: {order_no}")

            # 权限校验: 仅订单所有者可取消
            if order["userId"] != user_id:
                raise ValueError("无权操作: 仅订单所有者可取消")

            # 状态校验: 仅活跃状态可取消
            if order["status"] not in ORDER_ACTIVE_STATUSES:
                raise ValueError(
                    f"订单状态非法: 当前 {ORDER_STATUS_NAMES.get(order['status'], '')}, "
                    f"仅待审核/审核通过/待付款订单可取消"
                )

            # 更新状态
            order["status"] = ORDER_STATUS_CANCELLED
            order["updatedAt"] = ts()
            await self.repo.save_order(order)

            return await self.get_order_detail(order_no)

    # ============================================================
    # 管理端查询
    # ============================================================

    async def list_pending_orders(self, limit: int = 50) -> dict:
        """待审核订单列表(管理端)"""
        orders = await self.repo.list_orders(status=ORDER_STATUS_PENDING, limit=limit)
        for o in orders:
            o["statusName"] = ORDER_STATUS_NAMES.get(o.get("status", ""), "")
        return {
            "orders": orders,
            "count": len(orders),
        }

    async def get_stats(self) -> dict:
        """团购统计(管理端)"""
        all_orders = await self.repo.list_orders(limit=10000)
        total = len(all_orders)
        pending = sum(1 for o in all_orders if o.get("status") == ORDER_STATUS_PENDING)
        completed = sum(1 for o in all_orders if o.get("status") == ORDER_STATUS_COMPLETED)
        cancelled = sum(1 for o in all_orders if o.get("status") == ORDER_STATUS_CANCELLED)
        total_amount = sum(
            float(o.get("groupPrice", 0))
            for o in all_orders
            if o.get("status") not in (ORDER_STATUS_CANCELLED, ORDER_STATUS_REJECTED)
        )
        total_saved = sum(
            float(o.get("savedAmount", 0))
            for o in all_orders
            if o.get("status") not in (ORDER_STATUS_CANCELLED, ORDER_STATUS_REJECTED)
        )
        return {
            "totalOrders": total,
            "pendingOrders": pending,
            "completedOrders": completed,
            "cancelledOrders": cancelled,
            "totalAmount": round(total_amount, 2),
            "totalSaved": round(total_saved, 2),
        }
