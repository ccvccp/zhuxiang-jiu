"""68号 P8·信值·臻选——购物车与下单服务(平台化升级 §二 P8)

依据:《信值·臻选68号_平台化升级创新规划方案》§二/§三:
    P8 购物 = 加购(信值价实时试算快照) → 结算预览(实时重算
    防旧价套利) → 下单(三因子×α抵扣+年龄门+库存预扣)
    → 九态状态机(复用主站订单语义)

存储(68号存储范式扩展, zhuxiang:xinzhi:xz:* 新表):
    xz:cart:{memberId}       购物车单文档(快照仅展示)
    xz:orders:{orderId}      臻选订单(XZ 前缀/九态/抵扣明细)
    xz:listings:{listingId}  铺货商品(P7 写入, 本模块只读)
    xz:shops:{shopId}        店铺(P6 写入, 本模块只读)
    xz:settlements 表归 P9(xinzhi_settle_service), 仓储层
    统一放在本文件(XinzhiTradeRepository)供两服务共享,
    避免循环依赖(P9 单向 import 本模块)。

铁律(项目宪法):
    - 全确定性规则引擎: 计价复用 P2 XinzhiPricingService
      .price_breakdown(60号 compute_price 三因子 × α 信值
      抵扣), 同输入同输出, LLM 禁入判定链
    - 与主站订单分表并存: XZ 前缀独立订单域, 不 import
      order_service(避免耦合主站状态机——分表并存原因:
      臻选订单带抵扣明细, 不污染主站九态语义; 主站语义
      仅对齐复刻: 年龄门 core.age_gate / 库存锁 stock:{pid})
    - 下单以实时价为准(购物车快照仅展示——防旧价套利)
    - α 信值抵扣单笔 ≤30% 上限(P2 口径), 订单级二次校验
    - 资金给付永不自动: 本期仅取消(库存回补)与状态流转;
      退货/退款(RETURNING/REFUNDED)留语义不实现——售后
      走 68号既有 L1/L2/L3 反馈分流+46号审批轨
"""

import json
import logging
from datetime import datetime, UTC

from core.age_gate import (
    is_adult, MINOR_REJECT_MSG, AGE_CONFIRM_REQUIRED_MSG,
)
from core.helpers import ts
from core.locks import get_lock
from repositories.backend import (
    is_redis_mode, get_redis_client, get_in_memory_store, _k,
)
from repositories.inventory_repository import InventoryRepository
from repositories.member_repository import MemberRepository
from repositories.product_repository import ProductRepository
from services.xinzhi_pricing_service import XinzhiPricingService

logger = logging.getLogger(__name__)


# ============================================================
# 订单九态(对齐主站 order_service 语义, 独立常量避免耦合)
# ============================================================

PENDING = "PENDING"        # 待付款
PAID = "PAID"              # 待发货(已付款)
SHIPPED = "SHIPPED"        # 待收货(已发货)
RECEIVED = "RECEIVED"      # 待评价(已签收)
COMPLETED = "COMPLETED"    # 已完成
CANCELLED = "CANCELLED"    # 已取消(用户主动, 库存回补)
CLOSED = "CLOSED"          # 已关闭(超时未支付——调度语义本期未接入)
RETURNING = "RETURNING"    # 退货中(本期留语义, 售后走反馈分流)
REFUNDED = "REFUNDED"      # 已退款(本期留语义, 冲正仅结算侧)

STATUS_CN = {
    PENDING: "待付款", PAID: "待发货", SHIPPED: "待收货",
    RECEIVED: "待评价", COMPLETED: "已完成", CANCELLED: "已取消",
    CLOSED: "已关闭", RETURNING: "退货中", REFUNDED: "已退款",
}

# 运费规则(对齐主站: 满 99 免运费, 否则 10 元)
SHIPPING_FREE_THRESHOLD = 99
SHIPPING_FEE = 10

# α 信值抵扣单笔上限(P2 文档口径: ≤30% 防恶意套现)
ALPHA_CAP = 0.30

# 单条目数量上限(防异常大额——67号求购同款口径)
MAX_ITEM_QUANTITY = 99

# P7 铺货在售状态(状态机: draft→reviewing→listed⇄delisted→removed)
LISTING_STATUS_LISTED = "listed"


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


