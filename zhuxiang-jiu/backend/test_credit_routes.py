"""信用管理模块端到端测试(Service 层, 无需 Docker/fastapi)

直接调用 CreditService 方法, 模拟 10 个 HTTP 接口的业务逻辑。
使用 asyncio 内存模式(LOCK_MODE=asyncio, STORE_MODE=asyncio)。

运行:
    $env:LOCK_MODE="asyncio"; $env:STORE_MODE="asyncio"
    python test_credit_routes.py

覆盖 10 个接口对应的业务方法:
    1. 查询(3):    get_score / list_logs / get_paylater_quota
    2. 操作(1):    adjust_score
    3. 管理(6):    upgrade_level / downgrade_level / add_to_blacklist
                  / restore_credit / get_stats / get_credit_report
"""

import asyncio
import os
import sys

# 确保使用内存模式
os.environ["LOCK_MODE"] = "asyncio"
os.environ["STORE_MODE"] = "asyncio"

from services.credit_service import CreditService
from repositories.credit_repository import (
    CreditRepository,
    # 信用等级
    LEVEL_L1, LEVEL_L2, LEVEL_L3, LEVEL_L4, LEVEL_L5,
    LEVEL_PAYLATER_QUOTA, LEVEL_REWARD_MULTIPLIER,
    # 流水类型
    LOG_TYPE_EARN, LOG_TYPE_DEDUCT, LOG_TYPE_ADJUST,
    LOG_TYPE_UPGRADE, LOG_TYPE_DOWNGRADE,
    LOG_TYPE_BLACKLIST, LOG_TYPE_RESTORE,
    # 账户状态
    STATUS_NORMAL, STATUS_FROZEN, STATUS_BLACKLIST,
    # 角色
    ROLE_MEMBER, ROLE_AGENT,
    level_from_score, clamp_score,
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

USER_ID_1 = 10001
USER_ID_2 = 10002
USER_ID_3 = 10003


# ============================================================
# 测试用例
# ============================================================

class TestQueryScore:
    """查询信用分测试"""

    async def run(self, svc):
        # test 1: 新用户自动创建账户(起始分350, L1)
        account = await svc.get_score(USER_ID_1)
        record("test_01_auto_create_score",
               account["bambooScore"] == 350 and account["creditLevel"] == LEVEL_L1,
               f"expected 350/{LEVEL_L1}, got {account['bambooScore']}/{account['creditLevel']}")

        # test 2: 重复查询返回同一账户
        account2 = await svc.get_score(USER_ID_1)
        record("test_02_idempotent_query",
               account2["userId"] == account["userId"] == USER_ID_1,
               "重复查询应返回同一账户")

        # test 3: 代理商起始分500
        repo = svc.repo
        account = await repo.create_score(USER_ID_2, ROLE_AGENT)
        record("test_03_agent_initial_score",
               account["bambooScore"] == 500 and account["roleType"] == ROLE_AGENT,
               f"expected 500/agent, got {account['bambooScore']}/{account['roleType']}")

        # test 4: 信用等级评定正确(550分应为L3)
        level = level_from_score(550)
        record("test_04_level_judge_550",
               level == LEVEL_L3,
               f"expected {LEVEL_L3}, got {level}")

        # test 5: 信用等级评定正确(800分应为L5)
        level = level_from_score(800)
        record("test_05_level_judge_800",
               level == LEVEL_L5,
               f"expected {LEVEL_L5}, got {level}")

        # test 6: 额度查询(L1无额度)
        quota = await svc.get_paylater_quota(USER_ID_1)
        record("test_06_quota_l1_zero",
               quota["totalQuota"] == 0 and quota["availableQuota"] == 0,
               f"expected 0/0, got {quota['totalQuota']}/{quota['availableQuota']}")


class TestAdjustScore:
    """调整信用分测试"""

    async def run(self, svc):
        # test 7: 加分(350 + 100 = 450, 升级至L2)
        result = await svc.adjust_score(USER_ID_1, 100, "正常消费履约")
        record("test_07_earn_score",
               result["scoreAfter"] == 450 and result["levelAfter"] == LEVEL_L2,
               f"expected 450/{LEVEL_L2}, got {result['scoreAfter']}/{result['levelAfter']}")

        # test 8: 升级标记正确
        record("test_08_upgrade_flag",
               result["levelChanged"] is True and result["isUpgrade"] is True,
               "升级标记错误")

        # test 9: 扣分(450 - 100 = 350, 降级至L1)
        result = await svc.adjust_score(USER_ID_1, -100, "退款率过高扣分")
        record("test_09_deduct_score",
               result["scoreAfter"] == 350 and result["levelAfter"] == LEVEL_L1,
               f"expected 350/{LEVEL_L1}, got {result['scoreAfter']}/{result['levelAfter']}")

        # test 10: 降级标记正确
        record("test_10_downgrade_flag",
               result["levelChanged"] is True and result["isUpgrade"] is False,
               "降级标记错误")

        # test 11: 加分流水写入
        logs = await svc.list_logs(USER_ID_1, log_type=LOG_TYPE_EARN)
        record("test_11_earn_log",
               len(logs) == 1 and logs[0]["delta"] == 100,
               f"expected 1/100, got {len(logs)}/{logs[0]['delta'] if logs else 0}")

        # test 12: 扣分流水写入
        logs = await svc.list_logs(USER_ID_1, log_type=LOG_TYPE_DEDUCT)
        record("test_12_deduct_log",
               len(logs) == 1 and logs[0]["delta"] == -100,
               f"expected 1/-100, got {len(logs)}/{logs[0]['delta'] if logs else 0}")

        # test 13: 升降级流水写入
        upgrade_logs = await svc.list_logs(USER_ID_1, log_type=LOG_TYPE_UPGRADE)
        record("test_13_upgrade_log",
               len(upgrade_logs) == 1,
               f"expected 1, got {len(upgrade_logs)}")
        downgrade_logs = await svc.list_logs(USER_ID_1, log_type=LOG_TYPE_DOWNGRADE)
        record("test_14_downgrade_log",
               len(downgrade_logs) == 1,
               f"expected 1, got {len(downgrade_logs)}")

        # test 15: 分数上下限(0-1000)
        # 当前350 + 9999 = 10349 → clamp到1000
        result = await svc.adjust_score(USER_ID_2, 9999, "巨额加分测试")
        record("test_15_score_upper_limit",
               result["scoreAfter"] == 1000,
               f"expected 1000, got {result['scoreAfter']}")
        # 1000 - 9999 = -8999 → clamp到0
        result = await svc.adjust_score(USER_ID_2, -9999, "巨额扣分测试")
        record("test_16_score_lower_limit",
               result["scoreAfter"] == 0,
               f"expected 0, got {result['scoreAfter']}")

        # test 17: 黑名单后不可调整
        await svc.add_to_blacklist(USER_ID_2, "测试拉黑")
        try:
            await svc.adjust_score(USER_ID_2, 100, "黑名单后调整")
            record("test_17_blacklist_cannot_adjust", False, "应抛出ValueError")
        except ValueError:
            record("test_17_blacklist_cannot_adjust", True)


class TestUpgradeDowngrade:
    """信用升降级测试"""

    async def run(self, svc):
        # test 18: 强制升级 L1→L3
        await svc.get_score(USER_ID_1)  # 创建账户(L1, 350分)
        result = await svc.upgrade_level(USER_ID_1, LEVEL_L3, "表现良好强制升级")
        record("test_18_force_upgrade",
               result["levelAfter"] == LEVEL_L3 and result["scoreAfter"] == 550,
               f"expected {LEVEL_L3}/550, got {result['levelAfter']}/{result['scoreAfter']}")

        # test 19: 升级后额度更新
        quota = await svc.get_paylater_quota(USER_ID_1)
        record("test_19_quota_after_upgrade",
               quota["totalQuota"] == LEVEL_PAYLATER_QUOTA[LEVEL_L3],
               f"expected {LEVEL_PAYLATER_QUOTA[LEVEL_L3]}, got {quota['totalQuota']}")

        # test 20: 重复升级失败(目标等级不高于当前)
        try:
            await svc.upgrade_level(USER_ID_1, LEVEL_L3, "重复升级")
            record("test_20_duplicate_upgrade", False, "应抛出ValueError")
        except ValueError:
            record("test_20_duplicate_upgrade", True)

        # test 21: 降级到更低等级失败(目标等级高于当前)
        try:
            await svc.upgrade_level(USER_ID_1, LEVEL_L4, "升级测试")
            # L3 → L4 升级合法
            record("test_21_upgrade_to_l4", True)
        except ValueError:
            record("test_21_upgrade_to_l4", False, "L3→L4应为合法升级")

        # test 22: 强制降级 L4→L2
        result = await svc.downgrade_level(USER_ID_1, LEVEL_L2, "违规操作降级")
        record("test_22_force_downgrade",
               result["levelAfter"] == LEVEL_L2 and result["scoreAfter"] == 549,
               f"expected {LEVEL_L2}/549, got {result['levelAfter']}/{result['scoreAfter']}")

        # test 23: 降级后额度减少
        quota = await svc.get_paylater_quota(USER_ID_1)
        record("test_23_quota_after_downgrade",
               quota["totalQuota"] == LEVEL_PAYLATER_QUOTA[LEVEL_L2],
               f"expected {LEVEL_PAYLATER_QUOTA[LEVEL_L2]}, got {quota['totalQuota']}")

        # test 24: 不存在的账户升级失败(404)
        try:
            await svc.upgrade_level(99999, LEVEL_L3, "测试不存在账户")
            record("test_24_upgrade_nonexistent", False, "应抛出KeyError")
        except KeyError:
            record("test_24_upgrade_nonexistent", True)

        # test 25: 无效目标等级
        try:
            await svc.upgrade_level(USER_ID_1, "L9", "无效等级")
            record("test_25_invalid_target_level", False, "应抛出ValueError")
        except ValueError:
            record("test_25_invalid_target_level", True)


class TestBlacklistRestore:
    """黑名单与恢复测试"""

    async def run(self, svc):
        # 准备: 创建账户并升级至L3
        await svc.get_score(USER_ID_1)
        await svc.upgrade_level(USER_ID_1, LEVEL_L3, "升级测试")

        # test 26: 加入黑名单
        result = await svc.add_to_blacklist(USER_ID_1, "严重失信")
        record("test_26_add_blacklist",
               result["status"] == STATUS_BLACKLIST and result["scoreAfter"] == 0,
               f"expected blacklist/0, got {result['status']}/{result['scoreAfter']}")

        # test 27: 黑名单账户额度清零
        quota = await svc.get_paylater_quota(USER_ID_1)
        record("test_27_blacklist_quota_zero",
               quota["totalQuota"] == 0 and quota["status"] == STATUS_BLACKLIST,
               f"expected 0/blacklist, got {quota['totalQuota']}/{quota['status']}")

        # test 28: 重复加入黑名单失败
        try:
            await svc.add_to_blacklist(USER_ID_1, "重复拉黑")
            record("test_28_duplicate_blacklist", False, "应抛出ValueError")
        except ValueError:
            record("test_28_duplicate_blacklist", True)

        # test 29: 恢复信用
        result = await svc.restore_credit(USER_ID_1, 500, "信用修复审核通过")
        record("test_29_restore_credit",
               result["status"] == STATUS_NORMAL and result["scoreAfter"] == 500,
               f"expected normal/500, got {result['status']}/{result['scoreAfter']}")

        # test 30: 恢复后等级评定正确(500分应为L2)
        account = await svc.get_score(USER_ID_1)
        record("test_30_restore_level_correct",
               account["creditLevel"] == LEVEL_L2,
               f"expected {LEVEL_L2}, got {account['creditLevel']}")

        # test 31: 正常账户恢复失败
        try:
            await svc.restore_credit(USER_ID_1, 500, "重复恢复")
            record("test_31_restore_normal_account", False, "应抛出ValueError")
        except ValueError:
            record("test_31_restore_normal_account", True)

        # test 32: 恢复分数超出范围
        await svc.add_to_blacklist(USER_ID_1, "再次拉黑")
        try:
            await svc.restore_credit(USER_ID_1, 2000, "超限恢复")
            record("test_32_restore_score_out_of_range", False, "应抛出ValueError")
        except ValueError:
            record("test_32_restore_score_out_of_range", True)

        # test 33: 不存在的账户加入黑名单失败
        try:
            await svc.add_to_blacklist(88888, "测试")
            record("test_33_blacklist_nonexistent", False, "应抛出KeyError")
        except KeyError:
            record("test_33_blacklist_nonexistent", True)


class TestStatsReport:
    """统计与信用报告测试"""

    async def run(self, svc):
        # 准备: 多次操作
        await svc.get_score(USER_ID_1)
        await svc.adjust_score(USER_ID_1, 200, "加分")  # 350→550
        await svc.adjust_score(USER_ID_1, -50, "扣分")  # 550→500
        await svc.upgrade_level(USER_ID_1, LEVEL_L4, "升级")  # 500→700
        await svc.downgrade_level(USER_ID_1, LEVEL_L2, "降级")  # 700→549

        # test 34: 信用统计正确
        stats = await svc.get_stats(USER_ID_1)
        record("test_34_stats_fields",
               all(k in stats for k in ["bambooScore", "creditLevel", "logCount",
                   "earnCount", "deductCount", "upgradeCount", "downgradeCount"]),
               "统计字段缺失")
        record("test_35_stats_correct",
               stats["bambooScore"] == 549 and stats["creditLevel"] == LEVEL_L2,
               f"expected 549/{LEVEL_L2}, got {stats['bambooScore']}/{stats['creditLevel']}")
        record("test_36_stats_counts",
               stats["earnCount"] == 1 and stats["deductCount"] == 1
               and stats["upgradeCount"] >= 1 and stats["downgradeCount"] >= 1,
               f"count error: {stats}")

        # test 37: 信用报告生成
        report = await svc.get_credit_report(USER_ID_1)
        record("test_37_report_fields",
               all(k in report for k in ["bambooScore", "creditLevel", "paylater",
                   "benefits", "rewardMultiplier", "recentChanges"]),
               "报告字段缺失")

        # test 38: 报告近期变化正确
        record("test_38_report_recent_changes",
               isinstance(report["recentChanges"], list)
               and len(report["recentChanges"]) > 0,
               f"recentChanges 应为非空列表")

        # test 39: 报告权益匹配等级
        record("test_39_report_benefits_match",
               report["benefits"]["paylater"] == LEVEL_PAYLATER_QUOTA[LEVEL_L2]
               and report["benefits"]["rewardMultiplier"] == LEVEL_REWARD_MULTIPLIER[LEVEL_L2],
               "权益与等级不匹配")


class TestEdgeCases:
    """边界场景测试"""

    async def run(self, svc):
        # test 40: 等级序号正确(L1<L2<L3<L4<L5)
        from services.credit_service import CreditService as CS
        ranks = [CS._level_rank(l) for l in
                 [LEVEL_L1, LEVEL_L2, LEVEL_L3, LEVEL_L4, LEVEL_L5]]
        record("test_40_level_ranks_ordered",
               ranks == [1, 2, 3, 4, 5],
               f"expected [1,2,3,4,5], got {ranks}")

        # test 41: 分数边界(0=L1下限, 1000=L5上限)
        record("test_41_score_boundaries",
               level_from_score(0) == LEVEL_L1 and level_from_score(1000) == LEVEL_L5,
               "0分应为L1, 1000分应为L5")

        # test 42: 分数边界(399=L1上限, 400=L2下限)
        record("test_42_boundary_399_400",
               level_from_score(399) == LEVEL_L1 and level_from_score(400) == LEVEL_L2,
               "399分应为L1, 400分应为L2")

        # test 43: 等级最低分与最高分
        from services.credit_service import CreditService as CS
        record("test_43_level_min_max",
               CS._level_min_score(LEVEL_L3) == 550 and CS._level_max_score(LEVEL_L3) == 699,
               "L3 应为 550-699")

        # test 44: clamp_score 边界
        record("test_44_clamp_score",
               clamp_score(-100) == 0 and clamp_score(2000) == 1000
               and clamp_score(500) == 500,
               "clamp_score 边界错误")

        # test 45: 不存在的用户查询流水(空列表)
        logs = await svc.list_logs(77777)
        record("test_45_empty_logs",
               len(logs) == 0,
               f"expected 0, got {len(logs)}")

        # test 46: 不存在的用户统计(自动创建)
        stats = await svc.get_stats(66666)
        record("test_46_stats_auto_create",
               stats["bambooScore"] == 350 and stats["creditLevel"] == LEVEL_L1,
               f"expected 350/{LEVEL_L1}, got {stats['bambooScore']}/{stats['creditLevel']}")

        # test 47: 0分调整(无变化)
        await svc.get_score(USER_ID_3)
        result = await svc.adjust_score(USER_ID_3, 0, "零调整")
        record("test_47_zero_adjust",
               result["delta"] == 0 and result["scoreAfter"] == 350,
               f"expected 0/350, got {result['delta']}/{result['scoreAfter']}")


# ============================================================
# 测试运行
# ============================================================

async def main():
    print("=" * 60)
    print("信用管理模块端到端测试")
    print("=" * 60)
    print()

    test_classes = [
        TestQueryScore,
        TestAdjustScore,
        TestUpgradeDowngrade,
        TestBlacklistRestore,
        TestStatsReport,
        TestEdgeCases,
    ]

    for cls in test_classes:
        reset_store()
        svc = CreditService()
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
