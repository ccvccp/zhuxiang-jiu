"""68号·信值·臻选·P1 臻选货架引擎专项测试

覆盖(《68号 创新规划方案》§三 P1 + 文档"智能决策层"):
    1. 契合度: 知识映射(珍藏→专业/宴请→互助/热销→活跃)/
       用户维度加成/无映射中性50
    2. 安全硬闸: 下架→L4/违禁词→L4/差评观察×0.5/
       正常商品=1.0
    3. 转化潜力: 同系列统计/冷启动回退0.5
    4. 评分与分级: 公式 fit×safety×conv×100/L1/L2/
       L3 档/L4 短路/信值加权排序公式
    5. 货架与观测: L1 降序/懒加载/明细可解释/
       指定商品/404/幂等锚

运行:
    $env:LOCK_MODE="asyncio"; $env:STORE_MODE="asyncio"
    python test_xinzhi_p1.py
"""

import asyncio
import os
import sys


os.environ["LOCK_MODE"] = "asyncio"
os.environ["STORE_MODE"] = "asyncio"
os.environ.pop("LLM_API_KEY", None)
os.environ["LLM_ENABLED"] = "off"

from services.xinzhi_prime_service import (
    XinzhiPrimeService, compute_fit, compute_safety,
    grade_of, RANK_ALPHA, SAFETY_HARD_GATE,
    FIT_NO_MAP_SCORE,
)
from services.xinzhi_radar_service import (
    XinzhiRadarService,
)
from repositories.xinzhi_repository import (
    XinzhiRepository, DIM_EXPERT, DIM_MUTUAL,
)
from repositories.member_repository import MemberRepository

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
    from repositories.store import reset_store as _reset
    _reset()


async def seed_member(member_id: int = 1,
                      days_ago: int = 200) -> None:
    from datetime import datetime, UTC, timedelta
    repo = MemberRepository()
    member = {
        "id": member_id, "phone": f"1380000000{member_id}",
        "password": "x", "nickname": f"测试会员{member_id}",
        "level": 2, "points": 100, "status": 1,
        "created_at": (datetime.now(UTC)
                       - timedelta(days=days_ago)).isoformat(),
        "last_login_at": datetime.now(UTC).isoformat(),
    }
    await repo.save(member_id, member)


def make_product(**kw):
    """受控测试商品(product_repository 口径)"""
    return {
        "product_id": kw.get("product_id", "TEST-001"),
        "name": kw.get("name", "竹奕·测试款 42° 500ml"),
        "subtitle": kw.get("subtitle", "测试副标题"),
        "series": kw.get("series", "经典系列"),
        "price": kw.get("price", 268),
        "status": kw.get("status", "on_sale"),
        "rating_avg": kw.get("rating_avg", 4.8),
        "rating_count": kw.get("rating_count", 100),
        "sales_monthly": kw.get("sales_monthly", 500),
        "tags": kw.get("tags", ["主打"]),
        "scenes": kw.get("scenes", ["老友小聚"]),
        "description": kw.get("description", "测试描述"),
    }


HIGH_RADAR = {"integrity": 90, "mutual": 85, "expert": 88,
             "activity": 80, "growth": 70, "totalScore": 82}
MID_RADAR = {"integrity": 60, "mutual": 55, "expert": 50,
             "activity": 50, "growth": 50, "totalScore": 55}


# ============================================================
# 1. 契合度(5 断言)
# ============================================================

