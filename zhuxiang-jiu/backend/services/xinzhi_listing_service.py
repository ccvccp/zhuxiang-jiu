"""68号 P7·信值·臻选——店铺铺货服务(平台化升级)

依据:《68号 创新规划方案》§三 P7 + 任务书《信值·臻选 68号平台化升级》

核心命题:**四道门禁的确定性铺货**——商家从主站商品池只读选品
(product_repository 零回写), 逐道门禁检查留痕, admin 审核通过
方可上架; 信值价快照复用 P2 price_breakdown 试算缓存。

四道门禁(全确定性, LLM 禁入):
    ① qualification 商家资质: P4 商家档案四必查(主体资质/履约
       能力/服务保障/后台系统)全通过
    ② trace 溯源: 主站商品存在 + 库存>0 + 溯源批次字段存在且
       trace_prod 批次已放行(released)——酒类铁律
    ③ compliance 合规: 商品名不含违禁词(确定性词表, 40号口径
       + 广告法极限词)
    ④ primeScore 信值分: 店铺主信值雷达总分(68号信值分)≥40
       ——与 P6 雷达预警线同口径

铺货状态机: draft→reviewing→listed⇄delisted→removed
    - submit: 四门禁逐道检查留痕; 全过→reviewing, 任一失败→draft
    - review(admin): 四门禁复核 + approve(→listed)/reject(→removed),
      建议书模式(审核结果带 disposition)
    - delist/list: 商家自管理(仅 listed⇄delisted)
    - remove(admin): 违规移除(处置类, disposition 留痕)

红线(宪法域):
    - 主站商品池只读: 选品/验价均为读取, 永不回写 product_repository
    - 每店每商品一条(唯一性, removed 后可重新铺货)
    - 审核结果带 disposition, 门禁明细全留痕(可解释)
    - 同输入同输出: 门禁全为确定性规则
"""

import logging
from datetime import datetime, UTC

from services.xinzhi_shop_service import (
    _XzStore,
    SHOP_STATUS_SIGNED, SHOP_STATUS_PROBATION,
    SHOP_STATUS_ACTIVE,
)

logger = logging.getLogger(__name__)

MODEL_VERSION = "v1-xinzhi-listing"


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


# ============================================================
# P7 常量(任务书口径)
# ============================================================

# 可铺货店铺状态(签约后即可备货; 暂停/终态不可铺新货)
LISTING_SHOP_STATUSES = (SHOP_STATUS_SIGNED, SHOP_STATUS_PROBATION,
                         SHOP_STATUS_ACTIVE)

# 铺货状态机
LISTING_STATUS_DRAFT = "draft"          # 草稿(门禁未全过, 留痕)
LISTING_STATUS_REVIEWING = "reviewing"  # 待 admin 审核
LISTING_STATUS_LISTED = "listed"        # 已上架
LISTING_STATUS_DELISTED = "delisted"    # 商家下架
LISTING_STATUS_REMOVED = "removed"      # 平台移除/审核拒绝(终态)
LISTING_STATUSES = (
    LISTING_STATUS_DRAFT, LISTING_STATUS_REVIEWING,
    LISTING_STATUS_LISTED, LISTING_STATUS_DELISTED,
    LISTING_STATUS_REMOVED,
)
LISTING_TRANSITIONS = {
    LISTING_STATUS_DRAFT: (LISTING_STATUS_REVIEWING,
                           LISTING_STATUS_REMOVED),
    LISTING_STATUS_REVIEWING: (LISTING_STATUS_LISTED,
                               LISTING_STATUS_REMOVED),
    LISTING_STATUS_LISTED: (LISTING_STATUS_DELISTED,
                            LISTING_STATUS_REMOVED),
    LISTING_STATUS_DELISTED: (LISTING_STATUS_LISTED,
                              LISTING_STATUS_REMOVED),
    LISTING_STATUS_REMOVED: (),   # 终态(可重新铺货, 新建记录)
}
LISTING_STATUS_LABELS = {
    LISTING_STATUS_DRAFT: "草稿(门禁未全过)",
    LISTING_STATUS_REVIEWING: "待审核(门禁已过)",
    LISTING_STATUS_LISTED: "已上架",
    LISTING_STATUS_DELISTED: "商家下架",
    LISTING_STATUS_REMOVED: "已移除(终态)",
}

