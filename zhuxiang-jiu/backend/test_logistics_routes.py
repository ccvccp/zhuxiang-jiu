"""物流接口管理模块端到端测试

测试方式: 直接调用 Service 层(asyncio 内存模式), 覆盖 18 个 HTTP 接口
对应的全部业务方法, 与 test_payment_routes.py 风格一致。

覆盖场景:
    - 物流下单: 成功/参数校验/幂等/已签收可重新下单/运费计算
    - 状态流转: 完整流转/非法流转/终态校验/失败重试
    - 物流轨迹: 自动轨迹/轨迹回调/回调状态更新/回调非法状态忽略
    - 月结对账: 完全对平/差异场景/状态机/重复对账/付款闭环
    - 异常分支: 资源不存在 / 状态非法 / 参数非法
"""

import asyncio
import os
import sys
import unittest

# 确保使用内存模式
os.environ["LOCK_MODE"] = "asyncio"
os.environ["STORE_MODE"] = "asyncio"

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from repositories.store import _mock_store
from repositories.logistics_repository import (
    ORDER_STATUS_PENDING, ORDER_STATUS_BOOKED, ORDER_STATUS_PICKED,
    ORDER_STATUS_TRANSPORTING, ORDER_STATUS_DELIVERING, ORDER_STATUS_SIGNED,
    ORDER_STATUS_FAILED, ORDER_STATUS_RETURNED,
    SETTLE_STATUS_PENDING, SETTLE_STATUS_RECONCILING, SETTLE_STATUS_CONFIRMED,
    SETTLE_STATUS_PAID, SETTLE_STATUS_DIFF, SETTLE_STATUS_INVESTIGATING,
    SETTLE_STATUS_RESOLVED,
    CARRIER_SF, CARRIER_JD, CARRIER_LLL,
)
from services.logistics_service import (
    LogisticsService,
    _calc_insured_fee, _calc_package_fee, _calc_sf_base_fee,
    _calc_total_fee, _gen_settle_no,
    DIFF_TYPE_AMOUNT_MISMATCH, DIFF_TYPE_ORDER_MISSING, DIFF_TYPE_EXTRA_ORDER,
)


def _reset_store():
    """清空内存存储, 保证测试隔离"""
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


async def _create_order(svc, order_id="ORD001", carrier=CARRIER_SF, weight=2.5):
    """辅助: 创建一笔物流订单"""
    return await svc.create_order(
        order_id=order_id, order_type="retail",
        carrier=carrier, service_type="standard",
        sender=_sender(), receiver=_receiver(),
        weight=weight, piece_count=1, insured_value=1000.0,
    )


async def _flow_to_signed(svc, waybill_no):
    """辅助: 将运单流转到已签收"""
    for status in [
        ORDER_STATUS_BOOKED, ORDER_STATUS_PICKED, ORDER_STATUS_TRANSPORTING,
        ORDER_STATUS_DELIVERING, ORDER_STATUS_SIGNED,
    ]:
        await svc.update_status(waybill_no, status)


# ============================================================
# 1. 物流下单(覆盖 4 个接口对应业务方法)
# ============================================================

