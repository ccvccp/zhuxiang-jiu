#!/usr/bin/env python3
"""gen_citystore_districts.py——区县数据生成（一次性脚本，产物入库后留档）

数据源: modood/Administrative-divisions-of-China (dist/pca-code.json,
jsdelivr CDN 拉取, 省→地级(4位码)→区县(6位码) 三级树, 31省不含港澳台)

归组规则:
  - 直辖市(京津沪渝): 所有地级层(如重庆5001市辖区/5002县)并到 xx0100 单市条目
  - 常规: 地级4位码+"00" == REGION_CITIES 市码 → 区县挂该市
  - 省直辖县级市: 区县码自身 ∈ 市码(济源419001/仙桃429004/潜江429005/天门429006)
    → 挂自身单条目(码=市码重合,自检白名单)
  - 跳过留档: 不在344市口径的区县(神农架429021/海南4690系/新疆兵团6590系)
    ——与市级网店既有口径一致(这些区域市级也未开放)

本脚本执行时同步修正过 regions.py 黑龙江黑河(231200→231100)/绥化(231300→231200)码笔误。
"""
import json
import os
import sys
from collections import OrderedDict

BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BACKEND)

from services.citystore_regions import PROVINCES, REGION_CITIES

PCA_PATH = os.path.join(os.environ.get("TEMP", "/tmp"), "pca-code.json")
OUT_PATH = os.path.join(BACKEND, "services", "citystore_districts.py")

MUNICIPALITY_PREFIXES = ("11", "12", "31", "50")  # 直辖市省码前2位
HMT_CITIES = {"710100", "810100", "820100"}       # 港澳台(无县级细分,不收录)

# ---------- 1. 读数据源与344市索引 ----------
with open(PCA_PATH, encoding="utf-8") as f:
    data = json.load(f)

city_by_code = {}          # 市码 -> 市名
prov_of_city = {}          # 市码 -> 省码
for prov_code, cities in REGION_CITIES.items():
    for cc, cn in cities:
        city_by_code[cc] = cn
        prov_of_city[cc] = prov_code
city_codes = set(city_by_code)
print(f"[regions] 市码总数: {len(city_codes)}")

# ---------- 2. 归组 ----------
DISTRICTS = OrderedDict()
skipped = []

for prov in data:
    prov_code = prov["code"] + "0000"
    is_municipality = prov["code"] in MUNICIPALITY_PREFIXES
    for pref in prov["children"]:
        city_code = pref["code"] + "00"
        # 直辖市: 所有地级层并到 xx0100
        if is_municipality:
            city_code = prov["code"] + "0100"
        for dist in pref["children"]:
            dcode, dname = dist["code"], dist["name"]
            if city_code in city_codes:
                DISTRICTS.setdefault(city_code, []).append((dcode, dname))
            elif dcode in city_codes:
                # 省直辖县级市: 自身即市码
                DISTRICTS.setdefault(dcode, []).append((dcode, dname))
            else:
                skipped.append((dcode, dname, pref["name"]))

# ---------- 3. 对齐检查(口径修正后) ----------
self_direct = {c for c in city_codes if len(DISTRICTS.get(c, [])) == 1
               and DISTRICTS[c][0][0] == c}
covered = set(DISTRICTS)
uncovered = (city_codes - covered) - HMT_CITIES  # 344市中有区县数据覆盖的缺口

print(f"[districts] 挂靠市数: {len(DISTRICTS)} / 344 (港澳台3哨兵不计)")
print(f"[districts] 区县总数: {sum(len(v) for v in DISTRICTS.values())}")
print(f"[self-direct] 省直辖县级市单条目: {sorted(self_direct)}")
print(f"[skip] 未收录区县({len(skipped)}): 神农架/海南4690系/新疆兵团6590系")
for s in skipped[:5]:
    print("   ", s)
print(f"[gap] 344市中无区县数据(除港澳台, {len(uncovered)}):", sorted(uncovered))

# 省直辖县级市白名单 = 在册且 DISTRICTS 自挂
SELF_DIRECT_WHITELIST = sorted(self_direct)

# ---------- 4. 生成 citystore_districts.py ----------
all_districts = []
for city_code, dists in DISTRICTS.items():
    prov_code = prov_of_city[city_code]
    for dcode, dname in dists:
        all_districts.append({
            "districtCode": dcode, "districtName": dname,
            "cityCode": city_code, "cityName": city_by_code[city_code],
            "provinceCode": prov_code, "provinceName": PROVINCES[prov_code],
        })

