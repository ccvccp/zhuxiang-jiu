"""信用管理模块端到端测试(Service 层, 无需 Docker/fastapi)

直接调用 CreditService 方法, 模拟 20 个 HTTP 接口的业务逻辑。
使用 asyncio 内存模式(LOCK_MODE=asyncio, STORE_MODE=asyncio)。

运行:
    $env:LOCK_MODE="asyncio"; $env:STORE_MODE="asyncio"
    python test_credit_routes.py

覆盖 20 个接口对应的业务方法:
    1. 查询(8):     get_score / list_logs / get_paylater_quota / get_stats
                    / get_credit_report / list_quarterly_settlements
                    / list_exchanges / list_paylater_orders
    2. 操作(4):     adjust_score / upgrade_level / create_paylater_order
                    / repay_paylater_order
    3. 用户兑换(2): exchange_rewards / recommend_exchange
    4. 等级评估(1): evaluate_level_transition
    5. 管理(5):     downgrade_level / add_to_blacklist / restore_credit
                    / settle_quarter / review_paylater_order

v8.0 扩展体系覆盖:
    - 等级规则引擎: 区间持续天数/升级保护期/降级缓冲预警/信用修复期(文档4.1)
    - 季度信用积分结算: 行为分×权重×时序系数+等级加成+幂等(文档5.1)
    - 积分兑换: 现金/商品/权益/组合+个税+季度上限+AI推荐(文档5.2/5.3)
    - 先享后付: AI审批/额度占用/还款恢复/逾期费率(文档4.3)
"""

import asyncio
import os
import sys
from datetime import datetime, timedelta

# 确保使用内存模式
os.environ["LOCK_MODE"] = "asyncio"
os.environ["STORE_MODE"] = "asyncio"