class TestCreateOrder(unittest.IsolatedAsyncioTestCase):
    """物流下单测试"""

    async def asyncSetUp(self):
        _reset_store()
        self.svc = LogisticsService()

    async def test_01_create_order_success(self):
        """下单成功(含运费计算)"""
        order = await _create_order(self.svc)
        self.assertEqual(order["status"], ORDER_STATUS_PENDING)
        self.assertEqual(order["carrier"], CARRIER_SF)
        # 基础运费 22 元(3kg 内标准) + 保价 5 元 + 包装 2 元 = 29 元
        self.assertEqual(order["baseFee"], 22.0)
        self.assertEqual(order["insuredFee"], 5.0)
        self.assertEqual(order["packageFee"], 2.0)
        self.assertEqual(order["totalFee"], 29.0)

    async def test_02_phone_masked(self):
        """手机号脱敏"""
        order = await _create_order(self.svc)
        self.assertIn("****", order["senderPhone"])
        self.assertIn("****", order["receiverPhone"])
        # 保留前 3 后 4
        self.assertTrue(order["senderPhone"].startswith("138"))
        self.assertTrue(order["senderPhone"].endswith("8000"))

    async def test_03_invalid_order_type(self):
        """非法订单类型"""
        with self.assertRaises(ValueError):
            await self.svc.create_order(
                order_id="ORD001", order_type="invalid",
                carrier=CARRIER_SF, service_type="standard",
                sender=_sender(), receiver=_receiver(), weight=2.5,
            )

    async def test_04_invalid_carrier(self):
        """非法物流商"""
        with self.assertRaises(ValueError):
            await self.svc.create_order(
                order_id="ORD001", order_type="retail",
                carrier="XX", service_type="standard",
                sender=_sender(), receiver=_receiver(), weight=2.5,
            )

    async def test_05_invalid_weight(self):
        """非法重量"""
        with self.assertRaises(ValueError):
            await self.svc.create_order(
                order_id="ORD001", order_type="retail",
                carrier=CARRIER_SF, service_type="standard",
                sender=_sender(), receiver=_receiver(), weight=0,
            )

    async def test_06_invalid_discount(self):
        """非法折扣"""
        with self.assertRaises(ValueError):
            await self.svc.create_order(
                order_id="ORD001", order_type="retail",
                carrier=CARRIER_SF, service_type="standard",
                sender=_sender(), receiver=_receiver(), weight=2.5,
                discount=1.5,
            )

    async def test_07_missing_sender_info(self):
        """寄件人信息不完整"""
        sender = {"name": "", "phone": "13800138000", "address": "北京"}
        with self.assertRaises(ValueError):
            await self.svc.create_order(
                order_id="ORD001", order_type="retail",
                carrier=CARRIER_SF, service_type="standard",
                sender=sender, receiver=_receiver(), weight=2.5,
            )

    async def test_08_missing_receiver_info(self):
        """收件人信息不完整"""
        receiver = {"name": "", "phone": "13900139000", "address": "上海"}
        with self.assertRaises(ValueError):
            await self.svc.create_order(
                order_id="ORD001", order_type="retail",
                carrier=CARRIER_SF, service_type="standard",
                sender=_sender(), receiver=receiver, weight=2.5,
            )

    async def test_09_idempotent_create(self):
        """同订单重复下单 → ValueError"""
        await _create_order(self.svc)
        with self.assertRaises(ValueError):
            await _create_order(self.svc)

    async def test_10_recreate_after_signed(self):
        """已签收订单可重新下单"""
        order = await _create_order(self.svc)
        await _flow_to_signed(self.svc, order["waybillNo"])
        # 可再次下单
        new_order = await _create_order(self.svc)
        self.assertNotEqual(order["waybillNo"], new_order["waybillNo"])

    async def test_11_recreate_after_returned(self):
        """已退回订单可重新下单"""
        order = await _create_order(self.svc)
        await self.svc.update_status(order["waybillNo"], ORDER_STATUS_BOOKED)
        await self.svc.update_status(order["waybillNo"], ORDER_STATUS_FAILED)
        await self.svc.close_failed_order(order["waybillNo"], "地址错误")
        # 可再次下单
        new_order = await _create_order(self.svc)
        self.assertNotEqual(order["waybillNo"], new_order["waybillNo"])

    async def test_12_create_with_discount(self):
        """带折扣下单"""
        order = await self.svc.create_order(
            order_id="ORD001", order_type="retail",
            carrier=CARRIER_SF, service_type="standard",
            sender=_sender(), receiver=_receiver(),
            weight=2.5, piece_count=1, insured_value=1000.0,
            discount=0.8,
        )
        # 29 × 0.8 = 23.2
        self.assertEqual(order["totalFee"], 23.2)

    async def test_13_create_lll_order(self):
        """货拉拉下单(同城运费)"""
        order = await self.svc.create_order(
            order_id="ORD001", order_type="groupbuy",
            carrier=CARRIER_LLL, service_type="standard",
            sender=_sender(), receiver=_receiver(),
            weight=2.5, piece_count=1,
        )
        self.assertEqual(order["baseFee"], 55.0)  # 3kg 内 55 元

    async def test_14_get_order_by_order_id(self):
        """按订单号查询物流单"""
        await _create_order(self.svc)
        order = await self.svc.get_order_by_order_id("ORD001")
        self.assertIsNotNone(order)
        self.assertEqual(order["orderId"], "ORD001")

    async def test_15_get_order_by_order_id_not_found(self):
        """按订单号查询不存在"""
        order = await self.svc.get_order_by_order_id("NOT_EXIST")
        self.assertIsNone(order)

    async def test_16_list_orders_filter(self):
        """订单列表筛选"""
        await _create_order(self.svc, "ORD001", CARRIER_SF)
        await _create_order(self.svc, "ORD002", CARRIER_LLL)
        sf_items = await self.svc.list_orders(carrier=CARRIER_SF)
        self.assertEqual(len(sf_items), 1)
        lll_items = await self.svc.list_orders(carrier=CARRIER_LLL)
        self.assertEqual(len(lll_items), 1)


# ============================================================
# 2. 状态流转(覆盖 3 个接口对应业务方法)
# ============================================================

