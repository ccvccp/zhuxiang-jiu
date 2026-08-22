"""老酒兑换及回收模块端到端测试(Service 层, 无需 Docker/fastapi)

直接调用 RecycleService 方法, 模拟 12 个 HTTP 接口的业务逻辑。
使用 asyncio 内存模式(LOCK_MODE=asyncio, STORE_MODE=asyncio)。

运行:
    $env:LOCK_MODE="asyncio"; $env:STORE_MODE="asyncio"
    python test_recycle_routes.py

覆盖 12 个接口对应的业务方法:
    1. 估价(2):     submit_valuation / get_valuation
    2. 申请(3):     submit_application / review_application / list_applications
    3. 兑换(2):     exchange_new_wine / complete_exchange
    4. 回收(1):     recycle_for_cash
    5. 查询(2):     list_exchanges / get_inventory
    6. 状态(1):     transition_status
    7. 统计(1):     get_stats
"""

import asyncio
import os
import sys
from datetime import date, timedelta

# 确保使用内存模式
os.environ["LOCK_MODE"] = "asyncio"
os.environ["STORE_MODE"] = "asyncio"

from services.recycle_service import RecycleService
from repositories.recycle_repository import (
    RecycleRepository,
    TYPE_EXCHANGE, TYPE_RECYCLE,
    GRADE_A, GRADE_B, GRADE_C, GRADE_D,
    STATUS_PENDING, STATUS_VALUING, STATUS_VALUED, STATUS_REVIEWING,
    STATUS_APPROVED, STATUS_REJECTED, STATUS_RECYCLING, STATUS_EXCHANGING,
    STATUS_COMPLETED, STATUS_CANCELLED,
    STATUS_TRANSITIONS,
)
from repositories.store import _mock_store, reset_store as _reset_store_impl

# 测试结果收集
PASS = 0
FAIL = 0
RESULTS = []


def record(name, passed, detail=""):
    global PASS, FAIL
    if passed:
        PASS += 1
        RESULTS.append(f"  \u2713 {name}")
    else:
        FAIL += 1
        RESULTS.append(f"  \u2717 {name} \u2014 {detail}")


def reset_store():
    """重置内存存储, 保证测试隔离"""
    _reset_store_impl()


# ============================================================
# 测试数据
# ============================================================

USER_ID_1 = 1001
USER_ID_2 = 1002
PRODUCT_ID_1 = "ZX42-2026L07"
PRODUCT_ID_2 = "ZX52-2026L02"
NEW_PRODUCT_ID = "ZX53-2026Z01"


def _old_purchase_date(years_ago: int) -> str:
    """生成N年前的购买日期"""
    d = date.today() - timedelta(days=365 * years_ago + 10)
    return d.isoformat()


# ============================================================
# 测试用例
# ============================================================