class XinzhiTradeRepository:
    """68号 P8/P9 交易域数据访问层(双模式: 内存 + Redis)

    表清单(《平台化升级创新规划方案》§三 Redis Key 规划):
        xz:cart         购物车(member 级单文档)
        xz:orders       臻选订单(XZ 前缀/九态/抵扣明细)
        xz:settlements  结算单(P9 分账明细/冲正)
        xz:listings     铺货商品(P7 写入, 只读)
        xz:shops        店铺(P6 写入, 只读)

    序列化对齐 repositories/xinzhi_repository.py 范式:
        None→"" / bool→0|1 / dict|list→JSON 字符串,
        反序列化按类型清单+JSON 前缀启发式还原(P6g-4 教训)。
    """

    TABLE_CART = "xz:cart"
    TABLE_ORDERS = "xz:orders"
    TABLE_SETTLEMENTS = "xz:settlements"
    TABLE_LISTINGS = "xz:listings"
    TABLE_SHOPS = "xz:shops"

    _INT_FIELDS = ("memberId", "shopId", "shopMemberId", "settleId",
                   "listingId", "quantity", "snapshotId")
    _FLOAT_FIELDS = ("sourcePrice", "xinzhiPrice", "orderAmount",
                     "merchantProceeds", "platformFee", "proceedsRate",
                     "feeRate", "reversalDebt")
    _BOOL_FIELDS = ("ageConfirmed",)
    _JSON_LIST_FIELDS = ("items", "timeline", "breakdown", "funding")

    def __init__(self, store: dict = None):
        self.store = (store if store is not None
                      else get_in_memory_store())

    # --------------------------------------------------------
    # 序列化(口径对齐 xinzhi_repository)
    # --------------------------------------------------------

    @staticmethod
    def _serialize(record: dict) -> dict:
        out = {}
        for k, v in record.items():
            if v is None:
                out[k] = ""
            elif isinstance(v, bool):
                out[k] = 1 if v else 0
            elif isinstance(v, (dict, list)):
                out[k] = json.dumps(v, ensure_ascii=False)
            else:
                out[k] = v
        return out

    @classmethod
    def _deserialize(cls, data: dict) -> dict:
        record = {}
        for k, v in data.items():
            if k in cls._INT_FIELDS:
                try:
                    record[k] = int(v)
                except (TypeError, ValueError):
                    record[k] = v
            elif k in cls._FLOAT_FIELDS:
                try:
                    record[k] = float(v)
                except (TypeError, ValueError):
                    record[k] = v
            elif k in cls._BOOL_FIELDS:
                if v in ("0", 0):
                    record[k] = False
                elif v in ("1", 1):
                    record[k] = True
                else:
                    record[k] = bool(v)
            elif isinstance(v, str) and (
                    k in cls._JSON_LIST_FIELDS
                    or v.startswith(("{", "["))):
                try:
                    record[k] = json.loads(v)
                except ValueError:
                    record[k] = v
            else:
                record[k] = v
        return record

    def _ensure_store(self):
        for table in (self.TABLE_CART, self.TABLE_ORDERS,
                      self.TABLE_SETTLEMENTS, self.TABLE_LISTINGS,
                      self.TABLE_SHOPS):
            self.store.setdefault(table, {})

    async def next_id(self, kind: str) -> int:
        """自增序列(xz 域: order/settlement)"""
        if is_redis_mode():
            client = await get_redis_client()
            return await client.incr(
                _k("xinzhi", "xz", kind, "seq"))
        self._ensure_store()
        seq_key = f"_xz_{kind}_seq"
        seq = self.store.get(seq_key, 0) + 1
        self.store[seq_key] = seq
        return seq

    async def _save(self, table: str, record_id,
                    record: dict) -> dict:
        if is_redis_mode():
            client = await get_redis_client()
            await client.hset(_k("xinzhi", table, record_id),
                              mapping=self._serialize(record))
            return record
        self._ensure_store()
        self.store[table][record_id] = record
        return record

    async def _get(self, table: str, record_id) -> dict | None:
        if is_redis_mode():
            client = await get_redis_client()
            data = await client.hgetall(_k("xinzhi", table,
                                           record_id))
            return self._deserialize(data) if data else None
        self._ensure_store()
        return self.store[table].get(record_id)

    async def _list(self, table: str,
                    limit: int = 500) -> list[dict]:
        if is_redis_mode():
            client = await get_redis_client()
            keys = await client.keys(_k("xinzhi", table, "*"))
            result = []
            for key in keys:
                if str(key).endswith(":seq"):
                    continue
                data = await client.hgetall(key)
                if data:
                    result.append(self._deserialize(data))
        else:
            self._ensure_store()
            result = list(self.store[table].values())
        return result[:limit]

    # ---------- 购物车 ----------

    async def get_cart(self, member_id: int) -> dict | None:
        return await self._get(self.TABLE_CART, member_id)

    async def save_cart(self, record: dict) -> dict:
        return await self._save(self.TABLE_CART,
                                record["memberId"], record)

    # ---------- 订单 ----------

    async def save_order(self, record: dict) -> dict:
        return await self._save(self.TABLE_ORDERS,
                                record["orderId"], record)

    async def get_order(self, order_id: str) -> dict | None:
        return await self._get(self.TABLE_ORDERS, order_id)

    async def list_orders(self, member_id: int = None,
                          status: str = None,
                          limit: int = 200) -> list[dict]:
        result = []
        for r in await self._list(self.TABLE_ORDERS,
                                  limit=2000):
            if member_id is not None \
                    and r.get("memberId") != member_id:
                continue
            if status and r.get("status") != status:
                continue
            result.append(r)
        return sorted(result,
                      key=lambda x: str(x.get("createdAt") or ""),
                      reverse=True)[:limit]

    # ---------- 结算单(P9 共享仓储) ----------

    async def save_settlement(self, record: dict) -> dict:
        return await self._save(self.TABLE_SETTLEMENTS,
                                record["settleId"], record)

    async def get_settlement(self, settle_id: int) -> dict | None:
        return await self._get(self.TABLE_SETTLEMENTS, settle_id)

    async def list_settlements(self, status: str = None,
                               shop_member_id: int = None,
                               limit: int = 200) -> list[dict]:
        result = []
        for r in await self._list(self.TABLE_SETTLEMENTS,
                                  limit=2000):
            if status and r.get("status") != status:
                continue
            if shop_member_id is not None \
                    and r.get("shopMemberId") != shop_member_id:
                continue
            result.append(r)
        return sorted(result,
                      key=lambda x: int(x.get("settleId") or 0)
                      )[:limit]

    # ---------- 铺货/店铺(P6/P7 写入, 本模块只读) ----------

    # P6/P7 存储口径(表名下划线式 xz_listings/xz_shops, Redis
    # 键前缀 zhuxiang:xz:*)——主线集成时铺货/店铺以 P6/P7 服务
    # 写入为准; 本模块读路径双口径兼容(仅存储层读, 不 import
    # 其服务代码, 保持并行开发零耦合)
    TABLE_LISTINGS_P6 = "xz_listings"
    TABLE_SHOPS_P6 = "xz_shops"

    async def _get_xz(self, table: str,
                      record_id) -> dict | None:
        """P6/P7 存储口径读取(序列化格式与本仓同构)"""
        if is_redis_mode():
            client = await get_redis_client()
            data = await client.hgetall(
                _k("xz", table, record_id))
            return self._deserialize(data) if data else None
        self._ensure_store()
        return self.store.get(table, {}).get(record_id)

    async def get_listing(self, listing_id: int) -> dict | None:
        record = await self._get(self.TABLE_LISTINGS, listing_id)
        if record is None:
            record = await self._get_xz(
                self.TABLE_LISTINGS_P6, listing_id)
        return record

    async def get_shop(self, shop_id: int) -> dict | None:
        record = await self._get(self.TABLE_SHOPS, shop_id)
        if record is None:
            record = await self._get_xz(
                self.TABLE_SHOPS_P6, shop_id)
        return record


