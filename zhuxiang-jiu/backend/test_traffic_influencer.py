"""博主(KOL)多平台关联模块端到端测试(Service 层, 无需 Docker/fastapi)

直接调用 TrafficService 博主相关方法, 模拟 8 个 HTTP 接口的业务逻辑。
使用 asyncio 内存模式(LOCK_MODE=asyncio, STORE_MODE=asyncio)。

运行:
    $env:LOCK_MODE="asyncio"; $env:STORE_MODE="asyncio"
    python test_traffic_influencer.py

覆盖接口:
    1. 创建博主(1):      create_influencer
    2. 查询博主(1):      get_influencer
    3. 博主列表(1):      list_influencers
    4. 关联平台(1):      add_influencer_platform
    5. 同步平台(1):       sync_influencer_platform
    6. 推广码(1):         create_influencer_promo_code
    7. 归因查询(1):       get_influencer_attribution
    8. 流量归因(1):       attribute_traffic
"""

import asyncio
import os
import sys

# 确保使用内存模式
os.environ["LOCK_MODE"] = "asyncio"
os.environ["STORE_MODE"] = "asyncio"

from services.traffic_service import TrafficService
from repositories.traffic_repository import (
    TrafficRepository,
    SOURCE_DOUYIN, SOURCE_KUAISHOU, SOURCE_WECHAT,
    SOURCE_XIAOHONGSHU, SOURCE_BILIBILI,
    INFLUENCER_LEVEL_S, INFLUENCER_LEVEL_A, INFLUENCER_LEVEL_B, INFLUENCER_LEVEL_C,
    INFLUENCER_LEVEL_COMMISSION_RATE,
    INFLUENCER_STATUS_COOPERATING, INFLUENCER_STATUS_SUSPENDED, INFLUENCER_STATUS_ENDED,
    PROMO_CODE_ACTIVE, PROMO_CODE_EXPIRED,
)
from repositories.store import reset_store as _reset_store_impl

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

USER_ID_1 = 2001
USER_ID_2 = 2002


# ============================================================
# 测试用例
# ============================================================

class TestInfluencerCreate:
    """博主创建测试"""

    async def run(self, svc):
        # test 1: 创建博主(默认C级, 佣金10%)
        inf = await svc.create_influencer(USER_ID_1, "测试博主A")
        record("test_01_create_influencer_default",
               inf["id"] > 0 and inf["level"] == INFLUENCER_LEVEL_C,
               f"expected C level, got {inf.get('level')}")

        # test 2: 默认佣金比例(C级=10%)
        record("test_02_default_commission_rate",
               inf["commissionRate"] == 0.10,
               f"expected 0.10, got {inf['commissionRate']}")

        # test 3: 默认状态为合作中
        record("test_03_default_status_cooperating",
               inf["status"] == INFLUENCER_STATUS_COOPERATING,
               f"expected cooperating, got {inf['status']}")

        # test 4: 创建S级博主(佣金20%)
        inf_s = await svc.create_influencer(USER_ID_2, "头部博主S",
                                              level=INFLUENCER_LEVEL_S)
        record("test_04_create_s_level_influencer",
               inf_s["level"] == INFLUENCER_LEVEL_S and inf_s["commissionRate"] == 0.20,
               f"expected S/0.20, got {inf_s['level']}/{inf_s['commissionRate']}")

        # test 5: 非法等级抛 ValueError
        try:
            await svc.create_influencer(USER_ID_1, "非法", level="X")
            record("test_05_invalid_level_raises", False, "未抛出 ValueError")
        except ValueError:
            record("test_05_invalid_level_raises", True)
        except Exception as e:
            record("test_05_invalid_level_raises", False, f"抛出异常类型错误: {type(e).__name__}")

        # test 6: 佣金比例超限(>0.30)抛 ValueError
        try:
            await svc.create_influencer(USER_ID_1, "超限", level=INFLUENCER_LEVEL_C,
                                          commission_rate=0.50)
            record("test_06_commission_rate_over_limit", False, "未抛出 ValueError")
        except ValueError:
            record("test_06_commission_rate_over_limit", True)
        except Exception as e:
            record("test_06_commission_rate_over_limit", False, f"异常类型错误: {type(e).__name__}")


