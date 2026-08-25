"""评价管理模块扩展功能端到端测试

测试方式: 直接调用 Service 层(asyncio 内存模式), 覆盖 12 个新增评价管理接口
对应的全部业务方法, 与 test_payment_p1_routes.py 风格一致。

覆盖场景:
    - 评价扩展(P0): 详情/修改/删除/按订单查询/会员历史
    - 评价回复(P0): 提交回复/回复列表/角色校验/内容校验
    - 评价点赞(P1): 点赞/取消点赞/重复点赞/未点赞取消
    - 评价举报(P1): 举报/重复举报/原因非法/举报列表/处理举报/隐藏评价
"""

import asyncio
import os
import sys
import unittest

# 确保使用内存模式
os.environ["LOCK_MODE"] = "asyncio"
os.environ["STORE_MODE"] = "asyncio"

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from services.product_service import ProductService
from repositories.product_repository import (
    ProductRepository,
    REVIEW_STATUS_PUBLISHED, REVIEW_STATUS_HIDDEN,
    REPORT_STATUS_PENDING, REPORT_STATUS_CONFIRMED, REPORT_STATUS_REJECTED,
    REPORT_REASON_AD, REPORT_REASON_ABUSE, REPORT_REASON_OTHER,
)
from repositories.store import _mock_store

# 测试用产品 ID(使用初始数据中的产品)
PID_CLASSIC_42 = "ZX42-2026L07"


def _reset_store():
    """清空内存存储, 重新 seed 产品数据"""
    for k in list(_mock_store.keys()):
        _mock_store.pop(k, None)
    # 重新 seed 产品和评价数据
    repo = ProductRepository()
    repo._ensure_store()
    # 注入初始产品
    from repositories.product_repository import _initial_products, _initial_reviews
    _mock_store["products"] = {p["product_id"]: p for p in _initial_products()}
    _mock_store["product_reviews"] = _initial_reviews()
    # 初始化 inventory 键(get_by_id 会访问)
    if "inventory" not in _mock_store:
        _mock_store["inventory"] = {}


async def _create_review(svc, member_id=100, rating=5, content="好酒!",
                         product_id=PID_CLASSIC_42, order_id=""):
    """创建一条测试评价, 返回 review dict"""
    result = await svc.add_review(
        product_id=product_id,
        member_id=member_id,
        member_nickname=f"测试会员{member_id}",
        rating=rating,
        content=content,
    )
    # 补充 order_id(通过 repository 直接写入)
    if order_id:
        review_id = result["reviewId"]
        await svc.product_repo.update_review(
            product_id, review_id, {"order_id": order_id})
    return result


class TestReviewDetail(unittest.IsolatedAsyncioTestCase):
    """评价详情"""

    async def asyncSetUp(self):
        _reset_store()
        self.svc = ProductService()

    async def test_01_detail_success(self):
        """查询评价详情(含回复列表)"""
        created = await _create_review(self.svc)
        review_id = created["reviewId"]
        result = await self.svc.get_review_detail(PID_CLASSIC_42, review_id)
        self.assertTrue(result["success"])
        self.assertEqual(result["review"]["review_id"], review_id)
        self.assertEqual(result["review"]["replies"], [])

    async def test_02_detail_product_not_found(self):
        """产品不存在 → KeyError"""
        with self.assertRaises(KeyError):
            await self.svc.get_review_detail("NOT_EXIST", "rv_xxx")

    async def test_03_detail_review_not_found(self):
        """评价不存在 → KeyError"""
        with self.assertRaises(KeyError):
            await self.svc.get_review_detail(PID_CLASSIC_42, "rv_not_exist")

    async def test_04_detail_with_replies(self):
        """评价详情含回复列表"""
        created = await _create_review(self.svc)
        review_id = created["reviewId"]
        await self.svc.add_reply(
            PID_CLASSIC_42, review_id, "admin01", "admin",
            "客服", "感谢评价!")
        result = await self.svc.get_review_detail(PID_CLASSIC_42, review_id)
        self.assertEqual(len(result["review"]["replies"]), 1)


