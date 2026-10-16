# -*- coding: utf-8 -*-
"""站内内容社区 MVP 单测(community_service + 路由冒烟)

蓝图对照分析近期项③: sv73 六平台发布内容站内化 feed + 商品挂载 + UGC 评测容器。

覆盖:
    T1 feed 空库返回空列表(fail-soft 不报错)
    T2 feed 分页/平台筛选/倒序(publishedAt 新→旧, 非终态不透出)
    T3 挂载后 feed join 商品快照/商品缺失 best-effort(product=None 不崩)
    T4 bind_product 幂等 upsert + content/product 缺失 best-effort warning
    T5 create_post 落 pending + 参数校验(类型域外/长度/评分越界/身份缺失)
    T6 C 端 list_posts 只显 approved(pending+approved+rejected 三帖验证)
    T7 review 流转(pending→approved/rejected) + 重复审拒绝 + 帖不存在 404 语义
    T8 路由冒烟(httpx ASGITransport: feed/posts 200, 发帖 401/200,
       admin 头鉴权形态对齐 test_order_routes)

测试形态: 服务层 asyncio.run + 内存 store 直种(对齐 test_live_qa_service /
test_growth80_pricing); 路由层 httpx.AsyncClient + ASGITransport
(对齐 test_order_routes, conftest 已设 AUTH_COMPAT_TRUST_HEADERS=1)。

运行: pytest test_community_service.py
"""
import asyncio

import httpx
import pytest

from repositories.store import _mock_store, reset_store
from repositories.community_repository import CommunityRepository
from repositories.promo_repository import (
    PromoRepository,
    CONTENT_STATUS_PUBLISHED,
)
from services.community_service import CommunityService

MEMBER_HEADERS = {"X-Member-Id": "1"}
ADMIN_HEADERS = {"X-Role": "admin"}

# 初始 12 款商品之一(test_order_routes 同款锚点: ¥268)
REAL_PRODUCT_ID = "ZX42-2026L07"


def _seed_content(content_id: int, platform: str, published_at: str,
                  status: str = CONTENT_STATUS_PUBLISHED, **extra) -> dict:
    """直接种 36号 content store(绕开 service, 构造确定性数据)"""
    promo_repo = PromoRepository()
    promo_repo._ensure_store()
    content = {
        "contentId": content_id, "contentGroupId": 0, "hotspotId": 0,
        "platform": platform, "title": f"内容-{content_id}",
        "body": f"正文-{content_id} " + "竹香酒以竹笋竹叶古法酿制。" * 30,
        "hashtags": "#竹香酒 #国潮", "cta": "", "coverHint": "大字卡",
        "status": status, "shortCode": f"r{content_id:04d}",
        "scheduledAt": "", "publishedAt": published_at,
        "createdAt": published_at,
    }
    content.update(extra)
    _mock_store["promo_contents"][content_id] = content
    return content


def _svc() -> CommunityService:
    """新服务实例(共享内存 _mock_store 单例)"""
    return CommunityService(
        repo=CommunityRepository(), promo_repo=PromoRepository())


@pytest.fixture(autouse=True)
def _isolated():
    """每用例重置内存 store(隔离社区/内容/商品种子)"""
    reset_store()
    CommunityRepository()._ensure_store()
    yield


# ---------- T1 feed 空库 ----------

def test_t1_feed_empty_store():
    """空库: 返回空 feed 结构不报错(items/total/hasMore 口径齐全)"""

    async def run():
        r = await _svc().feed()
        assert r["items"] == []
        assert r["total"] == 0 and r["hasMore"] is False
        assert r["page"] == 1 and r["pageSize"] == 10

    asyncio.run(run())


# ---------- T2 feed 分页/筛选/倒序 ----------