class XinzhiCartService:
    """68号 P8·购物车+下单+订单流转"""

    def __init__(self, repo: XinzhiTradeRepository = None):
        self.repo = (repo if repo is not None
                     else XinzhiTradeRepository())
        self.member_repo = MemberRepository()
        self.product_repo = ProductRepository()
        self.inventory_repo = InventoryRepository()
        # P8 计价直接复用 P2 试算方法(三因子×α, 杀熟审计留痕)
        self.pricing = XinzhiPricingService()

    # ============================================================
    # 内部工具
    # ============================================================

    @staticmethod
    def _is_listed(listing: dict) -> bool:
        """铺货在售判定(P7 状态机 listed 为唯一在售态;
        兼容布尔 listed 字段写法——P7 并行开发容错)"""
        status = str(listing.get("status") or "")
        if status:
            return status == LISTING_STATUS_LISTED
        return bool(listing.get("listed"))

    async def _load_listing(self, listing_id: int) -> dict:
        listing = await self.repo.get_listing(listing_id)
        if listing is None:
            raise KeyError(
                f"铺货商品不存在(listingId={listing_id})")
        if not self._is_listed(listing):
            raise ValueError(
                f"铺货商品未上架(listingId={listing_id}, "
                f"状态 {listing.get('status')})")
        return listing

    async def _load_member(self, member_id: int) -> dict:
        member = await self.member_repo.get_by_id(member_id)
        if not member:
            raise KeyError(f"会员 {member_id} 不存在")
        if member.get("status", 1) != 1:
            raise ValueError("账号已被禁用")
        return member

    # ============================================================
    # 购物车(加购/改量/移除/查看)
    # ============================================================

    async def cart_add(self, member_id: int, listing_id: int,
                       quantity: int) -> dict:
        """加购(校验 listing 存在且 listed; 价格快照=当时试算)

        Raises:
            KeyError: 铺货商品/商品不存在
            ValueError: 数量非法/未上架
        """
        quantity = int(quantity or 0)
        if not (0 < quantity <= MAX_ITEM_QUANTITY):
            raise ValueError(
                f"数量须为 1-{MAX_ITEM_QUANTITY} 的整数")
        listing = await self._load_listing(listing_id)
        # 快照=当时试算(P2 price_breakdown: 三因子×α 全明细)
        price = await self.pricing.price_breakdown(
            member_id, listing["productId"])
        async with get_lock(f"xz:cart:{member_id}"):
            cart = await self.repo.get_cart(member_id)
            if cart is None:
                cart = {"memberId": member_id, "items": [],
                        "updatedAt": _now_iso()}
            items = cart.get("items") or []
            for item in items:
                if item.get("listingId") == listing_id:
                    item["quantity"] = min(
                        item["quantity"] + quantity,
                        MAX_ITEM_QUANTITY)
                    break
            else:
                items.append({
                    "listingId": listing_id,
                    "quantity": quantity,
                    "addedAt": _now_iso(),
                    "priceSnapshot": {
                        "finalPrice": price["finalPrice"],
                        "xinzhiCredit": price["xinzhiCredit"],
                        "breakdownLine": price["breakdownLine"],
                    },
                })
            cart["items"] = items
            cart["updatedAt"] = _now_iso()
            await self.repo.save_cart(cart)
        return cart

    async def cart_update(self, member_id: int, listing_id: int,
                          quantity: int) -> dict:
        """改量(条目须已在购物车)

        Raises:
            KeyError: 条目不存在
            ValueError: 数量非法
        """
        quantity = int(quantity or 0)
        if not (0 < quantity <= MAX_ITEM_QUANTITY):
            raise ValueError(
                f"数量须为 1-{MAX_ITEM_QUANTITY} 的整数")
        async with get_lock(f"xz:cart:{member_id}"):
            cart = await self.repo.get_cart(member_id)
            items = (cart or {}).get("items") or []
            for item in items:
                if item.get("listingId") == listing_id:
                    item["quantity"] = quantity
                    cart["updatedAt"] = _now_iso()
                    await self.repo.save_cart(cart)
                    return cart
        raise KeyError(
            f"购物车中无此商品(listingId={listing_id})")

    async def cart_remove(self, member_id: int,
                          listing_id: int) -> dict:
        """移除条目

        Raises:
            KeyError: 条目不存在
        """
        async with get_lock(f"xz:cart:{member_id}"):
            cart = await self.repo.get_cart(member_id)
            items = (cart or {}).get("items") or []
            remain = [i for i in items
                      if i.get("listingId") != listing_id]
            if len(remain) == len(items):
                raise KeyError(
                    f"购物车中无此商品(listingId={listing_id})")
            cart["items"] = remain
            cart["updatedAt"] = _now_iso()
            await self.repo.save_cart(cart)
            return cart

    async def cart_mine(self, member_id: int) -> dict:
        """我的购物车(空车返回空骨架——诚实零值)"""
        cart = await self.repo.get_cart(member_id)
        if cart is None:
            return {"memberId": member_id, "items": [],
                    "updatedAt": ""}
        return cart

    # ============================================================
    # 结算预览(实时重算——快照仅展示, 防旧价套利)
    # ============================================================

    async def checkout_preview(self, member_id: int) -> dict:
        """结算预览: 逐项实时重算 + 合计 + α 抵扣上限 30% 校验
        + 运费(满 99 免, 对齐主站)

        Raises:
            KeyError: 铺货商品/商品不存在
            ValueError: 未上架/α 抵扣超上限(防御性)
        """
        cart = await self.repo.get_cart(member_id)
        cart_items = (cart or {}).get("items") or []
        rows = []
        base_total = after_three_total = credit_total = 0.0
        goods_total = 0.0
        shop_ids: set[int] = set()
        for item in cart_items:
            lid = item.get("listingId")
            qty = int(item.get("quantity") or 0)
            listing = await self._load_listing(lid)
            # 实时计价(下单以此为准——快照仅展示)
            price = await self.pricing.price_breakdown(
                member_id, listing["productId"])
            snapshot = item.get("priceSnapshot") or {}
            changed = (
                snapshot.get("finalPrice")
                != price["finalPrice"]
                or snapshot.get("xinzhiCredit")
                != price["xinzhiCredit"])
            rows.append({
                "listingId": lid,
                "productId": listing["productId"],
                "quantity": qty,
                "snapshot": snapshot,
                "realtime": {
                    "finalPrice": price["finalPrice"],
                    "xinzhiCredit": price["xinzhiCredit"],
                    "breakdownLine": price["breakdownLine"],
                    "basePrice": price["basePrice"],
                    "afterThreeFactor":
                        price["afterThreeFactor"],
                    "xinzhiAlpha": price["xinzhiAlpha"],
                    "floored": price["floored"],
                    "auditFlag": price["auditFlag"],
                },
                "priceChanged": changed,
            })
            base_total += price["basePrice"] * qty
            after_three_total += \
                price["afterThreeFactor"] * qty
            credit_total += price["xinzhiCredit"] * qty
            goods_total += price["finalPrice"] * qty
            shop_ids.add(int(listing.get("shopId") or 0))
        base_total = round(base_total, 2)
        after_three_total = round(after_three_total, 2)
        credit_total = round(credit_total, 2)
        goods_total = round(goods_total, 2)
        shipping_fee = (0 if goods_total
                        >= SHIPPING_FREE_THRESHOLD
                        else SHIPPING_FEE)
        actual_amount = round(goods_total + shipping_fee, 2)
        # α 抵扣上限 30% 校验(P2 单笔口径; 逐项已封顶, 此处
        # 订单级二次防御性校验——确定性规则, 恒可复算)
        cap_amount = round(base_total * ALPHA_CAP, 2)
        alpha_ok = credit_total <= cap_amount + 1e-9
        if not alpha_ok:
            raise ValueError(
                f"α 信值抵扣超上限: {credit_total} > "
                f"{cap_amount}(单笔 ≤30% 铁律)")
        return {
            "memberId": member_id,
            "items": rows,
            "totals": {
                "baseTotal": base_total,
                "afterThreeTotal": after_three_total,
                "xinzhiCredit": credit_total,
                "goodsTotal": goods_total,
                "shippingFee": shipping_fee,
                "actualAmount": actual_amount,
            },
            "alphaCap": {
                "capRate": ALPHA_CAP,
                "creditTotal": credit_total,
                "capAmount": cap_amount,
                "ok": alpha_ok,
            },
            "shippingRule": {
                "freeThreshold": SHIPPING_FREE_THRESHOLD,
                "fee": SHIPPING_FEE,
            },
            "shopIds": sorted(shop_ids),
            "shopCount": len(shop_ids),
            "crossShop": len(shop_ids) > 1,
            "note": "快照仅展示, 下单以实时价为准(防旧价套利)",
        }

    # ============================================================
    # 下单(逐项校验/年龄门/实时计价/锁内库存预扣)
    # ============================================================

    async def create_order(self, member_id: int, items: list = None,
                           address: dict = None, remark: str = "",
                           age_confirmed: bool = False) -> dict:
        """创建臻选订单(从购物车全量或指定 items)

        流程(对齐主站 order_service.create 语义, 独立实现):
            1. 会员校验(存在/状态)
            2. 酒类合规年龄门(未成年硬拦截; 首次声明回写)
            3. 解析下单集合(全量购物车或指定条目)
            4. 逐项校验(listing 在售/店铺归属/单店口径)
               + 实时计价(三因子×α——P2 复用)
            5. 锁内库存预扣(stock:{pid} 锁——与主站/37号
               同款原子范式; 中途失败回滚已扣)
            6. 生成订单号 XZ+时间戳+序列(37号防碰撞范式)

        Raises:
            KeyError: 会员/铺货/商品/店铺不存在
            ValueError: 年龄门/数量/跨店/库存不足/α 超上限
        """
        if not isinstance(address, dict) or not address:
            raise ValueError("收货地址必填(address)")
        async with get_lock(f"xz:cart:{member_id}"):
            # 1) 会员校验
            member = await self._load_member(member_id)

            # 2) 酒类合规年龄门(对齐主站 P0-1: 未成年硬拦截/
            #    无成年声明须 ageConfirmed; 首次声明回写会员标记)
            birthdate = member.get("birthdate") or ""
            if birthdate and not is_adult(birthdate):
                raise ValueError(MINOR_REJECT_MSG)
            if not (member.get("ageVerified")
                    or member.get("ageConfirmed")):
                if not age_confirmed:
                    raise ValueError(AGE_CONFIRM_REQUIRED_MSG)
                await self.member_repo.update_fields(
                    member_id, {"ageConfirmed": True})

            # 3) 解析下单集合
            cart = await self.repo.get_cart(member_id)
            cart_items = {i.get("listingId"): i
                          for i in (cart or {}).get("items")
                          or []}
            if not cart_items:
                raise ValueError("购物车为空, 无法下单")
            if items:
                resolved = []
                for it in items:
                    lid = it.get("listingId")
                    if lid not in cart_items:
                        raise ValueError(
                            f"购物车中无此商品"
                            f"(listingId={lid})")
                    cart_qty = int(
                        cart_items[lid].get("quantity") or 0)
                    qty = int(it.get("quantity") or cart_qty)
                    if not (0 < qty <= cart_qty):
                        raise ValueError(
                            f"指定数量须为 1-{cart_qty}"
                            f"(listingId={lid})")
                    resolved.append((lid, qty))
            else:
                resolved = [
                    (lid, int(i.get("quantity") or 0))
                    for lid, i in cart_items.items()]

            # 4) 逐项校验 + 实时计价
            listings = {}
            for lid, qty in resolved:
                listing = await self._load_listing(lid)
                product = await self.product_repo.get_by_id(
                    listing["productId"])
                if product is None:
                    raise KeyError(f"商品不存在"
                                   f"(productId="
                                   f"{listing['productId']})")
                listings[lid] = (listing, qty, product)
            shop_ids = {int(l.get("shopId") or 0)
                        for l, _, _ in listings.values()}
            if len(shop_ids) > 1:
                raise ValueError(
                    "臻选订单限单店(跨店请分店下单——"
                    "发货归属与分账口径)")
            shop_id = shop_ids.pop()
            shop = await self.repo.get_shop(shop_id)
            if shop is None:
                raise KeyError(f"店铺不存在(shopId={shop_id})")

            order_lines = []
            for lid, (listing, qty, product) in \
                    listings.items():
                price = await self.pricing.price_breakdown(
                    member_id, listing["productId"])
                unit = float(price["finalPrice"])
                order_lines.append({
                    "listingId": lid,
                    "shopId": shop_id,
                    "productId": listing["productId"],
                    "productName": product.get("name", ""),
                    "quantity": qty,
                    "unitPrice": unit,
                    "subtotal": round(unit * qty, 2),
                    "unitCredit": float(price["xinzhiCredit"]),
                    "lineCredit": round(float(
                        price["xinzhiCredit"]) * qty, 2),
                    "breakdownLine": price["breakdownLine"],
                    "priceRecord": price,
                })

            # 5) 锁内库存预扣(主站 product_repository.stock
            #    口径; 中途失败回滚已扣——渐进预扣的原子化补强)
            deducted: list[tuple[str, int]] = []
            try:
                for line in order_lines:
                    pid = line["productId"]
                    qty = line["quantity"]
                    async with get_lock(f"stock:{pid}"):
                        product = await \
                            self.product_repo.get_by_id(pid)
                        if product is None:
                            raise KeyError(
                                f"商品不存在(productId={pid})")
                        stock = int(product.get("stock") or 0)
                        if stock < qty:
                            raise ValueError(
                                f"库存不足: {pid} 当前 "
                                f"{stock}, 需 {qty}")
                        await self.inventory_repo.deduct(
                            pid, qty)
                        deducted.append((pid, qty))
            except (KeyError, ValueError):
                for pid, qty in deducted:
                    async with get_lock(f"stock:{pid}"):
                        await self.inventory_repo.restock(
                            pid, qty)
                raise

            # 6) 汇总计价 + 生成订单号(XZ+时间戳+序列)
            goods_total = round(sum(
                l["subtotal"] for l in order_lines), 2)
            credit_total = round(sum(
                l["lineCredit"] for l in order_lines), 2)
            base_total = round(sum(
                l["priceRecord"]["basePrice"] * l["quantity"]
                for l in order_lines), 2)
            after_three_total = round(sum(
                l["priceRecord"]["afterThreeFactor"]
                * l["quantity"]
                for l in order_lines), 2)
            shipping_fee = (0 if goods_total
                            >= SHIPPING_FREE_THRESHOLD
                            else SHIPPING_FEE)
            actual_amount = round(
                goods_total + shipping_fee, 2)
            cap_amount = round(base_total * ALPHA_CAP, 2)
            alpha_ok = credit_total <= cap_amount + 1e-9
            if not alpha_ok:
                raise ValueError(
                    f"α 信值抵扣超上限: {credit_total} > "
                    f"{cap_amount}(单笔 ≤30% 铁律)")

            order_seq = await self.repo.next_id("order")
            order_id = (
                f"XZ{datetime.now(UTC).strftime('%Y%m%d%H%M%S')}"
                f"{order_seq:06d}")
            now = ts()
            items_out = [{
                "listingId": l["listingId"],
                "productId": l["productId"],
                "productName": l["productName"],
                "quantity": l["quantity"],
                "unitPrice": l["unitPrice"],
                "subtotal": l["subtotal"],
                "xinzhiCredit": l["lineCredit"],
                "breakdownLine": l["breakdownLine"],
            } for l in order_lines]
            order = {
                "orderId": order_id,
                "memberId": member_id,
                "shopId": shop_id,
                "shopMemberId": int(shop.get("memberId") or 0),
                "shopName": str(shop.get("shopName") or ""),
                "items": items_out,
                "priceDetail": {
                    "baseTotal": base_total,
                    "afterThreeTotal": after_three_total,
                    "xinzhiCredit": credit_total,
                    "goodsTotal": goods_total,
                    "shippingFee": shipping_fee,
                    "actualAmount": actual_amount,
                    "alphaCapRate": (
                        round(credit_total / base_total, 4)
                        if base_total else 0),
                    "alphaCapOk": alpha_ok,
                    "breakdown": [l["priceRecord"]
                                  for l in order_lines],
                },
                "status": PENDING,
                "address": address,
                "remark": (remark or "")[:200],
                "payment": {"method": "", "paidAt": "",
                            "funding": []},
                "logistics": {"carrier": "", "waybillNo": "",
                              "shippedAt": "", "signedAt": ""},
                "review": {"rating": 0, "content": "",
                           "reviewedAt": "", "creditNote": ""},
                "timeline": [{"status": PENDING, "time": now,
                              "action": "创建订单(臻选)"}],
                "createdAt": now,
                "updatedAt": now,
            }
            await self.repo.save_order(order)
            # 7) 购物车清理(已购条目移除/减量; 须在持有
            #    xz:cart 锁内调用——本方法已持锁, 不再重复加锁)
            self._consume_cart(cart, resolved)
            await self.repo.save_cart(cart)
            logger.info("xz_order_created order=%s member=%s "
                        "actual=%.2f credit=%.2f",
                        order_id, member_id, actual_amount,
                        credit_total)
            return {
                "success": True,
                "orderId": order_id,
                "status": PENDING,
                "statusName": STATUS_CN[PENDING],
                "memberId": member_id,
                "shopId": shop_id,
                "priceDetail": order["priceDetail"],
            }

    @staticmethod
    def _consume_cart(cart: dict, resolved: list) -> None:
        """购物车条目消耗(须在持有 xz:cart 锁内调用)"""
        items = cart.get("items") or []
        remain = []
        bought = {lid: qty for lid, qty in resolved}
        for item in items:
            lid = item.get("listingId")
            if lid in bought:
                left = int(item.get("quantity") or 0) \
                    - bought[lid]
                if left > 0:
                    item["quantity"] = left
                    remain.append(item)
            else:
                remain.append(item)
        cart["items"] = remain
        cart["updatedAt"] = _now_iso()

    # ============================================================
    # 订单查询/取消
    # ============================================================

    async def get_order(self, order_id: str) -> dict:
        """订单详情(访问控制在路由层: 买家/店铺归属/admin)

        Raises:
            KeyError: 订单不存在
        """
        order = await self.repo.get_order(order_id)
        if order is None:
            raise KeyError(f"订单不存在(orderId={order_id})")
        return order

    async def my_orders(self, member_id: int,
                        status: str = None) -> list[dict]:
        """我的臻选订单(最新优先)"""
        return await self.repo.list_orders(
            member_id=member_id, status=status)

    async def cancel_order(self, member_id: int, order_id: str,
                           reason: str = "用户取消") -> dict:
        """取消订单 PENDING→CANCELLED(库存回补)

        Raises:
            KeyError: 订单不存在
            PermissionError: 非订单买家
            ValueError: 状态非 PENDING
        """
        async with get_lock(f"xz:order:{order_id}"):
            order = await self.repo.get_order(order_id)
            if order is None:
                raise KeyError(
                    f"订单不存在(orderId={order_id})")
            if order.get("memberId") != member_id:
                raise PermissionError("仅订单买家可取消")
            if order.get("status") != PENDING:
                raise ValueError(
                    f"订单状态异常: 仅 {PENDING} 可取消, "
                    f"当前 {order.get('status')}")
            now = ts()
            for item in order.get("items") or []:
                pid = item["productId"]
                qty = int(item.get("quantity") or 0)
                async with get_lock(f"stock:{pid}"):
                    await self.inventory_repo.restock(pid, qty)
            order["status"] = CANCELLED
            order["timeline"].append(
                {"status": CANCELLED, "time": now,
                 "action": f"取消: {reason}"})
            order["updatedAt"] = now
            await self.repo.save_order(order)
            logger.info("xz_order_cancelled order=%s "
                        "stock_restored=%s",
                        order_id, len(order.get("items") or []))
            return order

    # ============================================================
    # 状态流转(发货/确认收货/评价——独立实现, 不耦合主站)
    # ============================================================

    async def ship_order(self, operator_id: int, order_id: str,
                         carrier: str, waybill_no: str) -> dict:
        """发货 PAID→SHIPPED(商家侧——X-Member-Id 归属店铺校验)

        Raises:
            KeyError: 订单不存在
            PermissionError: 非店铺归属商家
            ValueError: 状态非 PAID / 参数缺失
        """
        carrier = (carrier or "").strip()
        waybill_no = (waybill_no or "").strip()
        if not carrier or not waybill_no:
            raise ValueError("承运商与运单号必填")
        async with get_lock(f"xz:order:{order_id}"):
            order = await self.repo.get_order(order_id)
            if order is None:
                raise KeyError(
                    f"订单不存在(orderId={order_id})")
            if operator_id != order.get("shopMemberId"):
                raise PermissionError(
                    "仅店铺归属商家可发货")
            if order.get("status") != PAID:
                raise ValueError(
                    f"订单状态异常: 仅 {PAID} 可发货, "
                    f"当前 {order.get('status')}")
            now = ts()
            order["status"] = SHIPPED
            order["logistics"] = {
                "carrier": carrier,
                "waybillNo": waybill_no,
                "shippedAt": now,
                "signedAt": "",
            }
            order["timeline"].append(
                {"status": SHIPPED, "time": now,
                 "action": f"已发货 {carrier} {waybill_no}"})
            order["updatedAt"] = now
            await self.repo.save_order(order)
            return order

    async def confirm_order(self, member_id: int,
                            order_id: str) -> dict:
        """确认收货 SHIPPED→RECEIVED

        Raises:
            KeyError: 订单不存在
            PermissionError: 非订单买家
            ValueError: 状态非 SHIPPED
        """
        async with get_lock(f"xz:order:{order_id}"):
            order = await self.repo.get_order(order_id)
            if order is None:
                raise KeyError(
                    f"订单不存在(orderId={order_id})")
            if order.get("memberId") != member_id:
                raise PermissionError("仅订单买家可确认收货")
            if order.get("status") != SHIPPED:
                raise ValueError(
                    f"订单状态异常: 仅 {SHIPPED} 可确认收货, "
                    f"当前 {order.get('status')}")
            now = ts()
            order["status"] = RECEIVED
            order["logistics"]["signedAt"] = now
            order["timeline"].append(
                {"status": RECEIVED, "time": now,
                 "action": "确认收货"})
            order["updatedAt"] = now
            await self.repo.save_order(order)
            return order

    async def review_order(self, member_id: int, order_id: str,
                           rating: int, content: str = "") -> dict:
        """评价 RECEIVED→COMPLETED(信值回流留痕)

        信值回流口径: 购物行为仅记入订单 review 字段留痕;
        信值加分是 68号既有信值体系(雷达/等级)的职责,
        本期不做自动加分——永不越权重写信值。

        Raises:
            KeyError: 订单不存在
            PermissionError: 非订单买家
            ValueError: 状态非 RECEIVED / 评分越界
        """
        rating = int(rating or 0)
        if not (1 <= rating <= 5):
            raise ValueError("评分须为 1-5")
        async with get_lock(f"xz:order:{order_id}"):
            order = await self.repo.get_order(order_id)
            if order is None:
                raise KeyError(
                    f"订单不存在(orderId={order_id})")
            if order.get("memberId") != member_id:
                raise PermissionError("仅订单买家可评价")
            if order.get("status") != RECEIVED:
                raise ValueError(
                    f"订单状态异常: 仅 {RECEIVED} 可评价, "
                    f"当前 {order.get('status')}")
            now = ts()
            order["status"] = COMPLETED
            order["review"] = {
                "rating": rating,
                "content": (content or "")[:500],
                "reviewedAt": now,
                "creditNote": (
                    "购物行为信值回流留痕——加分由 68号既有"
                    "信值体系处理, 本期不自动加分"),
            }
            order["timeline"].append(
                {"status": COMPLETED, "time": now,
                 "action": f"评价 {rating} 星"})
            order["updatedAt"] = now
            await self.repo.save_order(order)
            return order