class TestReviewUpdate(unittest.IsolatedAsyncioTestCase):
    """修改评价"""

    async def asyncSetUp(self):
        _reset_store()
        self.svc = ProductService()

    async def test_05_update_by_owner(self):
        """本人修改评价"""
        created = await _create_review(self.svc, member_id=100)
        review_id = created["reviewId"]
        result = await self.svc.update_review(
            PID_CLASSIC_42, review_id, member_id=100,
            rating=4, content="修改后的内容")
        self.assertTrue(result["success"])
        self.assertEqual(result["review"]["rating"], 4)
        self.assertEqual(result["review"]["content"], "修改后的内容")
        self.assertTrue(result["review"]["updated_at"])

    async def test_06_update_by_admin(self):
        """管理员修改他人评价"""
        created = await _create_review(self.svc, member_id=100)
        review_id = created["reviewId"]
        result = await self.svc.update_review(
            PID_CLASSIC_42, review_id, member_id=999,
            content="管理员修改", is_admin=True)
        self.assertTrue(result["success"])
        self.assertEqual(result["review"]["content"], "管理员修改")

    async def test_07_update_by_other(self):
        """非本人非管理员 → ValueError"""
        created = await _create_review(self.svc, member_id=100)
        review_id = created["reviewId"]
        with self.assertRaises(ValueError):
            await self.svc.update_review(
                PID_CLASSIC_42, review_id, member_id=200,
                content="恶意修改")

    async def test_08_update_rating_out_of_range(self):
        """评分越界 → ValueError"""
        created = await _create_review(self.svc, member_id=100)
        review_id = created["reviewId"]
        with self.assertRaises(ValueError):
            await self.svc.update_review(
                PID_CLASSIC_42, review_id, member_id=100, rating=6)

    async def test_09_update_images(self):
        """修改评价图片"""
        created = await _create_review(self.svc, member_id=100)
        review_id = created["reviewId"]
        images = ["http://img1.jpg", "http://img2.jpg"]
        result = await self.svc.update_review(
            PID_CLASSIC_42, review_id, member_id=100, images=images)
        self.assertEqual(result["review"]["images"], images)

    async def test_10_update_too_many_images(self):
        """图片超过 9 张 → ValueError"""
        created = await _create_review(self.svc, member_id=100)
        review_id = created["reviewId"]
        with self.assertRaises(ValueError):
            await self.svc.update_review(
                PID_CLASSIC_42, review_id, member_id=100,
                images=["x"] * 10)


class TestReviewDelete(unittest.IsolatedAsyncioTestCase):
    """删除评价"""

    async def asyncSetUp(self):
        _reset_store()
        self.svc = ProductService()

    async def test_11_delete_by_owner(self):
        """本人删除评价"""
        created = await _create_review(self.svc, member_id=100)
        review_id = created["reviewId"]
        result = await self.svc.delete_review(
            PID_CLASSIC_42, review_id, member_id=100)
        self.assertTrue(result["success"])
        # 确认已删除
        review = await self.svc.product_repo.get_review(PID_CLASSIC_42, review_id)
        self.assertIsNone(review)

    async def test_12_delete_by_admin(self):
        """管理员删除他人评价"""
        created = await _create_review(self.svc, member_id=100)
        review_id = created["reviewId"]
        result = await self.svc.delete_review(
            PID_CLASSIC_42, review_id, member_id=999, is_admin=True)
        self.assertTrue(result["success"])

    async def test_13_delete_by_other(self):
        """非本人非管理员 → ValueError"""
        created = await _create_review(self.svc, member_id=100)
        review_id = created["reviewId"]
        with self.assertRaises(ValueError):
            await self.svc.delete_review(
                PID_CLASSIC_42, review_id, member_id=200)