class TestInfluencerQuery:
    """博主查询测试"""

    async def run(self, svc):
        # 准备: 创建博主
        inf = await svc.create_influencer(USER_ID_1, "查询测试博主")

        # test 7: 查询博主详情
        detail = await svc.get_influencer(inf["id"])
        record("test_07_get_influencer_detail",
               detail["id"] == inf["id"] and detail["name"] == "查询测试博主",
               f"expected id={inf['id']}, got {detail.get('id')}")

        # test 8: 查询结果包含空平台列表
        record("test_08_empty_platforms_list",
               detail.get("platforms") == [],
               f"expected [], got {detail.get('platforms')}")

        # test 9: 查询结果包含空推广码列表
        record("test_09_empty_promo_codes_list",
               detail.get("promoCodes") == [],
               f"expected [], got {detail.get('promoCodes')}")

        # test 10: 查询不存在的博主抛 KeyError
        try:
            await svc.get_influencer(99999)
            record("test_10_get_nonexistent_raises", False, "未抛出 KeyError")
        except KeyError:
            record("test_10_get_nonexistent_raises", True)
        except Exception as e:
            record("test_10_get_nonexistent_raises", False, f"异常类型错误: {type(e).__name__}")


class TestInfluencerList:
    """博主列表测试"""

    async def run(self, svc):
        # 准备: 创建多个博主
        await svc.create_influencer(USER_ID_1, "博主C1", level=INFLUENCER_LEVEL_C)
        await svc.create_influencer(USER_ID_1, "博主A1", level=INFLUENCER_LEVEL_A)
        await svc.create_influencer(USER_ID_2, "博主S1", level=INFLUENCER_LEVEL_S)

        # test 11: 列表查询所有博主
        all_list = await svc.list_influencers()
        record("test_11_list_all_influencers",
               len(all_list) >= 3,
               f"expected >=3, got {len(all_list)}")

        # test 12: 按等级筛选(S级)
        s_list = await svc.list_influencers(level=INFLUENCER_LEVEL_S)
        record("test_12_list_by_level_s",
               len(s_list) >= 1 and all(i["level"] == INFLUENCER_LEVEL_S for i in s_list),
               f"got {len(s_list)} S级博主")

        # test 13: 按状态筛选(合作中)
        coop_list = await svc.list_influencers(status=INFLUENCER_STATUS_COOPERATING)
        record("test_13_list_by_status_cooperating",
               len(coop_list) >= 3,
               f"expected >=3, got {len(coop_list)}")

        # test 14: 按等级筛选(C级)
        c_list = await svc.list_influencers(level=INFLUENCER_LEVEL_C)
        record("test_14_list_by_level_c",
               len(c_list) >= 1 and all(i["level"] == INFLUENCER_LEVEL_C for i in c_list),
               f"got {len(c_list)} C级博主")


class TestPlatformAssociation:
    """博主平台关联测试"""

    async def run(self, svc):
        # 准备: 创建博主
        inf = await svc.create_influencer(USER_ID_1, "平台关联博主")

        # test 15: 关联抖音平台
        plat1 = await svc.add_influencer_platform(
            influencer_id=inf["id"],
            platform=SOURCE_DOUYIN,
            platform_uid="dy_123456",
            platform_name="抖音测试号",
            follower_count=100000,
            verified=True,
        )
        record("test_15_add_douyin_platform",
               plat1["id"] > 0 and plat1["platform"] == SOURCE_DOUYIN,
               f"expected douyin, got {plat1.get('platform')}")

        # test 16: 粉丝数正确保存
        record("test_16_follower_count_saved",
               plat1["followerCount"] == 100000,
               f"expected 100000, got {plat1.get('followerCount')}")

        # test 17: 认证状态正确保存
        record("test_17_verified_saved",
               plat1["verified"] is True,
               f"expected True, got {plat1.get('verified')}")

        # test 18: 关联小红书平台
        plat2 = await svc.add_influencer_platform(
            influencer_id=inf["id"],
            platform=SOURCE_XIAOHONGSHU,
            platform_uid="xhs_789",
            platform_name="小红书测试号",
            follower_count=50000,
        )
        record("test_18_add_xiaohongshu_platform",
               plat2["id"] > 0 and plat2["platform"] == SOURCE_XIAOHONGSHU,
               f"expected xiaohongshu, got {plat2.get('platform')}")

        # test 19: 查询博主平台列表(应有2个)
        detail = await svc.get_influencer(inf["id"])
        record("test_19_platform_count_2",
               len(detail["platforms"]) == 2,
               f"expected 2 platforms, got {len(detail['platforms'])}")

        # test 20: 重复关联同平台抛 ValueError
        try:
            await svc.add_influencer_platform(
                influencer_id=inf["id"],
                platform=SOURCE_DOUYIN,
                platform_uid="dy_999",
            )
            record("test_20_duplicate_platform_raises", False, "未抛出 ValueError")
        except ValueError:
            record("test_20_duplicate_platform_raises", True)
        except Exception as e:
            record("test_20_duplicate_platform_raises", False, f"异常类型错误: {type(e).__name__}")

        # test 21: 不支持的平台抛 ValueError
        try:
            await svc.add_influencer_platform(
                influencer_id=inf["id"],
                platform="unknown_platform",
                platform_uid="xxx",
            )
            record("test_21_unsupported_platform_raises", False, "未抛出 ValueError")
        except ValueError:
            record("test_21_unsupported_platform_raises", True)
        except Exception as e:
            record("test_21_unsupported_platform_raises", False, f"异常类型错误: {type(e).__name__}")

        # test 22: 关联不存在的博主抛 KeyError
        try:
            await svc.add_influencer_platform(
                influencer_id=99999,
                platform=SOURCE_DOUYIN,
                platform_uid="xxx",
            )
            record("test_22_nonexistent_influencer_raises", False, "未抛出 KeyError")
        except KeyError:
            record("test_22_nonexistent_influencer_raises", True)
        except Exception as e:
            record("test_22_nonexistent_influencer_raises", False, f"异常类型错误: {type(e).__name__}")


