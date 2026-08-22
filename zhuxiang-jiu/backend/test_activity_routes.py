"""活动管理模块端到端测试(Service 层, 无需 Docker/fastapi)

直接调用 ActivityService 方法, 模拟 12 个 HTTP 接口的业务逻辑。
使用 asyncio 内存模式(LOCK_MODE=asyncio, STORE_MODE=asyncio)。

运行:
    $env:LOCK_MODE="asyncio"; $env:STORE_MODE="asyncio"
    python test_activity_routes.py

覆盖 12 个接口对应的业务方法:
    1. 用户端(6): list_activities / get_activity / register / cancel_registration
                  / get_leaderboard / get_stats
    2. 管理端(6): create_activity / transition_status / audit_activity
                  / list_admin_activities / submit_arena_score / list_registrations
"""

import asyncio
import os
import sys

os.environ["LOCK_MODE"] = "asyncio"
os.environ["STORE_MODE"] = "asyncio"

from services.activity_service import ActivityService
from repositories.activity_repository import (
    ActivityRepository,
    # 活动类型
    TYPE_PROMOTION, TYPE_LOTTERY, TYPE_COMPETITION, TYPE_ARENA,
    TYPE_INTERACTIVE, TYPE_GROUPBUY, TYPE_SECKILL, TYPE_PRESALE,
    # 擂台赛类型
    ARENA_L01, ARENA_L02, ARENA_L03, ARENA_L04,
    ARENA_L05, ARENA_L06, ARENA_L07, ARENA_L08,
    # 状态机
    STATUS_DRAFT, STATUS_REGISTERING, STATUS_ONGOING, STATUS_ENDED, STATUS_CANCELLED,
    can_transition,
    # 报名状态
    REG_STATUS_REGISTERED, REG_STATUS_CANCELLED,
)
from repositories.store import _mock_store, reset_store as _reset_store_impl

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
    _reset_store_impl()


# ============================================================
# 测试数据
# ============================================================

USER_ID_1 = 20001
USER_ID_2 = 20002
USER_ID_3 = 20003
ADMIN_ID = 1


# ============================================================
# 测试用例
# ============================================================

class TestCreateActivity:
    """创建活动测试"""

    async def run(self, svc):
        # test 1: 创建草稿活动(促销)
        result = await svc.create_activity(
            name="竹香仲夏满减季",
            type_=TYPE_PROMOTION,
            sub_type="FULL_CUT",
            description="满200减30",
            budget=10000.0,
            created_by=ADMIN_ID,
        )
        record("test_01_create_promotion",
               result["status"] == STATUS_DRAFT and result["type"] == TYPE_PROMOTION,
               f"expected {STATUS_DRAFT}/{TYPE_PROMOTION}, got {result['status']}/{result['type']}")

        # test 2: 活动编号格式正确
        record("test_02_activity_no_format",
               result["activityNo"].startswith("HD") and len(result["activityNo"]) > 2,
               f"activityNo={result['activityNo']}")

        # test 3: 创建擂台赛活动(L01引流)
        result = await svc.create_activity(
            name="Q1引流擂台赛",
            type_=TYPE_ARENA,
            sub_type=ARENA_L01,
            description="春节引流季·谁是引流王",
            budget=50000.0,
            created_by=ADMIN_ID,
        )
        record("test_03_create_arena_l01",
               result["type"] == TYPE_ARENA and result["subType"] == ARENA_L01,
               f"expected arena/{ARENA_L01}, got {result['type']}/{result['subType']}")

        # test 4: 无效类型创建失败
        try:
            await svc.create_activity(name="无效", type_="invalid_type")
            record("test_04_invalid_type", False, "应抛出ValueError")
        except ValueError:
            record("test_04_invalid_type", True)

        # test 5: 创建抽奖活动
        result = await svc.create_activity(
            name="竹香春节抽奖",
            type_=TYPE_LOTTERY,
            description="转盘抽奖",
            budget=20000.0,
        )
        record("test_05_create_lottery",
               result["type"] == TYPE_LOTTERY,
               f"expected {TYPE_LOTTERY}, got {result['type']}")