class TestReviewByOrder(unittest.IsolatedAsyncioTestCase):
    """按订单查询评价"""

    async def asyncSetUp(self):
        _reset_store()
        self.svc = ProductService()

    async def test_14_by_order_found(self):
        """按订单号查到评价"""
        created = await _create_review(self.svc, order_id="ORD001")
        review_id = created["reviewId"]
        result = await self.svc.get_review_by_order("ORD001")
        self.assertTrue(result["success"])
        self.assertEqual(result["review"]["review_id"], review_id)
        self.assertEqual(result["review"]["order_id"], "ORD001")

    async def test_15_by_order_not_found(self):
        """订单未评价 → KeyError"""
        with self.assertRaises(KeyError):
            await self.svc.get_review_by_order("ORD_NOT_EXIST")

    async def test_16_by_order_with_product_id(self):
        """按订单号+产品ID 查询"""
        await _create_review(self.svc, order_id="ORD002")
        result = await self.svc.get_review_by_order(
            "ORD002", product_id=PID_CLASSIC_42)
        self.assertTrue(result["success"])


class TestMyReviews(unittest.IsolatedAsyncioTestCase):
    """会员评价历史"""

    async def asyncSetUp(self):
        _reset_store()
        self.svc = ProductService()

    async def test_17_my_reviews(self):
        """查询会员评价历史"""
        await _create_review(self.svc, member_id=100)
        await _create_review(self.svc, member_id=100, rating=4,
                             content="第二条评价")
        result = await self.svc.list_my_reviews(member_id=100)
        self.assertTrue(result["success"])
        self.assertEqual(result["total"], 2)

    async def test_18_my_reviews_empty(self):
        """无评价历史"""
        result = await self.svc.list_my_reviews(member_id=999)
        self.assertEqual(result["total"], 0)


class TestReviewReply(unittest.IsolatedAsyncioTestCase):
    """评价回复"""

    async def asyncSetUp(self):
        _reset_store()
        self.svc = ProductService()
        self.created = asyncio.ensure_future(_create_review(self.svc))
        self.review_id = (await self.created)["reviewId"]

    async def test_19_reply_success(self):
        """提交回复成功"""
        result = await self.svc.add_reply(
            PID_CLASSIC_42, self.review_id, "admin01", "admin",
            "客服", "感谢您的评价!")
        self.assertTrue(result["success"])
        self.assertEqual(result["reply"]["content"], "感谢您的评价!")
        self.assertEqual(result["reply"]["replier_role"], "admin")

    async def test_20_reply_empty_content(self):
        """回复内容为空 → ValueError"""
        with self.assertRaises(ValueError):
            await self.svc.add_reply(
                PID_CLASSIC_42, self.review_id, "admin01", "admin",
                "客服", "   ")

    async def test_21_reply_invalid_role(self):
        """回复角色非法 → ValueError"""
        with self.assertRaises(ValueError):
            await self.svc.add_reply(
                PID_CLASSIC_42, self.review_id, "admin01", "user",
                "客服", "内容")

    async def test_22_reply_review_not_found(self):
        """评价不存在 → KeyError"""
        with self.assertRaises(KeyError):
            await self.svc.add_reply(
                PID_CLASSIC_42, "rv_not_exist", "admin01", "admin",
                "客服", "内容")

    async def test_23_reply_list(self):
        """回复列表(多条)"""
        await self.svc.add_reply(
            PID_CLASSIC_42, self.review_id, "admin01", "admin",
            "客服1", "第一条回复")
        await self.svc.add_reply(
            PID_CLASSIC_42, self.review_id, "admin02", "admin",
            "客服2", "第二条回复")
        result = await self.svc.list_replies(PID_CLASSIC_42, self.review_id)
        self.assertEqual(result["total"], 2)

    async def test_24_reply_count_increment(self):
        """回复后 reply_count 递增"""
        await self.svc.add_reply(
            PID_CLASSIC_42, self.review_id, "admin01", "admin",
            "客服", "回复1")
        review = await self.svc.product_repo.get_review(
            PID_CLASSIC_42, self.review_id)
        self.assertEqual(review.get("reply_count", 0), 1)


