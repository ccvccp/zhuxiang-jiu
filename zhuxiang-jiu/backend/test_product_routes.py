"""产品展示模块单元测试

覆盖 7 个端点(共 50+ 测试):
  - GET  /api/product/categories                    分类导航
  - GET  /api/product/list                          产品列表(筛选+排序+分页)
  - GET  /api/product/search                        关键词搜索
  - GET  /api/product/hot                           热销推荐
  - GET  /api/product/featured                      主推产品
  - GET  /api/product/{product_id}                  产品详情
  - GET  /api/product/{product_id}/reviews          评价列表
  - POST /api/product/{product_id}/reviews           提交评价

测试维度:
  - 成功路径 / 错误路径(404/409/401)
  - 筛选(series/alcohol/volume/price/scene)
  - 排序(comprehensive/sales/price_asc/price_desc/new/rating)
  - 分页(page/page_size/totalPages)
  - 搜索(关键词匹配/空关键词/分页)
  - 推荐热销/主推/关联(详情内)
  - 评价CRUD + 评分越界 + 内容校验 + 未登录 401

商品: 11 款(经典/珍藏/年份/礼盒/便携/典藏/竹香系列)
库存: 复用 _mock_store["inventory"](ZX42-2026L07 stock=500)

运行: pytest test_product_routes.py -v
"""

import pytest
from fastapi.testclient import TestClient

from main import app, _mock_store
from repositories.store import reset_store

client = TestClient(app)

# 已知产品ID(用于详情/评价测试)
PID_CLASSIC_42 = "ZX42-2026L07"     # 经典系列 42° 500ml  ¥268 stock=500
PID_CLASSIC_45 = "ZX45-2026L05"     # 经典系列 45° 500ml  ¥368 stock=300
PID_TREASURE = "ZX53-2026Z01"       # 珍藏系列 53° 500ml  ¥698 stock=200
PID_PORTABLE = "ZX42-2026B01"       # 便携系列 42° 250ml  ¥88  stock=800
PID_BAMBOO_X1 = "ZX52-2026X01"     # 竹香系列 52° 500ml  ¥398 stock=300
PID_BAMBOO_X3 = "ZX52-2026X03"     # 竹香系列 52° 500ml×2 ¥1888 stock=60


@pytest.fixture(autouse=True)
def _reset_product_store():
    """每个测试前重置 store 到初始状态(11 款产品 + 2 条种子评价)"""
    reset_store()
    yield


# ============================================================
#  分类导航: GET /api/product/categories
# ============================================================

class TestProductCategories:
    """分类导航(6)"""

    def test_categories_success(self):
        """正常返回分类树"""
        resp = client.get("/api/product/categories")
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["count"] == 5  # 系列/度数/容量/价格区间/场景
        keys = {c["key"] for c in data["categories"]}
        assert keys == {"series", "alcohol", "volume", "price", "scene"}

    def test_categories_has_7_series(self):
        """分类树包含 7 大系列"""
        resp = client.get("/api/product/categories")
        series = next(c for c in resp.json()["categories"] if c["key"] == "series")
        assert len(series["items"]) == 7
        series_codes = {s["code"] for s in series["items"]}
        assert "经典系列" in series_codes
        assert "竹香系列" in series_codes

    def test_categories_has_5_alcohol(self):
        """分类树包含 5 个度数"""
        resp = client.get("/api/product/categories")
        alcohol = next(c for c in resp.json()["categories"] if c["key"] == "alcohol")
        assert len(alcohol["items"]) == 5

    def test_categories_has_4_volumes(self):
        """分类树包含 4 个容量"""
        resp = client.get("/api/product/categories")
        volume = next(c for c in resp.json()["categories"] if c["key"] == "volume")
        assert len(volume["items"]) == 4

    def test_categories_has_5_price_ranges(self):
        """分类树包含 5 个价格区间"""
        resp = client.get("/api/product/categories")
        price = next(c for c in resp.json()["categories"] if c["key"] == "price")
        assert len(price["items"]) == 5

    def test_categories_has_logs(self):
        """响应包含 logs"""
        resp = client.get("/api/product/categories")
        assert resp.json()["logs"]
        assert "分类导航" in resp.json()["logs"][0]["step"]


# ============================================================
#  产品列表: GET /api/product/list
# ============================================================