class TestValuation:
    """老酒估价测试"""

    async def run(self, svc):
        # test 1: 正常估价(3年酒龄, A级)
        result = await svc.submit_valuation(
            USER_ID_1, PRODUCT_ID_1, 1000.0, _old_purchase_date(3),
            GRADE_A, 1, True
        )
        record("test_01_normal_valuation",
               result["wineAge"] == 3 and result["appreciationRate"] == 0.15,
               f"expected 3/0.15, got {result['wineAge']}/{result['appreciationRate']}")

        # test 2: 老酒价值计算(¥1000 × 1.15 × 1.0 = ¥1150)
        record("test_02_old_value_calc",
               result["oldValue"] == 1150.0,
               f"expected 1150.0, got {result['oldValue']}")

        # test 3: 折现金额(¥1150 × 0.8 = ¥920)
        record("test_03_cash_value_calc",
               result["cashValue"] == 920.0,
               f"expected 920.0, got {result['cashValue']}")

        # test 4: 酒龄不足(2年)
        try:
            await svc.submit_valuation(
                USER_ID_1, PRODUCT_ID_1, 1000.0, _old_purchase_date(2),
                GRADE_A, 1, True
            )
            record("test_04_underage_wine", False, "应抛出ValueError")
        except ValueError:
            record("test_04_underage_wine", True)

        # test 5: 增值率封顶(20年, 100%)
        result = await svc.submit_valuation(
            USER_ID_1, PRODUCT_ID_1, 1000.0, _old_purchase_date(20),
            GRADE_A, 1, True
        )
        record("test_05_capped_rate",
               result["appreciationRate"] == 1.0,
               f"expected 1.0, got {result['appreciationRate']}")

        # test 6: 品质分级B级(95%)
        result = await svc.submit_valuation(
            USER_ID_1, PRODUCT_ID_1, 1000.0, _old_purchase_date(5),
            GRADE_B, 1, True
        )
        expected_value = round(1000.0 * 1.25 * 0.95, 2)
        record("test_06_grade_b",
               result["oldValue"] == expected_value,
               f"expected {expected_value}, got {result['oldValue']}")

        # test 7: 会员等级加成(L5 +8%)
        result = await svc.submit_valuation(
            USER_ID_1, PRODUCT_ID_1, 1000.0, _old_purchase_date(3),
            GRADE_A, 5, True
        )
        # 15% + 8% = 23%, value = 1000 × 1.23 = 1230
        record("test_07_level_bonus",
               result["appreciationRate"] == 0.23 and result["oldValue"] == 1230.0,
               f"expected 0.23/1230.0, got {result['appreciationRate']}/{result['oldValue']}")

        # test 8: 回收不加等级加成
        result = await svc.submit_valuation(
            USER_ID_1, PRODUCT_ID_1, 1000.0, _old_purchase_date(3),
            GRADE_A, 5, False
        )
        record("test_08_no_bonus_for_recycle",
               result["appreciationRate"] == 0.15,
               f"expected 0.15, got {result['appreciationRate']}")

        # test 9: 查询估价
        val_id = result["id"]
        fetched = await svc.get_valuation(val_id)
        record("test_09_get_valuation",
               fetched["id"] == val_id,
               f"expected id={val_id}")

        # test 10: 无效品质分级
        try:
            await svc.submit_valuation(
                USER_ID_1, PRODUCT_ID_1, 1000.0, _old_purchase_date(3),
                "X", 1, True
            )
            record("test_10_invalid_grade", False, "应抛出ValueError")
        except ValueError:
            record("test_10_invalid_grade", True)


class TestApplication:
    """回收申请测试"""

    async def run(self, svc):
        # 准备估价
        val1 = await svc.submit_valuation(
            USER_ID_1, PRODUCT_ID_1, 1000.0, _old_purchase_date(3), GRADE_A, 1, True
        )
        val2 = await svc.submit_valuation(
            USER_ID_1, PRODUCT_ID_2, 800.0, _old_purchase_date(4), GRADE_B, 1, True
        )

        # test 11: 提交兑换申请
        result = await svc.submit_application(
            USER_ID_1, TYPE_EXCHANGE, [val1["id"], val2["id"]],
            NEW_PRODUCT_ID, 2000.0
        )
        record("test_11_submit_exchange",
               result["type"] == TYPE_EXCHANGE and result["status"] == STATUS_PENDING,
               f"expected exchange/pending, got {result['type']}/{result['status']}")

        # test 12: 老酒总价值累加
        expected_total = val1["oldValue"] + val2["oldValue"]
        record("test_12_total_value",
               result["oldWineTotalValue"] == round(expected_total, 2),
               f"expected {expected_total}, got {result['oldWineTotalValue']}")

        # test 13: 超单次兑换瓶数限制(6 > 5)
        vals = []
        for i in range(6):
            v = await svc.submit_valuation(
                USER_ID_2, PRODUCT_ID_1, 1000.0, _old_purchase_date(3), GRADE_A, 1, True
            )
            vals.append(v["id"])
        try:
            await svc.submit_application(
                USER_ID_2, TYPE_EXCHANGE, vals, NEW_PRODUCT_ID, 5000.0
            )
            record("test_13_over_bottle_limit", False, "应抛出ValueError")
        except ValueError:
            record("test_13_over_bottle_limit", True)

        # test 14: 提交回收申请
        val3 = await svc.submit_valuation(
            USER_ID_1, PRODUCT_ID_1, 1000.0, _old_purchase_date(5), GRADE_A, 1, False
        )
        result = await svc.submit_application(
            USER_ID_1, TYPE_RECYCLE, [val3["id"]],
            payout_method="wechat", payout_account="***8888"
        )
        record("test_14_submit_recycle",
               result["type"] == TYPE_RECYCLE,
               f"expected recycle, got {result['type']}")

        # test 15: 查询申请列表
        apps = await svc.list_applications(user_id=USER_ID_1)
        record("test_15_list_applications",
               len(apps) >= 2,
               f"expected >=2, got {len(apps)}")

        # test 16: 无效业务类型
        try:
            await svc.submit_application(
                USER_ID_1, "invalid", [val1["id"]], NEW_PRODUCT_ID, 1000.0
            )
            record("test_16_invalid_type", False, "应抛出ValueError")
        except ValueError:
            record("test_16_invalid_type", True)


