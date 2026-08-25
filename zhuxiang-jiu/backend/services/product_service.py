"""产品展示业务服务

功能:
    - 分类导航(返回分类树)
    - 产品列表(筛选 series/alcohol/volume/price/scene + 排序 comprehensive/sales/price_asc/price_desc/new/rating + 分页)
    - 搜索(关键词匹配 name/subtitle/series/tags)
    - 推荐热销(get_hot)/主推(get_featured)/关联(related)
    - 产品详情
    - 评价查询(CRUD: list + add)

并发安全:
    - 提交评价使用 product:reviews:{product_id} 锁保护 RMW
    - 其他读操作无锁

返回约定(参考 member_service):
    - 成功: {"success": True, ..., "logs": [...]}
    - 资源不存在: raise KeyError(message) → 路由层映射 404
    - 业务校验失败: raise ValueError(message) → 路由层映射 409
"""

import logging

from core.helpers import ts
from core.locks import get_lock
from repositories.product_repository import (
    ProductRepository,
    REVIEW_STATUS_PUBLISHED, REVIEW_STATUS_PENDING_REVIEW, REVIEW_STATUS_HIDDEN,
    REVIEW_STATUS_REJECTED, REVIEW_STATUS_NAMES,
    REPORT_STATUS_PENDING, REPORT_STATUS_CONFIRMED, REPORT_STATUS_REJECTED,
    REPORT_STATUS_RESOLVED, REPORT_STATUS_NAMES,
    REPORT_REASON_AD, REPORT_REASON_ABUSE, REPORT_REASON_PORN,
    REPORT_REASON_OTHER, REPORT_REASON_NAMES,
)

logger = logging.getLogger(__name__)


# 默认分页参数
DEFAULT_PAGE = 1
DEFAULT_PAGE_SIZE = 12
MAX_PAGE_SIZE = 100

# 支持的排序方式
SORT_COMPREHENSIVE = "comprehensive"  # 综合(默认)
SORT_SALES = "sales"                    # 销量
SORT_PRICE_ASC = "price_asc"           # 价格升序
SORT_PRICE_DESC = "price_desc"         # 价格降序
SORT_NEW = "new"                       # 上架时间降序
SORT_RATING = "rating"                  # 评分降序

VALID_SORTS = {
    SORT_COMPREHENSIVE, SORT_SALES, SORT_PRICE_ASC,
    SORT_PRICE_DESC, SORT_NEW, SORT_RATING,
}


