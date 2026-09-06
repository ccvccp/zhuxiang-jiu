"""64号·信值兑换管理模块 P5 专项测试
(看板+红队+收官)

运行方式:
    python test_xx64_p5.py

覆盖(64号计划 §八 P5):
    - 四区看板: 度量区数学/
      九态分布/流通聚合(借贷
      平衡)/防御区统计/宪法
      三开关
    - 红队七向量: RT-01 规则
      绕过/RT-02 拆单绕限/
      RT-03 积分套利/RT-04
      价格操纵/RT-05 双花
      (并发)/RT-06 申诉刷分/
      RT-07 负值透支——每
      向量 defended+evidence
      断言+自清理验证
    - repository delete 助手
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


async def add_order(repo, buyer, seller,
                    trust, product, price,
                    tv, status="paid"):
    from datetime import datetime, UTC
    from core.helpers import ts
    now = datetime.now(UTC).isoformat()
    oid = await repo.next_order_id()
    await repo.save_order({
        "orderId": oid,
        "buyerId": buyer,
        "sellerId": seller,
        "trustId": trust,
        "product": product,
        "price": float(price),
        "trustValue": float(tv),
        "cashValue": round(float(price)
                           - float(tv), 2),
        "balanceSnapshot": 500.0,
        "status": status,
        "paidAt": now if status in (
            "paid", "settled",
            "completed") else "",
        "createdAt": now,
    })
    return oid


class TestDashboard:
    """01 四区看板"""

    async def run(self):
        print("[01 四区看板]")
        reset_all()
        await seed_profile(1, 500.0)
        from repositories.xx64_repository import (
            Xx64Repository,
        )
        repo = Xx64Repository()
        # 2 paid+1 completed+1 cancelled
        await add_order(repo, 1, 2, 1,
                        "gA", 100, 30)
        await add_order(repo, 1, 2, 1,
                        "gB", 100, 30)
        await add_order(repo, 1, 2, 1,
                        "gC", 100, 30,
                        status="completed")
        await add_order(repo, 1, 2, 1,
                        "gD", 100, 30,
                        status="cancelled")
        # 借贷对
        eid = await repo.next_entry_id()
        for d, amt in (("credit", 30.0),
                       ("debit", -30.0)):
            await repo.save_ledger({
                "entryId": eid,
                "orderId": 1,
                "trustId": 1,
                "direction": d,
                "transferType": "pay",
                "amount": amt,
                "source":
                    "consumption_"
                    "transfer",
                "createdAt":
                    __import__(
                        "core.helpers",
                        fromlist=["ts"]).ts(),
            })
        from services.xx64_dashboard_service import (
            Xx64DashboardService,
        )
        d = await Xx64DashboardService() \
            .dashboard()
        z = d["zones"]

        # 度量区
        m = z["metrics"]
        record("度量区数学"
               "(4 单/120 信值/280 现金)",
               m["totalOrders"] == 4
               and m["totalTrustValue"]
               == 120.0
               and m["totalCashValue"]
               == 280.0
               and m["activeBuyers"]
               == 1,
               str(m))

        # 订单区(九态分布)
        dist = z["orders"][
            "statusDistribution"]
        record("九态分布"
               "(paid2+completed1+"
               "cancelled1)",
               dist["paid"] == 2
               and dist["completed"] == 1
               and dist["cancelled"] == 1
               and len(dist) == 9,
               str(dist))
        record("近期订单(≤5 条)",
               0 < len(z["orders"]["recent"])
               <= 5,
               str(len(z["orders"]
                        ["recent"])))

        # 流通区(借贷平衡)
        c = z["circulation"]
        record("流通区聚合"
               "(2 笔/30/30/平衡)",
               c["ledgerEntries"] == 2
               and c["debitTotal"] == 30.0
               and c["creditTotal"] == 30.0
               and c["balanced"] is True,
               str(c))
        record("流通区积分统计结构",
               set(c["exchanges"]
                   .keys()) == {
                   "pending", "credited",
                   "cancelled"},
               str(c["exchanges"]))

        # 防御区
        dfn = z["defense"]
        record("防御区结构"
               "(风控+申诉)",
               "riskEvents" in dfn
               and "byDetector" in dfn
               and "appeals" in dfn,
               str(dfn.keys()))
        record("防御区申诉统计"
               "(翻转率)",
               "overturnRate"
               in dfn["appeals"]
               and "approved"
               in dfn["appeals"],
               str(dfn["appeals"]))

        # 宪法三开关
        cst = d["constitution"]
        record("宪法三开关+R1-R7",
               cst["mode"] == "off"
               and cst["learnMode"] == "off"
               and cst["llmMode"] == "off"
               and "R1-R7" in cst["rules"],
               str(cst))


class TestRedteam:
    """02 红队七向量"""

    async def run(self):
        print("[02 红队七向量]")
        reset_all()
        await seed_profile(1, 500.0)
        os.environ["XX64_MODE"] = "shadow"
        from services.xx64_redteam_service import (
            Xx64RedteamService,
        )
        rt = Xx64RedteamService()
        r = await rt.run_all()
        os.environ["XX64_MODE"] = "off"

        vec = {v["vector"]: v
               for v in r["vectors"]}

        # 总览
        record("七向量齐备",
               r["total"] == 7
               and sorted(vec)
               == [f"RT-0{i}"
                   for i in
                   range(1, 8)],
               str(sorted(vec)))
        record("全防住"
               "(allDefended)",
               r["allDefended"] is True
               and r["defended"] == 7,
               str((r["defended"],
                    r["total"])))

        # RT-01 证据
        e1 = vec["RT-01"]["evidence"]
        record("RT-01 伪字段忽略+"
               "R4 拒+负价拒",
               e1["forgedIgnored"]
               is True
               and e1["r4Rejected"]
               is True
               and e1[
                   "invalidPriceRejected"]
               is True,
               str(e1))

        # RT-02 证据
        e2 = vec["RT-02"]["evidence"]
        record("RT-02 拆单 R5 拒"
               "(210>200)",
               e2["r5Rejected"] is True
               and e2["windowUsed"]
               == 150.0,
               str(e2))

        # RT-03 证据
        e3 = vec["RT-03"]["evidence"]
        record("RT-03 高频第 4 次拒"
               "(R6 日限频)",
               e3["fourthRejected"]
               is True
               and e3[
                   "succeededBeforeLimit"]
               == 3,
               str(e3))

        # RT-04 证据
        e4 = vec["RT-04"]["evidence"]
        record("RT-04 操纵检测"
               "(drift 0.5>0.2)",
               e4["detected"] is True
               and e4["drift"] == 0.5,
               str(e4))

        # RT-05 证据(并发双花)
        e5 = vec["RT-05"]["evidence"]
        record("RT-05 并发双花"
               "(恰 1 成功)",
               e5["exactlyOne"] is True
               and e5["successes"] == 1,
               str(e5))

        # RT-06 证据
        e6 = vec["RT-06"]["evidence"]
        record("RT-06 重复拒+过期"
               "不翻转",
               e6["duplicateRejected"]
               is True
               and e6[
                   "expiredNotFlipped"]
               is True
               and e6["finalStatus"]
               == "expired",
               str(e6))

        # RT-07 证据
        e7 = vec["RT-07"]["evidence"]
        record("RT-07 负值透支 R7 拒"
               "(10<30)",
               e7["r7Rejected"] is True
               and e7["balanceAtPay"]
               == 10.0,
               str(e7))

        # 自清理验证(红队订单
        # 不残留——98xx 买家域)
        from repositories.xx64_repository import (
            Xx64Repository,
        )
        repo = Xx64Repository()
        leftover = [
            o for o in await
            repo.list_orders(limit=500)
            if int(o.get("buyerId")
                   or 0) >= 9801
            and str(o.get("product")
                    or "").startswith(
                "rt")]
        record("红队自清理"
               "(无 rt 订单残留)",
               len(leftover) == 0,
               str([o.get("product")
                    for o in leftover]))

        # 双轮幂等(红队可重复执行
        # ——种子自清理保证)
        os.environ["XX64_MODE"] = "shadow"
        r2 = await Xx64RedteamService() \
            .run_all()
        os.environ["XX64_MODE"] = "off"
        record("红队双轮幂等"
               "(second 7/7)",
               r2["allDefended"] is True
               and r2["defended"] == 7,
               str((r2["defended"],
                    r2["total"])))

        # RT-05 失败样本为状态机拒绝
        sample = e5.get(
            "failureSample") or ""
        record("RT-05 失败为状态拒"
               "(不可支付)",
               "不可支付" in sample
               or "支付" in sample,
               sample)


class TestDeleteHelpers:
    """03 repository delete 助手"""

    async def run(self):
        print("[03 delete 助手]")
        reset_all()
        await seed_profile(1, 500.0)
        from repositories.xx64_repository import (
            Xx64Repository,
        )
        repo = Xx64Repository()
        oid = await add_order(repo, 1, 2,
                              1, "gX",
                              100, 30)
        record("delete_order 删除",
               await repo.delete_order(
                   oid) is True
               and await repo.get_order(
                   oid) is None,
               "")
        eid = await repo.next_entry_id()
        for d in ("credit", "debit"):
            await repo.save_ledger({
                "entryId": eid,
                "orderId": 1,
                "trustId": 1,
                "direction": d,
                "amount": 30.0
                if d == "credit"
                else -30.0,
                "source":
                    "consumption_"
                    "transfer",
                "createdAt": "x",
            })
        removed = await \
            repo.delete_ledger_pair(
                eid)
        record("delete_ledger_pair"
               "(借贷两笔)",
               removed == 2
               and await repo.list_ledger(
                   limit=10) == [],
               str(removed))
        rid = await repo.next_risk_id()
        await repo.save_risk({
            "riskId": rid,
            "detectedAt": "x",
            "detectorCode":
                "ARB-HF",
            "entityKey": "user:1",
            "severity": "medium",
            "riskScore": 45,
            "matched": True,
            "detail": {},
            "action": "pass",
            "status": "open",
        })
        record("delete_risk 删除",
               await repo.delete_risk(
                   rid) is True
               and await repo.list_risks(
                   limit=10) == [],
               "")


class TestPayLock:
    """04 支付并发双花防护锁"""

    async def run(self):
        print("[04 支付占位锁]")
        reset_all()
        await seed_profile(1, 500.0)
        from services.xx64_settle_service import (
            Xx64SettleService,
            _claim_pay, _release_pay,
        )
        # 直接占位/释放
        first = await _claim_pay(777)
        second = await _claim_pay(777)
        await _release_pay(777)
        third = await _claim_pay(777)
        await _release_pay(777)
        record("占位互斥+释放可重占",
               first is True
               and second is False
               and third is True,
               str((first, second,
                    third)))

        # 完整链路: 支付后锁释放
        # (二次顺序支付不被锁误拒)
        os.environ["XX64_MODE"] = \
            "shadow"
        from services.xx64_service import (
            Xx64Service,
        )
        o1 = await Xx64Service() \
            .create_order(1, 2, 1, 100,
                          product="L1")
        o2 = await Xx64Service() \
            .create_order(1, 2, 1, 100,
                          product="L2")
        settle = Xx64SettleService()
        r1 = await settle.pay_order(
            o1["orderId"])
        r2 = await settle.pay_order(
            o2["orderId"])
        os.environ["XX64_MODE"] = "off"
        record("支付后锁释放"
               "(相邻订单连续可付)",
               r1["status"] == "paid"
               and r2["status"] == "paid",
               str((r1["status"],
                    r2["status"])))


class TestHttp:
    """05 HTTP 端点"""

    async def run(self):
        print("[05 HTTP 端点+鉴权]")
        from httpx import ASGITransport, \
            AsyncClient
        from main import app

        reset_all()
        await seed_profile(1, 500.0)
        admin = {"X-Role": "admin"}
        member = {"X-Role": "member"}

        async with AsyncClient(
                transport=ASGITransport(
                    app=app),
                base_url="http://t"
        ) as client:
            # dashboard 观测面
            # (off 可用)
            resp = await client.get(
                "/api/xx64/dashboard",
                headers=admin)
            body = resp.json() or {}
            record("HTTP dashboard 200"
                   "(off 观测面)",
                   resp.status_code == 200
                   and "zones" in body
                   and set(body["zones"]
                           .keys()) == {
                       "metrics", "orders",
                       "circulation",
                       "defense"},
                   str((resp.status_code,
                        list((body.get(
                            "zones") or {}
                        ).keys()))))

            # dashboard member 403
            resp = await client.get(
                "/api/xx64/dashboard",
                headers=member)
            record("HTTP dashboard "
                   "member 403",
                   resp.status_code == 403,
                   str(resp.status_code))

            # redteam off 409
            resp = await client.post(
                "/api/xx64/redteam",
                headers=admin)
            record("HTTP redteam off 409",
                   resp.status_code == 409,
                   str(resp.status_code))

            # redteam shadow 200
            # (七向量全防)
            os.environ["XX64_MODE"] = \
                "shadow"
            resp = await client.post(
                "/api/xx64/redteam",
                headers=admin)
            body = resp.json() or {}
            record("HTTP redteam 200"
                   "(shadow 七向量)",
                   resp.status_code == 200
                   and body.get(
                       "allDefended")
                   is True
                   and body.get("total")
                   == 7,
                   str((resp.status_code,
                        body.get(
                            "defended"),
                        body.get(
                            "total"))))
            os.environ["XX64_MODE"] = "off"

            # redteam member 403
            os.environ["XX64_MODE"] = \
                "shadow"
            resp = await client.post(
                "/api/xx64/redteam",
                headers=member)
            record("HTTP redteam "
                   "member 403",
                   resp.status_code == 403,
                   str(resp.status_code))
            os.environ["XX64_MODE"] = "off"

            # 无 Role 403
            resp = await client.get(
                "/api/xx64/dashboard")
            record("HTTP 无 Role 403",
                   resp.status_code == 403,
                   str(resp.status_code))


class TestConstitution:
    """06 宪法断言(收官)"""

    async def run(self):
        print("[06 宪法断言(收官)]")
        from services.ai_learning_service import (
            SCORER_REGISTRY,
        )
        record("44号 39 档案在册",
               len(SCORER_REGISTRY) == 39,
               str(len(SCORER_REGISTRY)))

        from routes.xx64_routes import (
            router as xx_router,
        )
        count = sum(
            1 for r in xx_router.routes)
        record("64号路由 P5 26 端点"
               "(全期收官)",
               count == 26, str(count))

        # 三开关铁律
        record("三开关铁律"
               "(XX64/LLM/LEARN off)",
               os.environ.get(
                   "XX64_MODE") == "off"
               and os.environ.get(
                   "XX64_LLM_MODE")
               == "off"
               and os.environ.get(
                   "XX64_LEARN_MODE")
               == "off",
               "")

        # 刚性规则宪法不变
        from services.xx64_registry import (
            CASH_PORTION, CUMULATIVE_QUOTA_RATIO,
            POINTS_PER_TRUST,
            SINGLE_QUOTA_RATIO,
            TRUST_PORTION, WINDOW_DAYS,
        )
        record("R1-R7 宪法常量不变",
               TRUST_PORTION == 0.30
               and CASH_PORTION == 0.70
               and SINGLE_QUOTA_RATIO
               == 0.20
               and CUMULATIVE_QUOTA_RATIO
               == 0.40
               and POINTS_PER_TRUST
               == 100
               and WINDOW_DAYS == 30,
               "")

        # LLM 不进判定链
        # (看板/红队无 LLM 依赖)
        import inspect
        import services.xx64_dashboard_service \
            as dash_mod
        import services.xx64_redteam_service \
            as rt_mod
        for mod, name in (
                (dash_mod, "看板"),
                (rt_mod, "红队")):
            src = inspect.getsource(
                mod)
            imports = [
                l.strip() for l in
                src.splitlines()
                if l.strip().startswith(
                    ("import ",
                     "from "))]
            record(f"LLM 不进{name}"
                   f"判定链",
                   all("llm" not in
                       l.lower()
                       for l in
                       imports),
                   str(imports))


async def main():
    suites = [
        TestDashboard(), TestRedteam(),
        TestDeleteHelpers(), TestPayLock(),
        TestHttp(), TestConstitution(),
    ]
    for s in suites:
        await s.run()
    print()
    print("=" * 62)
    print("\n".join(RESULTS))
    print("=" * 62)
    print(f"总计: {PASS} 通过 / {FAIL} 失败")
    return FAIL


if __name__ == "__main__":
    sys.exit(1 if asyncio.run(main())
             else 0)