class TestReview:
    """审核测试"""

    async def run(self, svc):
        # 准备申请
        val = await svc.submit_valuation(
            USER_ID_1, PRODUCT_ID_1, 1000.0, _old_purchase_date(3), GRADE_A, 1, True
        )
        app = await svc.submit_application(
            USER_ID_1, TYPE_EXCHANGE, [val["id"]], NEW_PRODUCT_ID, 2000.0
        )
        app_id = app["id"]

        # test 17: 审核通过
        result = await svc.review_application(app_id, True, "admin", "通过")
        record("test_17_review_approve",
               result["status"] == STATUS_APPROVED,
               f"expected {STATUS_APPROVED}, got {result['status']}")

        # test 18: 审核拒绝(需新申请)
        val2 = await svc.submit_valuation(
            USER_ID_2, PRODUCT_ID_1, 1000.0, _old_purchase_date(3), GRADE_A, 1, True
        )
        app2 = await svc.submit_application(
            USER_ID_2, TYPE_EXCHANGE, [val2["id"]], NEW_PRODUCT_ID, 2000.0
        )
        result = await svc.review_application(app2["id"], False, "admin", "拒绝")
        record("test_18_review_reject",
               result["status"] == STATUS_REJECTED,
               f"expected {STATUS_REJECTED}, got {result['status']}")

        # test 19: 重复审核(状态非法)
        try:
            await svc.review_application(app_id, True, "admin")
            record("test_19_duplicate_review", False, "应抛出ValueError")
        except ValueError:
            record("test_19_duplicate_review", True)

        # test 20: 审核不存在的申请
        try:
            await svc.review_application(99999, True, "admin")
            record("test_20_review_not_found", False, "应抛出KeyError")
        except KeyError:
            record("test_20_review_not_found", True)


class TestExchange:
    """兑换新酒测试"""

    async def run(self, svc):
        # 准备: 估价→申请→审核通过
        val = await svc.submit_valuation(
            USER_ID_1, PRODUCT_ID_1, 1000.0, _old_purchase_date(5), GRADE_A, 1, True
        )
        # 老酒价值 = 1000 × 1.25 = 1250
        app = await svc.submit_application(
            USER_ID_1, TYPE_EXCHANGE, [val["id"]], NEW_PRODUCT_ID, 1000.0
        )
        app_id = app["id"]
        await svc.review_application(app_id, True, "admin")

        # test 21: 兑换新酒(老酒价值1250 > 新酒价格1000, 差额转积分)
        result = await svc.exchange_new_wine(app_id, NEW_PRODUCT_ID, 1000.0)
        record("test_21_exchange_surplus",
               result["priceDiff"] < 0 and result["pointsConverted"] > 0,
               f"expected diff<0/points>0, got {result['priceDiff']}/{result['pointsConverted']}")

        # test 22: 差额转积分正确(250元 × 10 = 2500竹叶)
        expected_points = int(250 * 10)
        record("test_22_points_conversion",
               result["pointsConverted"] == expected_points,
               f"expected {expected_points}, got {result['pointsConverted']}")

        # test 23: 需补差价(老酒价值 < 新酒价格)
        val2 = await svc.submit_valuation(
            USER_ID_2, PRODUCT_ID_1, 1000.0, _old_purchase_date(3), GRADE_A, 1, True
        )
        app2 = await svc.submit_application(
            USER_ID_2, TYPE_EXCHANGE, [val2["id"]], NEW_PRODUCT_ID, 2000.0
        )
        await svc.review_application(app2["id"], True, "admin")
        result = await svc.exchange_new_wine(app2["id"], NEW_PRODUCT_ID, 2000.0)
        # 老酒价值1150 < 新酒2000, 差价850
        record("test_23_exchange_deficit",
               result["priceDiff"] > 0 and result["diffPaymentAmount"] == 850.0,
               f"expected diff>0/850.0, got {result['priceDiff']}/{result['diffPaymentAmount']}")

        # test 24: 完成兑换
        ex_id = result["id"]
        result = await svc.complete_exchange(ex_id)
        record("test_24_complete_exchange",
               result["status"] == STATUS_COMPLETED,
               f"expected {STATUS_COMPLETED}, got {result['status']}")

        # test 25: 状态非法完成
        try:
            await svc.complete_exchange(ex_id)
            record("test_25_complete_invalid_status", False, "应抛出ValueError")
        except ValueError:
            record("test_25_complete_invalid_status", True)