class TestStatusFlow(unittest.IsolatedAsyncioTestCase):
    """状态流转测试"""

    async def asyncSetUp(self):
        _reset_store()
        self.svc = LogisticsService()
        self.order = await _create_order(self.svc)
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

    async def test_03_invalid_flow_skip_booked(self):
        """非法流转: pending → transporting(跳过 booked)"""
        with self.assertRaises(ValueError):
            await self.svc.update_status(self.waybill_no, ORDER_STATUS_TRANSPORTING)

    async def test_04_invalid_flow_signed_to_booked(self):
        """非法流转: signed → booked(终态不可流转)"""
        await _flow_to_signed(self.svc, self.waybill_no)
        with self.assertRaises(ValueError):
            await self.svc.update_status(self.waybill_no, ORDER_STATUS_BOOKED)

    async def test_05_signed_has_sign_info(self):
        """签收信息补充"""
        for status in [
            ORDER_STATUS_BOOKED, ORDER_STATUS_PICKED, ORDER_STATUS_TRANSPORTING,
            ORDER_STATUS_DELIVERING,
        ]:
            await self.svc.update_status(self.waybill_no, status)

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
        self.assertEqual(order["signLocation"], "31.2304,121.4737")

    async def test_06_auto_track_on_status_change(self):
        """状态流转自动添加轨迹"""
        await self.svc.update_status(self.waybill_no, ORDER_STATUS_BOOKED, track_desc="已下单")
        tracks = await self.svc.list_tracks(self.waybill_no)
        self.assertEqual(len(tracks), 1)
        self.assertEqual(tracks[0]["description"], "已下单")
        self.assertEqual(tracks[0]["unifiedStatus"], ORDER_STATUS_BOOKED)

    async def test_07_multiple_tracks_ordered(self):
        """多条轨迹按时间倒序(最新在前)"""
        for status in [
            ORDER_STATUS_BOOKED, ORDER_STATUS_PICKED, ORDER_STATUS_TRANSPORTING,
        ]:
            await self.svc.update_status(self.waybill_no, status)

        tracks = await self.svc.list_tracks(self.waybill_no)
        self.assertEqual(len(tracks), 3)
        # 最新在前
        self.assertEqual(tracks[0]["unifiedStatus"], ORDER_STATUS_TRANSPORTING)
        self.assertEqual(tracks[-1]["unifiedStatus"], ORDER_STATUS_BOOKED)

    async def test_08_failed_can_retry_or_return(self):
        """failed 可重投或退回"""
        await self.svc.update_status(self.waybill_no, ORDER_STATUS_BOOKED)
        await self.svc.update_status(self.waybill_no, ORDER_STATUS_FAILED)
        # failed → returned
        order = await self.svc.close_failed_order(self.waybill_no, "地址错误")
        self.assertEqual(order["status"], ORDER_STATUS_RETURNED)

    async def test_09_close_non_failed_rejected(self):
        """非 failed 状态不可关闭"""
        with self.assertRaises(ValueError):
            await self.svc.close_failed_order(self.waybill_no)

    async def test_10_update_status_not_found(self):
        """更新不存在的运单 → KeyError"""
        with self.assertRaises(KeyError):
            await self.svc.update_status("NOT_EXIST", ORDER_STATUS_BOOKED)

    async def test_11_list_tracks_not_found(self):
        """查询不存在的运单轨迹 → KeyError"""
        with self.assertRaises(KeyError):
            await self.svc.list_tracks("NOT_EXIST")

    async def test_12_get_order_not_found(self):
        """查询不存在的运单 → KeyError"""
        with self.assertRaises(KeyError):
            await self.svc.get_order("NOT_EXIST")


# ============================================================
# 3. 物流轨迹回调
# ============================================================

class TestTrackCallback(unittest.IsolatedAsyncioTestCase):
    """物流轨迹回调测试"""

    async def asyncSetUp(self):
        _reset_store()
        self.svc = LogisticsService()
        self.order = await _create_order(self.svc)
        self.waybill_no = self.order["waybillNo"]

    async def test_01_callback_adds_track(self):
        """轨迹回调添加轨迹"""
        track = await self.svc.add_track_callback(
            self.waybill_no, "ACCEPT", ORDER_STATUS_BOOKED, "已下单", "北京",
        )
        self.assertEqual(track["unifiedStatus"], ORDER_STATUS_BOOKED)
        self.assertEqual(track["description"], "已下单")

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

    async def test_05_callback_full_flow(self):
        """轨迹回调完整流转"""
        for track_status, unified_status, desc in [
            ("ACCEPT", ORDER_STATUS_BOOKED, "已下单"),
            ("PICK", ORDER_STATUS_PICKED, "已揽收"),
            ("TRANSPORT", ORDER_STATUS_TRANSPORTING, "运输中"),
            ("DELIVER", ORDER_STATUS_DELIVERING, "派送中"),
            ("SIGN", ORDER_STATUS_SIGNED, "已签收"),
        ]:
            await self.svc.add_track_callback(
                self.waybill_no, track_status, unified_status, desc, "上海",
            )

        order = await self.svc.get_order(self.waybill_no)
        self.assertEqual(order["status"], ORDER_STATUS_SIGNED)
        tracks = await self.svc.list_tracks(self.waybill_no)
        self.assertEqual(len(tracks), 5)


