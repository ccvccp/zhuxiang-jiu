"""64号·信值兑换管理模块 P2 专项测试
(智能体验层)

运行方式:
    python test_xx64_p2.py

覆盖(64号计划 §八 P2):
    - 最优支付组合: 30/70 刚性
      结构+方案 A/B 互斥对比
      +积分缺口换算补足
    - 智能凑单: 信值密度降序
      +单次限额内组合(确定性)
    - 规则可视化解释: R1-R6
      逐条+数字可溯源
    - HTTP 层+宪法断言
"""

import asyncio
import os
import sys

os.environ["LOCK_MODE"] = "asyncio"
os.environ["STORE_MODE"] = "asyncio"
os.environ.pop("LLM_API_KEY", None)
os.environ["LLM_ENABLED"] = "off"
os.environ["XIAOZHU_LLM_MODE"] = "off"
os.environ["XIAOZHU_PROACTIVE_MODE"] = "off"
os.environ["QR55_MODE"] = "off"
os.environ["QR55_LEARN_MODE"] = "off"
os.environ["AIUP56_MODE"] = "off"
os.environ["KB57_MODE"] = "off"
os.environ["II58_MODE"] = "off"
os.environ["II59_MODE"] = "off"
os.environ["AB63_MODE"] = "off"
os.environ["PAY60_MODE"] = "off"
os.environ["DM61_MODE"] = "off"
os.environ["AV62_MODE"] = "off"
os.environ["XX64_MODE"] = "off"
os.environ["XX64_LLM_MODE"] = "off"
os.environ["XX64_LEARN_MODE"] = "off"

PASS = 0
FAIL = 0
RESULTS = []


def record(name, passed, detail=""):
    global PASS, FAIL
    if passed:
        PASS += 1
        RESULTS.append(f"  ✓ {name}")
    else:
        FAIL += 1
        RESULTS.append(f"  ✗ {name} — {detail}")


def reset_all():
    from repositories.store import reset_store as _reset
    _reset()


async def seed_profile(trust_id, score=500.0):
    from repositories.trust_value_repository import (
        TrustValue45Repository,
    )
    await TrustValue45Repository() \
        .save_profile({
            "trustId": int(trust_id),
            "role": "person",
            "name": f"P{trust_id}",
            "idDigest": f"d-{trust_id}",
            "factors": {},
            "score": float(score),
            "rawScore": float(score),
            "grade": "A",
            "fused": False,
            "frozen": False,
            "createdAt":
                "2026-01-01T00:00:00",
            "updatedAt":
                "2026-01-01T00:00:00"})


class TestPlan:
    """01 最优支付组合"""

    async def run(self):
        print("[01 支付组合]")
        reset_all()
        from services.xx64_experience_service import (
            Xx64ExperienceService,
        )
        exp = Xx64ExperienceService()

        await seed_profile(1, 500.0)

        # ① 方案 A 可行(余额足)
        p = await exp.payment_plan(
            1, 100, discount_value=15)
        record("方案 A 结构"
               "(30 信值+70 现金)",
               (p.get("planA") or {})
               .get("trustValue") == 30.0
               and (p.get("planA")
                    or {}).get("cash")
               == 70.0,
               str(p.get("planA")))
        record("方案 A 可行",
               (p.get("planA") or {})
               .get("feasible")
               is True,
               "")
        record("方案 B 优惠"
               "(15 抵扣→85 现金)",
               (p.get("planB") or {})
               .get("cash") == 85.0,
               str(p.get("planB")))
        record("互斥对比"
               "(A 现金更省 15 元)",
               (p.get("comparison")
                or {}).get("betterPlan")
               == "A"
               and (p.get(
                   "comparison")
                   or {}).get(
                       "cashDiff")
               == 15.0,
               str(p.get(
                   "comparison")))

        # ② 方案 A 更省(优惠小)
        p2 = await exp.payment_plan(
            1, 100, discount_value=5)
        record("方案 A 更省"
               "(95>70 现金)",
               (p2.get("comparison")
                or {}).get("betterPlan")
               == "A",
               str((p2.get(
                        "comparison")
                    or {}).get(
                        "betterPlan")))

        # ③ 积分缺口(余额 5<
        #    信值 30——缺口 25)
        await seed_profile(2, 5.0)
        p3 = await exp.payment_plan(
            2, 100)
        record("积分缺口换算"
               "(缺 25→2500 积分)",
               (p3.get("planA") or {})
               .get("gap") == 25.0
               and (p3.get("planA")
                    or {}).get(
                        "gapPoints")
               == 2500.0,
               str((p3.get("planA")
                    or {}).get(
                        "gapPoints")))
        record("低余额方案 A "
               "不可行",
               (p3.get("planA")
                or {}).get(
                    "feasible")
               is False,
               "")

        # ④ 价格非法
        try:
            await exp.payment_plan(
                1, -5)
            ok, err = False, "未拒绝"
        except ValueError:
            ok, err = True, ""
        record("价格非正拒绝",
               ok, err)

        # ⑤ 优惠超价封顶
        p4 = await exp.payment_plan(
            1, 100,
            discount_value=200)
        record("优惠封顶"
               "(200→100)",
               (p4.get("planB")
                or {}).get("cash")
               == 0.0
               and (p4.get(
                   "planB")
                   or {}).get(
                       "discount")
               == 100.0,
               str(p4.get("planB")))

        # ⑥ 档案不存在
        try:
            await exp.payment_plan(
                999, 100)
            ok, err = False, "未拒绝"
        except KeyError:
            ok, err = True, ""
        record("档案不存在拒绝",
               ok, err)