class TestQueryActivity:
    """查询活动测试"""

    async def run(self, svc):
        # 准备: 创建活动(草稿/报名中)
        activity1 = await svc.create_activity(
            name="草稿活动", type_=TYPE_PROMOTION, created_by=ADMIN_ID
        )
        activity2 = await svc.create_activity(
            name="报名中活动", type_=TYPE_LOTTERY, created_by=ADMIN_ID
        )
        await svc.transition_status(activity2["id"], STATUS_REGISTERING, ADMIN_ID)

        # test 6: 查询活动详情
        result = await svc.get_activity(activity2["id"])
        record("test_06_get_activity",
               result["id"] == activity2["id"] and result["status"] == STATUS_REGISTERING,
               f"expected {activity2['id']}/{STATUS_REGISTERING}, got {result['id']}/{result['status']}")

        # test 7: 不存在的活动查询失败(404)
        try:
            await svc.get_activity(99999)
            record("test_07_get_nonexistent", False, "应抛出KeyError")
        except KeyError:
            record("test_07_get_nonexistent", True)

        # test 8: 列表默认不返回草稿
        activities = await svc.list_activities()
        draft_in_list = any(a["id"] == activity1["id"] for a in activities)
        record("test_08_list_excludes_draft",
               not draft_in_list,
               "列表不应包含草稿活动")

        # test 9: 列表按状态筛选
        registering = await svc.list_activities(status=STATUS_REGISTERING)
        all_registering = all(a["status"] == STATUS_REGISTERING for a in registering)
        record("test_09_filter_by_status",
               all_registering and any(a["id"] == activity2["id"] for a in registering),
               "按状态筛选失败")

        # test 10: 管理端列表包含草稿
        admin_list = await svc.list_admin_activities()
        draft_in_admin = any(a["id"] == activity1["id"] for a in admin_list)
        record("test_10_admin_list_includes_draft",
               draft_in_admin,
               "管理端列表应包含草稿")

        # test 11: 列表按类型筛选
        lotteries = await svc.list_activities(type_=TYPE_LOTTERY)
        all_lottery = all(a["type"] == TYPE_LOTTERY for a in lotteries)
        record("test_11_filter_by_type",
               all_lottery,
               "按类型筛选失败")


class TestRegister:
    """活动报名测试"""

    async def run(self, svc):
        # 准备: 创建活动并流转至报名中
        activity = await svc.create_activity(
            name="报名测试活动", type_=TYPE_INTERACTIVE, created_by=ADMIN_ID
        )
        await svc.transition_status(activity["id"], STATUS_REGISTERING, ADMIN_ID)

        # test 12: 正常报名
        result = await svc.register(activity["id"], USER_ID_1, {"channel": "wechat"})
        record("test_12_normal_register",
               result["status"] == REG_STATUS_REGISTERED and result["userId"] == USER_ID_1,
               f"expected registered/{USER_ID_1}, got {result['status']}/{result['userId']}")

        # test 13: 重复报名幂等失败
        try:
            await svc.register(activity["id"], USER_ID_1)
            record("test_13_duplicate_register", False, "应抛出ValueError")
        except ValueError:
            record("test_13_duplicate_register", True)

        # test 14: 第二个用户报名
        result = await svc.register(activity["id"], USER_ID_2)
        record("test_14_second_user_register",
               result["status"] == REG_STATUS_REGISTERED,
               f"expected registered, got {result['status']}")

        # test 15: 取消报名
        result = await svc.cancel_registration(activity["id"], USER_ID_1)
        record("test_15_cancel_registration",
               result["status"] == REG_STATUS_CANCELLED,
               f"expected {REG_STATUS_CANCELLED}, got {result['status']}")

        # test 16: 重复取消失败
        try:
            await svc.cancel_registration(activity["id"], USER_ID_1)
            record("test_16_duplicate_cancel", False, "应抛出ValueError")
        except ValueError:
            record("test_16_duplicate_cancel", True)

        # test 17: 取消后可重新报名
        result = await svc.register(activity["id"], USER_ID_1, {"re_register": True})
        record("test_17_re_register_after_cancel",
               result["status"] == REG_STATUS_REGISTERED,
               f"expected registered, got {result['status']}")

        # test 18: 草稿状态不可报名
        draft = await svc.create_activity(
            name="草稿不可报名", type_=TYPE_PROMOTION, created_by=ADMIN_ID
        )
        try:
            await svc.register(draft["id"], USER_ID_1)
            record("test_18_draft_cannot_register", False, "应抛出ValueError")
        except ValueError:
            record("test_18_draft_cannot_register", True)

        # test 19: 不存在的活动报名失败
        try:
            await svc.register(99999, USER_ID_1)
            record("test_19_register_nonexistent", False, "应抛出KeyError")
        except KeyError:
            record("test_19_register_nonexistent", True)

        # test 20: 取消不存在的报名记录失败
        try:
            await svc.cancel_registration(activity["id"], 99999)
            record("test_20_cancel_nonexistent", False, "应抛出KeyError")
        except KeyError:
            record("test_20_cancel_nonexistent", True)


