"""C2C 老酒交易市场 Phase3 行情撮合专项测试(设计稿 §六 Phase3)

运行:
    python test_recycle_c2c_market.py

覆盖(recycle 33→36 端点: market-stats / price-suggestion / recommendations):
    1. 行情看板: 在售量/酒龄档分布/近30天成交(笔数/均价/极值/溢价率中位)
    2. 定价建议: 三档(快速/平衡/惜售)+锚定区间 clamp+同档行情溢价融合+
       无行情回退+资格校验(未激活/不存在/品质非法/无原价)
    3. 撮合推荐: 性价比降序(锚定/要价)+预算过滤+酒龄下限过滤+空结果
    4. 参数校验: days 非法 422

行情口径: 溢价率 = 成交价 / 锚定估值; 酒龄档 3-5 / 6-10 / 11+
构造: 3-5 档三笔成交(溢价 0.833/1.0/1.167, 中位 1.0) +
      6-10 档一笔(溢价 1.111) + 在售三单(性价比 1.25/1.059/0.923)
"""
import asyncio
import os
import sys
from datetime import date, timedelta

os.environ.setdefault("LOCK_MODE", "asyncio")
os.environ.setdefault("STORE_MODE", "asyncio")
os.environ.setdefault("AUTH_MODE", "compat")
os.environ.setdefault("AUTH_COMPAT_TRUST_HEADERS", "1")

PASS = 0
FAIL = 0
RESULTS = []

SELLER = {"X-Member-Id": "99101"}
BUYER = {"X-Member-Id": "99102"}
PRODUCT = "ZX42-2026C03"


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        RESULTS.append(f"  [PASS] {name}")
    else:
        FAIL += 1
        RESULTS.append(f"  [FAIL] {name} {detail}")


def _err(r) -> str:
    j = r.json() or {}
    return j.get("error") or j.get("detail") or ""


def _old_date(years_ago: int) -> str:
    return (date.today() - timedelta(days=365 * years_ago + 10)).isoformat()


async def _make_life(seller_id: int, batch: str, price: float,
                     years_ago: int, with_price: bool = True) -> str:
    from services.trace_service import TraceService
    trace = TraceService()
    res = await trace.generate_life_codes(PRODUCT, batch, 1)
    item = res["lifeCodes"][0]
    await trace.activate_life_code(
        item["lifeCode"], seller_id,
        purchase_price=(price if with_price else 0))
    await trace.repo.update_life_code(item["id"], {
        "firstActivationDate": _old_date(years_ago)})
    return item["lifeCode"]


def _post(client, url, headers=None, json_body=None):
    return client.post(url, headers=headers, json=json_body or {})


def _deal(client, seller, buyer, life_code, ask_price):
    """offline 全链成交(挂单→买→确认)"""
    r = _post(client, "/api/recycle/c2c/listings", seller,
              {"lifeCode": life_code, "askPrice": ask_price})
    no = (r.json().get("data") or {}).get("listingNo")
    _post(client, f"/api/recycle/c2c/listings/{no}/buy", buyer)
    r = _post(client, f"/api/recycle/c2c/listings/{no}/confirm", seller)
    return r.json().get("data") or {}