class TestRecycle:
    """折现回收测试"""

    async def run(self, svc):
        # 准备: 估价→申请→审核通过
        val = await svc.submit_valuation(
            USER_ID_1, PRODUCT_ID_1, 1000.0, _old_purchase_date(5), GRADE_A, 1, False
        )
        # 老酒价值 = 1250, 折现 = 1250 × 0.8 = 1000
        app = await svc.submit_application(
            USER_ID_1, TYPE_RECYCLE, [val["id"]],
            payout_method="wechat", payout_account="***8888"
        )
        app_id = app["id"]
        await svc.review_application(app_id, True, "admin")

        # test 26: 折现回收
        result = await svc.recycle_for_cash(app_id, "wechat", "***8888")
        record("test_26_recycle_for_cash",
               result["type"] == TYPE_RECYCLE and result["status"] == STATUS_RECYCLING,
               f"expected recycle/recycling, got {result['type']}/{result['status']}")

        # test 27: 个税计算(折现1000 > 800, 个税 = (1000-800)×20% = 40)
        record("test_27_tax_calc",
               result["taxAmount"] == 40.0,
               f"expected 40.0, got {result['taxAmount']}")

        # test 28: 实付金额(1000 - 40 = 960)
        record("test_28_actual_payout",
               result["actualPayout"] == 960.0,
               f"expected 960.0, got {result['actualPayout']}")

        # test 29: 完成回收
        ex_id = result["id"]
        result = await svc.complete_exchange(ex_id)
        record("test_29_complete_recycle",
               result["status"] == STATUS_COMPLETED,
               f"expected {STATUS_COMPLETED}, got {result['status']}")

        # test 30: 非回收申请执行折现
        val2 = await svc.submit_valuation(
            USER_ID_2, PRODUCT_ID_1, 1000.0, _old_purchase_date(3), GRADE_A, 1, True
        )
        app2 = await svc.submit_application(
            USER_ID_2, TYPE_EXCHANGE, [val2["id"]], NEW_PRODUCT_ID, 1000.0
        )
        await svc.review_application(app2["id"], True, "admin")
        try:
            await svc.recycle_for_cash(app2["id"], "wechat", "***")
            record("test_30_wrong_type_recycle", False, "应抛出ValueError")
        except ValueError:
            record("test_30_wrong_type_recycle", True)


class TestTransition:
    """状态流转测试"""

    async def run(self, svc):
        # test 31: 合法状态流转(pending → cancelled)
        val = await svc.submit_valuation(
            USER_ID_1, PRODUCT_ID_1, 1000.0, _old_purchase_date(3), GRADE_A, 1, True
        )
        app = await svc.submit_application(
            USER_ID_1, TYPE_EXCHANGE, [val["id"]], NEW_PRODUCT_ID, 1000.0
        )
        result = await svc.transition_status(app["id"], STATUS_CANCELLED)
        record("test_31_legal_transition",
               result["status"] == STATUS_CANCELLED,
               f"expected {STATUS_CANCELLED}, got {result['status']}")

        # test 32: 非法状态流转(cancelled → approved)
        try:
            await svc.transition_status(app["id"], STATUS_APPROVED)
            record("test_32_illegal_transition", False, "应抛出ValueError")
        except ValueError:
            record("test_32_illegal_transition", True)

        # test 33: 不存在的申请
        try:
            await svc.transition_status(99999, STATUS_APPROVED)
            record("test_33_transition_not_found", False, "应抛出KeyError")
        except KeyError:
            record("test_33_transition_not_found", True)