from services.credit_service import CreditService
from repositories.credit_repository import (
    CreditRepository,
    # 信用等级
    LEVEL_L1, LEVEL_L2, LEVEL_L3, LEVEL_L4, LEVEL_L5,
    LEVEL_PAYLATER_QUOTA, LEVEL_B_PAYLATER_QUOTA, LEVEL_REWARD_MULTIPLIER,
    # v8.0 扩展常量
    LEVEL_UPGRADE_SUSTAIN_DAYS, LEVEL_DOWNGRADE_SUSTAIN_DAYS,
    UPGRADE_PROTECTION_DAYS, CREDIT_REPAIR_DAYS,
    QUARTER_TIME_FACTOR, QUARTER_POINTS_CAP, QUARTER_CASH_CAP,
    CASH_TAX_FREE_AMOUNT, CASH_TAX_RATE,
    BEHAVIOR_WEIGHTS, EXCHANGE_RATES, EXCHANGE_CATALOG,
    PAYLATER_OVERDUE_DAILY_RATE, PAYLATER_OVERDUE_PENALTY_RATE,
    PAYLATER_PENALTY_START_DAYS, PAYLATER_OVERDUE_SCORE_PENALTY,
    # 流水类型
    LOG_TYPE_EARN, LOG_TYPE_DEDUCT, LOG_TYPE_ADJUST,
    LOG_TYPE_UPGRADE, LOG_TYPE_DOWNGRADE,
    LOG_TYPE_BLACKLIST, LOG_TYPE_RESTORE,
    LOG_TYPE_SEASON_SETTLE, LOG_TYPE_EXCHANGE,
    LOG_TYPE_PAYLATER_ORDER, LOG_TYPE_PAYLATER_REPAY,
    LOG_TYPE_LEVEL_WARNING,
    # 先享后付状态
    PAYLATER_STATUS_REVIEW, PAYLATER_STATUS_ACTIVE,
    PAYLATER_STATUS_REPAID, PAYLATER_STATUS_REJECTED,
    PAYLATER_ACCOUNT_MEMBER, PAYLATER_ACCOUNT_B,
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
        # test 7: 加分(350 + 100 = 450; v8.0规则: 跨区间不即时升级, 等级保持L1待持续30天)
        result = await svc.adjust_score(USER_ID_1, 100, "正常消费履约")
        record("test_07_earn_score",
               result["scoreAfter"] == 450 and result["levelAfter"] == LEVEL_L1,
               f"expected 450/{LEVEL_L1}(v8.0持续天数规则), "
               f"got {result['scoreAfter']}/{result['levelAfter']}")

        # test 8: 跨区间标记正确(未持续满30天不升级)
        record("test_08_upgrade_flag",
               result["levelChanged"] is False and result["isUpgrade"] is False,
               "跨区间未满持续天数不应升级")

        # test 9: 扣分(450 - 100 = 350; 回到原区间, 等级保持L1)
        result = await svc.adjust_score(USER_ID_1, -100, "退款率过高扣分")
        record("test_09_deduct_score",
               result["scoreAfter"] == 350 and result["levelAfter"] == LEVEL_L1,
               f"expected 350/{LEVEL_L1}, got {result['scoreAfter']}/{result['levelAfter']}")

        # test 10: 无降级发生(等级未变)
        record("test_10_downgrade_flag",
               result["levelChanged"] is False,
               "等级未变化")

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

        # test 13: 无即时升降级流水(v8.0: 持续天数规则下本次调分未触发等级变更)
        upgrade_logs = await svc.list_logs(USER_ID_1, log_type=LOG_TYPE_UPGRADE)
        record("test_13_upgrade_log",
               len(upgrade_logs) == 0,
               f"expected 0(v8.0规则不即时升级), got {len(upgrade_logs)}")
        downgrade_logs = await svc.list_logs(USER_ID_1, log_type=LOG_TYPE_DOWNGRADE)
        record("test_14_downgrade_log",
               len(downgrade_logs) == 0,
               f"expected 0, got {len(downgrade_logs)}")

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


class TestLevelEvaluation:
    """v8.0 等级规则评估引擎测试(文档4.1: 持续天数/保护期/预警/修复期)"""

    async def run(self, svc):
        # ---- 场景1: 区间持续天数达标 → 升级 L1→L2(需30天, 已持续35天) ----
        await svc.get_score(USER_ID_1)  # L1/350
        account = await svc.repo.get_score(USER_ID_1)
        account["bambooScore"] = 450  # L2区间
        account["scoreZoneSince"] = _days_ago(35)
        await svc.repo.save_score(account)

        result = await svc.evaluate_level_transition(USER_ID_1)
        record("test_48_upgrade_after_sustain_days",
               result["levelChanged"] is True and result["isUpgrade"] is True
               and result["levelAfter"] == LEVEL_L2 and result["action"] == "upgrade",
               f"expected upgrade to {LEVEL_L2}, got {result}")

        # test 49: 升级流水写入
        logs = await svc.list_logs(USER_ID_1, log_type=LOG_TYPE_UPGRADE)
        record("test_49_upgrade_log_written",
               len(logs) == 1 and logs[0]["levelAfter"] == LEVEL_L2,
               f"expected 1/{LEVEL_L2}, got {len(logs)}")

        # test 50: 升级后额度更新为L2额度(0)
        quota = await svc.get_paylater_quota(USER_ID_1)
        record("test_50_quota_after_rule_upgrade",
               quota["totalQuota"] == LEVEL_PAYLATER_QUOTA[LEVEL_L2],
               f"expected {LEVEL_PAYLATER_QUOTA[LEVEL_L2]}, got {quota['totalQuota']}")

        # ---- 场景2: 升级保护期内跌破区间 → 不降级 ----
        # 刚升级(lastLevelChangeAt=刚刚), 扣分450→350跌回L1区间
        result = await svc.adjust_score(USER_ID_1, -100, "保护期测试扣分")
        ev = result["levelEvaluation"]
        record("test_51_no_downgrade_in_protection",
               ev["action"] == "none" and ev["protectionDaysLeft"] >= 29
               and result["levelAfter"] == LEVEL_L2,
               f"expected none/{LEVEL_L2}, got {ev['action']}/{result['levelAfter']}")

        # ---- 场景3: 保护期外跌破区间未满30天 → 降级缓冲预警(仅一次) ----
        account = await svc.repo.get_score(USER_ID_1)
        account["lastLevelChangeAt"] = _days_ago(35)   # 保护期已过
        account["scoreZoneSince"] = _days_ago(5)       # 跌破区间仅5天
        await svc.repo.save_score(account)

        result = await svc.evaluate_level_transition(USER_ID_1)
        record("test_52_downgrade_buffer_warning",
               result["action"] == "warning" and result["levelChanged"] is False,
               f"expected warning, got {result['action']}")

        warnings = await svc.list_logs(USER_ID_1, log_type=LOG_TYPE_LEVEL_WARNING)
        record("test_53_warning_log_written",
               len(warnings) == 1,
               f"expected 1, got {len(warnings)}")

        await svc.evaluate_level_transition(USER_ID_1)  # 重复评估
        warnings = await svc.list_logs(USER_ID_1, log_type=LOG_TYPE_LEVEL_WARNING)
        record("test_54_warning_only_once",
               len(warnings) == 1,
               f"expected 1(同一区间只预警一次), got {len(warnings)}")

        # ---- 场景4: 跌破区间满30天 → 降级 + 进入60天修复期 ----
        account = await svc.repo.get_score(USER_ID_1)
        account["scoreZoneSince"] = _days_ago(31)
        await svc.repo.save_score(account)

        result = await svc.evaluate_level_transition(USER_ID_1)
        record("test_55_downgrade_after_sustain_days",
               result["levelChanged"] is True and result["isUpgrade"] is False
               and result["levelAfter"] == LEVEL_L1,
               f"expected downgrade to {LEVEL_L1}, got {result}")

        account = await svc.repo.get_score(USER_ID_1)
        repair_until = account.get("repairUntil")
        record("test_56_repair_period_set",
               repair_until is not None
               and _days_between_now(repair_until) >= CREDIT_REPAIR_DAYS - 1,
               f"expected repairUntil≈+{CREDIT_REPAIR_DAYS}天, got {repair_until}")

        # test: 降级流水写入
        logs = await svc.list_logs(USER_ID_1, log_type=LOG_TYPE_DOWNGRADE)
        record("test_57_downgrade_log_written",
               len(logs) == 1 and logs[0]["levelAfter"] == LEVEL_L1,
               f"expected 1/{LEVEL_L1}, got {len(logs)}")

        # ---- 场景5: 修复期内升级所需持续天数减半(30/2=15天, 已持续16天) ----
        account["bambooScore"] = 450  # L2区间
        account["scoreZoneSince"] = _days_ago(16)
        await svc.repo.save_score(account)

        result = await svc.evaluate_level_transition(USER_ID_1)
        record("test_58_repair_period_half_days_upgrade",
               result["levelChanged"] is True and result["levelAfter"] == LEVEL_L2
               and result["inRepairPeriod"] is True,
               f"expected repair-accelerated upgrade to {LEVEL_L2}, got {result}")

        # ---- 场景6: 未满持续天数不升级(L2→L3需60天, 仅59天且不在修复期) ----
        account = await svc.repo.get_score(USER_ID_1)
        account["bambooScore"] = 600  # L3区间
        account["scoreZoneSince"] = _days_ago(59)
        account["repairUntil"] = None
        await svc.repo.save_score(account)

        result = await svc.evaluate_level_transition(USER_ID_1)
        record("test_59_no_upgrade_below_required_days",
               result["levelChanged"] is False,
               f"expected no upgrade(59<60天), got {result}")


class TestQuarterlySettlement:
    """v8.0 季度信用积分结算测试(文档5.1)"""

    async def run(self, svc):
        # 准备: USER_ID_1 升级L3(加成1.0)并产生行为流水
        await svc.get_score(USER_ID_1)
        await svc.upgrade_level(USER_ID_1, LEVEL_L3, "结算测试升级")
        await svc.adjust_score(USER_ID_1, 100, "消费履约")   # earn +100
        await svc.adjust_score(USER_ID_1, -50, "退款扣分")   # deduct -50

        now = datetime.utcnow()
        year, quarter = now.year, (now.month - 1) // 3 + 1

        # test 60: 结算成功且字段完整
        result = await svc.settle_quarter(USER_ID_1, year, quarter)
        record("test_60_settle_success_fields",
               all(k in result for k in ["settlementId", "basePoints",
                   "levelMultiplier", "finalPoints", "creditPointsAfter",
                   "logCount"]),
               f"字段缺失: {result}")

        # test 61: 基础积分=Σ(行为分×权重×时序系数1.5)
        expected_raw = (100 * BEHAVIOR_WEIGHTS[LOG_TYPE_EARN] * QUARTER_TIME_FACTOR
                        + (-50) * BEHAVIOR_WEIGHTS[LOG_TYPE_DEDUCT] * QUARTER_TIME_FACTOR)
        expected_base = max(0, min(QUARTER_POINTS_CAP, round(expected_raw)))
        record("test_61_base_points_formula",
               result["basePoints"] == expected_base,
               f"expected {expected_base}, got {result['basePoints']}")

        # test 62: 等级加成L3×1.0
        record("test_62_level_multiplier_l3",
               result["levelMultiplier"] == LEVEL_REWARD_MULTIPLIER[LEVEL_L3]
               and result["finalPoints"] == round(expected_base * 1.0),
               f"expected {LEVEL_REWARD_MULTIPLIER[LEVEL_L3]}, got {result}")

        # test 63: creditPoints累计入账
        record("test_63_credit_points_accumulated",
               result["creditPointsAfter"] == result["finalPoints"]
               and result["creditPointsAfter"] > 0,
               f"expected {result['finalPoints']}, got {result['creditPointsAfter']}")

        # test 64: 幂等性(同一季度重复结算拒绝)
        try:
            await svc.settle_quarter(USER_ID_1, year, quarter)
            record("test_64_settle_idempotent", False, "应抛出ValueError")
        except ValueError:
            record("test_64_settle_idempotent", True)

        # test 65: 无效季度
        try:
            await svc.settle_quarter(USER_ID_1, year, 5)
            record("test_65_invalid_quarter", False, "应抛出ValueError")
        except ValueError:
            record("test_65_invalid_quarter", True)

        # test 66: 结算记录查询
        settlements = await svc.list_quarterly_settlements(USER_ID_1)
        record("test_66_list_settlements",
               len(settlements) == 1 and settlements[0]["year"] == year
               and settlements[0]["quarter"] == quarter,
               f"expected 1({year}Q{quarter}), got {len(settlements)}")

        # test 67: 结算流水写入
        logs = await svc.list_logs(USER_ID_1, log_type=LOG_TYPE_SEASON_SETTLE)
        record("test_67_settle_log_written",
               len(logs) == 1,
               f"expected 1, got {len(logs)}")

        # test 68: L1等级加成为0 → 最终积分0
        await svc.adjust_score(USER_ID_3, 100, "L1结算测试")  # earn +100
        result = await svc.settle_quarter(USER_ID_3, year, quarter)
        record("test_68_l1_zero_multiplier",
               result["levelMultiplier"] == 0.0 and result["finalPoints"] == 0,
               f"expected 0.0/0, got {result['levelMultiplier']}/{result['finalPoints']}")

        # test 69: 空季度结算(无流水 → 0积分)
        next_year, next_quarter = (year + 1, 1) if quarter == 4 else (year, quarter + 1)
        result = await svc.settle_quarter(USER_ID_1, next_year, next_quarter)
        record("test_69_empty_quarter_zero_points",
               result["logCount"] == 0 and result["basePoints"] == 0
               and result["finalPoints"] == 0,
               f"expected 0/0/0, got {result}")


class TestPaylaterOrder:
    """v8.0 先享后付订单流转测试(文档4.3: AI审批/额度/还款/逾期)"""

    async def run(self, svc):
        # 准备: USER_ID_1会员升级L3(额度2000/单笔1000/月度5000)
        #       USER_ID_2代理商升级L3(B端额度50000/单笔20000/月度50000)
        await svc.get_score(USER_ID_1)
        await svc.upgrade_level(USER_ID_1, LEVEL_L3, "先享后付测试升级")
        await svc.repo.create_score(USER_ID_2, ROLE_AGENT)
        await svc.upgrade_level(USER_ID_2, LEVEL_L3, "B端测试升级")

        # test 70: L1无额度 → 拒绝
        await svc.get_score(USER_ID_3)  # L1
        try:
            await svc.create_paylater_order(USER_ID_3, 100)
            record("test_70_l1_no_quota", False, "应抛出ValueError")
        except ValueError:
            record("test_70_l1_no_quota", True)

        # test 71: 会员角色不可使用B端额度
        try:
            await svc.create_paylater_order(USER_ID_1, 100, PAYLATER_ACCOUNT_B)
            record("test_71_member_cannot_use_b_quota", False, "应抛出ValueError")
        except ValueError:
            record("test_71_member_cannot_use_b_quota", True)

        # test 72: 低风险小额 → 自动通过并占用额度
        order1 = await svc.create_paylater_order(USER_ID_1, 300)
        record("test_72_auto_approved_low_risk",
               order1["status"] == PAYLATER_STATUS_ACTIVE
               and order1["riskLevel"] == "low",
               f"expected active/low, got {order1['status']}/{order1['riskLevel']}")
        quota = await svc.get_paylater_quota(USER_ID_1)
        record("test_73_quota_occupied",
               quota["usedQuota"] == 300 and quota["availableQuota"] == 1700,
               f"expected 300/1700, got {quota['usedQuota']}/{quota['availableQuota']}")

        # test 74: 单笔金额超限(L3会员上限1000)
        try:
            await svc.create_paylater_order(USER_ID_1, 1500)
            record("test_74_single_limit", False, "应抛出ValueError")
        except ValueError:
            record("test_74_single_limit", True)

        # 种子: 本月已有一笔3500已还订单(模拟月度累计3800, 触发>80%预警线4000)
        await _seed_paylater_order(svc, USER_ID_1, 3500, PAYLATER_STATUS_REPAID)

        # test 75: 接近月度上限(>80%) → 转人工审批
        order2 = await svc.create_paylater_order(USER_ID_1, 500)
        record("test_75_review_near_monthly_limit",
               order2["status"] == PAYLATER_STATUS_REVIEW
               and order2["riskLevel"] == "mid"
               and any("月度上限" in f for f in order2["riskFlags"]),
               f"expected review/mid, got {order2}")

        # test 76: 人工审批拒绝 → rejected且不占额度
        result = await svc.review_paylater_order(order2["orderId"], approved=False)
        record("test_76_review_rejected",
               result["status"] == PAYLATER_STATUS_REJECTED,
               f"expected rejected, got {result['status']}")
        quota = await svc.get_paylater_quota(USER_ID_1)
        record("test_77_reject_no_quota_change",
               quota["usedQuota"] == 300,
               f"expected 300, got {quota['usedQuota']}")

        # test 78: 转人工审批订单 → 审批通过占用额度
        order3 = await svc.create_paylater_order(USER_ID_1, 1000)
        record("test_78_review_order_created",
               order3["status"] == PAYLATER_STATUS_REVIEW,
               f"expected review, got {order3['status']}")
        result = await svc.review_paylater_order(order3["orderId"], approved=True)
        record("test_79_review_approved",
               result["status"] == PAYLATER_STATUS_ACTIVE,
               f"expected active, got {result['status']}")
        quota = await svc.get_paylater_quota(USER_ID_1)
        record("test_80_approved_quota_occupied",
               quota["usedQuota"] == 1300,
               f"expected 1300, got {quota['usedQuota']}")

        # test 81: 正常还款(免息期内) → 恢复额度无费用
        repay = await svc.repay_paylater_order(order1["orderId"])
        record("test_81_repay_no_overdue",
               repay["status"] == PAYLATER_STATUS_REPAID
               and repay["overdueDays"] == 0 and repay["overdueFees"] == 0.0
               and repay["repayTotal"] == 300
               and repay["creditPenaltyApplied"] is False,
               f"expected repaid/0/0.0/300, got {repay}")
        quota = await svc.get_paylater_quota(USER_ID_1)
        record("test_82_repay_restores_quota",
               quota["usedQuota"] == 1000,
               f"expected 1000, got {quota['usedQuota']}")

        # test 83: 逾期10天还款 → 逾期费0.035%/天+罚息0.1%/天(7天起)+信用分-20
        order = await svc.repo.get_paylater_order(order3["orderId"])
        order["dueDate"] = _days_ago(10)
        await svc.repo.save_paylater_order(order)

        repay = await svc.repay_paylater_order(order3["orderId"])
        expected_fees = round(1000 * PAYLATER_OVERDUE_DAILY_RATE * 10, 2)
        expected_penalty = round(
            1000 * PAYLATER_OVERDUE_PENALTY_RATE
            * (10 - PAYLATER_PENALTY_START_DAYS + 1), 2)
        record("test_83_overdue_repay_fees",
               repay["overdueDays"] == 10
               and repay["overdueFees"] == expected_fees
               and repay["penaltyFees"] == expected_penalty
               and repay["repayTotal"] == round(1000 + expected_fees + expected_penalty, 2),
               f"expected 10/{expected_fees}/{expected_penalty}, got {repay}")

        account = await svc.get_score(USER_ID_1)
        record("test_84_overdue_credit_penalty",
               account["bambooScore"] == 550 - PAYLATER_OVERDUE_SCORE_PENALTY,
               f"expected {550 - PAYLATER_OVERDUE_SCORE_PENALTY}, "
               f"got {account['bambooScore']}")
        quota = await svc.get_paylater_quota(USER_ID_1)
        record("test_85_overdue_repay_restores_quota",
               quota["usedQuota"] == 0,
               f"expected 0, got {quota['usedQuota']}")

        # test 86: 已还款订单不可重复还款
        try:
            await svc.repay_paylater_order(order3["orderId"])
            record("test_86_repay_twice", False, "应抛出ValueError")
        except ValueError:
            record("test_86_repay_twice", True)

        # test 87: 信用分跌破L3门槛 → AI自动拒绝(订单记录rejected)
        try:
            await svc.create_paylater_order(USER_ID_1, 100)  # 分数530(L2区间)
            record("test_87_auto_reject_low_score", False, "应抛出ValueError")
        except ValueError:
            rejected = await svc.list_paylater_orders(
                USER_ID_1, status=PAYLATER_STATUS_REJECTED)
            record("test_87_auto_reject_low_score",
                   any(o.get("riskLevel") == "high" for o in rejected),
                   f"应有high风险rejected订单, got {len(rejected)}条")

        # test 88: 会员订单列表与状态筛选
        orders = await svc.list_paylater_orders(USER_ID_1)
        rejected = await svc.list_paylater_orders(
            USER_ID_1, status=PAYLATER_STATUS_REJECTED)
        repaid = await svc.list_paylater_orders(
            USER_ID_1, status=PAYLATER_STATUS_REPAID)
        record("test_88_list_orders_filter",
               len(orders) == 5 and len(rejected) == 2 and len(repaid) == 3,
               f"expected 5/2/3, got {len(orders)}/{len(rejected)}/{len(repaid)}")

        # test 89: 先享后付下单/还款流水写入
        order_logs = await svc.list_logs(USER_ID_1, log_type=LOG_TYPE_PAYLATER_ORDER)
        repay_logs = await svc.list_logs(USER_ID_1, log_type=LOG_TYPE_PAYLATER_REPAY)
        record("test_89_paylater_logs",
               len(order_logs) >= 5 and len(repay_logs) == 2,
               f"expected ≥5/2, got {len(order_logs)}/{len(repay_logs)}")

        # ---- B端额度流转 ----
        # test 90: B端代理商低风险下单自动通过
        b_order = await svc.create_paylater_order(
            USER_ID_2, 20000, PAYLATER_ACCOUNT_B, source="agent_purchase")
        record("test_90_b_order_auto_approved",
               b_order["status"] == PAYLATER_STATUS_ACTIVE
               and b_order["accountType"] == PAYLATER_ACCOUNT_B,
               f"expected active/b, got {b_order['status']}/{b_order['accountType']}")

        # 种子: B端本月已还20000(月度累计40000, 触发>80%预警线40000)
        await _seed_paylater_order(svc, USER_ID_2, 20000,
                                   PAYLATER_STATUS_REPAID, PAYLATER_ACCOUNT_B)

        # test 91: B端接近月度上限 → 转人工审批
        b_order2 = await svc.create_paylater_order(USER_ID_2, 5000, PAYLATER_ACCOUNT_B)
        record("test_91_b_review_near_monthly_limit",
               b_order2["status"] == PAYLATER_STATUS_REVIEW,
               f"expected review, got {b_order2['status']}")

        # test 92: B端审批通过占用B端额度
        result = await svc.review_paylater_order(b_order2["orderId"], approved=True)
        account = await svc.get_score(USER_ID_2)
        record("test_92_b_quota_occupied",
               result["status"] == PAYLATER_STATUS_ACTIVE
               and account["bPaylaterUsed"] == 25000,
               f"expected active/25000, got {result['status']}/{account['bPaylaterUsed']}")

        # test 93: B端还款恢复B端额度
        await svc.repay_paylater_order(b_order["orderId"])
        account = await svc.get_score(USER_ID_2)
        record("test_93_b_repay_restores_quota",
               account["bPaylaterUsed"] == 5000,
               f"expected 5000, got {account['bPaylaterUsed']}")

        # test 94: B端月度累计超限 → 拒绝(45000+10000 > 50000)
        try:
            await svc.create_paylater_order(USER_ID_2, 10000, PAYLATER_ACCOUNT_B)
            record("test_94_b_monthly_limit", False, "应抛出ValueError")
        except ValueError:
            record("test_94_b_monthly_limit", True)


class TestExchangeRewards:
    """v8.0 季度奖励兑换测试(文档5.2: 现金/商品/权益/组合+个税+上限)"""

    async def run(self, svc):
        await svc.get_score(USER_ID_1)

        # test 95: 无效兑换类型
        try:
            await svc.exchange_rewards(USER_ID_1, "crypto", 100)
            record("test_95_invalid_type", False, "应抛出ValueError")
        except ValueError:
            record("test_95_invalid_type", True)

        # test 96: 兑换积分须大于0
        try:
            await svc.exchange_rewards(USER_ID_1, "cash", 0)
            record("test_96_zero_points", False, "应抛出ValueError")
        except ValueError:
            record("test_96_zero_points", True)

        # test 97: 积分不足
        await _set_credit_points(svc, USER_ID_1, 100)
        try:
            await svc.exchange_rewards(USER_ID_1, "cash", 500)
            record("test_97_insufficient_points", False, "应抛出ValueError")
        except ValueError:
            record("test_97_insufficient_points", True)

        # test 98: 现金兑换≤¥800免税(100积分=¥1)
        await _set_credit_points(svc, USER_ID_1, 50000)
        result = await svc.exchange_rewards(USER_ID_1, "cash", 50000)
        record("test_98_cash_tax_free",
               result["value"] == 500.0 and result["tax"] == 0.0
               and result["netValue"] == 500.0,
               f"expected 500/0/500, got {result['value']}/{result['tax']}/{result['netValue']}")

        # test 99: 现金兑换超¥800部分扣20%个税(¥1000 → 税40)
        await _set_credit_points(svc, USER_ID_1, 100000)
        result = await svc.exchange_rewards(USER_ID_1, "cash", 100000)
        expected_tax = round((1000 - CASH_TAX_FREE_AMOUNT) * CASH_TAX_RATE, 2)
        record("test_99_cash_taxed",
               result["value"] == 1000.0 and result["tax"] == expected_tax
               and result["netValue"] == round(1000 - expected_tax, 2),
               f"expected 1000/{expected_tax}, got {result}")

        # test 100: 商品兑换按目录价(3000积分=陶瓷酒杯¥30, 无税收)
        await _set_credit_points(svc, USER_ID_1, 5000)
        result = await svc.exchange_rewards(USER_ID_1, "goods", 3000, "G102")
        record("test_100_goods_exchange",
               result["value"] == 30.0 and result["tax"] == 0.0
               and result["itemName"] == EXCHANGE_CATALOG["G102"]["name"],
               f"expected 30/0/{EXCHANGE_CATALOG['G102']['name']}, got {result}")

        # test 101: 商品积分与目录价不符
        try:
            await svc.exchange_rewards(USER_ID_1, "goods", 2000, "G102")
            record("test_101_goods_points_mismatch", False, "应抛出ValueError")
        except ValueError:
            record("test_101_goods_points_mismatch", True)

        # test 102: 无效目录ID
        try:
            await svc.exchange_rewards(USER_ID_1, "goods", 3000, "X999")
            record("test_102_invalid_item", False, "应抛出ValueError")
        except ValueError:
            record("test_102_invalid_item", True)

        # test 103: 兑换类型与目录分类不符(goods目录ID用于benefit)
        try:
            await svc.exchange_rewards(USER_ID_1, "benefit", 3000, "G102")
            record("test_103_category_mismatch", False, "应抛出ValueError")
        except ValueError:
            record("test_103_category_mismatch", True)

        # test 104: 会员不可兑换B端专属权益(B101仅B端角色)
        await _set_credit_points(svc, USER_ID_1, 60000)
        try:
            await svc.exchange_rewards(USER_ID_1, "benefit", 50000, "B101")
            record("test_104_member_cannot_buy_b_benefit", False, "应抛出ValueError")
        except ValueError:
            record("test_104_member_cannot_buy_b_benefit", True)

        # test 105: 组合兑换(100积分=¥1.3, 现金性质50%≤800免税)
        await _set_credit_points(svc, USER_ID_1, 100000)
        result = await svc.exchange_rewards(USER_ID_1, "combo", 100000)
        expected_value = round(100000 / 100.0 * EXCHANGE_RATES["combo"], 2)
        record("test_105_combo_exchange",
               result["value"] == expected_value and result["tax"] == 0.0,
               f"expected {expected_value}/0, got {result['value']}/{result['tax']}")

        # test 106: 季度现金上限边界(已兑500+1000, 本次3500 → 恰好5000)
        await _set_credit_points(svc, USER_ID_1, 350000)
        result = await svc.exchange_rewards(USER_ID_1, "cash", 350000)
        record("test_106_cash_cap_boundary",
               result["value"] == 3500.0,
               f"expected 3500, got {result['value']}")

        # test 107: 超季度现金上限拒绝(5000+1000 > 5000)
        await _set_credit_points(svc, USER_ID_1, 100000)
        try:
            await svc.exchange_rewards(USER_ID_1, "cash", 100000)
            record("test_107_cash_cap_exceeded", False, "应抛出ValueError")
        except ValueError:
            record("test_107_cash_cap_exceeded", True)

        # test 108: B端角色可兑换B端专属权益
        await svc.repo.create_score(USER_ID_2, ROLE_AGENT)
        await _set_credit_points(svc, USER_ID_2, 50000)
        result = await svc.exchange_rewards(USER_ID_2, "benefit", 50000, "B101")
        record("test_108_b_role_benefit_exchange",
               result["value"] == 500.0
               and result["itemName"] == EXCHANGE_CATALOG["B101"]["name"],
               f"expected 500/{EXCHANGE_CATALOG['B101']['name']}, got {result}")

        # test 109: 兑换记录查询与类型筛选
        records = await svc.list_exchanges(USER_ID_1)
        cash_records = await svc.list_exchanges(USER_ID_1, "cash")
        record("test_109_list_exchanges",
               len(records) == 5 and len(cash_records) == 3,
               f"expected 5/3, got {len(records)}/{len(cash_records)}")

        # test 110: 兑换流水写入
        logs = await svc.list_logs(USER_ID_1, log_type=LOG_TYPE_EXCHANGE)
        record("test_110_exchange_log_written",
               len(logs) == 5,
               f"expected 5, got {len(logs)}")


class TestExchangeRecommend:
    """v8.0 AI兑换方案推荐测试(文档5.3: Top3方案+推荐理由)"""

    async def run(self, svc):
        # test 111: 推荐3个方案且默认推荐方案1
        await svc.get_score(USER_ID_1)
        await _set_credit_points(svc, USER_ID_1, 6000)
        result = await svc.recommend_exchange(USER_ID_1)
        record("test_111_three_plans",
               len(result["plans"]) == 3 and result["recommendedPlanNo"] == 1,
               f"expected 3 plans, got {len(result['plans'])}")

        # test 112: 方案1为可负担的最高价值目录项(6000积分 → G001竹香酒¥60)
        plan1 = result["plans"][0]
        record("test_112_plan1_best_affordable",
               plan1["itemId"] == "G001" and plan1["netValue"] == 60.0
               and plan1["points"] == 5000,
               f"expected G001/60/5000, got {plan1}")

        # test 113: 方案2为全额现金(6000积分=¥60, ≤800免税)
        plan2 = result["plans"][1]
        record("test_113_plan2_full_cash",
               plan2["type"] == "cash" and plan2["value"] == 60.0
               and plan2["tax"] == 0.0,
               f"expected cash/60/0, got {plan2}")

        # test 114: 方案3为目标商品+补差价(缺G101(8000分)2000分 → ¥20)
        plan3 = result["plans"][2]
        record("test_114_plan3_target_with_supplement",
               plan3["itemId"] == "G101" and plan3["supplementCash"] == 20.0,
               f"expected G101/20.0, got {plan3}")

        # test 115: 大额现金推荐含个税提示(100000积分=¥1000 → 税40)
        await _set_credit_points(svc, USER_ID_1, 100000)
        result = await svc.recommend_exchange(USER_ID_1)
        plan2 = result["plans"][1]
        expected_tax = round((1000 - CASH_TAX_FREE_AMOUNT) * CASH_TAX_RATE, 2)
        record("test_115_plan2_tax_detail",
               plan2["value"] == 1000.0 and plan2["tax"] == expected_tax,
               f"expected 1000/{expected_tax}, got {plan2}")

        # test 116: 零积分用户仍可推荐(方案1含补差价)
        await svc.get_score(USER_ID_3)  # 0积分
        result = await svc.recommend_exchange(USER_ID_3)
        plan1 = result["plans"][0]
        record("test_116_zero_points_recommendation",
               result["creditPoints"] == 0 and plan1["supplementCash"] > 0,
               f"expected supplement>0, got {plan1}")

        # test 117: 积分可兑全部目录时方案3为组合兑换
        await _set_credit_points(svc, USER_ID_1, 999999)
        result = await svc.recommend_exchange(USER_ID_1)
        plan3 = result["plans"][2]
        record("test_117_rich_points_combo_plan",
               plan3["type"] == "combo" and plan3["supplementCash"] == 0.0,
               f"expected combo/0, got {plan3}")


# ============================================================
# 测试辅助
# ============================================================

def _days_ago(days: int) -> str:
    """N天前的ISO时间戳"""
    return (datetime.utcnow() - timedelta(days=days)).isoformat()


def _days_between_now(future_iso: str) -> int:
    """现在到未来ISO时间的天数"""
    delta = datetime.fromisoformat(future_iso) - datetime.utcnow()
    return max(0, int(delta.total_seconds() // 86400))


async def _seed_paylater_order(svc, user_id: int, amount: float,
                               status: str, account_type: str = "member") -> None:
    """种子先享后付订单(模拟历史月度累计, 不占用账户额度)"""
    now = datetime.utcnow().isoformat()
    await svc.repo.add_paylater_order({
        "userId": user_id,
        "orderNo": f"SEED{user_id}{amount}",
        "accountType": account_type,
        "source": "order",
        "amount": amount,
        "status": status,
        "creditLevelAtCreate": LEVEL_L3,
        "interestFreeDays": 15,
        "dueDate": now,
        "riskLevel": "low",
        "riskFlags": [],
    })


async def _set_credit_points(svc, user_id: int, points: int) -> None:
    """直接设置账户信用积分(测试前置)"""
    account = await svc.repo.get_or_create_score(user_id, ROLE_MEMBER)
    account["creditPoints"] = points
    await svc.repo.save_score(account)


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
        TestLevelEvaluation,
        TestQuarterlySettlement,
        TestPaylaterOrder,
        TestExchangeRewards,
        TestExchangeRecommend,
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