def main():
    from fastapi.testclient import TestClient
    from main import app
    client = TestClient(app)

    async def prep():
        return {
            # 成交构造码(3-5 档: 原价1000/酒龄4 → 锚定1200)
            "d1": await _make_life(99101, "B-MKT-01", 1000.0, 4),
            "d2": await _make_life(99101, "B-MKT-02", 1000.0, 4),
            "d3": await _make_life(99101, "B-MKT-03", 1000.0, 4),
            # 成交构造码(6-10 档: 原价1200/酒龄10 → 锚定1800)
            "d4": await _make_life(99101, "B-MKT-04", 1200.0, 10),
            # 在售单码
            "s1": await _make_life(99101, "B-MKT-05", 1000.0, 4),
            "s2": await _make_life(99101, "B-MKT-06", 1000.0, 4),
            "s3": await _make_life(99101, "B-MKT-07", 1200.0, 10),
            # 定价建议查询码
            "q1": await _make_life(99101, "B-MKT-08", 1000.0, 4),
            "q2": await _make_life(99101, "B-MKT-09", 1200.0, 10),
            "q3": await _make_life(99101, "B-MKT-10", 1000.0, 15),
            # 资格校验码
            "q_young": await _make_life(99101, "B-MKT-11", 1000.0, 1),
            "q_noprice": await _make_life(99101, "B-MKT-12", 0, 4,
                                          with_price=False),
            "q_unactivated": await _make_life(99101, "B-MKT-13", 1000.0, 4,
                                              with_price=False),
        }
    codes = asyncio.run(prep())

    # 四笔成交(offline 链路)
    _deal(client, SELLER, BUYER, codes["d1"], 1000.0)   # 0.833
    _deal(client, SELLER, BUYER, codes["d2"], 1200.0)   # 1.000
    _deal(client, SELLER, BUYER, codes["d3"], 1400.0)   # 1.167
    _deal(client, SELLER, BUYER, codes["d4"], 2000.0)   # 1.111

    # 三张在售单
    r = _post(client, "/api/recycle/c2c/listings", SELLER,
              {"lifeCode": codes["s1"], "askPrice": 960})
    s1_no = (r.json().get("data") or {}).get("listingNo")
    r = _post(client, "/api/recycle/c2c/listings", SELLER,
              {"lifeCode": codes["s2"], "askPrice": 1300})
    s2_no = (r.json().get("data") or {}).get("listingNo")
    r = _post(client, "/api/recycle/c2c/listings", SELLER,
              {"lifeCode": codes["s3"], "askPrice": 1700})
    s3_no = (r.json().get("data") or {}).get("listingNo")

    # --------------------------------------------------------
    print("[01 行情看板 market-stats]")
    r = client.get("/api/recycle/c2c/market-stats")
    d = (r.json().get("data") or {})
    deals = d.get("deals") or {}
    check("test_mkt_01 看板200(在售3+成交4)",
          r.status_code == 200 and d.get("onSaleCount") == 3
          and deals.get("count") == 4,
          str((r.status_code, d.get("onSaleCount"), deals.get("count"))))
    check("test_mkt_02 成交均价1400/极值1000~2000",
          abs(deals.get("avgPrice", 0) - 1400.0) < 0.01
          and deals.get("minPrice") == 1000.0
          and deals.get("maxPrice") == 2000.0,
          str(deals))
    check("test_mkt_03 溢价率中位≈1.056(四笔全档)",
          deals.get("medianPremium") is not None
          and abs(deals["medianPremium"] - 1.056) < 0.01,
          str(deals.get("medianPremium")))
    check("test_mkt_04 在售酒龄档分布(3-5:2 / 6-10:1)",
          (d.get("onSaleAsk") or {}).get("byAgeBand")
          == {"3-5": 2, "6-10": 1},
          str((d.get("onSaleAsk") or {}).get("byAgeBand")))

    r = client.get("/api/recycle/c2c/market-stats",
                   params={"days": 0})
    check("test_mkt_05 days非法422", r.status_code == 422,
          str(r.status_code))

    # --------------------------------------------------------
    print("[02 定价建议 price-suggestion]")
    r = client.get("/api/recycle/c2c/price-suggestion",
                   params={"lifeCode": "ZX-B-NOT-EXIST-999"})
    check("test_mkt_06 生命码不存在404", r.status_code == 404,
          str(r.status_code))

    r = client.get("/api/recycle/c2c/price-suggestion",
                   params={"lifeCode": codes["q_unactivated"]})
    check("test_mkt_07 未激活409", r.status_code == 409,
          str((r.status_code, _err(r))))

    r = client.get("/api/recycle/c2c/price-suggestion",
                   params={"lifeCode": codes["q1"],
                           "conditionGrade": "S"})
    check("test_mkt_08 品质分级非法409", r.status_code == 409,
          str(r.status_code))

    r = client.get("/api/recycle/c2c/price-suggestion",
                   params={"lifeCode": codes["q_noprice"]})
    check("test_mkt_09 无购买原价409(无法锚定)", r.status_code == 409
          and "原价" in _err(r), str((r.status_code, _err(r))))

    # 3-5 档: 锚定1200, 档内3笔溢价中位=1.0
    r = client.get("/api/recycle/c2c/price-suggestion",
                   params={"lifeCode": codes["q1"]})
    d = (r.json().get("data") or {})
    tiers = {t.get("tier"): t for t in d.get("suggestions", [])}
    check("test_mkt_10 建议200(锚定1200/区间600~1800/档3-5)",
          r.status_code == 200 and d.get("anchorValue") == 1200.0
          and d.get("ageBand") == "3-5"
          and d.get("legalBand") == {"min": 600.0, "max": 1800.0},
          str((r.status_code, d.get("anchorValue"), d.get("legalBand"))))
    check("test_mkt_11 三档=行情融合(1200/1020/1500)",
          abs(tiers.get("balanced", {}).get("price", 0) - 1200.0) < 0.01
          and abs(tiers.get("quick", {}).get("price", 0) - 1020.0) < 0.01
          and abs(tiers.get("premium", {}).get("price", 0) - 1500.0) < 0.01,
          str({k: v.get("price") for k, v in tiers.items()}))
    ref = d.get("marketReference") or {}
    check("test_mkt_12 同档行情参考(3笔/均价1200/中位溢价1.0)",
          ref.get("dealCount90d") == 3
          and abs(ref.get("avgPrice90d", 0) - 1200.0) < 0.01
          and abs(ref.get("medianPremium90d", 0) - 1.0) < 0.01,
          str(ref))

    # 6-10 档: 锚定1800, 档内1笔溢价1.111 → balanced=2000
    r = client.get("/api/recycle/c2c/price-suggestion",
                   params={"lifeCode": codes["q2"]})
    d = (r.json().get("data") or {})
    tiers = {t.get("tier"): t for t in d.get("suggestions", [])}
    check("test_mkt_13 6-10档行情融合(平衡2000/快速1700/惜售2500)",
          r.status_code == 200
          and abs(tiers.get("balanced", {}).get("price", 0) - 2000.0) < 0.01
          and abs(tiers.get("quick", {}).get("price", 0) - 1700.0) < 0.01
          and abs(tiers.get("premium", {}).get("price", 0) - 2500.0) < 0.01,
          str({k: v.get("price") for k, v in tiers.items()}))

    # 11+ 档: 无行情回退锚定(原价1000/酒龄15 → 锚定1750)
    r = client.get("/api/recycle/c2c/price-suggestion",
                   params={"lifeCode": codes["q3"]})
    d = (r.json().get("data") or {})
    tiers = {t.get("tier"): t for t in d.get("suggestions", [])}
    check("test_mkt_14 无行情档回退锚定(11+档锚定1750)",
          r.status_code == 200 and d.get("ageBand") == "11+"
          and d.get("anchorValue") == 1750.0
          and abs(tiers.get("balanced", {}).get("price", 0) - 1750.0) < 0.01
          and (d.get("marketReference") or {}).get("dealCount90d") == 0,
          str((d.get("anchorValue"), d.get("ageBand"),
               (d.get("marketReference") or {}).get("dealCount90d"))))

    # --------------------------------------------------------
    print("[03 撮合推荐 recommendations]")
    r = client.get("/api/recycle/c2c/recommendations")
    items = r.json().get("data") or []
    check("test_mkt_15 推荐默认10条内(共3单)",
          r.status_code == 200 and len(items) == 3,
          str((r.status_code, len(items))))
    check("test_mkt_16 性价比降序(960单>1700单>1300单)",
          [i.get("listingNo") for i in items]
          == [s1_no, s3_no, s2_no],
          str([(i.get("listingNo"), i.get("valueScore")) for i in items]))
    check("test_mkt_17 折价率留痕(-0.2/+0.083)",
          abs(items[0].get("discountRate", 0) - (-0.2)) < 0.01
          and abs(items[2].get("discountRate", 0) - 0.083) < 0.01,
          str([i.get("discountRate") for i in items]))

    r = client.get("/api/recycle/c2c/recommendations",
                   params={"budget": 1200})
    items = r.json().get("data") or []
    check("test_mkt_18 预算过滤(≤1200 仅960单)",
          len(items) == 1 and items[0].get("listingNo") == s1_no,
          str([(i.get("listingNo"), i.get("askPrice"))
               for i in items]))

    r = client.get("/api/recycle/c2c/recommendations",
                   params={"wineAgeMin": 8})
    items = r.json().get("data") or []
    check("test_mkt_19 酒龄下限过滤(≥8年 仅1700单)",
          len(items) == 1 and items[0].get("listingNo") == s3_no
          and items[0].get("wineAge") == 10,
          str([(i.get("listingNo"), i.get("wineAge")) for i in items]))

    r = client.get("/api/recycle/c2c/recommendations",
                   params={"budget": 100})
    check("test_mkt_20 超低预算空结果",
          r.status_code == 200 and (r.json().get("data") or []) == [],
          str(r.json().get("data")))

    # --------------------------------------------------------
    print()
    print("=" * 60)
    for line in RESULTS:
        print(line)
    print("=" * 60)
    print(f"总计: {PASS} 通过, {FAIL} 失败")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