def test_t2_feed_paging_platform_filter_and_order():
    """倒序(publishedAt 新→旧) / 平台筛选 / 分页 hasMore / 非终态不透出"""

    async def run():
        _seed_content(101, "douyin", "2026-10-01T10:00:00+00:00")
        _seed_content(102, "xiaohongshu", "2026-10-03T10:00:00+00:00")
        _seed_content(103, "douyin", "2026-10-02T10:00:00+00:00")
        _seed_content(104, "weibo", "2026-10-04T10:00:00+00:00",
                      status="pending")       # 非终态: 不透出
        _seed_content(105, "weibo", "2026-10-05T10:00:00+00:00",
                      status="approved")     # 非终态: 不透出
        svc = _svc()

        # 全量倒序: 102(03) → 103(02) → 101(01), pending/approved 不见
        r = await svc.feed()
        assert [it["contentId"] for it in r["items"]] == [102, 103, 101]
        assert r["total"] == 3

        # 平台筛选: 只显 douyin, 倒序 103 → 101
        r_dy = await svc.feed(platform="douyin")
        assert [it["contentId"] for it in r_dy["items"]] == [103, 101]
        assert r_dy["total"] == 2

        # 分页: page_size=2 → 第 1 页 [102, 103] hasMore=True, 第 2 页 [101]
        r_p1 = await svc.feed(page=1, page_size=2)
        assert [it["contentId"] for it in r_p1["items"]] == [102, 103]
        assert r_p1["hasMore"] is True
        r_p2 = await svc.feed(page=2, page_size=2)
        assert [it["contentId"] for it in r_p2["items"]] == [101]
        assert r_p2["hasMore"] is False

        # 透出字段口径: 摘要截断 + shortCode 直通
        first = r["items"][0]
        assert first["title"] == "内容-102"
        assert len(first["bodySummary"]) == 120   # 摘要 120 字截断
        assert first["shortCode"] == "r0102"
        assert first["product"] is None           # 未挂载 → None

    asyncio.run(run())


# ---------- T3 挂载 join 商品快照 / 商品缺失 best-effort ----------

def test_t3_feed_join_product_snapshot_and_missing():
    """挂载真实商品→快照(name/price/image); 挂不存在商品/未挂载→None 不崩"""

    async def run():
        _seed_content(201, "douyin", "2026-10-01T10:00:00+00:00")
        _seed_content(202, "xiaohongshu", "2026-10-02T10:00:00+00:00")
        _seed_content(203, "weibo", "2026-10-03T10:00:00+00:00")
        svc = _svc()
        await svc.bind_product(201, REAL_PRODUCT_ID)
        await svc.bind_product(202, "NO-SUCH-PRODUCT")   # 商品缺失

        r = await svc.feed()
        by_id = {it["contentId"]: it for it in r["items"]}
        # 真实商品: 快照齐全
        p = by_id[201]["product"]
        assert p["productId"] == REAL_PRODUCT_ID
        assert "竹奕" in p["name"]
        assert p["price"] == 268
        assert p["image"]                      # 初始商品有主图
        # 商品缺失: best-effort 回落 None 不崩
        assert by_id[202]["product"] is None
        # 未挂载: None
        assert by_id[203]["product"] is None

    asyncio.run(run())


# ---------- T4 bind_product 幂等 ----------

def test_t4_bind_product_idempotent():
    """同 contentId 重复挂载幂等: 单条记录 + 覆盖 productId 保留原 id;
    content/product 缺失 best-effort warning 不硬拒"""

    async def run():
        _seed_content(301, "douyin", "2026-10-01T10:00:00+00:00")
        svc = _svc()

        r1 = await svc.bind_product(301, REAL_PRODUCT_ID)
        assert r1["ok"] is True and r1["warnings"] == []
        binding = r1["binding"]
        assert binding["contentId"] == 301
        assert binding["productId"] == REAL_PRODUCT_ID

        # 幂等 upsert: 记录数不增, id 复用
        r2 = await svc.bind_product(301, "ZX42-2026L05")
        assert r2["binding"]["id"] == binding["id"]
        bindings = [b for b in _mock_store["content_products"].values()
                    if b["contentId"] == 301]
        assert len(bindings) == 1
        assert bindings[0]["productId"] == "ZX42-2026L05"

        # content 不存在: warning 不硬拒(挂载可先于产物补齐)
        r3 = await svc.bind_product(9999, REAL_PRODUCT_ID)
        assert r3["ok"] is True
        assert any("9999" in w for w in r3["warnings"])
        # product 不存在: warning 不硬拒
        r4 = await svc.bind_product(301, "GHOST-P")
        assert r4["ok"] is True
        assert any("GHOST-P" in w for w in r4["warnings"])

    asyncio.run(run())


