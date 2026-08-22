"""市级网店优先原则-下单入口决策测试(Service 层, 无需 fastapi)

运行:
    $env:LOCK_MODE="asyncio"; $env:STORE_MODE="asyncio"
    python test_citystore_entry.py

覆盖 decide_order_entry 的 11 个场景:
    输入解析(5):  cityCode 精确 / adcode 转市级 / cityName 匹配 /
                  经纬度附近门店推断 / 会员默认地址兜底
    决策结果(6):  运营中市店→市店入口 / 预警市店→市店入口 /
                  暂停市店→本站入口 / 无市店城市→本站入口 /
                  经纬度无附近门店→本站入口 / 无任何位置→本站入口
"""

import asyncio
import os
import sys

# 确保使用内存模式
os.environ["LOCK_MODE"] = "asyncio"
os.environ["STORE_MODE"] = "asyncio"

from services.citystore_service import CityStoreService
from repositories.citystore_repository import (
    CityStoreRepository,
    STORE_STATUS_OPERATING, STORE_STATUS_WARNING, STORE_STATUS_SUSPENDED,
)
from repositories.location_repository import LocationRepository
from repositories.store import reset_store as _reset_store_impl

PASS = 0
FAIL = 0
RESULTS = []


def record(name, passed, detail=""):
    global PASS, FAIL
    if passed:
        PASS += 1
        RESULTS.append(f"  [PASS] {name}")
    else:
        FAIL += 1
        RESULTS.append(f"  [FAIL] {name} -- {detail}")


def reset_store():
    _reset_store_impl()


def _store(store_code, city_code, city_name, province_code, province_name,
           status, store_name, discount=80):
    return {
        "storeCode": store_code,
        "storeName": store_name,
        "memberId": 1001,
        "cityCode": city_code,
        "cityName": city_name,
        "provinceCode": province_code,
        "provinceName": province_name,
        "status": status,
        "currentDiscount": discount,
        "openDate": "2026-01-01",
        "closeDate": "",
        "salesChannel": 1,
    }


async def seed():
    """造数: 北京运营中店 / 深圳预警店 / 上海暂停店 / 无店城市(杭州)"""
    reset_store()
    city_repo = CityStoreRepository()
    await city_repo.save_store(_store(
        "CS-110100-01", "110100", "北京市", "110000", "北京市",
        STORE_STATUS_OPERATING, "北京竹奕市店", discount=80))
    await city_repo.save_store(_store(
        "CS-440300-01", "440300", "深圳市", "440000", "广东省",
        STORE_STATUS_WARNING, "深圳竹奕市店", discount=90))
    await city_repo.save_store(_store(
        "CS-310100-01", "310100", "上海市", "310000", "上海市",
        STORE_STATUS_SUSPENDED, "上海竹奕市店", discount=90))

    # location: 北京朝阳区门店(天安门附近) + 会员默认地址
    loc_repo = LocationRepository()
    await loc_repo.create_store({
        "storeName": "北京朝阳体验店", "province": "北京市", "city": "北京市",
        "district": "朝阳区", "address": "东长安街1号",
        "longitude": 116.397, "latitude": 39.909, "status": "open",
    })
    await loc_repo.create_address({
        "userId": 2001, "receiverName": "测试用户", "receiverPhone": "13800000001",
        "province": "北京市", "city": "北京市", "district": "海淀区",
        "detailAddress": "中关村大街1号", "longitude": 116.31, "latitude": 39.98,
        "adcode": "110108", "isDefault": True,
    })