class TestProductList:
    """产品列表(15): 筛选+排序+分页+错误"""

    def test_list_default(self):
        """默认参数: 返回全部 11 款(分页 1/12)"""
        resp = client.get("/api/product/list")
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["total"] == 11
        assert data["page"] == 1
        assert data["pageSize"] == 12
        assert data["totalPages"] == 1
        assert data["count"] == 11
        assert data["sort"] == "comprehensive"

    def test_list_filter_series(self):
        """按系列筛选: 经典系列 → 2 款"""
        resp = client.get("/api/product/list?series=经典系列")
        data = resp.json()
        assert data["total"] == 2
        for p in data["products"]:
            assert p["series"] == "经典系列"

    def test_list_filter_category_alias(self):
        """category 字段是 series 的别名"""
        resp = client.get("/api/product/list?category=经典系列")
        data = resp.json()
        assert data["total"] == 2

    def test_list_filter_alcohol(self):
        """按度数筛选: 53° → 3 款(Z/Z01/N10/N20 中 Z01/N10/N20)"""
        resp = client.get("/api/product/list?alcohol=53")
        data = resp.json()
        assert data["total"] == 3  # ZX53-2026Z01, ZX53-2026N10, ZX53-2026N20
        for p in data["products"]:
            assert p["alcohol"] == 53

    def test_list_filter_volume(self):
        """按容量筛选: 250ml → 1 款(便携)"""
        resp = client.get("/api/product/list?volume=250ml")
        data = resp.json()
        assert data["total"] == 1
        assert data["products"][0]["product_id"] == PID_PORTABLE

    def test_list_filter_price_min(self):
        """价格下限: >=1000 → 5 款(ZX53N20/礼盒/典藏/竹香尊享大瓶/竹香礼盒)"""
        resp = client.get("/api/product/list?price_min=1000")
        data = resp.json()
        # 1000+: 礼盒(1288) 年份20(2688) 典藏(1580) 尊享750ml(998? no)
        # 实际: 礼盒1288, 年份20 2688, 典藏1580, 尊享礼盒1888 → 4 款
        assert data["total"] == 4
        for p in data["products"]:
            assert p["price"] >= 1000

    def test_list_filter_price_max(self):
        """价格上限: <=200 → 1 款(便携 88)"""
        resp = client.get("/api/product/list?price_max=200")
        data = resp.json()
        assert data["total"] == 1
        assert data["products"][0]["price"] <= 200

    def test_list_filter_price_range(self):
        """价格区间: 200-500 → 经典42(268)/经典45(368)/便携X1(398)"""
        resp = client.get("/api/product/list?price_min=200&price_max=500")
        data = resp.json()
        for p in data["products"]:
            assert 200 <= p["price"] <= 500

    def test_list_filter_scene(self):
        """按场景筛选: 收藏投资 → ZX53N20/Z01/N10/X03"""
        resp = client.get("/api/product/list?scene=收藏投资")
        data = resp.json()
        assert data["total"] >= 3
        for p in data["products"]:
            assert "收藏投资" in p["scenes"]

    def test_list_filter_combined(self):
        """组合筛选: 经典系列 + 42° → 1 款(ZX42-2026L07)"""
        resp = client.get("/api/product/list?series=经典系列&alcohol=42")
        data = resp.json()
        assert data["total"] == 1
        assert data["products"][0]["product_id"] == PID_CLASSIC_42

    def test_list_filter_no_match(self):
        """筛选无命中: 返回空"""
        resp = client.get("/api/product/list?series=不存在的系列")
        data = resp.json()
        assert data["total"] == 0
        assert data["products"] == []

    def test_list_sort_sales(self):
        """排序 sales: 销量降序, 第一条是便携(月销 2400)"""
        resp = client.get("/api/product/list?sort=sales")
        data = resp.json()
        assert data["products"][0]["product_id"] == PID_PORTABLE
        # 验证降序
        sales = [p["sales_monthly"] for p in data["products"]]
        assert sales == sorted(sales, reverse=True)

    def test_list_sort_price_asc(self):
        """排序 price_asc: 价格升序"""
        resp = client.get("/api/product/list?sort=price_asc")
        prices = [p["price"] for p in resp.json()["products"]]
        assert prices == sorted(prices)

    def test_list_sort_price_desc(self):
        """排序 price_desc: 价格降序"""
        resp = client.get("/api/product/list?sort=price_desc")
        prices = [p["price"] for p in resp.json()["products"]]
        assert prices == sorted(prices, reverse=True)

    def test_list_sort_new(self):
        """排序 new: 上架时间降序, 顶部应是最新的便携(2026-08-10)"""
        resp = client.get("/api/product/list?sort=new")
        data = resp.json()
        assert data["products"][0]["product_id"] == PID_PORTABLE

    def test_list_sort_rating(self):
        """排序 rating: 评分降序"""
        resp = client.get("/api/product/list?sort=rating")
        ratings = [p["rating_avg"] for p in resp.json()["products"]]
        assert ratings == sorted(ratings, reverse=True)

    def test_list_pagination_basic(self):
        """分页: page_size=5 → 第 1 页 5 条, totalPages=3"""
        resp = client.get("/api/product/list?page=1&page_size=5")
        data = resp.json()
        assert data["page"] == 1
        assert data["pageSize"] == 5
        assert data["total"] == 11
        assert data["totalPages"] == 3  # ceil(11/5)
        assert data["count"] == 5

    def test_list_pagination_last_page(self):
        """分页: 末页 page=3 (page_size=5) 应返回 1 条"""
        resp = client.get("/api/product/list?page=3&page_size=5")
        data = resp.json()
        assert data["count"] == 1

    def test_list_pagination_empty_page(self):
        """分页: 超出范围页 → 空列表但 total 正确"""
        resp = client.get("/api/product/list?page=99&page_size=10")
        data = resp.json()
        assert data["total"] == 11
        assert data["count"] == 0
        assert data["products"] == []

    def test_list_invalid_sort(self):
        """非法排序: 409"""
        resp = client.get("/api/product/list?sort=invalid")
        assert resp.status_code == 409
        assert "排序方式不支持" in resp.json()["error"]

    def test_list_invalid_page(self):
        """非法 page(0): 422(Query ge=1)"""
        resp = client.get("/api/product/list?page=0")
        assert resp.status_code == 422

    def test_list_invalid_page_size(self):
        """非法 page_size(0): 422"""
        resp = client.get("/api/product/list?page_size=0")
        assert resp.status_code == 422

    def test_list_invalid_alcohol_type(self):
        """alcohol 非整数: 409"""
        resp = client.get("/api/product/list?alcohol=abc")
        assert resp.status_code == 409
        assert "alcohol" in resp.json()["error"]

    def test_list_invalid_price_min(self):
        """price_min 非数字: 409"""
        resp = client.get("/api/product/list?price_min=abc")
        assert resp.status_code == 409

    def test_list_summary_fields(self):
        """列表项字段: summary 不含 description/attributes"""
        resp = client.get("/api/product/list?page_size=1")
        item = resp.json()["products"][0]
        # 关键字段
        for k in ("product_id", "name", "subtitle", "series", "alcohol",
                  "volume", "price", "member_price", "svip_price",
                  "stock", "rating_avg", "rating_count", "tags", "image"):
            assert k in item
        # summary 不暴露完整 description
        assert "description" not in item
        assert "attributes" not in item

    def test_list_member_price_discount(self):
        """会员价 = 9 折, SVIP 价 = 8.5 折"""
        resp = client.get("/api/product/list?series=经典系列&alcohol=42")
        p = resp.json()["products"][0]
        assert p["price"] == 268
        assert p["member_price"] == round(268 * 0.9, 2)  # 241.2
        assert p["svip_price"] == round(268 * 0.85, 2)  # 227.8

    def test_list_stock_injected(self):
        """列表项注入实时库存: ZX42-2026L07 stock=500"""
        resp = client.get(f"/api/product/list?series=经典系列&alcohol=42")
        p = resp.json()["products"][0]
        assert p["stock"] == 500

    def test_list_logs_structure(self):
        """响应 logs 包含 筛选/排序/分页 三步"""
        resp = client.get("/api/product/list?series=经典系列")
        steps = [log["step"] for log in resp.json()["logs"]]
        assert "筛选" in steps
        assert "排序" in steps
        assert "分页" in steps


