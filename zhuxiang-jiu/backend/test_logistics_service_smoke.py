"""物流接口管理 Service 冒烟测试

覆盖:
    - 物流下单(参数校验 + 幂等 + 运费计算)
    - 状态流转(状态机校验 + 自动轨迹)
    - 物流轨迹查询与回调
    - 月结对账(完全对平 + 差异场景 + 状态机)
"""

import asyncio
import os
import sys
import unittest

os.environ["LOCK_MODE"] = "asyncio"
os.environ["STORE_MODE"] = "asyncio"

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from repositories.store import _mock_store  # noqa: E402
from services.logistics_service import (  # noqa: E402
    LogisticsService,
    _calc_insured_fee, _calc_package_fee, _calc_sf_base_fee,
    _calc_lll_base_fee, _calc_total_fee, _gen_settle_no,
    SUPPORTED_CARRIERS, SUPPORTED_ORDER_TYPES, SUPPORTED_SETTLE_MODES,
    DIFF_TYPE_AMOUNT_MISMATCH, DIFF_TYPE_ORDER_MISSING, DIFF_TYPE_EXTRA_ORDER,
    HANDLE_SUGGEST_SUPPLEMENT, HANDLE_SUGGEST_REFUND, HANDLE_SUGGEST_IGNORE,
)
from repositories.logistics_repository import (  # noqa: E402
    ORDER_STATUS_PENDING, ORDER_STATUS_BOOKED, ORDER_STATUS_PICKED,
    ORDER_STATUS_TRANSPORTING, ORDER_STATUS_DELIVERING, ORDER_STATUS_SIGNED,
    ORDER_STATUS_FAILED, ORDER_STATUS_RETURNED,
    SETTLE_STATUS_PENDING, SETTLE_STATUS_RECONCILING, SETTLE_STATUS_CONFIRMED,
    SETTLE_STATUS_PAID, SETTLE_STATUS_DIFF, SETTLE_STATUS_INVESTIGATING,
    SETTLE_STATUS_RESOLVED,
    CARRIER_SF, CARRIER_JD, CARRIER_LLL,
)


def _reset_store():
    for k in list(_mock_store.keys()):
        _mock_store.pop(k, None)


def _sender():
    return {"name": "张三", "phone": "13800138000", "address": "北京市朝阳区XX路"}


def _receiver():
    return {
        "name": "李四", "phone": "13900139000",
        "address": "上海市浦东新区YY路",
        "province": "上海", "city": "上海",
    }


class TestFeeCalculation(unittest.TestCase):
    """运费计算函数测试"""

    def test_01_insured_fee(self):
        """保价费 = 保价金额 × 0.5%"""
        self.assertEqual(_calc_insured_fee(1000.0), 5.0)
        self.assertEqual(_calc_insured_fee(0.0), 0.0)

    def test_02_package_fee(self):
        """包装费 = 2 元/件"""
        self.assertEqual(_calc_package_fee(1), 2.0)
        self.assertEqual(_calc_package_fee(5), 10.0)

    def test_03_sf_base_fee_standard(self):
        """顺丰标准件运费(1kg 内 18 元)"""
        self.assertEqual(_calc_sf_base_fee("standard", 0.5), 18.0)
        self.assertEqual(_calc_sf_base_fee("standard", 3.0), 22.0)

    def test_04_sf_base_fee_express(self):
        """顺丰特快运费(1kg 内 25 元)"""
        self.assertEqual(_calc_sf_base_fee("express", 0.5), 25.0)

    def test_05_sf_base_fee_overweight(self):
        """顺丰超重(续重 2 元/kg)"""
        fee = _calc_sf_base_fee("standard", 25.0)
        # 20kg 内 58 元 + 5kg × 2 元 = 68 元
        self.assertEqual(fee, 68.0)

    def test_06_lll_base_fee(self):
        """货拉拉同城运费(1kg 内 35 元)"""
        self.assertEqual(_calc_lll_base_fee(0.5), 35.0)
        self.assertEqual(_calc_lll_base_fee(3.0), 55.0)

    def test_07_total_fee_with_discount(self):
        """总运费 = (基础+保价+包装+附加) × 折扣"""
        total = _calc_total_fee(50.0, 5.0, 2.0, 0.0, 0.8)
        self.assertEqual(total, 45.6)  # 57 × 0.8

    def test_08_settle_no_generation(self):
        """结算单号生成"""
        self.assertEqual(_gen_settle_no("2026-08", CARRIER_SF), "SETTLE202608SF")