class ProductService:
    """产品展示业务服务"""

    def __init__(self, product_repo: ProductRepository = ProductRepository()):
        self.product_repo = product_repo

    # ============================================================
    # 分类导航
    # ============================================================

    async def get_categories(self) -> dict:
        """返回分类树(系列/度数/容量/价格区间/场景)"""
        result = await self.product_repo.get_categories()
        return {
            "success": True,
            "categories": result["categories"],
            "count": result["count"],
            "logs": [{"step": "分类导航", "level": "INFO",
                      "msg": f"返回 {result['count']} 个顶级分类"}],
        }

    # ============================================================
    # 产品列表(筛选 + 排序 + 分页)
    # ============================================================

    async def list_products(self, filters: dict = None,
                            sort: str = SORT_COMPREHENSIVE,
                            page: int = DEFAULT_PAGE,
                            page_size: int = DEFAULT_PAGE_SIZE) -> dict:
        """产品列表(筛选 + 排序 + 分页)

        Args:
            filters: {series, alcohol, volume, price_min, price_max, scene}
            sort: comprehensive/sales/price_asc/price_desc/new/rating
            page: 页码(从 1 开始)
            page_size: 每页条数

        Raises:
            ValueError: 排序方式不支持 / 分页参数非法
        """
        if sort not in VALID_SORTS:
            raise ValueError(
                f"排序方式不支持: {sort}, 可选: {', '.join(sorted(VALID_SORTS))}"
            )
        if page < 1:
            raise ValueError(f"page 须 >= 1, 当前 {page}")
        if page_size < 1 or page_size > MAX_PAGE_SIZE:
            raise ValueError(f"page_size 须在 1-{MAX_PAGE_SIZE} 之间, 当前 {page_size}")

        # 筛选
        products = await self.product_repo.list_products(filters or {})
        # 排序
        sorted_products = self._sort_products(products, sort)
        # 分页
        total = len(sorted_products)
        start = (page - 1) * page_size
        end = start + page_size
        page_items = sorted_products[start:end]
        total_pages = (total + page_size - 1) // page_size

        applied_filters = {k: v for k, v in (filters or {}).items() if v not in (None, "", [])}
        return {
            "success": True,
            "count": len(page_items),
            "total": total,
            "page": page,
            "pageSize": page_size,
            "totalPages": total_pages,
            "filters": applied_filters,
            "sort": sort,
            "products": [self._to_summary(p) for p in page_items],
            "logs": [
                {"step": "筛选", "level": "INFO",
                 "msg": f"命中 {total} 款, 过滤条件: {applied_filters or '无'}"},
                {"step": "排序", "level": "INFO", "msg": f"排序方式: {sort}"},
                {"step": "分页", "level": "INFO",
                 "msg": f"第 {page}/{total_pages} 页, 返回 {len(page_items)} 条"},
            ],
        }

    # ============================================================
    # 搜索
    # ============================================================

    async def search(self, keyword: str, page: int = DEFAULT_PAGE,
                     page_size: int = DEFAULT_PAGE_SIZE) -> dict:
        """关键词搜索

        Raises:
            ValueError: 关键词为空 / 分页参数非法
        """
        if not keyword or not keyword.strip():
            raise ValueError("搜索关键词不能为空")
        if page < 1:
            raise ValueError(f"page 须 >= 1, 当前 {page}")
        if page_size < 1 or page_size > MAX_PAGE_SIZE:
            raise ValueError(f"page_size 须在 1-{MAX_PAGE_SIZE} 之间, 当前 {page_size}")

        results = await self.product_repo.search(keyword.strip())
        # 搜索结果按综合得分排序
        sorted_results = self._sort_products(results, SORT_COMPREHENSIVE)
        total = len(sorted_results)
        start = (page - 1) * page_size
        end = start + page_size
        page_items = sorted_results[start:end]
        total_pages = (total + page_size - 1) // page_size

        return {
            "success": True,
            "keyword": keyword,
            "count": len(page_items),
            "total": total,
            "page": page,
            "pageSize": page_size,
            "totalPages": total_pages,
            "products": [self._to_summary(p) for p in page_items],
            "logs": [
                {"step": "搜索", "level": "INFO",
                 "msg": f"关键词 '{keyword}', 命中 {total} 款"},
                {"step": "分页", "level": "INFO",
                 "msg": f"第 {page}/{total_pages} 页, 返回 {len(page_items)} 条"},
            ],
        }

    # ============================================================
    # 推荐
    # ============================================================

    async def get_hot_products(self, limit: int = 6) -> dict:
        """热销推荐(按 sales_monthly 降序)

        Raises:
            ValueError: limit 非法
        """
        if limit < 1 or limit > 50:
            raise ValueError(f"limit 须在 1-50 之间, 当前 {limit}")

        products = await self.product_repo.get_hot_products(limit)
        return {
            "success": True,
            "count": len(products),
            "limit": limit,
            "products": [self._to_summary(p) for p in products],
            "logs": [{"step": "热销推荐", "level": "INFO",
                      "msg": f"返回 {len(products)} 款热销产品"}],
        }

    async def get_featured(self, limit: int = 4) -> dict:
        """主推产品(featured=True, 按 hot_rank 升序)

        Raises:
            ValueError: limit 非法
        """
        if limit < 1 or limit > 20:
            raise ValueError(f"limit 须在 1-20 之间, 当前 {limit}")

        products = await self.product_repo.get_featured(limit)
        return {
            "success": True,
            "count": len(products),
            "limit": limit,
            "products": [self._to_summary(p) for p in products],
            "logs": [{"step": "主推产品", "level": "INFO",
                      "msg": f"返回 {len(products)} 款主推产品"}],
        }

    # ============================================================
    # 详情
    # ============================================================

    async def get_detail(self, product_id: str) -> dict:
        """产品详情(含关联推荐 4 条)

        Raises:
            KeyError: 产品不存在
        """
        product = await self.product_repo.get_by_id(product_id)
        if not product:
            raise KeyError(f"产品 {product_id} 不存在")

        related = await self.product_repo.get_related(product_id, limit=4)
        return {
            "success": True,
            "product": self._to_detail(product),
            "related": [self._to_summary(p) for p in related],
            "logs": [
                {"step": "详情", "level": "INFO",
                 "msg": f"产品 {product_id}: {product['name']}"},
                {"step": "关联推荐", "level": "INFO",
                 "msg": f"返回 {len(related)} 款关联产品"},
            ],
        }

    # ============================================================
    # 评价
    # ============================================================

    async def list_reviews(self, product_id: str, page: int = 1,
                           page_size: int = 10) -> dict:
        """产品评价列表(分页)

        Raises:
            KeyError: 产品不存在
        """
        product = await self.product_repo.get_by_id(product_id)
        if not product:
            raise KeyError(f"产品 {product_id} 不存在")
        if page < 1:
            raise ValueError(f"page 须 >= 1, 当前 {page}")
        if page_size < 1 or page_size > MAX_PAGE_SIZE:
            raise ValueError(f"page_size 须在 1-{MAX_PAGE_SIZE} 之间, 当前 {page_size}")

        reviews, total = await self.product_repo.get_reviews(
            product_id, page, page_size)
        total_pages = (total + page_size - 1) // page_size
        return {
            "success": True,
            "productId": product_id,
            "productName": product["name"],
            "count": len(reviews),
            "total": total,
            "page": page,
            "pageSize": page_size,
            "totalPages": total_pages,
            "ratingAvg": product.get("rating_avg", 0),
            "ratingCount": product.get("rating_count", 0),
            "reviews": reviews,
            "logs": [{"step": "评价列表", "level": "INFO",
                      "msg": f"第 {page}/{total_pages} 页, 返回 {len(reviews)} 条评价"}],
        }

    async def add_review(self, product_id: str, member_id,
                         member_nickname: str, rating: int,
                         content: str) -> dict:
        """提交评价(注入 review_id/created_at, 同步更新产品评分)

        Raises:
            KeyError: 产品不存在
            ValueError: 评分越界 / 内容为空
        """
        if not content or not content.strip():
            raise ValueError("评价内容不能为空")
        content = content.strip()
        if len(content) > 500:
            raise ValueError("评价内容不超过 500 字")

        async with get_lock(f"product:reviews:{product_id}"):
            review_data = {
                "member_id": member_id,
                "member_nickname": member_nickname or f"会员{member_id}",
                "rating": int(rating),
                "content": content,
            }
            saved = await self.product_repo.add_review(product_id, review_data)
            logger.info("review_added product=%s member=%r rating=%d",
                        product_id, member_id, rating)
            return {
                "success": True,
                "productId": product_id,
                "reviewId": saved["review_id"],
                "rating": saved["rating"],
                "logs": [
                    {"step": "评价提交", "level": "INFO",
                     "msg": f"{saved['rating']} 星评价已提交(评价ID: {saved['review_id']})"},
                    {"step": "评分更新", "level": "INFO",
                     "msg": "已重算产品评分"},
                ],
            }

    # ============================================================
    # 评价扩展(P0): 详情/修改/删除/按订单查询/会员历史
    # ============================================================

    async def get_review_detail(self, product_id: str, review_id: str) -> dict:
        """评价详情(含回复列表)

        Raises:
            KeyError: 产品或评价不存在
        """
        product = await self.product_repo.get_by_id(product_id)
        if not product:
            raise KeyError(f"产品 {product_id} 不存在")
        review = await self.product_repo.get_review(product_id, review_id)
        if not review:
            raise KeyError(f"评价 {review_id} 不存在")
        # 注入回复列表
        replies = await self.product_repo.get_replies(review_id)
        review = dict(review)
        review["replies"] = replies
        return {
            "success": True,
            "productId": product_id,
            "productName": product["name"],
            "review": review,
            "logs": [{"step": "评价详情", "level": "INFO",
                      "msg": f"评价 {review_id}, 回复 {len(replies)} 条"}],
        }

    async def update_review(self, product_id: str, review_id: str,
                            member_id: str, rating: int = None,
                            content: str = None, images: list = None,
                            is_admin: bool = False) -> dict:
        """修改评价(仅本人或管理员)

        Raises:
            KeyError: 产品/评价不存在
            ValueError: 非本人修改 / 内容为空 / 评分越界
        """
        product = await self.product_repo.get_by_id(product_id)
        if not product:
            raise KeyError(f"产品 {product_id} 不存在")
        review = await self.product_repo.get_review(product_id, review_id)
        if not review:
            raise KeyError(f"评价 {review_id} 不存在")
        # 权限校验: 仅本人或管理员可修改
        if not is_admin and str(review.get("member_id", "")) != str(member_id):
            raise ValueError("无权修改他人评价")

        fields = {}
        if rating is not None:
            if not isinstance(rating, int) or not (1 <= rating <= 5):
                raise ValueError("评分必须为 1-5 的整数")
            fields["rating"] = rating
        if content is not None:
            content = content.strip()
            if not content:
                raise ValueError("评价内容不能为空")
            if len(content) > 500:
                raise ValueError("评价内容不超过 500 字")
            fields["content"] = content
        if images is not None:
            if len(images) > 9:
                raise ValueError("评价图片不超过 9 张")
            fields["images"] = images
        fields["updated_at"] = ts()

        async with get_lock(f"product:review:{review_id}"):
            updated = await self.product_repo.update_review(
                product_id, review_id, fields)
            # rating 变更需重算产品评分
            if rating is not None:
                await self.product_repo._update_rating_stats(product_id)

        logger.info("review_updated product=%s review=%s member=%s",
                    product_id, review_id, member_id)
        return {
            "success": True,
            "productId": product_id,
            "reviewId": review_id,
            "review": updated,
            "logs": [{"step": "评价修改", "level": "INFO",
                      "msg": f"评价 {review_id} 已修改"}],
        }

    async def delete_review(self, product_id: str, review_id: str,
                            member_id: str, is_admin: bool = False) -> dict:
        """删除评价(仅本人或管理员)

        Raises:
            KeyError: 产品/评价不存在
            ValueError: 无权删除他人评价
        """
        product = await self.product_repo.get_by_id(product_id)
        if not product:
            raise KeyError(f"产品 {product_id} 不存在")
        review = await self.product_repo.get_review(product_id, review_id)
        if not review:
            raise KeyError(f"评价 {review_id} 不存在")
        if not is_admin and str(review.get("member_id", "")) != str(member_id):
            raise ValueError("无权删除他人评价")

        async with get_lock(f"product:review:{review_id}"):
            await self.product_repo.delete_review(product_id, review_id)
            # 重算产品评分
            await self.product_repo._update_rating_stats(product_id)

        logger.info("review_deleted product=%s review=%s member=%s",
                    product_id, review_id, member_id)
        return {
            "success": True,
            "productId": product_id,
            "reviewId": review_id,
            "logs": [{"step": "评价删除", "level": "INFO",
                      "msg": f"评价 {review_id} 已删除"}],
        }

    async def get_review_by_order(self, order_id: str,
                                  product_id: str = None) -> dict:
        """按订单号查询评价(判断是否已评价)

        Raises:
            KeyError: 订单未评价
        """
        review = await self.product_repo.get_review_by_order(order_id, product_id)
        if not review:
            raise KeyError(f"订单 {order_id} 未找到评价")
        # 注入回复列表
        replies = await self.product_repo.get_replies(review.get("review_id", ""))
        review = dict(review)
        review["replies"] = replies
        return {
            "success": True,
            "orderId": order_id,
            "review": review,
            "logs": [{"step": "按订单查评价", "level": "INFO",
                      "msg": f"订单 {order_id} 已评价"}],
        }

    async def list_my_reviews(self, member_id: str, limit: int = 50) -> dict:
        """会员评价历史"""
        reviews = await self.product_repo.list_reviews_by_member(member_id, limit)
        return {
            "success": True,
            "memberId": member_id,
            "total": len(reviews),
            "reviews": reviews,
            "logs": [{"step": "会员评价历史", "level": "INFO",
                      "msg": f"共 {len(reviews)} 条评价"}],
        }

    # ============================================================
    # 评价回复(P0)
    # ============================================================

    async def add_reply(self, product_id: str, review_id: str,
                        replier_id: str, replier_role: str,
                        replier_name: str, content: str,
                        parent_reply_id: str = "") -> dict:
        """提交评价回复(商家/管理员)

        Raises:
            KeyError: 产品/评价不存在
            ValueError: 内容为空 / 角色非法
        """
        valid_roles = {"merchant", "admin"}
        if replier_role not in valid_roles:
            raise ValueError(f"回复角色非法: {replier_role}")
        content = content.strip()
        if not content:
            raise ValueError("回复内容不能为空")
        if len(content) > 500:
            raise ValueError("回复内容不超过 500 字")

        product = await self.product_repo.get_by_id(product_id)
        if not product:
            raise KeyError(f"产品 {product_id} 不存在")
        review = await self.product_repo.get_review(product_id, review_id)
        if not review:
            raise KeyError(f"评价 {review_id} 不存在")

        async with get_lock(f"product:review:{review_id}"):
            reply_data = {
                "replier_id": replier_id,
                "replier_role": replier_role,
                "replier_name": replier_name,
                "content": content,
                "parent_reply_id": parent_reply_id,
            }
            saved = await self.product_repo.add_reply(review_id, reply_data)
            # 更新评价的 reply_count
            current_count = review.get("reply_count", 0)
            await self.product_repo.update_review(
                product_id, review_id, {"reply_count": current_count + 1})

        logger.info("reply_added review=%s replier=%s role=%s",
                    review_id, replier_id, replier_role)
        return {
            "success": True,
            "reviewId": review_id,
            "reply": saved,
            "logs": [{"step": "评价回复", "level": "INFO",
                      "msg": f"回复已提交(回复ID: {saved['reply_id']})"}],
        }

    async def list_replies(self, product_id: str, review_id: str) -> dict:
        """评价回复列表

        Raises:
            KeyError: 产品/评价不存在
        """
        product = await self.product_repo.get_by_id(product_id)
        if not product:
            raise KeyError(f"产品 {product_id} 不存在")
        review = await self.product_repo.get_review(product_id, review_id)
        if not review:
            raise KeyError(f"评价 {review_id} 不存在")
        replies = await self.product_repo.get_replies(review_id)
        return {
            "success": True,
            "reviewId": review_id,
            "total": len(replies),
            "replies": replies,
            "logs": [{"step": "回复列表", "level": "INFO",
                      "msg": f"共 {len(replies)} 条回复"}],
        }

    # ============================================================
    # 评价点赞(P1)
    # ============================================================

    async def like_review(self, review_id: str, member_id: str) -> dict:
        """点赞评价

        Raises:
            ValueError: 重复点赞
        """
        async with get_lock(f"product:review:like:{review_id}"):
            added = await self.product_repo.add_like(review_id, member_id)
            if not added:
                raise ValueError("已点赞过该评价")
        logger.info("review_liked review=%s member=%s", review_id, member_id)
        like_count = await self.product_repo.get_like_count(review_id)
        return {
            "success": True,
            "reviewId": review_id,
            "liked": True,
            "likeCount": like_count,
            "logs": [{"step": "点赞", "level": "INFO",
                      "msg": f"已点赞评价 {review_id}"}],
        }

    async def unlike_review(self, review_id: str, member_id: str) -> dict:
        """取消点赞

        Raises:
            ValueError: 未点赞
        """
        async with get_lock(f"product:review:like:{review_id}"):
            removed = await self.product_repo.remove_like(review_id, member_id)
            if not removed:
                raise ValueError("未点赞该评价, 无法取消")
        logger.info("review_unliked review=%s member=%s", review_id, member_id)
        like_count = await self.product_repo.get_like_count(review_id)
        return {
            "success": True,
            "reviewId": review_id,
            "liked": False,
            "likeCount": like_count,
            "logs": [{"step": "取消点赞", "level": "INFO",
                      "msg": f"已取消点赞评价 {review_id}"}],
        }

    # ============================================================
    # 评价举报(P1)
    # ============================================================

    async def report_review(self, review_id: str, reporter_id: str,
                            reason: str, description: str = "") -> dict:
        """举报评价

        Raises:
            ValueError: 重复举报 / 举报原因非法
        """
        valid_reasons = {
            REPORT_REASON_AD, REPORT_REASON_ABUSE,
            REPORT_REASON_PORN, REPORT_REASON_OTHER,
        }
        if reason not in valid_reasons:
            raise ValueError(f"举报原因非法: {reason}")
        description = description.strip()
        if len(description) > 500:
            raise ValueError("举报描述不超过 500 字")

        # 防重复举报
        existing = await self.product_repo.get_report_by_review_reporter(
            review_id, reporter_id)
        if existing:
            raise ValueError("已举报过该评价, 请勿重复举报")

        report_data = {
            "review_id": review_id,
            "reporter_id": reporter_id,
            "reason": reason,
            "description": description,
        }
        saved = await self.product_repo.create_report(report_data)
        logger.info("review_reported review=%s reporter=%s reason=%s",
                    review_id, reporter_id, reason)
        return {
            "success": True,
            "report": saved,
            "logs": [{"step": "评价举报", "level": "INFO",
                      "msg": f"举报已提交(举报ID: {saved['report_id']})"}],
        }

    async def list_reports(self, status: str = None, limit: int = 50) -> dict:
        """举报列表(管理员)

        Raises:
            ValueError: 状态非法
        """
        if status and status not in REPORT_STATUS_NAMES:
            raise ValueError(f"举报状态非法: {status}")
        reports = await self.product_repo.list_reports(status, limit)
        return {
            "success": True,
            "total": len(reports),
            "reports": reports,
            "logs": [{"step": "举报列表", "level": "INFO",
                      "msg": f"共 {len(reports)} 条举报"}],
        }

    async def handle_report(self, report_id: str, handler_id: str,
                            action: str, remark: str = "") -> dict:
        """处理举报(管理员)

        Args:
            action: confirmed(举报成立) / rejected(驳回)
        Raises:
            KeyError: 举报不存在
            ValueError: 状态非法 / 已处理
        """
        report = await self.product_repo.get_report(report_id)
        if not report:
            raise KeyError(f"举报 {report_id} 不存在")
        if report.get("status") != REPORT_STATUS_PENDING:
            raise ValueError(f"举报已处理, 当前状态: {report.get('status')}")

        valid_actions = {"confirmed", "rejected"}
        if action not in valid_actions:
            raise ValueError(f"处理动作非法: {action}")
        remark = remark.strip()
        if len(remark) > 500:
            raise ValueError("处理备注不超过 500 字")

        # 举报成立: 更新举报状态 + 隐藏对应评价
        if action == "confirmed":
            new_status = REPORT_STATUS_CONFIRMED
            review_id = report.get("review_id", "")
            if review_id:
                # 隐藏评价(遍历查找, 需在评价锁保护下)
                async with get_lock(f"product:review:{review_id}"):
                    await self._hide_review_internal(review_id)
        else:
            new_status = REPORT_STATUS_REJECTED

        fields = {
            "status": new_status,
            "handler_id": handler_id,
            "handle_remark": remark,
            "handled_at": ts(),
        }
        updated = await self.product_repo.update_report(report_id, fields)

        logger.info("report_handled report=%s action=%s handler=%s",
                    report_id, action, handler_id)
        return {
            "success": True,
            "report": updated,
            "logs": [{"step": "举报处理", "level": "INFO",
                      "msg": f"举报 {report_id} 已处理: {REPORT_STATUS_NAMES.get(new_status, new_status)}"}],
        }

    async def hide_review(self, product_id: str, review_id: str,
                          is_hide: bool = True) -> dict:
        """管理员隐藏/恢复评价

        Raises:
            KeyError: 产品/评价不存在
        """
        product = await self.product_repo.get_by_id(product_id)
        if not product:
            raise KeyError(f"产品 {product_id} 不存在")
        review = await self.product_repo.get_review(product_id, review_id)
        if not review:
            raise KeyError(f"评价 {review_id} 不存在")

        new_status = REVIEW_STATUS_HIDDEN if is_hide else REVIEW_STATUS_PUBLISHED
        async with get_lock(f"product:review:{review_id}"):
            await self.product_repo.update_review(
                product_id, review_id, {"status": new_status})
        action = "隐藏" if is_hide else "恢复"
        logger.info("review_%s product=%s review=%s",
                    "hidden" if is_hide else "restored", product_id, review_id)
        return {
            "success": True,
            "productId": product_id,
            "reviewId": review_id,
            "status": new_status,
            "logs": [{"step": f"评价{action}", "level": "INFO",
                      "msg": f"评价 {review_id} 已{action}"}],
        }

    async def _hide_review_internal(self, review_id: str):
        """内部方法: 隐藏评价(遍历查找, 不加锁, 由调用方加锁)"""
        # 遍历所有产品评价, 找到对应 review_id 并更新状态
        if hasattr(self.product_repo, 'is_redis_mode'):
            is_redis = self.product_repo.is_redis_mode
        else:
            from repositories.backend import is_redis_mode
            is_redis = is_redis_mode
        if is_redis():
            all_products = await self.product_repo._redis_list_all()
        else:
            all_products = self.product_repo._mem_list_all()
        for p in all_products:
            pid = p.get("product_id")
            if not pid:
                continue
            review = await self.product_repo.get_review(pid, review_id)
            if review:
                await self.product_repo.update_review(
                    pid, review_id, {"status": REVIEW_STATUS_HIDDEN})
                return

    # ============================================================
    # 排序辅助
    # ============================================================

    def _sort_products(self, products: list[dict], sort: str) -> list[dict]:
        """根据 sort 参数排序产品列表

        综合(comprehensive): 销量×0.3 + 评分×0.3 + 转化率×0.2 + 新品×0.2
            - 销量: 归一化 sales_monthly
            - 评分: 归一化 rating_avg
            - 转化率: sales_total/rating_count(评价越多转化越好, 兜底0)
            - 新品: created_at 越新分数越高
        """
        if not products:
            return []
        if sort == SORT_SALES:
            return sorted(products,
                          key=lambda p: p.get("sales_monthly", 0),
                          reverse=True)
        if sort == SORT_PRICE_ASC:
            return sorted(products, key=lambda p: p.get("price", 0))
        if sort == SORT_PRICE_DESC:
            return sorted(products,
                          key=lambda p: p.get("price", 0), reverse=True)
        if sort == SORT_NEW:
            return sorted(products,
                          key=lambda p: p.get("created_at", ""),
                          reverse=True)
        if sort == SORT_RATING:
            return sorted(products,
                          key=lambda p: p.get("rating_avg", 0),
                          reverse=True)
        # 默认: 综合得分
        return sorted(products,
                       key=lambda p: self._comprehensive_score(p, products),
                       reverse=True)

    def _comprehensive_score(self, product: dict, all_products: list[dict]) -> float:
        """综合得分: 销量×0.3 + 评分×0.3 + 转化率×0.2 + 新品×0.2"""
        sales = product.get("sales_monthly", 0)
        rating = product.get("rating_avg", 0)
        rating_count = product.get("rating_count", 0)
        created = product.get("created_at", "")

        # 归一化基础数据
        max_sales = max((p.get("sales_monthly", 0) for p in all_products), default=1) or 1
        max_rating_count = max((p.get("rating_count", 0) for p in all_products), default=1) or 1

        sales_norm = sales / max_sales
        rating_norm = rating / 5.0
        # 转化率: 评价率(评价数/销量), 反馈越多越好
        conversion_norm = (rating_count / max_rating_count) if max_rating_count else 0
        # 新品: 时间戳越大越新(用字符串字典序近似)
        # 用 rank: 越晚创建 rank 越大
        created_list = sorted({p.get("created_at", "") for p in all_products})
        try:
            new_rank = created_list.index(created) + 1 if created in created_list else 1
            new_norm = new_rank / max(1, len(created_list))
        except ValueError:
            new_norm = 0

        return (
            sales_norm * 0.3
            + rating_norm * 0.3
            + conversion_norm * 0.2
            + new_norm * 0.2
        )

    # ============================================================
    # 响应字段裁剪
    # ============================================================

    def _to_summary(self, product: dict) -> dict:
        """产品列表/搜索的精简视图(不含 description/attributes 全字段)"""
        return {
            "product_id": product.get("product_id"),
            "name": product.get("name"),
            "subtitle": product.get("subtitle"),
            "brand": product.get("brand"),
            "series": product.get("series"),
            "alcohol": product.get("alcohol"),
            "volume": product.get("volume"),
            "price": product.get("price"),
            "original_price": product.get("original_price"),
            "member_price": product.get("member_price"),
            "svip_price": product.get("svip_price"),
            "stock": product.get("stock", 0),
            "sales_monthly": product.get("sales_monthly", 0),
            "sales_total": product.get("sales_total", 0),
            "rating_avg": product.get("rating_avg", 0),
            "rating_count": product.get("rating_count", 0),
            "tags": product.get("tags", []),
            "scenes": product.get("scenes", []),
            "status": product.get("status", ""),
            "featured": product.get("featured", False),
            "image": (product.get("images") or {}).get("main", ""),
            "created_at": product.get("created_at", ""),
        }

    def _to_detail(self, product: dict) -> dict:
        """产品详情视图(全字段)"""
        summary = self._to_summary(product)
        summary.update({
            "origin": product.get("origin"),
            "images": product.get("images", {}),
            "attributes": product.get("attributes", {}),
            "description": product.get("description", ""),
            "reserved": product.get("reserved", 0),
            "hot_rank": product.get("hot_rank", 0),
        })
        return summary