# ============================================================
#  搜索: GET /api/product/search
# ============================================================

class TestProductSearch:
    """搜索(7)"""

    def test_search_by_name(self):
        """按名称搜索: 竹奕·竹香经典 → 经典系列 2 款"""
        resp = client.get("/api/product/search?keyword=竹香经典")
        data = resp.json()
        assert data["success"] is True
        assert data["total"] == 2

    def test_search_by_series(self):
        """按系列搜索: 珍藏 → 1 款"""
        resp = client.get("/api/product/search?keyword=珍藏")
        data = resp.json()
        assert data["total"] == 1
        assert data["products"][0]["series"] == "珍藏系列"

    def test_search_by_tag(self):
        """按标签搜索: 旗舰 → 竹香系列 3 款"""
        resp = client.get("/api/product/search?keyword=旗舰")
        data = resp.json()
        assert data["total"] == 3

    def test_search_by_brand(self):
        """按品牌搜索: 竹奕 → 全部 11 款"""
        resp = client.get("/api/product/search?keyword=竹奕")
        data = resp.json()
        assert data["total"] == 11

    def test_search_no_match(self):
        """无命中关键词"""
        resp = client.get("/api/product/search?keyword=不存在的酒")
        data = resp.json()
        assert data["total"] == 0
        assert data["products"] == []

    def test_search_empty_keyword(self):
        """空关键词: 409"""
        resp = client.get("/api/product/search?keyword=")
        assert resp.status_code == 409
        assert "不能为空" in resp.json()["error"]

    def test_search_pagination(self):
        """搜索分页: keyword=竹奕 page_size=5 → totalPages=3"""
        resp = client.get("/api/product/search?keyword=竹奕&page_size=5")
        data = resp.json()
        assert data["total"] == 11
        assert data["count"] == 5
        assert data["totalPages"] == 3
        assert data["page"] == 1


