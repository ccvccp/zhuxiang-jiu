"""下单入口决策极端边界测试(Service 层)

运行: python -B test_citystore_entry_edge.py
重点: 经纬度完全缺失/半缺/零值、空串输入、垃圾adcode、无地址会员、whitespace
"""

import asyncio
import os
import sys

os.environ["LOCK_MODE"] = "asyncio"
os.environ["STORE_MODE"] = "asyncio"

from services.citystore_service import CityStoreService
from repositories.citystore_repository import CityStoreRepository
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


async def seed():
    _reset_store_impl()
    city_repo = CityStoreRepository()
    await city_repo.save_store({
        "storeCode": "CS-110100-01", "storeName": "北京竹奕市店",
        "memberId": 1001, "cityCode": "110100", "cityName": "北京市",
        "provinceCode": "110000", "provinceName": "北京市",
        "status": 1, "currentDiscount": 80, "createdAt": "2026-01-01T00:00:00",
    })
    loc_repo = LocationRepository()
    await loc_repo.create_store({
        "storeName": "北京朝阳店", "province": "北京市", "city": "北京市",
        "district": "朝阳区", "address": "东长安街1号",
        "longitude": 116.397, "latitude": 39.909, "status": "open",
    })
    # 会员 2001: 有默认地址(adcode 110108)
    await loc_repo.create_address({
        "userId": 2001, "receiverName": "有地址用户", "receiverPhone": "13800000001",
        "province": "北京市", "city": "北京市", "district": "海淀区",
        "detailAddress": "中关村大街1号", "adcode": "110108", "isDefault": True,
    })
    # 会员 2002: 地址无 adcode 无 city(极端脏数据)
    await loc_repo.create_address({
        "userId": 2002, "receiverName": "脏数据用户", "receiverPhone": "13800000002",
        "province": "", "city": "", "district": "", "detailAddress": "x",
        "isDefault": True,
    })
    # 会员 2003: 无任何地址


async def main():
    print("=" * 60)
    print("下单入口决策 - 极端边界测试")
    print("=" * 60)
    await seed()
    svc = CityStoreService()

    # ---- 经纬度极端情况 ----

    # 1. 经纬度完全缺失且无会员 → 本站入口
    r = await svc.decide_order_entry()
    record("01_完全无输入_site入口",
           r["entry"] == "site" and "未获取到位置" in r["reason"]
           and r["city"] is None and r["store"] is None,
           f"entry={r['entry']}, reason={r['reason']}")

    # 2. 半缺: 只有经度无纬度 → 不走定位路径, 安全回退
    r = await svc.decide_order_entry(longitude=116.397)
    record("02_只有经度_安全回退",
           r["entry"] == "site" and "未获取到位置" in r["reason"],
           f"entry={r['entry']}, reason={r['reason']}")

    # 3. 半缺: 只有纬度无经度 → 同上
    r = await svc.decide_order_entry(latitude=39.909)
    record("03_只有纬度_安全回退",
           r["entry"] == "site" and "未获取到位置" in r["reason"],
           f"entry={r['entry']}")

    # 4. 零值经纬度(0,0 几内亚湾, 前端常见"未定位"占位) → 走定位但无附近门店 → 回退
    r = await svc.decide_order_entry(longitude=0, latitude=0)
    record("04_零值经纬度_无附近门店回退",
           r["entry"] == "site" and "未获取到位置" in r["reason"]
           and r["nearbyStores"] == [],
           f"entry={r['entry']}, nearby={len(r['nearbyStores'])}")

    # 5. 半缺+会员兜底: 只有经度但有默认地址 → 地址兜底成功
    r = await svc.decide_order_entry(longitude=116.0, member_id=2001)
    record("05_半缺经纬度_默认地址兜底",
           r["entry"] == "citystore" and r["citySource"] == "defaultAddress",
           f"entry={r['entry']}, source={r.get('citySource')}")

    # 6. 完全无定位+会员兜底: 仅传 member_id → 地址兜底
    r = await svc.decide_order_entry(member_id=2001)
    record("06_仅member_id_地址兜底",
           r["entry"] == "citystore", f"entry={r['entry']}")

    # ---- 空串/垃圾输入 ----

    # 7. cityCode 空串 → 视为未提供, 不崩溃
    r = await svc.decide_order_entry(city_code="")
    record("07_cityCode空串_安全回退",
           r["entry"] == "site" and "未获取到位置" in r["reason"],
           f"entry={r['entry']}")

    # 8. adcode 空串 → 同上
    r = await svc.decide_order_entry(adcode="")
    record("08_adcode空串_安全回退",
           r["entry"] == "site", f"entry={r['entry']}")

    # 9. 垃圾 adcode("abcd" 非数字) → 不崩溃, 本站入口
    r = await svc.decide_order_entry(adcode="abcd")
    record("09_垃圾adcode_不崩溃",
           r["entry"] == "site" and isinstance(r["reason"], str),
           f"entry={r['entry']}, reason={r['reason']}")

    # 10. 过短 adcode("11") → 原样返回无匹配 → 本站入口
    r = await svc.decide_order_entry(adcode="11")
    record("10_过短adcode_本站入口",
           r["entry"] == "site", f"entry={r['entry']}")

    # 11. cityName 空串 → 未提供
    r = await svc.decide_order_entry(city_name="")
    record("11_cityName空串_安全回退",
           r["entry"] == "site" and "未获取到位置" in r["reason"],
           f"entry={r['entry']}")

    # 12. cityCode 带前后空格(" 110100 ") → strip后正常匹配市店
    r = await svc.decide_order_entry(city_code=" 110100 ")
    record("12_cityCode带空格_strip后匹配",
           r["entry"] == "citystore"
           and r["store"]["storeCode"] == "CS-110100-01",
           f"entry={r['entry']}, reason={r['reason']}")

    # ---- 会员极端情况 ----

    # 13. 会员无任何地址(2003) → 本站入口
    r = await svc.decide_order_entry(member_id=2003)
    record("13_会员无地址_本站入口",
           r["entry"] == "site" and "未获取到位置" in r["reason"],
           f"entry={r['entry']}")

    # 14. 会员地址脏数据(2002 无adcode无city) → 本站入口
    r = await svc.decide_order_entry(member_id=2002)
    record("14_脏地址_本站入口",
           r["entry"] == "site" and "未获取到位置" in r["reason"],
           f"entry={r['entry']}")

    # 15. member_id=0(伪造头) → 无地址 → 本站入口, 不崩溃
    r = await svc.decide_order_entry(member_id=0)
    record("15_member_id零_不崩溃",
           r["entry"] == "site", f"entry={r['entry']}")

    # 16. 负 member_id → 不崩溃
    r = await svc.decide_order_entry(member_id=-1)
    record("16_负member_id_不崩溃",
           r["entry"] == "site", f"entry={r['entry']}")

    # 17. 不存在城市的 cityCode → 本站入口且城市信息保留
    r = await svc.decide_order_entry(city_code="999999")
    record("17_不存在城市_本站入口",
           r["entry"] == "site" and r["city"] is not None
           and r["city"]["cityCode"] == "999999",
           f"entry={r['entry']}, city={r['city']}")

    # 18. 半径极端小(0.01km) 附近无门店 → 回退
    r = await svc.decide_order_entry(longitude=116.40, latitude=39.90,
                                     nearby_radius_km=0.01)
    record("18_极小半径_无门店回退",
           r["entry"] == "site" and "未获取到位置" in r["reason"],
           f"entry={r['entry']}")

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
