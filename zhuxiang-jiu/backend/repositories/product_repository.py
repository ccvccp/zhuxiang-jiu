"""产品展示 Repository

双模式(内存/Redis)透明切换:
    - 内存模式: 直接操作 _mock_store["products"] / _mock_store["product_reviews"]
    - Redis 模式:
        * 产品主信息: Hash  zhuxiang:product:{product_id}
                       (嵌套字段如 tags/attributes/images 序列化为 JSON 字符串)
        * 分类树:     List  zhuxiang:product:categories
                       (每个元素为 JSON 字符串, 形如 {"key": "series", ...})
        * 产品评价:   List  zhuxiang:product:reviews:{product_id}
                       (每个元素为 JSON 字符串)

库存字段(stock/reserved)不在产品主信息中, 复用 InventoryRepository:
    - 内存: _mock_store["inventory"][product_id]
    - Redis: Hash zhuxiang:inventory:{product_id}

锁键: product:{product_id}(评价写入等 RMW 操作, 由 services 层负责)
"""

import json
import logging

from repositories.backend import is_redis_mode, get_redis_client, get_in_memory_store, _k
from repositories.inventory_repository import InventoryRepository
from datetime import UTC

logger = logging.getLogger(__name__)


# ============================================================
# 评价状态机常量
# ============================================================

REVIEW_STATUS_PUBLISHED = "published"          # 已发布(默认)
REVIEW_STATUS_PENDING_REVIEW = "pending_review"  # 待审核
REVIEW_STATUS_HIDDEN = "hidden"                 # 已隐藏
REVIEW_STATUS_REJECTED = "rejected"              # 审核拒绝

REVIEW_STATUS_NAMES = {
    REVIEW_STATUS_PUBLISHED: "已发布",
    REVIEW_STATUS_PENDING_REVIEW: "待审核",
    REVIEW_STATUS_HIDDEN: "已隐藏",
    REVIEW_STATUS_REJECTED: "审核拒绝",
}

# 举报状态机常量
REPORT_STATUS_PENDING = "pending"               # 待处理
REPORT_STATUS_CONFIRMED = "confirmed"           # 举报成立
REPORT_STATUS_REJECTED = "rejected"             # 举报驳回
REPORT_STATUS_RESOLVED = "resolved"             # 已处理

REPORT_STATUS_NAMES = {
    REPORT_STATUS_PENDING: "待处理",
    REPORT_STATUS_CONFIRMED: "举报成立",
    REPORT_STATUS_REJECTED: "举报驳回",
    REPORT_STATUS_RESOLVED: "已处理",
}

# 举报原因类型
REPORT_REASON_AD = "ad"                         # 广告推广
REPORT_REASON_ABUSE = "abuse"                    # 辱骂攻击
REPORT_REASON_PORN = "porn"                     # 色情低俗
REPORT_REASON_OTHER = "other"                    # 其他

REPORT_REASON_NAMES = {
    REPORT_REASON_AD: "广告推广",
    REPORT_REASON_ABUSE: "辱骂攻击",
    REPORT_REASON_PORN: "色情低俗",
    REPORT_REASON_OTHER: "其他",
}


# ============================================================
# 分类树(静态元数据, 与产品线对齐)
# ============================================================

PRODUCT_CATEGORIES = [
    {
        "key": "series",
        "name": "系列",
        "items": [
            {"code": "经典系列", "name": "经典系列"},
            {"code": "珍藏系列", "name": "珍藏系列"},
            {"code": "年份系列", "name": "年份系列"},
            {"code": "礼盒系列", "name": "礼盒系列"},
            {"code": "便携系列", "name": "便携系列"},
            {"code": "典藏系列", "name": "典藏系列"},
            {"code": "竹香系列", "name": "竹香系列"},
        ],
    },
    {
        "key": "alcohol",
        "name": "度数",
        "items": [
            {"code": "42", "name": "42°"},
            {"code": "45", "name": "45°"},
            {"code": "50", "name": "50°"},
            {"code": "52", "name": "52°"},
            {"code": "53", "name": "53°"},
        ],
    },
    {
        "key": "volume",
        "name": "容量",
        "items": [
            {"code": "250ml", "name": "250ml"},
            {"code": "500ml", "name": "500ml"},
            {"code": "500ml×2", "name": "500ml×2"},
            {"code": "750ml", "name": "750ml"},
        ],
    },
    {
        "key": "price",
        "name": "价格区间",
        "items": [
            {"code": "0-200", "name": "0-200元", "min": 0, "max": 200},
            {"code": "200-500", "name": "200-500元", "min": 200, "max": 500},
            {"code": "500-1000", "name": "500-1000元", "min": 500, "max": 1000},
            {"code": "1000-2000", "name": "1000-2000元", "min": 1000, "max": 2000},
            {"code": "2000+", "name": "2000元以上", "min": 2000, "max": None},
        ],
    },
    {
        "key": "scene",
        "name": "适用场景",
        "items": [
            {"code": "商务宴请", "name": "商务宴请"},
            {"code": "高端礼赠", "name": "高端礼赠"},
            {"code": "老友小聚", "name": "老友小聚"},
            {"code": "收藏投资", "name": "收藏投资"},
            {"code": "团购定制", "name": "团购定制"},
        ],
    },
]


# ============================================================
# 11 款产品初始数据(基于设计文档产品线)
# ============================================================

def _img(prompt: str, size: str = "1024x1024") -> str:
    """构造占位图 URL(遵循项目约定的图片 URL 格式)"""
    return (
        "https://trae-api-cn.mchost.guru/api/ide/v1/text_to_image"
        f"?prompt={prompt}&image_size={size}"
    )