# ============================================================
#  热销推荐: GET /api/product/hot
# ============================================================

class TestProductHot:
    """热销推荐(4)"""

    def test_hot_default(self):
        """默认 limit=6"""
        resp = client.get("/api/product/hot")
        data = resp.json()
        assert data["success"] is True
        assert data["limit"] == 6
        assert data["count"] == 6
        # 销量降序
        sales = [p["sales_monthly"] for p in data["products"]]
        assert sales == sorted(sales, reverse=True)

    def test_hot_custom_limit(self):
        """自定义 limit=3"""
        resp = client.get("/api/product/hot?limit=3")
        data = resp.json()
        assert data["count"] == 3
        assert data["limit"] == 3
        # 第一条是便携(月销 2400)
        assert data["products"][0]["product_id"] == PID_PORTABLE

    def test_hot_first_is_top_seller(self):
        """榜首是便携 ZX42-2026B01(月销 2400)"""
        resp = client.get("/api/product/hot?limit=1")
        assert resp.json()["products"][0]["product_id"] == PID_PORTABLE

    def test_hot_invalid_limit_zero(self):
        """limit=0: 422"""
        resp = client.get("/api/product/hot?limit=0")
        assert resp.status_code == 422


# ============================================================
#  主推产品: GET /api/product/featured
# ============================================================

class TestProductFeatured:
    """主推产品(4)"""

    def test_featured_default(self):
        """默认 limit=4"""
        resp = client.get("/api/product/featured")
        data = resp.json()
        assert data["success"] is True
        assert data["limit"] == 4
        assert data["count"] == 4
        # 全部 featured=True
        for p in data["products"]:
            assert p["featured"] is True

    def test_featured_custom_limit(self):
        """limit=2: 返回 2 款"""
        resp = client.get("/api/product/featured?limit=2")
        data = resp.json()
        assert data["count"] == 2
        assert data["limit"] == 2

    def test_featured_all_featured_products(self):
        """获取所有主推产品(limit 足够大)

        featured=True 的产品有: ZX42-2026L07, ZX53-2026Z01, ZX53-2026N20,
        ZX52-2026L02, ZX52-2026X03 共 5 款
        """
        resp = client.get("/api/product/featured?limit=20")
        data = resp.json()
        assert data["count"] == 5

    def test_featured_sorted_by_hot_rank(self):
        """按 hot_rank 升序: 第一条应是 ZX42-2026L07(hot_rank=1)"""
        resp = client.get("/api/product/featured?limit=4")
        assert resp.json()["products"][0]["product_id"] == PID_CLASSIC_42


# ============================================================
#  产品详情: GET /api/product/{product_id}
# ============================================================