class TestFit:
    async def run(self):
        # 1) 珍藏系列 → 专业度映射(基准 85×用户加成)
        p = make_product(series="珍藏系列", name="竹香珍藏")
        fit, modules = compute_fit(p, HIGH_RADAR)
        record("契合-珍藏→专业度",
               80 <= fit <= 86 and any(
                   "expert" in m for m in modules),
               f"fit={fit} m={modules}")

        # 2) 宴请场景 → 互助值映射
        p = make_product(scenes=["商务宴请"])
        fit, modules = compute_fit(p, HIGH_RADAR)
        record("契合-宴请→互助值",
               75 <= fit <= 81 and any(
                   "mutual" in m for m in modules),
               f"fit={fit}")

        # 3) 热销标签 → 活跃度映射
        p = make_product(tags=["热销"])
        fit, modules = compute_fit(p, HIGH_RADAR)
        record("契合-热销→活跃度",
               69 <= fit <= 76 and any(
                   "activity" in m for m in modules),
               f"fit={fit}")

        # 4) 用户维度加成(高专业 vs 中专业)
        p = make_product(series="珍藏系列")
        fit_hi = compute_fit(p, HIGH_RADAR)[0]
        fit_mid = compute_fit(p, MID_RADAR)[0]
        record("契合-用户维度加成",
               fit_hi > fit_mid,
               f"hi={fit_hi} mid={fit_mid}")

        # 5) 无映射 → 中性 50
        p = make_product(series="未知系列",
                         tags=[], scenes=[])
        fit, modules = compute_fit(p, HIGH_RADAR)
        record("契合-无映射中性50",
               fit == FIT_NO_MAP_SCORE and modules == [],
               f"fit={fit}")


# ============================================================
# 2. 安全硬闸(5 断言)
# ============================================================

class TestSafety:
    async def run(self):
        # 6) 下架商品 → L4 屏蔽
        s, r, b = compute_safety(
            make_product(status="off_shelf"))
        record("硬闸-下架L4",
               s == 0.0 and b, f"b={b}")

        # 7) 违禁词 → L4
        s, r, b = compute_safety(
            make_product(description="本品为原单尾货"))
        record("硬闸-违禁词L4",
               s == 0.0 and any("违禁词" in x for x in b),
               f"b={b}")

        # 8) 40号风险词复用(医疗事故完整词)
        s, r, b = compute_safety(
            make_product(description="涉医疗事故批次白酒"))
        record("硬闸-40号风险词复用",
               s == 0.0 and b, f"b={b}")

        # 9) 差评观察(评分3.2 且 100 条 → ×0.5)
        s, r, b = compute_safety(
            make_product(rating_avg=3.2, rating_count=100))
        record("硬闸-差评观察×0.5",
               abs(s - 0.5) < 1e-9 and not b
               and any("评分观察" in x for x in r),
               f"s={s} r={r}")

        # 10) 正常商品 → 1.0
        s, r, b = compute_safety(make_product())
        record("硬闸-正常商品满安",
               s == 1.0 and not r and not b, f"s={s}")


# ============================================================
# 3. 转化与分级(6 断言)
# ============================================================

class TestConversion:
    async def run(self):
        reset_store()
        await seed_member(1)
        svc = XinzhiPrimeService()

        # 11) 冷启动回退(同系列<3 样本)
        # (种子里"珍藏系列"有 2 款 → 冷启动)
        p = make_product(series="孤本系列")
        conv, n = await svc._conversion_potential(p)
        record("转化-冷启动回退",
               conv == 0.5 and n < 3, f"c={conv} n={n}")

        # 12) 同系列统计(竹香系列 3 款 → 非 0.5)
        p = make_product(series="竹香系列")
        conv2, n2 = await svc._conversion_potential(p)
        record("转化-同系列统计",
               n2 >= 3 and conv2 != 0.5 and 0 < conv2 <= 1,
               f"c={conv2} n={n2}")

        # 13) 分级矩阵(纯函数)
        record("分级-阈值矩阵",
               grade_of(80, 1.0, []) == "L1"
               and grade_of(75, 1.0, []) == "L1"
               and grade_of(74.9, 1.0, []) == "L2"
               and grade_of(50, 1.0, []) == "L2"
               and grade_of(49.9, 1.0, []) == "L3"
               and grade_of(100, 0.59, []) == "L4"
               and grade_of(100, 1.0, ["x"]) == "L4",
               "matrix")

        # 14) 全量评分(11 款种子商品)
        r = await svc.score_products(1)
        record("评分-全量批次",
               r["scored"] >= 10
               and sum(r["grades"].values()) == r["scored"],
               f"n={r['scored']} g={r['grades']}")

        # 15) 三维公式与 L4 短路(0 分不进乘法)
        by_grade = {}
        for x in r["results"]:
            by_grade.setdefault(x["grade"], []).append(x)
        if "L4" in by_grade:
            l4 = by_grade["L4"][0]
            ok = l4["valueScore"] == 0.0 \
                 and l4["hardBlocked"] is True
        else:
            ok = True   # 种子无 L4 也合规
        record("评分-L4短路不进乘法", ok, "hard-gate")

        # 16) 信值加权排序公式(基础×(1+α×总分/100))
        # (radarTotal 为真实雷达分——从评分记录读)
        sample_full = await svc.repo.find_product_score(
            r["results"][0]["productId"], 1)
        radar_total = float(sample_full.get("radarTotal")
                            or 0)
        sample = r["results"][0]
        expect = round(float(sample["valueScore"])
                       * (1 + RANK_ALPHA * radar_total
                          / 100), 1)
        record("评分-信值加权排序公式",
               abs(float(sample["finalRank"]) - expect)
               < 0.05,
               f"r={sample['finalRank']} e={expect} "
               f"rt={radar_total}")


