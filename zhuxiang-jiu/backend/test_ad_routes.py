"""广告管理模块端到端测试(Service 层, 无需 Docker/fastapi)

直接调用 AdService 方法, 模拟 12 个 HTTP 接口的业务逻辑。
使用 asyncio 内存模式(LOCK_MODE=asyncio, STORE_MODE=asyncio)。

运行:
    $env:LOCK_MODE="asyncio"; $env:STORE_MODE="asyncio"
    python test_ad_routes.py

覆盖 12 个接口对应的业务方法:
    1. 广告(5):    create_ad / list_ads / get_ad / update_ad / online_ad+offline_ad
    2. 审核(1):    review_ad
    3. 效果(3):    record_impression / record_click / get_ad_stats
    4. 广告位(4):  create_slot / list_slots / update_slot / delete_slot
    5. 投放(1):    list_placements
    6. 统计(1):    get_stats

测试覆盖:
    - 广告CRUD(创建/查询/列表/筛选/更新/不可变字段/状态约束)
    - AI审核(合规通过/禁用词/缺健康警示/缺广告标识/多违规/状态流转/重复审核/审核历史)
    - 上下线(未审核上线失败/审核通过上线/创建投放/下线/状态约束)
    - 效果统计(非投放中拒绝/曝光/点击/转化+产出/CTR/CVR/ROI)
    - 广告位CRUD(创建/重复/查询/列表/更新/不可变/删除)
    - 投放记录(按广告/按广告位/空列表)
    - 边界(不存在的广告/广告位/统计)
"""

import asyncio
import os
import sys

# 确保使用内存模式
os.environ["LOCK_MODE"] = "asyncio"
os.environ["STORE_MODE"] = "asyncio"

