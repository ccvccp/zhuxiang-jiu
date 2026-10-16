# -*- coding: utf-8 -*-
"""站内内容社区 MVP(蓝图对照分析近期项③)

定位:
    - sv73 六平台发布内容站内化 feed(读 36号 content store 已发布态,
      倒序分页透出, /r/ 短码直接携带)
    - 管理员商品挂载(content ↔ product 映射, feed join 商品快照,
      不改 36号 content 本体结构)
    - UGC 评测帖(对内容/商品发帖, 独立于 product_service 产品评价区;
      先审后显铁律——pending 永不直接公开, 对齐 sv73 三审/
      知识库 review-publish 纪律)

fail-soft 约定:
    - feed join 挂载/商品查询失败 → product=None 不崩
    - bind_product 校验 content/product 存在为 best-effort warning 不硬拒

异常约定(对齐 promo/product service):
    - KeyError → 404(帖子不存在)
    - ValueError → 409(参数校验失败/重复审核)
"""

import logging

from repositories.community_repository import (
    CommunityRepository,
    POST_STATUS_PENDING, POST_STATUS_APPROVED, POST_STATUS_REJECTED,
    POST_TARGET_CONTENT, POST_TARGET_PRODUCT, POST_TARGET_TYPES,
)
from repositories.promo_repository import (
    PromoRepository, CONTENT_STATUS_PUBLISHED,
)
from repositories.product_repository import ProductRepository

logger = logging.getLogger(__name__)

# feed 默认分页
FEED_DEFAULT_PAGE = 1
FEED_DEFAULT_PAGE_SIZE = 10
FEED_MAX_PAGE_SIZE = 50
# feed 拉取上限(与 sv73 _find_content_by_script 的 list 上限同量级)
FEED_SCAN_LIMIT = 500
# feed 正文摘要长度
FEED_BODY_SUMMARY_LEN = 120
# UGC 帖正文长度区间
POST_TEXT_MAX_LEN = 1000


def _now_iso() -> str:
    """微秒精度时间戳(保证同秒内多帖 createdAt 可稳定倒序)"""
    from datetime import datetime, UTC
    return datetime.now(UTC).isoformat(timespec="microseconds")