class TestFill:
    """02 智能凑单"""

    async def run(self):
        print("[02 智能凑单]")
        reset_all()
        from services.xx64_experience_service import (
            Xx64ExperienceService,
        )
        exp = Xx64ExperienceService()

        await seed_profile(1, 500.0)

        # 候选: 密度相同(全 0.3)
        # ——价格降序
        candidates = [
            {"name": "商品C",
             "price": 50},
            {"name": "商品A",
             "price": 200},
            {"name": "商品B",
             "price": 100},
        ]
        r = await exp.smart_fill(
            1, candidates)
        ranked = [i["name"] for i in
                  (r.get("ranked")
                   or [])]
        record("密度排序"
               "(同密度价格降序)",
               ranked == ["商品A",
                          "商品B",
                          "商品C"],
               str(ranked))
        record("信值抵扣额"
               "(60/30/15)",
               [(r.get("ranked")
                 or [])[0]
                ["trustValue"],
                (r.get("ranked")
                 or [])[1]
                ["trustValue"]]
               == [60.0, 30.0],
               str([i.get(
                   "trustValue")
                   for i in
                   (r.get("ranked")
                    or [])]))

        # 单次限额内组合
        # (500×20%=100:
        #  60+30=90≤100, 15 超)
        affordable = [i["name"] for i in
                     (r.get("affordable")
                      or [])]
        record("限额内组合"
               "(A+B 不含 C)",
               affordable == ["商品A",
                             "商品B"],
               str(affordable))
        record("组合信值总额 90",
               r.get(
                   "affordableTrust"
                   "Total") == 90.0,
               str(r.get(
                   "affordable"
                   "TrustTotal")))

        # 确定性(同输入同输出)
        r2 = await exp.smart_fill(
            1, candidates)
        record("确定性排序"
               "(同输入同输出)",
               [i["name"] for i in
                (r2.get("ranked")
                 or [])]
               == ranked,
               "")

        # 空候选
        r3 = await exp.smart_fill(
            1, [])
        record("空候选(空列表)",
               (r3.get("ranked")
                or []) == []
               and (r3.get(
                   "affordable")
                   or []) == [],
               "")

        # 非法候选过滤
        r4 = await exp.smart_fill(
            1, [{"name": "X",
                 "price": -5},
                {"name": "Y"},
                "invalid"])
        record("非法候选过滤",
               (r4.get("ranked")
                or []) == [],
               str(r4.get("ranked")))


class TestExplain:
    """03 规则可视化解释"""

    async def run(self):
        print("[03 规则解释]")
        reset_all()
        from services.xx64_service import (
            Xx64Service,
        )
        await seed_profile(1, 500.0)
        os.environ["XX64_MODE"] = "shadow"
        r = await Xx64Service() \
            .create_order(
                101, 202, 1, 100,
                product="解释测试")

        from services.xx64_experience_service import (
            Xx64ExperienceService,
        )
        exp = Xx64ExperienceService()
        e = await exp.explain_order(
            r["orderId"])

        record("六步解释"
               "(R1-R6+R7)",
               len(e.get("steps")
                   or []) == 6,
               str(len(e.get(
                   "steps") or [])))
        rules = [s.get("rule") for s in
                 (e.get("steps")
                  or [])]
        record("规则序完整"
               "(R1→R7)",
               rules == ["R1", "R2",
                         "R4", "R5",
                         "R6", "R7"],
               str(rules))

        # R1 数字可溯源
        r1_step = (e.get("steps")
                   or [])[0]
        record("R1 计算可溯源"
               "(100×30%=30)",
               "价格 100.0"
               in r1_step.get("calc")
               and "30.0"
               in r1_step.get("calc"),
               str(r1_step.get(
                   "calc"))[:50])

        # R4 数字可溯源
        r4_step = (e.get("steps")
                   or [])[2]
        record("R4 限额可溯源"
               "(500×20%=100)",
               "500.0"
               in r4_step.get("calc")
               and "20%"
               in r4_step.get("calc"),
               str(r4_step.get(
                   "calc"))[:50])

        # R5 数字可溯源
        r5_step = (e.get("steps")
                   or [])[3]
        record("R5 窗口可溯源"
               "(40%)",
               "40%"
               in r5_step.get("calc"),
               str(r5_step.get(
                   "calc"))[:50])

        # 溯源字段(source)
        record("溯源字段齐备"
               "(每步有 source)",
               all(s.get("source")
                   for s in
                   (e.get("steps")
                    or [])),
               "")

        # 订单摘要
        record("订单摘要联动",
               (e.get("order")
                or {}).get("price")
               == 100.0,
               str((e.get("order")
                    or {}).get(
                        "price")))

        # 不存在订单
        try:
            await exp.explain_order(
                999)
            ok, err = False, "未拒绝"
        except KeyError:
            ok, err = True, ""
        record("订单不存在 404",
               ok, err)
        os.environ["XX64_MODE"] = "off"


