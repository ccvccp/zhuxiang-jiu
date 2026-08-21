"""物流接口管理 Repository 冒烟测试

覆盖 P0 三张表的全部方法,验证:
    - CRUD 基本功能
    - 索引维护(orderId/waybillNo/status/pending 集合)
    - 状态机常量与流转规则
    - 锁机制(acquire/release)
    - 双模式存储一致性(内存模式)
"""

import asyncio
import os
import sys
import unittest

# 确保使用内存模式
os.environ["LOCK_MODE"] = "asyncio"
os.environ["STORE_MODE"] = "asyncio"

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from repositories.store import _mock_store  # noqa: E402
from repositories.logistics_repository import (  # noqa: E402
    LogisticsRepository,
    ORDER_STATUS_PENDING, ORDER_STATUS_BOOKED, ORDER_STATUS_PICKED,
    ORDER_STATUS_TRANSPORTING, ORDER_STATUS_DELIVERING, ORDER_STATUS_SIGNED,
    ORDER_STATUS_FAILED, ORDER_STATUS_RETURNED,
    ORDER_STATUS_FLOW,
    SETTLE_STATUS_PENDING, SETTLE_STATUS_RECONCILING, SETTLE_STATUS_CONFIRMED,
    SETTLE_STATUS_PAID, SETTLE_STATUS_DIFF, SETTLE_STATUS_INVESTIGATING,
    SETTLE_STATUS_RESOLVED, SETTLE_STATUS_FLOW, SETTLE_PENDING_STATUSES,
    CARRIER_SF, CARRIER_JD,
)


def _reset_store():
    """清空内存存储"""
    for k in list(_mock_store.keys()):
        _mock_store.pop(k, None)


class TestOrderCRUD(unittest.IsolatedAsyncioTestCase):
    """物流订单 CRUD 测试"""

    async def asyncSetUp(self):
        _reset_store()
        self.repo = LogisticsRepository()

    async def _create_order(self, waybill_no="SF1234567890", order_id="ORD001"):
        return await self.repo.save_order({
            "waybillNo": waybill_no,
            "orderId": order_id,
            "orderType": "retail",
            "carrier": CARRIER_SF,
            "carrierName": "顺丰速运",
            "serviceType": "顺丰特快",
            "senderName": "张三", "senderPhone": "13800138000",
            "senderAddress": "北京市朝阳区",
            "receiverName": "李四", "receiverPhone": "13900139000",
            "receiverAddress": "上海市浦东新区",
            "province": "上海", "city": "上海",
            "weight": 2.5, "volume": 0.02, "pieceCount": 1,
            "insuredValue": 1000.0,
            "baseFee": 25.0, "insuredFee": 5.0, "packageFee": 2.0,
            "extraFee": 0.0, "discount": 1.0, "totalFee": 32.0,
            "settleMode": "monthly",
            "status": ORDER_STATUS_PENDING,
        })

    async def test_01_save_order_success(self):
        """保存物流订单成功"""
        order = await self._create_order()
        self.assertEqual(order["waybillNo"], "SF1234567890")
        self.assertEqual(order["status"], ORDER_STATUS_PENDING)
        self.assertIn("createdAt", order)
        self.assertIn("updatedAt", order)

    async def test_02_get_order_success(self):
        """查询物流订单成功"""
        await self._create_order()
        order = await self.repo.get_order("SF1234567890")
        self.assertIsNotNone(order)
        self.assertEqual(order["orderId"], "ORD001")

    async def test_03_get_order_not_found(self):
        """查询不存在的订单"""
        order = await self.repo.get_order("NOT_EXIST")
        self.assertIsNone(order)

    async def test_04_find_by_order_success(self):
        """按订单号查询物流单"""
        await self._create_order()
        order = await self.repo.find_by_order("ORD001")
        self.assertIsNotNone(order)
        self.assertEqual(order["waybillNo"], "SF1234567890")

    async def test_05_find_by_order_not_found(self):
        """按订单号查询不存在"""
        order = await self.repo.find_by_order("NOT_EXIST")
        self.assertIsNone(order)

    async def test_06_list_orders_all(self):
        """查询全部订单"""
        await self._create_order("SF001", "ORD001")
        await self._create_order("SF002", "ORD002")
        items = await self.repo.list_orders()
        self.assertEqual(len(items), 2)

    async def test_07_list_orders_by_carrier(self):
        """按物流商筛选"""
        await self._create_order("SF001", "ORD001")
        await self.repo.save_order({
            "waybillNo": "JD001", "orderId": "ORD002", "orderType": "retail",
            "carrier": CARRIER_JD, "carrierName": "京东物流",
            "status": ORDER_STATUS_PENDING,
        })
        sf_items = await self.repo.list_orders(carrier=CARRIER_SF)
        self.assertEqual(len(sf_items), 1)
        self.assertEqual(sf_items[0]["carrier"], CARRIER_SF)

    async def test_08_list_orders_by_status(self):
        """按状态筛选"""
        await self._create_order("SF001", "ORD001")
        await self._create_order("SF002", "ORD002")
        # 更新其中一个为已下单
        await self.repo.update_order_fields("SF001", {"status": ORDER_STATUS_BOOKED})
        pending_items = await self.repo.list_orders(status=ORDER_STATUS_PENDING)
        self.assertEqual(len(pending_items), 1)
        self.assertEqual(pending_items[0]["waybillNo"], "SF002")

    async def test_09_update_order_fields_success(self):
        """更新物流订单字段"""
        await self._create_order()
        result = await self.repo.update_order_fields("SF1234567890", {
            "status": ORDER_STATUS_BOOKED,
            "labelUrl": "https://example.com/label.pdf",
        })
        self.assertEqual(result["status"], ORDER_STATUS_BOOKED)
        self.assertEqual(result["labelUrl"], "https://example.com/label.pdf")

    async def test_10_update_order_not_found(self):
        """更新不存在的订单 → KeyError"""
        with self.assertRaises(KeyError):
            await self.repo.update_order_fields("NOT_EXIST", {"status": ORDER_STATUS_BOOKED})


