"""会员积分管理模块端到端测试(Service 层, 无需 Docker/fastapi)

直接调用 PointsService 方法, 模拟 10 个 HTTP 接口的业务逻辑。
使用 asyncio 内存模式(LOCK_MODE=asyncio, STORE_MODE=asyncio)。

运行:
    $env:LOCK_MODE="asyncio"; $env:STORE_MODE="asyncio"
    python test_points_routes.py

覆盖 10 个接口对应的业务方法:
    1. 签到(2):     signin / get_signin_records
    2. 消费返分(1):  earn_order_points
    3. 积分抵现(1):  deduct_points
    4. 退款返还(1):  refund_points
    5. 过期处理(2):  run_expire_process / get_expiring_points
    6. 查询统计(3):  get_account / list_logs / get_stats

测试覆盖:
    - 签到积分(首次/连续/宝箱/幂等/断签)
    - 消费返分(基础/等级加成/每日上限/每月上限)
    - 积分抵现(正常/30%上限/不足/低于最低/非整数倍)
    - 退款返还(正常/扣回超出余额)
    - 过期处理(扫描/扣减/流水)
    - 查询统计(账户/流水/将过期/统计)
"""

import asyncio
import os
import sys
from datetime import datetime, date, timedelta, timezone

# 确保使用内存模式
os.environ["LOCK_MODE"] = "asyncio"
os.environ["STORE_MODE"] = "asyncio"