class TestPlatformSync:
    """平台数据同步测试"""

    async def run(self, svc):
        # 准备: 创建博主+关联平台
        inf = await svc.create_influencer(USER_ID_1, "同步测试博主")
        plat = await svc.add_influencer_platform(
            influencer_id=inf["id"],
            platform=SOURCE_BILIBILI,
            platform_uid="bili_123",
            follower_count=10000,
        )

        # test 23: 同步粉丝数
        updated = await svc.sync_influencer_platform(
            platform_id=plat["id"],
            follower_count=15000,
        )
        record("test_23_sync_follower_count",
               updated["followerCount"] == 15000,
               f"expected 15000, got {updated.get('followerCount')}")

        # test 24: 同步认证状态
        updated = await svc.sync_influencer_platform(
            platform_id=plat["id"],
            verified=True,
        )
        record("test_24_sync_verified",
               updated["verified"] is True,
               f"expected True, got {updated.get('verified')}")

        # test 25: 同步不存在的平台抛 KeyError
        try:
            await svc.sync_influencer_platform(platform_id=99999, follower_count=100)
            record("test_25_sync_nonexistent_raises", False, "未抛出 KeyError")
        except KeyError:
            record("test_25_sync_nonexistent_raises", True)
        except Exception as e:
            record("test_25_sync_nonexistent_raises", False, f"异常类型错误: {type(e).__name__}")

        # test 26: 负粉丝数抛 ValueError
        try:
            await svc.sync_influencer_platform(platform_id=plat["id"], follower_count=-1)
            record("test_26_negative_follower_raises", False, "未抛出 ValueError")
        except ValueError:
            record("test_26_negative_follower_raises", True)
        except Exception as e:
            record("test_26_negative_follower_raises", False, f"异常类型错误: {type(e).__name__}")


class TestPromoCode:
    """博主推广码测试"""

    async def run(self, svc):
        # 准备: 创建博主+关联平台
        inf = await svc.create_influencer(USER_ID_1, "推广码测试博主")
        await svc.add_influencer_platform(
            influencer_id=inf["id"],
            platform=SOURCE_DOUYIN,
            platform_uid="dy_promo_test",
            follower_count=200000,
        )

        # test 27: 生成推广码
        code = await svc.create_influencer_promo_code(
            influencer_id=inf["id"],
            platform=SOURCE_DOUYIN,
        )
        record("test_27_create_promo_code",
               code["id"] > 0 and code["promoCode"].startswith(f"KOL{inf['id']}_douyin_"),
               f"expected KOL{inf['id']}_douyin_xxx, got {code.get('promoCode')}")

        # test 28: 推广码包含推广链接
        record("test_28_promo_link_generated",
               code["promoLink"].startswith("https://zhuxiang-jiu.com/r/KOL"),
               f"expected https url, got {code.get('promoLink')}")

        # test 29: 推广码状态为 active
        record("test_29_promo_code_active",
               code["status"] == PROMO_CODE_ACTIVE,
               f"expected active, got {code.get('status')}")

        # test 30: 为未关联的平台生成推广码抛 ValueError
        try:
            await svc.create_influencer_promo_code(
                influencer_id=inf["id"],
                platform=SOURCE_KUAISHOU,
            )
            record("test_30_code_for_unlinked_platform_raises", False, "未抛出 ValueError")
        except ValueError:
            record("test_30_code_for_unlinked_platform_raises", True)
        except Exception as e:
            record("test_30_code_for_unlinked_platform_raises", False, f"异常类型错误: {type(e).__name__}")

        # test 31: 为不存在的博主生成推广码抛 KeyError
        try:
            await svc.create_influencer_promo_code(
                influencer_id=99999,
                platform=SOURCE_DOUYIN,
            )
            record("test_31_code_for_nonexistent_raises", False, "未抛出 KeyError")
        except KeyError:
            record("test_31_code_for_nonexistent_raises", True)
        except Exception as e:
            record("test_31_code_for_nonexistent_raises", False, f"异常类型错误: {type(e).__name__}")