class TestReviewLike(unittest.IsolatedAsyncioTestCase):
    """评价点赞"""

    async def asyncSetUp(self):
        _reset_store()
        self.svc = ProductService()
        created = await _create_review(self.svc)
        self.review_id = created["reviewId"]

    async def test_25_like_success(self):
        """点赞成功"""
        result = await self.svc.like_review(self.review_id, "user100")
        self.assertTrue(result["success"])
        self.assertTrue(result["liked"])
        self.assertEqual(result["likeCount"], 1)

    async def test_26_like_duplicate(self):
        """重复点赞 → ValueError"""
        await self.svc.like_review(self.review_id, "user100")
        with self.assertRaises(ValueError):
            await self.svc.like_review(self.review_id, "user100")

    async def test_27_unlike_success(self):
        """取消点赞成功"""
        await self.svc.like_review(self.review_id, "user100")
        result = await self.svc.unlike_review(self.review_id, "user100")
        self.assertTrue(result["success"])
        self.assertFalse(result["liked"])
        self.assertEqual(result["likeCount"], 0)

    async def test_28_unlike_not_liked(self):
        """未点赞时取消 → ValueError"""
        with self.assertRaises(ValueError):
            await self.svc.unlike_review(self.review_id, "user100")

    async def test_29_like_count_after_multiple(self):
        """多人点赞后计数"""
        await self.svc.like_review(self.review_id, "user1")
        await self.svc.like_review(self.review_id, "user2")
        await self.svc.like_review(self.review_id, "user3")
        result = await self.svc.like_review(self.review_id, "user4")
        self.assertEqual(result["likeCount"], 4)


class TestReviewReport(unittest.IsolatedAsyncioTestCase):
    """评价举报"""

    async def asyncSetUp(self):
        _reset_store()
        self.svc = ProductService()
        created = await _create_review(self.svc)
        self.review_id = created["reviewId"]

    async def test_30_report_success(self):
        """举报成功"""
        result = await self.svc.report_review(
            self.review_id, "user100", REPORT_REASON_AD, "广告内容")
        self.assertTrue(result["success"])
        self.assertEqual(result["report"]["reason"], REPORT_REASON_AD)
        self.assertEqual(result["report"]["status"], REPORT_STATUS_PENDING)

    async def test_31_report_duplicate(self):
        """重复举报 → ValueError"""
        await self.svc.report_review(
            self.review_id, "user100", REPORT_REASON_AD)
        with self.assertRaises(ValueError):
            await self.svc.report_review(
                self.review_id, "user100", REPORT_REASON_OTHER)

    async def test_32_report_invalid_reason(self):
        """举报原因非法 → ValueError"""
        with self.assertRaises(ValueError):
            await self.svc.report_review(
                self.review_id, "user100", "invalid_reason")

    async def test_33_report_different_users(self):
        """不同用户可分别举报同一评价"""
        await self.svc.report_review(
            self.review_id, "user1", REPORT_REASON_AD)
        result = await self.svc.report_review(
            self.review_id, "user2", REPORT_REASON_ABUSE)
        self.assertTrue(result["success"])


class TestReportList(unittest.IsolatedAsyncioTestCase):
    """举报列表"""

    async def asyncSetUp(self):
        _reset_store()
        self.svc = ProductService()
        created = await _create_review(self.svc)
        self.review_id = created["reviewId"]
        # 创建多条举报
        self.report1 = await self.svc.report_review(
            self.review_id, "user1", REPORT_REASON_AD)
        self.report2 = await self.svc.report_review(
            self.review_id, "user2", REPORT_REASON_ABUSE)

    async def test_34_list_all_reports(self):
        """查询所有举报"""
        result = await self.svc.list_reports()
        self.assertTrue(result["success"])
        self.assertEqual(result["total"], 2)

    async def test_35_list_by_status(self):
        """按状态筛选举报"""
        result = await self.svc.list_reports(status=REPORT_STATUS_PENDING)
        self.assertEqual(result["total"], 2)

    async def test_36_list_invalid_status(self):
        """状态非法 → ValueError"""
        with self.assertRaises(ValueError):
            await self.svc.list_reports(status="invalid")