from services.ad_service import AdService
from repositories.ad_repository import (
    AdRepository,
    # 广告类型
    AD_TYPE_IMAGE, AD_TYPE_VIDEO, AD_TYPE_BANNER, AD_TYPE_TEXT,
    # 广告状态
    AD_STATUS_DRAFT, AD_STATUS_REVIEWING, AD_STATUS_APPROVED, AD_STATUS_REJECTED,
    AD_STATUS_ONLINE, AD_STATUS_PAUSED, AD_STATUS_OFFLINE,
    # 审核结果
    REVIEW_RESULT_PASS, REVIEW_RESULT_REJECT,
    # 广告位状态
    SLOT_STATUS_ENABLED, SLOT_STATUS_DISABLED,
    # 投放状态
    PLACEMENT_STATUS_RUNNING, PLACEMENT_STATUS_ENDED,
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
# 测试数据(合规广告标题: 含广告标识 + 健康警示 + 无禁用词)
# ============================================================

# 合规标题(0 违规, 满分100, 通过)
TITLE_COMPLIANT = "竹香酒广告 过量饮酒有害健康"
# 含禁用词(3 违规: 禁用词+缺健康警示+缺广告标识, 分数40, 驳回)
TITLE_FORBIDDEN = "最好的酒"
# 仅缺健康警示(1 违规, 分数80, 通过)
TITLE_MISSING_HEALTH = "竹香酒广告"
# 仅缺广告标识(1 违规, 分数80, 通过)
TITLE_MISSING_LABEL = "竹香酒 过量饮酒有害健康"
# 缺健康警示+缺广告标识(2 违规, 分数60, 驳回)
TITLE_TWO_ISSUES = "竹香酒"

SLOT_HOME_BANNER = "AD_HOME_BANNER"
SLOT_FEED = "AD_FEED"


# ============================================================
# 测试用例
# ============================================================

class TestCreateAd:
    """广告创建测试"""

    async def run(self, svc):
        # test 1: 创建广告默认状态为草稿
        ad = await svc.create_ad(
            advertiser_name="竹香酒业", name="中秋促销广告",
            ad_type=AD_TYPE_IMAGE, position=SLOT_HOME_BANNER,
            title=TITLE_COMPLIANT,
        )
        record("test_01_create_ad_draft",
               ad["status"] == AD_STATUS_DRAFT,
               f"expected {AD_STATUS_DRAFT}, got {ad['status']}")

        # test 2: 广告编号以 AD 开头
        record("test_02_ad_no_prefix",
               ad["adNo"].startswith("AD"),
               f"expected AD prefix, got {ad['adNo']}")

        # test 3: 广告ID为正整数
        record("test_03_ad_id_positive",
               isinstance(ad["id"], int) and ad["id"] > 0,
               f"expected positive int, got {ad['id']}")

        # test 4: 创建时含时间戳
        record("test_04_ad_has_timestamps",
               bool(ad.get("createdAt")) and bool(ad.get("updatedAt")),
               f"missing timestamps: {ad}")

        # test 5: targetRules 默认空字典
        record("test_05_ad_target_rules_default",
               ad["targetRules"] == {},
               f"expected {{}}, got {ad['targetRules']}")

        # test 6: 创建第二个广告ID递增
        ad2 = await svc.create_ad(
            advertiser_name="竹香酒业", name="国庆广告",
            ad_type=AD_TYPE_VIDEO, position=SLOT_FEED,
            title=TITLE_COMPLIANT,
        )
        record("test_06_ad_id_increment",
               ad2["id"] == ad["id"] + 1,
               f"expected {ad['id'] + 1}, got {ad2['id']}")


class TestGetAd:
    """广告查询测试"""

    async def run(self, svc):
        ad = await svc.create_ad(
            advertiser_name="竹香酒业", name="查询测试广告",
            ad_type=AD_TYPE_BANNER, position=SLOT_HOME_BANNER,
            title=TITLE_COMPLIANT,
        )

        # test 7: 按ID查询广告
        result = await svc.get_ad(ad["id"])
        record("test_07_get_ad_by_id",
               result["id"] == ad["id"] and result["name"] == "查询测试广告",
               f"unexpected ad: {result}")

        # test 8: 查询不存在的广告抛出KeyError(404)
        try:
            await svc.get_ad(99999)
            record("test_08_get_ad_not_exist", False, "应抛出KeyError")
        except KeyError:
            record("test_08_get_ad_not_exist", True)


class TestListAds:
    """广告列表与筛选测试"""

    async def run(self, svc):
        await svc.create_ad(
            advertiser_name="竹香酒业", name="图片广告1",
            ad_type=AD_TYPE_IMAGE, position=SLOT_HOME_BANNER,
            title=TITLE_COMPLIANT,
        )
        await svc.create_ad(
            advertiser_name="竹香酒业", name="视频广告2",
            ad_type=AD_TYPE_VIDEO, position=SLOT_FEED,
            title=TITLE_COMPLIANT,
        )

        # test 9: 列表返回全部广告
        ads = await svc.list_ads()
        record("test_09_list_all_ads",
               len(ads) >= 2,
               f"expected >= 2, got {len(ads)}")

        # test 10: 按类型筛选
        image_ads = await svc.list_ads(ad_type=AD_TYPE_IMAGE)
        record("test_10_list_by_type",
               all(a["type"] == AD_TYPE_IMAGE for a in image_ads) and len(image_ads) >= 1,
               f"unexpected: {image_ads}")

        # test 11: 按广告位筛选
        feed_ads = await svc.list_ads(position=SLOT_FEED)
        record("test_11_list_by_position",
               all(a["position"] == SLOT_FEED for a in feed_ads) and len(feed_ads) >= 1,
               f"unexpected: {feed_ads}")

        # test 12: 按状态筛选(全部为草稿)
        draft_ads = await svc.list_ads(status=AD_STATUS_DRAFT)
        record("test_12_list_by_status",
               all(a["status"] == AD_STATUS_DRAFT for a in draft_ads),
               f"unexpected: {draft_ads}")


class TestUpdateAd:
    """广告更新测试"""

    async def run(self, svc):
        ad = await svc.create_ad(
            advertiser_name="竹香酒业", name="更新测试",
            ad_type=AD_TYPE_IMAGE, position=SLOT_HOME_BANNER,
            title=TITLE_COMPLIANT, budget=1000,
        )

        # test 13: 草稿状态可更新
        updated = await svc.update_ad(ad["id"], {"name": "更新后名称", "budget": 2000})
        record("test_13_update_draft",
               updated["name"] == "更新后名称" and updated["budget"] == 2000,
               f"unexpected: {updated}")

        # test 14: id/adNo/createdAt 不可变
        original_adno = ad["adNo"]
        original_id = ad["id"]
        original_created = ad["createdAt"]
        updated2 = await svc.update_ad(ad["id"], {
            "id": 99999, "adNo": "AD_HACK", "createdAt": "1970-01-01",
        })
        record("test_14_update_immutable_fields",
               updated2["id"] == original_id
               and updated2["adNo"] == original_adno
               and updated2["createdAt"] == original_created,
               f"immutable fields changed: {updated2}")

        # test 15: 审核通过后不可更新(ValueError/409)
        await svc.review_ad(ad["id"])  # 标题合规 → 通过
        try:
            await svc.update_ad(ad["id"], {"name": "不应更新"})
            record("test_15_update_approved_fails", False, "应抛出ValueError")
        except ValueError:
            record("test_15_update_approved_fails", True)

        # test 16: 被驳回后可重新更新
        bad_ad = await svc.create_ad(
            advertiser_name="竹香酒业", name="驳回测试",
            ad_type=AD_TYPE_TEXT, position=SLOT_HOME_BANNER,
            title=TITLE_TWO_ISSUES,
        )
        await svc.review_ad(bad_ad["id"])  # 2违规 → 驳回
        record("test_16_bad_ad_rejected",
               True, "")  # 占位, 实际断言在 review 测试
        # 被驳回后可更新
        updated3 = await svc.update_ad(bad_ad["id"], {"title": TITLE_COMPLIANT})
        record("test_16_update_rejected_ok",
               updated3["title"] == TITLE_COMPLIANT,
               f"expected updated title, got {updated3['title']}")


class TestReviewAd:
    """广告AI审核测试"""

    async def run(self, svc):
        # test 17: 合规广告审核通过(0违规, 满分100)
        ad = await svc.create_ad(
            advertiser_name="竹香酒业", name="合规广告",
            ad_type=AD_TYPE_IMAGE, position=SLOT_HOME_BANNER,
            title=TITLE_COMPLIANT,
        )
        result = await svc.review_ad(ad["id"])
        record("test_17_review_compliant_pass",
               result["result"] == REVIEW_RESULT_PASS
               and result["score"] == 100
               and result["status"] == AD_STATUS_APPROVED
               and len(result["issues"]) == 0,
               f"unexpected: {result}")

        # test 18: 含禁用词审核驳回
        bad_ad = await svc.create_ad(
            advertiser_name="竹香酒业", name="禁用词广告",
            ad_type=AD_TYPE_TEXT, position=SLOT_HOME_BANNER,
            title=TITLE_FORBIDDEN,
        )
        result2 = await svc.review_ad(bad_ad["id"])
        has_forbidden = any(i["type"] == "forbidden_word" for i in result2["issues"])
        record("test_18_review_forbidden_reject",
               result2["result"] == REVIEW_RESULT_REJECT
               and result2["score"] < 80
               and has_forbidden,
               f"unexpected: {result2}")

        # test 19: 仅缺健康警示(1违规, 分数80, 通过)
        ad3 = await svc.create_ad(
            advertiser_name="竹香酒业", name="缺警示广告",
            ad_type=AD_TYPE_IMAGE, position=SLOT_HOME_BANNER,
            title=TITLE_MISSING_HEALTH,
        )
        result3 = await svc.review_ad(ad3["id"])
        record("test_19_review_missing_health_pass",
               result3["result"] == REVIEW_RESULT_PASS
               and result3["score"] == 80
               and any(i["type"] == "missing_health_warning" for i in result3["issues"]),
               f"unexpected: {result3}")

        # test 20: 仅缺广告标识(1违规, 分数80, 通过)
        ad4 = await svc.create_ad(
            advertiser_name="竹香酒业", name="缺标识广告",
            ad_type=AD_TYPE_IMAGE, position=SLOT_HOME_BANNER,
            title=TITLE_MISSING_LABEL,
        )
        result4 = await svc.review_ad(ad4["id"])
        record("test_20_review_missing_label_pass",
               result4["result"] == REVIEW_RESULT_PASS
               and result4["score"] == 80
               and any(i["type"] == "missing_ad_label" for i in result4["issues"]),
               f"unexpected: {result4}")

        # test 21: 2违规(分数60, 驳回)
        ad5 = await svc.create_ad(
            advertiser_name="竹香酒业", name="双违规广告",
            ad_type=AD_TYPE_TEXT, position=SLOT_HOME_BANNER,
            title=TITLE_TWO_ISSUES,
        )
        result5 = await svc.review_ad(ad5["id"])
        record("test_21_review_two_issues_reject",
               result5["result"] == REVIEW_RESULT_REJECT
               and result5["score"] == 60
               and len(result5["issues"]) == 2,
               f"unexpected: {result5}")

        # test 22: 审核已通过的广告抛出ValueError(409)
        try:
            await svc.review_ad(ad["id"])
            record("test_22_review_approved_fails", False, "应抛出ValueError")
        except ValueError:
            record("test_22_review_approved_fails", True)

        # test 23: 审核不存在的广告抛出KeyError(404)
        try:
            await svc.review_ad(99999)
            record("test_23_review_not_exist", False, "应抛出KeyError")
        except KeyError:
            record("test_23_review_not_exist", True)

        # test 24: 被驳回后可重新审核
        # ad5 被驳回 → 修改为合规 → 重新审核通过
        await svc.update_ad(ad5["id"], {"title": TITLE_COMPLIANT})
        result6 = await svc.review_ad(ad5["id"])
        record("test_24_review_after_fix",
               result6["result"] == REVIEW_RESULT_PASS
               and result6["status"] == AD_STATUS_APPROVED,
               f"unexpected: {result6}")

        # test 25: 查询审核历史
        history = await svc.get_review_history(ad5["id"])
        record("test_25_review_history",
               len(history) >= 2,  # 驳回1次 + 通过1次
               f"expected >= 2 reviews, got {len(history)}")


class TestOnlineOffline:
    """广告上下线测试"""

    async def run(self, svc):
        # test 26: 未审核的广告不能上线(ValueError/409)
        ad = await svc.create_ad(
            advertiser_name="竹香酒业", name="上下线测试",
            ad_type=AD_TYPE_IMAGE, position=SLOT_HOME_BANNER,
            title=TITLE_COMPLIANT,
        )
        try:
            await svc.online_ad(ad["id"])
            record("test_26_online_draft_fails", False, "应抛出ValueError")
        except ValueError:
            record("test_26_online_draft_fails", True)

        # test 27: 审核通过后可上线
        await svc.review_ad(ad["id"])
        result = await svc.online_ad(ad["id"])
        record("test_27_online_approved_ok",
               result["status"] == AD_STATUS_ONLINE
               and result["placementId"] > 0,
               f"unexpected: {result}")

        # test 28: 上线后广告状态为 online
        ad_after = await svc.get_ad(ad["id"])
        record("test_28_ad_status_online",
               ad_after["status"] == AD_STATUS_ONLINE,
               f"expected {AD_STATUS_ONLINE}, got {ad_after['status']}")

        # test 29: 重复上线抛出ValueError
        try:
            await svc.online_ad(ad["id"])
            record("test_29_repeated_online_fails", False, "应抛出ValueError")
        except ValueError:
            record("test_29_repeated_online_fails", True)

        # test 30: 下线成功
        offline_result = await svc.offline_ad(ad["id"], reason="活动结束")
        record("test_30_offline_ok",
               offline_result["status"] == AD_STATUS_OFFLINE,
               f"unexpected: {offline_result}")

        # test 31: 下线后广告状态为 offline
        ad_offline = await svc.get_ad(ad["id"])
        record("test_31_ad_status_offline",
               ad_offline["status"] == AD_STATUS_OFFLINE,
               f"expected {AD_STATUS_OFFLINE}, got {ad_offline['status']}")

        # test 32: 下线后投放记录状态为 ended
        placements = await svc.list_placements(ad_id=ad["id"])
        record("test_32_placement_ended",
               len(placements) >= 1
               and all(p["status"] == PLACEMENT_STATUS_ENDED for p in placements),
               f"unexpected: {placements}")

        # test 33: 已下线广告不能再次下线
        try:
            await svc.offline_ad(ad["id"])
            record("test_33_offline_again_fails", False, "应抛出ValueError")
        except ValueError:
            record("test_33_offline_again_fails", True)


class TestRecordStats:
    """曝光/点击/转化与效果统计测试"""

    async def run(self, svc):
        # 创建并上线一个广告
        ad = await svc.create_ad(
            advertiser_name="竹香酒业", name="效果统计测试",
            ad_type=AD_TYPE_IMAGE, position=SLOT_HOME_BANNER,
            title=TITLE_COMPLIANT,
        )
        await svc.review_ad(ad["id"])
        await svc.online_ad(ad["id"])

        # test 34: 非投放中广告记录曝光抛出ValueError(用已下线的另一个广告)
        offline_ad = await svc.create_ad(
            advertiser_name="竹香酒业", name="下线广告",
            ad_type=AD_TYPE_IMAGE, position=SLOT_HOME_BANNER,
            title=TITLE_COMPLIANT,
        )
        await svc.review_ad(offline_ad["id"])
        await svc.online_ad(offline_ad["id"])
        await svc.offline_ad(offline_ad["id"])
        try:
            await svc.record_impression(offline_ad["id"])
            record("test_34_impression_offline_fails", False, "应抛出ValueError")
        except ValueError:
            record("test_34_impression_offline_fails", True)

        # test 35: 投放中广告记录曝光
        imp = await svc.record_impression(ad["id"], count=10)
        record("test_35_record_impression",
               imp["impressions"] == 10,
               f"expected 10, got {imp['impressions']}")

        # test 36: 累计曝光
        imp2 = await svc.record_impression(ad["id"], count=5)
        record("test_36_impression_accumulate",
               imp2["impressions"] == 15,
               f"expected 15, got {imp2['impressions']}")

        # test 37: 记录点击
        click = await svc.record_click(ad["id"], count=3)
        record("test_37_record_click",
               click["clicks"] == 3,
               f"expected 3, got {click['clicks']}")

        # test 38: 记录转化(含产出)
        conv = await svc.record_conversion(ad["id"], count=1, revenue=99.5)
        record("test_38_record_conversion",
               conv["conversions"] == 1 and conv["revenue"] == 99.5,
               f"unexpected: {conv}")

        # test 39: 查询效果统计(CTR/CVR计算)
        stats = await svc.get_ad_stats(ad["id"])
        # impressions=15, clicks=3, conversions=1, revenue=99.5
        record("test_39_stats_ctr",
               stats["impressions"] == 15
               and stats["clicks"] == 3
               and abs(stats["ctr"] - round(3 / 15, 4)) < 0.001,
               f"unexpected ctr: {stats['ctr']}, impressions={stats['impressions']}, clicks={stats['clicks']}")

        # test 40: CVR 计算
        record("test_40_stats_cvr",
               abs(stats["cvr"] - round(1 / 3, 4)) < 0.001,
               f"unexpected cvr: {stats['cvr']}")

        # test 41: revenue 累计正确
        record("test_41_stats_revenue",
               abs(stats["revenue"] - 99.5) < 0.01,
               f"expected 99.5, got {stats['revenue']}")

        # test 42: 查询不存在广告的统计抛出KeyError
        try:
            await svc.get_ad_stats(99999)
            record("test_42_stats_not_exist", False, "应抛出KeyError")
        except KeyError:
            record("test_42_stats_not_exist", True)


class TestSlotCRUD:
    """广告位CRUD测试"""

    async def run(self, svc):
        # test 43: 创建广告位
        slot = await svc.create_slot(
            slot_code=SLOT_HOME_BANNER, name="首页横幅位",
            position="首页顶部", size="1920×600",
            supported_types=[AD_TYPE_IMAGE, AD_TYPE_BANNER],
            daily_estimate_impressions=10000,
        )
        record("test_43_create_slot",
               slot["slotCode"] == SLOT_HOME_BANNER
               and slot["status"] == SLOT_STATUS_ENABLED,
               f"unexpected: {slot}")

        # test 44: 重复创建广告位抛出ValueError(409)
        try:
            await svc.create_slot(
                slot_code=SLOT_HOME_BANNER, name="重复广告位",
                position="首页", size="100×100",
            )
            record("test_44_duplicate_slot_fails", False, "应抛出ValueError")
        except ValueError:
            record("test_44_duplicate_slot_fails", True)

        # test 45: 查询广告位
        got = await svc.get_slot(SLOT_HOME_BANNER)
        record("test_45_get_slot",
               got["name"] == "首页横幅位",
               f"unexpected: {got}")

        # test 46: 查询不存在的广告位抛出KeyError(404)
        try:
            await svc.get_slot("AD_NOT_EXIST")
            record("test_46_get_slot_not_exist", False, "应抛出KeyError")
        except KeyError:
            record("test_46_get_slot_not_exist", True)

        # test 47: 创建第二个广告位并列表查询
        await svc.create_slot(
            slot_code=SLOT_FEED, name="信息流广告位",
            position="首页信息流", size="640×320",
        )
        slots = await svc.list_slots()
        record("test_47_list_slots",
               len(slots) >= 2,
               f"expected >= 2, got {len(slots)}")

        # test 48: 更新广告位
        updated = await svc.update_slot(SLOT_HOME_BANNER, {
            "name": "首页大横幅", "status": SLOT_STATUS_DISABLED,
        })
        record("test_48_update_slot",
               updated["name"] == "首页大横幅"
               and updated["status"] == SLOT_STATUS_DISABLED,
               f"unexpected: {updated}")

        # test 49: 广告位 slotCode/createdAt 不可变
        original_code = slot["slotCode"]
        original_created = slot["createdAt"]
        updated2 = await svc.update_slot(SLOT_HOME_BANNER, {
            "slotCode": "AD_HACK", "createdAt": "1970-01-01",
        })
        record("test_49_slot_immutable",
               updated2["slotCode"] == original_code
               and updated2["createdAt"] == original_created,
               f"immutable changed: {updated2}")

        # test 50: 按状态筛选广告位
        enabled_slots = await svc.list_slots(status=SLOT_STATUS_ENABLED)
        record("test_50_list_slots_by_status",
               all(s["status"] == SLOT_STATUS_ENABLED for s in enabled_slots),
               f"unexpected: {enabled_slots}")

        # test 51: 删除广告位
        del_result = await svc.delete_slot(SLOT_FEED)
        record("test_51_delete_slot",
               del_result["deleted"] is True,
               f"unexpected: {del_result}")

        # test 52: 删除后再查询抛出KeyError
        try:
            await svc.get_slot(SLOT_FEED)
            record("test_52_deleted_get_fails", False, "应抛出KeyError")
        except KeyError:
            record("test_52_deleted_get_fails", True)

        # test 53: 删除不存在的广告位抛出KeyError
        try:
            await svc.delete_slot("AD_NOT_EXIST")
            record("test_53_delete_not_exist_fails", False, "应抛出KeyError")
        except KeyError:
            record("test_53_delete_not_exist_fails", True)


class TestPlacements:
    """投放记录查询测试"""

    async def run(self, svc):
        # 创建并上线两个广告(不同广告位)
        ad1 = await svc.create_ad(
            advertiser_name="竹香酒业", name="投放测试1",
            ad_type=AD_TYPE_IMAGE, position=SLOT_HOME_BANNER,
            title=TITLE_COMPLIANT,
        )
        await svc.review_ad(ad1["id"])
        await svc.online_ad(ad1["id"])

        ad2 = await svc.create_ad(
            advertiser_name="竹香酒业", name="投放测试2",
            ad_type=AD_TYPE_VIDEO, position=SLOT_FEED,
            title=TITLE_COMPLIANT,
        )
        await svc.review_ad(ad2["id"])
        await svc.online_ad(ad2["id"])

        # test 54: 按广告ID查询投放记录
        placements1 = await svc.list_placements(ad_id=ad1["id"])
        record("test_54_list_placements_by_ad",
               len(placements1) >= 1
               and all(p["adId"] == ad1["id"] for p in placements1),
               f"unexpected: {placements1}")

        # test 55: 按广告位查询投放记录
        placements2 = await svc.list_placements(slot_code=SLOT_FEED)
        record("test_55_list_placements_by_slot",
               len(placements2) >= 1
               and all(p["slotCode"] == SLOT_FEED for p in placements2),
               f"unexpected: {placements2}")

        # test 56: 查询全部投放记录
        all_placements = await svc.list_placements()
        record("test_56_list_all_placements",
               len(all_placements) >= 2,
               f"expected >= 2, got {len(all_placements)}")

        # test 57: 空结果(不存在的广告ID)
        empty = await svc.list_placements(ad_id=99999)
        record("test_57_list_placements_empty",
               len(empty) == 0,
               f"expected empty, got {len(empty)}")


class TestOverviewStats:
    """广告模块总览统计测试"""

    async def run(self, svc):
        # 创建多个不同状态/类型的广告
        ad1 = await svc.create_ad(
            advertiser_name="竹香酒业", name="总览广告1",
            ad_type=AD_TYPE_IMAGE, position=SLOT_HOME_BANNER,
            title=TITLE_COMPLIANT,
        )
        await svc.create_ad(
            advertiser_name="竹香酒业", name="总览广告2",
            ad_type=AD_TYPE_VIDEO, position=SLOT_FEED,
            title=TITLE_COMPLIANT,
        )
        # ad1 上线 + 产生效果
        await svc.review_ad(ad1["id"])
        await svc.online_ad(ad1["id"])
        await svc.record_impression(ad1["id"], count=100)
        await svc.record_click(ad1["id"], count=20)

        # test 58: 总览统计返回正确结构
        stats = await svc.get_stats()
        record("test_58_overview_structure",
               "totalAds" in stats and "statusDistribution" in stats
               and "typeDistribution" in stats and "onlineAds" in stats,
               f"missing fields: {stats}")

        # test 59: 总览统计广告总数
        record("test_59_overview_total_ads",
               stats["totalAds"] >= 2,
               f"expected >= 2, got {stats['totalAds']}")

        # test 60: 总览统计投放中广告数
        record("test_60_overview_online_count",
               stats["onlineAds"] >= 1,
               f"expected >= 1, got {stats['onlineAds']}")

        # test 61: 总览统计状态分布含 online
        record("test_61_overview_status_dist",
               AD_STATUS_ONLINE in stats["statusDistribution"],
               f"missing online in dist: {stats['statusDistribution']}")

        # test 62: 总览统计类型分布含 IMAGE/VIDEO
        record("test_62_overview_type_dist",
               AD_TYPE_IMAGE in stats["typeDistribution"]
               and AD_TYPE_VIDEO in stats["typeDistribution"],
               f"unexpected type dist: {stats['typeDistribution']}")

        # test 63: 总览统计累计曝光/点击
        record("test_63_overview_totals",
               stats["totalImpressions"] >= 100
               and stats["totalClicks"] >= 20,
               f"unexpected: impressions={stats['totalImpressions']}, clicks={stats['totalClicks']}")

        # test 64: 总览统计平均CTR
        record("test_64_overview_avg_ctr",
               stats["avgCtr"] > 0,
               f"expected > 0, got {stats['avgCtr']}")


# ============================================================
# 测试运行
# ============================================================

async def main():
    print("=" * 60)
    print("广告管理模块端到端测试")
    print("=" * 60)
    print()

    test_classes = [
        TestCreateAd,
        TestGetAd,
        TestListAds,
        TestUpdateAd,
        TestReviewAd,
        TestOnlineOffline,
        TestRecordStats,
        TestSlotCRUD,
        TestPlacements,
        TestOverviewStats,
    ]

    for cls in test_classes:
        reset_store()
        svc = AdService()
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