class TestAttribution:
    """流量归因测试"""

    async def run(self, svc):
        # 准备: 创建博主+关联平台+生成推广码
        inf = await svc.create_influencer(USER_ID_1, "归因测试博主",
                                            level=INFLUENCER_LEVEL_A)
        await svc.add_influencer_platform(
            influencer_id=inf["id"],
            platform=SOURCE_DOUYIN,
            platform_uid="dy_attr_test",
            follower_count=300000,
            verified=True,
        )
        code = await svc.create_influencer_promo_code(
            influencer_id=inf["id"],
            platform=SOURCE_DOUYIN,
        )
        promo_code = code["promoCode"]

        # test 32: 点击归因
        result = await svc.attribute_traffic(promo_code, is_click=True)
        record("test_32_click_attribution",
               result["attributed"] is True and result["click"] is True,
               f"expected attributed+click, got {result}")

        # test 33: 注册归因
        result = await svc.attribute_traffic(promo_code, is_lead=True)
        record("test_33_lead_attribution",
               result["attributed"] is True and result["lead"] is True,
               f"expected attributed+lead, got {result}")

        # test 34: 订单归因(GMV=500)
        result = await svc.attribute_traffic(promo_code, order_amount=500.00)
        record("test_34_order_attribution",
               result["attributed"] is True and result["orderAmount"] == 500.00,
               f"expected attributed+500, got {result}")

        # test 35: 推广码统计已更新(click=1, lead=1, order=1, gmv=500)
        detail = await svc.get_influencer(inf["id"])
        code_stats = detail["promoCodes"][0]
        record("test_35_code_stats_updated",
               code_stats["clickCount"] == 1 and code_stats["leadCount"] == 1
               and code_stats["orderCount"] == 1 and code_stats["gmv"] == 500.00,
               f"got click={code_stats['clickCount']}, lead={code_stats['leadCount']}, "
               f"order={code_stats['orderCount']}, gmv={code_stats['gmv']}")

        # test 36: 博主累计统计已更新(traffic=2, orders=1, gmv=500)
        record("test_36_influencer_stats_updated",
               inf_detail_total := detail["totalTraffic"] == 2
               and detail["totalOrders"] == 1
               and detail["totalGmv"] == 500.00,
               f"got traffic={detail['totalTraffic']}, orders={detail['totalOrders']}, "
               f"gmv={detail['totalGmv']}")

        # test 37: 查询归因数据
        attr = await svc.get_influencer_attribution(inf["id"])
        record("test_37_get_attribution",
               attr["totalGmv"] == 500.00 and len(attr["platformAttribution"]) == 1,
               f"got gmv={attr['totalGmv']}, platforms={len(attr['platformAttribution'])}")

        # test 38: 归因数据包含平台粉丝数
        plat_attr = attr["platformAttribution"][0]
        record("test_38_attribution_has_follower_count",
               plat_attr["platform"] == SOURCE_DOUYIN and plat_attr["followerCount"] == 300000,
               f"got platform={plat_attr['platform']}, followers={plat_attr.get('followerCount')}")

        # test 39: 不存在的推广码归因抛 KeyError
        try:
            await svc.attribute_traffic("NONEXISTENT_CODE", is_click=True)
            record("test_39_nonexistent_code_raises", False, "未抛出 KeyError")
        except KeyError:
            record("test_39_nonexistent_code_raises", True)
        except Exception as e:
            record("test_39_nonexistent_code_raises", False, f"异常类型错误: {type(e).__name__}")


