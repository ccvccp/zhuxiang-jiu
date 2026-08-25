"""市级网店模块端到端测试(Service 层, 无需 Docker/fastapi)

直接调用 CityStoreService 方法, 模拟 12 个 HTTP 接口的业务逻辑。
使用 asyncio 内存模式(LOCK_MODE=asyncio, STORE_MODE=asyncio)。

运行:
    $env:LOCK_MODE="asyncio"; $env:STORE_MODE="asyncio"
    python test_citystore_routes.py

覆盖 12 个接口对应的业务方法:
    1. 开店申请(3):  apply / get_store_detail / list_stores
    2. 可用城市(1):  list_available_cities
    3. 审核流程(1):  audit_store
    4. 状态流转(1):  update_status
    5. 月度考核(3):  run_assessment / get_assessment / list_assessments
    6. 订单关联(2):  add_order / list_orders
    7. 管理端(2):    list_pending_stores / get_stats

测试覆盖:
    - SVIP 资格校验(非 SVIP 不可申请)
    - 资质校验(营业执照/食品证必填)
    - 城市独占校验(一城一店)
    - 重复申请校验(同一会员不可重复开店)
    - 审核流程(待审核 → 运营中/已取消)
    - 状态机(运营 → 预警/暂停 → 取消, 已取消为终态)
    - 月度考核(三档折扣 70/80/90 + 连续不达标 + 资格取消)
    - 订单关联(仅运营中网店可关联)
    - 异常分支(资源不存在/状态非法/权限不符)
"""

import asyncio
import os
import sys
from datetime import datetime, timezone, UTC

# 确保使用内存模式
os.environ["LOCK_MODE"] = "asyncio"
os.environ["STORE_MODE"] = "asyncio"

