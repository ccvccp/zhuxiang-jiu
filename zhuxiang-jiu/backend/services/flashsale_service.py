"""限时秒杀模块业务逻辑层

核心规则:
    - 库存扣减/回补: flash:item:{itemId} 锁内原子判定(读-改-写全临界区)
    - 幂等: 同会员+同秒杀商品仅允许一张非取消订单(pending/paid)
    - 限购: 按 (pending+paid) 订单数量合计判定, cancelled 不计入
    - 防刷: 注册时长/会员等级/账号状态(参数管理端可调)

异常约定: KeyError→404(不存在) / ValueError→409(业务冲突)
"""

import logging
from datetime import datetime, timedelta, UTC

from core.locks import get_lock
from repositories.flashsale_repository import (
    FlashSaleRepository, parse_iso,
    SESSION_STATUS_DRAFT, SESSION_STATUS_PUBLISHED, SESSION_STATUS_CANCELLED,
    RUNTIME_NOT_STARTED, RUNTIME_IN_PROGRESS, RUNTIME_ENDED,
    ITEM_STATUS_ACTIVE, ORDER_STATUS_PENDING, ORDER_STATUS_PAID,
    ORDER_STATUS_CANCELLED,
)
from repositories.member_repository import MemberRepository
from repositories.product_repository import ProductRepository

logger = logging.getLogger(__name__)

RUNTIME_STATUS_NAMES = {
    SESSION_STATUS_DRAFT: "草稿",
    SESSION_STATUS_CANCELLED: "已取消",
    RUNTIME_NOT_STARTED: "未开始",
    RUNTIME_IN_PROGRESS: "进行中",
    RUNTIME_ENDED: "已结束",
}

# 参数白名单(管理端可修改的字段)
_SETTING_FIELDS = ("enabled", "minRegisterHours", "minMemberLevel",
                   "orderExpireMinutes", "maxQuantityPerOrder")


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


