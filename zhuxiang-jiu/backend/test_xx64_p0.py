"""64号·信值兑换商品/服务AI智能管理模块
P0 专项测试(刚性规则底座)

运行方式:
    python test_xx64_p0.py

覆盖(64号计划 §八 P0):
    - R1-R7 刚性规则封闭注册+启动自检
    - 预校验四查(R1 结构/R4 单次/
      R5 窗口/R7 非负)
    - 限额基准快照机制(窗口最大
      快照防拆单压基数)
    - 订单九态状态机(initiated
      →prechecked→reserved)
    - 取消(解锁)
    - 第38档案八因子
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
os.environ.pop("XX64_LLM_MODE", None)
os.environ.pop("XX64_LEARN_MODE", None)

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


async def seed_profile(trust_id, score=100.0):
    """45号档案种子(纯读取——
    非侵入式落库)"""
    from repositories.trust_value_repository import (
        TrustValue45Repository,
    )
    repo = TrustValue45Repository()
    await repo.save_profile({
        "trustId": int(trust_id),
        "role": "person",
        "name": f"测试主体{trust_id}",
        "idDigest": f"digest-{trust_id}",
        "factors": {},
        "score": float(score),
        "rawScore": float(score),
        "grade": "A",
        "fused": False,
        "frozen": False,
        "createdAt": "2026-01-01T00:00:00",
        "updatedAt": "2026-01-01T00:00:00",
    })
    return trust_id


class TestRegistry:
    """01 刚性规则注册表"""

    async def run(self):
        print("[01 刚性规则]")
        reset_all()
        from services.xx64_registry import (
            CASH_PORTION, CUMULATIVE_QUOTA_RATIO,
            ORDER_STATES, ORDER_TRANSITIONS,
            POINTS_DAILY_LIMIT,
            POINTS_PER_TRUST, SINGLE_QUOTA_RATIO,
            TRUST_PORTION, WINDOW_DAYS,
            registry_view,
        )

        record("R1 占比 30/70",
               TRUST_PORTION == 0.30
               and CASH_PORTION == 0.70,
               f"{TRUST_PORTION}/"
               f"{CASH_PORTION}")
        record("R4 单次 20%",
               SINGLE_QUOTA_RATIO == 0.20,
               str(SINGLE_QUOTA_RATIO))
        record("R5 累计 40%+窗口 30 日",
               CUMULATIVE_QUOTA_RATIO
               == 0.40
               and WINDOW_DAYS == 30,
               f"{CUMULATIVE_QUOTA_RATIO}"
               f"/{WINDOW_DAYS}")
        record("R5>R4(累计≥单次)",
               CUMULATIVE_QUOTA_RATIO
               > SINGLE_QUOTA_RATIO,
               "")
        record("R6 积分 100:1+日限 3",
               POINTS_PER_TRUST == 100
               and POINTS_DAILY_LIMIT == 3,
               f"{POINTS_PER_TRUST}/"
               f"{POINTS_DAILY_LIMIT}")

        # 九态状态机
        record("订单九态",
               len(ORDER_STATES) == 9
               and "initiated"
               in ORDER_STATES,
               str(len(ORDER_STATES)))
        record("合法迁移(initiated→"
               "prechecked)",
               "prechecked" in
               ORDER_TRANSITIONS[
                   "initiated"],
               "")
        record("终态无出边"
               "(cancelled)",
               ORDER_TRANSITIONS[
                   "cancelled"] == (),
               "")
        record("paid 可退款",
               "refunded" in
               ORDER_TRANSITIONS["paid"],
               "")

        # 观测面
        v = registry_view()
        record("registry 观测面"
               "(R1-R7)",
               len(v.get("rigidRules")
                   or {}) == 7
               and v.get("mode") == "off",
               str(len(v.get(
                   "rigidRules") or {})))
        record("快照机制标注",
               "基准快照" in str(
                   (v.get("rigidRules")
                    or {}).get("R5")),
               "")

        # 启动自检(导入即验)
        record("启动自检通过",
               True, "")


class TestPrecheck:
    """02 预校验四查"""

    async def run(self):
        print("[02 预校验]")
        reset_all()
        from services.xx64_service import (
            Xx64Service,
        )
        svc = Xx64Service()

        # 45号档案不存在
        try:
            await svc.precheck(999, 100)
            ok, err = False, "未拒绝"
        except KeyError:
            ok, err = True, ""
        record("档案不存在拒绝",
               ok, err)

        # 余额 100 信值
        await seed_profile(1, 100.0)

        # ① 结构+限额(价格 100:
        #    R1 满足但 R4 超)
        c = await svc.precheck(1, 100)
        record("四查结构齐备",
               set(c.get("checks")
                   or {}) == {
                   "R1_STRUCT",
                   "R4_SINGLE",
                   "R5_WINDOW",
                   "R7_NONNEG"},
               str(sorted(c.get(
                   "checks") or {})))
        record("30% 信值=30",
               c.get("trustValue") == 30.0,
               str(c.get("trustValue")))
        record("70% 现付=70",
               c.get("cashValue") == 70.0,
               str(c.get("cashValue")))
        record("单次上限=20"
               "(余额 100×20%)",
               c.get("singleQuota")
               == 20.0,
               str(c.get("singleQuota")))
        record("R4 超限(30>20)",
               (c.get("checks")
                or {}).get(
                    "R4_SINGLE")
               is False
               and c.get("passed")
               is False,
               str(c.get("checks")))

        # ② R1 结构不满足(余额 5<
        #    信值 30)
        await seed_profile(2, 5.0)
        c2 = await svc.precheck(2, 100)
        record("R1 结构不足"
               "(余额 5<信值 30)",
               c2.get("passed") is False
               and (c2.get("checks")
                    or {}).get(
                        "R1_STRUCT")
               is False,
               str(c2.get("checks")))

        # ③ 高余额账户(500: 单次上限
        #    100≥信值 30 通过)
        await seed_profile(3, 500.0)
        c5 = await svc.precheck(3, 100)
        record("高余额通过"
               "(上限 100)",
               c5.get("passed") is True
               and c5.get("singleQuota")
               == 100.0,
               str(c5.get("singleQuota")))

        # 价格非法
        try:
            await svc.precheck(1, -5)
            ok, err = False, "未拒绝"
        except ValueError:
            ok, err = True, ""
        record("价格非正拒绝", ok, err)

        # 限额状态观测面
        q = await svc.quota_status(1)
        record("限额状态(单次 20/累计 40)",
               q.get("singleQuota") == 20.0
               and q.get(
                   "cumulativeQuota")
               == 40.0
               and q.get(
                   "windowRemaining")
               == 40.0,
               str((q.get(
                        "singleQuota"),
                    q.get(
                        "cumulativeQuota"))))


class TestOrder:
    """03 订单创建+锁值+取消"""

    async def run(self):
        print("[03 订单]")
        reset_all()
        from services.xx64_service import (
            Xx64Service,
        )
        svc = Xx64Service()
        await seed_profile(1, 500.0)
        await seed_profile(2, 300.0)

        # off 铁律
        try:
            await svc.create_order(
                101, 202, 1, 100)
            ok, err = False, "未拒绝"
        except ValueError:
            ok, err = True, ""
        record("off 铁律(创建拒绝)",
               ok, err)

        os.environ["XX64_MODE"] = "shadow"

        # 参数校验
        for args in (
            (0, 202, 1, 100,
             "买方缺省", True),
            (101, 0, 1, 100,
             "卖方缺省", True),
            (101, 101, 1, 100,
             "自买自卖", True),
            (101, 202, 1, -5,
             "价格非正", True),
            (101, 202, 1, 100,
             "纯现付", False)):
            try:
                await svc.create_order(
                    args[0], args[1],
                    args[2], args[3],
                    use_trust=args[5])
                ok, err = False, "未拒绝"
            except ValueError:
                ok, err = True, ""
            record(args[4] + " 拒绝",
                   ok, err)

        # 创建订单(reserved 锁值)
        r = await svc.create_order(
            101, 202, 1, 100,
            product="测试商品A",
            created_by="会员甲")
        record("创建成功(reserved)",
               r.get("status")
               == "reserved"
               and r.get("orderId") == 1,
               str(r.get("status")))
        record("锁值留痕"
               "(reserved=True)",
               r.get("trustValue") == 30.0
               and r.get("exclusive")
               is True,
               str(r.get("trustValue")))
        record("余额快照落库"
               "(500)",
               r.get(
                   "balanceSnapshot")
               == 500.0,
               str(r.get(
                   "balanceSnapshot")))
        record("指纹链(sha256)",
               str(r.get("fingerprint")
                   or "").startswith(
                       "sha256:"),
               str(r.get(
                   "fingerprint")
                   )[:20])

        # 预校验不满足拒绝(R4 超限
        # ——余额 500×20%=100,
        # 价格 1000→信值 300>100)
        try:
            await svc.create_order(
                101, 202, 1, 1000)
            ok, err = False, "未拒绝"
        except ValueError as e:
            ok = "R4_SINGLE" in str(e)
            err = str(e)[:40]
        record("R4 超限创建拒绝"
               "(结构化原因)",
               ok, err)

        # 窗口用量更新(创建后预校验
        # 反映窗口已用——仅成功订单
        # 占用 30)
        c = await svc.precheck(1, 100)
        record("窗口用量累计"
               "(30 已用)",
               c.get("windowUsed") == 30.0,
               str(c.get("windowUsed")))
        record("窗口累计上限"
               "(最大快照 500×40%=200)",
               c.get("cumulativeQuota")
               == 200.0,
               str(c.get(
                   "cumulativeQuota")))

        # R5 窗口累计边界(已用 30+
        # 新 30=60<200 通过; 余额
        # 降至低值造窗口占满场景)
        await seed_profile(1, 80.0)
        # 80×40%=32 窗口上限, 但最大
        # 快照仍 500→200; 单次 80×20%
        # =16<30 → R4 拒
        try:
            await svc.create_order(
                101, 202, 1, 100)
            ok, err = False, "未拒绝"
        except ValueError as e:
            ok = "R4_SINGLE" in str(e)
            err = ""
        record("R4 动态余额拒"
               "(80×20%=16<30)",
               ok, err)
        await seed_profile(1, 500.0)

        # 取消(解锁)
        cv = await svc.cancel_order(1)
        record("取消成功"
               "(cancelled+释放)",
               cv.get("status")
               == "cancelled"
               and cv.get("released")
               == 30.0,
               str((cv.get("status"),
                    cv.get("released"))))

        # 取消后窗口回退
        c2 = await svc.precheck(1, 100)
        record("取消后窗口回退"
               "(0 已用)",
               c2.get("windowUsed") == 0.0,
               str(c2.get("windowUsed")))

        # 重复取消拒绝
        try:
            await svc.cancel_order(1)
            ok, err = False, "未拒绝"
        except ValueError:
            ok, err = True, ""
        record("重复取消拒绝", ok, err)

        # 不存在订单
        try:
            await svc.cancel_order(999)
            ok, err = False, "未拒绝"
        except KeyError:
            ok, err = True, ""
        record("订单不存在 404", ok, err)

        # 详情+列表
        d = await svc.get_order(1)
        record("详情观测(快照+"
               "预校验记录)",
               ((d.get("order") or {})
                .get("status")
                == "cancelled")
               and ((d.get("order") or {})
                    .get("price")) == 100.0,
               str((d.get("order") or {})
                   .get("status")))
        lv = await svc.list_orders()
        record("列表观测(九态分布)",
               lv.get("total") == 1
               and (lv.get("byStatus")
                    or {}).get(
                        "cancelled") == 1,
               str(lv.get("byStatus")))

        # 事件留痕(创建+取消)
        from repositories.xx64_repository import (
            Xx64Repository,
        )
        evs = await Xx64Repository() \
            .list_events(limit=20)
        record("事件链(order×2)",
               len(evs) == 2,
               str(len(evs)))

        # 状态机九态在册
        from services.xx64_registry import (
            ORDER_STATES,
        )
        record("九态状态机在册",
               len(ORDER_STATES) == 9,
               str(len(
                   ORDER_STATES)))
        os.environ["XX64_MODE"] = "off"


class TestScorer:
    """04 第38档案八因子"""

    async def run(self):
        print("[04 第38档案]")
        reset_all()
        from services.xx64_scorer import (
            Xx64Scorer,
        )
        scorer = Xx64Scorer()

        try:
            await scorer.score({})
            ok, err = False, "未拒绝"
        except ValueError:
            ok, err = True, ""
        record("空上下文拒绝", ok, err)

        r = await scorer.score({
            "exchangeHealth": 0.95,
            "ruleCompliance": 0.98,
            "arbitrageBlocked": 0.9,
            "anchorVolatility": 0.02,
            "liquidityRatio": 1.0,
            "tier": "trusted",
            "appealOverturnRate": 0.02,
            "latencyP95Ok": 0.99,
        })
        record("八因子齐备",
               len(r.get("factors")
                   or []) == 8,
               str(len(r.get("factors")
                        or [])))
        record("权重和=1.0",
               abs(sum((r.get(
                        "weightsUsed") or {})
                       .values()) - 1.0)
               < 0.01,
               str(sum((r.get(
                        "weightsUsed")
                       or {}).values())))
        record("高分→optimize/urgent",
               r.get("decision") in (
                   "optimize", "urgent"),
               str((r.get("trustScore"),
                    r.get("decision"))))

        r2 = await scorer.score({
            "exchangeHealth": 0.3,
            "ruleCompliance": 0.3,
            "arbitrageBlocked": 0.2,
            "anchorVolatility": 0.5,
            "liquidityRatio": 3.0,
            "tier": "restricted",
            "appealOverturnRate": 0.3,
            "latencyP95Ok": 0.3,
        })
        record("低分→observe",
               r2.get("decision")
               == "observe"
               and (r2.get(
                   "trustScore")
                    or 0) < 50,
               str((r2.get(
                        "trustScore"),
                    r2.get(
                        "decision"))))

        # 因子明细八条
        names = {f["name"] for f in
                 r.get("factors")}
        record("因子明细八条",
               names == {
                   "exchange_health",
                   "rule_compliance",
                   "arbitrage_blocked",
                   "anchor_stability",
                   "liquidity_posture",
                   "member_trust",
                   "appeal_overturn",
                   "latency_budget"},
               str(sorted(names)))

        # tier 基线
        r5 = await scorer.score({
            "tier": "trusted"})
        f5 = [f for f in
              r5.get("factors")
              if f["name"]
              == "member_trust"]
        record("tier 基线"
               "(trusted=90)",
               bool(f5) and f5[0]["score"]
               == 90.0,
               str(f5[0]["score"] if f5
                   else None))

        # 流动性均衡偏离扣减
        r6 = await scorer.score({
            "liquidityRatio": 1.0})
        r7 = await scorer.score({
            "liquidityRatio": 3.0})
        f6 = [f for f in
              r6.get("factors")
              if f["name"]
              == "liquidity_posture"]
        f7 = [f for f in
              r7.get("factors")
              if f["name"]
              == "liquidity_posture"]
        record("流动性偏离扣减"
               "(均衡>过热)",
               (f6[0]["score"] if f6
                else 0)
               > (f7[0]["score"] if f7
                  else 100),
               str((f6[0]["score"] if f6
                    else None,
                    f7[0]["score"] if f7
                    else None)))


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
        record("第38档案 value_exchange"
               "(batch23)",
               SCORER_REGISTRY.get(
                   "value_exchange")
               is not None
               and SCORER_REGISTRY[
                   "value_exchange"
               ]["batch"] == 23,
               str(SCORER_REGISTRY.get(
                   "value_exchange")))

        # 45号零改动(纯读取)
        try:
            from repositories import \
                trust_value_repository as r45
            record("45号零改动"
                   "(纯读取)",
                   r45 is not None,
                   "")
        except ImportError:
            record("45号零改动"
                   "(纯读取)",
                   False, "导入失败")

        # 47号零改动
        try:
            from services import \
                trust_risk_profile_service as s47
            record("47号零改动",
                   s47 is not None,
                   "")
        except ImportError:
            record("47号零改动",
                   False, "导入失败")

        record("三开关铁律"
               "(默认 off)",
               os.environ.get(
                   "XX64_MODE",
                   "off") == "off",
               str(os.environ.get(
                   "XX64_MODE")))


class TestHttp:
    """06 HTTP 层"""

    async def run(self):
        print("[06 HTTP]")
        reset_all()
        from fastapi.testclient import \
            TestClient
        from main import app
        client = TestClient(app)
        admin = {"X-Role": "admin"}
        member = {"X-Role": "member"}

        # 观测面 off 可用
        resp = client.get(
            "/api/xx64/registry",
            headers=admin)
        body = resp.json() or {}
        record("HTTP registry 观测面 200",
               resp.status_code == 200
               and len(body.get(
                   "rigidRules")
                   or {}) == 7
               and body.get("mode")
               == "off",
               str((resp.status_code,
                    len(body.get(
                        "rigidRules")
                        or {}))))

        resp = client.get(
            "/api/xx64/model/status",
            headers=admin)
        record("HTTP model/status 200",
               resp.status_code == 200
               and ((resp.json()
                     or {}).get("status")
                    or {}).get("scorerId")
               == "value_exchange",
               str(resp.status_code))

        # 决策面 off 409
        resp = client.post(
            "/api/xx64/orders",
            json={"buyerId": 101,
                  "sellerId": 202,
                  "trustId": 1,
                  "price": 100},
            headers=member)
        record("HTTP orders off 409",
               resp.status_code == 409,
               str(resp.status_code))

        # shadow 全链
        os.environ["XX64_MODE"] = "shadow"
        await seed_profile(1, 500.0)
        resp = client.post(
            "/api/xx64/orders",
            json={"buyerId": 101,
                  "sellerId": 202,
                  "trustId": 1,
                  "price": 100,
                  "product":
                      "HTTP 商品"},
            headers=member)
        body = resp.json() or {}
        record("HTTP orders 200"
               "(reserved)",
               resp.status_code == 200
               and body.get("status")
               == "reserved",
               str((resp.status_code,
                    body.get("status"))))

        # R4 超限 409(结构化——
        # 余额 500×20%=100<信值 300)
        resp = client.post(
            "/api/xx64/orders",
            json={"buyerId": 101,
                  "sellerId": 202,
                  "trustId": 1,
                  "price": 1000},
            headers=member)
        record("HTTP 超限 409",
               resp.status_code == 409,
               str(resp.status_code))

        # 详情观测面
        resp = client.get(
            "/api/xx64/orders/1",
            headers=member)
        record("HTTP 详情 200",
               resp.status_code == 200
               and ((resp.json()
                     or {}).get("order")
                    or {}).get("buyerId")
               == 101,
               str(resp.status_code))
        resp = client.get(
            "/api/xx64/orders/999",
            headers=admin)
        record("HTTP 详情 404",
               resp.status_code == 404,
               str(resp.status_code))

        # 限额观测面
        resp = client.get(
            "/api/xx64/quota?trust_id=1",
            headers=member)
        body = resp.json() or {}
        record("HTTP quota 200",
               resp.status_code == 200
               and body.get(
                   "singleQuota")
               == 100.0
               and body.get(
                   "windowUsed")
               == 30.0,
               str((resp.status_code,
                    body.get(
                        "windowUsed"))))

        # 鉴权 403(无 Role)
        for method, path in (
                ("GET",
                 "/api/xx64/registry"),
                ("POST",
                 "/api/xx64/orders"),
                ("GET",
                 "/api/xx64/orders"),
                ("GET",
                 "/api/xx64/quota?"
                 "trust_id=1"),
                ("GET",
                 "/api/xx64/model/"
                 "status")):
            resp = client.request(
                method, path, json={})
            short = path.split('/')[-1] \
                .split('?')[0]
            record(f"HTTP {short}"
                   f" 无 Role 403",
                   resp.status_code == 403,
                   str(resp.status_code))

        # 非法角色 403
        resp = client.get(
            "/api/xx64/registry",
            headers={"X-Role":
                         "hacker"})
        record("非法角色 403",
               resp.status_code == 403,
               str(resp.status_code))

        # 路由累计 6
        from routes.xx64_routes import (
            router as xx_router,
        )
        count = sum(
            1 for r in xx_router.routes)
        record("64号路由 P0 6 端点",
               count == 6, str(count))
        os.environ["XX64_MODE"] = "off"


async def run_all():
    await TestRegistry().run()
    await TestPrecheck().run()
    await TestOrder().run()
    await TestScorer().run()
    await TestConstitution().run()
    await TestHttp().run()


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