class TestCreateOrder(unittest.IsolatedAsyncioTestCase):
    """物流下单测试"""

    async def asyncSetUp(self):
        _reset_store()
        self.svc = LogisticsService()

    async def test_01_create_order_success(self):
        """下单成功(含运费计算)"""
        order = await self.svc.create_order(
            order_id="ORD001", order_type="retail",
            carrier=CARRIER_SF, service_type="standard",
            sender=_sender(), receiver=_receiver(),
            weight=2.5, piece_count=1, insured_value=1000.0,
        )
        self.assertEqual(order["status"], ORDER_STATUS_PENDING)
        self.assertEqual(order["carrier"], CARRIER_SF)
        # 基础运费 22 元(3kg 内标准) + 保价 5 元 + 包装 2 元 = 29 元
        self.assertEqual(order["baseFee"], 22.0)
        self.assertEqual(order["insuredFee"], 5.0)
        self.assertEqual(order["packageFee"], 2.0)
        self.assertEqual(order["totalFee"], 29.0)
        # 手机号脱敏
        self.assertIn("****", order["senderPhone"])
        self.assertIn("****", order["receiverPhone"])

    async def test_02_create_order_invalid_type(self):
        """非法订单类型"""
        with self.assertRaises(ValueError):
            await self.svc.create_order(
                order_id="ORD001", order_type="invalid",
                carrier=CARRIER_SF, service_type="standard",
                sender=_sender(), receiver=_receiver(), weight=2.5,
            )

    async def test_03_create_order_invalid_carrier(self):
        """非法物流商"""
        with self.assertRaises(ValueError):
            await self.svc.create_order(
                order_id="ORD001", order_type="retail",
                carrier="XX", service_type="standard",
                sender=_sender(), receiver=_receiver(), weight=2.5,
            )

    async def test_04_create_order_invalid_weight(self):
        """非法重量"""
        with self.assertRaises(ValueError):
            await self.svc.create_order(
                order_id="ORD001", order_type="retail",
                carrier=CARRIER_SF, service_type="standard",
                sender=_sender(), receiver=_receiver(), weight=0,
            )

    async def test_05_create_order_invalid_discount(self):
        """非法折扣"""
        with self.assertRaises(ValueError):
            await self.svc.create_order(
                order_id="ORD001", order_type="retail",
                carrier=CARRIER_SF, service_type="standard",
                sender=_sender(), receiver=_receiver(), weight=2.5,
                discount=1.5,
            )

    async def test_06_create_order_missing_sender(self):
        """寄件人信息不完整"""
        sender = {"name": "", "phone": "13800138000", "address": "北京"}
        with self.assertRaises(ValueError):
            await self.svc.create_order(
                order_id="ORD001", order_type="retail",
                carrier=CARRIER_SF, service_type="standard",
                sender=sender, receiver=_receiver(), weight=2.5,
            )

    async def test_07_create_order_idempotent(self):
        """同订单重复下单 → ValueError"""
        await self.svc.create_order(
            order_id="ORD001", order_type="retail",
            carrier=CARRIER_SF, service_type="standard",
            sender=_sender(), receiver=_receiver(), weight=2.5,
        )
        with self.assertRaises(ValueError):
            await self.svc.create_order(
                order_id="ORD001", order_type="retail",
                carrier=CARRIER_SF, service_type="standard",
                sender=_sender(), receiver=_receiver(), weight=2.5,
            )

    async def test_08_create_order_after_signed(self):
        """已签收订单可重新下单"""
        order = await self.svc.create_order(
            order_id="ORD001", order_type="retail",
            carrier=CARRIER_SF, service_type="standard",
            sender=_sender(), receiver=_receiver(), weight=2.5,
        )
        # 手动设置为已签收
        await self.svc.repo.update_order_fields(order["waybillNo"], {"status": ORDER_STATUS_SIGNED})
        # 可再次下单
        new_order = await self.svc.create_order(
            order_id="ORD001", order_type="retail",
            carrier=CARRIER_SF, service_type="standard",
            sender=_sender(), receiver=_receiver(), weight=2.5,
        )
        self.assertNotEqual(order["waybillNo"], new_order["waybillNo"])

    async def test_09_create_order_lll(self):
        """货拉拉下单(同城运费)"""
        order = await self.svc.create_order(
            order_id="ORD001", order_type="groupbuy",
            carrier=CARRIER_LLL, service_type="standard",
            sender=_sender(), receiver=_receiver(),
            weight=2.5, piece_count=1,
        )
        self.assertEqual(order["baseFee"], 55.0)  # 3kg 内 55 元