def _build_product(pid, name, subtitle, series, alcohol, volume, price,
                   original_price, tags, scenes, attributes_extra=None,
                   description="", created_at="", sales_monthly=0,
                   sales_total=0, rating_avg=5.0, rating_count=0,
                   featured=False, hot_rank=0):
    """构造单个产品 dict(member_price/svip_price 自动按 9 折/8.5 折计算)"""
    attrs = {
        "aroma": "竹香型",
        "process": "固态发酵·古法酿造",
        "alcohol": f"{alcohol}°",
        "volume": volume,
        "origin": "山东泰安",
        "ingredients": "泰山泉水·优质高粱·大米·小麦·竹叶提取物",
        "storage": "阴凉·避光·直立存放",
        "taste": "入口绵甜·回味悠长",
    }
    if attributes_extra:
        attrs.update(attributes_extra)
    return {
        "product_id": pid,
        "name": name,
        "subtitle": subtitle,
        "brand": "竹奕",
        "series": series,
        "alcohol": alcohol,
        "volume": volume,
        "price": price,
        "original_price": original_price,
        "member_price": round(price * 0.9, 2),
        "svip_price": round(price * 0.85, 2),
        "sales_monthly": sales_monthly,
        "sales_total": sales_total,
        "rating_avg": rating_avg,
        "rating_count": rating_count,
        "tags": list(tags),
        "scenes": list(scenes),
        "status": "on_sale",
        "origin": "山东泰安",
        "featured": featured,
        "hot_rank": hot_rank,
        "images": {
            "main": _img(f"{name} 主图"),
            "gallery": [
                _img(f"{name} 细节图1"),
                _img(f"{name} 细节图2"),
                _img(f"{name} 包装图"),
            ],
        },
        "attributes": attrs,
        "created_at": created_at,
        "description": description or f"{name}，源自山东泰安，竹香型白酒代表作。",
    }


# 初始产品清单(11 款), created_at 用 ISO8601 区分新品排序
_INITIAL_PRODUCTS = [
    _build_product(
        "ZX42-2026L07", "竹奕·竹香经典 42° 500ml", "经典入门·绵柔顺喉",
        "经典系列", 42, "500ml", 268, 368,
        tags=["主打", "热销"], scenes=["老友小聚", "商务宴请"],
        attributes_extra={"taste": "绵柔顺喉·余味回甘"},
        description="竹奕经典系列入门款，42°绵柔顺喉，适合日常小聚与商务宴请。",
        created_at="2026-08-01T00:00:00+00:00",
        sales_monthly=1280, sales_total=15360, rating_avg=4.8, rating_count=320,
        featured=True, hot_rank=1,
    ),
    _build_product(
        "ZX45-2026L05", "竹奕·竹香经典 45° 500ml", "经典进阶·醇厚饱满",
        "经典系列", 45, "500ml", 368, 468,
        tags=["热销"], scenes=["老友小聚", "商务宴请"],
        attributes_extra={"taste": "醇厚饱满·尾净爽净"},
        description="经典系列进阶款，45°醇厚饱满，性价比之选。",
        created_at="2026-08-03T00:00:00+00:00",
        sales_monthly=960, sales_total=11520, rating_avg=4.7, rating_count=210,
        featured=False, hot_rank=2,
    ),
    _build_product(
        "ZX53-2026Z01", "竹奕·竹香珍藏 53° 500ml", "珍藏佳酿·品鉴典藏",
        "珍藏系列", 53, "500ml", 698, 888,
        tags=["品鉴"], scenes=["商务宴请", "收藏投资"],
        attributes_extra={"taste": "陈韵悠长·层次丰富"},
        description="珍藏系列代表作，53°高度数，陈韵悠长，适合品鉴收藏。",
        created_at="2026-08-05T00:00:00+00:00",
        sales_monthly=320, sales_total=3840, rating_avg=4.9, rating_count=128,
        featured=True, hot_rank=5,
    ),
    _build_product(
        "ZX53-2026N10", "竹奕·竹香年份10年 53° 500ml", "十年陈酿·岁月沉淀",
        "年份系列", 53, "500ml", 888, 1088,
        tags=["陈酿"], scenes=["商务宴请", "收藏投资"],
        attributes_extra={"taste": "陈香浓郁·绵柔醇厚", "aged": "10年"},
        description="10年陈酿，53°高度数，岁月沉淀出的醇厚口感。",
        created_at="2026-07-28T00:00:00+00:00",
        sales_monthly=180, sales_total=2160, rating_avg=4.9, rating_count=86,
        featured=False, hot_rank=6,
    ),
    _build_product(
        "ZX53-2026N20", "竹奕·竹香二十年限量 53° 500ml", "二十年限量·收藏臻品",
        "年份系列", 53, "500ml", 2688, 3288,
        tags=["限量", "收藏"], scenes=["收藏投资", "高端礼赠"],
        attributes_extra={"taste": "稀有陈韵·回味无穷", "aged": "20年"},
        description="20年限量珍藏，收藏级臻品，全球限量发售。",
        created_at="2026-07-20T00:00:00+00:00",
        sales_monthly=60, sales_total=720, rating_avg=5.0, rating_count=32,
        featured=True, hot_rank=8,
    ),
    _build_product(
        "ZX52-2026L02", "竹奕·竹香礼盒 52° 500ml×2", "礼盒装·礼赠首选",
        "礼盒系列", 52, "500ml×2", 1288, 1588,
        tags=["礼赠"], scenes=["高端礼赠", "商务宴请"],
        attributes_extra={"taste": "醇和馥郁·礼盒双瓶"},
        description="52°礼盒双瓶装，礼赠首选，商务往来之选。",
        created_at="2026-07-25T00:00:00+00:00",
        sales_monthly=240, sales_total=2880, rating_avg=4.8, rating_count=96,
        featured=True, hot_rank=4,
    ),
    _build_product(
        "ZX42-2026B01", "竹奕·竹香便携 42° 250ml", "便携小瓶·随行畅饮",
        "便携系列", 42, "250ml", 88, 128,
        tags=["便携"], scenes=["老友小聚"],
        attributes_extra={"taste": "绵柔顺喉·小瓶便携"},
        description="42°便携小瓶，随行畅饮，自饮试饮首选。",
        created_at="2026-08-10T00:00:00+00:00",
        sales_monthly=2400, sales_total=28800, rating_avg=4.6, rating_count=580,
        featured=False, hot_rank=3,
    ),
    _build_product(
        "ZX50-2026D01", "竹奕·竹香典藏 50° 750ml", "典藏大瓶·宴席首选",
        "典藏系列", 50, "750ml", 1580, 1888,
        tags=["典藏"], scenes=["商务宴请", "团购定制"],
        attributes_extra={"taste": "陈香馥郁·醇厚圆润"},
        description="典藏系列大瓶装，50°，宴席团购首选。",
        created_at="2026-07-15T00:00:00+00:00",
        sales_monthly=120, sales_total=1440, rating_avg=4.9, rating_count=64,
        featured=False, hot_rank=7,
    ),
    _build_product(
        "ZX52-2026X01", "竹奕·竹香尊享 52° 500ml", "竹香旗舰·旗舰之选",
        "竹香系列", 52, "500ml", 398, 498,
        tags=["旗舰"], scenes=["老友小聚", "商务宴请"],
        attributes_extra={"taste": "竹香突出·绵柔醇厚"},
        description="竹香系列旗舰款，52°，竹香突出，性价比旗舰之选。",
        created_at="2026-08-08T00:00:00+00:00",
        sales_monthly=560, sales_total=6720, rating_avg=4.7, rating_count=152,
        featured=False, hot_rank=5,
    ),
    _build_product(
        "ZX52-2026X02", "竹奕·竹香尊享 52° 750ml", "尊享大瓶·礼宴之选",
        "竹香系列", 52, "750ml", 998, 1288,
        tags=["旗舰"], scenes=["商务宴请", "高端礼赠"],
        attributes_extra={"taste": "竹香馥郁·醇厚圆润"},
        description="竹香系列尊享大瓶，52°750ml，礼宴之选。",
        created_at="2026-08-06T00:00:00+00:00",
        sales_monthly=280, sales_total=3360, rating_avg=4.8, rating_count=104,
        featured=False, hot_rank=6,
    ),
    _build_product(
        "ZX52-2026X03", "竹奕·竹香尊享 52° 礼盒装", "尊享礼盒·限量臻选",
        "竹香系列", 52, "500ml×2", 1888, 2288,
        tags=["限量", "旗舰"], scenes=["高端礼赠", "收藏投资"],
        attributes_extra={"taste": "竹香馥郁·礼盒臻选"},
        description="竹香系列限量礼盒，52°双瓶装，限量臻选。",
        created_at="2026-07-18T00:00:00+00:00",
        sales_monthly=80, sales_total=960, rating_avg=4.9, rating_count=42,
        featured=True, hot_rank=9,
    ),
]