# ============================================================
# 四道门禁常量
# ============================================================

GATE_QUALIFICATION = "qualification"   # 商家资质门禁
GATE_TRACE = "trace"                   # 溯源门禁
GATE_COMPLIANCE = "compliance"         # 合规门禁
GATE_PRIME = "primeScore"              # 信值分门禁
LISTING_GATES = (GATE_QUALIFICATION, GATE_TRACE,
                 GATE_COMPLIANCE, GATE_PRIME)
GATE_LABELS = {
    GATE_QUALIFICATION: "商家资质门禁",
    GATE_TRACE: "溯源门禁",
    GATE_COMPLIANCE: "合规门禁",
    GATE_PRIME: "信值分门禁",
}

# 合规违禁词(确定性词表——40号风险词口径+广告法极限词+酒类营销禁语)
LISTING_BANNED_WORDS = (
    "假货", "仿冒", "三无", "原单", "复刻",
    "治病", "疗效", "药用", "保健功效", "壮阳",
    "国家级", "最高级", "最佳", "特效",
)

# 信值分门槛(店铺主雷达总分——与 P6 雷达预警线同口径)
PRIME_SCORE_MIN = 40

# 溯源批次放行状态(trace_prod 口径)
TRACE_BATCH_RELEASED = "released"

# 货架聚合
SHELF_DEFAULT_LIMIT = 20


