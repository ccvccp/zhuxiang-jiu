"""位置地图管理模块端到端测试(Service 层, 无需 Docker/fastapi)

直接调用 LocationService 方法, 模拟 12 个 HTTP 接口的业务逻辑。
使用 asyncio 内存模式(LOCK_MODE=asyncio, STORE_MODE=asyncio)。

运行:
    $env:LOCK_MODE="asyncio"; $env:STORE_MODE="asyncio"
    python test_location_routes.py

覆盖 12 个接口对应的业务方法:
    1. 地址管理(5):  add_address / list_addresses / update_address / delete_address / set_default
    2. 门店(2):       list_nearby_stores / get_store
    3. 代理商(2):     list_agent_locations / get_agent_location
    4. 物流(1):       create_shipment_track / update / get
    5. 配送(1):       check_delivery_point
    6. 存证(2):       add_evidence / verify_evidence_by_hash
    7. 统计(1):       get_stats
"""

import asyncio
import os
import sys

# 确保使用内存模式
os.environ["LOCK_MODE"] = "asyncio"
os.environ["STORE_MODE"] = "asyncio"

from services.location_service import LocationService
from repositories.location_repository import (
    LocationRepository,
    haversine_km,
    # 门店类型
    STORE_TYPE_FLAGSHIP, STORE_TYPE_EXPERIENCE, STORE_TYPE_EXCLUSIVE,
    STORE_STATUS_OPEN, STORE_STATUS_CLOSED,
    # 代理商等级
    AGENT_LEVEL_DIAMOND, AGENT_LEVEL_GOLD, AGENT_LEVEL_SILVER,
    # 物流状态
    SHIPMENT_STATUS_IN_TRANSIT, SHIPMENT_STATUS_DELIVERING, SHIPMENT_STATUS_SIGNED,
    # 配送范围类型
    ZONE_TYPE_NATIONAL, ZONE_TYPE_CITY, ZONE_TYPE_SELF, ZONE_TYPE_REMOTE,
    # 存证类型
    EVIDENCE_TYPE_ADDRESS, EVIDENCE_TYPE_LOCATION, EVIDENCE_TYPE_LOGISTICS,
    EVIDENCE_TYPE_DELIVERY, EVIDENCE_TYPE_HEATMAP, EVIDENCE_TYPE_SITE,
    # 常量
    MAX_ADDRESSES_PER_USER,
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

USER_ID_1 = 1001
USER_ID_2 = 1002

# 泰安市中心坐标
TAI_AN_LNG = 117.089
TAI_AN_LAT = 36.200
# 济南坐标(约60km外)
JINAN_LNG = 117.000
JINAN_LAT = 36.675
# 北京坐标(远)
BEIJING_LNG = 116.407
BEIJING_LAT = 39.904

STORE_NAME_1 = "竹香酒泰安旗舰店"
STORE_NAME_2 = "竹香酒济南体验店"
AGENT_NAME_1 = "泰安代理商"
AGENT_NAME_2 = "济南代理商"
SHIPMENT_ID_1 = "SF20260822001"
ORDER_ID_1 = 5001


# ============================================================
# 测试用例
# ============================================================

class TestAddressManage:
    """收货地址管理测试"""

    async def run(self, svc):
        # test 1: 新增地址
        result = await svc.add_address(
            USER_ID_1, "张三", "13800000001",
            "山东省", "泰安市", "泰山区",
            "竹香路1号", TAI_AN_LNG, TAI_AN_LAT,
            label="home", is_default=True
        )
        record("test_01_add_address_success",
               result["id"] > 0 and result["isDefault"] is True,
               f"expected id>0/default=True, got {result.get('id')}/{result.get('isDefault')}")

        # test 2: 区块链存证生成
        record("test_02_evidence_generated",
               result.get("evidenceHash") is not None,
               "expected evidence hash")

        # test 3: 新增第二个地址(非默认)
        result2 = await svc.add_address(
            USER_ID_1, "张三", "13800000001",
            "山东省", "泰安市", "岱岳区",
            "竹香路2号", TAI_AN_LNG + 0.01, TAI_AN_LAT + 0.01,
            label="company"
        )
        record("test_03_add_second_address",
               result2["id"] > 0 and result2["isDefault"] is False,
               "expected id>0/default=False")

        # test 4: 地址列表(默认排前)
        addresses = await svc.list_addresses(USER_ID_1)
        record("test_04_list_addresses",
               len(addresses) == 2 and addresses[0]["isDefault"] is True,
               f"expected 2/default first, got {len(addresses)}/{addresses[0].get('isDefault')}")

        # test 5: 设为默认(清除旧默认)
        await svc.set_default_address(result2["id"], USER_ID_1)
        addresses = await svc.list_addresses(USER_ID_1)
        default_addr = [a for a in addresses if a["isDefault"]][0]
        record("test_05_set_default",
               default_addr["id"] == result2["id"],
               f"expected id={result2['id']}, got {default_addr['id']}")

        # test 6: 旧默认被清除
        old_default = [a for a in addresses if a["id"] == result["id"]][0]
        record("test_06_old_default_cleared",
               old_default["isDefault"] is False,
               f"expected False, got {old_default['isDefault']}")

        # test 7: 编辑地址
        updated = await svc.update_address(result["id"], receiver_name="李四")
        record("test_07_update_address",
               updated["receiverName"] == "李四",
               f"expected 李四, got {updated['receiverName']}")

        # test 8: 编辑不存在的地址
        try:
            await svc.update_address(99999, receiver_name="X")
            record("test_08_update_nonexistent", False, "应抛出KeyError")
        except KeyError:
            record("test_08_update_nonexistent", True)

        # test 9: 删除地址
        deleted = await svc.delete_address(result2["id"])
        record("test_09_delete_address",
               deleted["deleted"] is True,
               f"expected True, got {deleted.get('deleted')}")

        # test 10: 删除后列表减少
        addresses = await svc.list_addresses(USER_ID_1)
        record("test_10_list_after_delete",
               len(addresses) == 1,
               f"expected 1, got {len(addresses)}")

        # test 11: 删除不存在的地址
        try:
            await svc.delete_address(99999)
            record("test_11_delete_nonexistent", False, "应抛出KeyError")
        except KeyError:
            record("test_11_delete_nonexistent", True)

        # test 12: 缺失必填字段
        try:
            await svc.add_address(USER_ID_1, "", "138", "省", "市", "区", "址")
            record("test_12_missing_fields", False, "应抛出ValueError")
        except ValueError:
            record("test_12_missing_fields", True)

        # test 13: 地址不属于该用户(设默认时)
        try:
            await svc.set_default_address(result["id"], USER_ID_2)
            record("test_13_not_owner", False, "应抛出ValueError")
        except ValueError:
            record("test_13_not_owner", True)


class TestAddressLimit:
    """地址上限测试"""

    async def run(self, svc):
        # test 14: 地址数量上限(20条)
        for i in range(MAX_ADDRESSES_PER_USER):
            await svc.add_address(
                USER_ID_1, f"用户{i}", "13800000000",
                "山东省", "泰安市", "泰山区",
                f"地址{i}号"
            )

        try:
            await svc.add_address(
                USER_ID_1, "超出", "138", "省", "市", "区", "址"
            )
            record("test_14_address_limit", False, "应抛出ValueError")
        except ValueError:
            record("test_14_address_limit", True)


class TestNearbyStores:
    """附近门店测试"""

    async def run(self, svc):
        # 准备: 新增门店(泰安+济南)
        store1 = await svc.add_store(
            STORE_NAME_1, STORE_TYPE_FLAGSHIP,
            "山东省", "泰安市", "泰山区",
            "竹香路1号", TAI_AN_LNG, TAI_AN_LAT,
            phone="0538-8888888", open_hours="09:00-18:00"
        )
        store2 = await svc.add_store(
            STORE_NAME_2, STORE_TYPE_EXPERIENCE,
            "山东省", "济南市", "历下区",
            "经十路9999号", JINAN_LNG, JINAN_LAT,
            phone="0531-6666666"
        )

        # test 15: 附近门店(泰安坐标, 100km半径)
        stores = await svc.list_nearby_stores(TAI_AN_LNG, TAI_AN_LAT, 100.0)
        record("test_15_nearby_stores",
               len(stores) >= 2,
               f"expected >=2, got {len(stores)}")

        # test 16: 按距离排序
        record("test_16_sorted_by_distance",
               stores[0]["distance"] <= stores[1]["distance"],
               f"expected ascending, got {stores[0]['distance']}/{stores[1]['distance']}")

        # test 17: 泰安门店距离最近
        record("test_17_nearest_store",
               stores[0]["storeName"] == STORE_NAME_1,
               f"expected {STORE_NAME_1}, got {stores[0]['storeName']}")

        # test 18: 小半径筛选(1km, 只含泰安门店)
        stores = await svc.list_nearby_stores(TAI_AN_LNG, TAI_AN_LAT, 1.0)
        record("test_18_small_radius",
               len(stores) == 1 and stores[0]["storeName"] == STORE_NAME_1,
               f"expected 1/{STORE_NAME_1}, got {len(stores)}/{stores[0]['storeName'] if stores else 'N/A'}")

        # test 19: 门店详情
        store = await svc.get_store(store1["id"])
        record("test_19_get_store_detail",
               store["id"] == store1["id"] and store["storeType"] == STORE_TYPE_FLAGSHIP,
               f"expected id={store1['id']}/{STORE_TYPE_FLAGSHIP}")

        # test 20: 查询不存在的门店
        try:
            await svc.get_store(99999)
            record("test_20_get_nonexistent_store", False, "应抛出KeyError")
        except KeyError:
            record("test_20_get_nonexistent_store", True)

        # test 21: 门店列表(按城市筛选)
        stores = await svc.list_stores(city="泰安市")
        record("test_21_list_by_city",
               all(s["city"] == "泰安市" for s in stores) and len(stores) >= 1,
               "expected all 泰安市")

        # test 22: 门店列表(按类型筛选)
        stores = await svc.list_stores(store_type=STORE_TYPE_FLAGSHIP)
        record("test_22_list_by_type",
               all(s["storeType"] == STORE_TYPE_FLAGSHIP for s in stores),
               "expected all flagship")


class TestAgentLocations:
    """代理商查询测试"""

    async def run(self, svc):
        # 准备: 新增代理商(泰安钻石+济南金牌)
        agent1 = await svc.add_agent_location(
            2001, AGENT_NAME_1, AGENT_LEVEL_DIAMOND,
            "山东省", "泰安市", "竹香路1号",
            TAI_AN_LNG, TAI_AN_LAT,
            contact_name="王经理", contact_phone="0538-8888888"
        )
        agent2 = await svc.add_agent_location(
            2002, AGENT_NAME_2, AGENT_LEVEL_GOLD,
            "山东省", "济南市", "经十路9999号",
            JINAN_LNG, JINAN_LAT,
            contact_name="李经理"
        )

        # test 23: 代理商列表(按省筛选)
        agents = await svc.list_agent_locations(province="山东省")
        record("test_23_list_by_province",
               all(a["province"] == "山东省" for a in agents) and len(agents) >= 2,
               "expected all 山东省")

        # test 24: 代理商列表(按等级筛选)
        agents = await svc.list_agent_locations(agent_level=AGENT_LEVEL_DIAMOND)
        record("test_24_list_by_level",
               all(a["agentLevel"] == AGENT_LEVEL_DIAMOND for a in agents) and len(agents) >= 1,
               "expected all diamond")

        # test 25: 附近代理商(泰安坐标, 100km半径)
        agents = await svc.list_nearby_agents(TAI_AN_LNG, TAI_AN_LAT, 100.0)
        record("test_25_nearby_agents",
               len(agents) >= 2 and agents[0]["distance"] <= agents[1]["distance"],
               "expected >=2/sorted")

        # test 26: 泰安代理商距离最近
        record("test_26_nearest_agent",
               agents[0]["agentName"] == AGENT_NAME_1,
               f"expected {AGENT_NAME_1}, got {agents[0]['agentName']}")

        # test 27: 代理商详情
        agent = await svc.get_agent_location(agent1["id"])
        record("test_27_get_agent_detail",
               agent["id"] == agent1["id"] and agent["agentLevel"] == AGENT_LEVEL_DIAMOND,
               f"expected id={agent1['id']}/diamond")

        # test 28: 查询不存在的代理商
        try:
            await svc.get_agent_location(99999)
            record("test_28_get_nonexistent_agent", False, "应抛出KeyError")
        except KeyError:
            record("test_28_get_nonexistent_agent", True)


class TestShipmentTrack:
    """物流轨迹追踪测试"""

    async def run(self, svc):
        # test 29: 创建物流轨迹
        result = await svc.create_shipment_track(
            SHIPMENT_ID_1, ORDER_ID_1, "顺丰速运", "SF1234567890",
            TAI_AN_LNG, TAI_AN_LAT,  # 发货地(泰安)
            JINAN_LNG, JINAN_LAT,    # 收货地(济南)
            current_lng=TAI_AN_LNG, current_lat=TAI_AN_LAT,
            current_address="泰安市泰山区竹香路1号"
        )
        record("test_29_create_track_success",
               result["shipmentId"] == SHIPMENT_ID_1 and result["id"] > 0,
               f"expected {SHIPMENT_ID_1}/{'>0'}")

        # test 30: 初始状态为在途
        record("test_30_initial_status",
               result["status"] == SHIPMENT_STATUS_IN_TRANSIT,
               f"expected {SHIPMENT_STATUS_IN_TRANSIT}, got {result['status']}")

        # test 31: 区块链存证生成
        record("test_31_evidence_generated",
               result.get("evidenceHash") is not None,
               "expected evidence hash")

        # test 32: 按运单号查询轨迹
        track_id = result["id"]
        track = await svc.get_track_by_shipment(SHIPMENT_ID_1)
        record("test_32_get_by_shipment",
               track["shipmentId"] == SHIPMENT_ID_1,
               f"expected {SHIPMENT_ID_1}")

        # test 33: 更新物流轨迹(位置+状态)
        updated = await svc.update_shipment_track(
            track_id,
            current_lng=(TAI_AN_LNG + JINAN_LNG) / 2,
            current_lat=(TAI_AN_LAT + JINAN_LAT) / 2,
            current_address="途中最远点",
            status=SHIPMENT_STATUS_DELIVERING
        )
        record("test_33_update_track",
               updated["status"] == SHIPMENT_STATUS_DELIVERING,
               f"expected {SHIPMENT_STATUS_DELIVERING}, got {updated['status']}")

        # test 34: 剩余距离计算
        record("test_34_remaining_distance",
               "remainingDistance" in updated and updated["remainingDistance"] > 0,
               f"expected remainingDistance>0, got {updated.get('remainingDistance')}")

        # test 35: 签收状态更新
        updated = await svc.update_shipment_track(
            track_id,
            current_lng=JINAN_LNG, current_lat=JINAN_LAT,
            current_address="济南市历下区经十路9999号",
            status=SHIPMENT_STATUS_SIGNED
        )
        record("test_35_signed_status",
               updated["status"] == SHIPMENT_STATUS_SIGNED,
               f"expected {SHIPMENT_STATUS_SIGNED}, got {updated['status']}")

        # test 36: 签收后剩余距离接近0
        record("test_36_zero_remaining",
               updated["remainingDistance"] < 1.0,
               f"expected <1.0, got {updated.get('remainingDistance')}")

        # test 37: 查询不存在的运单
        try:
            await svc.get_track_by_shipment("NONEXISTENT")
            record("test_37_get_nonexistent", False, "应抛出KeyError")
        except KeyError:
            record("test_37_get_nonexistent", True)

        # test 38: 更新不存在的轨迹
        try:
            await svc.update_shipment_track(99999, status=SHIPMENT_STATUS_SIGNED)
            record("test_38_update_nonexistent", False, "应抛出KeyError")
        except KeyError:
            record("test_38_update_nonexistent", True)


class TestDeliveryZone:
    """配送范围检测测试"""

    async def run(self, svc):
        # 准备: 新增配送范围
        await svc.add_delivery_zone(
            "全国配送", ZONE_TYPE_NATIONAL,
            shipping_fee=15.0, free_threshold=200.0,
            delivery_time="3-5天"
        )
        await svc.add_delivery_zone(
            "泰安同城", ZONE_TYPE_CITY,
            center_lng=TAI_AN_LNG, center_lat=TAI_AN_LAT,
            radius=30.0, shipping_fee=8.0, free_threshold=100.0,
            delivery_time="当日达"
        )

        # test 39: 全国配送范围(任意坐标都匹配)
        result = await svc.check_delivery_point(BEIJING_LNG, BEIJING_LAT)
        record("test_39_national_zone_match",
               result["inDeliveryRange"] is True,
               f"expected True, got {result['inDeliveryRange']}")

        # test 40: 同城配送范围匹配(泰安坐标)
        result = await svc.check_delivery_point(TAI_AN_LNG, TAI_AN_LAT)
        matched_types = [z["zoneType"] for z in result["matchedZones"]]
        record("test_40_city_zone_match",
               ZONE_TYPE_NATIONAL in matched_types and ZONE_TYPE_CITY in matched_types,
               f"expected national+city, got {matched_types}")

        # test 41: 同城配送范围不匹配(北京坐标)
        result = await svc.check_delivery_point(BEIJING_LNG, BEIJING_LAT)
        matched_types = [z["zoneType"] for z in result["matchedZones"]]
        record("test_41_city_zone_no_match",
               ZONE_TYPE_CITY not in matched_types,
               f"expected no city zone, got {matched_types}")

        # test 42: 查询配送范围列表
        zones = await svc.list_delivery_zones()
        record("test_42_list_zones",
               len(zones) >= 2,
               f"expected >=2, got {len(zones)}")

        # test 43: 按类型筛选配送范围
        zones = await svc.list_delivery_zones(zone_type=ZONE_TYPE_NATIONAL)
        record("test_43_filter_by_type",
               all(z["zoneType"] == ZONE_TYPE_NATIONAL for z in zones),
               "expected all national")


class TestBlockchainEvidence:
    """区块链存证测试"""

    async def run(self, svc):
        # test 44: 新增地址存证
        result = await svc.add_evidence(EVIDENCE_TYPE_ADDRESS, "地址存证数据")
        record("test_44_add_evidence_success",
               result["evidenceType"] == EVIDENCE_TYPE_ADDRESS and result["id"] > 0,
               f"expected address/{'>0'}")

        # test 45: 存证哈希生成
        evidence_hash = result["evidenceHash"]
        record("test_45_hash_generated",
               evidence_hash is not None and len(evidence_hash) > 0,
               f"expected hash, got {evidence_hash}")

        # test 46: 按哈希验证存证
        verify_result = await svc.verify_evidence_by_hash(evidence_hash)
        record("test_46_verify_by_hash",
               verify_result["verified"] is True,
               f"expected True, got {verify_result['verified']}")

        # test 47: 验证不存在的哈希
        try:
            await svc.verify_evidence_by_hash("0xNONEXISTENT")
            record("test_47_verify_nonexistent", False, "应抛出KeyError")
        except KeyError:
            record("test_47_verify_nonexistent", True)

        # test 48: 非法存证类型
        try:
            await svc.add_evidence("invalid_type")
            record("test_48_invalid_type", False, "应抛出ValueError")
        except ValueError:
            record("test_48_invalid_type", True)

        # test 49: 查询存证列表
        evidences = await svc.list_evidence(evidence_type=EVIDENCE_TYPE_ADDRESS)
        record("test_49_list_by_type",
               all(e["evidenceType"] == EVIDENCE_TYPE_ADDRESS for e in evidences) and len(evidences) >= 1,
               "expected all address")

        # test 50: 查询存证详情
        evidence_id = result["id"]
        detail = await svc.get_evidence(evidence_id)
        record("test_50_get_detail",
               detail["id"] == evidence_id,
               f"expected id={evidence_id}")


class TestDistanceCalc:
    """距离计算测试"""

    async def run(self, svc):
        # test 51: 计算泰安→济南距离
        result = await svc.calculate_distance(TAI_AN_LNG, TAI_AN_LAT,
                                                 JINAN_LNG, JINAN_LAT)
        record("test_51_calculate_distance",
               result["distance"] > 0 and result["unit"] == "km",
               f"expected >0/km, got {result['distance']}/{result['unit']}")

        # test 52: 泰安→济南约60km
        record("test_52_distance_range",
               50 < result["distance"] < 80,
               f"expected 50-80km, got {result['distance']}")

        # test 53: 相同点距离为0
        result = await svc.calculate_distance(TAI_AN_LNG, TAI_AN_LAT,
                                                 TAI_AN_LNG, TAI_AN_LAT)
        record("test_53_same_point",
               result["distance"] == 0.0,
               f"expected 0.0, got {result['distance']}")


class TestStats:
    """统计测试"""

    async def run(self, svc):
        # 准备数据
        await svc.add_store("门店1", STORE_TYPE_FLAGSHIP, "山东省", "泰安市", "泰山区",
                              "址1", TAI_AN_LNG, TAI_AN_LAT)
        await svc.add_store("门店2", STORE_TYPE_EXPERIENCE, "山东省", "济南市", "历下区",
                              "址2", JINAN_LNG, JINAN_LAT)
        await svc.add_agent_location(3001, "代理1", AGENT_LEVEL_DIAMOND,
                                       "山东省", "泰安市", "址", TAI_AN_LNG, TAI_AN_LAT)
        await svc.create_shipment_track("SF001", 7001, "顺丰", "SF001",
                                           TAI_AN_LNG, TAI_AN_LAT, JINAN_LNG, JINAN_LAT)
        await svc.add_delivery_zone("全国", ZONE_TYPE_NATIONAL)
        await svc.add_evidence(EVIDENCE_TYPE_ADDRESS, "data")

        # test 54: 统计字段完整性
        stats = await svc.get_stats()
        record("test_54_stats_fields",
               all(k in stats for k in ["totalStores", "totalAgentLocations",
                                          "totalShipmentTracks", "totalDeliveryZones",
                                          "totalEvidence", "storeTypeCount",
                                          "agentLevelCount", "trackStatusCount"]),
               f"missing fields: {stats}")

        # test 55: 统计数量正确
        record("test_55_stats_count",
               stats["totalStores"] >= 2 and stats["totalAgentLocations"] >= 1,
               f"expected >=2/>=1, got {stats['totalStores']}/{stats['totalAgentLocations']}")

        # test 56: 门店类型分布
        record("test_56_store_type_dist",
               STORE_TYPE_FLAGSHIP in stats["storeTypeCount"],
               f"expected flagship in: {stats['storeTypeCount']}")

        # test 57: 代理商等级分布
        record("test_57_agent_level_dist",
               AGENT_LEVEL_DIAMOND in stats["agentLevelCount"],
               f"expected diamond in: {stats['agentLevelCount']}")

        # test 58: 物流状态分布
        record("test_58_track_status_dist",
               SHIPMENT_STATUS_IN_TRANSIT in stats["trackStatusCount"],
               f"expected in_transit in: {stats['trackStatusCount']}")


# ============================================================
# 测试运行
# ============================================================

async def main():
    print("=" * 60)
    print("位置地图管理模块端到端测试")
    print("=" * 60)
    print()

    test_classes = [
        TestAddressManage,
        TestAddressLimit,
        TestNearbyStores,
        TestAgentLocations,
        TestShipmentTrack,
        TestDeliveryZone,
        TestBlockchainEvidence,
        TestDistanceCalc,
        TestStats,
    ]

    for cls in test_classes:
        reset_store()
        svc = LocationService()
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