class TestTrackManagement(unittest.IsolatedAsyncioTestCase):
    """物流轨迹管理测试"""

    async def asyncSetUp(self):
        _reset_store()
        self.repo = LogisticsRepository()

    async def test_01_add_track_success(self):
        """添加轨迹成功"""
        track = await self.repo.add_track({
            "waybillNo": "SF001",
            "carrier": CARRIER_SF,
            "trackStatus": "ACCEPT",
            "unifiedStatus": ORDER_STATUS_BOOKED,
            "description": "已下单",
            "location": "北京",
            "operator": "system",
            "trackTime": "2026-08-22 10:00:00",
        })
        self.assertIn("trackId", track)
        self.assertTrue(track["trackId"].startswith("TRACK"))
        self.assertEqual(track["waybillNo"], "SF001")

    async def test_02_list_tracks_by_waybill(self):
        """按运单号查询轨迹列表(按时间倒序)"""
        # 添加多条轨迹
        for i, (status, desc) in enumerate([
            (ORDER_STATUS_BOOKED, "已下单"),
            (ORDER_STATUS_PICKED, "已揽收"),
            (ORDER_STATUS_TRANSPORTING, "运输中"),
        ]):
            await self.repo.add_track({
                "waybillNo": "SF001",
                "carrier": CARRIER_SF,
                "trackStatus": status.upper(),
                "unifiedStatus": status,
                "description": desc,
                "location": "北京",
                "trackTime": f"2026-08-22 1{i}:00:00",
            })
        tracks = await self.repo.list_tracks("SF001")
        self.assertEqual(len(tracks), 3)
        # 最新在前(LPUSH 效果)
        self.assertEqual(tracks[0]["unifiedStatus"], ORDER_STATUS_TRANSPORTING)
        self.assertEqual(tracks[-1]["unifiedStatus"], ORDER_STATUS_BOOKED)

    async def test_03_list_tracks_empty(self):
        """查询无轨迹的运单"""
        tracks = await self.repo.list_tracks("NOT_EXIST")
        self.assertEqual(tracks, [])

    async def test_04_multiple_waybills_isolated(self):
        """不同运单的轨迹隔离"""
        for waybill_no in ["SF001", "SF002"]:
            await self.repo.add_track({
                "waybillNo": waybill_no,
                "carrier": CARRIER_SF,
                "trackStatus": "ACCEPT",
                "unifiedStatus": ORDER_STATUS_BOOKED,
                "description": "已下单",
                "location": "北京",
                "trackTime": "2026-08-22 10:00:00",
            })
        self.assertEqual(len(await self.repo.list_tracks("SF001")), 1)
        self.assertEqual(len(await self.repo.list_tracks("SF002")), 1)


