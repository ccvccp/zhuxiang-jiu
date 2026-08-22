"""流量管理模块端到端测试(Service 层, 无需 Docker/fastapi)

直接调用 TrafficService 方法, 模拟 10 个 HTTP 接口的业务逻辑。
使用 asyncio 内存模式(LOCK_MODE=asyncio, STORE_MODE=asyncio)。

运行:
    $env:LOCK_MODE="asyncio"; $env:STORE_MODE="asyncio"
    python test_traffic_routes.py

覆盖 10 个接口对应的业务方法:
    1. 用户端(5): create_promoter / get_promoter / record_lead / get_stats / get_promoter_level
    2. 管理端(5): calculate_commission / get_fission_tree / create_source
                  / distribute_traffic / get_admin_stats
"""

import asyncio
import os
import sys

os.environ["LOCK_MODE"] = "asyncio"
os.environ["STORE_MODE"] = "asyncio"

from services.traffic_service import TrafficService
from repositories.traffic_repository import (
    TrafficRepository,
    # 流量来源
    SOURCE_DOUYIN, SOURCE_KUAISHOU, SOURCE_WECHAT, SOURCE_DIRECT,
    # 引流方式
    MEDIUM_VIDEO, MEDIUM_LIVE, MEDIUM_SHARE, MEDIUM_AD,
    # 推广员等级
    LEVEL_TRAINEE, LEVEL_JUNIOR, LEVEL_INTERMEDIATE, LEVEL_SENIOR, LEVEL_GOLD,
    LEVEL_COMMISSION_RATE, LEVEL_UPGRADE_CONDITIONS, LEVEL_EXTRA_REWARD,
    LEVEL_RANK,
    # 推广员状态
    PROMOTER_STATUS_ACTIVE, PROMOTER_STATUS_PAUSED, PROMOTER_STATUS_BANNED,
    # 引流记录状态
    LEAD_STATUS_PENDING, LEAD_STATUS_REGISTERED, LEAD_STATUS_ORDERED,
    LEAD_STATUS_INVALID, LEAD_EFFECTIVE_TRUE, LEAD_EFFECTIVE_FALSE,
    # 佣金状态
    COMMISSION_PENDING, COMMISSION_SETTLED, COMMISSION_WITHDRAWN,
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

USER_ID_1 = 30001
USER_ID_2 = 30002
USER_ID_3 = 30003
ORDER_ID_1 = "ORD20260822001"
ORDER_ID_2 = "ORD20260822002"


# ============================================================
# 测试用例
# ============================================================

class TestCreatePromoter:
    """创建推广员测试"""

    async def run(self, svc):
        # test 1: 创建见习推广员
        result = await svc.create_promoter(USER_ID_1, "张三", LEVEL_TRAINEE)
        record("test_01_create_trainee",
               result["level"] == LEVEL_TRAINEE and result["commissionRate"] == 0.05,
               f"expected {LEVEL_TRAINEE}/0.05, got {result['level']}/{result['commissionRate']}")

        # test 2: 推广码格式正确
        record("test_02_promoter_code_format",
               result["promoterCode"].startswith("P"),
               f"promoterCode={result['promoterCode']}")

        # test 3: 创建金牌推广员(15%佣金)
        result = await svc.create_promoter(USER_ID_2, "李四", LEVEL_GOLD)
        record("test_03_create_gold",
               result["level"] == LEVEL_GOLD and result["commissionRate"] == 0.15,
               f"expected {LEVEL_GOLD}/0.15, got {result['level']}/{result['commissionRate']}")

        # test 4: 无效等级创建失败
        try:
            await svc.create_promoter(USER_ID_3, "王五", "invalid_level")
            record("test_04_invalid_level", False, "应抛出ValueError")
        except ValueError:
            record("test_04_invalid_level", True)

        # test 5: 创建带上级推广员(裂变)
        result = await svc.create_promoter(USER_ID_3, "王五", LEVEL_JUNIOR,
                                            parent_promoter_id=1)
        record("test_05_with_parent",
               result["parentPromoterId"] == 1,
               f"expected parent=1, got {result['parentPromoterId']}")

        # test 6: 等级佣金比例映射正确
        record("test_06_commission_rate_map",
               LEVEL_COMMISSION_RATE[LEVEL_JUNIOR] == 0.08
               and LEVEL_COMMISSION_RATE[LEVEL_INTERMEDIATE] == 0.10
               and LEVEL_COMMISSION_RATE[LEVEL_SENIOR] == 0.12,
               "佣金比例映射错误")


class TestQueryPromoter:
    """查询推广员测试"""

    async def run(self, svc):
        # 准备
        p1 = await svc.create_promoter(USER_ID_1, "张三", LEVEL_TRAINEE)
        p2 = await svc.create_promoter(USER_ID_2, "李四", LEVEL_GOLD)

        # test 7: 按ID查询
        result = await svc.get_promoter(p1["id"])
        record("test_07_get_by_id",
               result["id"] == p1["id"] and result["name"] == "张三",
               f"expected {p1['id']}/张三, got {result['id']}/{result['name']}")

        # test 8: 按推广码查询
        result = await svc.get_promoter_by_code(p2["promoterCode"])
        record("test_08_get_by_code",
               result["id"] == p2["id"],
               "按推广码查询失败")

        # test 9: 不存在的推广员查询失败
        try:
            await svc.get_promoter(99999)
            record("test_09_get_nonexistent", False, "应抛出KeyError")
        except KeyError:
            record("test_09_get_nonexistent", True)

        # test 10: 无效推广码查询失败
        try:
            await svc.get_promoter_by_code("INVALID_CODE")
            record("test_10_invalid_code", False, "应抛出KeyError")
        except KeyError:
            record("test_10_invalid_code", True)

        # test 11: 按等级筛选列表
        gold_list = await svc.list_promoters(level=LEVEL_GOLD)
        record("test_11_filter_by_level",
               all(p["level"] == LEVEL_GOLD for p in gold_list),
               "按等级筛选失败")

        # test 12: 按状态筛选列表
        active_list = await svc.list_promoters(status=PROMOTER_STATUS_ACTIVE)
        record("test_12_filter_by_status",
               all(p["status"] == PROMOTER_STATUS_ACTIVE for p in active_list),
               "按状态筛选失败")


class TestRecordLead:
    """引流记录测试"""

    async def run(self, svc):
        # 准备
        p1 = await svc.create_promoter(USER_ID_1, "张三", LEVEL_TRAINEE)
        p2 = await svc.create_promoter(USER_ID_2, "李四", LEVEL_TRAINEE)

        # test 13: 正常引流记录
        result = await svc.record_lead(
            p1["id"], USER_ID_2, source=SOURCE_DOUYIN, medium=MEDIUM_VIDEO,
            is_effective=LEAD_EFFECTIVE_TRUE, status=LEAD_STATUS_PENDING
        )
        record("test_13_record_lead",
               result["promoterId"] == p1["id"] and result["source"] == SOURCE_DOUYIN,
               f"expected {p1['id']}/{SOURCE_DOUYIN}, got {result['promoterId']}/{result['source']}")

        # test 14: 推广员累计邀请数增加
        promoter = await svc.get_promoter(p1["id"])
        record("test_14_total_invited_increment",
               promoter["totalInvited"] == 1,
               f"expected 1, got {promoter['totalInvited']}")

        # test 15: 注册状态引流记录累计注册数
        await svc.record_lead(
            p1["id"], USER_ID_3, source=SOURCE_WECHAT, medium=MEDIUM_SHARE,
            status=LEAD_STATUS_REGISTERED
        )
        promoter = await svc.get_promoter(p1["id"])
        record("test_15_registered_increment",
               promoter["totalRegistered"] == 1,
               f"expected 1, got {promoter['totalRegistered']}")

        # test 16: 下单状态引流记录累计下单数
        await svc.record_lead(
            p1["id"], 30004, source=SOURCE_KUAISHOU, medium=MEDIUM_LIVE,
            status=LEAD_STATUS_ORDERED
        )
        promoter = await svc.get_promoter(p1["id"])
        record("test_16_ordered_increment",
               promoter["totalOrdered"] == 1,
               f"expected 1, got {promoter['totalOrdered']}")

        # test 17: 不存在的推广员引流失败
        try:
            await svc.record_lead(99999, USER_ID_3)
            record("test_17_lead_nonexistent_promoter", False, "应抛出KeyError")
        except KeyError:
            record("test_17_lead_nonexistent_promoter", True)

        # test 18: 封禁推广员引流失败
        repo = svc.repo
        promoter = await repo.get_promoter(p2["id"])
        promoter["status"] = PROMOTER_STATUS_BANNED
        await repo.save_promoter(promoter)
        try:
            await svc.record_lead(p2["id"], USER_ID_3)
            record("test_18_banned_promoter_lead", False, "应抛出ValueError")
        except ValueError:
            record("test_18_banned_promoter_lead", True)

        # test 19: 按推广员筛选引流列表
        leads = await svc.list_leads(promoter_id=p1["id"])
        record("test_19_filter_by_promoter",
               all(l["promoterId"] == p1["id"] for l in leads),
               "按推广员筛选失败")

        # test 20: 按来源筛选引流列表
        douyin_leads = await svc.list_leads(source=SOURCE_DOUYIN)
        record("test_20_filter_by_source",
               all(l["source"] == SOURCE_DOUYIN for l in douyin_leads),
               "按来源筛选失败")


class TestCommission:
    """佣金计算测试"""

    async def run(self, svc):
        # 准备
        p1 = await svc.create_promoter(USER_ID_1, "张三", LEVEL_TRAINEE)  # 5%
        p2 = await svc.create_promoter(USER_ID_2, "李四", LEVEL_GOLD)     # 15%

        # test 21: 见习推广员佣金(¥100 × 5% = ¥5)
        result = await svc.calculate_commission(
            p1["id"], ORDER_ID_1, 100.0, USER_ID_2
        )
        record("test_21_trainee_commission",
               result["commission"] == 5.0 and result["commissionRate"] == 0.05,
               f"expected 5.0/0.05, got {result['commission']}/{result['commissionRate']}")

        # test 22: 金牌推广员佣金(¥100 × 15% = ¥15)
        result = await svc.calculate_commission(
            p2["id"], ORDER_ID_2, 100.0, USER_ID_1
        )
        record("test_22_gold_commission",
               result["commission"] == 15.0 and result["commissionRate"] == 0.15,
               f"expected 15.0/0.15, got {result['commission']}/{result['commissionRate']}")

        # test 23: 推广员累计佣金增加
        promoter = await svc.get_promoter(p1["id"])
        record("test_23_total_commission_increment",
               promoter["totalCommission"] == 5.0
               and promoter["pendingCommission"] == 5.0,
               f"expected 5.0/5.0, got {promoter['totalCommission']}/{promoter['pendingCommission']}")

        # test 24: 订单金额为0失败
        try:
            await svc.calculate_commission(p1["id"], "ORD_ZERO", 0.0)
            record("test_24_zero_amount", False, "应抛出ValueError")
        except ValueError:
            record("test_24_zero_amount", True)

        # test 25: 自购不计佣金
        try:
            await svc.calculate_commission(p1["id"], "ORD_SELF", 100.0, USER_ID_1)
            record("test_25_self_purchase", False, "应抛出ValueError")
        except ValueError:
            record("test_25_self_purchase", True)

        # test 26: 不存在的推广员佣金计算失败
        try:
            await svc.calculate_commission(99999, ORDER_ID_1, 100.0)
            record("test_26_nonexistent_promoter", False, "应抛出KeyError")
        except KeyError:
            record("test_26_nonexistent_promoter", True)

        # test 27: 封禁推广员佣金计算失败
        repo = svc.repo
        p3 = await svc.create_promoter(30010, "王五", LEVEL_JUNIOR)
        promoter = await repo.get_promoter(p3["id"])
        promoter["status"] = PROMOTER_STATUS_BANNED
        await repo.save_promoter(promoter)
        try:
            await svc.calculate_commission(p3["id"], ORDER_ID_1, 100.0, USER_ID_1)
            record("test_27_banned_commission", False, "应抛出ValueError")
        except ValueError:
            record("test_27_banned_commission", True)


class TestPromoterLevel:
    """推广员等级测试"""

    async def run(self, svc):
        # 准备: 见习推广员
        p1 = await svc.create_promoter(USER_ID_1, "张三", LEVEL_TRAINEE)

        # test 28: 查询等级信息
        result = await svc.get_promoter_level(p1["id"])
        record("test_28_get_level_info",
               result["currentLevel"] == LEVEL_TRAINEE and result["nextLevel"] == LEVEL_JUNIOR,
               f"expected {LEVEL_TRAINEE}/{LEVEL_JUNIOR}, got {result['currentLevel']}/{result['nextLevel']}")

        # test 29: 升级条件正确(初级需邀请5人)
        record("test_29_upgrade_condition",
               result["nextCondition"] == LEVEL_UPGRADE_CONDITIONS[LEVEL_JUNIOR],
               "升级条件错误")

        # test 30: 等级额外奖励正确
        p2 = await svc.create_promoter(USER_ID_2, "李四", LEVEL_GOLD)
        result = await svc.get_promoter_level(p2["id"])
        record("test_30_extra_reward",
               "experience" in result["extraReward"]
               and result["extraReward"]["experience"] == "泰山游资格",
               "金牌额外奖励错误")

        # test 31: 等级排序正确
        record("test_31_level_ranks",
               LEVEL_RANK[LEVEL_TRAINEE] == 1 and LEVEL_RANK[LEVEL_GOLD] == 5,
               "等级排序错误")

        # test 32: 不存在的推广员查询等级失败
        try:
            await svc.get_promoter_level(99999)
            record("test_32_level_nonexistent", False, "应抛出KeyError")
        except KeyError:
            record("test_32_level_nonexistent", True)


class TestFissionTree:
    """裂变关系测试"""

    async def run(self, svc):
        # 准备: 创建推广员层级
        p1 = await svc.create_promoter(USER_ID_1, "张三", LEVEL_JUNIOR)
        p2 = await svc.create_promoter(USER_ID_2, "李四", LEVEL_TRAINEE,
                                        parent_promoter_id=p1["id"])
        p3 = await svc.create_promoter(USER_ID_3, "王五", LEVEL_TRAINEE,
                                        parent_promoter_id=p1["id"])
        p4 = await svc.create_promoter(30010, "赵六", LEVEL_TRAINEE,
                                        parent_promoter_id=p2["id"])

        # test 33: 查询裂变树
        result = await svc.get_fission_tree(p1["id"])
        record("test_33_fission_tree",
               result["promoterId"] == p1["id"] and result["directSubordinates"] == 2,
               f"expected {p1['id']}/2, got {result['promoterId']}/{result['directSubordinates']}")

        # test 34: 间接下线数正确
        record("test_34_indirect_subordinates",
               result["indirectSubordinates"] == 1,
               f"expected 1, got {result['indirectSubordinates']}")

        # test 35: 总下线数正确
        record("test_35_total_subordinates",
               result["totalSubordinates"] == 3,
               f"expected 3, got {result['totalSubordinates']}")

        # test 36: 下线列表正确
        sub_ids = [s["promoterId"] for s in result["subordinates"]]
        record("test_36_subordinates_list",
               p2["id"] in sub_ids and p3["id"] in sub_ids and p4["id"] not in sub_ids,
               "下线列表错误")

        # test 37: 不存在的推广员裂变查询失败
        try:
            await svc.get_fission_tree(99999)
            record("test_37_fission_nonexistent", False, "应抛出KeyError")
        except KeyError:
            record("test_37_fission_nonexistent", True)


class TestSourceAndDistribute:
    """流量来源与分发测试"""

    async def run(self, svc):
        # test 38: 创建流量来源
        result = await svc.create_source(SOURCE_DOUYIN, "抖音", "抖音短视频引流")
        record("test_38_create_source",
               result["code"] == SOURCE_DOUYIN and result["name"] == "抖音",
               f"expected {SOURCE_DOUYIN}/抖音, got {result['code']}/{result['name']}")

        # test 39: 重复创建流量来源失败
        try:
            await svc.create_source(SOURCE_DOUYIN, "抖音重复")
            record("test_39_duplicate_source", False, "应抛出ValueError")
        except ValueError:
            record("test_39_duplicate_source", True)

        # test 40: 查询流量来源列表
        await svc.create_source(SOURCE_WECHAT, "微信", "微信分享引流")
        sources = await svc.list_sources()
        record("test_40_list_sources",
               len(sources) >= 2,
               f"expected >=2, got {len(sources)}")

        # test 41: 流量分发(按比例)
        # 创建多个推广员(数据隔离已重置store)
        await svc.create_promoter(USER_ID_1, "张三", LEVEL_TRAINEE)
        await svc.create_promoter(USER_ID_2, "李四", LEVEL_GOLD)
        # 添加一些邀请数
        await svc.record_lead(1, 30010, status=LEAD_STATUS_REGISTERED)
        await svc.record_lead(2, 30011, status=LEAD_STATUS_REGISTERED)
        await svc.record_lead(2, 30012, status=LEAD_STATUS_REGISTERED)
        result = await svc.distribute_traffic(1000, "proportional")
        record("test_41_distribute_proportional",
               result["totalTraffic"] == 1000 and len(result["distributions"]) == 2,
               f"expected 1000/2, got {result['totalTraffic']}/{len(result['distributions'])}")

        # test 42: 流量分发(平均)
        result = await svc.distribute_traffic(100, "average")
        record("test_42_distribute_average",
               result["distributorCount"] == 2,
               f"expected 2, got {result['distributorCount']}")

        # test 43: 流量分发(按等级加权)
        result = await svc.distribute_traffic(100, "weighted")
        record("test_43_distribute_weighted",
               len(result["distributions"]) == 2,
               f"expected 2, got {len(result['distributions'])}")

        # test 44: 无推广员时分发为空
        reset_store()
        svc_new = TrafficService()
        result = await svc_new.distribute_traffic(100, "average")
        record("test_44_distribute_no_promoter",
               len(result["distributions"]) == 0,
               f"expected 0, got {len(result['distributions'])}")


class TestStats:
    """统计测试"""

    async def run(self, svc):
        # 准备
        p1 = await svc.create_promoter(USER_ID_1, "张三", LEVEL_TRAINEE)
        p2 = await svc.create_promoter(USER_ID_2, "李四", LEVEL_GOLD)
        await svc.record_lead(p1["id"], USER_ID_2, source=SOURCE_DOUYIN,
                                status=LEAD_STATUS_REGISTERED)
        await svc.record_lead(p2["id"], USER_ID_1, source=SOURCE_WECHAT,
                                status=LEAD_STATUS_ORDERED)
        await svc.calculate_commission(p1["id"], ORDER_ID_1, 100.0, USER_ID_2)

        # test 45: 推广员统计字段完整
        stats = await svc.get_stats(promoter_id=p1["id"])
        record("test_45_promoter_stats_fields",
               all(k in stats for k in ["totalInvited", "totalRegistered",
                   "totalOrdered", "totalCommission", "pendingCommission"]),
               "统计字段缺失")

        # test 46: 推广员统计数值正确
        record("test_46_promoter_stats_correct",
               stats["totalInvited"] == 1 and stats["totalCommission"] == 5.0,
               f"expected 1/5.0, got {stats['totalInvited']}/{stats['totalCommission']}")

        # test 47: 全局统计字段完整
        global_stats = await svc.get_stats()
        record("test_47_global_stats_fields",
               all(k in global_stats for k in ["totalPromoters", "activePromoters",
                   "totalLeads", "effectiveLeads", "totalCommission"]),
               "全局统计字段缺失")

        # test 48: 全局统计数值正确
        record("test_48_global_stats_correct",
               global_stats["totalPromoters"] == 2 and global_stats["totalLeads"] == 2,
               f"expected 2/2, got {global_stats['totalPromoters']}/{global_stats['totalLeads']}")

        # test 49: 管理端统计字段完整
        admin_stats = await svc.get_admin_stats()
        record("test_49_admin_stats_fields",
               all(k in admin_stats for k in ["totalPromoters", "levelDistribution",
                   "sourceDistribution", "totalCommission"]),
               "管理端统计字段缺失")

        # test 50: 管理端统计等级分布正确
        record("test_50_admin_level_distribution",
               admin_stats["levelDistribution"].get(LEVEL_TRAINEE) == 1
               and admin_stats["levelDistribution"].get(LEVEL_GOLD) == 1,
               "等级分布错误")

        # test 51: 不存在的推广员统计失败
        try:
            await svc.get_stats(promoter_id=99999)
            record("test_51_stats_nonexistent", False, "应抛出KeyError")
        except KeyError:
            record("test_51_stats_nonexistent", True)


# ============================================================
# 测试运行
# ============================================================

async def main():
    print("=" * 60)
    print("流量管理模块端到端测试")
    print("=" * 60)
    print()

    test_classes = [
        TestCreatePromoter,
        TestQueryPromoter,
        TestRecordLead,
        TestCommission,
        TestPromoterLevel,
        TestFissionTree,
        TestSourceAndDistribute,
        TestStats,
    ]

    for cls in test_classes:
        reset_store()
        svc = TrafficService()
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