class TestStatusFlow(unittest.IsolatedAsyncioTestCase):
    """状态流转测试"""

    async def asyncSetUp(self):
        _reset_store()
        self.svc = LogisticsService()
        self.order = await self.svc.create_order(
            order_id="ORD001", order_type="retail",
            carrier=CARRIER_SF, service_type="standard",
            sender=_sender(), receiver=_receiver(), weight=2.5,
        )
        self.waybill_no = self.order["waybillNo"]

    async def test_01_pending_to_booked(self):
        """pending → booked"""
        order = await self.svc.update_status(self.waybill_no, ORDER_STATUS_BOOKED)
        self.assertEqual(order["status"], ORDER_STATUS_BOOKED)

    async def test_02_full_flow_to_signed(self):
        """完整流转: pending → booked → picked → transporting → delivering → signed"""
        for new_status in [
            ORDER_STATUS_BOOKED, ORDER_STATUS_PICKED, ORDER_STATUS_TRANSPORTING,
            ORDER_STATUS_DELIVERING, ORDER_STATUS_SIGNED,
        ]:
            order = await self.svc.update_status(self.waybill_no, new_status)
            self.assertEqual(order["status"], new_status)

        # 签收状态应有签收时间
        self.assertIn("signedTime", order)
        # 应有 5 条轨迹
        tracks = await self.svc.list_tracks(self.waybill_no)
        self.assertEqual(len(tracks), 5)

    async def test_03_invalid_flow(self):
        """非法流转: pending → transporting(跳过 booked)"""
        with self.assertRaises(ValueError):
            await self.svc.update_status(self.waybill_no, ORDER_STATUS_TRANSPORTING)

    async def test_04_signed_terminal(self):
        """signed 为终态(不可再流转)"""
        for new_status in [
            ORDER_STATUS_BOOKED, ORDER_STATUS_PICKED, ORDER_STATUS_TRANSPORTING,
            ORDER_STATUS_DELIVERING, ORDER_STATUS_SIGNED,
        ]:
            await self.svc.update_status(self.waybill_no, new_status)

        with self.assertRaises(ValueError):
            await self.svc.update_status(self.waybill_no, ORDER_STATUS_BOOKED)

    async def test_05_failed_to_returned(self):
        """failed → returned(关闭失败运单)"""
        await self.svc.update_status(self.waybill_no, ORDER_STATUS_BOOKED)
        await self.svc.update_status(self.waybill_no, ORDER_STATUS_FAILED)
        order = await self.svc.close_failed_order(self.waybill_no, reason="地址错误")
        self.assertEqual(order["status"], ORDER_STATUS_RETURNED)

    async def test_06_close_non_failed(self):
        """非 failed 状态不可关闭"""
        with self.assertRaises(ValueError):
            await self.svc.close_failed_order(self.waybill_no)

    async def test_07_order_not_found(self):
        """更新不存在的运单 → KeyError"""
        with self.assertRaises(KeyError):
            await self.svc.update_status("NOT_EXIST", ORDER_STATUS_BOOKED)

    async def test_08_auto_track_added(self):
        """状态流转自动添加轨迹"""
        await self.svc.update_status(self.waybill_no, ORDER_STATUS_BOOKED, track_desc="已下单")
        tracks = await self.svc.list_tracks(self.waybill_no)
        self.assertEqual(len(tracks), 1)
        self.assertEqual(tracks[0]["description"], "已下单")

    async def test_09_sign_info(self):
        """签收信息补充"""
        for new_status in [
            ORDER_STATUS_BOOKED, ORDER_STATUS_PICKED, ORDER_STATUS_TRANSPORTING,
            ORDER_STATUS_DELIVERING,
        ]:
            await self.svc.update_status(self.waybill_no, new_status)

        order = await self.svc.update_status(
            self.waybill_no, ORDER_STATUS_SIGNED,
            sign_info={
                "signerName": "王五",
                "signType": "agent",
                "signPhoto": "https://example.com/sign.jpg",
                "signLocation": "31.2304,121.4737",
            },
        )
        self.assertEqual(order["signerName"], "王五")
        self.assertEqual(order["signType"], "agent")
        self.assertEqual(order["signPhoto"], "https://example.com/sign.jpg")


