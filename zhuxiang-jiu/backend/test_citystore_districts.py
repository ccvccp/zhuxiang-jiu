"""县（区）网店模块——区县数据与校验测试

运行:
    $env:LOCK_MODE="asyncio"; $env:STORE_MODE="asyncio"; $env:AUTH_MODE="compat"
    python test_citystore_districts.py

覆盖:
    1. 数据自检: 3028 区县导入即验(唯一性/挂靠/前4位规则/直辖市并挂/总量)
    2. 省直辖县级市单条目(济源419001/仙桃429004/潜江429005/天门429006)
    3. 港澳台哨兵: 710100/810100/820100 无区县数据
    4. 辅助函数: get_district/is_valid_district/districts_by_city/all_districts
    5. 抽样核验: 北京东城区/重庆城口县(5002层)/石家庄长安区/黑河爱辉区(码笔误修复后)
    6. 未收录留档: 神农架/海南4690系/新疆兵团6590系 不在册
"""
import os
import sys

os.environ.setdefault("LOCK_MODE", "asyncio")
os.environ.setdefault("STORE_MODE", "asyncio")
os.environ.setdefault("AUTH_MODE", "compat")

from services.citystore_districts import (
    ALL_DISTRICTS, DISTRICTS, SELF_DIRECT_CITIES,
    all_districts, districts_by_city, get_district, is_valid_district,
)
from services.citystore_regions import REGION_CITIES

PASS = 0
FAIL = 0
RESULTS = []


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        RESULTS.append(f"  [PASS] {name}")
    else:
        FAIL += 1
        RESULTS.append(f"  [FAIL] {name} {detail}")


def run_tests():
    city_codes = {cc for cities in REGION_CITIES.values() for cc, _ in cities}

    # 1. 数据规模与结构
    check("区县总量≥2800", len(ALL_DISTRICTS) >= 2800,
          f"实际 {len(ALL_DISTRICTS)}")
    check("挂靠市数=341(344-港澳台3)", len(DISTRICTS) == 341,
          f"实际 {len(DISTRICTS)}")
    check("ALL_DISTRICTS 与 DISTRICTS 条目一致",
          len(ALL_DISTRICTS) == sum(len(v) for v in DISTRICTS.values()))

    # 2. 省直辖县级市单条目
    check("省直辖县级市白名单为4",
          sorted(SELF_DIRECT_CITIES) ==
          ["419001", "429004", "429005", "429006"],
          f"实际 {sorted(SELF_DIRECT_CITIES)}")
    check("济源自挂单条目",
          DISTRICTS.get("419001") == [("419001", "济源市")])
    check("仙桃自挂单条目",
          DISTRICTS.get("429004") == [("429004", "仙桃市")])

    # 3. 港澳台哨兵
    for hmt in ("710100", "810100", "820100"):
        check(f"港澳台哨兵 {hmt} 无区县", hmt not in DISTRICTS)

    # 4. 辅助函数
    d = get_district("110105")
    check("get_district 110105=朝阳区",
          d and d["districtName"] == "朝阳区"
          and d["cityCode"] == "110100" and d["cityName"] == "北京市"
          and d["provinceName"] == "北京市")
    check("is_valid_district 110105", is_valid_district("110105"))
    check("is_valid_district 无效码", not is_valid_district("999999"))
    check("get_district None", get_district("999999") is None)
    check("districts_by_city 130100 首项长安区",
          districts_by_city("130100")[0]["districtName"] == "长安区")
    check("all_districts 长度一致", len(all_districts()) == len(ALL_DISTRICTS))

    # 5. 抽样核验
    check("北京东城区在册", ("110101", "东城区") in DISTRICTS["110100"])
    check("重庆5002县层并挂(城口县)",
          ("500229", "城口县") in DISTRICTS["500100"])
    check("黑河爱辉区在册(码笔误修复后)",
          ("231102", "爱辉区") in DISTRICTS.get("231100", []),
          f"231100 区县: {DISTRICTS.get('231100', [])[:2]}")
    check("绥化市码231200在册", "231200" in city_codes)
    check("上海浦东新区在册", ("310115", "浦东新区") in DISTRICTS["310100"])

    # 6. 未收录留档(口径一致)
    for code, name in (("429021", "神农架林区"), ("469002", "琼海市"),
                       ("659001", "石河子市")):
        check(f"未收录留档 {name}{code}", not is_valid_district(code))

    # 7. 挂靠键全部在344市册
    bad_keys = [k for k in DISTRICTS if k not in city_codes]
    check("挂靠键全在344市册", not bad_keys, f"越界键 {bad_keys}")


def main():
    run_tests()
    print("=" * 60)
    print("县(区)数据测试".center(54))
    print("=" * 60)
    for r in RESULTS:
        print(r)
    print("-" * 60)
    print(f"TOTAL: {PASS} pass, {FAIL} fail / {PASS + FAIL}")
    print("=" * 60)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