class TestProductDetail:
    """产品详情(6)"""

    def test_detail_success(self):
        """正常详情: 含 description/attributes/related"""
        resp = client.get(f"/api/product/{PID_CLASSIC_42}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        p = data["product"]
        assert p["product_id"] == PID_CLASSIC_42
        assert p["name"] == "竹奕·竹香经典 42° 500ml"
        assert p["brand"] == "竹奕"
        assert p["series"] == "经典系列"
        assert p["alcohol"] == 42
        assert p["volume"] == "500ml"
        assert p["price"] == 268
        assert p["stock"] == 500
        assert "description" in p
        assert "attributes" in p
        assert "images" in p

    def test_detail_attributes_structure(self):
        """attributes 含 aroma/process/origin 等字段"""
        resp = client.get(f"/api/product/{PID_CLASSIC_42}")
        attrs = resp.json()["product"]["attributes"]
        for k in ("aroma", "process", "alcohol", "volume", "origin",
                  "ingredients", "storage", "taste"):
            assert k in attrs
        assert attrs["origin"] == "山东泰安"

    def test_detail_related_products(self):
        """详情含关联推荐 4 条"""
        resp = client.get(f"/api/product/{PID_CLASSIC_42}")
        related = resp.json()["related"]
        assert len(related) == 4
        # 不应包含自身
        for r in related:
            assert r["product_id"] != PID_CLASSIC_42

    def test_detail_related_same_series_first(self):
        """关联推荐同系列优先(经典系列另一款 45°)"""
        resp = client.get(f"/api/product/{PID_CLASSIC_42}")
        related = resp.json()["related"]
        # 第一条应是同系列的 ZX45-2026L05
        assert related[0]["product_id"] == PID_CLASSIC_45

    def test_detail_not_found(self):
        """产品不存在: 404"""
        resp = client.get("/api/product/ZX-NOPE-999")
        assert resp.status_code == 404
        assert "不存在" in resp.json()["error"]

    def test_detail_logs(self):
        """响应 logs 含 详情/关联推荐 两步"""
        resp = client.get(f"/api/product/{PID_CLASSIC_42}")
        steps = [log["step"] for log in resp.json()["logs"]]
        assert "详情" in steps
        assert "关联推荐" in steps


# ============================================================
#  评价列表: GET /api/product/{product_id}/reviews
# ============================================================

class TestProductReviewsList:
    """评价列表(5)"""

    def test_reviews_list_success(self):
        """ZX42-2026L07 有 2 条种子评价

        ratingCount/ratingAvg 取自产品元数据字段(seed 时为 320/4.8,
        代表"真实世界"的总评分数, 与文本评价数不同)。
        提交评价后会通过 _update_rating_stats 重算为评价列表的实际值。
        """
        resp = client.get(f"/api/product/{PID_CLASSIC_42}/reviews")
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["productId"] == PID_CLASSIC_42
        # total/count 反映实际评价列表
        assert data["total"] == 2
        assert data["count"] == 2
        # ratingCount/ratingAvg 反映产品元数据(seed 值)
        assert data["ratingCount"] == 320
        assert data["ratingAvg"] == 4.8

    def test_reviews_list_pagination(self):
        """分页: page=1 page_size=1 → 返回 1 条, totalPages=2"""
        resp = client.get(f"/api/product/{PID_CLASSIC_42}/reviews?page=1&page_size=1")
        data = resp.json()
        assert data["count"] == 1
        assert data["total"] == 2
        assert data["totalPages"] == 2
        assert data["page"] == 1
        assert data["pageSize"] == 1

    def test_reviews_list_sorted_by_time_desc(self):
        """评价按时间倒序: 最新(rv_seed_002 2026-08-16)在前"""
        resp = client.get(f"/api/product/{PID_CLASSIC_42}/reviews")
        reviews = resp.json()["reviews"]
        assert reviews[0]["review_id"] == "rv_seed_002"
        # 时间倒序验证
        times = [r["created_at"] for r in reviews]
        assert times == sorted(times, reverse=True)

    def test_reviews_list_empty_product(self):
        """无评价产品: 返回空列表"""
        resp = client.get(f"/api/product/{PID_PORTABLE}/reviews")
        data = resp.json()
        assert data["total"] == 0
        assert data["count"] == 0
        assert data["reviews"] == []

    def test_reviews_list_product_not_found(self):
        """产品不存在: 404"""
        resp = client.get("/api/product/ZX-NOPE/reviews")
        assert resp.status_code == 404


# ============================================================
#  提交评价: POST /api/product/{product_id}/reviews
# ============================================================

class TestProductReviewCreate:
    """提交评价(8)"""

    def test_create_review_success(self):
        """成功提交评价"""
        resp = client.post(f"/api/product/{PID_CLASSIC_42}/reviews",
                           json={"rating": 5, "content": "非常好的酒!"},
                           headers={"X-Member-Id": "1"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["rating"] == 5
        assert "reviewId" in data
        # 评价已写入 store
        reviews = _mock_store["product_reviews"][PID_CLASSIC_42]
        assert len(reviews) == 3  # 原有 2 + 新增 1

    def test_create_review_updates_rating(self):
        """提交评价后产品评分自动重算

        原 ZX42-2026L07: 2 条评价(5+4=9, avg=4.5)
        新增 5 星后: 3 条(5+4+5=14, avg≈4.67)
        """
        client.post(f"/api/product/{PID_CLASSIC_42}/reviews",
                    json={"rating": 5, "content": "好评"},
                    headers={"X-Member-Id": "1"})
        # 查询列表验证 ratingAvg 已更新
        resp = client.get(f"/api/product/{PID_CLASSIC_42}/reviews")
        data = resp.json()
        assert data["ratingCount"] == 3
        # (5+4+5)/3 ≈ 4.67
        assert abs(data["ratingAvg"] - round(14 / 3, 2)) < 0.01

    def test_create_review_no_auth(self):
        """未登录: 401"""
        resp = client.post(f"/api/product/{PID_CLASSIC_42}/reviews",
                           json={"rating": 5, "content": "好"})
        assert resp.status_code == 401

    def test_create_review_bad_member_id(self):
        """X-Member-Id 非数字: 401"""
        resp = client.post(f"/api/product/{PID_CLASSIC_42}/reviews",
                           json={"rating": 5, "content": "好"},
                           headers={"X-Member-Id": "abc"})
        assert resp.status_code == 401

    def test_create_review_product_not_found(self):
        """产品不存在: 404"""
        resp = client.post("/api/product/ZX-NOPE/reviews",
                           json={"rating": 5, "content": "好"},
                           headers={"X-Member-Id": "1"})
        assert resp.status_code == 404

    def test_create_review_rating_out_of_range(self):
        """评分越界(6): 422(Pydantic 校验)"""
        resp = client.post(f"/api/product/{PID_CLASSIC_42}/reviews",
                           json={"rating": 6, "content": "好"},
                           headers={"X-Member-Id": "1"})
        assert resp.status_code == 422

    def test_create_review_rating_zero(self):
        """评分 0: 422"""
        resp = client.post(f"/api/product/{PID_CLASSIC_42}/reviews",
                           json={"rating": 0, "content": "好"},
                           headers={"X-Member-Id": "1"})
        assert resp.status_code == 422

    def test_create_review_content_missing(self):
        """内容缺失: 422"""
        resp = client.post(f"/api/product/{PID_CLASSIC_42}/reviews",
                           json={"rating": 5},
                           headers={"X-Member-Id": "1"})
        assert resp.status_code == 422


# ============================================================
#  集成场景: 跨端点联动
# ============================================================

class TestProductIntegration:
    """集成场景(3)"""

    def test_review_then_list(self):
        """提交评价后立即查询: 列表可见新评价"""
        resp = client.post(f"/api/product/{PID_TREASURE}/reviews",
                           json={"rating": 4, "content": "不错的珍藏款"},
                           headers={"X-Member-Id": "1"})
        assert resp.status_code == 200
        # ZX53-2026Z01 原有 1 条, 现应有 2 条
        list_resp = client.get(f"/api/product/{PID_TREASURE}/reviews")
        data = list_resp.json()
        assert data["total"] == 2
        assert data["count"] == 2
        # 顶部应是新评价
        assert data["reviews"][0]["content"] == "不错的珍藏款"

    def test_filter_then_detail(self):
        """筛选 → 查看详情: 列表中的产品详情可读"""
        list_resp = client.get("/api/product/list?series=便携系列")
        pid = list_resp.json()["products"][0]["product_id"]
        detail_resp = client.get(f"/api/product/{pid}")
        assert detail_resp.status_code == 200
        assert detail_resp.json()["product"]["product_id"] == pid

    def test_hot_and_featured_distinct(self):
        """热销与主推返回不同视角的产品集合"""
        hot = client.get("/api/product/hot?limit=11").json()["products"]
        featured = client.get("/api/product/featured?limit=20").json()["products"]
        hot_ids = {p["product_id"] for p in hot}
        featured_ids = {p["product_id"] for p in featured}
        # 主推 5 款均为热销 11 款的子集
        assert featured_ids.issubset(hot_ids)
        # 至少有 1 款非主推的热销产品
        assert len(hot_ids - featured_ids) >= 6