# ============================================================
# 3.1 物流轨迹回调 - 异常状态分支补充
# ============================================================

class TestTrackCallbackEdgeCases(unittest.IsolatedAsyncioTestCase):
    """物流轨迹回调异常状态分支测试

    覆盖 add_track_callback 的三类异常分支:
        A. update_status 抛 ValueError(状态机非法流转) → 仍记录轨迹(logger.warning 路径)
        B. unified_status 不在 valid_statuses → 直接仅记录轨迹
        C. 参数边界(track_time 自动填充/operator 透传/多次累积)
    """

    async def asyncSetUp(self):
        _reset_store()
        self.svc = LogisticsService()
        self.order = await _create_order(self.svc)
        self.waybill_no = self.order["waybillNo"]

    async def test_06_callback_duplicate_status_logs_only(self):
        """相同状态重复回调(booked → booked)触发 ValueError, 轨迹仍记录"""
        # 先流转到 booked
        await self.svc.update_status(self.waybill_no, ORDER_STATUS_BOOKED)
        # 再次回调 booked(状态机不允许 booked → booked)
        track = await self.svc.add_track_callback(
            self.waybill_no, "ACCEPT", ORDER_STATUS_BOOKED, "重复下单回调", "北京",
        )
        # 轨迹已添加
        self.assertEqual(track["unifiedStatus"], ORDER_STATUS_BOOKED)
        self.assertEqual(track["description"], "重复下单回调")
        # 订单状态未变(仍是 booked)
        order = await self.svc.get_order(self.waybill_no)
        self.assertEqual(order["status"], ORDER_STATUS_BOOKED)

    async def test_07_callback_after_signed_terminal(self):
        """signed 终态后回调 picked(非法流转, 轨迹记录但状态不变)"""
        await _flow_to_signed(self.svc, self.waybill_no)
        # signed → picked 非法(终态不可流转)
        track = await self.svc.add_track_callback(
            self.waybill_no, "PICK", ORDER_STATUS_PICKED, "终态后回调", "上海",
        )
        self.assertEqual(track["unifiedStatus"], ORDER_STATUS_PICKED)
        # 订单状态仍是 signed
        order = await self.svc.get_order(self.waybill_no)
        self.assertEqual(order["status"], ORDER_STATUS_SIGNED)

    async def test_08_callback_after_signed_same_status(self):
        """signed 终态后重复回调 signed(重复签收, 轨迹记录但状态不变)"""
        await _flow_to_signed(self.svc, self.waybill_no)
        track = await self.svc.add_track_callback(
            self.waybill_no, "SIGN", ORDER_STATUS_SIGNED, "重复签收回调", "上海",
        )
        self.assertEqual(track["unifiedStatus"], ORDER_STATUS_SIGNED)
        order = await self.svc.get_order(self.waybill_no)
        self.assertEqual(order["status"], ORDER_STATUS_SIGNED)

    async def test_09_callback_after_returned_terminal(self):
        """returned 终态后回调 booked(非法流转, 轨迹记录但状态不变)"""
        # 流转到 returned: pending → booked → failed → returned
        await self.svc.update_status(self.waybill_no, ORDER_STATUS_BOOKED)
        await self.svc.update_status(self.waybill_no, ORDER_STATUS_FAILED)
        await self.svc.close_failed_order(self.waybill_no, "地址错误")
        # returned 终态后回调 booked(非法)
        track = await self.svc.add_track_callback(
            self.waybill_no, "ACCEPT", ORDER_STATUS_BOOKED, "退回后回调", "北京",
        )
        self.assertEqual(track["unifiedStatus"], ORDER_STATUS_BOOKED)
        order = await self.svc.get_order(self.waybill_no)
        self.assertEqual(order["status"], ORDER_STATUS_RETURNED)

    async def test_10_callback_failed_from_pending(self):
        """pending → failed(合法流转, 状态更新 + 轨迹记录)"""
        track = await self.svc.add_track_callback(
            self.waybill_no, "FAIL", ORDER_STATUS_FAILED, "下单失败", "北京",
        )
        self.assertEqual(track["unifiedStatus"], ORDER_STATUS_FAILED)
        order = await self.svc.get_order(self.waybill_no)
        self.assertEqual(order["status"], ORDER_STATUS_FAILED)

    async def test_11_callback_failed_from_booked(self):
        """booked → failed(合法流转, 状态更新 + 轨迹记录)"""
        await self.svc.update_status(self.waybill_no, ORDER_STATUS_BOOKED)
        track = await self.svc.add_track_callback(
            self.waybill_no, "FAIL", ORDER_STATUS_FAILED, "揽收失败", "北京",
        )
        self.assertEqual(track["unifiedStatus"], ORDER_STATUS_FAILED)
        order = await self.svc.get_order(self.waybill_no)
        self.assertEqual(order["status"], ORDER_STATUS_FAILED)

    async def test_12_callback_unknown_unified_status(self):
        """未知 unified_status(不在 valid_statuses)仅记录轨迹, 状态不变"""
        track = await self.svc.add_track_callback(
            self.waybill_no, "UNKNOWN", "unknown_status", "未知状态回调", "北京",
        )
        self.assertEqual(track["unifiedStatus"], "unknown_status")
        self.assertEqual(track["trackStatus"], "UNKNOWN")
        # 订单状态未变(仍是 pending)
        order = await self.svc.get_order(self.waybill_no)
        self.assertEqual(order["status"], ORDER_STATUS_PENDING)

    async def test_13_callback_pending_status_only_logs(self):
        """pending 状态(不在 valid_statuses)仅记录轨迹, 状态不变"""
        track = await self.svc.add_track_callback(
            self.waybill_no, "INIT", ORDER_STATUS_PENDING, "初始化回调", "北京",
        )
        self.assertEqual(track["unifiedStatus"], ORDER_STATUS_PENDING)
        # 订单状态仍是 pending(未变化)
        order = await self.svc.get_order(self.waybill_no)
        self.assertEqual(order["status"], ORDER_STATUS_PENDING)

    async def test_14_callback_auto_fill_track_time(self):
        """未指定 track_time 时自动填充当前时间"""
        track = await self.svc.add_track_callback(
            self.waybill_no, "ACCEPT", ORDER_STATUS_BOOKED, "已下单", "北京",
        )
        # track_time 应为非空字符串(由 ts() 生成)
        self.assertTrue(track["trackTime"])
        self.assertIsInstance(track["trackTime"], str)

    async def test_15_callback_custom_operator(self):
        """自定义 operator 字段透传"""
        track = await self.svc.add_track_callback(
            self.waybill_no, "ACCEPT", ORDER_STATUS_BOOKED, "已下单", "北京",
            operator="SF-API",
        )
        self.assertEqual(track["operator"], "SF-API")

    async def test_16_callback_custom_track_time(self):
        """自定义 track_time 字段透传"""
        custom_time = "2026-08-22 10:00:00"
        track = await self.svc.add_track_callback(
            self.waybill_no, "INIT", ORDER_STATUS_PENDING, "初始化", "北京",
            track_time=custom_time,
        )
        self.assertEqual(track["trackTime"], custom_time)

    async def test_17_callback_multiple_accumulate(self):
        """多次回调累积轨迹(数量正确, 最新在前)"""
        for i in range(3):
            await self.svc.add_track_callback(
                self.waybill_no, f"STAGE{i}", ORDER_STATUS_PENDING,
                f"轨迹{i}", "北京",
            )
        tracks = await self.svc.list_tracks(self.waybill_no)
        self.assertEqual(len(tracks), 3)
        # 最新在前(倒序)
        self.assertEqual(tracks[0]["description"], "轨迹2")
        self.assertEqual(tracks[-1]["description"], "轨迹0")

    async def test_18_callback_returned_track_fields_complete(self):
        """回调返回轨迹字段完整性(仅记录轨迹分支)"""
        track = await self.svc.add_track_callback(
            self.waybill_no, "INIT", ORDER_STATUS_PENDING, "初始化", "北京",
            operator="carrier",
        )
        # 校验轨迹字段完整性
        for field in ["waybillNo", "trackStatus", "unifiedStatus",
                      "description", "location", "operator", "trackTime"]:
            self.assertIn(field, track, f"轨迹缺少字段: {field}")
        self.assertEqual(track["waybillNo"], self.waybill_no)
        self.assertEqual(track["location"], "北京")

    async def test_19_callback_returned_from_failed(self):
        """failed → returned(合法流转, 通过回调触发)"""
        # 准备: pending → booked → failed
        await self.svc.update_status(self.waybill_no, ORDER_STATUS_BOOKED)
        await self.svc.update_status(self.waybill_no, ORDER_STATUS_FAILED)
        # 通过回调触发 failed → returned
        track = await self.svc.add_track_callback(
            self.waybill_no, "RETURN", ORDER_STATUS_RETURNED, "退回仓库", "北京",
        )
        self.assertEqual(track["unifiedStatus"], ORDER_STATUS_RETURNED)
        # 订单状态已更新为 returned
        order = await self.svc.get_order(self.waybill_no)
        self.assertEqual(order["status"], ORDER_STATUS_RETURNED)

    async def test_20_callback_retry_from_failed(self):
        """failed → delivering(合法重投, 通过回调触发)"""
        # 准备: pending → booked → failed
        await self.svc.update_status(self.waybill_no, ORDER_STATUS_BOOKED)
        await self.svc.update_status(self.waybill_no, ORDER_STATUS_FAILED)
        # 通过回调触发 failed → delivering(重投)
        track = await self.svc.add_track_callback(
            self.waybill_no, "REDELIVER", ORDER_STATUS_DELIVERING, "重新派送", "上海",
        )
        self.assertEqual(track["unifiedStatus"], ORDER_STATUS_DELIVERING)
        # 订单状态已更新为 delivering
        order = await self.svc.get_order(self.waybill_no)
        self.assertEqual(order["status"], ORDER_STATUS_DELIVERING)