from services.citystore_service import CityStoreService
from repositories.citystore_repository import (
    CityStoreRepository,
    # 网店状态
    STORE_STATUS_PENDING, STORE_STATUS_OPERATING, STORE_STATUS_WARNING,
    STORE_STATUS_SUSPENDED, STORE_STATUS_CANCELLED,
    STORE_STATUS_NAMES, STORE_STATUS_FLOW,
    STORE_ACTIVE_STATUSES, STORE_TERMINAL_STATUSES,
    # 考核资格状态
    QUAL_STATUS_NORMAL, QUAL_STATUS_WARNING, QUAL_STATUS_YELLOW_CARD, QUAL_STATUS_CANCELLED,
    QUAL_STATUS_NAMES,
    # 阶梯折扣
    DISCOUNT_EXCELLENT, DISCOUNT_QUALIFIED, DISCOUNT_UNQUALIFIED,
    PURCHASE_TARGET, SALES_TARGET, MAX_CONSECUTIVE_BELOW,
    # 销售渠道
    CHANNEL_LIVE, CHANNEL_MINIPROGRAM, CHANNEL_COMMUNITY, CHANNEL_H5, CHANNEL_DOUYIN,
    # 函数
    calc_discount,
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
        RESULTS.append(f"  ✓ {name}")
    else:
        FAIL += 1
        RESULTS.append(f"  ✗ {name} — {detail}")


def reset_store():
    """重置内存存储, 保证测试隔离"""
    _reset_store_impl()


def current_month() -> str:
    """当前 UTC 月份(YYYY-MM), 与 ts() 对齐"""
    return datetime.now(UTC).strftime("%Y-%m")


# ============================================================
# 测试数据
# ============================================================

# SVIP 会员
MEMBER_ID_SVIP = 1001
MEMBER_LEVEL_SVIP = 5

# 非 SVIP 会员
MEMBER_ID_NON_SVIP = 1002
MEMBER_LEVEL_NON_SVIP = 3

# 测试城市 1: 济南市
CITY_CODE_JN = "370100"
CITY_NAME_JN = "济南市"
PROVINCE_CODE_SD = "370000"
PROVINCE_NAME_SD = "山东省"

# 测试城市 2: 青岛市
CITY_CODE_QD = "370200"
CITY_NAME_QD = "青岛市"

# 测试城市 3: 淄博市
CITY_CODE_ZB = "370300"
CITY_NAME_ZB = "淄博市"

# 资质
BUSINESS_LICENSE = "91370100MA00001XX"
FOOD_LICENSE = "JY13701001234567"
TAX_REG_NO = "91370100MA00001XX"

# 产品
PRODUCT_ID = "ZX42-2026L07"
PRODUCT_NAME = "竹香型 42° 500ml"
RETAIL_PRICE = 268.0


# ============================================================
# 测试用例
# ============================================================

class TestCityStoreApply:
    """开店申请测试"""

    async def run(self, svc):
        # test 01: 非 SVIP 不可申请
        try:
            await svc.apply(
                member_id=MEMBER_ID_NON_SVIP, member_level=MEMBER_LEVEL_NON_SVIP,
                store_name="非 SVIP 网店", city_code=CITY_CODE_JN, city_name=CITY_NAME_JN,
                province_code=PROVINCE_CODE_SD, province_name=PROVINCE_NAME_SD,
                business_license=BUSINESS_LICENSE, food_license=FOOD_LICENSE,
            )
            record("test_01_non_svip_blocked", False, "应抛出 ValueError")
        except ValueError:
            record("test_01_non_svip_blocked", True)

        # test 02: 营业执照缺失被拒
        try:
            await svc.apply(
                member_id=MEMBER_ID_SVIP, member_level=MEMBER_LEVEL_SVIP,
                store_name="无照网店", city_code=CITY_CODE_JN, city_name=CITY_NAME_JN,
                province_code=PROVINCE_CODE_SD, province_name=PROVINCE_NAME_SD,
                business_license="", food_license=FOOD_LICENSE,
            )
            record("test_02_no_business_license", False, "应抛出 ValueError")
        except ValueError:
            record("test_02_no_business_license", True)

        # test 03: 食品许可证缺失被拒
        try:
            await svc.apply(
                member_id=MEMBER_ID_SVIP, member_level=MEMBER_LEVEL_SVIP,
                store_name="无证网店", city_code=CITY_CODE_JN, city_name=CITY_NAME_JN,
                province_code=PROVINCE_CODE_SD, province_name=PROVINCE_NAME_SD,
                business_license=BUSINESS_LICENSE, food_license="",
            )
            record("test_03_no_food_license", False, "应抛出 ValueError")
        except ValueError:
            record("test_03_no_food_license", True)

        # test 04: SVIP 申请成功
        store = await svc.apply(
            member_id=MEMBER_ID_SVIP, member_level=MEMBER_LEVEL_SVIP,
            store_name="竹香济南网店", city_code=CITY_CODE_JN, city_name=CITY_NAME_JN,
            province_code=PROVINCE_CODE_SD, province_name=PROVINCE_NAME_SD,
            business_license=BUSINESS_LICENSE, food_license=FOOD_LICENSE,
            tax_reg_no=TAX_REG_NO,
        )
        record("test_04_apply_success",
               store["storeCode"].startswith(f"CS-{CITY_CODE_JN}-"),
               f"storeCode 格式错误: {store['storeCode']}")

        # test 05: 状态为待审核
        record("test_05_status_pending",
               store["status"] == STORE_STATUS_PENDING,
               f"状态错误: {store.get('status')}")

        # test 06: statusName 正确
        record("test_06_status_name",
               store.get("statusName") == "待审核",
               f"statusName 错误: {store.get('statusName')}")

        # test 07: 默认折扣为 90(未达标)
        record("test_07_default_discount",
               store["currentDiscount"] == DISCOUNT_UNQUALIFIED,
               f"默认折扣错误: {store.get('currentDiscount')}")

        # test 08: 重复申请被拒(同一会员)
        try:
            await svc.apply(
                member_id=MEMBER_ID_SVIP, member_level=MEMBER_LEVEL_SVIP,
                store_name="第二家店", city_code=CITY_CODE_QD, city_name=CITY_NAME_QD,
                province_code=PROVINCE_CODE_SD, province_name=PROVINCE_NAME_SD,
                business_license=BUSINESS_LICENSE, food_license=FOOD_LICENSE,
            )
            record("test_08_duplicate_member_blocked", False, "应抛出 ValueError")
        except ValueError:
            record("test_08_duplicate_member_blocked", True)

        # test 09: 城市独占校验(同城市被拒, 不同会员)
        try:
            await svc.apply(
                member_id=2001, member_level=MEMBER_LEVEL_SVIP,
                store_name="抢占济南", city_code=CITY_CODE_JN, city_name=CITY_NAME_JN,
                province_code=PROVINCE_CODE_SD, province_name=PROVINCE_NAME_SD,
                business_license="91370100MA00002YY", food_license="JY13701007654321",
            )
            record("test_09_city_occupied_blocked", False, "应抛出 ValueError")
        except ValueError:
            record("test_09_city_occupied_blocked", True)

        # test 10: 不同城市可申请(另一 SVIP 会员)
        store2 = await svc.apply(
            member_id=2002, member_level=MEMBER_LEVEL_SVIP,
            store_name="竹香青岛网店", city_code=CITY_CODE_QD, city_name=CITY_NAME_QD,
            province_code=PROVINCE_CODE_SD, province_name=PROVINCE_NAME_SD,
            business_license="91370200MA00003ZZ", food_license="JY13702001122334",
        )
        record("test_10_different_city_success",
               store2["storeCode"].startswith(f"CS-{CITY_CODE_QD}-"),
               f"不同城市申请失败: {store2.get('storeCode')}")


class TestCityStoreQuery:
    """网店查询测试"""

    async def run(self, svc):
        # 创建测试网店
        store = await svc.apply(
            member_id=MEMBER_ID_SVIP, member_level=MEMBER_LEVEL_SVIP,
            store_name="济南查询测试", city_code=CITY_CODE_JN, city_name=CITY_NAME_JN,
            province_code=PROVINCE_CODE_SD, province_name=PROVINCE_NAME_SD,
            business_license=BUSINESS_LICENSE, food_license=FOOD_LICENSE,
        )
        store_code = store["storeCode"]

        # test 11: 网店详情
        detail = await svc.get_store_detail(store_code)
        record("test_11_get_detail",
               detail["storeCode"] == store_code and detail["storeName"] == "济南查询测试",
               f"详情错误: {detail.get('storeCode')}")

        # test 12: 网店不存在 → KeyError
        try:
            await svc.get_store_detail("CS-000000-999")
            record("test_12_store_not_found", False, "应抛出 KeyError")
        except KeyError:
            record("test_12_store_not_found", True)

        # test 13: 我的网店列表
        result = await svc.list_stores(member_id=MEMBER_ID_SVIP)
        record("test_13_list_stores",
               result["count"] >= 1,
               f"列表数量错误: {result['count']}")

        # test 14: 可用城市列表(15 预定义 - 1 已占 = 14)
        cities = await svc.list_available_cities()
        record("test_14_available_cities",
               cities["count"] == 14 and cities["occupiedCount"] == 1,
               f"可用城市数量错误: {cities['count']}, occupied={cities['occupiedCount']}")

        # test 15: 已占城市不在可用列表中
        occupied_codes = [c["cityCode"] for c in cities["cities"]]
        record("test_15_occupied_excluded",
               CITY_CODE_JN not in occupied_codes,
               "已占城市仍出现在可用列表中")


class TestCityStoreAudit:
    """审核流程测试"""

    async def run(self, svc):
        # test 16: 审核通过(待审核 → 运营中)
        store = await svc.apply(
            member_id=MEMBER_ID_SVIP, member_level=MEMBER_LEVEL_SVIP,
            store_name="济南审核通过", city_code=CITY_CODE_JN, city_name=CITY_NAME_JN,
            province_code=PROVINCE_CODE_SD, province_name=PROVINCE_NAME_SD,
            business_license=BUSINESS_LICENSE, food_license=FOOD_LICENSE,
        )
        approved = await svc.audit_store(
            store_code=store["storeCode"], auditor="admin01",
            approved=True, remark="资质齐全",
        )
        record("test_16_audit_approved",
               approved["status"] == STORE_STATUS_OPERATING,
               f"审核后状态错误: {approved.get('status')}")
        record("test_17_open_date_set",
               approved.get("openDate") is not None,
               f"openDate 未设置: {approved.get('openDate')}")

        # test 18: 审核驳回(待审核 → 已取消)
        store2 = await svc.apply(
            member_id=3001, member_level=MEMBER_LEVEL_SVIP,
            store_name="青岛审核驳回", city_code=CITY_CODE_QD, city_name=CITY_NAME_QD,
            province_code=PROVINCE_CODE_SD, province_name=PROVINCE_NAME_SD,
            business_license="91370200MA00004AA", food_license="JY13702009988776",
        )
        rejected = await svc.audit_store(
            store_code=store2["storeCode"], auditor="admin01",
            approved=False, remark="资质不符",
        )
        record("test_18_audit_rejected",
               rejected["status"] == STORE_STATUS_CANCELLED,
               f"驳回后状态错误: {rejected.get('status')}")
        record("test_19_close_date_set",
               rejected.get("closeDate") is not None,
               f"closeDate 未设置: {rejected.get('closeDate')}")

        # test 20: 非待审核状态不可审核
        try:
            await svc.audit_store(
                store_code=store["storeCode"], auditor="admin01",
                approved=False, remark="重复审核",
            )
            record("test_20_reject_re_audit", False, "应抛出 ValueError")
        except ValueError:
            record("test_20_reject_re_audit", True)

        # test 21: 审核不存在的网店 → KeyError
        try:
            await svc.audit_store(
                store_code="CS-000000-999", auditor="admin01",
                approved=True,
            )
            record("test_21_audit_not_found", False, "应抛出 KeyError")
        except KeyError:
            record("test_21_audit_not_found", True)


class TestCityStoreStatusFlow:
    """状态流转测试"""

    async def _create_operating_store(self, svc, member_id=MEMBER_ID_SVIP,
                                       city_code=CITY_CODE_JN, city_name=CITY_NAME_JN,
                                       store_name="状态流转测试"):
        """辅助: 创建并审核通过的运营中网店"""
        store = await svc.apply(
            member_id=member_id, member_level=MEMBER_LEVEL_SVIP,
            store_name=store_name, city_code=city_code, city_name=city_name,
            province_code=PROVINCE_CODE_SD, province_name=PROVINCE_NAME_SD,
            business_license=BUSINESS_LICENSE, food_license=FOOD_LICENSE,
        )
        await svc.audit_store(
            store_code=store["storeCode"], auditor="admin01",
            approved=True,
        )
        return store["storeCode"]

    async def run(self, svc):
        # test 22: 运营 → 预警
        sc = await self._create_operating_store(svc, 4001, CITY_CODE_JN, CITY_NAME_JN, "运营转预警")
        result = await svc.update_status(sc, STORE_STATUS_WARNING, operator="admin01")
        record("test_22_operating_to_warning",
               result["status"] == STORE_STATUS_WARNING,
               f"状态错误: {result.get('status')}")

        # test 23: 预警 → 运营(恢复)
        result = await svc.update_status(sc, STORE_STATUS_OPERATING, operator="admin01")
        record("test_23_warning_to_operating",
               result["status"] == STORE_STATUS_OPERATING,
               f"状态错误: {result.get('status')}")

        # test 24: 运营 → 暂停
        sc2 = await self._create_operating_store(svc, 4002, CITY_CODE_QD, CITY_NAME_QD, "运营转暂停")
        result = await svc.update_status(sc2, STORE_STATUS_SUSPENDED, operator="admin01")
        record("test_24_operating_to_suspended",
               result["status"] == STORE_STATUS_SUSPENDED,
               f"状态错误: {result.get('status')}")

        # test 25: 暂停 → 运营(恢复)
        result = await svc.update_status(sc2, STORE_STATUS_OPERATING, operator="admin01")
        record("test_25_suspended_to_operating",
               result["status"] == STORE_STATUS_OPERATING,
               f"状态错误: {result.get('status')}")

        # test 26: 运营 → 取消
        sc3 = await self._create_operating_store(svc, 4003, CITY_CODE_ZB, CITY_NAME_ZB, "运营转取消")
        result = await svc.update_status(sc3, STORE_STATUS_CANCELLED, operator="admin01")
        record("test_26_operating_to_cancelled",
               result["status"] == STORE_STATUS_CANCELLED,
               f"状态错误: {result.get('status')}")
        record("test_27_close_date_set",
               result.get("closeDate") is not None,
               "closeDate 未设置")

        # test 28: 已取消为终态(不可流转)
        try:
            await svc.update_status(sc3, STORE_STATUS_OPERATING, operator="admin01")
            record("test_28_cancelled_terminal", False, "应抛出 ValueError")
        except ValueError:
            record("test_28_cancelled_terminal", True)

        # test 29: 非法流转(待审核 → 预警, 不可直接跳过审核)
        store = await svc.apply(
            member_id=4004, member_level=MEMBER_LEVEL_SVIP,
            store_name="非法流转测试", city_code="370400", city_name="枣庄市",
            province_code=PROVINCE_CODE_SD, province_name=PROVINCE_NAME_SD,
            business_license="91370400MA00005BB", food_license="JY13704005566778",
        )
        try:
            await svc.update_status(store["storeCode"], STORE_STATUS_WARNING, operator="admin01")
            record("test_29_illegal_transition", False, "应抛出 ValueError")
        except ValueError:
            record("test_29_illegal_transition", True)

        # test 30: 状态流转不存在的网店 → KeyError
        try:
            await svc.update_status("CS-000000-999", STORE_STATUS_WARNING)
            record("test_30_status_not_found", False, "应抛出 KeyError")
        except KeyError:
            record("test_30_status_not_found", True)


class TestCityStoreAssessment:
    """月度考核测试"""

    async def _create_operating_store(self, svc, member_id, city_code, city_name, store_name):
        """辅助: 创建并审核通过的运营中网店"""
        store = await svc.apply(
            member_id=member_id, member_level=MEMBER_LEVEL_SVIP,
            store_name=store_name, city_code=city_code, city_name=city_name,
            province_code=PROVINCE_CODE_SD, province_name=PROVINCE_NAME_SD,
            business_license=BUSINESS_LICENSE, food_license=FOOD_LICENSE,
        )
        await svc.audit_store(
            store_code=store["storeCode"], auditor="admin01",
            approved=True,
        )
        return store["storeCode"]

    async def run(self, svc):
        month = current_month()

        # test 31: 月销 > 9000 → 70 折(优秀)
        sc1 = await self._create_operating_store(svc, 5001, CITY_CODE_JN, CITY_NAME_JN, "优秀考核")
        # 添加订单(总额 10000 > 9000)
        await svc.add_order(
            store_code=sc1, order_no="ORD-EXCEL-001",
            product_id=PRODUCT_ID, product_name=PRODUCT_NAME,
            quantity=37, retail_price=RETAIL_PRICE, total_amount=10000.0,
            sales_channel=CHANNEL_MINIPROGRAM,
        )
        result = await svc.run_assessment(sc1, month)
        record("test_31_excellent_discount",
               result["nextMonthDiscount"] == DISCOUNT_EXCELLENT,
               f"折扣错误: {result.get('nextMonthDiscount')}")
        record("test_32_excellent_qualified",
               result["purchaseQualified"] == 1 and result["salesQualified"] == 1,
               f"达标错误: purchase={result.get('purchaseQualified')}, sales={result.get('salesQualified')}")

        # test 33: 月销 5000-9000 → 80 折(达标)
        sc2 = await self._create_operating_store(svc, 5002, CITY_CODE_QD, CITY_NAME_QD, "达标考核")
        await svc.add_order(
            store_code=sc2, order_no="ORD-QUAL-001",
            product_id=PRODUCT_ID, product_name=PRODUCT_NAME,
            quantity=22, retail_price=RETAIL_PRICE, total_amount=6000.0,
            sales_channel=CHANNEL_LIVE,
        )
        result = await svc.run_assessment(sc2, month)
        record("test_33_qualified_discount",
               result["nextMonthDiscount"] == DISCOUNT_QUALIFIED,
               f"折扣错误: {result.get('nextMonthDiscount')}")
        record("test_34_qualified_sales_only",
               result["purchaseQualified"] == 0 and result["salesQualified"] == 1,
               f"达标错误: purchase={result.get('purchaseQualified')}, sales={result.get('salesQualified')}")

        # test 35: 月销 < 5000 → 90 折(未达标)
        sc3 = await self._create_operating_store(svc, 5003, CITY_CODE_ZB, CITY_NAME_ZB, "未达标考核")
        await svc.add_order(
            store_code=sc3, order_no="ORD-UNQUAL-001",
            product_id=PRODUCT_ID, product_name=PRODUCT_NAME,
            quantity=10, retail_price=RETAIL_PRICE, total_amount=2680.0,
            sales_channel=CHANNEL_COMMUNITY,
        )
        result = await svc.run_assessment(sc3, month)
        record("test_35_unqualified_discount",
               result["nextMonthDiscount"] == DISCOUNT_UNQUALIFIED,
               f"折扣错误: {result.get('nextMonthDiscount')}")
        record("test_36_unqualified_both",
               result["purchaseQualified"] == 0 and result["salesQualified"] == 0,
               f"达标错误: purchase={result.get('purchaseQualified')}, sales={result.get('salesQualified')}")

        # test 37: 重复考核被拒
        try:
            await svc.run_assessment(sc1, month)
            record("test_37_duplicate_assessment", False, "应抛出 ValueError")
        except ValueError:
            record("test_37_duplicate_assessment", True)

        # test 38: 查询考核结果
        assessment = await svc.get_assessment(sc1, month)
        record("test_38_get_assessment",
               assessment["storeCode"] == sc1 and assessment["assessmentMonth"] == month,
               f"考核查询错误: {assessment.get('storeCode')}")

        # test 39: 考核记录不存在 → KeyError
        try:
            await svc.get_assessment(sc1, "2020-01")
            record("test_39_assessment_not_found", False, "应抛出 KeyError")
        except KeyError:
            record("test_39_assessment_not_found", True)

        # test 40: 考核记录列表
        result = await svc.list_assessments(sc1)
        record("test_40_list_assessments",
               result["count"] >= 1,
               f"考核列表错误: {result.get('count')}")

        # test 41: 考核不存在的网店 → KeyError
        try:
            await svc.run_assessment("CS-000000-999", month)
            record("test_41_assessment_store_not_found", False, "应抛出 KeyError")
        except KeyError:
            record("test_41_assessment_store_not_found", True)


class TestCityStoreConsecutiveBelow:
    """连续不达标考核测试(3 月自动取消资格)"""

    async def _create_operating_store(self, svc, member_id, city_code, city_name, store_name):
        """辅助: 创建并审核通过的运营中网店"""
        store = await svc.apply(
            member_id=member_id, member_level=MEMBER_LEVEL_SVIP,
            store_name=store_name, city_code=city_code, city_name=city_name,
            province_code=PROVINCE_CODE_SD, province_name=PROVINCE_NAME_SD,
            business_license=BUSINESS_LICENSE, food_license=FOOD_LICENSE,
        )
        await svc.audit_store(
            store_code=store["storeCode"], auditor="admin01",
            approved=True,
        )
        return store["storeCode"]

    async def run(self, svc):
        sc = await self._create_operating_store(svc, 6001, CITY_CODE_JN, CITY_NAME_JN, "连续不达标测试")

        # 第 1 月: 无订单 → 未达标(连续 1 月 → 预警)
        result1 = await svc.run_assessment(sc, "2026-01")
        record("test_42_month1_warning",
               result1["qualificationStatus"] == QUAL_STATUS_WARNING,
               f"资格状态错误: {result1.get('qualificationStatus')}")
        record("test_43_month1_consecutive",
               result1["consecutiveBelowPurchase"] == 1 and result1["consecutiveBelowSales"] == 1,
               f"连续不达标月数错误: purchase={result1.get('consecutiveBelowPurchase')}, sales={result1.get('consecutiveBelowSales')}")
        record("test_44_month1_store_warning",
               result1["storeStatus"] == STORE_STATUS_WARNING,
               f"网店状态错误: {result1.get('storeStatus')}")

        # 第 2 月: 无订单 → 未达标(连续 2 月 → 黄牌)
        result2 = await svc.run_assessment(sc, "2026-02")
        record("test_45_month2_yellow_card",
               result2["qualificationStatus"] == QUAL_STATUS_YELLOW_CARD,
               f"资格状态错误: {result2.get('qualificationStatus')}")
        record("test_46_month2_consecutive",
               result2["consecutiveBelowPurchase"] == 2 and result2["consecutiveBelowSales"] == 2,
               f"连续不达标月数错误: purchase={result2.get('consecutiveBelowPurchase')}, sales={result2.get('consecutiveBelowSales')}")

        # 第 3 月: 无订单 → 未达标(连续 3 月 → 取消资格)
        result3 = await svc.run_assessment(sc, "2026-03")
        record("test_47_month3_cancelled",
               result3["qualificationStatus"] == QUAL_STATUS_CANCELLED,
               f"资格状态错误: {result3.get('qualificationStatus')}")
        record("test_48_month3_consecutive",
               result3["consecutiveBelowPurchase"] == 3 and result3["consecutiveBelowSales"] == 3,
               f"连续不达标月数错误: purchase={result3.get('consecutiveBelowPurchase')}, sales={result3.get('consecutiveBelowSales')}")
        record("test_49_month3_store_cancelled",
               result3["storeStatus"] == STORE_STATUS_CANCELLED,
               f"网店状态错误: {result3.get('storeStatus')}")

        # test 50: 达标后连续不达标计数清零
        sc2 = await self._create_operating_store(svc, 6002, CITY_CODE_QD, CITY_NAME_QD, "达标重置测试")
        # 第 1 月不达标(无订单 → 状态变预警)
        await svc.run_assessment(sc2, "2026-01")
        # 恢复运营状态后再添加订单(预警 → 运营是合法流转)
        await svc.update_status(sc2, STORE_STATUS_OPERATING, operator="admin01")
        # 第 2 月添加达标订单
        await svc.add_order(
            store_code=sc2, order_no="ORD-RECOVER-001",
            product_id=PRODUCT_ID, product_name=PRODUCT_NAME,
            quantity=37, retail_price=RETAIL_PRICE, total_amount=10000.0,
        )
        # 修改订单的 createdAt 为 2026-02 月份(直接操作存储)
        repo = svc.repo
        orders = repo._mem_list_orders(sc2)
        for o in orders:
            o["createdAt"] = "2026-02-15T10:00:00+00:00"
        result = await svc.run_assessment(sc2, "2026-02")
        record("test_50_reset_on_qualified",
               result["consecutiveBelowPurchase"] == 0 and result["consecutiveBelowSales"] == 0,
               f"达标后计数未清零: purchase={result.get('consecutiveBelowPurchase')}, sales={result.get('consecutiveBelowSales')}")


class TestCityStoreOrders:
    """订单关联测试"""

    async def run(self, svc):
        # 创建运营中网店
        store = await svc.apply(
            member_id=MEMBER_ID_SVIP, member_level=MEMBER_LEVEL_SVIP,
            store_name="济南订单测试", city_code=CITY_CODE_JN, city_name=CITY_NAME_JN,
            province_code=PROVINCE_CODE_SD, province_name=PROVINCE_NAME_SD,
            business_license=BUSINESS_LICENSE, food_license=FOOD_LICENSE,
        )
        await svc.audit_store(
            store_code=store["storeCode"], auditor="admin01", approved=True,
        )
        sc = store["storeCode"]

        # test 51: 关联订单成功
        order = await svc.add_order(
            store_code=sc, order_no="ORD-TEST-001",
            product_id=PRODUCT_ID, product_name=PRODUCT_NAME,
            quantity=10, retail_price=RETAIL_PRICE, total_amount=2680.0,
            customer_phone="138****0001", delivery_city_code=CITY_CODE_JN,
            sales_channel=CHANNEL_MINIPROGRAM,
        )
        record("test_51_add_order_success",
               order["orderNo"] == "ORD-TEST-001" and order["totalAmount"] == 2680.0,
               f"订单错误: {order.get('orderNo')}")

        # test 52: 订单列表查询
        result = await svc.list_orders(sc)
        record("test_52_list_orders",
               result["count"] >= 1 and result["totalAmount"] >= 2680.0,
               f"订单列表错误: count={result.get('count')}, total={result.get('totalAmount')}")

        # test 53: 按月份筛选订单
        month = current_month()
        result = await svc.list_orders(sc, month=month)
        record("test_53_filter_by_month",
               result["count"] >= 1,
               f"按月筛选错误: count={result.get('count')}")

        # test 54: 不存在的月份筛选(返回空)
        result = await svc.list_orders(sc, month="2020-01")
        record("test_54_empty_month",
               result["count"] == 0,
               f"空月份筛选错误: count={result.get('count')}")

        # test 55: 网店非运营状态不可关联订单
        store2 = await svc.apply(
            member_id=7001, member_level=MEMBER_LEVEL_SVIP,
            store_name="青岛待审核订单", city_code=CITY_CODE_QD, city_name=CITY_NAME_QD,
            province_code=PROVINCE_CODE_SD, province_name=PROVINCE_NAME_SD,
            business_license="91370200MA00006CC", food_license="JY13702003344556",
        )
        try:
            await svc.add_order(
                store_code=store2["storeCode"], order_no="ORD-FAIL-001",
                product_id=PRODUCT_ID, product_name=PRODUCT_NAME,
                quantity=1, retail_price=RETAIL_PRICE, total_amount=268.0,
            )
            record("test_55_non_operating_blocked", False, "应抛出 ValueError")
        except ValueError:
            record("test_55_non_operating_blocked", True)

        # test 56: 网店不存在 → KeyError
        try:
            await svc.add_order(
                store_code="CS-000000-999", order_no="ORD-NOT-FOUND",
                product_id=PRODUCT_ID, product_name=PRODUCT_NAME,
                quantity=1, retail_price=RETAIL_PRICE, total_amount=268.0,
            )
            record("test_56_order_store_not_found", False, "应抛出 KeyError")
        except KeyError:
            record("test_56_order_store_not_found", True)


class TestCityStoreAdmin:
    """管理端测试"""

    async def run(self, svc):
        # 创建多个网店(不同状态)
        # 1. 待审核
        await svc.apply(
            member_id=8001, member_level=MEMBER_LEVEL_SVIP,
            store_name="济南待审核1", city_code=CITY_CODE_JN, city_name=CITY_NAME_JN,
            province_code=PROVINCE_CODE_SD, province_name=PROVINCE_NAME_SD,
            business_license=BUSINESS_LICENSE, food_license=FOOD_LICENSE,
        )
        # 2. 待审核
        await svc.apply(
            member_id=8002, member_level=MEMBER_LEVEL_SVIP,
            store_name="青岛待审核2", city_code=CITY_CODE_QD, city_name=CITY_NAME_QD,
            province_code=PROVINCE_CODE_SD, province_name=PROVINCE_NAME_SD,
            business_license="91370200MA00007DD", food_license="JY13702002233445",
        )
        # 3. 已审核通过
        store3 = await svc.apply(
            member_id=8003, member_level=MEMBER_LEVEL_SVIP,
            store_name="淄博运营中", city_code=CITY_CODE_ZB, city_name=CITY_NAME_ZB,
            province_code=PROVINCE_CODE_SD, province_name=PROVINCE_NAME_SD,
            business_license="91370300MA00008EE", food_license="JY13703001122334",
        )
        await svc.audit_store(store3["storeCode"], "admin01", approved=True)

        # test 57: 待审核网店列表
        result = await svc.list_pending_stores()
        record("test_57_list_pending",
               result["count"] >= 2,
               f"待审核数量错误: {result['count']}")

        # test 58: 网店统计
        stats = await svc.get_stats()
        record("test_58_stats_fields",
               all(k in stats for k in [
                   "totalStores", "pendingStores", "operatingStores",
                   "warningStores", "suspendedStores", "cancelledStores", "occupiedCities",
               ]),
               f"统计字段缺失: {stats}")
        record("test_59_stats_values",
               stats["totalStores"] >= 3 and stats["pendingStores"] >= 2 and stats["operatingStores"] >= 1,
               f"统计值错误: {stats}")


# ============================================================
# 测试运行
# ============================================================

async def main():
    """运行所有测试"""
    print("=" * 60)
    print("市级网店模块端到端测试")
    print("=" * 60)

    test_classes = [
        ("开店申请", TestCityStoreApply),
        ("网店查询", TestCityStoreQuery),
        ("审核流程", TestCityStoreAudit),
        ("状态流转", TestCityStoreStatusFlow),
        ("月度考核", TestCityStoreAssessment),
        ("连续不达标", TestCityStoreConsecutiveBelow),
        ("订单关联", TestCityStoreOrders),
        ("管理端", TestCityStoreAdmin),
    ]

    for name, cls in test_classes:
        reset_store()
        svc = CityStoreService()
        print(f"\n[{name}]")
        instance = cls()
        await instance.run(svc)

    print("\n" + "=" * 60)
    print(f"通过: {PASS}  失败: {FAIL}  总计: {PASS + FAIL}")
    print("=" * 60)
    for r in RESULTS:
        print(r)
    print()
    if FAIL > 0:
        print(f"❌ {FAIL} 项测试失败")
        sys.exit(1)
    else:
        print("✅ 全部测试通过!")


if __name__ == "__main__":
    asyncio.run(main())