# ---------- T5 create_post 落 pending + 参数校验 ----------

def test_t5_create_post_pending_and_validation():
    """正常发帖落 pending(永不直接公开) + 全量参数校验 ValueError"""

    async def run():
        svc = _svc()
        post = await svc.create_post(
            member_id=1, nickname="竹友小张", target_type="product",
            target_id=REAL_PRODUCT_ID, text="  回甘绵柔, 家宴好评!  ",
            rating=5)
        assert post["status"] == "pending"          # 先审后显铁律
        assert post["text"] == "回甘绵柔, 家宴好评!"  # strip 落库
        assert post["memberId"] == 1 and post["rating"] == 5
        assert post["postId"] == 1

        # target_type 域外
        with pytest.raises(ValueError):
            await svc.create_post(1, "n", "video", "x", "文本")
        # 正文空 / 超长
        with pytest.raises(ValueError):
            await svc.create_post(1, "n", "product", "p", "   ")
        with pytest.raises(ValueError):
            await svc.create_post(1, "n", "product", "p", "酒" * 1001)
        # 边界内 1000 字通过
        ok = await svc.create_post(1, "n", "product", "p", "酒" * 1000)
        assert ok["status"] == "pending"
        # rating 越界 / 类型非法(字符串/布尔/0/6)
        for bad in (0, 6, "5", True, 2.5):
            with pytest.raises(ValueError):
                await svc.create_post(1, "n", "product", "p", "文本",
                                      rating=bad)
        # 身份缺失 / 对象缺失
        with pytest.raises(ValueError):
            await svc.create_post(None, "n", "product", "p", "文本")
        with pytest.raises(ValueError):
            await svc.create_post(1, "n", "product", "", "文本")

    asyncio.run(run())


# ---------- T6 C 端 list_posts 只显 approved ----------

def test_t6_list_posts_only_approved():
    """pending/approved/rejected 三帖并存: C 端只显 approved; 目标过滤生效"""

    async def run():
        svc = _svc()
        p_pending = await svc.create_post(
            1, "甲", "product", REAL_PRODUCT_ID, "待审帖")
        p_approved = await svc.create_post(
            2, "乙", "content", "401", "已过审帖", rating=4)
        p_rejected = await svc.create_post(
            3, "丙", "product", REAL_PRODUCT_ID, "被拒帖")
        await svc.review_post(p_approved["postId"], True, operator="审A")
        await svc.review_post(p_rejected["postId"], False, operator="审A")

        r = await svc.list_posts()
        assert r["total"] == 1
        assert [it["postId"] for it in r["items"]] == [p_approved["postId"]]
        assert r["items"][0]["status"] == "approved"

        # 目标过滤: content 域只见 approved 的那条
        r_content = await svc.list_posts(target_type="content",
                                         target_id="401")
        assert r_content["total"] == 1
        # product 域的 approved 为零(pending/rejected 均不可见)
        r_product = await svc.list_posts(target_type="product",
                                         target_id=REAL_PRODUCT_ID)
        assert r_product["total"] == 0 and r_product["items"] == []

    asyncio.run(run())


# ---------- T7 review 流转 + 重复审拒绝 ----------