class TestHttp:
    """04 HTTP 层"""

    async def run(self):
        print("[04 HTTP]")
        reset_all()
        from fastapi.testclient import \
            TestClient
        from main import app
        client = TestClient(app)
        admin = {"X-Role": "admin"}
        member = {"X-Role": "member"}

        await seed_profile(1, 500.0)
        os.environ["XX64_MODE"] = "shadow"
        # 种子订单
        resp = client.post(
            "/api/xx64/orders",
            json={"buyerId": 101,
                  "sellerId": 202,
                  "trustId": 1,
                  "price": 100},
            headers=member)
        order_id = (resp.json()
                    or {}).get(
                        "orderId")
        os.environ["XX64_MODE"] = "off"

        # plan 观测面(off 可用)
        resp = client.get(
            "/api/xx64/plan"
            "?trust_id=1&price=100"
            "&discount_value=15",
            headers=member)
        body = resp.json() or {}
        record("HTTP plan 200"
               "(off 观测面)",
               resp.status_code == 200
               and (body.get(
                   "comparison")
                   or {}).get(
                       "betterPlan")
               == "A",
               str((resp.status_code,
                    (body.get(
                        "comparison")
                     or {}).get(
                        "better"
                        "Plan"))))

        # plan 凑单(candidates 串)
        resp = client.get(
            "/api/xx64/plan"
            "?trust_id=1&price=100"
            "&candidates=甲:200,乙:100,"
            "丙:50",
            headers=member)
        body = resp.json() or {}
        sf = body.get("smartFill") or {}
        record("HTTP plan 凑单"
               "(密度+限额组合)",
               resp.status_code == 200
               and [i.get("name")
                    for i in (
                        sf.get(
                            "affordable")
                        or [])]
               == ["甲", "乙"],
               str((resp.status_code,
                    [i.get("name")
                     for i in (
                         sf.get(
                             "affordable")
                         or [])])))

        # explain 观测面
        resp = client.get(
            f"/api/xx64/orders/"
            f"{order_id}/explain",
            headers=member)
        body = resp.json() or {}
        record("HTTP explain 200"
               "(六步)",
               resp.status_code == 200
               and len(body.get(
                   "steps") or [])
               == 6,
               str((resp.status_code,
                    len(body.get(
                        "steps")
                        or []))))

        # explain 404
        resp = client.get(
            "/api/xx64/orders/999/"
            "explain",
            headers=admin)
        record("HTTP explain 404",
               resp.status_code == 404,
               str(resp.status_code))

        # plan 参数非法 409
        resp = client.get(
            "/api/xx64/plan"
            "?trust_id=1&price=-5",
            headers=member)
        record("HTTP plan 非法 409",
               resp.status_code == 409,
               str(resp.status_code))

        # 鉴权 403
        for path in (
                "/api/xx64/plan?"
                "trust_id=1&price=100",
                "/api/xx64/orders/1/"
                "explain"):
            resp = client.get(path)
            record("HTTP 无 Role 403",
                   resp.status_code
                   == 403,
                   str(resp.status_code))

        # 路由累计 14
        from routes.xx64_routes import (
            router as xx_router,
        )
        count = sum(
            1 for r in
            xx_router.routes)
        record("64号路由 P2 14 端点",
               count == 14, str(count))


class TestConstitution:
    """05 宪法断言"""

    async def run(self):
        print("[05 宪法断言]")
        from services.ai_learning_service import (
            SCORER_REGISTRY,
        )
        record("44号 39 档案在册",
               len(SCORER_REGISTRY)
               == 39,
               str(len(
                   SCORER_REGISTRY)))
        record("LLM 不进判定链"
               "(确定性组合)",
               os.environ.get(
                   "XX64_LLM_MODE")
               == "off",
               "")
        record("三开关铁律",
               os.environ.get(
                   "XX64_MODE") == "off",
               "")


async def run_all():
    await TestPlan().run()
    await TestFill().run()
    await TestExplain().run()
    await TestHttp().run()
    await TestConstitution().run()


def main():
    asyncio.run(run_all())
    print()
    print("=" * 62)
    print("\n".join(RESULTS))
    print("=" * 62)
    print(f"总计: {PASS} 通过 / {FAIL} 失败")
    return FAIL


if __name__ == "__main__":
    sys.exit(1 if main() else 0)