class XinzhiListingService:
    """68号 P7·店铺铺货(四门禁/信值价快照/上下架/货架聚合)"""

    def __init__(self, store: _XzStore = None):
        self.store = store if store is not None else _XzStore()

    # --------------------------------------------------------
    # 内部辅助
    # --------------------------------------------------------

    @staticmethod
    def _transition(listing: dict, target: str) -> None:
        """状态转移校验(转移表外一律 ValueError→409)"""
        current = listing.get("status")
        allowed = LISTING_TRANSITIONS.get(current, ())
        if target not in allowed:
            raise ValueError(
                f"铺货状态转移非法({current}→{target}, "
                f"允许:{'/'.join(allowed) or '终态'})")

    async def _require_my_listing(self, listing_id: int,
                                  member_id: int) -> dict:
        """加载铺货并校验归属(非本店资源 → PermissionError)"""
        listing = await self.store.get_listing(listing_id)
        if listing is None:
            raise KeyError(
                f"铺货不存在(listingId={listing_id})")
        if listing.get("memberId") != member_id:
            raise PermissionError(
                f"非本店资源(listingId={listing_id} 归属会员"
                f"{listing.get('memberId')})")
        return listing

    # --------------------------------------------------------
    # ① 四道门禁(逐道检查, 结果留痕)
    # --------------------------------------------------------

    async def check_gates(self, shop: dict,
                          product: dict) -> dict:
        """四道门禁逐道检查(确定性——同输入同输出)

        Returns:
            {qualification/trace/compliance/primeScore:
             {passed, detail}}
        """
        return {
            GATE_QUALIFICATION: await self._gate_qualification(
                shop),
            GATE_TRACE: await self._gate_trace(product),
            GATE_COMPLIANCE: self._gate_compliance(product),
            GATE_PRIME: await self._gate_prime(shop),
        }

    async def _gate_qualification(self, shop: dict) -> dict:
        """门禁① 商家资质(P4 商家档案四必查)"""
        from repositories.xinzhi_repository import (
            XinzhiRepository)
        from services.xinzhi_merchant_service import (
            REQUIRED_CHECKS, CHECK_LABELS)
        merchant = await XinzhiRepository(
        ).find_merchant_by_member(shop.get("memberId"))
        if merchant is None:
            return {"passed": False,
                    "detail": "无商家档案(P4 4+2 认证缺失)"}
        if not merchant.get("certified"):
            return {"passed": False,
                    "detail": "商家档案未认证(四必查未通过)"}
        checks = merchant.get("checks") or {}
        missing = [CHECK_LABELS.get(c, c) for c in REQUIRED_CHECKS
                   if not checks.get(c)]
        if missing:
            return {"passed": False,
                    "detail": f"四必查未过({'/'.join(missing)})"}
        return {"passed": True, "detail": "四必查全过(4/4)"}

    async def _gate_trace(self, product: dict) -> dict:
        """门禁② 溯源(商品存在+库存>0+溯源字段+批次已放行)

        商品存在性由 submit 前置校验(KeyError→404);
        本门禁校验库存与溯源留痕(酒类须 trace_prod 已放行批次)。
        """
        stock = int(product.get("stock") or 0)
        if stock <= 0:
            return {"passed": False,
                    "detail": f"库存不足(stock={stock})"}
        batch_no = str(product.get("traceBatchNo") or "").strip()
        if not batch_no:
            return {"passed": False,
                    "detail": "无溯源批次字段(traceBatchNo 缺失)"}
        from repositories.trace_prod_repository import (
            TraceProdRepository)
        batch = await TraceProdRepository().get_batch(batch_no)
        if batch is None:
            return {"passed": False,
                    "detail": f"溯源批次不存在({batch_no})"}
        status = str(batch.get("status") or "")
        if status != TRACE_BATCH_RELEASED:
            return {"passed": False,
                    "detail": f"溯源批次未放行(状态{status})"}
        return {"passed": True,
                "detail": f"已放行批次({batch_no})"}

    @staticmethod
    def _gate_compliance(product: dict) -> dict:
        """门禁③ 合规(商品名违禁词确定性词表)"""
        name = str(product.get("name") or "")
        hit = next((w for w in LISTING_BANNED_WORDS if w in name),
                   None)
        if hit:
            return {"passed": False,
                    "detail": f"违禁词命中({hit})"}
        return {"passed": True, "detail": "商品名无违禁词"}

    async def _gate_prime(self, shop: dict) -> dict:
        """门禁④ 信值分(店铺主雷达总分≥40——P6 预警线同口径)"""
        from services.xinzhi_radar_service import (
            XinzhiRadarService)
        radar = await XinzhiRadarService().get_radar(
            shop.get("memberId"))
        score = round(float(radar.get("totalScore") or 0), 1)
        if score < PRIME_SCORE_MIN:
            return {"passed": False,
                    "detail": f"店铺信值分不足({score}<"
                              f"{PRIME_SCORE_MIN})"}
        return {"passed": True,
                "detail": f"店铺信值分 {score}(≥{PRIME_SCORE_MIN})"}

    # --------------------------------------------------------
    # ② 信值价快照(P2 price_breakdown 试算缓存)
    # --------------------------------------------------------

    async def _price_snapshot(self, product: dict,
                              member_id: int = None) -> dict:
        """信值价快照(price_breakdown 试算缓存, 含 α 抵扣明细)

        有会员上下文 → 该会员雷达等级 α; 无会员上下文/试算失败
        → 通用档(α=0, 等级 C 无抵扣基准)。
        """
        pid = product.get("product_id")
        if member_id:
            try:
                from services.xinzhi_pricing_service import (
                    XinzhiPricingService)
                detail = await XinzhiPricingService(
                ).price_breakdown(member_id, pid)
                return {
                    "tier": "member",
                    "grade": detail.get("grade"),
                    "basePrice": detail.get("basePrice"),
                    "afterThreeFactor": detail.get(
                        "afterThreeFactor"),
                    "xinzhiAlpha": detail.get("xinzhiAlpha"),
                    "xinzhiCredit": detail.get("xinzhiCredit"),
                    "finalPrice": detail.get("finalPrice"),
                    "breakdownLine": detail.get("breakdownLine"),
                    "pricedAt": detail.get("pricedAt"),
                }
            except (KeyError, ValueError) as exc:
                logger.warning("xz_p7_price_fallback pid=%s: %s",
                               pid, exc)
        # 通用档: α=0(等级 C 无抵扣基准——三因子价)
        base = round(float(product.get("price") or 0), 2)
        from services.pay60_registry import compute_price
        three = compute_price(base, tier="standard",
                              compliance_months=0,
                              promo_factor=1.0)
        after = float(three["finalPrice"])
        line = (f"原价¥{base} - 信值抵扣¥0(通用档 α=0)"
                f" - 三因子折扣¥{round(base - after, 2)}"
                f" = 实付¥{after}")
        return {
            "tier": "generic",
            "grade": "C",
            "basePrice": base,
            "afterThreeFactor": after,
            "xinzhiAlpha": 0.0,
            "xinzhiCredit": 0.0,
            "finalPrice": after,
            "breakdownLine": line,
            "pricedAt": _now_iso(),
        }

    # --------------------------------------------------------
    # ③ 铺货发布(商家从主站商品池选品)
    # --------------------------------------------------------

    async def submit(self, member_id: int,
                     product_id: str) -> dict:
        """铺货发布(主站商品池只读选品→四门禁→draft/reviewing)

        Raises:
            ValueError: 无可铺货店铺/店铺状态不可/重复铺货
            KeyError: 商品不存在
        """
        shop = await self.store.find_shop_by_member(
            member_id, statuses=LISTING_SHOP_STATUSES)
        if shop is None:
            any_shop = await self.store.find_shop_by_member(
                member_id)
            if any_shop is None:
                raise ValueError(
                    f"尚未开设角色店铺(memberId={member_id}, "
                    "先经 P6 开店申请)")
            raise ValueError(
                f"店铺状态不可铺货(当前{any_shop.get('status')}, "
                f"须为{'/'.join(LISTING_SHOP_STATUSES)})")
        from repositories.product_repository import (
            ProductRepository)
        product = await ProductRepository().get_by_id(product_id)
        if product is None:
            raise KeyError(f"商品不存在(productId={product_id})")
        dup = await self.store.find_listing_by_shop_product(
            shop["shopId"], product_id)
        if dup is not None:
            raise ValueError(
                f"每店每商品仅一条铺货(listingId="
                f"{dup.get('listingId')}, "
                f"状态{dup.get('status')})")
        gates = await self.check_gates(shop, product)
        passed = all(g.get("passed")
                     for g in gates.values())
        price = await self._price_snapshot(product, member_id)
        listing_id = await self.store.next_id("listing")
        now = _now_iso()
        listing = {
            "listingId": listing_id,
            "shopId": shop["shopId"],
            "memberId": member_id,
            "shopName": shop.get("shopName"),
            "productId": product_id,
            "productName": product.get("name", ""),
            "category": product.get("series", ""),
            "sourcePrice": round(
                float(product.get("price") or 0), 2),
            "status": (LISTING_STATUS_REVIEWING if passed
                       else LISTING_STATUS_DRAFT),
            "gates": gates,
            "gatesPassed": passed,
            "gatesCheckedAt": now,
            "xinzhiPrice": price,
            "reviews": [],
            "listedAt": "",
            "delistedAt": "",
            "removedAt": "",
            "createdAt": now,
            "updatedAt": now,
        }
        await self.store.save_listing(listing)
        logger.info("xz_p7_submit shop=%s pid=%s passed=%s "
                    "status=%s", shop["shopId"], product_id,
                    passed, listing["status"])
        return listing

    # --------------------------------------------------------
    # ④ 查询视图
    # --------------------------------------------------------

    async def my_listings(self, member_id: int,
                          limit: int = 200) -> list[dict]:
        """我的铺货(商家维度, 最新优先)"""
        return await self.store.list_listings(
            member_id=member_id, limit=limit)

    async def get_listing(self, listing_id: int) -> dict:
        """铺货详情(完整记录——含门禁明细; 归属校验由路由层做)

        Raises:
            KeyError: 铺货不存在
        """
        listing = await self.store.get_listing(listing_id)
        if listing is None:
            raise KeyError(
                f"铺货不存在(listingId={listing_id})")
        return listing

    async def admin_list(self, status: str = None,
                         limit: int = 200) -> list[dict]:
        """铺货列表(admin; 可按状态过滤)"""
        return await self.store.list_listings(status=status,
                                              limit=limit)

    async def shop_listings(self, shop_id: int,
                            limit: int = 200) -> dict:
        """店铺商品(公开——listed/delisted 透明可见)

        Raises:
            KeyError: 店铺不存在
        """
        shop = await self.store.get_shop(shop_id)
        if shop is None:
            raise KeyError(f"店铺不存在(shopId={shop_id})")
        rows = []
        for l in await self.store.list_listings(
                shop_id=shop_id, limit=limit * 5):
            if l.get("status") not in (LISTING_STATUS_LISTED,
                                       LISTING_STATUS_DELISTED):
                continue
            rows.append(self._public_item(l))
        return {
            "shopId": shop_id,
            "shopName": shop.get("shopName"),
            "shopLevel": shop.get("shopLevel"),
            "count": len(rows),
            "items": rows,
        }

    @staticmethod
    def _public_item(listing: dict) -> dict:
        """货架/店铺商品公开条目(信值价快照展平)"""
        price = listing.get("xinzhiPrice") or {}
        return {
            "listingId": listing.get("listingId"),
            "productId": listing.get("productId"),
            "productName": listing.get("productName"),
            "category": listing.get("category"),
            "shopId": listing.get("shopId"),
            "shopName": listing.get("shopName"),
            "sourcePrice": listing.get("sourcePrice"),
            "xinzhiFinalPrice": price.get("finalPrice"),
            "xinzhiAlpha": price.get("xinzhiAlpha"),
            "xinzhiCredit": price.get("xinzhiCredit"),
            "breakdownLine": price.get("breakdownLine"),
            "status": listing.get("status"),
            "listedAt": listing.get("listedAt"),
        }

    async def shelf(self, category: str = None,
                    limit: int = SHELF_DEFAULT_LIMIT) -> dict:
        """公开货架(listed 按 category=主站商品系列聚合+店铺名)

        对齐 xinzhi prime 货架风格(信值加权排序此处以信值价
        快照展示——排序按上架时间新者优先)。
        """
        groups: dict = {}
        total = 0
        for l in await self.store.list_listings(
                status=LISTING_STATUS_LISTED, limit=1000):
            cat = str(l.get("category") or "未分类")
            if category and cat != category:
                continue
            groups.setdefault(cat, []).append(
                self._public_item(l))
            total += 1
        categories = []
        for cat, items in groups.items():
            items.sort(key=lambda x: str(x.get("listedAt") or ""),
                       reverse=True)
            categories.append({
                "category": cat,
                "count": len(items),
                "items": items[:limit],
            })
        categories.sort(key=lambda c: (-c["count"], c["category"]))
        return {"total": total, "categories": categories}

    def gates_dict(self) -> dict:
        """门禁字典(四门禁规则公示——公开口径)"""
        return {
            "gates": [
                {"key": GATE_QUALIFICATION,
                 "label": GATE_LABELS[GATE_QUALIFICATION],
                 "rule": "商家档案四必查(主体资质/履约能力/"
                         "服务保障/后台系统)全通过",
                 "threshold": "4/4"},
                {"key": GATE_TRACE,
                 "label": GATE_LABELS[GATE_TRACE],
                 "rule": "主站商品存在+库存>0+溯源批次字段存在"
                         "且 trace_prod 批次已放行(released)",
                 "threshold": TRACE_BATCH_RELEASED},
                {"key": GATE_COMPLIANCE,
                 "label": GATE_LABELS[GATE_COMPLIANCE],
                 "rule": "商品名不含违禁词(确定性词表: "
                         + "/".join(LISTING_BANNED_WORDS) + ")",
                 "threshold": "0 命中"},
                {"key": GATE_PRIME,
                 "label": GATE_LABELS[GATE_PRIME],
                 "rule": "店铺主信值雷达总分(68号信值分)≥40"
                         "(与 P6 雷达预警线同口径)",
                 "threshold": PRIME_SCORE_MIN},
            ],
            "statuses": [
                {"code": s, "label": LISTING_STATUS_LABELS[s],
                 "allowedTransitions": list(
                     LISTING_TRANSITIONS[s])}
                for s in LISTING_STATUSES],
        }

    # --------------------------------------------------------
    # ⑤ admin 审核(四门禁复核 + 建议书)
    # --------------------------------------------------------

    async def review_listing(self, listing_id: int,
                             approved: bool, note: str = "",
                             reviewer: str = "admin") -> dict:
        """铺货审核(四门禁复核+approve→listed/reject→removed)

        建议书模式: 审核结果带 disposition(人工执行留痕)。

        Raises:
            KeyError: 铺货/店铺/商品不存在
            ValueError: 状态非法(非 reviewing)/门禁复核未过
        """
        listing = await self.store.get_listing(listing_id)
        if listing is None:
            raise KeyError(
                f"铺货不存在(listingId={listing_id})")
        if listing.get("status") != LISTING_STATUS_REVIEWING:
            raise ValueError(
                f"铺货状态非法(当前{listing.get('status')}, "
                f"须为{LISTING_STATUS_REVIEWING})")
        shop = await self.store.get_shop(listing.get("shopId"))
        if shop is None:
            raise KeyError(
                f"店铺不存在(shopId={listing.get('shopId')})")
        from repositories.product_repository import (
            ProductRepository)
        product = await ProductRepository().get_by_id(
            listing.get("productId"))
        if product is None:
            raise KeyError(
                f"商品不存在(productId={listing.get('productId')})")
        # 四门禁复核(留痕更新)
        recheck = await self.check_gates(shop, product)
        recheck_passed = all(g.get("passed")
                             for g in recheck.values())
        if approved and not recheck_passed:
            failed = [GATE_LABELS.get(k, k) for k, g in
                      recheck.items() if not g.get("passed")]
            raise ValueError(
                f"四门禁复核未过({'/'.join(failed)}), "
                "不可上架")
        target = (LISTING_STATUS_LISTED if approved
                  else LISTING_STATUS_REMOVED)
        self._transition(listing, target)
        now = _now_iso()
        decision = "approve" if approved else "reject"
        listing["reviews"] = list(listing.get("reviews") or [])
        listing["reviews"].append({
            "reviewer": reviewer, "decision": decision,
            "note": (note or "")[:200],
            "gatesPassed": recheck_passed, "at": now})
        listing["status"] = target
        listing["gates"] = recheck
        listing["gatesPassed"] = recheck_passed
        listing["gatesCheckedAt"] = now
        if approved:
            listing["listedAt"] = now
        else:
            listing["removedAt"] = now
        listing["updatedAt"] = now
        await self.store.save_listing(listing)
        disposition = {
            "kind": "listing_review",
            "decision": decision,
            "executed": True,
            "executedBy": reviewer,
            "gatesRecheckPassed": recheck_passed,
            "note": (note or "") or (
                "四门禁复核通过, 上架" if approved
                else "人工审核拒绝, 移除(终态)"),
            "at": now,
        }
        logger.info("xz_p7_review listing=%s %s by=%s",
                    listing_id, decision, reviewer)
        return {**listing, "disposition": disposition}

    # --------------------------------------------------------
    # ⑥ 上下架(商家自管理——仅 listed⇄delisted)
    # --------------------------------------------------------

    async def delist(self, listing_id: int,
                     member_id: int) -> dict:
        """商家下架(listed→delisted)

        Raises:
            KeyError: 铺货不存在
            PermissionError: 非本店资源
            ValueError: 状态非法
        """
        listing = await self._require_my_listing(listing_id,
                                                 member_id)
        self._transition(listing, LISTING_STATUS_DELISTED)
        now = _now_iso()
        listing["status"] = LISTING_STATUS_DELISTED
        listing["delistedAt"] = now
        listing["updatedAt"] = now
        await self.store.save_listing(listing)
        return listing

    async def relist(self, listing_id: int,
                     member_id: int) -> dict:
        """商家重新上架(delisted→listed)

        Raises:
            KeyError: 铺货不存在
            PermissionError: 非本店资源
            ValueError: 状态非法
        """
        listing = await self._require_my_listing(listing_id,
                                                 member_id)
        self._transition(listing, LISTING_STATUS_LISTED)
        now = _now_iso()
        listing["status"] = LISTING_STATUS_LISTED
        listing["listedAt"] = now
        listing["updatedAt"] = now
        await self.store.save_listing(listing)
        return listing

    # --------------------------------------------------------
    # ⑦ 平台移除(admin——处置类, disposition 留痕)
    # --------------------------------------------------------

    async def remove_listing(self, listing_id: int,
                             reason: str = "",
                             operator: str = "admin") -> dict:
        """平台移除(违规——listed/delisted/reviewing/draft→removed)

        Raises:
            KeyError: 铺货不存在
            ValueError: 状态非法(已 removed)
        """
        listing = await self.store.get_listing(listing_id)
        if listing is None:
            raise KeyError(
                f"铺货不存在(listingId={listing_id})")
        self._transition(listing, LISTING_STATUS_REMOVED)
        now = _now_iso()
        listing["status"] = LISTING_STATUS_REMOVED
        listing["removedAt"] = now
        listing["updatedAt"] = now
        await self.store.save_listing(listing)
        disposition = {
            "kind": "listing_remove",
            "decision": "remove",
            "executed": True,
            "executedBy": operator,
            "note": (reason or "")[:200] or "违规移除(admin 确认)",
            "at": now,
        }
        logger.warning("xz_p7_remove listing=%s by=%s reason=%s",
                       listing_id, operator, reason)
        return {**listing, "disposition": disposition}