def test_t7_review_flow_and_reject_repeated():
    """approve/reject 流转留痕(reviewedAt/operator); 非 pending 重复审 ValueError;
    帖子不存在 KeyError(→路由层 404)"""

    async def run():
        svc = _svc()
        post_a = await svc.create_post(1, "甲", "product",
                                       REAL_PRODUCT_ID, "帖A", rating=5)
        post_b = await svc.create_post(2, "乙", "content", "501", "帖B")

        # approve 流转
        approved = await svc.review_post(post_a["postId"], True,
                                         operator="审A")
        assert approved["status"] == "approved"
        assert approved["reviewedAt"] and approved["operator"] == "审A"
        # 重复审同一帖 → 拒绝(幂等保护)
        with pytest.raises(ValueError):
            await svc.review_post(post_a["postId"], False, operator="审B")
        assert svc.repo and (await svc.repo.get_post(
            post_a["postId"]))["status"] == "approved"   # 状态未被覆写

        # reject 流转
        rejected = await svc.review_post(post_b["postId"], False,
                                         operator="审B")
        assert rejected["status"] == "rejected"
        with pytest.raises(ValueError):
            await svc.review_post(post_b["postId"], True, operator="审B")

        # 不存在的帖 → KeyError(路由层映射 404)
        with pytest.raises(KeyError):
            await svc.review_post(99999, True, operator="审A")

        # pending_posts 面板: 两帖均已审 → 空
        assert await svc.pending_posts() == []

    asyncio.run(run())


# ---------- T8 路由冒烟(httpx ASGITransport) ----------

async def test_t8_routes_smoke():
    """feed/posts 公开 200; 发帖 401→200 落 pending; admin 头鉴权 403→200"""
    from main import app
    _seed_content(601, "douyin", "2026-10-01T10:00:00+00:00")

    async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://test") as client:
        # 公开 feed
        resp = await client.get("/api/community/feed")
        assert resp.status_code == 200
        body = resp.json()
        assert body["code"] == 0
        assert [it["contentId"] for it in body["data"]["items"]] == [601]

        # 公开帖子列表(空库 approved 为零)
        resp = await client.get("/api/community/posts")
        assert resp.status_code == 200
        assert resp.json()["data"]["total"] == 0

        # 发帖: 无登录头 401(对齐 order_routes X-Member-Id 惯例)
        resp = await client.post("/api/community/posts", json={
            "targetType": "content", "targetId": "601",
            "text": "路由冒烟帖", "rating": 5})
        assert resp.status_code == 401
        # 带登录头 200 → 落 pending
        resp = await client.post("/api/community/posts", json={
            "targetType": "content", "targetId": "601",
            "text": "路由冒烟帖", "rating": 5}, headers=MEMBER_HEADERS)
        assert resp.status_code == 200
        post_id = resp.json()["data"]["postId"]
        assert resp.json()["data"]["status"] == "pending"

        # admin: 待审列表无头 403 → admin 头 200 可见该帖
        resp = await client.get("/api/community/posts/pending")
        assert resp.status_code == 403
        resp = await client.get("/api/community/posts/pending",
                                headers=ADMIN_HEADERS)
        assert resp.status_code == 200
        assert post_id in [p["postId"] for p in resp.json()["data"]]

        # admin: 审核流转(approve) → C 端可见
        resp = await client.post("/api/community/posts/review", json={
            "postId": post_id, "approve": True},
            headers={**ADMIN_HEADERS, "X-Admin-Id": "9"})
        assert resp.status_code == 200
        assert resp.json()["data"]["status"] == "approved"
        resp = await client.get("/api/community/posts",
                                params={"target_type": "content",
                                        "target_id": "601"})
        assert resp.json()["data"]["total"] == 1
        # 重复审 → 409
        resp = await client.post("/api/community/posts/review", json={
            "postId": post_id, "approve": False}, headers=ADMIN_HEADERS)
        assert resp.status_code == 409

        # admin: 挂载商品(幂等 upsert) → feed join 快照
        resp = await client.post("/api/community/bind-product", json={
            "contentId": 601, "productId": REAL_PRODUCT_ID},
            headers=ADMIN_HEADERS)
        assert resp.status_code == 200
        resp = await client.post("/api/community/bind-product", json={
            "contentId": 601, "productId": REAL_PRODUCT_ID},
            headers=ADMIN_HEADERS)
        assert resp.status_code == 200
        resp = await client.get("/api/community/feed")
        item = resp.json()["data"]["items"][0]
        assert item["product"]["productId"] == REAL_PRODUCT_ID
        # 挂载无 admin 头 → 403
        resp = await client.post("/api/community/bind-product", json={
            "contentId": 601, "productId": REAL_PRODUCT_ID})
        assert resp.status_code == 403