# ============================================================
# 4. 月结对账(覆盖 6 个接口对应业务方法)
# ============================================================

class TestSettlement(unittest.IsolatedAsyncioTestCase):
    """月结对账测试"""

    async def asyncSetUp(self):
        _reset_store()
        self.svc = LogisticsService()
        # 创建几笔订单
        for i in range(3):
            await _create_order(self.svc, f"ORD{i:03d}")

    async def test_01_start_settlement_no_diff(self):
        """启动对账(无差异 → reconciling)"""
        settle = await self.svc.start_settlement("2026-08", CARRIER_SF)
        self.assertEqual(settle["status"], SETTLE_STATUS_RECONCILING)
        self.assertEqual(settle["totalOrders"], 3)
        self.assertEqual(settle["diffCount"], 0)
        self.assertEqual(settle["diffDetails"], [])

    async def test_02_start_settlement_channel_matched(self):
        """对账(平台与物流商数据一致 → 无差异)"""
        platform_orders = await self.svc.list_orders(carrier=CARRIER_SF)
        channel_orders = [
            {"waybillNo": o["waybillNo"], "totalFee": o["totalFee"]}
            for o in platform_orders
        ]
        settle = await self.svc.start_settlement("2026-08", CARRIER_SF, channel_orders)
        self.assertEqual(settle["status"], SETTLE_STATUS_RECONCILING)
        self.assertEqual(settle["diffCount"], 0)

    async def test_03_start_settlement_amount_mismatch(self):
        """对账(金额不符 → diff)"""
        platform_orders = await self.svc.list_orders(carrier=CARRIER_SF)
        channel_orders = [
            {"waybillNo": o["waybillNo"], "totalFee": o["totalFee"] + 10}
            for o in platform_orders
        ]
        settle = await self.svc.start_settlement("2026-08", CARRIER_SF, channel_orders)
        self.assertEqual(settle["status"], SETTLE_STATUS_DIFF)
        self.assertEqual(settle["diffCount"], 3)
        self.assertEqual(settle["diffDetails"][0]["type"], DIFF_TYPE_AMOUNT_MISMATCH)

    async def test_04_start_settlement_missing_order(self):
        """对账(单据缺失 → diff)"""
        platform_orders = await self.svc.list_orders(carrier=CARRIER_SF)
        channel_orders = [
            {"waybillNo": o["waybillNo"], "totalFee": o["totalFee"]}
            for o in platform_orders[:2]
        ]
        settle = await self.svc.start_settlement("2026-08", CARRIER_SF, channel_orders)
        self.assertEqual(settle["status"], SETTLE_STATUS_DIFF)
        self.assertEqual(settle["diffCount"], 1)
        self.assertEqual(settle["diffDetails"][0]["type"], DIFF_TYPE_ORDER_MISSING)

    async def test_05_start_settlement_extra_order(self):
        """对账(多余单据 → diff)"""
        platform_orders = await self.svc.list_orders(carrier=CARRIER_SF)
        channel_orders = [
            {"waybillNo": o["waybillNo"], "totalFee": o["totalFee"]}
            for o in platform_orders
        ]
        channel_orders.append({"waybillNo": "SF_EXTRA", "totalFee": 50.0})
        settle = await self.svc.start_settlement("2026-08", CARRIER_SF, channel_orders)
        self.assertEqual(settle["status"], SETTLE_STATUS_DIFF)
        self.assertEqual(settle["diffCount"], 1)
        self.assertEqual(settle["diffDetails"][0]["type"], DIFF_TYPE_EXTRA_ORDER)

    async def test_06_duplicate_settlement(self):
        """重复对账 → ValueError"""
        await self.svc.start_settlement("2026-08", CARRIER_SF)
        with self.assertRaises(ValueError):
            await self.svc.start_settlement("2026-08", CARRIER_SF)

    async def test_07_invalid_carrier(self):
        """非法物流商"""
        with self.assertRaises(ValueError):
            await self.svc.start_settlement("2026-08", "XX")

    async def test_08_get_settlement(self):
        """查询结算单"""
        await self.svc.start_settlement("2026-08", CARRIER_SF)
        settle = await self.svc.get_settlement("SETTLE202608SF")
        self.assertEqual(settle["carrier"], CARRIER_SF)
        self.assertEqual(settle["period"], "2026-08")

    async def test_09_get_settlement_not_found(self):
        """查询不存在的结算单"""
        with self.assertRaises(KeyError):
            await self.svc.get_settlement("NOT_EXIST")

    async def test_10_list_settlements(self):
        """结算单列表"""
        await self.svc.start_settlement("2026-08", CARRIER_SF)
        items = await self.svc.list_settlements()
        self.assertEqual(len(items), 1)

    async def test_11_list_settlements_by_period(self):
        """按账期筛选"""
        await self.svc.start_settlement("2026-08", CARRIER_SF)
        # 创建 9 月的结算单(需要先重置锁)
        await self.svc.start_settlement("2026-09", CARRIER_SF)
        aug_items = await self.svc.list_settlements(period="2026-08")
        self.assertEqual(len(aug_items), 1)

    async def test_12_list_pending_settlements(self):
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

    async def test_13_investigate_diff(self):
        """介入调查(diff → investigating)"""
        platform_orders = await self.svc.list_orders(carrier=CARRIER_SF)
        channel_orders = [
            {"waybillNo": o["waybillNo"], "totalFee": o["totalFee"] + 10}
            for o in platform_orders
        ]
        await self.svc.start_settlement("2026-08", CARRIER_SF, channel_orders)
        settle = await self.svc.investigate_diff("SETTLE202608SF")
        self.assertEqual(settle["status"], SETTLE_STATUS_INVESTIGATING)

    async def test_14_investigate_non_diff(self):
        """非 diff 状态不可介入"""
        await self.svc.start_settlement("2026-08", CARRIER_SF)
        with self.assertRaises(ValueError):
            await self.svc.investigate_diff("SETTLE202608SF")

    async def test_15_resolve_settlement(self):
        """处理完毕(investigating → resolved)"""
        platform_orders = await self.svc.list_orders(carrier=CARRIER_SF)
        channel_orders = [
            {"waybillNo": o["waybillNo"], "totalFee": o["totalFee"] + 10}
            for o in platform_orders
        ]
        await self.svc.start_settlement("2026-08", CARRIER_SF, channel_orders)
        await self.svc.investigate_diff("SETTLE202608SF")
        settle = await self.svc.resolve_settlement("SETTLE202608SF", "已补单")
        self.assertEqual(settle["status"], SETTLE_STATUS_RESOLVED)

    async def test_16_resolve_non_investigating(self):
        """非 investigating 状态不可处理"""
        await self.svc.start_settlement("2026-08", CARRIER_SF)
        with self.assertRaises(ValueError):
            await self.svc.resolve_settlement("SETTLE202608SF")

    async def test_17_confirm_after_reconciling(self):
        """reconciling → confirmed"""
        await self.svc.start_settlement("2026-08", CARRIER_SF)
        settle = await self.svc.confirm_settlement("SETTLE202608SF")
        self.assertEqual(settle["status"], SETTLE_STATUS_CONFIRMED)

    async def test_18_confirm_after_resolved(self):
        """resolved → confirmed(差异处理后确认)"""
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

    async def test_19_confirm_invalid_status(self):
        """非 reconciling/resolved 不可确认"""
        await self.svc.start_settlement("2026-08", CARRIER_SF)
        await self.svc.confirm_settlement("SETTLE202608SF")
        # 已 confirmed, 再次确认 → ValueError
        with self.assertRaises(ValueError):
            await self.svc.confirm_settlement("SETTLE202608SF")

    async def test_20_pay_settlement(self):
        """confirmed → paid"""
        await self.svc.start_settlement("2026-08", CARRIER_SF)
        await self.svc.confirm_settlement("SETTLE202608SF")
        settle = await self.svc.pay_settlement("SETTLE202608SF")
        self.assertEqual(settle["status"], SETTLE_STATUS_PAID)
        self.assertIn("payTime", settle)

    async def test_21_pay_non_confirmed(self):
        """非 confirmed 不可付款"""
        await self.svc.start_settlement("2026-08", CARRIER_SF)
        with self.assertRaises(ValueError):
            await self.svc.pay_settlement("SETTLE202608SF")

    async def test_22_paid_terminal(self):
        """paid 为终态"""
        await self.svc.start_settlement("2026-08", CARRIER_SF)
        await self.svc.confirm_settlement("SETTLE202608SF")
        await self.svc.pay_settlement("SETTLE202608SF")
        # 再次付款 → ValueError(状态非 confirmed)
        with self.assertRaises(ValueError):
            await self.svc.pay_settlement("SETTLE202608SF")

    async def test_23_full_diff_flow(self):
        """差异对账完整流程: diff → investigating → resolved → confirmed → paid"""
        platform_orders = await self.svc.list_orders(carrier=CARRIER_SF)
        channel_orders = [
            {"waybillNo": o["waybillNo"], "totalFee": o["totalFee"] + 10}
            for o in platform_orders
        ]
        # 1. 启动对账(有差异)
        settle = await self.svc.start_settlement("2026-08", CARRIER_SF, channel_orders)
        self.assertEqual(settle["status"], SETTLE_STATUS_DIFF)
        # 2. 介入调查
        settle = await self.svc.investigate_diff("SETTLE202608SF")
        self.assertEqual(settle["status"], SETTLE_STATUS_INVESTIGATING)
        # 3. 处理完毕
        settle = await self.svc.resolve_settlement("SETTLE202608SF", "已补单")
        self.assertEqual(settle["status"], SETTLE_STATUS_RESOLVED)
        # 4. 确认
        settle = await self.svc.confirm_settlement("SETTLE202608SF")
        self.assertEqual(settle["status"], SETTLE_STATUS_CONFIRMED)
        # 5. 付款
        settle = await self.svc.pay_settlement("SETTLE202608SF")
        self.assertEqual(settle["status"], SETTLE_STATUS_PAID)