class TestStatusTransition:
    """状态流转测试"""

    async def run(self, svc):
        # test 21: 正常流转 草稿→报名中→进行中→已结束
        activity = await svc.create_activity(
            name="状态流转测试", type_=TYPE_PROMOTION, created_by=ADMIN_ID
        )
        r1 = await svc.transition_status(activity["id"], STATUS_REGISTERING, ADMIN_ID)
        record("test_21_draft_to_registering",
               r1["statusAfter"] == STATUS_REGISTERING,
               f"expected {STATUS_REGISTERING}, got {r1['statusAfter']}")
        r2 = await svc.transition_status(activity["id"], STATUS_ONGOING, ADMIN_ID)
        record("test_22_registering_to_ongoing",
               r2["statusAfter"] == STATUS_ONGOING,
               f"expected {STATUS_ONGOING}, got {r2['statusAfter']}")
        r3 = await svc.transition_status(activity["id"], STATUS_ENDED, ADMIN_ID)
        record("test_23_ongoing_to_ended",
               r3["statusAfter"] == STATUS_ENDED,
               f"expected {STATUS_ENDED}, got {r3['statusAfter']}")

        # test 24: 已结束不可再流转
        try:
            await svc.transition_status(activity["id"], STATUS_ONGOING, ADMIN_ID)
            record("test_24_ended_cannot_revert", False, "应抛出ValueError")
        except ValueError:
            record("test_24_ended_cannot_revert", True)

        # test 25: 跳跃状态失败(草稿→进行中)
        activity2 = await svc.create_activity(
            name="跳跃状态", type_=TYPE_PROMOTION, created_by=ADMIN_ID
        )
        try:
            await svc.transition_status(activity2["id"], STATUS_ONGOING, ADMIN_ID)
            record("test_25_skip_status", False, "应抛出ValueError")
        except ValueError:
            record("test_25_skip_status", True)

        # test 26: 取消活动(草稿→已取消)
        activity3 = await svc.create_activity(
            name="取消活动", type_=TYPE_PROMOTION, created_by=ADMIN_ID
        )
        result = await svc.transition_status(activity3["id"], STATUS_CANCELLED, ADMIN_ID)
        record("test_26_cancel_activity",
               result["statusAfter"] == STATUS_CANCELLED,
               f"expected {STATUS_CANCELLED}, got {result['statusAfter']}")

        # test 27: 不存在的活动状态流转失败
        try:
            await svc.transition_status(99999, STATUS_REGISTERING, ADMIN_ID)
            record("test_27_transition_nonexistent", False, "应抛出KeyError")
        except KeyError:
            record("test_27_transition_nonexistent", True)