class TestSettlementCRUD(unittest.IsolatedAsyncioTestCase):
    """物流结算 CRUD 测试"""

    async def asyncSetUp(self):
        _reset_store()
        self.repo = LogisticsRepository()

    async def _create_settle(self, settle_no="SETTLE202608SF"):
        return await self.repo.create_settlement({
            "settleNo": settle_no,
            "carrier": CARRIER_SF,
            "period": "2026-08",
            "totalOrders": 10,
            "totalWeight": 25.5,
            "baseFeeTotal": 250.0,
            "insuredTotal": 50.0,
            "packageTotal": 20.0,
            "extraTotal": 0.0,
            "subtotal": 320.0,
            "discountAmount": 32.0,
            "payableAmount": 288.0,
            "status": SETTLE_STATUS_PENDING,
        })

    async def test_01_create_settlement_success(self):
        """创建结算单成功"""
        settle = await self._create_settle()
        self.assertEqual(settle["settleNo"], "SETTLE202608SF")
        self.assertEqual(settle["status"], SETTLE_STATUS_PENDING)
        self.assertEqual(settle["diffCount"], 0)
        self.assertEqual(settle["diffDetails"], [])

    async def test_02_create_settlement_duplicate(self):
        """重复创建结算单 → ValueError"""
        await self._create_settle()
        with self.assertRaises(ValueError):
            await self._create_settle()

    async def test_03_get_settlement_success(self):
        """查询结算单"""
        await self._create_settle()
        settle = await self.repo.get_settlement("SETTLE202608SF")
        self.assertIsNotNone(settle)
        self.assertEqual(settle["carrier"], CARRIER_SF)

    async def test_04_get_settlement_not_found(self):
        """查询不存在的结算单"""
        result = await self.repo.get_settlement("NOT_EXIST")
        self.assertIsNone(result)

    async def test_05_update_settlement_status(self):
        """更新结算单状态(自动维护 pending 集合)

        pending → reconciling: 移出 pending 集合(对账中不算待对账)
        reconciling → diff: 重新加入 pending 集合(有差异需处理)
        diff → resolved → confirmed: 最终移出 pending 集合
        """
        await self._create_settle()
        # pending(初始在集合) → reconciling(移出 pending 集合)
        await self.repo.update_settlement_fields("SETTLE202608SF", {
            "status": SETTLE_STATUS_RECONCILING,
        })
        pending = await self.repo.list_pending_settlements()
        self.assertEqual(len(pending), 0)
        # reconciling → diff(重新加入 pending 集合)
        await self.repo.update_settlement_fields("SETTLE202608SF", {
            "status": SETTLE_STATUS_DIFF,
        })
        pending = await self.repo.list_pending_settlements()
        self.assertEqual(len(pending), 1)
        # diff → resolved → confirmed(最终移出 pending 集合)
        await self.repo.update_settlement_fields("SETTLE202608SF", {
            "status": SETTLE_STATUS_RESOLVED,
        })
        await self.repo.update_settlement_fields("SETTLE202608SF", {
            "status": SETTLE_STATUS_CONFIRMED,
        })
        pending = await self.repo.list_pending_settlements()
        self.assertEqual(len(pending), 0)

    async def test_06_update_settlement_not_found(self):
        """更新不存在的结算单 → KeyError"""
        with self.assertRaises(KeyError):
            await self.repo.update_settlement_fields("NOT_EXIST", {"status": SETTLE_STATUS_CONFIRMED})

    async def test_07_list_settlements_all(self):
        """查询全部结算单"""
        await self._create_settle("SETTLE202608SF")
        await self._create_settle("SETTLE202608JD")
        # 第二个改为京东
        await self.repo.update_settlement_fields("SETTLE202608JD", {"carrier": CARRIER_JD})
        items = await self.repo.list_settlements()
        self.assertEqual(len(items), 2)

    async def test_08_list_settlements_by_carrier(self):
        """按物流商筛选"""
        await self._create_settle("SETTLE202608SF")
        await self._create_settle("SETTLE202608JD")
        await self.repo.update_settlement_fields("SETTLE202608JD", {"carrier": CARRIER_JD})
        sf_items = await self.repo.list_settlements(carrier=CARRIER_SF)
        self.assertEqual(len(sf_items), 1)
        self.assertEqual(sf_items[0]["carrier"], CARRIER_SF)

    async def test_09_list_settlements_by_period(self):
        """按账期筛选"""
        await self._create_settle("SETTLE202608SF")
        await self._create_settle("SETTLE202609SF")
        await self.repo.update_settlement_fields("SETTLE202609SF", {"period": "2026-09"})
        aug_items = await self.repo.list_settlements(period="2026-08")
        self.assertEqual(len(aug_items), 1)

    async def test_10_list_pending_settlements_empty(self):
        """空待对账列表"""
        items = await self.repo.list_pending_settlements()
        self.assertEqual(items, [])

    async def test_11_diff_status_in_pending(self):
        """diff 状态在 pending 集合"""
        await self._create_settle()
        await self.repo.update_settlement_fields("SETTLE202608SF", {
            "status": SETTLE_STATUS_RECONCILING,
        })
        await self.repo.update_settlement_fields("SETTLE202608SF", {
            "status": SETTLE_STATUS_DIFF,
        })
        pending = await self.repo.list_pending_settlements()
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0]["status"], SETTLE_STATUS_DIFF)