def _initial_products() -> list[dict]:
    """返回 11 款产品数据的深拷贝(避免外部修改污染常量)"""
    import copy
    return copy.deepcopy(_INITIAL_PRODUCTS)


# ============================================================
# 初始评价数据(少量种子, 主要覆盖排序/分页/详情场景)
# ============================================================

def _initial_reviews() -> dict:
    """返回产品评价的初始映射 {product_id: [review, ...]}"""
    return {
        "ZX42-2026L07": [
            {
                "review_id": "rv_seed_001",
                "member_id": 1,
                "member_nickname": "测试会员小竹",
                "rating": 5,
                "content": "经典款很绵柔, 适合日常小聚, 性价比高。",
                "created_at": "2026-08-15T10:00:00+00:00",
            },
            {
                "review_id": "rv_seed_002",
                "member_id": 2,
                "member_nickname": "竹香爱好者",
                "rating": 4,
                "content": "口感不错, 但希望包装再精致一些。",
                "created_at": "2026-08-16T11:30:00+00:00",
            },
        ],
        "ZX53-2026Z01": [
            {
                "review_id": "rv_seed_003",
                "member_id": 1,
                "member_nickname": "测试会员小竹",
                "rating": 5,
                "content": "珍藏款陈韵悠长, 值得品鉴收藏。",
                "created_at": "2026-08-14T09:00:00+00:00",
            },
        ],
    }


# ============================================================
# Repository 主体
# ============================================================