class TestReportHandle(unittest.IsolatedAsyncioTestCase):
    """处理举报"""

    async def asyncSetUp(self):
        _reset_store()
        self.svc = ProductService()
        created = await _create_review(self.svc)
        self.review_id = created["reviewId"]
        self.report = await self.svc.report_review(
            self.review_id, "user1", REPORT_REASON_AD)

    async def test_37_handle_confirm(self):
        """举报成立 → 评价被隐藏"""
        report_id = self.report["report"]["report_id"]
        result = await self.svc.handle_report(
            report_id, "admin01", "confirmed", "广告内容属实")
        self.assertTrue(result["success"])
        self.assertEqual(result["report"]["status"], REPORT_STATUS_CONFIRMED)
        # 验证评价被隐藏
        review = await self.svc.product_repo.get_review(
            PID_CLASSIC_42, self.review_id)
        self.assertEqual(review["status"], REVIEW_STATUS_HIDDEN)

    async def test_38_handle_reject(self):
        """举报驳回"""
        report_id = self.report["report"]["report_id"]
        result = await self.svc.handle_report(
            report_id, "admin01", "rejected", "内容正常")
        self.assertTrue(result["success"])
        self.assertEqual(result["report"]["status"], REPORT_STATUS_REJECTED)
        # 评价未被隐藏
        review = await self.svc.product_repo.get_review(
            PID_CLASSIC_42, self.review_id)
        self.assertEqual(review["status"], REVIEW_STATUS_PUBLISHED)

    async def test_39_handle_already_processed(self):
        """重复处理 → ValueError"""
        report_id = self.report["report"]["report_id"]
        await self.svc.handle_report(report_id, "admin01", "confirmed")
        with self.assertRaises(ValueError):
            await self.svc.handle_report(report_id, "admin01", "rejected")

    async def test_40_handle_not_found(self):
        """举报不存在 → KeyError"""
        with self.assertRaises(KeyError):
            await self.svc.handle_report(
                "rpt_not_exist", "admin01", "confirmed")

    async def test_41_handle_invalid_action(self):
        """处理动作非法 → ValueError"""
        report_id = self.report["report"]["report_id"]
        with self.assertRaises(ValueError):
            await self.svc.handle_report(
                report_id, "admin01", "invalid_action")


class TestReviewHide(unittest.IsolatedAsyncioTestCase):
    """隐藏/恢复评价"""

    async def asyncSetUp(self):
        _reset_store()
        self.svc = ProductService()
        created = await _create_review(self.svc)
        self.review_id = created["reviewId"]

    async def test_42_hide_success(self):
        """隐藏评价"""
        result = await self.svc.hide_review(
            PID_CLASSIC_42, self.review_id, is_hide=True)
        self.assertTrue(result["success"])
        self.assertEqual(result["status"], REVIEW_STATUS_HIDDEN)
        review = await self.svc.product_repo.get_review(
            PID_CLASSIC_42, self.review_id)
        self.assertEqual(review["status"], REVIEW_STATUS_HIDDEN)

    async def test_43_restore_success(self):
        """恢复评价"""
        await self.svc.hide_review(
            PID_CLASSIC_42, self.review_id, is_hide=True)
        result = await self.svc.hide_review(
            PID_CLASSIC_42, self.review_id, is_hide=False)
        self.assertEqual(result["status"], REVIEW_STATUS_PUBLISHED)

    async def test_44_hide_product_not_found(self):
        """产品不存在 → KeyError"""
        with self.assertRaises(KeyError):
            await self.svc.hide_review(
                "NOT_EXIST", self.review_id, is_hide=True)

    async def test_45_hide_review_not_found(self):
        """评价不存在 → KeyError"""
        with self.assertRaises(KeyError):
            await self.svc.hide_review(
                PID_CLASSIC_42, "rv_not_exist", is_hide=True)


# ============================================================
# 测试运行
# ============================================================

test_classes = [
    TestReviewDetail, TestReviewUpdate, TestReviewDelete,
    TestReviewByOrder, TestMyReviews, TestReviewReply,
    TestReviewLike, TestReviewReport, TestReportList,
    TestReportHandle, TestReviewHide,
]


def load_tests(loader, tests, pattern):
    suite = unittest.TestSuite()
    for cls in test_classes:
        suite.addTests(loader.loadTestsFromTestCase(cls))
    return suite


if __name__ == "__main__":
    runner = unittest.TextTestRunner(verbosity=2)
    suite = load_tests(unittest.TestLoader(), None, None)
    result = runner.run(suite)
    sys.exit(0 if result.wasSuccessful() else 1)