class TestTrackCallback(unittest.IsolatedAsyncioTestCase):
    """物流轨迹回调测试"""

    async def asyncSetUp(self):
        _reset_store()
        self.svc = LogisticsService()
        self.order = await self.svc.create_order(
            order_id="ORD001", order_type="retail",
            carrier=CARRIER_SF, service_type="standard",
            sender=_sender(), receiver=_receiver(), weight=2.5,
        )
        self.waybill_no = self.order["waybillNo"]

    async def test_01_callback_adds_track(self):
        """轨迹回调添加轨迹"""
        track = await self.svc.add_track_callback(
            self.waybill_no, "ACCEPT", ORDER_STATUS_BOOKED, "已下单", "北京",
        )
        self.assertEqual(track["unifiedStatus"], ORDER_STATUS_BOOKED)

    async def test_02_callback_updates_order_status(self):
        """轨迹回调自动更新订单状态"""
        await self.svc.add_track_callback(
            self.waybill_no, "ACCEPT", ORDER_STATUS_BOOKED, "已下单", "北京",
        )
        order = await self.svc.get_order(self.waybill_no)
        self.assertEqual(order["status"], ORDER_STATUS_BOOKED)

    async def test_03_callback_invalid_status_ignored(self):
        """非法状态流转(轨迹仍记录, 状态不更新)"""
        # 订单处于 pending, 直接回调 transporting(跳过 booked)应忽略状态更新
        await self.svc.add_track_callback(
            self.waybill_no, "TRANSPORT", ORDER_STATUS_TRANSPORTING, "运输中", "北京",
        )
        # 轨迹已添加
        tracks = await self.svc.list_tracks(self.waybill_no)
        self.assertEqual(len(tracks), 1)
        # 订单状态未变(仍是 pending)
        order = await self.svc.get_order(self.waybill_no)
        self.assertEqual(order["status"], ORDER_STATUS_PENDING)

    async def test_04_callback_not_found(self):
        """回调不存在的运单 → KeyError"""
        with self.assertRaises(KeyError):
            await self.svc.add_track_callback(
                "NOT_EXIST", "ACCEPT", ORDER_STATUS_BOOKED, "已下单", "北京",
            )


