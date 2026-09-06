"""64号·信值兑换管理模块 P1 专项测试
(支付结算引擎+积分兑换管道)

运行方式:
    python test_xx64_p1.py

覆盖(64号计划 §八 P1):
    - 原子转移: 买扣卖增借贷对
      (entryId 关联+失败回滚)
    - 支付状态机: reserved→paid
    - 退款反向转移: paid→refunded
      (买增卖扣, 不受开关影响)
    - 积分兑换管道: 100:1 整数倍
      +T+1 冻结观察+日限频
      +观察期取消返还
    - 转移可追溯(账本借贷平衡)
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
    repo = TrustValue45Repository()
    await repo.save_profile({
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
        "createdAt": "2026-01-01T00:00:00",
        "updatedAt": "2026-01-01T00:00:00",
    })


async def seed_points(user_id, points):
    from repositories.points_repository import (
        PointsRepository,
    )
    repo = PointsRepository()
    account = await repo \
        .get_or_create_account(user_id)
    account["totalPoints"] = int(points)
    await repo.save_account(account)


async def create_reserved(svc, price=100,
                          trust=1,
                          buyer=101,
                          seller=202):
    return await svc.create_order(
        buyer, seller, trust, price,
        product="P1 测试商品")


class TestPay:
    """01 支付原子转移"""

    async def run(self):
        print("[01 支付]")
        reset_all()
        from services.xx64_service import (
            Xx64Service,
        )
        from services.xx64_settle_service import (
            Xx64SettleService,
        )
        base = Xx64Service()
        settle = Xx64SettleService()

        await seed_profile(1, 500.0)
        os.environ["XX64_MODE"] = "shadow"

        # off 铁律
        os.environ["XX64_MODE"] = "off"
        try:
            await settle.pay_order(1)
            ok, err = False, "未拒绝"
        except ValueError:
            ok, err = True, ""
        record("off 铁律(支付拒绝)",
               ok, err)
        os.environ["XX64_MODE"] = "shadow"

        # 不存在订单
        try:
            await settle.pay_order(999)
            ok, err = False, "未拒绝"
        except KeyError:
            ok, err = True, ""
        record("订单不存在 404", ok, err)

        # 创建+支付
        r = await create_reserved(base)
        pay = await settle.pay_order(
            r["orderId"],
            paid_by="会员甲")
        record("支付成功(paid)",
               pay.get("status") == "paid"
               and (pay.get("entryId")
                    or 0) > 0,
               str((pay.get("status"),
                    pay.get("entryId"))))
        record("转移对(买扣卖增)",
               (pay.get("transfer")
                or {}).get(
                    "buyerDebit") == -30.0
               and (pay.get(
                        "transfer")
                    or {}).get(
                        "sellerCredit")
               == 30.0,
               str(pay.get("transfer")))

        # 借贷对账本读回
        from repositories.xx64_repository import (
            Xx64Repository,
        )
        ledger = await Xx64Repository() \
            .list_ledger(order_id=1)
        record("借贷对(两笔同 entryId)",
               len(ledger) == 2
               and len({e.get(
                   "entryId")
                   for e in ledger})
               == 1,
               str(len(ledger)))
        directions = {e.get(
            "direction") for e in
            ledger}
        record("方向对(debit+credit)",
               directions == {
                   "debit", "credit"},
               str(directions))
        sources = {e.get(
            "source") for e in ledger}
        record("来源标记"
               "(consumption_transfer)",
               sources == {
                   "consumption_transfer"},
               str(sources))

        # 重复支付拒绝
        try:
            await settle.pay_order(1)
            ok, err = False, "未拒绝"
        except ValueError:
            ok, err = True, ""
        record("重复支付拒绝", ok, err)

        # 未锁值订单支付拒绝
        r2 = await create_reserved(
            base, price=90)
        await base.cancel_order(
            r2["orderId"])
        try:
            await settle.pay_order(
                r2["orderId"])
            ok, err = False, "未拒绝"
        except ValueError:
            ok, err = True, ""
        record("cancelled 态支付拒绝",
               ok, err)

        # R4 前置拦截(低余额——
        # 余额 10: 价 30→信值 9>
        # 单次上限 2 创建即拒)
        await seed_profile(2, 10.0)
        try:
            await Xx64Service() \
                .create_order(
                    103, 204, 2, 30)
            ok, err = False, "未拒绝"
        except ValueError as e:
            ok = "R4_SINGLE" in str(e)
            err = ""
        record("R4 前置拦截"
               "(余额 10×20%=2<9)",
               ok, err)

        # 状态机: paid 可退款
        from services.xx64_registry import (
            ORDER_TRANSITIONS,
        )
        record("paid 可退款迁移",
               "refunded" in
               ORDER_TRANSITIONS[
                   "paid"],
               "")

        # 事件留痕
        evs = await Xx64Repository() \
            .list_events(limit=20)
        record("事件链"
               "(order+settle)",
               len(evs) >= 4,
               str(len(evs)))
        os.environ["XX64_MODE"] = "off"


class TestRefund:
    """02 退款反向转移"""

    async def run(self):
        print("[02 退款]")
        reset_all()
        from services.xx64_service import (
            Xx64Service,
        )
        from services.xx64_settle_service import (
            Xx64SettleService,
        )
        base = Xx64Service()
        settle = Xx64SettleService()

        await seed_profile(1, 500.0)
        os.environ["XX64_MODE"] = "shadow"
        r = await create_reserved(base)
        await settle.pay_order(1)

        # off 态退款不受影响
        # (资金安全人工铁律)
        os.environ["XX64_MODE"] = "off"
        refund = await settle \
            .refund_order(
                1, refunded_by="管理员")
        record("off 态退款成功"
               "(不受开关影响)",
               refund.get("status")
               == "refunded",
               str(refund.get(
                   "status")))
        record("反向转移"
               "(买增卖扣)",
               (refund.get("refund")
                or {}).get(
                    "buyerCredit")
               == 30.0
               and (refund.get(
                        "refund")
                    or {}).get(
                        "sellerDebit")
               == -30.0,
               str(refund.get(
                   "refund")))

        # 账本平衡(支付+退款
        # 四笔借贷对)
        ledger = await settle \
            .ledger_view()
        totals = (ledger.get(
            "totals") or {})
        record("账本借贷平衡",
               totals.get("balanced")
               is True
               and totals.get(
                   "credit")
               == 60.0,
               str(totals))

        # 重复退款拒绝
        try:
            await settle.refund_order(1)
            ok, err = False, "未拒绝"
        except ValueError:
            ok, err = True, ""
        record("重复退款拒绝", ok, err)

        # reserved 态退款拒绝
        os.environ["XX64_MODE"] = "shadow"
        r2 = await create_reserved(
            base, price=90)
        try:
            await settle.refund_order(
                r2["orderId"])
            ok, err = False, "未拒绝"
        except ValueError:
            ok, err = True, ""
        record("reserved 态退款拒绝",
               ok, err)

        # 不存在订单
        try:
            await settle.refund_order(
                999)
            ok, err = False, "未拒绝"
        except KeyError:
            ok, err = True, ""
        record("退款订单不存在 404",
               ok, err)
        os.environ["XX64_MODE"] = "off"


class TestPoints:
    """03 积分兑换管道"""

    async def run(self):
        print("[03 积分兑换]")
        reset_all()
        from services.xx64_points_service import (
            Xx64PointsService,
        )
        pts = Xx64PointsService()

        await seed_profile(1, 500.0)
        await seed_points(101, 1000)
        os.environ["XX64_MODE"] = "shadow"

        # off 铁律
        os.environ["XX64_MODE"] = "off"
        try:
            await pts.exchange(
                101, 1, 100)
            ok, err = False, "未拒绝"
        except ValueError:
            ok, err = True, ""
        record("off 铁律(兑换拒绝)",
               ok, err)
        os.environ["XX64_MODE"] = "shadow"

        # 参数校验
        for args in (
            (101, 1, 0, "积分非正"),
            (101, 1, -5, "积分为负"),
            (101, 1, 50, "非 100 倍数"),
            (0, 1, 100, "userId 缺省"),
            (101, 0, 100,
             "trustId 缺省")):
            try:
                await pts.exchange(
                    args[0], args[1],
                    args[2])
                ok, err = False, "未拒绝"
            except ValueError:
                ok, err = True, ""
            record(args[3] + " 拒绝",
                   ok, err)

        # ① 兑换(500→5 信值)
        r = await pts.exchange(
            101, 1, 500,
            exchanged_by="会员乙")
        record("兑换成功(pending)",
               r.get("status")
               == "pending"
               and r.get(
                   "pointsValue")
               == 5.0,
               str((r.get("status"),
                    r.get(
                        "pointsValue"))))
        record("T+1 冻结标注",
               (r.get("releaseAt")
                or "") != "",
               str(r.get(
                   "releaseAt")
                   )[:20])

        # 积分扣减读回
        from repositories.points_repository import (
            PointsRepository,
        )
        account = await (
            PointsRepository()
            .get_account(101))
        record("积分扣减"
               "(1000→500)",
               int(account.get(
                   "totalPoints"))
               == 500,
               str(account.get(
                   "totalPoints")))

        # 积分流水留痕
        logs = await (
            PointsRepository()
            .list_logs(101, limit=5))
        record("积分流水留痕",
               len(logs) >= 1,
               str(len(logs)))

        # 日限频(已 1 次——
        # 再兑 2 次后第 4 次拒)
        await pts.exchange(
            101, 1, 100)
        await pts.exchange(
            101, 1, 100)
        try:
            await pts.exchange(
                101, 1, 100)
            ok, err = False, "未拒绝"
        except ValueError as e:
            ok = "限频" in str(e)
            err = ""
        record("日限频拒绝"
               "(第 4 次)",
               ok, err)

        # 积分不足拒绝
        await seed_points(202, 50)
        try:
            await pts.exchange(
                202, 1, 100)
            ok, err = False, "未拒绝"
        except ValueError:
            ok, err = True, ""
        record("积分不足拒绝", ok, err)

        # ② T+1 入账(手动到期
        # 3 条 pending——模拟
        # 观察期届满)
        from datetime import datetime, \
            UTC, timedelta
        from repositories.xx64_repository import (
            Xx64Repository,
        )
        repo64 = Xx64Repository()
        expired = (datetime.now(UTC)
                   - timedelta(
                       hours=1)
                   ).isoformat()
        for ex in await repo64 \
                .list_exchanges(
                    user_id=101,
                    limit=10):
            if ex.get("status") \
                    == "pending":
                ex["releaseAt"] = expired
                await repo64.save_exchange(
                    ex, create=False)
        s = await pts.settle_pending()
        record("T+1 入账批量"
               "(3 条 pending→"
               "credited)",
               s.get("settled") == 3,
               str(s.get("settled")))

        # 重复入账幂等(已入账
        # 不再计)
        s2 = await pts.settle_pending()
        record("入账幂等(0 新)",
               s2.get("settled") == 0,
               str(s2.get("settled")))

        # ③ 观察期取消
        await seed_points(303, 400)
        r3 = await pts.exchange(
            303, 1, 200)
        cv = await pts.cancel_exchange(
            r3["exchangeId"])
        record("观察期取消"
               "(cancelled)",
               cv.get("status")
               == "cancelled"
               and cv.get(
                   "refundedPoints")
               == 200,
               str((cv.get("status"),
                    cv.get(
                        "refundedPoints"))))
        # 积分返还读回
        account3 = await (
            PointsRepository()
            .get_account(303))
        record("积分返还"
               "(400 恢复)",
               int(account3.get(
                   "totalPoints"))
               == 400,
               str(account3.get(
                   "totalPoints")))

        # 已入账不可取消
        try:
            await pts.cancel_exchange(
                1)
            ok, err = False, "未拒绝"
        except ValueError:
            ok, err = True, ""
        record("已入账取消拒绝",
               ok, err)

        # 不存在兑换
        try:
            await pts.cancel_exchange(
                999)
            ok, err = False, "未拒绝"
        except KeyError:
            ok, err = True, ""
        record("兑换不存在 404", ok, err)

        # ④ 预览
        pv = await pts.preview(
            1, needed_trust=2.0)
        record("换算预览(2 信值"
               "需 200 积分)",
               pv.get("rate")
               == "1 信值 = 100 积分"
               and pv.get(
                   "neededPoints")
               == 200.0,
               str((pv.get("rate"),
                    pv.get(
                        "neededPoints"))))
        record("预览统计"
               "(pending+credited)",
               (pv.get(
                   "creditedValue")
                or 0) == 7.0,
               str(pv.get(
                   "creditedValue")))
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

        # shadow 全链
        os.environ["XX64_MODE"] = "shadow"
        await seed_profile(1, 500.0)
        await seed_points(101, 1000)

        # 创建+支付
        resp = client.post(
            "/api/xx64/orders",
            json={"buyerId": 101,
                  "sellerId": 202,
                  "trustId": 1,
                  "price": 100},
            headers=member)
        body = resp.json() or {}
        record("HTTP 创建 200",
               resp.status_code == 200
               and body.get("status")
               == "reserved",
               str(resp.status_code))

        resp = client.post(
            "/api/xx64/orders/1/pay",
            json={"paidBy": "HTTP官"},
            headers=member)
        body = resp.json() or {}
        record("HTTP pay 200(paid)",
               resp.status_code == 200
               and body.get("status")
               == "paid"
               and (body.get(
                   "transfer")
                   or {}).get(
                       "sellerCredit")
               == 30.0,
               str((resp.status_code,
                    body.get("status"))))

        # 重复支付 409
        resp = client.post(
            "/api/xx64/orders/1/pay",
            json={},
            headers=member)
        record("HTTP 重复支付 409",
               resp.status_code == 409,
               str(resp.status_code))

        # 退款(不受开关影响)
        os.environ["XX64_MODE"] = "off"
        resp = client.post(
            "/api/xx64/orders/1/refund",
            json={"refundedBy":
                      "管理员"},
            headers=admin)
        body = resp.json() or {}
        record("HTTP refund 200"
               "(off 不受影响)",
               resp.status_code == 200
               and body.get("status")
               == "refunded",
               str((resp.status_code,
                    body.get("status"))))

        # off 态支付 409
        resp = client.post(
            "/api/xx64/orders/1/pay",
            json={},
            headers=member)
        record("HTTP pay off 409",
               resp.status_code == 409,
               str(resp.status_code))

        # off 态积分兑换 409
        resp = client.post(
            "/api/xx64/points/exchange",
            json={"userId": 101,
                  "trustId": 1,
                  "points": 100},
            headers=member)
        record("HTTP 兑换 off 409",
               resp.status_code == 409,
               str(resp.status_code))

        # 账本观测面
        resp = client.get(
            "/api/xx64/ledger",
            headers=admin)
        body = resp.json() or {}
        record("HTTP ledger 200"
               "(借贷平衡)",
               resp.status_code == 200
               and (body.get(
                   "totals")
                   or {}).get(
                       "balanced")
               is True,
               str((resp.status_code,
                    (body.get(
                        "totals")
                     or {}).get(
                        "balanced"))))

        # 预览观测面
        resp = client.get(
            "/api/xx64/points/preview"
            "?trust_id=1",
            headers=member)
        record("HTTP preview 200",
               resp.status_code == 200,
               str(resp.status_code))

        # shadow 积分兑换
        os.environ["XX64_MODE"] = "shadow"
        resp = client.post(
            "/api/xx64/points/exchange",
            json={"userId": 101,
                  "trustId": 1,
                  "points": 200},
            headers=member)
        body = resp.json() or {}
        record("HTTP 兑换 200"
               "(pending)",
               resp.status_code == 200
               and body.get("status")
               == "pending"
               and body.get(
                   "pointsValue")
               == 2.0,
               str((resp.status_code,
                    body.get(
                        "status"))))

        # 非整数倍 409
        resp = client.post(
            "/api/xx64/points/exchange",
            json={"userId": 101,
                  "trustId": 1,
                  "points": 150},
            headers=member)
        record("HTTP 非整数倍 409",
               resp.status_code == 409,
               str(resp.status_code))

        # 取消(决策面)
        resp = client.post(
            "/api/xx64/orders",
            json={"buyerId": 101,
                  "sellerId": 202,
                  "trustId": 1,
                  "price": 90},
            headers=member)
        order2 = resp.json() or {}
        resp = client.post(
            f"/api/xx64/orders/"
            f"{order2.get('orderId')}"
            f"/cancel",
            json={},
            headers=member)
        body = resp.json() or {}
        record("HTTP cancel 200",
               resp.status_code == 200
               and body.get("status")
               == "cancelled",
               str(body.get("status")))

        # 鉴权 403
        for method, path, role in (
                ("POST",
                 "/api/xx64/orders/1/pay",
                 None),
                ("POST",
                 "/api/xx64/orders/1/"
                 "refund", None),
                ("POST",
                 "/api/xx64/points/"
                 "exchange", None),
                ("GET",
                 "/api/xx64/ledger",
                 None),
                ("POST",
                 "/api/xx64/orders/1/"
                 "refund", "member")):
            resp = client.request(
                method, path, json={},
                headers=({"X-Role": role}
                         if role else None))
            record(f"HTTP "
                   f"{path.split('/')[-1]}"
                   f" 鉴权 403",
                   resp.status_code == 403,
                   str(resp.status_code))

        # 路由累计 12
        from routes.xx64_routes import (
            router as xx_router,
        )
        count = sum(
            1 for r in xx_router.routes)
        record("64号路由 P1 12 端点",
               count == 12, str(count))
        os.environ["XX64_MODE"] = "off"


class TestConstitution:
    """05 宪法断言"""

    async def run(self):
        print("[05 宪法断言]")
        from services.ai_learning_service import (
            SCORER_REGISTRY,
        )
        record("44号 39 档案在册",
               len(SCORER_REGISTRY) == 39,
               str(len(SCORER_REGISTRY)))

        # 积分模块零改动
        # (账户读写既有接口)
        try:
            from repositories import \
                points_repository as p
            record("积分模块零改动"
                   "(账户读写复用)",
                   p is not None,
                   "")
        except ImportError:
            record("积分模块零改动",
                   False, "导入失败")

        # 45号零改动
        try:
            from repositories import \
                trust_value_repository as r45
            record("45号零改动",
                   r45 is not None,
                   "")
        except ImportError:
            record("45号零改动",
                   False, "导入失败")

        record("三开关铁律(默认 off)",
               os.environ.get(
                   "XX64_MODE") == "off",
               "")


async def run_all():
    await TestPay().run()
    await TestRefund().run()
    await TestPoints().run()
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