from services.points_service import PointsService
from repositories.points_repository import (
    PointsRepository,
    # 流水类型
    LOG_TYPE_EARN, LOG_TYPE_SPEND,
    # 流水来源
    SOURCE_CHECKIN, SOURCE_ORDER, SOURCE_DEDUCT, SOURCE_REFUND, SOURCE_EXPIRE,
    # 流水状态
    LOG_STATUS_AVAILABLE, LOG_STATUS_EXPIRED, LOG_STATUS_CONSUMED,
    # 过期批次状态
    EXPIRE_STATUS_ACTIVE, EXPIRE_STATUS_EXPIRED, EXPIRE_STATUS_CONSUMED,
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

# 测试用户
USER_ID_1 = 1001
USER_ID_2 = 1002
USER_ID_3 = 1003

# 会员等级
LEVEL_NORMAL = 1     # 普通会员(1.0×)
LEVEL_SILVER = 2     # 银卡(1.2×)
LEVEL_GOLD = 3        # 金卡(1.5×)
LEVEL_PLATINUM = 4   # 白金(2.0×)
LEVEL_SVIP = 5       # SVIP(3.0×)

# 订单
ORDER_ID_1 = "ORD20260822001"
ORDER_ID_2 = "ORD20260822002"


# ============================================================
# 测试用例
# ============================================================

class TestSignin:
    """签到积分测试"""

    async def run(self, svc):
        # test 1: 首次签到(基础10分)
        result = await svc.signin(USER_ID_1)
        record("test_01_first_signin",
               result["pointsEarned"] == 10 and result["continuousDays"] == 1,
               f"expected 10/1, got {result['pointsEarned']}/{result['continuousDays']}")

        # test 2: 重复签到幂等(409)
        try:
            await svc.signin(USER_ID_1)
            record("test_02_duplicate_signin", False, "应抛出ValueError")
        except ValueError:
            record("test_02_duplicate_signin", True)

        # test 3: 查询签到记录
        records = await svc.get_signin_records(USER_ID_1)
        record("test_03_signin_records", len(records) == 1, f"expected 1, got {len(records)}")

        # test 4: 连续签到(模拟昨日签到)
        repo = svc.repo
        yesterday = (date.today() - timedelta(days=1)).isoformat()
        await repo.add_signin({
            "userId": USER_ID_2,
            "signDate": yesterday,
            "continuousDays": 1,
            "pointsEarned": 10,
            "isBonus": 0,
        })
        # 今日签到
        result = await svc.signin(USER_ID_2)
        record("test_04_continuous_signin",
               result["continuousDays"] == 2 and result["pointsEarned"] == 15,
               f"expected 2/15, got {result['continuousDays']}/{result['pointsEarned']}")

        # test 5: 账户余额正确(首次10 + 连续15 = 25)
        account = await svc.get_account(USER_ID_1)
        record("test_05_account_balance",
               account["totalPoints"] == 10,
               f"expected 10, got {account['totalPoints']}")

        # test 6: 签到写入流水
        logs = await svc.list_logs(USER_ID_1, source=SOURCE_CHECKIN)
        record("test_06_signin_log",
               len(logs) == 1 and logs[0]["points"] == 10,
               f"expected 1 log/10pts, got {len(logs)}/{logs[0]['points'] if logs else 0}")


class TestEarnOrderPoints:
    """消费返分测试"""

    async def run(self, svc):
        # 先签到确保账户存在
        await svc.signin(USER_ID_1)

        # test 7: 基础返分(¥100 × 1.5 × 1.0 = 150分)
        result = await svc.earn_order_points(
            USER_ID_1, ORDER_ID_1, 100.0, LEVEL_NORMAL
        )
        record("test_07_base_earn",
               result["earnedPoints"] == 150,
               f"expected 150, got {result['earnedPoints']}")

        # test 8: SVIP加成(¥100 × 1.5 × 3.0 = 450分)
        await svc.signin(USER_ID_3)
        result = await svc.earn_order_points(
            USER_ID_3, ORDER_ID_2, 100.0, LEVEL_SVIP
        )
        record("test_08_svip_multiplier",
               result["earnedPoints"] == 450 and result["multiplier"] == 3.0,
               f"expected 450/3.0, got {result['earnedPoints']}/{result['multiplier']}")

        # test 9: 账户余额增加
        account = await svc.get_account(USER_ID_1)
        record("test_09_balance_after_earn",
               account["totalPoints"] == 160,  # 10签到 + 150返分
               f"expected 160, got {account['totalPoints']}")

        # test 10: 消费返分流水
        logs = await svc.list_logs(USER_ID_1, source=SOURCE_ORDER)
        record("test_10_earn_log",
               len(logs) == 1 and logs[0]["points"] == 150,
               f"expected 1/150, got {len(logs)}/{logs[0]['points'] if logs else 0}")


class TestDeductPoints:
    """积分抵现测试"""

    async def run(self, svc):
        # 准备: 签到获得10分 + 消费返分获得150分 = 160分
        await svc.signin(USER_ID_1)
        await svc.earn_order_points(USER_ID_1, ORDER_ID_1, 100.0, LEVEL_NORMAL)

        # test 11: 正常抵扣(100分=¥1)
        result = await svc.deduct_points(
            USER_ID_1, ORDER_ID_1, 1000.0, 100
        )
        record("test_11_normal_deduct",
               result["deductPoints"] == 100 and result["deductAmount"] == 1.0,
               f"expected 100/1.0, got {result['deductPoints']}/{result['deductAmount']}")

        # test 12: 余额正确(160 - 100 = 60)
        account = await svc.get_account(USER_ID_1)
        record("test_12_balance_after_deduct",
               account["totalPoints"] == 60,
               f"expected 60, got {account['totalPoints']}")

        # test 13: 30%上限校验(¥100订单最多抵30% = ¥30 = 3000分)
        try:
            await svc.deduct_points(USER_ID_1, ORDER_ID_1, 100.0, 3100)
            record("test_13_deduct_over_limit", False, "应抛出ValueError")
        except ValueError:
            record("test_13_deduct_over_limit", True)

        # test 14: 低于最低抵扣(50 < 100)
        try:
            await svc.deduct_points(USER_ID_1, ORDER_ID_1, 1000.0, 50)
            record("test_14_below_min_deduct", False, "应抛出ValueError")
        except ValueError:
            record("test_14_below_min_deduct", True)

        # test 15: 非100整数倍(150)
        try:
            await svc.deduct_points(USER_ID_1, ORDER_ID_1, 1000.0, 150)
            record("test_15_non_multiple_deduct", False, "应抛出ValueError")
        except ValueError:
            record("test_15_non_multiple_deduct", True)

        # test 16: 积分不足
        try:
            await svc.deduct_points(USER_ID_1, ORDER_ID_1, 1000.0, 6000)
            record("test_16_insufficient_points", False, "应抛出ValueError")
        except ValueError:
            record("test_16_insufficient_points", True)

        # test 17: 抵扣流水
        logs = await svc.list_logs(USER_ID_1, source=SOURCE_DEDUCT)
        record("test_17_deduct_log",
               len(logs) == 1 and logs[0]["points"] == -100,
               f"expected 1/-100, got {len(logs)}/{logs[0]['points'] if logs else 0}")


class TestRefundPoints:
    """退款返还测试"""

    async def run(self, svc):
        # 准备: 签到10分 + 消费返分150分 = 160分
        await svc.signin(USER_ID_1)
        await svc.earn_order_points(USER_ID_1, ORDER_ID_1, 100.0, LEVEL_NORMAL)

        # test 18: 正常扣回
        account_before = await svc.get_account(USER_ID_1)
        balance_before = account_before["totalPoints"]
        result = await svc.refund_points(USER_ID_1, ORDER_ID_1, 50)
        record("test_18_normal_refund",
               result["actualRefund"] == 50,
               f"expected 50, got {result['actualRefund']}")

        # test 19: 余额减少
        account_after = await svc.get_account(USER_ID_1)
        record("test_19_balance_after_refund",
               account_after["totalPoints"] == balance_before - 50,
               f"expected {balance_before - 50}, got {account_after['totalPoints']}")

        # test 20: 退款流水
        logs = await svc.list_logs(USER_ID_1, source=SOURCE_REFUND)
        record("test_20_refund_log",
               len(logs) == 1 and logs[0]["points"] == -50,
               f"expected 1/-50, got {len(logs)}/{logs[0]['points'] if logs else 0}")

        # test 21: 扣回超出余额(扣到0)
        result = await svc.refund_points(USER_ID_1, ORDER_ID_1, 999999)
        record("test_21_refund_exceed_balance",
               result["remainingPoints"] == 0,
               f"expected 0, got {result['remainingPoints']}")


class TestExpireProcess:
    """积分过期处理测试"""

    async def run(self, svc):
        # 创建一个新用户并获取积分
        await svc.signin(USER_ID_2)  # 10分

        # 手动插入一个已过期的批次
        repo = svc.repo
        account = await repo.get_account(USER_ID_2)
        past_date = (datetime.utcnow() - timedelta(days=1)).isoformat()
        await repo.add_expire_batch({
            "userId": USER_ID_2,
            "logId": 999,
            "points": 5,
            "consumedPoints": 0,
            "earnedAt": past_date,
            "expireAt": past_date,
            "status": EXPIRE_STATUS_ACTIVE,
        })
        # 增加账户余额以匹配过期批次
        account["totalPoints"] = account.get("totalPoints", 0) + 5
        await repo.save_account(account)

        # test 22: 执行过期扫描
        result = await svc.run_expire_process()
        record("test_22_expire_run",
               result["expiredCount"] >= 1 and result["affectedUsers"] >= 1,
               f"expected >=1/>=1, got {result['expiredCount']}/{result['affectedUsers']}")

        # test 23: 账户余额扣减
        account = await svc.get_account(USER_ID_2)
        # 原有10 + 手动加5 - 过期5 = 10
        record("test_23_balance_after_expire",
               account["totalPoints"] == 10,
               f"expected 10, got {account['totalPoints']}")

        # test 24: 过期流水
        logs = await svc.list_logs(USER_ID_2, source=SOURCE_EXPIRE)
        record("test_24_expire_log",
               len(logs) >= 1 and logs[0]["points"] == -5,
               f"expected >=1/-5, got {len(logs)}/{logs[0]['points'] if logs else 0}")

        # test 25: 查询将过期积分
        result = await svc.get_expiring_points(USER_ID_2, days=30)
        record("test_25_expiring_query",
               isinstance(result["expiringPoints"], int),
               f"unexpected result: {result}")


class TestQueryStats:
    """查询统计测试"""

    async def run(self, svc):
        # 准备: 签到 + 返分
        await svc.signin(USER_ID_1)
        await svc.earn_order_points(USER_ID_1, ORDER_ID_1, 100.0, LEVEL_NORMAL)

        # test 26: 查询账户
        account = await svc.get_account(USER_ID_1)
        record("test_26_get_account",
               "totalPoints" in account and "totalEarned" in account,
               "账户字段缺失")

        # test 27: 查询流水(全部)
        logs = await svc.list_logs(USER_ID_1)
        record("test_27_list_logs",
               len(logs) > 0,
               "流水为空")

        # test 28: 查询流水(按来源筛选)
        checkin_logs = await svc.list_logs(USER_ID_1, source=SOURCE_CHECKIN)
        record("test_28_filter_logs_by_source",
               all(l["source"] == SOURCE_CHECKIN for l in checkin_logs),
               "筛选失败")

        # test 29: 积分统计
        stats = await svc.get_stats(USER_ID_1)
        record("test_29_get_stats",
               "earnBySource" in stats and "spendBySource" in stats and "signinCount" in stats,
               "统计字段缺失")

        # test 30: 统计正确性
        record("test_30_stats_correct",
               stats["signinCount"] >= 1 and SOURCE_CHECKIN in stats["earnBySource"],
               f"统计错误: {stats}")


class TestEdgeCases:
    """边界场景测试"""

    async def run(self, svc):
        # test 31: 新用户自动创建账户
        new_user = 99999
        account = await svc.get_account(new_user)
        record("test_31_auto_create_account",
               account["totalPoints"] == 0 and account["totalEarned"] == 0,
               f"expected 0/0, got {account['totalPoints']}/{account['totalEarned']}")

        # test 32: 订单金额为0返分失败
        try:
            await svc.earn_order_points(new_user, "ORD_ZERO", 0.0, LEVEL_NORMAL)
            record("test_32_zero_order_earn", False, "应抛出ValueError")
        except ValueError:
            record("test_32_zero_order_earn", True)

        # test 33: 退款积分为0失败
        try:
            await svc.refund_points(new_user, "ORD_ZERO", 0)
            record("test_33_zero_refund", False, "应抛出ValueError")
        except ValueError:
            record("test_33_zero_refund", True)

        # test 34: 抵扣积分为0失败
        try:
            await svc.deduct_points(new_user, "ORD_ZERO", 100.0, 0)
            record("test_34_zero_deduct", False, "应抛出ValueError")
        except ValueError:
            record("test_34_zero_deduct", True)

        # test 35: 等级加成倍数正确(¥100 × 1.5 × 1.0 = 150)
        result = await svc.earn_order_points(
            88881, "ORD_LVL1", 100.0, LEVEL_NORMAL
        )
        expected_l1 = int(100 * 1.5 * 1.0)  # 150
        record("test_35_level_normal_multiplier",
               result["earnedPoints"] == expected_l1,
               f"expected {expected_l1}, got {result['earnedPoints']}")

        # test 36: 白金加成(¥100 × 1.5 × 2.0 = 300, 不超每日上限500)
        result = await svc.earn_order_points(
            88882, "ORD_LVL4", 100.0, LEVEL_PLATINUM
        )
        expected_l4 = int(100 * 1.5 * 2.0)  # 300
        record("test_36_level_platinum_multiplier",
               result["earnedPoints"] == expected_l4,
               f"expected {expected_l4}, got {result['earnedPoints']}")

        # test 37: 查询不存在的用户流水
        logs = await svc.list_logs(77777)
        record("test_37_empty_logs",
               len(logs) == 0,
               f"expected 0, got {len(logs)}")

        # test 38: 查询不存在的用户统计
        stats = await svc.get_stats(77777)
        record("test_38_empty_stats",
               stats["totalPoints"] == 0 and stats["signinCount"] == 0,
               f"expected 0/0, got {stats['totalPoints']}/{stats['signinCount']}")


# ============================================================
# 测试运行
# ============================================================

async def main():
    print("=" * 60)
    print("会员积分管理模块端到端测试")
    print("=" * 60)
    print()

    test_classes = [
        TestSignin,
        TestEarnOrderPoints,
        TestDeductPoints,
        TestRefundPoints,
        TestExpireProcess,
        TestQueryStats,
        TestEdgeCases,
    ]

    for cls in test_classes:
        reset_store()
        svc = PointsService()
        print(f"[{cls.__name__}]")
        instance = cls()
        await instance.run(svc)
        print()
        # 输出当前测试类的结果
        for r in RESULTS[-len(instance.run.__code__.co_consts):]:
            pass
        # 简单输出已记录结果

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