class TestSettlement(unittest.IsolatedAsyncioTestCase):
    """月结对账测试"""

    async def asyncSetUp(self):
        _reset_store()
        self.svc = LogisticsService()
        # 创建几笔订单
        for i in range(3):
            await self.svc.create_order(
                order_id=f"ORD{i:03d}", order_type="retail",
                carrier=CARRIER_SF, service_type="standard",
                sender=_sender(), receiver=_receiver(), weight=2.5,
            )

    async def test_01_start_settlement_no_diff(self):
        """启动对账(无差异 → reconciling)"""
        settle = await self.svc.start_settlement("2026-08", CARRIER_SF)
        self.assertEqual(settle["status"], SETTLE_STATUS_RECONCILING)
        self.assertEqual(settle["totalOrders"], 3)
        self.assertEqual(settle["diffCount"], 0)

    async def test_02_start_settlement_with_channel_orders_matched(self):
        """对账(平台与物流商数据一致 → 无差异)"""
        # 模拟物流商返回的对账明细(与平台一致)
        platform_orders = await self.svc.list_orders(carrier=CARRIER_SF)
        channel_orders = [
            {"waybillNo": o["waybillNo"], "totalFee": o["totalFee"]}
            for o in platform_orders
        ]
        settle = await self.svc.start_settlement("2026-08", CARRIER_SF, channel_orders)
        self.assertEqual(settle["status"], SETTLE_STATUS_RECONCILING)
        self.assertEqual(settle["diffCount"], 0)

    async def test_03_start_settlement_with_amount_mismatch(self):
        """对账(金额不符 → diff)"""
        platform_orders = await self.svc.list_orders(carrier=CARRIER_SF)
        # 物流商返回的金额与平台不一致
        channel_orders = [
            {"waybillNo": o["waybillNo"], "totalFee": o["totalFee"] + 10}
            for o in platform_orders
        ]
        settle = await self.svc.start_settlement("2026-08", CARRIER_SF, channel_orders)
        self.assertEqual(settle["status"], SETTLE_STATUS_DIFF)
        self.assertEqual(settle["diffCount"], 3)
        # 差异类型为金额不符
        self.assertEqual(settle["diffDetails"][0]["type"], DIFF_TYPE_AMOUNT_MISMATCH)

    async def test_04_start_settlement_with_missing_order(self):
        """对账(单据缺失 → diff)"""
        platform_orders = await self.svc.list_orders(carrier=CARRIER_SF)
        # 物流商少返回 1 笔
        channel_orders = [
            {"waybillNo": o["waybillNo"], "totalFee": o["totalFee"]}
            for o in platform_orders[:2]
        ]
        settle = await self.svc.start_settlement("2026-08", CARRIER_SF, channel_orders)
        self.assertEqual(settle["status"], SETTLE_STATUS_DIFF)
        self.assertEqual(settle["diffCount"], 1)
        self.assertEqual(settle["diffDetails"][0]["type"], DIFF_TYPE_ORDER_MISSING)

    async def test_05_start_settlement_with_extra_order(self):
        """对账(多余单据 → diff)"""
        platform_orders = await self.svc.list_orders(carrier=CARRIER_SF)
        channel_orders = [
            {"waybillNo": o["waybillNo"], "totalFee": o["totalFee"]}
            for o in platform_orders
        ]
        # 添加一笔物流商有但平台没有的
        channel_orders.append({"waybillNo": "SF_EXTRA", "totalFee": 50.0})
        settle = await self.svc.start_settlement("2026-08", CARRIER_SF, channel_orders)
        self.assertEqual(settle["status"], SETTLE_STATUS_DIFF)
        self.assertEqual(settle["diffCount"], 1)
        self.assertEqual(settle["diffDetails"][0]["type"], DIFF_TYPE_EXTRA_ORDER)

    async def test_06_settlement_duplicate(self):
        """重复对账 → ValueError"""
        await self.svc.start_settlement("2026-08", CARRIER_SF)
        with self.assertRaises(ValueError):
            await self.svc.start_settlement("2026-08", CARRIER_SF)

    async def test_07_settlement_invalid_carrier(self):
        """非法物流商"""
        with self.assertRaises(ValueError):
            await self.svc.start_settlement("2026-08", "XX")

    async def test_08_get_settlement(self):
        """查询结算单"""
        await self.svc.start_settlement("2026-08", CARRIER_SF)
        settle = await self.svc.get_settlement("SETTLE202608SF")
        self.assertEqual(settle["carrier"], CARRIER_SF)

    async def test_09_get_settlement_not_found(self):
        """查询不存在的结算单"""
        with self.assertRaises(KeyError):
            await self.svc.get_settlement("NOT_EXIST")

    async def test_10_list_settlements(self):
        """结算单列表"""
        await self.svc.start_settlement("2026-08", CARRIER_SF)
        items = await self.svc.list_settlements()
        self.assertEqual(len(items), 1)

    async def test_11_investigate_diff(self):
        """介入调查(diff → investigating)"""
        platform_orders = await self.svc.list_orders(carrier=CARRIER_SF)
        channel_orders = [
            {"waybillNo": o["waybillNo"], "totalFee": o["totalFee"] + 10}
            for o in platform_orders
        ]
        await self.svc.start_settlement("2026-08", CARRIER_SF, channel_orders)
        settle = await self.svc.investigate_diff("SETTLE202608SF")
        self.assertEqual(settle["status"], SETTLE_STATUS_INVESTIGATING)

    async def test_12_investigate_non_diff(self):
        """非 diff 状态不可介入"""
        await self.svc.start_settlement("2026-08", CARRIER_SF)
        with self.assertRaises(ValueError):
            await self.svc.investigate_diff("SETTLE202608SF")

    async def test_13_resolve_settlement(self):
        """处理完毕(investigating → resolved)"""
        platform_orders = await self.svc.list_orders(carrier=CARRIER_SF)
        channel_orders = [
            {"waybillNo": o["waybillNo"], "totalFee": o["totalFee"] + 10}
            for o in platform_orders
        ]
        await self.svc.start_settlement("2026-08", CARRIER_SF, channel_orders)
        await self.svc.investigate_diff("SETTLE202608SF")
        settle = await self.svc.resolve_settlement("SETTLE202608SF", resolution="已补单")
        self.assertEqual(settle["status"], SETTLE_STATUS_RESOLVED)

    async def test_14_confirm_settlement(self):
        """确认结算单(reconciling → confirmed)"""
        await self.svc.start_settlement("2026-08", CARRIER_SF)
        settle = await self.svc.confirm_settlement("SETTLE202608SF")
        self.assertEqual(settle["status"], SETTLE_STATUS_CONFIRMED)
        self.assertIn("confirmTime", settle)

    async def test_15_confirm_after_resolve(self):
        """差异处理后确认(resolved → confirmed)"""
        platform_orders = await self.svc.list_orders(carrier=CARRIER_SF)
        channel_orders = [
            {"waybillNo": o["waybillNo"], "totalFee": o["totalFee"] + 10}
            for o in platform_orders
        ]
        await self.svc.start_settlement("2026-08", CARRIER_SF, channel_orders)
        await self.svc.investigate_diff("SETTLE202608SF")
        await self.svc.resolve_settlement("SETTLE202608SF")
        settle = await self.svc.confirm_settlement("SETTLE202608SF")
        self.assertEqual(settle["status"], SETTLE_STATUS_CONFIRMED)

    async def test_16_pay_settlement(self):
        """付款(confirmed → paid)"""
        await self.svc.start_settlement("2026-08", CARRIER_SF)
        await self.svc.confirm_settlement("SETTLE202608SF")
        settle = await self.svc.pay_settlement("SETTLE202608SF")
        self.assertEqual(settle["status"], SETTLE_STATUS_PAID)
        self.assertIn("payTime", settle)

    async def test_17_pay_non_confirmed(self):
        """非 confirmed 状态不可付款"""
        await self.svc.start_settlement("2026-08", CARRIER_SF)
        with self.assertRaises(ValueError):
            await self.svc.pay_settlement("SETTLE202608SF")

    async def test_18_paid_terminal(self):
        """paid 为终态(不可再付款)"""
        await self.svc.start_settlement("2026-08", CARRIER_SF)
        await self.svc.confirm_settlement("SETTLE202608SF")
        await self.svc.pay_settlement("SETTLE202608SF")
        # 再次付款 → 状态机不允许
        with self.assertRaises(ValueError):
            await self.svc.confirm_settlement("SETTLE202608SF")

    async def test_19_list_pending_settlements(self):
        """待处理结算单列表"""
        # 创建 1 个 diff 状态结算单
        platform_orders = await self.svc.list_orders(carrier=CARRIER_SF)
        channel_orders = [
            {"waybillNo": o["waybillNo"], "totalFee": o["totalFee"] + 10}
            for o in platform_orders
        ]
        await self.svc.start_settlement("2026-08", CARRIER_SF, channel_orders)
        pending = await self.svc.list_pending_settlements()
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0]["status"], SETTLE_STATUS_DIFF)