class ProductRepository:
    """产品数据访问(双模式: 内存 dict / Redis Hash+List)"""

    def __init__(self, store: dict = None):
        # store 参数仅用于内存模式兼容(测试可能注入)
        # Redis 模式下忽略 store, 走 Redis 客户端
        self.store = store if store is not None else get_in_memory_store()
        # 库存读取复用 InventoryRepository(双模式一致)
        self.inventory_repo = InventoryRepository(self.store)

    # ---------- 产品主表 ----------

    async def get_by_id(self, product_id: str) -> dict | None:
        """按 ID 查询产品(注入实时库存), 不存在返回 None"""
        if is_redis_mode():
            product = await self._redis_get_by_id(product_id)
        else:
            product = self._mem_get_by_id(product_id)
        if product is None:
            return None
        # 注入库存(stock/reserved), 复用 InventoryRepository
        inv = await self.inventory_repo.get(product_id)
        product = dict(product)
        product["stock"] = inv["stock"] if inv else 0
        product["reserved"] = inv["reserved"] if inv else 0
        return product

    async def list_all(self) -> list[dict]:
        """列出所有产品(注入实时库存)"""
        if is_redis_mode():
            products = await self._redis_list_all()
        else:
            products = self._mem_list_all()
        return await self._inject_stock(products)

    async def list_products(self, filters: dict = None) -> list[dict]:
        """按筛选条件列出产品(支持 series/alcohol/volume/price_min/price_max/scene)

        Args:
            filters: {
                "series": str|None, "alcohol": int|None, "volume": str|None,
                "price_min": float|None, "price_max": float|None,
                "scene": str|None,
            }
        """
        products = await self.list_all()
        return self._apply_filters(products, filters or {})

    async def search(self, keyword: str) -> list[dict]:
        """关键词搜索(匹配 name/subtitle/series/tags)

        Args:
            keyword: 关键词(空串返回空列表)
        """
        if not keyword:
            return []
        products = await self.list_all()
        kw = keyword.strip().lower()
        result = []
        for p in products:
            haystacks = [
                str(p.get("name", "")),
                str(p.get("subtitle", "")),
                str(p.get("series", "")),
                str(p.get("brand", "")),
            ]
            haystacks.extend(str(t) for t in p.get("tags", []))
            haystacks.extend(str(s) for s in p.get("scenes", []))
            if any(kw in h.lower() for h in haystacks):
                result.append(p)
        return result

    async def get_hot_products(self, limit: int = 6) -> list[dict]:
        """热销推荐(按 sales_monthly 降序, 默认 6 条)"""
        products = await self.list_all()
        products.sort(key=lambda p: p.get("sales_monthly", 0), reverse=True)
        return products[: max(0, limit)]

    async def get_featured(self, limit: int = 4) -> list[dict]:
        """主推产品(featured=True, 默认 4 条)"""
        products = await self.list_all()
        featured = [p for p in products if p.get("featured")]
        # 主推产品按 hot_rank 升序(数字小优先级高)
        featured.sort(key=lambda p: p.get("hot_rank", 999))
        return featured[: max(0, limit)]

    async def get_related(self, product_id: str, limit: int = 4) -> list[dict]:
        """关联推荐(同系列优先, 其次同度数)"""
        products = await self.list_all()
        target = None
        for p in products:
            if p.get("product_id") == product_id:
                target = p
                break
        if not target:
            return []
        same_series = [p for p in products
                       if p.get("product_id") != product_id
                       and p.get("series") == target.get("series")]
        same_alcohol = [p for p in products
                        if p.get("product_id") != product_id
                        and p.get("alcohol") == target.get("alcohol")
                        and p not in same_series]
        rest = [p for p in products
                if p.get("product_id") != product_id
                and p not in same_series and p not in same_alcohol]
        return (same_series + same_alcohol + rest)[: max(0, limit)]

    async def get_categories(self) -> dict:
        """返回分类树(7大系列 / 度数 / 容量 / 价格区间 / 场景)

        返回结构:
            {
                "categories": [...],   # 与 PRODUCT_CATEGORIES 一致的列表
                "count": int,          # 顶级分类数量
            }
        """
        if is_redis_mode():
            return await self._redis_get_categories()
        return self._mem_get_categories()

    # ---------- 评价 ----------

    async def get_reviews(self, product_id: str,
                          page: int = 1, page_size: int = 10) -> tuple[list[dict], int]:
        """查询产品评价列表(分页)

        Returns:
            (reviews_page, total): 当前页评价列表 + 总数
        """
        if is_redis_mode():
            reviews = await self._redis_get_reviews(product_id)
        else:
            reviews = self._mem_get_reviews(product_id)
        # 按时间倒序
        reviews.sort(key=lambda r: r.get("created_at", ""), reverse=True)
        total = len(reviews)
        start = max(0, (page - 1) * page_size)
        end = start + page_size
        return reviews[start:end], total

    async def add_review(self, product_id: str, review: dict) -> dict:
        """新增评价(注入 review_id/created_at), 返回完整 review

        Raises:
            KeyError: 产品不存在
            ValueError: 评分越界
        """
        rating = review.get("rating")
        if not isinstance(rating, int) or not (1 <= rating <= 5):
            raise ValueError("评分必须为 1-5 的整数")

        product = await self.get_by_id(product_id)
        if not product:
            raise KeyError(f"产品 {product_id} 不存在")

        import time, random
        review_id = f"rv_{int(time.time() * 1000)}_{random.randint(1000, 9999)}"
        review = dict(review)
        review["review_id"] = review_id
        review.setdefault("created_at", _now_iso())
        review["product_id"] = product_id
        # P0 扩展字段(向后兼容: 现有评价无此字段时使用默认值)
        review.setdefault("order_id", "")
        review.setdefault("images", [])
        review.setdefault("status", REVIEW_STATUS_PUBLISHED)
        review.setdefault("is_anonymous", False)
        review.setdefault("reply_count", 0)
        review.setdefault("like_count", 0)
        review.setdefault("updated_at", "")

        if is_redis_mode():
            await self._redis_add_review(product_id, review)
        else:
            self._mem_add_review(product_id, review)
        # 评价写回后同步更新产品的 rating_avg/rating_count
        await self._update_rating_stats(product_id)
        return review

    async def _update_rating_stats(self, product_id: str) -> None:
        """根据当前评价列表重算并更新产品 rating_avg/rating_count"""
        if is_redis_mode():
            reviews = await self._redis_get_reviews(product_id)
        else:
            reviews = self._mem_get_reviews(product_id)
        count = len(reviews)
        if count == 0:
            return
        avg = round(sum(r.get("rating", 0) for r in reviews) / count, 2)
        if is_redis_mode():
            await self._redis_update_rating_fields(product_id, avg, count)
        else:
            self._mem_update_rating_fields(product_id, avg, count)

    # ============================================================
    # 评价扩展(P0): 单条查询/修改/删除/按订单查询
    # ============================================================

    async def get_review(self, product_id: str, review_id: str) -> dict | None:
        """查询单条评价(返回 None 表示不存在)"""
        if is_redis_mode():
            reviews = await self._redis_get_reviews(product_id)
        else:
            reviews = self._mem_get_reviews(product_id)
        for r in reviews:
            if r.get("review_id") == review_id:
                return r
        return None

    async def update_review(self, product_id: str, review_id: str,
                            fields: dict) -> dict | None:
        """更新评价字段(返回更新后的评价, None 表示不存在)

        注意: rating 变更时需调用方触发 _update_rating_stats
        """
        if is_redis_mode():
            return await self._redis_update_review(product_id, review_id, fields)
        return self._mem_update_review(product_id, review_id, fields)

    async def delete_review(self, product_id: str, review_id: str) -> bool:
        """删除评价(返回是否删除成功)"""
        if is_redis_mode():
            return await self._redis_delete_review(product_id, review_id)
        return self._mem_delete_review(product_id, review_id)

    async def get_review_by_order(self, order_id: str,
                                  product_id: str = None) -> dict | None:
        """按订单号查询评价(可选限定 product_id)

        Returns:
            评价 dict 或 None(未评价)
        """
        # 遍历评价查找 order_id 匹配项
        if product_id:
            # 指定了 product_id, 直接查该产品评价
            if is_redis_mode():
                reviews = await self._redis_get_reviews(product_id)
            else:
                reviews = self._mem_get_reviews(product_id)
            for r in reviews:
                if r.get("order_id") == order_id:
                    return r
            return None
        # 未指定 product_id, 遍历所有产品评价
        if is_redis_mode():
            all_products = await self._redis_list_all()
        else:
            all_products = self._mem_list_all()
        for p in all_products:
            pid = p.get("product_id")
            if not pid:
                continue
            if is_redis_mode():
                reviews = await self._redis_get_reviews(pid)
            else:
                reviews = self._mem_get_reviews(pid)
            for r in reviews:
                if r.get("order_id") == order_id:
                    return r
        return None

    async def list_reviews_by_member(self, member_id: str,
                                      limit: int = 50) -> list:
        """按会员查询评价历史"""
        if is_redis_mode():
            all_products = await self._redis_list_all()
        else:
            all_products = self._mem_list_all()
        result = []
        for p in all_products:
            pid = p.get("product_id")
            if not pid:
                continue
            if is_redis_mode():
                reviews = await self._redis_get_reviews(pid)
            else:
                reviews = self._mem_get_reviews(pid)
            for r in reviews:
                if str(r.get("member_id", "")) == str(member_id):
                    r = dict(r)
                    r["product_id"] = pid
                    result.append(r)
        # 按时间倒序
        result.sort(key=lambda r: r.get("created_at", ""), reverse=True)
        return result[:limit]

    # ============================================================
    # 评价回复(P0)
    # ============================================================

    async def add_reply(self, review_id: str, reply: dict) -> dict:
        """新增评价回复(注入 reply_id/created_at)"""
        import time, random
        reply_id = f"rp_{int(time.time() * 1000)}_{random.randint(1000, 9999)}"
        reply = dict(reply)
        reply["reply_id"] = reply_id
        reply.setdefault("created_at", _now_iso())
        reply["review_id"] = review_id

        if is_redis_mode():
            await self._redis_add_reply(review_id, reply)
        else:
            self._mem_add_reply(review_id, reply)
        return reply

    async def get_replies(self, review_id: str) -> list:
        """查询评价回复列表(按时间正序)"""
        if is_redis_mode():
            return await self._redis_get_replies(review_id)
        return self._mem_get_replies(review_id)

    async def delete_reply(self, review_id: str, reply_id: str) -> bool:
        """删除单条回复(返回是否删除成功)"""
        if is_redis_mode():
            return await self._redis_delete_reply(review_id, reply_id)
        return self._mem_delete_reply(review_id, reply_id)

    # ============================================================
    # 评价点赞(P1)
    # ============================================================

    async def add_like(self, review_id: str, member_id: str) -> bool:
        """点赞(已点赞返回 False, 首次点赞返回 True)"""
        if is_redis_mode():
            added = await self._redis_add_like(review_id, member_id)
        else:
            added = self._mem_add_like(review_id, member_id)
        if added:
            await self._increment_review_like_count(review_id, 1)
        return added

    async def remove_like(self, review_id: str, member_id: str) -> bool:
        """取消点赞(已点赞返回 True, 未点赞返回 False)"""
        if is_redis_mode():
            removed = await self._redis_remove_like(review_id, member_id)
        else:
            removed = self._mem_remove_like(review_id, member_id)
        if removed:
            await self._increment_review_like_count(review_id, -1)
        return removed

    async def is_liked(self, review_id: str, member_id: str) -> bool:
        """是否已点赞"""
        if is_redis_mode():
            return await self._redis_is_liked(review_id, member_id)
        return self._mem_is_liked(review_id, member_id)

    async def get_like_count(self, review_id: str) -> int:
        """点赞数"""
        if is_redis_mode():
            return await self._redis_get_like_count(review_id)
        return self._mem_get_like_count(review_id)

    async def _increment_review_like_count(self, review_id: str, delta: int):
        """更新评价的 like_count 字段(需遍历找到对应评价)"""
        if is_redis_mode():
            await self._redis_increment_like_count(review_id, delta)
        else:
            self._mem_increment_like_count(review_id, delta)

    # ============================================================
    # 评价举报(P1)
    # ============================================================

    async def create_report(self, report: dict) -> dict:
        """创建举报(注入 report_id/created_at/status)"""
        import time, random
        report_id = f"rpt_{int(time.time() * 1000)}_{random.randint(1000, 9999)}"
        report = dict(report)
        report["report_id"] = report_id
        report.setdefault("created_at", _now_iso())
        report.setdefault("status", REPORT_STATUS_PENDING)
        report.setdefault("handler_id", "")
        report.setdefault("handle_remark", "")
        report.setdefault("handled_at", "")

        if is_redis_mode():
            await self._redis_create_report(report_id, report)
        else:
            self._mem_create_report(report_id, report)
        return report

    async def get_report(self, report_id: str) -> dict | None:
        """查询单条举报"""
        if is_redis_mode():
            return await self._redis_get_report(report_id)
        return self._mem_get_report(report_id)

    async def list_reports(self, status: str = None,
                           limit: int = 50) -> list:
        """举报列表(可按状态筛选)"""
        if is_redis_mode():
            return await self._redis_list_reports(status, limit)
        return self._mem_list_reports(status, limit)

    async def update_report(self, report_id: str, fields: dict) -> dict | None:
        """更新举报字段"""
        if is_redis_mode():
            return await self._redis_update_report(report_id, fields)
        return self._mem_update_report(report_id, fields)

    async def get_report_by_review_reporter(self, review_id: str,
                                            reporter_id: str) -> dict | None:
        """查询用户是否已举报某评价(防重复举报)"""
        if is_redis_mode():
            return await self._redis_get_report_by_review_reporter(
                review_id, reporter_id)
        return self._mem_get_report_by_review_reporter(review_id, reporter_id)

    # ============================================================
    # 筛选/排序辅助
    # ============================================================

    def _apply_filters(self, products: list[dict], filters: dict) -> list[dict]:
        """应用筛选条件(支持 series/alcohol/volume/price_min/price_max/scene)"""
        result = products
        series = filters.get("series")
        alcohol = filters.get("alcohol")
        volume = filters.get("volume")
        price_min = filters.get("price_min")
        price_max = filters.get("price_max")
        scene = filters.get("scene")

        if series:
            result = [p for p in result if p.get("series") == series]
        if alcohol is not None and alcohol != "":
            try:
                alcohol_int = int(alcohol)
                result = [p for p in result if p.get("alcohol") == alcohol_int]
            except (TypeError, ValueError):
                pass
        if volume:
            result = [p for p in result if p.get("volume") == volume]
        if price_min is not None:
            try:
                pmin = float(price_min)
                result = [p for p in result if p.get("price", 0) >= pmin]
            except (TypeError, ValueError):
                pass
        if price_max is not None:
            try:
                pmax = float(price_max)
                result = [p for p in result if p.get("price", 0) <= pmax]
            except (TypeError, ValueError):
                pass
        if scene:
            result = [p for p in result if scene in (p.get("scenes") or [])]
        # 仅返回在售产品
        result = [p for p in result if p.get("status") == "on_sale"]
        return result

    async def _inject_stock(self, products: list[dict]) -> list[dict]:
        """批量注入库存字段(stock/reserved)"""
        result = []
        for p in products:
            inv = await self.inventory_repo.get(p.get("product_id"))
            new_p = dict(p)
            new_p["stock"] = inv["stock"] if inv else 0
            new_p["reserved"] = inv["reserved"] if inv else 0
            result.append(new_p)
        return result

    # ============================================================
    # 内存后端
    # ============================================================

    def _ensure_store(self):
        """确保 store 包含 products / product_reviews / review_replies / review_likes / review_reports 键"""
        if "products" not in self.store:
            self.store["products"] = {}
        if "product_reviews" not in self.store:
            self.store["product_reviews"] = {}
        if "review_replies" not in self.store:
            self.store["review_replies"] = {}
        if "review_likes" not in self.store:
            self.store["review_likes"] = {}
        if "review_reports" not in self.store:
            self.store["review_reports"] = {}

    def _mem_get_by_id(self, product_id: str) -> dict | None:
        self._ensure_store()
        return self.store["products"].get(product_id)

    def _mem_list_all(self) -> list[dict]:
        self._ensure_store()
        return list(self.store["products"].values())

    def _mem_get_reviews(self, product_id: str) -> list[dict]:
        self._ensure_store()
        return list(self.store["product_reviews"].get(product_id, []))

    def _mem_add_review(self, product_id: str, review: dict) -> None:
        self._ensure_store()
        if product_id not in self.store["product_reviews"]:
            self.store["product_reviews"][product_id] = []
        self.store["product_reviews"][product_id].append(review)

    def _mem_update_rating_fields(self, product_id: str, avg: float, count: int) -> None:
        self._ensure_store()
        product = self.store["products"].get(product_id)
        if product is None:
            return
        product["rating_avg"] = avg
        product["rating_count"] = count

    def _mem_get_categories(self) -> dict:
        import copy
        return {
            "categories": copy.deepcopy(PRODUCT_CATEGORIES),
            "count": len(PRODUCT_CATEGORIES),
        }

    # ---------- 评价扩展(内存) ----------

    def _mem_update_review(self, product_id: str, review_id: str,
                           fields: dict) -> dict | None:
        """更新评价字段(内存模式)"""
        self._ensure_store()
        reviews = self.store["product_reviews"].get(product_id, [])
        for r in reviews:
            if r.get("review_id") == review_id:
                r.update(fields)
                return r
        return None

    def _mem_delete_review(self, product_id: str, review_id: str) -> bool:
        """删除评价(内存模式)"""
        self._ensure_store()
        reviews = self.store["product_reviews"].get(product_id, [])
        for i, r in enumerate(reviews):
            if r.get("review_id") == review_id:
                reviews.pop(i)
                return True
        return False

    # ---------- 评价回复(内存) ----------

    def _mem_add_reply(self, review_id: str, reply: dict) -> None:
        """新增回复(内存模式)"""
        self._ensure_store()
        if review_id not in self.store["review_replies"]:
            self.store["review_replies"][review_id] = []
        self.store["review_replies"][review_id].append(reply)

    def _mem_get_replies(self, review_id: str) -> list:
        """查询回复列表(内存模式, 按时间正序)"""
        self._ensure_store()
        replies = list(self.store["review_replies"].get(review_id, []))
        replies.sort(key=lambda r: r.get("created_at", ""))
        return replies

    def _mem_delete_reply(self, review_id: str, reply_id: str) -> bool:
        """删除回复(内存模式)"""
        self._ensure_store()
        replies = self.store["review_replies"].get(review_id, [])
        for i, r in enumerate(replies):
            if r.get("reply_id") == reply_id:
                replies.pop(i)
                return True
        return False

    # ---------- 评价点赞(内存) ----------

    def _mem_add_like(self, review_id: str, member_id: str) -> bool:
        """点赞(内存模式, Set 语义)"""
        self._ensure_store()
        if review_id not in self.store["review_likes"]:
            self.store["review_likes"][review_id] = set()
        if member_id in self.store["review_likes"][review_id]:
            return False
        self.store["review_likes"][review_id].add(member_id)
        return True

    def _mem_remove_like(self, review_id: str, member_id: str) -> bool:
        """取消点赞(内存模式)"""
        self._ensure_store()
        likes = self.store["review_likes"].get(review_id, set())
        if member_id not in likes:
            return False
        likes.discard(member_id)
        return True

    def _mem_is_liked(self, review_id: str, member_id: str) -> bool:
        """是否已点赞(内存模式)"""
        self._ensure_store()
        return member_id in self.store["review_likes"].get(review_id, set())

    def _mem_get_like_count(self, review_id: str) -> int:
        """点赞数(内存模式)"""
        self._ensure_store()
        return len(self.store["review_likes"].get(review_id, set()))

    def _mem_increment_like_count(self, review_id: str, delta: int):
        """更新评价的 like_count 字段(内存模式, 遍历所有产品评价)"""
        self._ensure_store()
        for product_id, reviews in self.store["product_reviews"].items():
            for r in reviews:
                if r.get("review_id") == review_id:
                    current = r.get("like_count", 0)
                    r["like_count"] = max(0, current + delta)
                    return

    # ---------- 评价举报(内存) ----------

    def _mem_create_report(self, report_id: str, report: dict) -> None:
        """创建举报(内存模式)"""
        self._ensure_store()
        self.store["review_reports"][report_id] = report

    def _mem_get_report(self, report_id: str) -> dict | None:
        """查询举报(内存模式)"""
        self._ensure_store()
        return self.store["review_reports"].get(report_id)

    def _mem_list_reports(self, status: str = None,
                          limit: int = 50) -> list:
        """举报列表(内存模式, 按时间倒序)"""
        self._ensure_store()
        reports = list(self.store["review_reports"].values())
        if status:
            reports = [r for r in reports if r.get("status") == status]
        reports.sort(key=lambda r: r.get("created_at", ""), reverse=True)
        return reports[:limit]

    def _mem_update_report(self, report_id: str,
                           fields: dict) -> dict | None:
        """更新举报(内存模式)"""
        self._ensure_store()
        report = self.store["review_reports"].get(report_id)
        if not report:
            return None
        report.update(fields)
        return report

    def _mem_get_report_by_review_reporter(self, review_id: str,
                                            reporter_id: str) -> dict | None:
        """查询用户是否已举报某评价(内存模式)"""
        self._ensure_store()
        for r in self.store["review_reports"].values():
            if r.get("review_id") == review_id and r.get("reporter_id") == reporter_id:
                return r
        return None

    # ============================================================
    # Redis 后端
    # ============================================================

    async def _redis_get_by_id(self, product_id: str) -> dict | None:
        client = await get_redis_client()
        data = await client.hgetall(_k("product", product_id))
        if not data:
            return None
        return self._deserialize_product(data)

    async def _redis_list_all(self) -> list[dict]:
        client = await get_redis_client()
        keys = await client.keys(_k("product", "*"))
        result = []
        for key in keys:
            # 排除 categories / reviews:* 键
            if key.endswith(":product:categories") or ":reviews:" in key:
                continue
            data = await client.hgetall(key)
            if data:
                result.append(self._deserialize_product(data))
        return result

    async def _redis_get_categories(self) -> dict:
        client = await get_redis_client()
        items = await client.lrange(_k("product", "categories"), 0, -1)
        if not items:
            # 兜底: Redis 未 seed 时返回常量
            import copy
            return {
                "categories": copy.deepcopy(PRODUCT_CATEGORIES),
                "count": len(PRODUCT_CATEGORIES),
            }
        categories = []
        for raw in items:
            try:
                categories.append(json.loads(raw))
            except (TypeError, ValueError):
                continue
        return {"categories": categories, "count": len(categories)}

    async def _redis_get_reviews(self, product_id: str) -> list[dict]:
        client = await get_redis_client()
        items = await client.lrange(_k("product", "reviews", product_id), 0, -1)
        reviews = []
        for raw in items:
            try:
                reviews.append(json.loads(raw))
            except (TypeError, ValueError):
                continue
        return reviews

    async def _redis_add_review(self, product_id: str, review: dict) -> None:
        client = await get_redis_client()
        await client.rpush(_k("product", "reviews", product_id),
                           json.dumps(review, ensure_ascii=False))

    async def _redis_update_rating_fields(self, product_id: str,
                                          avg: float, count: int) -> None:
        client = await get_redis_client()
        key = _k("product", product_id)
        if not await client.exists(key):
            return
        await client.hset(key, mapping={
            "rating_avg": avg,
            "rating_count": count,
        })

    # ---------- 评价扩展(Redis) ----------

    async def _redis_update_review(self, product_id: str, review_id: str,
                                   fields: dict) -> dict | None:
        """更新评价字段(Redis 模式, List 需读取→修改→写回)"""
        client = await get_redis_client()
        key = _k("product", "reviews", product_id)
        items = await client.lrange(key, 0, -1)
        for i, raw in enumerate(items):
            try:
                review = json.loads(raw)
            except (TypeError, ValueError):
                continue
            if review.get("review_id") == review_id:
                review.update(fields)
                await client.lset(key, i, json.dumps(review, ensure_ascii=False))
                return review
        return None

    async def _redis_delete_review(self, product_id: str, review_id: str) -> bool:
        """删除评价(Redis 模式, List 需读取→删除→重写)"""
        client = await get_redis_client()
        key = _k("product", "reviews", product_id)
        items = await client.lrange(key, 0, -1)
        for i, raw in enumerate(items):
            try:
                review = json.loads(raw)
            except (TypeError, ValueError):
                continue
            if review.get("review_id") == review_id:
                # 用占位值替换后删除(redis lrem 按值删除)
                await client.lset(key, i, "__DELETED__")
                await client.lrem(key, 1, "__DELETED__")
                return True
        return False

    # ---------- 评价回复(Redis) ----------

    async def _redis_add_reply(self, review_id: str, reply: dict) -> None:
        """新增回复(Redis 模式, List rpush)"""
        client = await get_redis_client()
        await client.rpush(_k("product", "review", "replies", review_id),
                          json.dumps(reply, ensure_ascii=False))

    async def _redis_get_replies(self, review_id: str) -> list:
        """查询回复列表(Redis 模式, List lrange 按时间正序)"""
        client = await get_redis_client()
        items = await client.lrange(
            _k("product", "review", "replies", review_id), 0, -1)
        replies = []
        for raw in items:
            try:
                replies.append(json.loads(raw))
            except (TypeError, ValueError):
                continue
        return replies

    async def _redis_delete_reply(self, review_id: str, reply_id: str) -> bool:
        """删除回复(Redis 模式, List lset + lrem)"""
        client = await get_redis_client()
        key = _k("product", "review", "replies", review_id)
        items = await client.lrange(key, 0, -1)
        for i, raw in enumerate(items):
            try:
                reply = json.loads(raw)
            except (TypeError, ValueError):
                continue
            if reply.get("reply_id") == reply_id:
                await client.lset(key, i, "__DELETED__")
                await client.lrem(key, 1, "__DELETED__")
                return True
        return False

    # ---------- 评价点赞(Redis) ----------

    async def _redis_add_like(self, review_id: str, member_id: str) -> bool:
        """点赞(Redis 模式, Set sadd 返回新增数)"""
        client = await get_redis_client()
        added = await client.sadd(
            _k("product", "review", "likes", review_id), member_id)
        return added > 0

    async def _redis_remove_like(self, review_id: str, member_id: str) -> bool:
        """取消点赞(Redis 模式, Set srem 返回移除数)"""
        client = await get_redis_client()
        removed = await client.srem(
            _k("product", "review", "likes", review_id), member_id)
        return removed > 0

    async def _redis_is_liked(self, review_id: str, member_id: str) -> bool:
        """是否已点赞(Redis 模式, Set sismember)"""
        client = await get_redis_client()
        return await client.sismember(
            _k("product", "review", "likes", review_id), member_id)

    async def _redis_get_like_count(self, review_id: str) -> int:
        """点赞数(Redis 模式, Set scard)"""
        client = await get_redis_client()
        return await client.scard(_k("product", "review", "likes", review_id))

    async def _redis_increment_like_count(self, review_id: str, delta: int):
        """更新评价 like_count 字段(Redis 模式, 遍历产品 List 修改)"""
        client = await get_redis_client()
        # 遍历所有产品评价列表, 找到对应 review_id 并更新 like_count
        keys = await client.keys(_k("product", "reviews", "*"))
        for key in keys:
            if key.endswith(":product:reviews") or ":review:" in key:
                continue
            items = await client.lrange(key, 0, -1)
            for i, raw in enumerate(items):
                try:
                    review = json.loads(raw)
                except (TypeError, ValueError):
                    continue
                if review.get("review_id") == review_id:
                    current = review.get("like_count", 0)
                    review["like_count"] = max(0, current + delta)
                    await client.lset(
                        key, i, json.dumps(review, ensure_ascii=False))
                    return

    # ---------- 评价举报(Redis) ----------

    async def _redis_create_report(self, report_id: str, report: dict) -> None:
        """创建举报(Redis 模式, String(JSON))"""
        client = await get_redis_client()
        await client.set(_k("product", "review", "report", report_id),
                        json.dumps(report, ensure_ascii=False))

    async def _redis_get_report(self, report_id: str) -> dict | None:
        """查询举报(Redis 模式)"""
        client = await get_redis_client()
        raw = await client.get(_k("product", "review", "report", report_id))
        if not raw:
            return None
        try:
            return json.loads(raw)
        except (TypeError, ValueError):
            return None

    async def _redis_list_reports(self, status: str = None,
                                 limit: int = 50) -> list:
        """举报列表(Redis 模式, 遍历 report:* 键)"""
        client = await get_redis_client()
        keys = await client.keys(_k("product", "review", "report", "*"))
        reports = []
        for key in keys:
            raw = await client.get(key)
            if not raw:
                continue
            try:
                report = json.loads(raw)
            except (TypeError, ValueError):
                continue
            if status and report.get("status") != status:
                continue
            reports.append(report)
        reports.sort(key=lambda r: r.get("created_at", ""), reverse=True)
        return reports[:limit]

    async def _redis_update_report(self, report_id: str,
                                  fields: dict) -> dict | None:
        """更新举报(Redis 模式, 读取→修改→写回)"""
        client = await get_redis_client()
        key = _k("product", "review", "report", report_id)
        raw = await client.get(key)
        if not raw:
            return None
        try:
            report = json.loads(raw)
        except (TypeError, ValueError):
            return None
        report.update(fields)
        await client.set(key, json.dumps(report, ensure_ascii=False))
        return report

    async def _redis_get_report_by_review_reporter(self, review_id: str,
                                                   reporter_id: str) -> dict | None:
        """查询用户是否已举报某评价(Redis 模式, 遍历 report:* 键)"""
        client = await get_redis_client()
        keys = await client.keys(_k("product", "review", "report", "*"))
        for key in keys:
            raw = await client.get(key)
            if not raw:
                continue
            try:
                report = json.loads(raw)
            except (TypeError, ValueError):
                continue
            if (report.get("review_id") == review_id
                    and report.get("reporter_id") == reporter_id):
                return report
        return None

    # ============================================================
    # 序列化辅助(Redis Hash 要求 value 为 str/int/float)
    # ============================================================

    def _serialize_product(self, product: dict) -> dict:
        """将产品 dict 序列化为 Redis Hash 兼容的 mapping

        嵌套结构(tags/scenes/attributes/images/featured)序列化为 JSON 字符串。
        """
        json_fields = ("tags", "scenes", "attributes", "images")
        result = {}
        for k, v in product.items():
            if v is None:
                continue
            if k in json_fields:
                result[k] = json.dumps(v, ensure_ascii=False)
            elif isinstance(v, bool):
                result[k] = 1 if v else 0
            elif isinstance(v, (int, float)):
                result[k] = v
            else:
                result[k] = str(v)
        return result

    def _deserialize_product(self, data: dict) -> dict:
        """将 Redis hgetall 返回的 dict 反序列化(还原嵌套结构 + 类型)"""
        def _to_int(v):
            try:
                return int(v)
            except (TypeError, ValueError):
                return v

        def _to_float(v):
            try:
                return float(v)
            except (TypeError, ValueError):
                return v

        result = dict(data)
        # 还原 JSON 嵌套字段
        for k in ("tags", "scenes", "attributes", "images"):
            if k in result and isinstance(result[k], str):
                try:
                    result[k] = json.loads(result[k])
                except (TypeError, ValueError):
                    pass
        # 类型还原
        for k in ("alcohol", "sales_monthly", "sales_total", "rating_count"):
            if k in result:
                result[k] = _to_int(result[k])
        for k in ("price", "original_price", "member_price", "svip_price", "rating_avg"):
            if k in result:
                result[k] = _to_float(result[k])
        if "featured" in result:
            result["featured"] = _to_int(result["featured"]) == 1
        if "hot_rank" in result:
            result["hot_rank"] = _to_int(result["hot_rank"])
        return result


def _now_iso() -> str:
    """ISO8601 UTC 时间戳"""
    from datetime import datetime
    return datetime.now(UTC).isoformat()