class TestAudit:
    """活动审核测试"""

    async def run(self, svc):
        # test 28: 审核通过(草稿→报名中)
        activity = await svc.create_activity(
            name="审核通过测试", type_=TYPE_PROMOTION, created_by=ADMIN_ID
        )
        result = await svc.audit_activity(activity["id"], True, ADMIN_ID, "符合活动规范")
        record("test_28_audit_approve",
               result["approved"] is True and result["status"] == STATUS_REGISTERING,
               f"expected True/{STATUS_REGISTERING}, got {result['approved']}/{result['status']}")

        # test 29: 审核拒绝(草稿→已取消)
        activity2 = await svc.create_activity(
            name="审核拒绝测试", type_=TYPE_PROMOTION, created_by=ADMIN_ID
        )
        result = await svc.audit_activity(activity2["id"], False, ADMIN_ID, "活动违规")
        record("test_29_audit_reject",
               result["approved"] is False and result["status"] == STATUS_CANCELLED,
               f"expected False/{STATUS_CANCELLED}, got {result['approved']}/{result['status']}")

        # test 30: 非草稿状态不可审核
        activity3 = await svc.create_activity(
            name="已审核活动", type_=TYPE_PROMOTION, created_by=ADMIN_ID
        )
        await svc.audit_activity(activity3["id"], True, ADMIN_ID)
        try:
            await svc.audit_activity(activity3["id"], True, ADMIN_ID)
            record("test_30_audit_non_draft", False, "应抛出ValueError")
        except ValueError:
            record("test_30_audit_non_draft", True)

        # test 31: 不存在的活动审核失败
        try:
            await svc.audit_activity(99999, True, ADMIN_ID)
            record("test_31_audit_nonexistent", False, "应抛出KeyError")
        except KeyError:
            record("test_31_audit_nonexistent", True)


class TestArenaLeaderboard:
    """擂台赛排名测试"""

    async def run(self, svc):
        # 准备: 创建擂台赛并流转至进行中
        activity = await svc.create_activity(
            name="Q1引流擂台赛", type_=TYPE_ARENA, sub_type=ARENA_L01,
            budget=50000.0, created_by=ADMIN_ID
        )
        await svc.transition_status(activity["id"], STATUS_REGISTERING, ADMIN_ID)
        await svc.transition_status(activity["id"], STATUS_ONGOING, ADMIN_ID)

        # test 32: 提交擂台赛分数
        result = await svc.submit_arena_score(
            activity["id"], USER_ID_1, 85.5, "张三"
        )
        record("test_32_submit_score",
               result["score"] == 85.5 and result["rank"] == 1,
               f"expected 85.5/1, got {result['score']}/{result['rank']}")

        # test 33: 第二个用户提交分数(更高分排第一)
        result = await svc.submit_arena_score(
            activity["id"], USER_ID_2, 95.0, "李四"
        )
        record("test_33_submit_higher_score",
               result["rank"] == 1 and result["totalParticipants"] == 2,
               f"expected 1/2, got {result['rank']}/{result['totalParticipants']}")

        # test 34: 排名正确(李四第一, 张三第二)
        leaderboard = await svc.get_leaderboard(activity["id"])
        record("test_34_leaderboard_order",
               leaderboard[0]["userId"] == USER_ID_2 and leaderboard[0]["rank"] == 1
               and leaderboard[1]["userId"] == USER_ID_1 and leaderboard[1]["rank"] == 2,
               "排名顺序错误")

        # test 35: 更新已有分数(张三分数超过李四)
        result = await svc.submit_arena_score(
            activity["id"], USER_ID_1, 98.0, "张三"
        )
        leaderboard = await svc.get_leaderboard(activity["id"])
        record("test_35_update_score_reorder",
               leaderboard[0]["userId"] == USER_ID_1 and leaderboard[0]["score"] == 98.0,
               "更新分数后排名未调整")

        # test 36: 非擂台赛活动查询排名失败
        promo = await svc.create_activity(
            name="促销活动", type_=TYPE_PROMOTION, created_by=ADMIN_ID
        )
        try:
            await svc.get_leaderboard(promo["id"])
            record("test_36_non_arena_leaderboard", False, "应抛出ValueError")
        except ValueError:
            record("test_36_non_arena_leaderboard", True)

        # test 37: 非擂台赛活动提交分数失败
        try:
            await svc.submit_arena_score(promo["id"], USER_ID_1, 50.0)
            record("test_37_submit_score_non_arena", False, "应抛出ValueError")
        except ValueError:
            record("test_37_submit_score_non_arena", True)

        # test 38: 已结束的擂台赛不可提交
        await svc.transition_status(activity["id"], STATUS_ENDED, ADMIN_ID)
        try:
            await svc.submit_arena_score(activity["id"], USER_ID_3, 60.0, "王五")
            record("test_38_submit_after_ended", False, "应抛出ValueError")
        except ValueError:
            record("test_38_submit_after_ended", True)