class TestMultiPlatformScenario:
    """多平台综合场景测试"""

    async def run(self, svc):
        # 准备: 创建博主+关联3个平台+生成3个推广码
        inf = await svc.create_influencer(USER_ID_1, "多平台博主",
                                            level=INFLUENCER_LEVEL_S)

        # 关联抖音
        await svc.add_influencer_platform(
            influencer_id=inf["id"], platform=SOURCE_DOUYIN,
            platform_uid="dy_multi", follower_count=500000, verified=True,
        )
        # 关联小红书
        await svc.add_influencer_platform(
            influencer_id=inf["id"], platform=SOURCE_XIAOHONGSHU,
            platform_uid="xhs_multi", follower_count=200000, verified=True,
        )
        # 关联B站
        await svc.add_influencer_platform(
            influencer_id=inf["id"], platform=SOURCE_BILIBILI,
            platform_uid="bili_multi", follower_count=100000,
        )

        # test 40: 博主关联3个平台
        detail = await svc.get_influencer(inf["id"])
        record("test_40_multi_platform_count_3",
               len(detail["platforms"]) == 3,
               f"expected 3 platforms, got {len(detail['platforms'])}")

        # 为3个平台生成推广码
        code_dy = await svc.create_influencer_promo_code(inf["id"], SOURCE_DOUYIN)
        code_xhs = await svc.create_influencer_promo_code(inf["id"], SOURCE_XIAOHONGSHU)
        code_bili = await svc.create_influencer_promo_code(inf["id"], SOURCE_BILIBILI)

        # test 41: 生成3个推广码
        detail = await svc.get_influencer(inf["id"])
        record("test_41_multi_promo_codes_3",
               len(detail["promoCodes"]) == 3,
               f"expected 3 codes, got {len(detail['promoCodes'])}")

        # 模拟流量归因: 抖音 1000 GMV, 小红书 500 GMV, B站 200 GMV
        await svc.attribute_traffic(code_dy["promoCode"], is_click=True, order_amount=1000.00)
        await svc.attribute_traffic(code_xhs["promoCode"], is_click=True, order_amount=500.00)
        await svc.attribute_traffic(code_bili["promoCode"], is_click=True, order_amount=200.00)

        # test 42: 多平台归因汇总(总GMV=1700)
        attr = await svc.get_influencer_attribution(inf["id"])
        record("test_42_multi_platform_total_gmv",
               attr["totalGmv"] == 1700.00,
               f"expected 1700.00, got {attr['totalGmv']}")

        # test 43: 归因数据包含3个平台
        record("test_43_attribution_has_3_platforms",
               len(attr["platformAttribution"]) == 3,
               f"expected 3, got {len(attr['platformAttribution'])}")

        # test 44: 抖音平台GMV=1000
        dy_attr = next(p for p in attr["platformAttribution"] if p["platform"] == SOURCE_DOUYIN)
        record("test_44_douyin_gmv_1000",
               dy_attr["gmv"] == 1000.00,
               f"expected 1000, got {dy_attr['gmv']}")

        # test 45: 小红书平台GMV=500
        xhs_attr = next(p for p in attr["platformAttribution"] if p["platform"] == SOURCE_XIAOHONGSHU)
        record("test_45_xiaohongshu_gmv_500",
               xhs_attr["gmv"] == 500.00,
               f"expected 500, got {xhs_attr['gmv']}")

        # test 46: B站平台GMV=200
        bili_attr = next(p for p in attr["platformAttribution"] if p["platform"] == SOURCE_BILIBILI)
        record("test_46_bilibili_gmv_200",
               bili_attr["gmv"] == 200.00,
               f"expected 200, got {bili_attr['gmv']}")

        # test 47: 总订单数=3
        record("test_47_total_orders_3",
               attr["totalOrders"] == 3,
               f"expected 3, got {attr['totalOrders']}")

        # test 48: 总点击数=3
        record("test_48_total_clicks_3",
               attr["totalTraffic"] == 3,
               f"expected 3, got {attr['totalTraffic']}")


# ============================================================
# 主函数
# ============================================================

async def main():
    print("=" * 60)
    print("博主(KOL)多平台关联模块测试")
    print("=" * 60)

    reset_store()
    svc = TrafficService()

    test_classes = [
        TestInfluencerCreate(),
        TestInfluencerQuery(),
        TestInfluencerList(),
        TestPlatformAssociation(),
        TestPlatformSync(),
        TestPromoCode(),
        TestAttribution(),
        TestMultiPlatformScenario(),
    ]

    for tc in test_classes:
        print(f"\n[{tc.__class__.__name__}]")
        await tc.run(svc)

    print("\n" + "=" * 60)
    for line in RESULTS:
        print(line)
    print("=" * 60)
    print(f"总计: {PASS} 通过, {FAIL} 失败")
    print("=" * 60)

    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.path.insert(0, ".")
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