class CommunityService:
    """站内内容社区业务服务"""

    def __init__(self, repo: CommunityRepository = None,
                 promo_repo: PromoRepository = None,
                 product_repo: ProductRepository = None):
        self.repo = repo or CommunityRepository()
        self.promo_repo = promo_repo or PromoRepository()
        self.product_repo = product_repo or ProductRepository()

    # ============================================================
    # C 端: 站内化内容 feed(已发布态 + 商品挂载 join)
    # ============================================================

    async def feed(self, page: int = FEED_DEFAULT_PAGE,
                   page_size: int = FEED_DEFAULT_PAGE_SIZE,
                   platform: str = None) -> dict:
        """sv73 产物站内化 feed

        读 36号 content store 中已发布态(CONTENT_STATUS_PUBLISHED)内容,
        按 publishedAt(缺省 createdAt)倒序分页; join 挂载映射 → 商品快照
        (name/price/图片, best-effort——商品缺失时 product=None 不崩)。
        无内容/读取异常返回空 feed 不报错。
        """
        if page < 1:
            page = FEED_DEFAULT_PAGE
        if page_size < 1 or page_size > FEED_MAX_PAGE_SIZE:
            page_size = FEED_DEFAULT_PAGE_SIZE

        try:
            contents = await self.promo_repo.list_contents(
                platform=platform, status=CONTENT_STATUS_PUBLISHED,
                limit=FEED_SCAN_LIMIT)
        except Exception as exc:   # fail-soft: content store 不可用不崩
            logger.warning("community feed 读取 content store 失败: %s", exc)
            contents = []

        # 倒序: publishedAt 优先, 缺省回落 createdAt
        contents = sorted(
            contents,
            key=lambda c: (c.get("publishedAt") or c.get("createdAt") or ""),
            reverse=True)
        total = len(contents)
        start = (page - 1) * page_size
        page_contents = contents[start:start + page_size]

        items = []
        for content in page_contents:
            content_id = content.get("contentId")
            binding = None
            try:
                binding = await self.repo.get_content_product(content_id)
            except Exception as exc:   # fail-soft: 挂载查询失败不崩
                logger.warning("feed 挂载查询失败(contentId=%s): %s",
                               content_id, exc)
            product = None
            if binding:
                product = await self._product_snapshot(
                    binding.get("productId"))
            body = (content.get("body") or "").strip()
            items.append({
                "contentId": content_id,
                "title": content.get("title") or "",
                "bodySummary": body[:FEED_BODY_SUMMARY_LEN],
                "hashtags": content.get("hashtags") or "",
                "coverHint": content.get("coverHint") or "",
                "platform": content.get("platform") or "",
                "publishedAt": content.get("publishedAt")
                                or content.get("createdAt") or "",
                "shortCode": content.get("shortCode") or "",
                "product": product,
            })

        return {
            "items": items,
            "page": page,
            "pageSize": page_size,
            "total": total,
            "hasMore": start + page_size < total,
        }

    async def _product_snapshot(self, product_id) -> dict | None:
        """商品快照(productId/name/price/图片, best-effort 不崩)"""
        if product_id in (None, ""):
            return None
        try:
            product = await self.product_repo.get_by_id(str(product_id))
        except Exception as exc:   # fail-soft: 商品查询失败不崩
            logger.warning("feed 商品快照查询失败(productId=%s): %s",
                           product_id, exc)
            return None
        if not product:
            return None
        return {
            "productId": product.get("product_id"),
            "name": product.get("name") or "",
            "price": product.get("price"),
            "image": (product.get("images") or {}).get("main", ""),
        }

    # ============================================================
    # admin: 内容挂载商品
    # ============================================================

    async def bind_product(self, content_id, product_id) -> dict:
        """内容挂载商品(admin; 幂等 upsert: 同 contentId 覆盖 productId)

        content/product 存在校验 best-effort——缺失仅 warning 不硬拒
        (挂载可先于产物补齐, feed join 时自然回落 product=None)。
        """
        warnings = []
        try:
            content = await self.promo_repo.get_content(content_id)
            if not content:
                warnings.append(f"content {content_id} 不在 36号内容库(仍允许挂载)")
        except Exception as exc:
            warnings.append(f"content 存在校验异常(仍允许挂载): {exc}")
        try:
            product = await self.product_repo.get_by_id(str(product_id))
            if not product:
                warnings.append(f"product {product_id} 不在商品库(仍允许挂载)")
        except Exception as exc:
            warnings.append(f"product 存在校验异常(仍允许挂载): {exc}")

        binding = await self.repo.upsert_content_product(content_id,
                                                         product_id)
        for w in warnings:
            logger.warning("bind_product: %s", w)
        return {"ok": True, "binding": binding, "warnings": warnings}

    # ============================================================
    # C 端: UGC 评测帖(先审后显)
    # ============================================================

    async def create_post(self, member_id, nickname, target_type,
                          target_id, text, rating=None) -> dict:
        """发帖(登录态; 落 pending 待审, 永不直接公开)

        Raises:
            ValueError: 参数校验失败(target_type 域外/正文长度/
                rating 越界/身份缺失)
        """
        if not member_id:
            raise ValueError("缺少会员身份(memberId)")
        if target_type not in POST_TARGET_TYPES:
            raise ValueError(
                f"targetType 须为 {'/'.join(POST_TARGET_TYPES)}, 当前 {target_type}")
        if target_id in (None, ""):
            raise ValueError("缺少评测对象(targetId)")
        cleaned = (text or "").strip()
        if not cleaned or len(cleaned) > POST_TEXT_MAX_LEN:
            raise ValueError(
                f"正文长度须在 1-{POST_TEXT_MAX_LEN} 字之间, 当前 {len(cleaned)}")
        if rating is not None:
            if isinstance(rating, bool) or not isinstance(rating, int) \
                    or not 1 <= rating <= 5:
                raise ValueError(f"rating 须为 1-5 整数或留空, 当前 {rating}")

        post = {
            "postId": await self.repo.next_id("post"),
            "memberId": member_id,
            "nickname": (nickname or "").strip() or "竹香会员",
            "targetType": target_type,
            "targetId": target_id,
            "text": cleaned,
            "rating": rating,
            "status": POST_STATUS_PENDING,
            "createdAt": _now_iso(),
            "reviewedAt": "",
        }
        return await self.repo.save_post(post)

    async def list_posts(self, target_type: str = None, target_id=None,
                         page: int = 1, page_size: int = 10) -> dict:
        """C 端帖子列表(只显 approved, 先审后显铁律)"""
        if page < 1:
            page = 1
        if page_size < 1 or page_size > FEED_MAX_PAGE_SIZE:
            page_size = 10
        posts = await self.repo.list_posts(
            status=POST_STATUS_APPROVED, target_type=target_type,
            target_id=target_id, limit=FEED_SCAN_LIMIT)
        total = len(posts)
        start = (page - 1) * page_size
        return {
            "items": posts[start:start + page_size],
            "page": page,
            "pageSize": page_size,
            "total": total,
            "hasMore": start + page_size < total,
        }

    # ============================================================
    # admin: 帖子审核(pending → approved/rejected)
    # ============================================================

    async def pending_posts(self, limit: int = 50) -> list[dict]:
        """待审帖列表(admin)"""
        return await self.repo.list_posts(status=POST_STATUS_PENDING,
                                          limit=limit)

    async def review_post(self, post_id, approve: bool,
                          operator: str = "") -> dict:
        """审核流转(admin; 幂等——非 pending 拒绝重复审)

        Raises:
            KeyError: 帖子不存在
            ValueError: 帖子已审(重复审核)
        """
        post = await self.repo.get_post(post_id)
        if not post:
            raise KeyError(f"帖子 {post_id} 不存在")
        if post.get("status") != POST_STATUS_PENDING:
            raise ValueError(
                f"帖子 {post_id} 已审过(status={post.get('status')}), 拒绝重复审核")
        post["status"] = (POST_STATUS_APPROVED if approve
                          else POST_STATUS_REJECTED)
        post["reviewedAt"] = _now_iso()
        post["operator"] = operator or "admin"
        return await self.repo.save_post(post)