class TestStats:
    """活动统计测试"""

    async def run(self, svc):
        # 准备: 创建擂台赛并报名+提交分数
        activity = await svc.create_activity(
            name="统计测试擂台赛", type_=TYPE_ARENA, sub_type=ARENA_L03,
            budget=30000.0, created_by=ADMIN_ID
        )
        await svc.transition_status(activity["id"], STATUS_REGISTERING, ADMIN_ID)
        await svc.register(activity["id"], USER_ID_1)
        await svc.register(activity["id"], USER_ID_2)
        await svc.cancel_registration(activity["id"], USER_ID_2)
        await svc.transition_status(activity["id"], STATUS_ONGOING, ADMIN_ID)
        await svc.submit_arena_score(activity["id"], USER_ID_1, 90.0, "张三")

        # test 39: 统计字段完整
        stats = await svc.get_stats(activity["id"])
        record("test_39_stats_fields",
               all(k in stats for k in ["budget", "usedBudget", "registrationCount",
                   "cancelledCount", "leaderboardCount"]),
               "统计字段缺失")

        # test 40: 统计数值正确
        record("test_40_stats_correct",
               stats["registrationCount"] == 1 and stats["cancelledCount"] == 1
               and stats["leaderboardCount"] == 1,
               f"expected 1/1/1, got {stats['registrationCount']}/{stats['cancelledCount']}/{stats['leaderboardCount']}")

        # test 41: 预算使用率
        record("test_41_budget_usage_rate",
               stats["budget"] == 30000.0 and stats["budgetUsageRate"] == 0.0,
               f"expected 30000.0/0.0, got {stats['budget']}/{stats['budgetUsageRate']}")

        # test 42: 不存在的活动统计失败
        try:
            await svc.get_stats(99999)
            record("test_42_stats_nonexistent", False, "应抛出KeyError")
        except KeyError:
            record("test_42_stats_nonexistent", True)


class TestStateMachine:
    """状态机规则测试"""

    async def run(self, svc):
        # test 43: 状态机规则函数
        record("test_43_can_transition_draft_to_registering",
               can_transition(STATUS_DRAFT, STATUS_REGISTERING) is True,
               "草稿→报名中应允许")
        record("test_44_can_transition_draft_to_ongoing",
               can_transition(STATUS_DRAFT, STATUS_ONGOING) is False,
               "草稿→进行中应禁止")
        record("test_45_can_transition_ended_to_ongoing",
               can_transition(STATUS_ENDED, STATUS_ONGOING) is False,
               "已结束→进行中应禁止")
        record("test_46_can_transition_cancelled_to_ongoing",
               can_transition(STATUS_CANCELLED, STATUS_ONGOING) is False,
               "已取消→进行中应禁止")
        record("test_47_can_transition_registering_to_ongoing",
               can_transition(STATUS_REGISTERING, STATUS_ONGOING) is True,
               "报名中→进行中应允许")
        record("test_48_can_transition_ongoing_to_ended",
               can_transition(STATUS_ONGOING, STATUS_ENDED) is True,
               "进行中→已结束应允许")


# ============================================================
# 测试运行
# ============================================================

async def main():
    print("=" * 60)
    print("活动管理模块端到端测试")
    print("=" * 60)
    print()

    test_classes = [
        TestCreateActivity,
        TestQueryActivity,
        TestRegister,
        TestStatusTransition,
        TestAudit,
        TestArenaLeaderboard,
        TestStats,
        TestStateMachine,
    ]

    for cls in test_classes:
        reset_store()
        svc = ActivityService()
        print(f"[{cls.__name__}]")
        instance = cls()
        await instance.run(svc)
        print()

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