class FlashSaleService:
    """限时秒杀模块业务逻辑层"""

    def __init__(self, store: dict = None):
        self.flash_repo = FlashSaleRepository(store)
        self.member_repo = MemberRepository(store)
        self.product_repo = ProductRepository(store)

    # ============================================================
    # 运行时状态推导(不落库, 按当前时间计算)
    # ============================================================

    @staticmethod
    def _runtime_status(session: dict) -> str:
        status = session.get("status", "")
        if status in (SESSION_STATUS_DRAFT, SESSION_STATUS_CANCELLED):
            return status
        now = datetime.now(UTC)
        start = parse_iso(session["startTime"])
        end = parse_iso(session["endTime"])
        if now < start:
            return RUNTIME_NOT_STARTED
        if now > end:
            return RUNTIME_ENDED
        return RUNTIME_IN_PROGRESS

    def _session_view(self, session: dict) -> dict:
        """场次对外视图(附运行时状态/中文名/商品数)"""
        view = dict(session)
        runtime = self._runtime_status(session)
        view["runtimeStatus"] = runtime
        view["runtimeStatusName"] = RUNTIME_STATUS_NAMES.get(runtime, runtime)
        view["itemCount"] = session.get("itemOrder", 0)
        return view

    # ============================================================
    # 管理端: 场次管理
    # ============================================================

    async def create_session(self, name: str, start_time: str, end_time: str,
                             created_by: str = "admin") -> dict:
        """创建秒杀场次(草稿状态)

        Raises:
            ValueError: 名称/时间非法
        """
        name = (name or "").strip()
        if not name:
            raise ValueError("场次名称不能为空")
        try:
            start = parse_iso(start_time)
            end = parse_iso(end_time)
        except (ValueError, TypeError):
            raise ValueError("时间格式非法, 请使用 ISO8601(如 2026-08-22T20:00:00+08:00)") from None
        if end <= start:
            raise ValueError("结束时间必须晚于开始时间")
        if end <= datetime.now(UTC):
            raise ValueError("结束时间必须晚于当前时间")

        session_id = await self.flash_repo.next_session_id()
        session = {
            "sessionId": session_id,
            "name": name,
            "startTime": start.isoformat(),
            "endTime": end.isoformat(),
            "status": SESSION_STATUS_DRAFT,
            "itemOrder": 0,
            "createdBy": created_by,
            "createdAt": _now_iso(),
            "updatedAt": _now_iso(),
        }
        await self.flash_repo.save_session(session)
        logger.info("flash_session_created session=%s name=%s", session_id, name)
        return self._session_view(session)

    async def add_item(self, session_id: str, product_id: str,
                       flash_price: float, flash_stock: int,
                       limit_per_member: int) -> dict:
        """向场次添加秒杀商品(仅草稿可加)

        Raises:
            KeyError: 场次/产品不存在
            ValueError: 状态/参数非法或重复添加
        """
        session = await self.flash_repo.get_session(session_id)
        if not session:
            raise KeyError(f"秒杀场次 {session_id} 不存在")
        if session.get("status") != SESSION_STATUS_DRAFT:
            raise ValueError("仅草稿状态的场次可添加商品")

        product = await self.product_repo.get_by_id(product_id)
        if not product:
            raise KeyError(f"产品 {product_id} 不存在")

        flash_price = float(flash_price)
        flash_stock = int(flash_stock)
        limit_per_member = int(limit_per_member)
        original_price = float(product.get("price", 0))
        if flash_price <= 0:
            raise ValueError("秒杀价必须大于 0")
        if original_price > 0 and flash_price >= original_price:
            raise ValueError(f"秒杀价 {flash_price} 必须低于原价 {original_price}")
        if flash_stock < 1:
            raise ValueError("秒杀库存必须不少于 1")
        if limit_per_member < 1:
            raise ValueError("每人限购数量必须不少于 1")

        existing = await self.flash_repo.list_items_by_session(session_id)
        if any(i.get("productId") == product_id for i in existing):
            raise ValueError(f"产品 {product_id} 已在该场次中, 不可重复添加")

        item_id = await self.flash_repo.next_item_id()
        item = {
            "itemId": item_id,
            "sessionId": session_id,
            "productId": product_id,
            "productName": product.get("name", ""),
            "originalPrice": original_price,
            "flashPrice": flash_price,
            "flashStock": flash_stock,
            "soldCount": 0,
            "limitPerMember": limit_per_member,
            "status": ITEM_STATUS_ACTIVE,
            "createdAt": _now_iso(),
            "updatedAt": _now_iso(),
        }
        await self.flash_repo.save_item(item)
        await self.flash_repo.update_session_fields(session_id, {
            "itemOrder": len(existing) + 1,
            "updatedAt": _now_iso(),
        })
        logger.info("flash_item_added session=%s item=%s product=%s price=%s stock=%d",
                    session_id, item_id, product_id, flash_price, flash_stock)
        return dict(item)

    async def publish_session(self, session_id: str) -> dict:
        """发布场次(草稿→已发布); 空场次拒绝发布

        Raises:
            KeyError: 场次不存在
            ValueError: 状态非法/无商品
        """
        session = await self.flash_repo.get_session(session_id)
        if not session:
            raise KeyError(f"秒杀场次 {session_id} 不存在")
        if session.get("status") != SESSION_STATUS_DRAFT:
            raise ValueError("仅草稿状态的场次可发布")
        items = await self.flash_repo.list_items_by_session(session_id)
        if not any(i.get("status") == ITEM_STATUS_ACTIVE for i in items):
            raise ValueError("场次未添加秒杀商品, 不可发布")
        fields = {"status": SESSION_STATUS_PUBLISHED, "updatedAt": _now_iso()}
        await self.flash_repo.update_session_fields(session_id, fields)
        session.update(fields)
        logger.info("flash_session_published session=%s", session_id)
        return self._session_view(session)

    async def cancel_session(self, session_id: str) -> dict:
        """取消场次; 联动取消其全部待支付订单并回补库存

        Raises:
            KeyError: 场次不存在
            ValueError: 已取消/已结束场次不可取消
        """
        session = await self.flash_repo.get_session(session_id)
        if not session:
            raise KeyError(f"秒杀场次 {session_id} 不存在")
        if session.get("status") == SESSION_STATUS_CANCELLED:
            raise ValueError("场次已是取消状态")
        if self._runtime_status(session) == RUNTIME_ENDED:
            raise ValueError("已结束的场次不可取消")

        fields = {"status": SESSION_STATUS_CANCELLED, "updatedAt": _now_iso()}
        await self.flash_repo.update_session_fields(session_id, fields)

        # 联动取消待支付订单(逐单走锁内回补)
        cancelled = 0
        for item in await self.flash_repo.list_items_by_session(session_id):
            pending = await self.flash_repo.list_orders_by_item(
                item["itemId"], statuses=(ORDER_STATUS_PENDING,))
            for order in pending:
                await self._cancel_order_internal(order, "场次取消")
                cancelled += 1
        session.update(fields)
        logger.info("flash_session_cancelled session=%s cancelledOrders=%d",
                    session_id, cancelled)
        view = self._session_view(session)
        view["cancelledOrders"] = cancelled
        return view

    # ============================================================
    # 用户端: 场次/商品查询
    # ============================================================

    async def list_sessions(self, only_published: bool = True) -> list[dict]:
        """场次列表(附运行时状态)"""
        sessions = await self.flash_repo.list_sessions()
        views = [self._session_view(s) for s in sessions]
        if only_published:
            views = [v for v in views
                     if v.get("status") == SESSION_STATUS_PUBLISHED]
        return views

    async def get_session_detail(self, session_id: str) -> dict:
        """场次详情 + 商品列表(含剩余库存/抢购进度)

        Raises:
            KeyError: 场次不存在
        """
        session = await self.flash_repo.get_session(session_id)
        if not session:
            raise KeyError(f"秒杀场次 {session_id} 不存在")
        view = self._session_view(session)
        view["items"] = [self._item_view(i) for i in
                         await self.flash_repo.list_items_by_session(session_id)
                         if i.get("status") == ITEM_STATUS_ACTIVE]
        return view

    @staticmethod
    def _item_view(item: dict) -> dict:
        view = dict(item)
        sold = int(item.get("soldCount", 0))
        stock = int(item.get("flashStock", 0))
        view["remainingStock"] = max(stock - sold, 0)
        view["progressPercent"] = round(sold * 100 / stock, 1) if stock > 0 else 100.0
        return view

    # ============================================================
    # 用户端: 抢购下单(核心临界区)
    # ============================================================

    async def purchase(self, member_id: int, session_id: str,
                       item_id: str, quantity: int = 1) -> dict:
        """抢购下单: 校验(锁外) → 锁内原子判定(幂等/限购/库存) → 扣减 → 建单

        Raises:
            KeyError: 会员/场次/秒杀商品不存在
            ValueError: 各类业务冲突(见设计文档防刷清单)
        """
        settings = await self.flash_repo.get_settings()
        if not settings.get("enabled", True):
            raise ValueError("秒杀活动暂未开启")

        member = await self.member_repo.get_by_id(member_id)
        if not member:
            raise KeyError(f"会员 {member_id} 不存在")
        if member.get("status", 1) == 0:
            raise ValueError("账号已被禁用, 无法参与秒杀")

        min_hours = int(settings.get("minRegisterHours", 0))
        if min_hours > 0:
            created_at = member.get("created_at", "")
            if not created_at:
                raise ValueError("会员注册时间缺失, 无法参与秒杀")
            reg = parse_iso(created_at)
            if datetime.now(UTC) - reg < timedelta(hours=min_hours):
                raise ValueError(f"注册满 {min_hours} 小时后方可参与秒杀")

        min_level = int(settings.get("minMemberLevel", 0))
        if min_level > 0 and int(member.get("level", 1)) < min_level:
            raise ValueError(f"会员等级达到 L{min_level} 后方可参与秒杀")

        quantity = int(quantity)
        if quantity < 1:
            raise ValueError("购买数量必须不少于 1")
        max_qty = int(settings.get("maxQuantityPerOrder", 5))
        if quantity > max_qty:
            raise ValueError(f"单笔订单数量不可超过 {max_qty}")

        session = await self.flash_repo.get_session(session_id)
        if not session:
            raise KeyError(f"秒杀场次 {session_id} 不存在")
        runtime = self._runtime_status(session)
        if runtime != RUNTIME_IN_PROGRESS:
            raise ValueError(f"秒杀场次当前状态为「{RUNTIME_STATUS_NAMES.get(runtime, runtime)}」, 不可下单")

        async with get_lock(f"flash:item:{item_id}"):
            item = await self.flash_repo.get_item(item_id)
            if not item:
                raise KeyError(f"秒杀商品 {item_id} 不存在")
            if item.get("sessionId") != session_id:
                raise ValueError("秒杀商品不属于该场次")
            if item.get("status") != ITEM_STATUS_ACTIVE:
                raise ValueError("秒杀商品已下架")

            # 幂等: 已有待支付订单 → 拒绝(防连点重复下单)
            orders = await self.flash_repo.list_orders_by_item(
                item_id, statuses=(ORDER_STATUS_PENDING, ORDER_STATUS_PAID))
            mine = [o for o in orders if o.get("memberId") == member_id]
            pending_mine = [o for o in mine if o.get("status") == ORDER_STATUS_PENDING]
            if pending_mine:
                raise ValueError(f"已有待支付秒杀订单 {pending_mine[0]['orderNo']}, 请先支付或取消")

            # 限购: pending+paid 合计(paid 可再购, 累计不超限)
            bought = sum(int(o.get("quantity", 0)) for o in mine)
            limit = int(item.get("limitPerMember", 1))
            if bought + quantity > limit:
                raise ValueError(f"每人限购 {limit} 件, 已购 {bought} 件, 本次最多可购 {max(limit - bought, 0)} 件")

            # 库存
            sold = int(item.get("soldCount", 0))
            stock = int(item.get("flashStock", 0))
            if sold + quantity > stock:
                raise ValueError("秒杀库存不足, 手慢了")

            # 扣减库存 + 建单(同一临界区)
            await self.flash_repo.update_item_fields(item_id, {
                "soldCount": sold + quantity,
                "updatedAt": _now_iso(),
            })
            order_no = await self.flash_repo.next_order_no()
            unit_price = float(item.get("flashPrice", 0))
            order = {
                "orderNo": order_no,
                "memberId": member_id,
                "sessionId": session_id,
                "itemId": item_id,
                "productId": item.get("productId", ""),
                "productName": item.get("productName", ""),
                "quantity": quantity,
                "unitPrice": unit_price,
                "totalAmount": round(unit_price * quantity, 2),
                "status": ORDER_STATUS_PENDING,
                "cancelReason": "",
                "paidAt": "",
                "cancelledAt": "",
                "createdAt": _now_iso(),
                "updatedAt": _now_iso(),
            }
            await self.flash_repo.save_order(order)
            logger.info("flash_order_created order=%s member=%s item=%s qty=%d "
                        "sold=%d/%d", order_no, member_id, item_id,
                        quantity, sold + quantity, stock)
            return dict(order)

    # ============================================================
    # 用户端: 订单查询/支付/取消
    # ============================================================

    async def my_orders(self, member_id: int) -> list[dict]:
        return await self.flash_repo.list_orders_by_member(member_id)

    async def get_order(self, order_no: str, member_id: int | None = None,
                        is_admin: bool = False) -> dict:
        """订单详情(本人或管理员可查)

        Raises:
            KeyError: 订单不存在
            ValueError: 无权查看
        """
        order = await self.flash_repo.get_order(order_no)
        if not order:
            raise KeyError(f"秒杀订单 {order_no} 不存在")
        if not is_admin and member_id is not None \
                and int(order.get("memberId", 0)) != int(member_id):
            raise ValueError("无权查看他人订单")
        return dict(order)

    async def pay_order(self, order_no: str, member_id: int | None = None,
                        is_admin: bool = False) -> dict:
        """模拟支付成功(pending→paid)

        Raises:
            KeyError: 订单不存在
            ValueError: 状态非法/无权操作
        """
        order = await self.flash_repo.get_order(order_no)
        if not order:
            raise KeyError(f"秒杀订单 {order_no} 不存在")
        if not is_admin and member_id is not None \
                and int(order.get("memberId", 0)) != int(member_id):
            raise ValueError("无权支付他人订单")
        if order.get("status") != ORDER_STATUS_PENDING:
            raise ValueError(f"订单状态为「{order.get('status')}」, 不可支付")
        fields = {"status": ORDER_STATUS_PAID,
                  "paidAt": _now_iso(), "updatedAt": _now_iso()}
        await self.flash_repo.update_order_fields(order_no, fields)
        order.update(fields)
        logger.info("flash_order_paid order=%s", order_no)
        return dict(order)

    async def cancel_order(self, order_no: str, member_id: int | None = None,
                           is_admin: bool = False) -> dict:
        """主动取消订单: 锁内回补库存

        Raises:
            KeyError: 订单不存在
            ValueError: 状态非法/无权操作
        """
        order = await self.flash_repo.get_order(order_no)
        if not order:
            raise KeyError(f"秒杀订单 {order_no} 不存在")
        if not is_admin and member_id is not None \
                and int(order.get("memberId", 0)) != int(member_id):
            raise ValueError("无权取消他人订单")
        if order.get("status") != ORDER_STATUS_PENDING:
            raise ValueError(f"订单状态为「{order.get('status')}」, 仅待支付订单可取消")

        async with get_lock(f"flash:item:{order['itemId']}"):
            result = await self._cancel_order_internal(order, "买家主动取消")
        logger.info("flash_order_cancelled order=%s", order_no)
        return result

    async def _cancel_order_internal(self, order: dict, reason: str) -> dict:
        """锁内取消+回补(调用方必须已持有 flash:item:{itemId} 锁或串行上下文)"""
        item_id = order["itemId"]
        item = await self.flash_repo.get_item(item_id)
        if item:
            sold = int(item.get("soldCount", 0))
            await self.flash_repo.update_item_fields(item_id, {
                "soldCount": max(sold - int(order.get("quantity", 0)), 0),
                "updatedAt": _now_iso(),
            })
        fields = {"status": ORDER_STATUS_CANCELLED,
                  "cancelReason": reason,
                  "cancelledAt": _now_iso(), "updatedAt": _now_iso()}
        await self.flash_repo.update_order_fields(order["orderNo"], fields)
        order.update(fields)
        return dict(order)

    # ============================================================
    # 管理端: 超时订单批量取消 / 参数 / 统计
    # ============================================================

    async def cancel_expired_orders(self) -> dict:
        """批量取消超时未支付订单(回补库存); 供定时任务/管理端触发"""
        settings = await self.flash_repo.get_settings()
        expire_minutes = int(settings.get("orderExpireMinutes", 15))
        threshold = datetime.now(UTC) - timedelta(minutes=expire_minutes)
        cancelled = []
        for order in await self.flash_repo.list_pending_orders():
            if parse_iso(order.get("createdAt", _now_iso())) < threshold:
                async with get_lock(f"flash:item:{order['itemId']}"):
                    fresh = await self.flash_repo.get_order(order["orderNo"])
                    if fresh and fresh.get("status") == ORDER_STATUS_PENDING:
                        await self._cancel_order_internal(fresh, "超时未支付自动取消")
                        cancelled.append(order["orderNo"])
        logger.info("flash_expired_orders_cancelled count=%d", len(cancelled))
        return {"success": True, "cancelledCount": len(cancelled),
                "orderNos": cancelled, "expireMinutes": expire_minutes}

    async def get_settings(self) -> dict:
        return await self.flash_repo.get_settings()

    async def update_settings(self, payload: dict, updated_by: str = "admin") -> dict:
        """修改参数(白名单字段, 非法值拒绝)

        Raises:
            ValueError: 字段非法
        """
        settings = await self.flash_repo.get_settings()
        updates = {}
        for key in _SETTING_FIELDS:
            if key not in payload:
                continue
            value = payload[key]
            if key == "enabled":
                if not isinstance(value, bool):
                    raise ValueError("enabled 必须为布尔值")
                updates[key] = value
                continue
            try:
                value = int(value)
            except (TypeError, ValueError):
                raise ValueError(f"{key} 必须为非负整数") from None
            if value < 0:
                raise ValueError(f"{key} 必须为非负整数")
            if key == "maxQuantityPerOrder" and value < 1:
                raise ValueError("maxQuantityPerOrder 必须不少于 1")
            updates[key] = value
        if not updates:
            raise ValueError("无可更新的参数")
        settings.update(updates)
        settings["updatedAt"] = _now_iso()
        settings["updatedBy"] = updated_by
        await self.flash_repo.save_settings(settings)
        logger.info("flash_settings_updated fields=%s by=%s",
                    sorted(updates.keys()), updated_by)
        return dict(settings)

    async def stats(self) -> dict:
        """全局统计(按场次聚合)"""
        sessions = await self.flash_repo.list_sessions()
        orders = await self.flash_repo.list_all_orders()
        session_stats = []
        for session in sessions:
            sid = session["sessionId"]
            session_orders = [o for o in orders if o.get("sessionId") == sid]
            paid = [o for o in session_orders if o.get("status") == ORDER_STATUS_PAID]
            pending = [o for o in session_orders if o.get("status") == ORDER_STATUS_PENDING]
            cancelled = [o for o in session_orders if o.get("status") == ORDER_STATUS_CANCELLED]
            items = await self.flash_repo.list_items_by_session(sid)
            session_stats.append({
                "sessionId": sid,
                "name": session.get("name", ""),
                "runtimeStatus": self._runtime_status(session),
                "itemCount": len(items),
                "totalStock": sum(int(i.get("flashStock", 0)) for i in items),
                "soldCount": sum(int(i.get("soldCount", 0)) for i in items),
                "orderCount": len(session_orders),
                "paidCount": len(paid),
                "pendingCount": len(pending),
                "cancelledCount": len(cancelled),
                "paidAmount": round(sum(float(o.get("totalAmount", 0)) for o in paid), 2),
            })
        return {
            "sessionCount": len(sessions),
            "orderCount": len(orders),
            "paidAmount": round(sum(
                float(o.get("totalAmount", 0)) for o in orders
                if o.get("status") == ORDER_STATUS_PAID), 2),
            "sessions": session_stats,
        }
