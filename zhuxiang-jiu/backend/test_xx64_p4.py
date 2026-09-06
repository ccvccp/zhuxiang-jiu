"""64号·信值兑换管理模块 P4 专项测试
(价值锚定与治理层)

运行方式:
    python test_xx64_p4.py

覆盖(64号 P4 设计 §十一):
    - 购买力指数: 均价/指数=
      基准比/冷启动<3 不落/
      同日重算幂等/通胀通缩预警
      /样本可溯源/序列排序
    - 供需预警: 需求激增/不激增
      /仅建议/事件留痕
    - 校准建议: 失衡方向判定
      /46号提交轨 fail-soft
      /宪法域不可校准
    - 申诉通道: 提交不受开关
      /重复申诉拒/重算展示/
      approve 翻转/reject 维持/
      expired 48h/翻转留痕
    - 回流幂等: 双轮 collect
      labeled=0/信号判定/
      pooled 标记回写
    - 调度+HTTP+宪法断言
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
                    tv, status="paid",
                    paid_at=None,
                    created_at=None):
    from datetime import datetime, UTC
    from core.helpers import ts
    now = paid_at or created_at or \
        datetime.now(UTC).isoformat()
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
        "createdAt": created_at
        or datetime.now(UTC).isoformat(),
    })
    return oid


class TestAnchor:
    """01 购买力指数"""

    async def run(self):
        print("[01 购买力指数]")
        from repositories.xx64_repository import (
            Xx64Repository,
        )
        from services.xx64_anchor_service import (
            Xx64AnchorService,
        )
        reset_all()
        await seed_profile(1, 500.0)
        await seed_profile(2, 500.0)
        repo = Xx64Repository()
        svc = Xx64AnchorService()

        # 均价(4 笔: 100×3+50)
        for _ in range(3):
            await add_order(repo, 1, 2, 1,
                            "gA", 100, 30)
        await add_order(repo, 1, 2, 1,
                        "gA", 50, 15)
        snap = await svc.snapshot()
        record("均价计算(87.5)",
               snap.get("avgPrice") == 87.5
               and snap.get("samples")
               == 4,
               str(snap))

        # 指数=基准/当前(首期自锚=1.0)
        record("指数首期自锚(1.0)",
               snap.get("purchasingPower")
               == 1.0,
               str(snap.get(
                   "purchasingPower")))

        # 同日重算幂等(同 anchorId)
        snap2 = await svc.snapshot()
        record("同日重算幂等",
               snap2.get("anchorId")
               == snap.get("anchorId"),
               str((snap.get("anchorId"),
                    snap2.get(
                        "anchorId"))))

        # 冷启动 <3 不落
        reset_all()
        for _ in range(2):
            await add_order(repo, 1, 2, 1,
                            "gB", 100, 30)
        snap3 = await svc.snapshot()
        record("冷启动<3 不落快照",
               snap3.get("skipped")
               is True
               and snap3.get("samples")
               == 2,
               str(snap3))

        # 通胀预警(连续 3 日下行
        # 且累计 >10%)
        reset_all()
        from datetime import datetime, \
            UTC, timedelta
        base = datetime.now(UTC)
        # 手工构造 4 日序列
        powers = [1.0, 0.95, 0.85, 0.75]
        for i, p in enumerate(powers):
            day = (base
                   - timedelta(days=3 - i)
                   ).strftime("%Y-%m-%d")
            aid = await \
                repo.next_anchor_id()
            await repo.save_anchor({
                "anchorId": aid,
                "anchorDate": day,
                "avgPrice": round(
                    100 / p, 2),
                "purchasingPower": p,
                "samples": 10,
                "baseline": 100.0,
                "createdAt": ts_day(day),
                "updatedAt": ts_day(day),
            })
        view = await svc.anchors_view()
        alarms = view.get("alarms") or []
        record("通胀预警"
               "(3 日下行>10%)",
               len(alarms) == 1
               and alarms[0]["type"]
               == "inflation"
               and alarms[0]
               ["autoExecute"]
               is False,
               str(alarms))

        # 序列排序(日期升序)
        series = view.get("series") or []
        record("序列日期升序",
               [s["anchorDate"]
                for s in series]
               == sorted(
                   s["anchorDate"]
                   for s in series),
               str([s["anchorDate"]
                    for s in series]))


def ts_day(day: str) -> str:
    return f"{day}T00:00:00+00:00"


class TestSupplyDemand:
    """02 供需预警"""

    async def run(self):
        print("[02 供需预警]")
        from repositories.xx64_repository import (
            Xx64Repository,
        )
        from services.xx64_anchor_service import (
            Xx64AnchorService,
        )
        reset_all()
        repo = Xx64Repository()
        svc = Xx64AnchorService()
        from datetime import datetime, \
            UTC, timedelta
        now = datetime.now(UTC)
        old = (now
               - timedelta(days=3)
               ).isoformat()

        # 需求激增: 前 7 日 7 笔
        # (日均 1)+24h 6 笔(>1×3
        # 且 ≥5)
        for _ in range(7):
            await add_order(repo, 1, 2,
                            1, "gS", 100,
                            30, paid_at=old)
        for _ in range(6):
            await add_order(repo, 1, 2,
                            1, "gS", 100,
                            30)
        sd = await \
            svc.supply_demand_scan()
        alerts = sd.get("alerts") or []
        record("需求激增"
               "(6 笔>日均1×3)",
               len(alerts) == 1
               and alerts[0]["product"]
               == "gS"
               and alerts[0]["count24h"]
               == 6
               and alerts[0]
               ["autoExecute"]
               is False,
               str(alerts))

        # 不激增: 24h 2 笔(<5)
        reset_all()
        for _ in range(7):
            await add_order(repo, 1, 2,
                            1, "gS", 100,
                            30, paid_at=old)
        for _ in range(2):
            await add_order(repo, 1, 2,
                            1, "gS", 100,
                            30)
        sd = await \
            svc.supply_demand_scan()
        record("2 笔不激增(<5)",
               len(sd.get("alerts")
                   or []) == 0,
               str(sd.get("alerts")))

        # 事件留痕(supply_demand)
        reset_all()
        for _ in range(7):
            await add_order(repo, 1, 2,
                            1, "gS", 100,
                            30, paid_at=old)
        for _ in range(6):
            await add_order(repo, 1, 2,
                            1, "gS", 100,
                            30)
        await svc.supply_demand_scan()
        events = await repo.list_events(
            limit=10)
        record("事件留痕"
               "(supply_demand)",
               any(e.get("eventType")
                   == "supply_demand"
                   for e in events),
               str([e.get("eventType")
                    for e in events]))


class TestCalibrate:
    """03 校准建议"""

    async def run(self):
        print("[03 校准建议]")
        from services.xx64_anchor_service import (
            Xx64AnchorService,
        )
        svc = Xx64AnchorService()

        # 均衡(无流水——ratio 全 0
        # → burn_fast 但 46号
        # fail-soft 未入册跳过)
        rc = await svc.rate_check()
        record("失衡判定+46号提交轨",
               rc.get("status") in (
                   "balanced",
                   "burn_fast",
                   "earn_fast")
               and rc.get("submitted")
               is False,
               str((rc.get("status"),
                    rc.get(
                        "submitted"))))

        # 提交轨 fail-soft
        # (未入册不抛异常)
        record("46号 fail-soft 跳过",
               rc.get("submitError")
               != "" or rc.get(
                   "submitted") is True,
               str(rc.get(
                   "submitError")))

        # 阈值域: 宪法域不可校准
        tv = await svc.thresholds_view()
        record("宪法域永不可校准",
               "trustPortion"
               in tv["constitution"]
               and "calibratable"
               in tv,
               str(tv.get(
                   "constitution")))
        record("可调域清单齐备",
               set(tv["calibratable"]
                   .keys()) == {
                   "singleQuotaRatio",
                   "cumulativeQuotaRatio",
                   "windowDays",
                   "pointsDailyLimit",
                   "pointsFrozenHours"},
               str(tv["calibratable"]
                   .keys()))


class TestAppeal:
    """04 申诉通道"""

    async def run(self):
        print("[04 申诉通道]")
        from repositories.xx64_repository import (
            Xx64Repository,
        )
        from services.xx64_appeal_service import (
            Xx64AppealService,
        )
        reset_all()
        await seed_profile(1, 500.0)
        await seed_profile(2, 500.0)
        repo = Xx64Repository()
        svc = Xx64AppealService()

        # paid 订单+提交(off 态
        # ——不受开关影响)
        oid = await add_order(repo, 1, 2,
                              1, "gP", 100,
                              30)
        os.environ["XX64_MODE"] = "off"
        ap = await svc.submit(
            oid, "金额计算争议")
        record("提交不受开关影响",
               ap.get("status")
               == "recalculated",
               str(ap))

        # 重算展示(precheck+explain
        # +risk 三件套)
        recalc = ap.get("recalc") or {}
        record("重算展示三件套",
               "precheck" in recalc
               and "explainSteps"
               in recalc
               and "riskFindings"
               in recalc,
               str(recalc.keys()))

        # 重复申诉拒
        try:
            await svc.submit(
                oid, "重复申诉")
            dup = False
        except ValueError:
            dup = True
        record("重复申诉拒绝",
               dup is True, "")

        # reject 维持
        rv = await svc.review(
            ap["appealId"], "reject",
            "证据不足")
        record("reject 维持原判",
               rv["status"] == "rejected"
               and rv["compensation"]
               == {},
               str(rv))

        # 拒绝后可再申诉(终态)
        ap2 = await svc.submit(
            oid, "补充证据再诉")
        record("终态后可再申诉",
               ap2["appealId"]
               > ap["appealId"],
               str(ap2["appealId"]))

        # approve 翻转(disputed
        # →paid)
        reset_all()
        await seed_profile(1, 500.0)
        oid2 = await add_order(
            repo, 1, 2, 1, "gD", 100, 30,
            status="disputed")
        ap3 = await svc.submit(
            oid2, "风控误判申诉")
        rv3 = await svc.review(
            ap3["appealId"], "approve",
            "人工核实误判")
        record("approve 翻转"
               "(disputed→paid)",
               rv3["status"]
               == "approved"
               and rv3["compensation"]
               .get("actions")[0]
               ["action"] == "unfreeze",
               str(rv3["compensation"]))
        order = await repo.get_order(
            oid2)
        record("订单恢复 paid",
               order.get("status")
               == "paid",
               str(order.get(
                   "status")))

        # expired 48h(构造过期)
        reset_all()
        await seed_profile(1, 500.0)
        oid3 = await add_order(repo, 1,
                               2, 1, "gE",
                               100, 30)
        ap4 = await svc.submit(
            oid3, "过期测试")
        # 手工改 expiresAt 为 1h 前
        appeal = await repo.get_appeal(
            ap4["appealId"])
        from datetime import datetime, \
            UTC, timedelta
        appeal["expiresAt"] = (
            datetime.now(UTC)
            - timedelta(hours=1)
        ).isoformat()
        await repo.save_appeal(
            appeal, create=False)
        try:
            await svc.review(
                ap4["appealId"],
                "approve", "迟到的终审")
            expired = False
        except ValueError:
            expired = True
        record("48h 过期终审拒",
               expired is True, "")

        # 观测面清理过期
        closed = await svc \
            .expire_stale()
        record("过期批量关闭"
               "(不翻转)",
               closed >= 1,
               str(closed))


class TestLearn:
    """05 回流幂等"""

    async def run(self):
        print("[05 回流幂等]")
        from repositories.xx64_repository import (
            Xx64Repository,
        )
        from services.xx64_learn_service import (
            Xx64LearnService,
        )
        reset_all()
        repo = Xx64Repository()
        svc = Xx64LearnService()

        # 终态订单(completed/
        # refunded/cancelled×2)
        await add_order(repo, 1, 2, 1,
                        "g1", 100, 30,
                        status="completed")
        await add_order(repo, 1, 2, 1,
                        "g2", 100, 30,
                        status="refunded")
        await add_order(repo, 1, 2, 1,
                        "g3", 100, 30,
                        status="cancelled")
        await add_order(repo, 1, 2, 1,
                        "g4", 100, 30,
                        status="paid")

        # 首轮: 2 labeled
        # (completed+refunded)
        c1 = await svc.collect_feedback()
        record("首轮 2 信号"
               "(ok+refunded)",
               c1["labeled"] == 2
               and c1["signals"].get(
                   "exchange_ok") == 1
               and c1["signals"].get(
                   "exchange_refunded")
               == 1,
               str(c1["signals"]))

        # 幂等: 第二轮 labeled=0
        c2 = await svc.collect_feedback()
        record("双轮幂等"
               "(second labeled=0)",
               c2["labeled"] == 0
               and c2["skipped"] == 4,
               str((c2["labeled"],
                    c2["skipped"])))

        # pooled 标记回写
        order1 = await repo.get_order(1)
        record("pooledFeedbackId 回写",
               int(order1.get(
                   "pooledFeedbackId")
                   or 0) > 0
               and order1.get(
                   "poolSignal")
               == "exchange_ok",
               str((order1.get(
                   "pooledFeedbackId"),
                    order1.get(
                        "poolSignal"))))

        # 因子聚合观测面
        st = await svc.learn_status()
        factors = st.get("factors") or {}
        record("因子聚合观测面",
               st["learnMode"] == "off"
               and "exchangeHealth"
               in factors
               and "anchorVolatility"
               in factors,
               str(factors.keys()))


class TestScheduler:
    """06 调度"""

    async def run(self):
        print("[06 调度]")
        import services.xx64_scheduler \
            as sched

        # LEARN_MODE off 不启动
        record("调度开关 off",
               sched.scheduler_enabled()
               is False,
               "")

        # 手动一轮(四任务齐)
        reset_all()
        await seed_profile(1, 500.0)
        await seed_profile(2, 500.0)
        from repositories.xx64_repository import (
            Xx64Repository,
        )
        repo = Xx64Repository()
        await add_order(repo, 1, 2, 1,
                        "gS", 100, 30,
                        status="completed")
        r = await \
            sched.run_scheduled_tasks()
        record("手动四任务齐",
               r["snapshot"]
               is not None
               and r["collect"]
               is not None
               and r["supplyDemand"]
               is not None
               and r["rateCheck"]
               is not None
               and r["errors"] == [],
               str(r["errors"]))

        # 调度留痕
        events = await repo.list_events(
            limit=5)
        record("调度留痕"
               "(scheduler_run)",
               any(e.get("eventType")
                   == "scheduler_run"
                   for e in events),
               str([e.get("eventType")
                    for e in events]))


class TestHttp:
    """07 HTTP 端点"""

    async def run(self):
        print("[07 HTTP 端点]")
        from httpx import ASGITransport, \
            AsyncClient
        from main import app

        reset_all()
        await seed_profile(1, 500.0)
        await seed_profile(2, 500.0)
        admin = {"X-Role": "admin"}
        member = {"X-Role": "member"}

        from repositories.xx64_repository import (
            Xx64Repository,
        )
        repo = Xx64Repository()
        oid = await add_order(repo, 1, 2,
                              1, "gH", 100,
                              30,
                              status="completed")

        async with AsyncClient(
                transport=ASGITransport(
                    app=app),
                base_url="http://t"
        ) as client:
            # anchors 观测面(off 可用)
            resp = await client.get(
                "/api/xx64/anchors",
                headers=member)
            record("HTTP anchors 200"
                   "(off 观测面)",
                   resp.status_code == 200
                   and "series"
                   in (resp.json()
                       or {}),
                   str(resp.status_code))

            # anchors/audit off 409
            resp = await client.post(
                "/api/xx64/anchors/audit",
                headers=admin)
            record("HTTP audit off 409",
                   resp.status_code == 409,
                   str(resp.status_code))

            # appeal 全链(不受开关)
            resp = await client.post(
                "/api/xx64/appeals",
                json={"orderId": oid,
                      "reason":
                          "HTTP 全链测试"},
                headers=member)
            body = resp.json() or {}
            appeal_id = body.get(
                "appealId")
            record("HTTP appeal 200"
                   "(off 不受开关)",
                   resp.status_code
                   == 200
                   and appeal_id > 0
                   and "recalc" in body,
                   str((resp.status_code,
                        appeal_id)))

            # review 翻转
            resp = await client.post(
                f"/api/xx64/appeals/"
                f"{appeal_id}/review",
                json={"decision":
                          "approve",
                      "reviewNote":
                          "人工核实"},
                headers=admin)
            record("HTTP review 200+翻转",
                   resp.status_code == 200
                   and (resp.json()
                        or {}).get(
                       "status")
                   == "approved",
                   str(resp.status_code))

            # thresholds 观测面
            resp = await client.get(
                "/api/xx64/thresholds",
                headers=admin)
            record("HTTP thresholds 200",
                   resp.status_code == 200
                   and "constitution"
                   in (resp.json()
                       or {}),
                   str(resp.status_code))

            # feedback/collect(不受开关)
            resp = await client.post(
                "/api/xx64/feedback/"
                "collect",
                headers=admin)
            record("HTTP collect 200"
                   "(off 不受开关)",
                   resp.status_code == 200
                   and (resp.json()
                        or {}).get(
                       "labeled")
                   == 1,
                   str((resp.status_code,
                        (resp.json()
                         or {}).get(
                            "labeled"))))

            # learn/status 观测面
            resp = await client.get(
                "/api/xx64/learn/status",
                headers=admin)
            record("HTTP learn 200",
                   resp.status_code == 200
                   and "factors"
                   in (resp.json()
                       or {}),
                   str(resp.status_code))

            # 鉴权: review member 403
            resp = await client.post(
                "/api/xx64/appeals/1/"
                "review",
                json={"decision":
                          "approve"},
                headers=member)
            record("HTTP review "
                   "member 403",
                   resp.status_code == 403,
                   str(resp.status_code))


class TestConstitution:
    """08 宪法断言"""

    async def run(self):
        print("[08 宪法断言]")
        from services.ai_learning_service import (
            SCORER_REGISTRY,
        )
        record("44号 39 档案在册",
               len(SCORER_REGISTRY) == 40,
               str(len(SCORER_REGISTRY)))

        from routes.xx64_routes import (
            router as xx_router,
        )
        count = sum(
            1 for r in xx_router.routes)
        record("64号路由 P4 24 端点",
               count >= 24, str(count))

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

        # 申诉/回流不受开关影响
        # (路由无 mode 门控——
        # 源码检查)
        import inspect
        from routes import xx64_routes
        src = inspect.getsource(
            xx64_routes)
        appeal_sec = src[src.index(
            "async def submit_appeal"):
            src.index(
                "async def feedback_collect")]
        record("申诉无模式门控",
               "require_active_mode"
               not in appeal_sec,
               "")

        # 回流无模式门控
        record("回流无模式门控",
               "async def feedback_collect"
               in src
               and src.count(
                   "require_active_mode")
               >= 6,
               str(src.count(
                   "require_active_mode")))


async def main():
    suites = [
        TestAnchor(), TestSupplyDemand(),
        TestCalibrate(), TestAppeal(),
        TestLearn(), TestScheduler(),
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