lines = []
A = lines.append
A('"""县（区）网店模块——全国县级行政区数据源（市级下区县挂靠）')
A('')
A('依据: GB/T 2260 行政区划码(数据源 modood/Administrative-divisions-of-China')
A('pca-code.json, 2023+ 口径), 按"区县码前4位+00 == 市码"归组到')
A('citystore_regions.REGION_CITIES 的 344 市下。')
A('')
A('用途:')
A('    - 县(区)网店"申请开店"区县选择(GET /api/citystore/districts/available)')
A('    - apply 区县合法性校验(districtCode 必须在册)')
A('')
A('口径:')
A('    - 直辖市(京津沪渝): 所有区县并挂 xx0100 单市条目(含重庆5002县层)')
A('    - 省直辖县级市(济源/仙桃/潜江/天门): 自身单条目, 码=市码重合(白名单)')
A('    - 港澳台(710100/810100/820100): 无县级细分不收录, apply 409 哨兵')
A('    - 神农架429021/海南4690系/新疆兵团6590系: 不在344市口径, 跳过留档')
A('      (与市级网店既有口径一致——这些区域市级也未开放)')
A('    - 生成工具: backend/scripts/gen_citystore_districts.py')
A('"""')
A('')
A('from services.citystore_regions import PROVINCES, REGION_CITIES  # noqa: F401')
A('')
A('# 省直辖县级市单条目白名单(区县码 == 市码)')
A('SELF_DIRECT_CITIES = ' + repr(SELF_DIRECT_WHITELIST))
A('')
A('DISTRICTS: dict[str, list[tuple[str, str]]] = {')
for city_code, dists in DISTRICTS.items():
    A(f'    "{city_code}": [')
    # 每行最多3个区县元组, 控制行宽
    for i in range(0, len(dists), 3):
        chunk = dists[i:i + 3]
        row = ", ".join(f'("{c}", "{n}")' for c, n in chunk)
        end = "," if i + 3 < len(dists) else ""
        A(f"        {row}{end}")
    A("    ],")
A("}")
A("")
A("ALL_DISTRICTS: list[dict] = [")
for d in all_districts:
    A("    {" + ", ".join(f'"{k}": "{v}"' for k, v in d.items()) + "},")
A("]")
A("")
A("_DISTRICT_CODE_INDEX: dict[str, dict] = {")
A('    d["districtCode"]: d for d in ALL_DISTRICTS}')
A("")
A("""
def all_districts() -> list[dict]:
    \"\"\"全量区县列表(扁平化含省市县四级信息)\"\"\"
    return ALL_DISTRICTS


def districts_by_city(city_code: str) -> list[dict]:
    \"\"\"按市码取区县列表\"\"\"
    return [d for d in ALL_DISTRICTS if d["cityCode"] == city_code]


def is_valid_district(district_code: str) -> bool:
    \"\"\"区县码是否在册\"\"\"
    return district_code in _DISTRICT_CODE_INDEX


def get_district(district_code: str) -> dict | None:
    \"\"\"区县码 -> {districtCode/districtName/cityCode/cityName/省} 或 None\"\"\"
    return _DISTRICT_CODE_INDEX.get(district_code)


def _validate() -> None:
    \"\"\"导入即自检(封闭不变量)\"\"\"
    city_codes = {cc for cities in REGION_CITIES.values() for cc, _ in cities}
    hmt = {"710100", "810100", "820100"}
    municipality = {"110100", "120100", "310100", "500100"}  # 直辖市并挂
    # 1) 区县码全局唯一(省直辖县级市自身单条目白名单)
    seen = set()
    for city_code, dists in DISTRICTS.items():
        for dcode, _ in dists:
            assert dcode not in seen, f"区县码重复: {dcode}"
            seen.add(dcode)
            # 2) 前4位规则: 区县码前4位+00 == 市码(白名单除外)
            if dcode in SELF_DIRECT_CITIES:
                assert dcode == city_code, \\
                    f"省直辖县级市{dcode}未自挂{city_code}"
            elif city_code in municipality:
                # 直辖市: 所有地级层区县并挂(如重庆5002县层), 前2位一致即可
                assert dcode[:2] == city_code[:2], \\
                    f"直辖市区县{dcode}与所属市{city_code}省域不符"
            else:
                assert dcode[:4] + "00" == city_code, \\
                    f"区县{dcode}前4位与所属市{city_code}不符"
    # 3) 挂靠键必在344市码中
    for city_code in DISTRICTS:
        assert city_code in city_codes, f"挂靠市码{city_code}不在344市册"
    # 4) 总量下限
    assert len(ALL_DISTRICTS) >= 2800, \\
        f"区县总量{len(ALL_DISTRICTS)}异常(应≥2800)"
    # 5) 挂靠市数 = 344 - 港澳台3
    assert len(DISTRICTS) == len(city_codes) - len(hmt), \\
        f"挂靠市数{len(DISTRICTS)} != 341(344-港澳台3)"
    # 6) 扁平化与DISTRICTS条目一致
    assert len(ALL_DISTRICTS) == sum(len(v) for v in DISTRICTS.values())


_validate()""")
A("")

with open(OUT_PATH, "w", encoding="utf-8") as f:
    f.write("\n".join(lines))
print(f"[out] written -> {OUT_PATH} ({len(lines)} lines, "
      f"{sum(len(v) for v in DISTRICTS.values())} districts)")