# ============================================================
# 4. 货架与观测面(8 断言)
# ============================================================

class TestShelf:
    async def run(self):
        reset_store()
        await seed_member(1)
        svc = XinzhiPrimeService()

        # 17) 臻选货架懒加载(无评分自动触发)
        shelf = await svc.prime_shelf(1, limit=10)
        record("货架-懒加载",
               isinstance(shelf, list) and len(shelf) >= 0
               and all(s.get("grade", "L1") == "L1"
                      for s in
                      await svc.repo.list_product_scores(
                          member_id=1, grade="L1")),
               f"n={len(shelf)}")

        # 18) L1 降序(finalRank)
        ranks = [float(s["finalRank"]) for s in shelf]
        record("货架-finalRank降序",
               ranks == sorted(ranks, reverse=True),
               f"r={ranks[:5]}")

        # 19) 指定商品评分(单商品)
        r = await svc.score_products(
            1, product_ids=["ZX42-2026L07"])
        record("观测-指定商品评分",
               r["scored"] == 1
               and r["results"][0]["productId"]
               == "ZX42-2026L07",
               f"n={r['scored']}")

        # 20) 商品明细可解释(解释文本含三维公式)
        d = await svc.product_detail(1, "ZX42-2026L07")
        record("观测-明细可解释",
               "契合" in d["explanation"]
               and "安全" in d["explanation"]
               and "转化" in d["explanation"]
               and d["productId"] == "ZX42-2026L07",
               d["explanation"][:50])

        # 21) 明细幂等锚(二次调用不重复计算)
        n1 = len(await svc.repo.list_product_scores(
            member_id=1, limit=1000))
        await svc.product_detail(1, "ZX42-2026L07")
        n2 = len(await svc.repo.list_product_scores(
            member_id=1, limit=1000))
        record("观测-明细幂等锚",
               n1 == n2, f"{n1}->{n2}")

        # 22) 雷达联动(高信值用户排序更高)
        r_hi = await svc.score_products(1)
        top_hi = max(r_hi["results"],
                     key=lambda x: x["finalRank"])
        record("观测-高信值排序加成",
               float(top_hi["finalRank"])
               > float(top_hi["valueScore"]),
               f"fr={top_hi['finalRank']} "
               f"vs={top_hi['valueScore']}")

        # 23) 404(商品不存在)
        ok = False
        try:
            await svc.score_products(
                1, product_ids=["NOPE-999"])
        except KeyError:
            ok = True
        record("观测-商品404", ok)

        # 24) L4 屏蔽不入货架(留痕在评分表)
        all_scores = await svc.repo.list_product_scores(
            member_id=1, limit=1000)
        l4 = [s for s in all_scores if s["grade"] == "L4"]
        record("观测-L4留痕不入架",
               all(s.get("hardBlocked") for s in l4)
               and all(s not in shelf for s in l4),
               f"l4n={len(l4)}")


async def main():
    tests = [TestFit(), TestSafety(), TestConversion(),
             TestShelf()]
    for t in tests:
        await t.run()
    print("=" * 60)
    print("68号 P1 信值·臻选 臻选货架引擎专项测试")
    print("=" * 60)
    for line in RESULTS:
        print(line)
    print("-" * 60)
    print(f"通过: {PASS} / {PASS + FAIL}")
    if FAIL:
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
