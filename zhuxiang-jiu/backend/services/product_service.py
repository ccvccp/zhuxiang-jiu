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
from typing import Optional

from core.helpers import ts
from core.locks import get_lock
from repositories.product_repository import ProductRepository

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
        page_items = sorted_results[start:start + page_size]
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
                     "msg": f"已重算产品评分"},
                ],
            }

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
        sales_total = product.get("sales_total", 0)
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
