"""老酒兑换及回收模块端到端测试(Service 层, 无需 Docker/fastapi)

直接调用 RecycleService 方法, 模拟 20 个 HTTP 接口的业务逻辑。
使用 asyncio 内存模式(LOCK_MODE=asyncio, STORE_MODE=asyncio)。

运行:
    $env:LOCK_MODE="asyncio"; $env:STORE_MODE="asyncio"
    python test_recycle_routes.py

覆盖 20 个接口对应的业务方法:
    1. 老酒估价(2):     submit_valuation / get_valuation
    2. 老酒申请(3):     submit_application / review_application / list_applications
    3. 老酒兑换(2):     exchange_new_wine / complete_exchange
    4. 老酒回收(1):     recycle_for_cash
    5. 查询(2):          list_exchanges / get_inventory
    6. 状态(1):          transition_status
    7. 统计(1):          get_stats
    8. 新酒估价(1):      submit_new_wine_valuation
    9. 议价(5):          get_negotiation / list_negotiations / user_propose / ai_counter / accept
    10. 议价拒绝(1):     reject_negotiation
    11. 新酒回收(1):     complete_new_wine_recycle
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
    TYPE_EXCHANGE, TYPE_RECYCLE, TYPE_NEW_WINE_RECYCLE,
    GRADE_A, GRADE_B, GRADE_C, GRADE_D,
    STATUS_PENDING, STATUS_VALUING, STATUS_VALUED, STATUS_REVIEWING,
    STATUS_APPROVED, STATUS_REJECTED, STATUS_RECYCLING, STATUS_EXCHANGING,
    STATUS_COMPLETED, STATUS_CANCELLED,
    STATUS_TRANSITIONS,
    WINE_AGE_CURRENT, WINE_AGE_ONE_YEAR, WINE_AGE_TWO_YEARS, WINE_AGE_THREE_YEARS,
    WINE_AGE_CATEGORY_MAP, WINE_AGE_CATEGORY_NAMES, NEW_WINE_DISCOUNT_RATES,
    NEG_STATUS_PENDING, NEG_STATUS_USER_PROPOSED, NEG_STATUS_AI_COUNTER,
    NEG_STATUS_ACCEPTED, NEG_STATUS_REJECTED,
    MAX_NEGOTIATION_ROUNDS,
    NEGOTIATION_COEFFICIENT_MIN, NEGOTIATION_COEFFICIENT_MAX,
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


def _new_purchase_date(years_ago: int) -> str:
    """生成N年前的购买日期(新酒用, 确保酒龄精确)"""
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
# 新酒议价回收测试(当年/1年/2年/3年酒)
# ============================================================

class TestNewWineValuation:
    """新酒估价测试"""

    async def run(self, svc):
        # test 45: 当年酒估价(0年, 9折)
        neg = await svc.submit_new_wine_valuation(
            USER_ID_1, PRODUCT_ID_1, 1000.0, _new_purchase_date(0), GRADE_A, 1
        )
        record("test_45_current_wine_valuation",
               neg["wineAgeCategory"] == WINE_AGE_CURRENT,
               f"expected {WINE_AGE_CURRENT}, got {neg['wineAgeCategory']}")

        # test 46: 当年酒AI基准价(1000 × 90% × 1.0 = 900)
        record("test_46_current_wine_base_price",
               neg["aiBasePrice"] == 900.0,
               f"expected 900.0, got {neg['aiBasePrice']}")

        # test 47: 1年酒估价(85折)
        neg1 = await svc.submit_new_wine_valuation(
            USER_ID_1, PRODUCT_ID_1, 1000.0, _new_purchase_date(1), GRADE_A, 1
        )
        record("test_47_one_year_wine",
               neg1["wineAgeCategory"] == WINE_AGE_ONE_YEAR and neg1["aiBasePrice"] == 850.0,
               f"expected {WINE_AGE_ONE_YEAR}/850.0, got {neg1['wineAgeCategory']}/{neg1['aiBasePrice']}")

        # test 48: 2年酒估价(8折)
        neg2 = await svc.submit_new_wine_valuation(
            USER_ID_1, PRODUCT_ID_1, 1000.0, _new_purchase_date(2), GRADE_A, 1
        )
        record("test_48_two_years_wine",
               neg2["wineAgeCategory"] == WINE_AGE_TWO_YEARS and neg2["aiBasePrice"] == 800.0,
               f"expected {WINE_AGE_TWO_YEARS}/800.0, got {neg2['wineAgeCategory']}/{neg2['aiBasePrice']}")

        # test 49: 3年酒估价(75折)
        neg3 = await svc.submit_new_wine_valuation(
            USER_ID_1, PRODUCT_ID_1, 1000.0, _new_purchase_date(3), GRADE_A, 1
        )
        record("test_49_three_years_wine",
               neg3["wineAgeCategory"] == WINE_AGE_THREE_YEARS and neg3["aiBasePrice"] == 750.0,
               f"expected {WINE_AGE_THREE_YEARS}/750.0, got {neg3['wineAgeCategory']}/{neg3['aiBasePrice']}")

        # test 50: 品质B级影响(当年酒: 1000 × 90% × 95% = 855)
        neg_b = await svc.submit_new_wine_valuation(
            USER_ID_1, PRODUCT_ID_1, 1000.0, _new_purchase_date(0), GRADE_B, 1
        )
        record("test_50_grade_b_coefficient",
               neg_b["aiBasePrice"] == 855.0,
               f"expected 855.0, got {neg_b['aiBasePrice']}")

        # test 51: 多瓶回收(2瓶当年酒: 900 × 2 = 1800)
        neg_multi = await svc.submit_new_wine_valuation(
            USER_ID_1, PRODUCT_ID_1, 1000.0, _new_purchase_date(0), GRADE_A, 2
        )
        record("test_51_multi_bottles",
               neg_multi["bottleCount"] == 2,
               f"expected 2, got {neg_multi['bottleCount']}")

        # test 52: 酒龄超过3年拒绝
        try:
            await svc.submit_new_wine_valuation(
                USER_ID_1, PRODUCT_ID_1, 1000.0, _new_purchase_date(4), GRADE_A, 1
            )
            record("test_52_over_age_reject", False, "应抛出ValueError")
        except ValueError:
            record("test_52_over_age_reject", True)

        # test 53: 议价初始状态
        record("test_53_initial_status",
               neg["status"] == NEG_STATUS_PENDING,
               f"expected {NEG_STATUS_PENDING}, got {neg['status']}")


class TestNegotiation:
    """议价流程测试"""

    async def run(self, svc):
        # 准备: 创建议价记录(当年酒, AI基准价900)
        neg = await svc.submit_new_wine_valuation(
            USER_ID_1, PRODUCT_ID_1, 1000.0, _new_purchase_date(0), GRADE_A, 1
        )
        neg_id = neg["id"]
        ai_base = neg["aiBasePrice"]  # 900.0

        # test 54: 用户出价(系数1.05, 价格945)
        proposed = round(ai_base * 1.05, 2)  # 945
        result = await svc.user_propose_price(neg_id, proposed, "品质良好")
        record("test_54_user_propose",
               result["status"] == NEG_STATUS_USER_PROPOSED and result["currentPrice"] == proposed,
               f"expected {NEG_STATUS_USER_PROPOSED}/{proposed}, got {result['status']}/{result['currentPrice']}")

        # test 55: 轮次递增(第1轮)
        record("test_55_round_increment",
               result["negotiationRound"] == 1,
               f"expected 1, got {result['negotiationRound']}")

        # test 56: AI反价(系数1.02, 价格918)
        counter = round(ai_base * 1.02, 2)  # 918
        result = await svc.ai_counter_price(neg_id, counter, "市场参考价")
        record("test_56_ai_counter",
               result["status"] == NEG_STATUS_AI_COUNTER and result["currentPrice"] == counter,
               f"expected {NEG_STATUS_AI_COUNTER}/{counter}, got {result['status']}/{result['currentPrice']}")

        # test 57: 接受议价(取当前价918)
        result = await svc.accept_negotiation(neg_id, "user")
        record("test_57_accept_negotiation",
               result["status"] == NEG_STATUS_ACCEPTED and result["finalPrice"] == counter,
               f"expected {NEG_STATUS_ACCEPTED}/{counter}, got {result['status']}/{result['finalPrice']}")

        # test 58: 查询议价记录
        neg_data = await svc.get_negotiation(neg_id)
        record("test_58_get_negotiation",
               neg_data["id"] == neg_id,
               f"expected {neg_id}, got {neg_data['id']}")

        # test 59: 议价历史记录(初始1条 + 用户出价 + AI反价 + 接受 = 4条)
        record("test_59_negotiation_history",
               len(neg_data["history"]) == 4,
               f"expected 4 history entries, got {len(neg_data['history'])}")


class TestNegotiationEdgeCases:
    """议价边界场景测试"""

    async def run(self, svc):
        # test 60: 系数超范围(0.85 < 0.90)
        neg = await svc.submit_new_wine_valuation(
            USER_ID_1, PRODUCT_ID_1, 1000.0, _new_purchase_date(0), GRADE_A, 1
        )
        ai_base = neg["aiBasePrice"]  # 900
        too_low = round(ai_base * 0.85, 2)  # 765, 系数0.85 < 0.90
        try:
            await svc.user_propose_price(neg["id"], too_low)
            record("test_60_coefficient_too_low", False, "应抛出ValueError")
        except ValueError:
            record("test_60_coefficient_too_low", True)

        # test 61: 系数超范围(1.15 > 1.10)
        too_high = round(ai_base * 1.15, 2)  # 1035, 系数1.15 > 1.10
        try:
            await svc.user_propose_price(neg["id"], too_high)
            record("test_61_coefficient_too_high", False, "应抛出ValueError")
        except ValueError:
            record("test_61_coefficient_too_high", True)

        # test 62: 状态非法(AI反价时状态须为user_proposed)
        try:
            await svc.ai_counter_price(neg["id"], ai_base * 1.0)
            record("test_62_ai_counter_wrong_status", False, "应抛出ValueError")
        except ValueError:
            record("test_62_ai_counter_wrong_status", True)

        # test 63: 查询不存在的议价记录
        try:
            await svc.get_negotiation(99999)
            record("test_63_negotiation_not_found", False, "应抛出KeyError")
        except KeyError:
            record("test_63_negotiation_not_found", True)

        # test 64: 议价轮次超限(最多3轮)
        neg2 = await svc.submit_new_wine_valuation(
            USER_ID_1, PRODUCT_ID_2, 1000.0, _new_purchase_date(1), GRADE_A, 1
        )
        ai_base2 = neg2["aiBasePrice"]
        # 执行3轮议价
        for i in range(3):
            await svc.user_propose_price(neg2["id"], round(ai_base2 * 1.05, 2))
            await svc.ai_counter_price(neg2["id"], round(ai_base2 * 1.02, 2))
        # 第4轮应失败
        try:
            await svc.user_propose_price(neg2["id"], round(ai_base2 * 1.05, 2))
            record("test_64_round_exceeded", False, "应抛出ValueError")
        except ValueError:
            record("test_64_round_exceeded", True)

        # test 65: 拒绝议价
        neg3 = await svc.submit_new_wine_valuation(
            USER_ID_1, PRODUCT_ID_1, 1000.0, _new_purchase_date(0), GRADE_A, 1
        )
        result = await svc.reject_negotiation(neg3["id"], "user", "价格不合适")
        record("test_65_reject_negotiation",
               result["status"] == NEG_STATUS_REJECTED,
               f"expected {NEG_STATUS_REJECTED}, got {result['status']}")

        # test 66: 已拒绝的议价不能接受
        try:
            await svc.accept_negotiation(neg3["id"], "user")
            record("test_66_accept_after_reject", False, "应抛出ValueError")
        except ValueError:
            record("test_66_accept_after_reject", True)

        # test 67: 列表查询议价记录
        negs = await svc.list_negotiations(user_id=USER_ID_1)
        record("test_67_list_negotiations",
               len(negs) >= 3,
               f"expected >=3, got {len(negs)}")


class TestNewWineRecycle:
    """新酒回收完成测试"""

    async def run(self, svc):
        # 准备: 议价成功(3年酒, 原价1000, 75折, AI基准750, 议价765)
        neg = await svc.submit_new_wine_valuation(
            USER_ID_1, PRODUCT_ID_1, 1000.0, _new_purchase_date(3), GRADE_A, 2  # 2瓶
        )
        ai_base = neg["aiBasePrice"]  # 750.0
        proposed = round(ai_base * 1.02, 2)  # 765
        await svc.user_propose_price(neg["id"], proposed)
        await svc.accept_negotiation(neg["id"], "ai", proposed)

        # test 68: 完成新酒回收
        result = await svc.complete_new_wine_recycle(neg["id"], "wechat", "***8888")
        record("test_68_complete_new_wine_recycle",
               result["type"] == TYPE_NEW_WINE_RECYCLE and result["status"] == STATUS_COMPLETED,
               f"expected {TYPE_NEW_WINE_RECYCLE}/{STATUS_COMPLETED}, got {result['type']}/{result['status']}")

        # test 69: 回收总价(765 × 2瓶 = 1530)
        record("test_69_total_value",
               result["oldWineTotalValue"] == 1530.0,
               f"expected 1530.0, got {result['oldWineTotalValue']}")

        # test 70: 折现金额(1530 × 80% = 1224)
        record("test_70_cash_amount",
               result["cashAmount"] == 1224.0,
               f"expected 1224.0, got {result['cashAmount']}")

        # test 71: 个税计算(1224 > 800, 个税 = (1224-800)×20% = 84.8)
        record("test_71_tax_amount",
               result["taxAmount"] == 84.8,
               f"expected 84.8, got {result['taxAmount']}")

        # test 72: 实付金额(1224 - 84.8 = 1139.2)
        record("test_72_actual_payout",
               result["actualPayout"] == 1139.2,
               f"expected 1139.2, got {result['actualPayout']}")

        # test 73: 瓶数记录
        record("test_73_bottle_count",
               result["bottleCount"] == 2,
               f"expected 2, got {result['bottleCount']}")

        # test 74: 新酒分类记录
        record("test_74_wine_age_category",
               result["wineAgeCategory"] == WINE_AGE_THREE_YEARS,
               f"expected {WINE_AGE_THREE_YEARS}, got {result['wineAgeCategory']}")

        # test 75: 未接受的议价不能回收
        neg2 = await svc.submit_new_wine_valuation(
            USER_ID_1, PRODUCT_ID_1, 1000.0, _new_purchase_date(0), GRADE_A, 1
        )
        try:
            await svc.complete_new_wine_recycle(neg2["id"], "wechat", "***")
            record("test_75_recycle_not_accepted", False, "应抛出ValueError")
        except ValueError:
            record("test_75_recycle_not_accepted", True)

        # test 76: 库存入库验证
        inventory = await svc.get_inventory(PRODUCT_ID_1)
        stock = inventory.get(PRODUCT_ID_1, {}).get("stock", 0)
        record("test_76_inventory_updated",
               stock >= 2,
               f"expected >=2, got {stock}")


# ============================================================
# 测试运行
# ============================================================

async def main():
    print("=" * 60)
    print("老酒兑换及回收模块端到端测试(含新酒议价回收)")
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
        TestNewWineValuation,
        TestNegotiation,
        TestNegotiationEdgeCases,
        TestNewWineRecycle,
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