async def main():
    print("=" * 60)
    print("市级网店优先原则 - 下单入口决策测试")
    print("=" * 60)

    svc = CityStoreService()

    # ---------------- 输入解析 ----------------

    # 1. cityCode 精确匹配 → 北京市店入口
    await seed()
    r = await svc.decide_order_entry(city_code="110100")
    record("test_01_citycode_match_citystore_entry",
           r["entry"] == "citystore"
           and r["store"]["storeCode"] == "CS-110100-01"
           and r["orderEntry"]["storeCode"] == "CS-110100-01"
           and r["orderEntry"]["currentDiscount"] == 80
           and r["citySource"] == "cityCode",
           f"entry={r['entry']}, store={r['store'] and r['store']['storeCode']}")

    # 2. adcode 区县码自动转市级(110105 朝阳 → 110100) → 市店入口
    r = await svc.decide_order_entry(adcode="110105")
    record("test_02_adcode_to_citycode",
           r["entry"] == "citystore" and r["citySource"] == "adcode",
           f"entry={r['entry']}, source={r['citySource']}")

    # 3. cityName 匹配("北京市"含市后缀归一化) → 市店入口
    r = await svc.decide_order_entry(city_name="北京市",
                                     province_name="北京市")
    record("test_03_cityname_match",
           r["entry"] == "citystore" and r["citySource"] == "cityName",
           f"entry={r['entry']}, source={r['citySource']}")

    # 4. cityName 去后缀匹配("北京" → 北京市店)
    r = await svc.decide_order_entry(city_name="北京")
    record("test_04_cityname_suffix_normalized",
           r["entry"] == "citystore" and r["store"]["cityName"] == "北京市",
           f"entry={r['entry']}")

    # 5. 经纬度 → 附近门店(朝阳店)推断城市 → 市店入口 + nearbyStores 附带
    r = await svc.decide_order_entry(longitude=116.40, latitude=39.90)
    record("test_05_location_nearby_store_inference",
           r["entry"] == "citystore" and r["citySource"] == "location"
           and len(r["nearbyStores"]) >= 1
           and r["nearbyStores"][0]["city"] == "北京市",
           f"entry={r['entry']}, source={r.get('citySource')}, "
           f"nearby={len(r['nearbyStores'])}")

    # 6. 会员默认地址兜底(adcode=110108 海淀 → 110100) → 市店入口
    r = await svc.decide_order_entry(member_id=2001)
    record("test_06_default_address_fallback",
           r["entry"] == "citystore" and r["citySource"] == "defaultAddress",
           f"entry={r['entry']}, source={r.get('citySource')}")

    # ---------------- 决策结果 ----------------

    # 7. 预警市店仍在营业 → 市店入口
    r = await svc.decide_order_entry(city_code="440300")
    record("test_07_warning_store_still_orderable",
           r["entry"] == "citystore"
           and r["store"]["statusName"] == "预警",
           f"entry={r['entry']}, status={r['store'] and r['store']['statusName']}")

    # 8. 暂停市店 → 本站入口 + reason 说明
    r = await svc.decide_order_entry(city_code="310100")
    record("test_08_suspended_store_fallback_site",
           r["entry"] == "site" and "暂停" in r["reason"]
           and r["orderEntry"]["url"] == "/api/order/create"
           and r["store"] is not None,
           f"entry={r['entry']}, reason={r['reason']}")

    # 9. 无市店城市(杭州 330100) → 本站入口
    r = await svc.decide_order_entry(city_code="330100")
    record("test_09_no_store_city_fallback_site",
           r["entry"] == "site" and "暂无市级网店" in r["reason"]
           and r["store"] is None
           and r["city"]["cityCode"] == "330100",
           f"entry={r['entry']}, reason={r['reason']}")

    # 10. 经纬度但附近无门店(拉萨) → 本站入口
    r = await svc.decide_order_entry(longitude=91.1, latitude=29.6)
    record("test_10_location_no_nearby_store",
           r["entry"] == "site" and "未获取到位置信息" in r["reason"],
           f"entry={r['entry']}, reason={r['reason']}")

    # 11. 无任何位置信息 → 本站入口
    r = await svc.decide_order_entry()
    record("test_11_no_input_fallback_site",
           r["entry"] == "site" and r["orderEntry"]["type"] == "site"
           and r["nearbyStores"] == [],
           f"entry={r['entry']}")

    # ---------------- 输出 ----------------
    print()
    for line in RESULTS:
        print(line)
    print()
    print("=" * 60)
    print(f"总计: {PASS + FAIL}  通过: {PASS}  失败: {FAIL}")
    print("=" * 60)
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