class TestSettleLock(unittest.IsolatedAsyncioTestCase):
    """对账锁测试"""

    async def asyncSetUp(self):
        _reset_store()
        self.repo = LogisticsRepository()

    async def test_01_acquire_lock_success(self):
        """获取对账锁成功"""
        ok = await self.repo.acquire_settle_lock("2026-08", CARRIER_SF)
        self.assertTrue(ok)

    async def test_02_acquire_lock_duplicate(self):
        """重复获取对账锁失败"""
        await self.repo.acquire_settle_lock("2026-08", CARRIER_SF)
        ok = await self.repo.acquire_settle_lock("2026-08", CARRIER_SF)
        self.assertFalse(ok)

    async def test_03_acquire_lock_different_carrier(self):
        """不同物流商可同时获取锁"""
        await self.repo.acquire_settle_lock("2026-08", CARRIER_SF)
        ok = await self.repo.acquire_settle_lock("2026-08", CARRIER_JD)
        self.assertTrue(ok)

    async def test_04_release_lock_success(self):
        """释放对账锁后可再次获取"""
        await self.repo.acquire_settle_lock("2026-08", CARRIER_SF)
        await self.repo.release_settle_lock("2026-08", CARRIER_SF)
        ok = await self.repo.acquire_settle_lock("2026-08", CARRIER_SF)
        self.assertTrue(ok)

    async def test_05_release_lock_idempotent(self):
        """重复释放锁无副作用"""
        await self.repo.acquire_settle_lock("2026-08", CARRIER_SF)
        await self.repo.release_settle_lock("2026-08", CARRIER_SF)
        # 再次释放不报错
        await self.repo.release_settle_lock("2026-08", CARRIER_SF)
        # 仍可获取
        ok = await self.repo.acquire_settle_lock("2026-08", CARRIER_SF)
        self.assertTrue(ok)


class TestStatusConstants(unittest.TestCase):
    """状态常量与状态机校验"""

    def test_01_order_status_flow_pending(self):
        """pending 可流转到 booked/failed"""
        self.assertIn(ORDER_STATUS_BOOKED, ORDER_STATUS_FLOW[ORDER_STATUS_PENDING])
        self.assertIn(ORDER_STATUS_FAILED, ORDER_STATUS_FLOW[ORDER_STATUS_PENDING])

    def test_02_order_status_flow_signed_terminal(self):
        """signed 为终态(不可流转)"""
        self.assertEqual(ORDER_STATUS_FLOW[ORDER_STATUS_SIGNED], set())

    def test_03_order_status_flow_returned_terminal(self):
        """returned 为终态"""
        self.assertEqual(ORDER_STATUS_FLOW[ORDER_STATUS_RETURNED], set())

    def test_04_order_status_flow_failed_can_retry(self):
        """failed 可重投或退回"""
        self.assertIn(ORDER_STATUS_DELIVERING, ORDER_STATUS_FLOW[ORDER_STATUS_FAILED])
        self.assertIn(ORDER_STATUS_RETURNED, ORDER_STATUS_FLOW[ORDER_STATUS_FAILED])

    def test_05_settle_status_flow_pending_to_reconciling(self):
        """pending → reconciling"""
        self.assertIn(SETTLE_STATUS_RECONCILING, SETTLE_STATUS_FLOW[SETTLE_STATUS_PENDING])

    def test_06_settle_status_flow_reconciling_to_confirmed_or_diff(self):
        """reconciling → confirmed 或 diff"""
        self.assertIn(SETTLE_STATUS_CONFIRMED, SETTLE_STATUS_FLOW[SETTLE_STATUS_RECONCILING])
        self.assertIn(SETTLE_STATUS_DIFF, SETTLE_STATUS_FLOW[SETTLE_STATUS_RECONCILING])

    def test_07_settle_status_flow_paid_terminal(self):
        """paid 为终态"""
        self.assertEqual(SETTLE_STATUS_FLOW[SETTLE_STATUS_PAID], set())

    def test_08_settle_pending_statuses_includes_diff(self):
        """pending 集合包含 diff/investigating"""
        self.assertIn(SETTLE_STATUS_DIFF, SETTLE_PENDING_STATUSES)
        self.assertIn(SETTLE_STATUS_INVESTIGATING, SETTLE_PENDING_STATUSES)
        self.assertIn(SETTLE_STATUS_PENDING, SETTLE_PENDING_STATUSES)

    def test_09_settle_resolved_to_confirmed(self):
        """resolved → confirmed(处理完毕后确认)"""
        self.assertIn(SETTLE_STATUS_CONFIRMED, SETTLE_STATUS_FLOW[SETTLE_STATUS_RESOLVED])


if __name__ == "__main__":
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()

    test_classes = [
        TestOrderCRUD,
        TestTrackManagement,
        TestSettlementCRUD,
        TestSettleLock,
        TestStatusConstants,
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