class TestQueryStats:
    """查询统计测试"""

    async def run(self, svc):
        # 准备数据: 估价+申请+兑换
        val = await svc.submit_valuation(
            USER_ID_1, PRODUCT_ID_1, 1000.0, _old_purchase_date(3), GRADE_A, 1, True
        )
        app = await svc.submit_application(
            USER_ID_1, TYPE_EXCHANGE, [val["id"]], NEW_PRODUCT_ID, 1000.0
        )
        await svc.review_application(app["id"], True, "admin")
        await svc.exchange_new_wine(app["id"], NEW_PRODUCT_ID, 1000.0)

        # test 34: 查询兑换记录
        exchanges = await svc.list_exchanges(user_id=USER_ID_1)
        record("test_34_list_exchanges",
               len(exchanges) >= 1,
               f"expected >=1, got {len(exchanges)}")

        # test 35: 查询回收库存
        inventory = await svc.get_inventory()
        record("test_35_get_inventory",
               isinstance(inventory, dict),
               f"unexpected: {inventory}")

        # test 36: 统计
        stats = await svc.get_stats(user_id=USER_ID_1)
        record("test_36_stats",
               "totalApplications" in stats and "totalExchanges" in stats,
               f"统计字段缺失: {stats}")

        # test 37: 统计正确性
        record("test_37_stats_correct",
               stats["totalApplications"] >= 1 and stats["totalExchanges"] >= 1,
               f"统计错误: {stats}")


class TestEdgeCases:
    """边界场景测试"""

    async def run(self, svc):
        # test 38: 购买价格为0
        try:
            await svc.submit_valuation(
                USER_ID_1, PRODUCT_ID_1, 0.0, _old_purchase_date(3), GRADE_A, 1, True
            )
            record("test_38_zero_price", False, "应抛出ValueError")
        except ValueError:
            record("test_38_zero_price", True)

        # test 39: 空估价列表
        try:
            await svc.submit_application(
                USER_ID_1, TYPE_EXCHANGE, [], NEW_PRODUCT_ID, 1000.0
            )
            record("test_39_empty_valuations", False, "应抛出ValueError")
        except ValueError:
            record("test_39_empty_valuations", True)

        # test 40: 兑换未指定新酒
        val = await svc.submit_valuation(
            USER_ID_1, PRODUCT_ID_1, 1000.0, _old_purchase_date(3), GRADE_A, 1, True
        )
        try:
            await svc.submit_application(
                USER_ID_1, TYPE_EXCHANGE, [val["id"]], None, None
            )
            record("test_40_exchange_no_new_wine", False, "应抛出ValueError")
        except ValueError:
            record("test_40_exchange_no_new_wine", True)

        # test 41: 查询不存在的估价
        try:
            await svc.get_valuation(99999)
            record("test_41_valuation_not_found", False, "应抛出KeyError")
        except KeyError:
            record("test_41_valuation_not_found", True)

        # test 42: 查询不存在的兑换记录
        try:
            await svc.get_exchange(99999)
            record("test_42_exchange_not_found", False, "应抛出KeyError")
        except KeyError:
            record("test_42_exchange_not_found", True)

        # test 43: 完成不存在的兑换记录
        try:
            await svc.complete_exchange(99999)
            record("test_43_complete_not_found", False, "应抛出KeyError")
        except KeyError:
            record("test_43_complete_not_found", True)

        # test 44: 兑换申请状态非法(未审核)
        val2 = await svc.submit_valuation(
            USER_ID_1, PRODUCT_ID_1, 1000.0, _old_purchase_date(3), GRADE_A, 1, True
        )
        app = await svc.submit_application(
            USER_ID_1, TYPE_EXCHANGE, [val2["id"]], NEW_PRODUCT_ID, 1000.0
        )
        try:
            await svc.exchange_new_wine(app["id"], NEW_PRODUCT_ID, 1000.0)
            record("test_44_exchange_not_approved", False, "应抛出ValueError")
        except ValueError:
            record("test_44_exchange_not_approved", True)


# ============================================================
# 测试运行
# ============================================================

async def main():
    print("=" * 60)
    print("老酒兑换及回收模块端到端测试")
    print("=" * 60)
    print()

    test_classes = [
        TestValuation,
        TestApplication,
        TestReview,
        TestExchange,
        TestRecycle,
        TestTransition,
        TestQueryStats,
        TestEdgeCases,
    ]

    for cls in test_classes:
        reset_store()
        svc = RecycleService()
        print(f"[{cls.__name__}]")
        instance = cls()
        await instance.run(svc)
        print()

    # 输出全部结果
    print("=" * 60)
    print("测试结果汇总:")
    print("-" * 60)
    for r in RESULTS:
        print(r)
    print("-" * 60)
    print(f"通过: {PASS}  失败: {FAIL}  总计: {PASS + FAIL}")
    print("=" * 60)

    if FAIL > 0:
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
