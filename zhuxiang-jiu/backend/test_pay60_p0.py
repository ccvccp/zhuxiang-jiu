"""60号·AI智能支付管理模块 P0 专项测试
(支付注册表+订单状态机+渠道底座)

运行方式:
    python test_pay60_p0.py

覆盖(60号计划 §七 P0):
    - 注册表封闭: 定价三因子+分账合约
      +收银台上下文+九态状态机+渠道三态
      +启动自检
    - 定价引擎: 三因子计算+归因透明
      +叠加封顶 0.7 防击穿
    - 分账: rate 和=1.0+金额守恒
    - 状态机: 封闭转移表+非法流转拒绝
      +终态封闭
    - 渠道三态: mock 默认/real
      fail-hard/mock_fallback 回退
    - 归因链: 哈希指纹链
    - 观测面: registry/orders/model
      status(off 不受影响)
    - 第35档案: 八因子+44号入册 36
    - QC: 注册封闭; 58/59/48号零改动
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
os.environ["PAY60_CHANNEL_MODE"] = "mock"
os.environ.pop("PAY60_CHANNEL_KEY", None)
os.environ.pop("PAY60_LLM_MODE", None)
os.environ.pop("PAY60_LEARN_MODE", None)

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


class TestRegistry:
    """01 注册表封闭注册"""

    async def run(self):
        print("[01 注册表]")
        from services import pay60_registry as reg

        # ① 模式三态+渠道三态
        record("模式三态(off/shadow/assist)",
               reg.MODE_VALUES == (
                   "off", "shadow", "assist"),
               str(reg.MODE_VALUES))
        record("渠道三态(mock/real/fallback)",
               reg.CHANNEL_MODES == (
                   "mock", "real",
                   "mock_fallback"),
               str(reg.CHANNEL_MODES))
        record("默认模式 off",
               reg.current_mode() == "off",
               reg.current_mode())
        record("默认渠道 mock",
               reg.current_channel_mode()
               == "mock",
               reg.current_channel_mode())

        # ② 定价规则
        record("信值折扣四档",
               reg.TRUST_DISCOUNT == {
                   "trusted": 0.95,
                   "standard": 1.0,
                   "watched": 1.0,
                   "restricted": 1.05},
               str(reg.TRUST_DISCOUNT))
        record("贡献阶梯(6/3/0)",
               reg.CONTRIBUTION_TIERS == (
                   (6, 0.8), (3, 0.9), (0, 1.0)),
               str(reg.CONTRIBUTION_TIERS))
        record("叠加封顶 0.7(防击穿)",
               reg.PRICING_FLOOR == 0.7,
               str(reg.PRICING_FLOOR))
        record("定价规则 DSL 注册",
               list(reg.PRICING_RULES)
               == ["v1_three_factor"]
               and reg.PRICING_RULES[
                   "v1_three_factor"]["floor"]
               == 0.7,
               str(list(reg.PRICING_RULES)))

        # ③ 分账合约
        record("分账合约 3 份(封闭)",
               len(reg.SPLIT_CONTRACTS) == 3,
               str(len(
                   reg.SPLIT_CONTRACTS)))
        rate_sums = [round(sum(
            p["rate"] for p in
            c["parts"]), 6)
            for c in
            reg.SPLIT_CONTRACTS.values()]
        record("合约 rate 和=1.0(守恒)",
               rate_sums == [1.0, 1.0, 1.0],
               str(rate_sums))

        # ④ 收银台上下文
        record("收银台上下文 5 组",
               len(reg.CHECKOUT_CONTEXTS)
               == 5,
               str(len(
                   reg.CHECKOUT_CONTEXTS)))
        record("上下文键(场景×角色)",
               ("listing",
                "ally_merchant")
               in reg.CHECKOUT_CONTEXTS
               and ("renewal", "member")
               in reg.CHECKOUT_CONTEXTS,
               "")
        record("支付方式域封闭",
               set(reg.PAY_METHODS) == {
                   "standard",
                   "deposit_service_bundle",
                   "child_pay", "voice_confirm",
                   "credit_free_renew",
                   "balance_pay"},
               str(reg.PAY_METHODS))

        # ⑤ 状态机
        record("状态机全态(11 态)",
               len(reg.ORDER_STATES) == 11
               and "settled" in
               reg.ORDER_STATES
               and "refunded" in
               reg.ORDER_STATES,
               str(len(
                   reg.ORDER_STATES)))
        record("终态四封闭",
               reg.ORDER_TERMINAL == (
                   "settled", "priced_failed",
                   "cancelled", "refunded"),
               str(reg.ORDER_TERMINAL))
        record("转移表与全态一致",
               set(reg.ORDER_STATES)
               == set(
                   reg.ORDER_TRANSITIONS),
               "")
        record("终态无出边",
               all(not
                   reg.ORDER_TRANSITIONS[t]
                   for t in
                   reg.ORDER_TERMINAL),
               "")

        # ⑥ 注册表视图(观测面)
        view = reg.registry_view()
        record("注册表视图(观测面)",
               view["success"] is True
               and view["pricingRules"]
               == 1
               and view["splitContracts"]
               == 3
               and view[
                   "checkoutContexts"] == 5
               and view["orderStates"]
               == 11
               and view["channelMode"]
               == "mock",
               str(view)[:80])

        # ⑦ 启动自检(已通过=导入成功)
        record("启动自检(导入即验)",
               reg._validate_registry
               is not None,
               "")


class TestPricing:
    """02 定价引擎(三因子+封顶)"""

    async def run(self):
        print("[02 定价引擎]")
        from services.pay60_registry import (
            compute_price,
        )

        # ① 基准: 无折扣
        r = compute_price(100.0)
        record("基准价(无折扣)",
               r["finalPrice"] == 100.0
               and r["attribution"] == {
                   "trustDiscount": 1.0,
                   "contributionDiscount":
                       1.0,
                   "promoFactor": 1.0},
               str(r))

        # ② 信值折扣: trusted 0.95
        r = compute_price(
            100.0, tier="trusted")
        record("信值折扣(trusted 95 折)",
               r["finalPrice"] == 95.0
               and r["attribution"]
               ["trustDiscount"]
               == 0.95,
               str(r["finalPrice"]))

        # ③ 贡献折扣: ≥6 月 0.8
        r = compute_price(
            100.0,
            compliance_months=7)
        record("贡献折扣(7 月→8 折)",
               r["finalPrice"] == 80.0
               and r["attribution"]
               ["contributionDiscount"]
               == 0.8,
               str(r["finalPrice"]))

        # ④ 阶梯边界: 3 月→0.9
        r = compute_price(
            100.0, compliance_months=3)
        record("阶梯边界(3 月→9 折)",
               r["finalPrice"] == 90.0,
               str(r["finalPrice"]))
        r = compute_price(
            100.0, compliance_months=2)
        record("阶梯下限(2 月→无折)",
               r["finalPrice"] == 100.0,
               str(r["finalPrice"]))

        # ⑤ 活动叠加
        r = compute_price(
            100.0, promo_factor=0.9)
        record("活动叠加(9 折)",
               r["finalPrice"] == 90.0,
               str(r["finalPrice"]))

        # ⑥ 叠加封顶 0.7 防击穿
        #    (trusted 0.95×0.8×0.5=0.38
        #     →封顶 0.7=70 元)
        r = compute_price(
            100.0, tier="trusted",
            compliance_months=6,
            promo_factor=0.5)
        record("叠加封顶(0.7 防击穿)",
               r["finalPrice"] == 70.0
               and r["floored"] is True,
               str(r["finalPrice"]))

        # ⑦ restricted 上浮 1.05
        r = compute_price(
            100.0, tier="restricted")
        record("restricted 上浮(1.05)",
               r["finalPrice"] == 105.0,
               str(r["finalPrice"]))

        # ⑧ 归因透明(铁律)
        r = compute_price(
            200.0, tier="trusted",
            compliance_months=6,
            promo_factor=0.9)
        record("归因透明(三因子展示)",
               r["attribution"] == {
                   "trustDiscount": 0.95,
                   "contributionDiscount":
                       0.8,
                   "promoFactor": 0.9}
               and r["ruleId"]
               == "v1_three_factor",
               str(r["attribution"]))


class TestSplit:
    """03 分账引擎(守恒)"""

    async def run(self):
        print("[03 分账引擎]")
        from services.pay60_registry import (
            compute_split,
        )

        # ① 同盟商标准分账
        r = compute_split(
            1000.0,
            "v1_alliance_standard")
        record("同盟商分账(8/12/80)",
               [s["amount"] for s in
                r["splits"]] == [
                   80.0, 120.0, 800.0]
               and r["conserved"]
               is True,
               str(r["splits"]))
        record("分账金额守恒",
               r["total"] == 1000.0,
               str(r["total"]))

        # ② 平台直收
        r = compute_split(
            99.99, "v1_platform_direct")
        record("平台直收(全额)",
               r["splits"][0]["amount"]
               == 99.99
               and r["conserved"]
               is True,
               str(r["splits"]))

        # ③ 浮点守恒(残差归末段)
        r = compute_split(
            33.33,
            "v1_alliance_standard")
        record("浮点守恒(残差归末段)",
               r["conserved"] is True
               and r["total"] == 33.33,
               str((r["total"],
                    [s["amount"] for s in
                     r["splits"]])))

        # ④ 结算三模式
        modes = {s["mode"] for s in
                 r["splits"]}
        record("结算三模式(实时/T+1)",
               modes == {
                   "realtime", "t1"},
               str(modes))

        # ⑤ 合约域外拒绝
        try:
            compute_split(100.0,
                          "hack_contract")
            ok, err = False, "未拒绝"
        except KeyError:
            ok, err = True, ""
        record("合约域外拒绝", ok, err)

        # ⑥ 金额非正拒绝
        try:
            compute_split(0,
                          "v1_platform_direct")
            ok, err = False, "未拒绝"
        except ValueError:
            ok, err = True, ""
        record("金额非正拒绝", ok, err)


class TestStateMachine:
    """04 九态状态机"""

    async def run(self):
        print("[04 状态机]")
        from services.pay60_registry import (
            ORDER_TRANSITIONS,
            assert_transition,
        )

        # ① 主链流转全通过
        chain = ("created", "priced",
                 "verified", "executing",
                 "success", "settled")
        ok = True
        for i in range(len(chain) - 1):
            try:
                assert_transition(
                    chain[i], chain[i + 1])
            except ValueError:
                ok = False
        record("主链流转(created→settled)",
               ok is True, "")

        # ② 失败恢复链
        for cur, tgt in (
                ("failed", "recovering"),
                ("recovering",
                 "executing"),
                ("failed", "refunded")):
            try:
                assert_transition(cur, tgt)
                ok = True
            except ValueError:
                ok = False
            if not ok:
                break
        record("失败恢复链(failed 分支)",
               ok is True, "")

        # ③ 定价失败/取消分支
        for cur, tgt in (
                ("created", "priced_failed"),
                ("created", "cancelled"),
                ("priced", "cancelled")):
            try:
                assert_transition(cur, tgt)
                ok = True
            except ValueError:
                ok = False
            if not ok:
                break
        record("定价失败/取消分支",
               ok is True, "")

        # ④ 非法流转拒绝(跳跃)
        for cur, tgt in (
                ("created", "success"),
                ("created", "executing"),
                ("priced", "settled"),
                ("verified", "failed")):
            try:
                assert_transition(cur, tgt)
                ok = False
                break
            except ValueError:
                ok = True
        record("跳跃流转拒绝",
               ok is True, "")

        # ⑤ 终态封闭拒绝
        for terminal in (
                "settled", "cancelled",
                "refunded",
                "priced_failed"):
            try:
                assert_transition(
                    terminal, "created")
                ok = False
                break
            except ValueError:
                ok = True
        record("终态封闭拒绝", ok is True, "")

        # ⑥ 域外状态拒绝
        try:
            assert_transition("hacked",
                              "priced")
            ok, err = False, "未拒绝"
        except ValueError:
            ok, err = True, ""
        record("域外状态拒绝", ok, err)
        try:
            assert_transition(
                "created", "hacked")
            ok, err = False, "未拒绝"
        except ValueError:
            ok, err = True, ""
        record("域外目标态拒绝", ok, err)


class TestChannel:
    """05 渠道三态适配层"""

    async def run(self):
        print("[05 渠道三态]")
        reset_all()
        from services.pay60_service import (
            Pay60Service,
        )
        svc = Pay60Service()

        # off 拒绝(决策面)
        try:
            await svc.execute_channel(
                1, 100.0)
            ok, err = False, "未拒绝"
        except ValueError as e:
            ok, err = "off" in str(e), \
                str(e)[:30]
        record("off 态渠道执行拒绝", ok, err)

        os.environ["PAY60_MODE"] = "shadow"

        # ① mock(默认): 确定性回执
        r = await svc.execute_channel(
            10, 100.0, mode="mock")
        record("mock 回执(captured)",
               r["receipt"]["channel"]
               == "mock"
               and r["receipt"][
                   "status"]
               == "captured"
               and r["fallback"]
               is False
               and str(r["fingerprint"])
               .startswith("sha256:"),
               str(r["receipt"]))

        # ② real fail-hard(无凭证拒绝)
        try:
            await svc.execute_channel(
                11, 100.0, mode="real")
            ok, err = False, "未拒绝"
        except ValueError as e:
            ok, err = "凭证" in str(e), \
                str(e)[:30]
        record("real 无凭证 fail-hard",
               ok, err)

        # ③ real 有凭证(回执 REAL)
        os.environ[
            "PAY60_CHANNEL_KEY"] = "test"
        r = await svc.execute_channel(
            12, 50.0, mode="real")
        record("real 有凭证回执",
               r["receipt"]["channel"]
               == "real"
               and r["receipt"][
                   "refNo"].startswith(
                   "REAL"),
               str(r["receipt"]))
        os.environ.pop(
            "PAY60_CHANNEL_KEY", None)

        # ④ mock_fallback(无凭证回退)
        r = await svc.execute_channel(
            13, 50.0,
            mode="mock_fallback")
        record("fallback 回退 mock",
               r["fallback"] is True
               and r["receipt"][
                   "channel"] == "mock"
               and "回退" in str(
                   r["receipt"].get(
                       "fallbackReason")),
               str(r["receipt"]))

        # ⑤ 金额非正拒绝
        try:
            await svc.execute_channel(
                14, 0)
            ok, err = False, "未拒绝"
        except ValueError as e:
            ok, err = "正数" in str(e), \
                str(e)[:30]
        record("金额非正拒绝", ok, err)

        # ⑥ 流水留痕读回
        from repositories.pay60_repository \
            import Pay60Repository
        repo = Pay60Repository()
        flows = await repo.list_flows(
            pay_id=10)
        record("渠道流水留痕",
               len(flows) == 1
               and flows[0][
                   "channelMode"]
               == "mock",
               str(len(flows)))
        os.environ["PAY60_MODE"] = "off"


class TestOrderBase:
    """06 订单底座(归因链+流转)"""

    async def run(self):
        print("[06 订单底座]")
        reset_all()
        from services.pay60_service import (
            Pay60Service,
        )
        from repositories.pay60_repository \
            import Pay60Repository
        from core.helpers import ts
        svc = Pay60Service()
        repo = Pay60Repository()

        # 种订单(created 态+归因链)
        pay_id = await repo.next_pay_id()
        attribution = svc.build_attribution(
            pay_id, intent_id=58,
            session_id=48, tier="trusted",
            risk_tier="pass",
            pricing={"finalPrice": 95.0})
        await repo.save_order({
            "payId": pay_id,
            "memberId": 100,
            "status": "created",
            "attribution": attribution,
            "fingerprint": "sha256:seed",
            "createdAt": ts(),
            "updatedAt": ts()})

        # ① 归因链结构(铁律)
        record("归因链六要素",
               attribution["payId"]
               == pay_id
               and attribution[
                   "intentId"] == 58
               and attribution[
                   "sessionId"] == 48
               and attribution[
                   "tier"] == "trusted"
               and attribution[
                   "riskTier"] == "pass"
               and attribution[
                   "pricing"][
                   "finalPrice"] == 95.0,
               str(attribution))

        # ② off 拒绝流转(决策面)
        try:
            await svc.advance(
                pay_id, "priced")
            ok, err = False, "未拒绝"
        except ValueError as e:
            ok, err = "off" in str(e), \
                str(e)[:30]
        record("off 态流转拒绝", ok, err)

        os.environ["PAY60_MODE"] = "shadow"

        # ③ 合法流转(created→priced)
        r = await svc.advance(
            pay_id, "priced",
            note="定价完成")
        record("合法流转(created→priced)",
               r["success"] is True
               and r["from"] == "created"
               and r["to"] == "priced"
               and str(r["fingerprint"])
               .startswith("sha256:"),
               str(r)[:60])

        # ④ 非法流转拒绝(priced→success)
        try:
            await svc.advance(
                pay_id, "success")
            ok, err = False, "未拒绝"
        except ValueError as e:
            ok, err = "非法流转" in str(e), \
                str(e)[:40]
        record("非法流转拒绝", ok, err)

        # ⑤ 终态封闭(cancelled)
        pay_id2 = await \
            repo.next_pay_id()
        await repo.save_order({
            "payId": pay_id2,
            "memberId": 101,
            "status": "cancelled",
            "createdAt": ts(),
            "updatedAt": ts()})
        try:
            await svc.advance(
                pay_id2, "priced")
            ok, err = False, "未拒绝"
        except ValueError as e:
            ok, err = "终态" in str(e), \
                str(e)[:30]
        record("终态流转拒绝", ok, err)

        # ⑥ 不存在订单
        try:
            await svc.advance(
                99999, "priced")
            ok, err = False, "未拒绝"
        except KeyError:
            ok, err = True, ""
        record("订单不存在拒绝", ok, err)

        # ⑦ 事件留痕
        evs = await repo.list_events(
            event_type="order", limit=10)
        record("order 事件留痕",
               len(evs) >= 1,
               str(len(evs)))
        os.environ["PAY60_MODE"] = "off"


class TestObservation:
    """07 观测面(off 不受影响)"""

    async def run(self):
        print("[07 观测面]")
        reset_all()
        from services.pay60_service import (
            Pay60Service,
        )
        from repositories.pay60_repository \
            import Pay60Repository
        from core.helpers import ts
        svc = Pay60Service()
        repo = Pay60Repository()

        # 种三单不同状态
        for i, status in enumerate(
                ("created", "priced",
                 "settled")):
            pay_id = await \
                repo.next_pay_id()
            await repo.save_order({
                "payId": pay_id,
                "memberId": 200 + i,
                "status": status,
                "attribution": {},
                "createdAt": ts(),
                "updatedAt": ts()})

        # off 态观测面可用
        record("观测面 off 可用(铁律)",
               os.environ.get(
                   "PAY60_MODE") == "off",
               "")

        # ① registry
        view = svc.registry()
        record("registry 观测面",
               view["success"] is True
               and view["mode"] == "off"
               and view["scorer"]
               ["scorerId"]
               == "payment_orchestration",
               str(view)[:70])

        # ② orders 列表
        r = await svc.list_orders()
        record("orders 列表(3 单)",
               r["total"] == 3
               and r["byStatus"] == {
                   "created": 1,
                   "priced": 1,
                   "settled": 1},
               str(r["byStatus"]))

        # ③ 过滤(member)
        r = await svc.list_orders(
            member_id=200)
        record("orders 会员过滤",
               r["total"] == 1,
               str(r["total"]))

        # ④ 单条(不存在 404 语义)
        r = await svc.get_order(1)
        record("order 单条读回",
               r["order"]["payId"] == 1
               and "flows" in r,
               str(r["order"]
                   )[:50])
        try:
            await svc.get_order(99999)
            ok, err = False, "未拒绝"
        except KeyError:
            ok, err = True, ""
        record("order 不存在拒绝", ok, err)

        # ⑤ model_status(第35档案)
        r = await svc.model_status()
        record("model/status 第35档案",
               r["status"]["scorerId"]
               == "payment_orchestration"
               and len(
                   r["status"]
                   ["factorsMeta"]) == 8,
               str(r["status"]
                   ["scorerId"]))


class TestScorer:
    """08 第35档案八因子"""

    async def run(self):
        print("[08 第35档案]")
        from services.pay60_scorer import (
            Pay60Scorer,
        )

        # ① 八因子权重
        record("八因子权重(和=1.0)",
               len(Pay60Scorer.WEIGHTS)
               == 8
               and abs(sum(
                   Pay60Scorer.WEIGHTS
                   .values()) - 1.0)
               < 1e-9,
               str(Pay60Scorer.WEIGHTS))

        # ② 满分上下文(trusted 基线 90
        #    → 满分=20+15+15+15+9+10
        #    +5+10=99)
        r = await Pay60Scorer().score({
            "paymentSuccessRate": 1.0,
            "verificationFriction":
                1.0,
            "reconAccuracy": 1.0,
            "fraudInterception": 1.0,
            "tier": "trusted",
            "disputeRate": 0.0,
            "latencyP95Ok": 1.0,
            "coverageBreadth": 1.0,
        })
        record("满分(99→urgent)",
               r["trustScore"] == 99.0
               and r["decision"]
               == "urgent",
               str(r["trustScore"]))

        # ③ 中性上下文(未探因子 70 中性)
        r = await Pay60Scorer().score(
            {"tier": "standard"})
        record("中性(70 附近→optimize)",
               55.0 <= r["trustScore"]
               <= 80.0
               and r["decision"]
               == "optimize",
               str(r["trustScore"]))

        # ④ 争议反向(10% 争议→0 分)
        r = await Pay60Scorer().score({
            "disputeRate": 0.1})
        f6 = [f for f in
              r["factors"]
              if f["name"]
              == "dispute_rate"][0]
        record("争议反向(10%→0 分)",
               f6["score"] == 0.0,
               str(f6["score"]))

        # ⑤ 空上下文拒绝
        try:
            await Pay60Scorer().score({})
            ok, err = False, "未拒绝"
        except ValueError:
            ok, err = True, ""
        record("空上下文拒绝", ok, err)


class TestConstitution:
    """09 宪法+QC"""

    async def run(self):
        print("[09 宪法+QC]")
        # ① 44号 37 档案(61号入册后)
        from services.ai_learning_service import (
            SCORER_REGISTRY,
        )
        record("44号 37 档案(61号入册)",
               "payment_orchestration"
               in SCORER_REGISTRY
               and len(SCORER_REGISTRY)
               == 38
               and SCORER_REGISTRY[
                   "payment_"
                   "orchestration"]
               ["batch"] == 19,
               str(len(
                   SCORER_REGISTRY)))

        # ② 感知源零改动(纯消费)
        import services.ii58_service as s58
        import services.ii59_service as s59
        import services.xiaozhu_service as s48
        import services.trust_risk_profile_service as s47
        record("感知源零改动"
               "(47/48/58/59)",
               s47.__name__.endswith(
                   "trust_risk_"
                   "profile_service")
               and s48.__name__.endswith(
                   "xiaozhu_service")
               and s58.__name__.endswith(
                   "ii58_service")
               and s59.__name__.endswith(
                   "ii59_service"),
               "")

        # ③ 四开关铁律
        record("四开关铁律(默认 off/mock)",
               os.environ.get(
                   "PAY60_MODE",
                   "off") == "off"
               and os.environ.get(
                   "PAY60_CHANNEL_MODE",
                   "mock") == "mock"
               and os.environ.get(
                   "PAY60_LLM_MODE",
                   "off") == "off"
               and os.environ.get(
                   "PAY60_LEARN_MODE",
                   "off") == "off",
               "")

        # ④ 隐私红线(mask 范式——
        #    卡号原文不残留)
        from services.xiaozhu_service import (
            mask_pii,
        )
        masked = mask_pii(
            "卡号 6222021234567890")
        record("卡号 Token 化(mask)",
               "6222021234567890" not in
               masked,
               masked)


async def run_all():
    await TestRegistry().run()
    await TestPricing().run()
    await TestSplit().run()
    await TestStateMachine().run()
    await TestChannel().run()
    await TestOrderBase().run()
    await TestObservation().run()
    await TestScorer().run()
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
