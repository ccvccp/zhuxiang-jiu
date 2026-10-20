"""竹盾特性测试基线（78号 T2.2, 2026-10-21）

锁定三条行为资产现行为(红线标记.md B 表), 任何触碰相关文件的功能变更都应先跑本基线:
- B1 运费规则: 折后金额满 ¥99 免运费, 未满收 ¥10 (services/checkout_service.py)
- B2 评价双写: 订单评价须同步商品评价体系 (services/product_service.add_review)
- B3 注册面: birthdate 硬校验成年(18) + 声明确认 (core/age_gate.py)

对应五步 SOP 门 3(特性测试锁行为)——T2.3 模块升级试点前的回归安全网。
运行: LOCK_MODE=asyncio python -m pytest backend/test_feature_baseline.py -q
"""
import asyncio
import os
import sys
from datetime import date

os.environ.setdefault("LOCK_MODE", "asyncio")  # 锁走纯 asyncio, 单测零 Redis 依赖
BACKEND = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "backend"))
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)

from core.age_gate import (
    calc_age, is_adult, parse_birthdate,
)
from services.checkout_service import calculate_order_price


# ============================================================
# B1 运费规则(services/checkout_service.calculate_order_price)
# 口径: 折后金额(会员折扣→满减→券→积分后)满 99 免运费, 未满收 10
# 对齐生产实证 prod_verify_checkout_shipping.py(50→10/599→0/99→0)
# ============================================================
def _price(goods, coupon=0.0, points=0):
    return calculate_order_price(goods, "L1", points, coupon)


def test_b1_shipping_free_at_threshold_99():
    p = _price(99)
    assert p["shipping"] == 0 and p["finalAmount"] == 99.0


def test_b1_shipping_10_below_threshold():
    p = _price(50)
    assert p["shipping"] == 10 and p["finalAmount"] == 60.0  # 生产实证口径


def test_b1_shipping_free_above_threshold():
    """599 含 L1 满减 30(现行为) → 折后 569 仍免运费"""
    p = _price(599)
    assert p["discountDetail"]["reductionAmount"] == 30.0  # 满减生效(现行为)
    assert p["shipping"] == 0 and p["finalAmount"] == 569.0


def test_b1_coupon_crosses_threshold():
    """券后 95 < 99 → 仍收运费(免邮线在折后判定)"""
    p = _price(100, coupon=5)
    assert p["shipping"] == 10 and p["finalAmount"] == 105.0


def test_b1_points_deduction_cap_30pct():
    """积分抵扣上限 = 原价 30%(放大积分也不得突破)"""
    p = _price(100, points=10_000_000)
    assert p["discountDetail"]["pointsApplied"] <= 30.0


def test_b1_final_equals_discounted_plus_shipping():
    """实付恒等式: finalAmount == 折后 + 运费"""
    for goods, coupon in ((100, 0), (98, 0), (200, 60), (99, 0)):
        p = _price(goods, coupon=coupon)
        discounted = goods - p["discountDetail"]["couponApplied"] \
            - p["discountDetail"]["pointsApplied"] \
            - p["discountDetail"]["reductionAmount"] \
            - p["discountDetail"]["memberDiscount"]
        assert p["finalAmount"] == round(discounted + p["shipping"], 2)


# ============================================================
# B2 评价双写链路(services/product_service.add_review)
# 订单评价闭环: 携带 order_id 时同订单同产品仅可评价一次;
# 评价必须经 repo.add_review 写入商品评价体系(双写 :607/:638 的入口)
# ============================================================
class _FakeProductRepo:
    def __init__(self, existing=None):
        self.existing = existing
        self.add_calls = []

    async def get_review_by_order(self, order_id, product_id):
        return self.existing

    async def add_review(self, product_id, data):
        self.add_calls.append((product_id, dict(data)))
        return {"review_id": f"RV-{len(self.add_calls)}", "rating": data["rating"]}


def _svc(repo):
    from services.product_service import ProductService
    return ProductService(product_repo=repo)


def test_b2_empty_content_rejected():
    async def run():
        repo = _FakeProductRepo()
        try:
            await _svc(repo).add_review("P1", "M1", "tester", 5, "   ")
        except ValueError as e:
            assert "不能为空" in str(e)
            return
        raise AssertionError("空内容未被拦截")
    asyncio.run(run())


def test_b2_content_over_500_chars_rejected():
    async def run():
        repo = _FakeProductRepo()
        try:
            await _svc(repo).add_review("P1", "M1", "t", 5, "好" * 501)
        except ValueError as e:
            assert "500" in str(e)
            return
        raise AssertionError("超长内容未被拦截")
    asyncio.run(run())


def test_b2_images_over_9_rejected():
    async def run():
        repo = _FakeProductRepo()
        try:
            await _svc(repo).add_review("P1", "M1", "t", 5, "不错",
                                        images=[f"i{i}" for i in range(10)])
        except ValueError as e:
            assert "9" in str(e)
            return
        raise AssertionError("超限图片未被拦截")
    asyncio.run(run())


def test_b2_duplicate_order_review_rejected():
    async def run():
        repo = _FakeProductRepo(existing={"review_id": "RV-0"})
        try:
            await _svc(repo).add_review("P1", "M1", "t", 5, "回购",
                                        order_id="ORD-1")
        except ValueError as e:
            assert "不可重复评价" in str(e)
            assert repo.add_calls == []  # 拦截在校验层, 未落库
            return
        raise AssertionError("重复评价未被拦截")
    asyncio.run(run())


def test_b2_review_written_exactly_once_with_payload():
    async def run():
        repo = _FakeProductRepo()
        res = await _svc(repo).add_review(
            "P1", "M7", "竹友", 4, "口感醇厚", order_id="ORD-9",
            images=["a.png"], is_anonymous=False)
        assert res["success"] is True and res["reviewId"] == "RV-1" and res["rating"] == 4
        assert len(repo.add_calls) == 1  # 双写入口恰好触发一次
        pid, data = repo.add_calls[0]
        assert pid == "P1" and data["order_id"] == "ORD-9"
        assert data["member_id"] == "M7" and data["rating"] == 4
        assert data["member_nickname"] == "竹友"  # 非匿名保留昵称
    asyncio.run(run())


def test_b2_anonymous_masks_nickname():
    async def run():
        repo = _FakeProductRepo()
        await _svc(repo).add_review("P1", "M8", "某人", 5, "好评", is_anonymous=True)
        assert repo.add_calls[0][1]["member_nickname"] == "匿名用户"
    asyncio.run(run())


# ============================================================
# B3 注册面(core/age_gate: birthdate 硬校验成年 18)
# ============================================================
def test_b3_adult_on_exact_18th_birthday():
    today = date.today()
    bd = today.replace(year=today.year - 18)
    assert is_adult(bd.isoformat()) is True  # 18 岁生日当天即成年


def test_b3_minor_under_18():
    today = date.today()
    bd = today.replace(year=today.year - 17)
    assert is_adult(bd.isoformat()) is False


def test_b3_calc_age_known_value():
    today = date.today()
    bd = f"{today.year - 26}-{today.month:02d}-{max(today.day - 1, 1):02d}"
    assert calc_age(bd) in (25, 26)


def test_b3_invalid_birthdate_raises():
    for bad in ("", "2000/01/01", "abc", "2000-13-01"):
        try:
            parse_birthdate(bad)
        except ValueError:
            continue
        raise AssertionError(f"非法生日未被拦截: {bad!r}")