class TestConstants(unittest.TestCase):
    """常量校验"""

    def test_01_supported_carriers(self):
        """支持的物流商集合"""
        self.assertIn(CARRIER_SF, SUPPORTED_CARRIERS)
        self.assertIn(CARRIER_JD, SUPPORTED_CARRIERS)
        self.assertIn(CARRIER_LLL, SUPPORTED_CARRIERS)

    def test_02_supported_order_types(self):
        """支持的订单类型"""
        self.assertIn("retail", SUPPORTED_ORDER_TYPES)
        self.assertIn("groupbuy", SUPPORTED_ORDER_TYPES)
        self.assertIn("return", SUPPORTED_ORDER_TYPES)

    def test_03_supported_settle_modes(self):
        """支持的结算模式"""
        self.assertIn("monthly", SUPPORTED_SETTLE_MODES)
        self.assertIn("cash", SUPPORTED_SETTLE_MODES)


if __name__ == "__main__":
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()

    test_classes = [
        TestFeeCalculation,
        TestCreateOrder,
        TestStatusFlow,
        TestTrackCallback,
        TestSettlement,
        TestConstants,
    ]

    for test_class in test_classes:
        tests = loader.loadTestsFromTestCase(test_class)
        suite.addTests(tests)

    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)

    print(f"\n通过: {result.testsRun - len(result.failures) - len(result.errors)}"
          f"  失败: {len(result.failures)}  总计: {result.testsRun}")
    if result.failures or result.errors:
        print("存在失败/错误!")
        sys.exit(1)
    else:
        print("全部测试通过!")