# ============================================================
# 5. 运费计算函数测试
# ============================================================

class TestFeeCalculation(unittest.TestCase):
    """运费计算函数测试"""

    def test_01_insured_fee(self):
        """保价费 = 保价金额 × 0.5%"""
        self.assertEqual(_calc_insured_fee(1000.0), 5.0)
        self.assertEqual(_calc_insured_fee(0.0), 0.0)
        self.assertEqual(_calc_insured_fee(2000.0), 10.0)

    def test_02_package_fee(self):
        """包装费 = 2 元/件"""
        self.assertEqual(_calc_package_fee(1), 2.0)
        self.assertEqual(_calc_package_fee(5), 10.0)

    def test_03_sf_base_fee_standard(self):
        """顺丰标准件运费"""
        self.assertEqual(_calc_sf_base_fee("standard", 0.5), 18.0)
        self.assertEqual(_calc_sf_base_fee("standard", 3.0), 22.0)
        self.assertEqual(_calc_sf_base_fee("standard", 5.0), 28.0)

    def test_04_sf_base_fee_express(self):
        """顺丰特快运费"""
        self.assertEqual(_calc_sf_base_fee("express", 0.5), 25.0)
        self.assertEqual(_calc_sf_base_fee("express", 3.0), 30.0)

    def test_05_sf_base_fee_overweight(self):
        """顺丰超重(续重 2 元/kg)"""
        fee = _calc_sf_base_fee("standard", 25.0)
        # 20kg 内 58 元 + 5kg × 2 元 = 68 元
        self.assertEqual(fee, 68.0)

    def test_06_total_fee_with_discount(self):
        """总运费 = (基础+保价+包装+附加) × 折扣"""
        total = _calc_total_fee(50.0, 5.0, 2.0, 0.0, 0.8)
        self.assertEqual(total, 45.6)  # 57 × 0.8

    def test_07_total_fee_no_discount(self):
        """总运费(无折扣)"""
        total = _calc_total_fee(50.0, 5.0, 2.0, 3.0, 1.0)
        self.assertEqual(total, 60.0)

    def test_08_settle_no_generation(self):
        """结算单号生成"""
        self.assertEqual(_gen_settle_no("2026-08", CARRIER_SF), "SETTLE202608SF")
        self.assertEqual(_gen_settle_no("2026-09", CARRIER_JD), "SETTLE202609JD")


if __name__ == "__main__":
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()

    test_classes = [
        TestCreateOrder,
        TestStatusFlow,
        TestTrackCallback,
        TestTrackCallbackEdgeCases,
        TestSettlement,
        TestFeeCalculation,
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
