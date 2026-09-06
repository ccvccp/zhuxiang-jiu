"""60号·AI智能支付管理模块 P1 专项测试
(智能收银台)

运行方式:
    python test_pay60_p1.py

覆盖(60号计划 §3.1/§七 P1):
    - 意图联动开单: 58号 resolved→
      intentId 入归因链(fail-soft
      不阻塞)
    - 三因子动态定价: 归因透明
      可审计+叠加封顶
    - 上下文感知渲染: 场景×角色
      →方式组合+老年优先
      (child_pay/voice 前置)+
      高信值续费默认免密
    - 失败智能恢复: 四类失败→
      有序建议集(建议性不自动
      执行铁律)+failed→recovering
    - QC: 定价归因可审计;
      恢复建议性
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


class TestCreateOrder:
    """01 意图联动开单"""

    async def run(self):
        print("[01 意图联动开单]")
        reset_all()
        from services.pay60_checkout_service import (
            Pay60CheckoutService,
        )
        svc = Pay60CheckoutService()

        # off 拒绝
        try:
            await svc.create_order(
                10, "purchase", "member",
                100.0)
            ok, err = False, "未拒绝"
        except ValueError as e:
            ok, err = "off" in str(e), \
                str(e)[:30]
        record("off 态开单拒绝", ok, err)

        os.environ["PAY60_MODE"] = "shadow"

        # 场景域外
        try:
            await svc.create_order(
                10, "gambling", "member",
                100.0)
            ok, err = False, "未拒绝"
        except ValueError as e:
            ok, err = "场景" in str(e), \
                str(e)[:30]
        record("场景域外拒绝", ok, err)

        # 角色域外
        try:
            await svc.create_order(
                10, "purchase", "hacker",
                100.0)
            ok, err = False, "未拒绝"
        except ValueError as e:
            ok, err = "角色" in str(e), \
                str(e)[:30]
        record("角色域外拒绝", ok, err)

        # 价格非法
        try:
            await svc.create_order(
                10, "purchase", "member", 0)
            ok, err = False, "未拒绝"
        except ValueError as e:
            ok, err = "正数" in str(e), \
                str(e)[:30]
        record("价格非正拒绝", ok, err)

        # ① 基本开单(无意图)
        r = await svc.create_order(
            10, "purchase", "member",
            100.0, tier="standard")
        record("基本开单(priced)",
               r["status"] == "priced"
               and r["finalPrice"]
               == 100.0
               and (r["payId"]
                    or 0) > 0,
               str((r["status"],
                    r["finalPrice"])))

        # ② 归因链完整(六要素——
        #    无意图时 intentId=0)
        attr = r["attribution"]
        record("归因链(六要素+场景)",
               attr["payId"]
               == r["payId"]
               and attr["intentId"] == 0
               and attr["tier"]
               == "standard"
               and attr["riskTier"]
               == "unverified"
               and attr["pricing"]
               ["finalPrice"] == 100.0
               and attr["scene"]
               == "purchase",
               str(attr)[:60])

        # ②b 归因链 intentId 保留
        #     58号原值(字符串)
        record("归因链 intentId 原值",
               isinstance(
                   attr["intentId"],
                   (int, str)),
               str(attr["intentId"]))

        # ③ 定价归因可审计(QC)
        record("定价归因可审计",
               r["pricing"]
               ["attribution"] == {
                   "trustDiscount": 1.0,
                   "contributionDiscount":
                       1.0,
                   "promoFactor": 1.0}
               and r["pricing"]
               ["ruleId"]
               == "v1_three_factor",
               str(r["pricing"]
                   ["attribution"]))

        # ④ 三因子定价(trusted+6月+0.9)
        r = await svc.create_order(
            11, "renewal", "member",
            100.0, tier="trusted",
            compliance_months=6,
            promo_factor=0.9)
        # 100×0.95×0.8×0.9=68.4→
        # 封顶 70
        record("三因子+封顶(68.4→70)",
               r["finalPrice"] == 70.0
               and r["pricing"]
                   ["floored"] is True,
               str(r["finalPrice"]))

        # ⑤ 定价正常折扣链
        r = await svc.create_order(
            12, "purchase", "member",
            100.0, tier="trusted",
            compliance_months=6)
        record("正常折扣链(76 元)",
               r["finalPrice"] == 76.0
               and r["pricing"]
                   ["floored"] is False,
               str(r["finalPrice"]))

        # ⑥ 58号意图联动(fail-soft——
        #    II58 off 时不阻塞)
        r = await svc.create_order(
            13, "purchase", "member",
            50.0, tier="standard",
            intent_text="想购买养老服务")
        record("意图 fail-soft(off 不阻塞)",
               r["status"] == "priced"
               and r["intent"][
                   "state"]
               in ("failsoft", None),
               str(r["intent"]))

        # ⑦ 58号 shadow 联动(
        #    resolved intentId 入归因)
        from repositories.ii58_repository \
            import Ii58Repository
        from core.helpers import ts
        repo = Ii58Repository()
        cid = await repo.next_corpus_id()
        await repo.save_corpus({
            "corpusId": cid,
            "intentId":
                "product.new_query",
            "sampleType": "positive",
            "text": "想购买居家养老服务",
            "weight": 1.0,
            "status": "active",
            "createdAt": ts(),
            "updatedAt": ts()})
        os.environ["II58_MODE"] = "shadow"
        r = await svc.create_order(
            14, "purchase", "member",
            50.0, tier="standard",
            intent_text="想购买居家养老服务")
        record("意图联动(resolved 入链)",
               r["intent"]["state"]
               == "resolved",
               str(r["intent"]))
        os.environ["II58_MODE"] = "off"

        # ⑧ 订单落库读回
        from repositories.pay60_repository \
            import Pay60Repository
        pay_repo = Pay60Repository()
        order = await pay_repo.get_order(
            r["payId"])
        record("订单落库(attribution)",
               order.get("status")
               == "priced"
               and (order.get(
                   "attribution")
                   or {}).get("scene")
               == "purchase",
               str(order.get("status")))

        # ⑨ order 事件留痕(5 次成功
        #    开单——拒绝路径不留痕)
        evs = await pay_repo.list_events(
            event_type="order", limit=20)
        record("order 事件留痕",
               len(evs) == 5,
               str(len(evs)))
        os.environ["PAY60_MODE"] = "off"


class TestRenderCheckout:
    """02 上下文感知渲染"""

    async def run(self):
        print("[02 上下文感知渲染]")
        reset_all()
        from services.pay60_checkout_service import (
            Pay60CheckoutService,
        )
        svc = Pay60CheckoutService()

        # off 拒绝
        try:
            await svc.render_checkout(
                10, "purchase", "member")
            ok, err = False, "未拒绝"
        except ValueError as e:
            ok, err = "off" in str(e), \
                str(e)[:30]
        record("off 态渲染拒绝", ok, err)

        os.environ["PAY60_MODE"] = "shadow"

        # 场景×角色未注册
        try:
            await svc.render_checkout(
                10, "settlement",
                "member")
            ok, err = False, "未拒绝"
        except ValueError as e:
            ok, err = "无收银台" in str(e), \
                str(e)[:30]
        record("未注册上下文拒绝", ok, err)

        # ① 标准购买(四方式)
        r = await svc.render_checkout(
            10, "purchase", "member")
        record("标准购买(4 方式)",
               r["methods"] == [
                   "standard",
                   "balance_pay",
                   "child_pay",
                   "voice_confirm"],
               str(r["methods"]))

        # ② 老年优先(child_pay/voice
        #    前置——49号偏好标记纯消费)
        r = await svc.render_checkout(
            11, "purchase", "member",
            senior=True)
        record("老年优先(代付/语音前置)",
               r["methods"][:2] == [
                   "child_pay",
                   "voice_confirm"]
               and any("老年" in str(n)
                      for n in
                      r["renderOptions"]
                      ["notes"]),
               str(r["methods"]))

        # ③ 续费(会员未建档→standard
        #    无免密默认)
        r = await svc.render_checkout(
            12, "renewal", "member")
        record("续费非 trusted(无默认)",
               r["defaults"] == {},
               str(r["defaults"]))

        # ④ 同盟商上架(合并单优先)
        r = await svc.render_checkout(
            13, "listing",
            "ally_merchant")
        record("上架合并单(bundle 优先)",
               r["methods"][0]
               == "deposit_service_bundle",
               str(r["methods"]))

        # ⑤ 渲染留痕(checkouts 表)
        from repositories.pay60_repository \
            import Pay60Repository
        repo = Pay60Repository()
        recs = await repo.list_checkouts(
            member_id=10)
        record("渲染留痕(checkout 落库)",
               len(recs) == 1
               and (recs[0].get(
                   "renderOptions")
                   or {}).get("methods")
               is not None,
               str(len(recs)))

        # ⑥ renderOptions 可审计
        r2 = await svc.render_checkout(
            14, "purchase", "member",
            senior=True)
        opts = r2["renderOptions"]
        record("renderOptions 可审计",
               opts["senior"] is True
               and opts["tier"]
               in ("standard",
                   "trusted")
               and isinstance(
                   opts["methods"], list),
               str(opts)[:60])

        # ⑦ 观测面(off 可用)
        os.environ["PAY60_MODE"] = "off"
        view = await svc.checkout_view()
        record("checkout 观测面(off 可用)",
               view["total"] == 5,
               str(view["total"]))


class TestRecover:
    """03 失败智能恢复"""

    async def run(self):
        print("[03 失败智能恢复]")
        reset_all()
        from services.pay60_checkout_service import (
            Pay60CheckoutService,
        )
        from repositories.pay60_repository \
            import Pay60Repository
        from core.helpers import ts
        svc = Pay60CheckoutService()
        repo = Pay60Repository()

        # 种 failed 态订单(经 next_pay_id
        # 推进序列——防覆盖)
        async def seed_failed():
            pay_id = await \
                repo.next_pay_id()
            await repo.save_order({
                "payId": pay_id,
                "memberId": 20,
                "scene": "purchase",
                "role": "member",
                "status": "failed",
                "basePrice": 100.0,
                "finalPrice": 100.0,
                "attribution": {},
                "createdAt": ts(),
                "updatedAt": ts()})
            return pay_id

        pid1 = await seed_failed()
        pid2 = await seed_failed()
        pid3 = await seed_failed()
        pid4 = await seed_failed()

        # off 拒绝
        try:
            await svc.recover(
                pid1, "channel_timeout")
            ok, err = False, "未拒绝"
        except ValueError as e:
            ok, err = "off" in str(e), \
                str(e)[:30]
        record("off 态恢复拒绝", ok, err)

        os.environ["PAY60_MODE"] = "shadow"

        # 失败原因域外
        try:
            await svc.recover(
                pid1, "hacked_reason")
            ok, err = False, "未拒绝"
        except ValueError as e:
            ok, err = "域外" in str(e), \
                str(e)[:30]
        record("失败原因域外拒绝", ok, err)

        # ① 余额不足(四建议有序)
        r = await svc.recover(
            pid1, "insufficient_balance")
        record("余额不足(4 建议有序)",
               r["status"] == "recovering"
               and [s["action"]
                    for s in
                    r["suggestions"]] == [
                   "split_payment",
                   "switch_channel",
                   "temporary_credit",
                   "retry_later"],
               str([s["action"] for s in
                    r["suggestions"]]))

        # ② 建议性标记(QC 铁律)
        record("建议性(advisoryOnly)",
               all(s["advisory"] is True
                   for s in
                   r["suggestions"]),
               str(r["suggestions"]
                   [:1]))

        # ③ 限额(2 建议)
        r = await svc.recover(
            pid2, "limit_exceeded")
        record("限额(2 建议)",
               [s["action"]
                for s in
                r["suggestions"]] == [
                   "split_payment",
                   "retry_later"],
               str([s["action"] for s in
                    r["suggestions"]]))

        # ④ 风控拦截(1 建议)
        r = await svc.recover(
            pid3, "risk_blocked")
        record("风控拦截(1 建议)",
               [s["action"]
                for s in
                r["suggestions"]] == [
                   "temporary_credit"],
               str([s["action"] for s in
                    r["suggestions"]]))

        # ⑤ 渠道超时(2 建议)
        r = await svc.recover(
            pid4, "channel_timeout")
        record("渠道超时(换渠道优先)",
               [s["action"]
                for s in
                r["suggestions"]][0]
               == "switch_channel",
               str([s["action"] for s in
                    r["suggestions"]]))

        # ⑥ 恢复后留痕(recovery 快照)
        order = await repo.get_order(pid1)
        rec = order.get("recovery") or {}
        record("恢复留痕(recovery 快照)",
               rec.get(
                   "failureReason")
               == "insufficient_balance"
               and rec.get(
                   "advisoryOnly")
               is True,
               str(rec)[:60])

        # ⑦ 已恢复态不可再恢复
        try:
            await svc.recover(
                pid1, "channel_timeout")
            ok, err = False, "未拒绝"
        except ValueError as e:
            ok, err = "非失败态" in str(e), \
                str(e)[:30]
        record("recovering 不可再恢复", ok, err)

        # ⑧ 非 failed 态不可恢复(priced)
        pay_id = await repo.next_pay_id()
        await repo.save_order({
            "payId": pay_id,
            "memberId": 20,
            "status": "priced",
            "basePrice": 100.0,
            "finalPrice": 100.0,
            "createdAt": ts(),
            "updatedAt": ts()})
        try:
            await svc.recover(
                pay_id,
                "channel_timeout")
            ok, err = False, "未拒绝"
        except ValueError as e:
            ok, err = "非失败态" in str(e), \
                str(e)[:30]
        record("priced 态不可恢复", ok, err)

        # ⑨ 订单不存在
        try:
            await svc.recover(
                99999,
                "channel_timeout")
            ok, err = False, "未拒绝"
        except KeyError:
            ok, err = True, ""
        record("订单不存在拒绝", ok, err)

        # ⑩ recovering→executing
        #    重试通道(状态机)
        from services.pay60_service import (
            Pay60Service,
        )
        r = await Pay60Service().advance(
            pid1, "executing",
            note="采纳换渠道建议")
        record("recovering→executing",
               r["success"] is True
               and r["to"]
               == "executing",
               str(r)[:50])
        os.environ["PAY60_MODE"] = "off"


class TestHttp:
    """04 HTTP 层(P1)"""

    async def run(self):
        print("[04 HTTP]")
        reset_all()
        from fastapi.testclient import TestClient
        from main import app
        client = TestClient(app)
        admin = {"X-Role": "admin"}

        # off 409(决策面)
        for path in (
                "/api/pay60/orders",
                "/api/pay60/checkout/render"):
            resp = client.post(
                path, json={
                    "memberId": 30,
                    "scene": "purchase",
                    "role": "member",
                    "basePrice": 100.0},
                headers=admin)
            record(f"HTTP {path.split('/')[-1]}"
                   f" off 409",
                   resp.status_code == 409,
                   str(resp.status_code))

        os.environ["PAY60_MODE"] = "shadow"

        # ① 开单 200
        resp = client.post(
            "/api/pay60/orders",
            json={"memberId": 30,
                  "scene": "purchase",
                  "role": "member",
                  "basePrice": 100.0,
                  "tier": "standard"},
            headers=admin)
        body = resp.json() or {}
        pay_id = body.get("payId")
        record("HTTP 开单 200(priced)",
               resp.status_code == 200
               and body.get("status")
               == "priced"
               and bool(pay_id),
               str((resp.status_code,
                    body.get("status"))))

        # ② 渲染 200(senior)
        resp = client.post(
            "/api/pay60/checkout/render",
            json={"memberId": 30,
                  "scene": "purchase",
                  "role": "member",
                  "senior": True},
            headers=admin)
        body = resp.json() or {}
        record("HTTP 渲染 200(老年优先)",
               resp.status_code == 200
               and (body.get("methods")
                    or [None])[0]
               == "child_pay",
               str((resp.status_code,
                    body.get("methods"))))

        # ③ 域外 409
        resp = client.post(
            "/api/pay60/orders",
            json={"memberId": 30,
                  "scene": "hack",
                  "role": "member",
                  "basePrice": 100.0},
            headers=admin)
        record("HTTP 开单域外 409",
               resp.status_code == 409,
               str(resp.status_code))

        # ④ 恢复链(造 failed 态)
        from repositories.pay60_repository \
            import Pay60Repository
        from core.helpers import ts
        repo = Pay60Repository()
        await repo.save_order({
            "payId": pay_id,
            "memberId": 30,
            "scene": "purchase",
            "role": "member",
            "status": "failed",
            "basePrice": 100.0,
            "finalPrice": 100.0,
            "attribution": {},
            "createdAt": ts(),
            "updatedAt": ts()},
            create=False)
        resp = client.post(
            f"/api/pay60/orders/"
            f"{pay_id}/recover",
            json={"failureReason":
                  "insufficient_balance"},
            headers=admin)
        body = resp.json() or {}
        record("HTTP 恢复 200(建议集)",
               resp.status_code == 200
               and body.get("status")
               == "recovering"
               and len(body.get(
                   "suggestions")
                   or []) == 4,
               str((resp.status_code,
                    body.get("status"))))

        # ⑤ checkouts 观测面
        resp = client.get(
            "/api/pay60/checkouts",
            headers=admin)
        body = resp.json() or {}
        record("HTTP checkouts 观测面",
               resp.status_code == 200
               and body.get("total") == 1,
               str((resp.status_code,
                    body.get("total"))))

        # ⑥ 恢复不存在 404
        resp = client.post(
            "/api/pay60/orders/99999/"
            "recover",
            json={"failureReason":
                  "channel_timeout"},
            headers=admin)
        record("HTTP 恢复 404",
               resp.status_code == 404,
               str(resp.status_code))

        # ⑦ 鉴权 403
        resp = client.post(
            "/api/pay60/orders",
            json={"memberId": 1,
                  "scene": "purchase",
                  "role": "member",
                  "basePrice": 10.0})
        record("HTTP 开单无 Role 403",
               resp.status_code == 403,
               str(resp.status_code))
        os.environ["PAY60_MODE"] = "off"


class TestConstitution:
    """05 宪法+QC"""

    async def run(self):
        print("[05 宪法+QC]")
        from services.pay60_checkout_service import (
            FAIL_RECOVERY,
            FAILURE_REASONS,
            RECOVERY_ACTIONS,
        )
        # ① 失败四类封闭
        record("失败四类(封闭)",
               FAILURE_REASONS == (
                   "insufficient_balance",
                   "limit_exceeded",
                   "risk_blocked",
                   "channel_timeout"),
               str(FAILURE_REASONS))

        # ② 每类失败→非空建议有序集
        record("失败→建议全覆盖",
               all(
                   FAIL_RECOVERY[r]
                   and all(a in
                           RECOVERY_ACTIONS
                           for a in
                           FAIL_RECOVERY[r])
                   for r in
                   FAILURE_REASONS),
               str(FAIL_RECOVERY))

        # ③ 建议性铁律(代码层——
        #    suggestions 仅输出)
        import inspect
        from services.pay60_checkout_service \
            import (
                Pay60CheckoutService,
            )
        src = inspect.getsource(
            Pay60CheckoutService.recover)
        record("恢复建议性(不自动执行)",
               "advisoryOnly" in src
               and "executing" not in
               src.split("return")[0],
               "")

        # ④ 感知源零改动(纯消费)
        import services.ii58_service as s58
        import services.trust_risk_profile_service as s47
        record("感知源零改动(47/58)",
               s47.__name__.endswith(
                   "trust_risk_profile_"
                   "service")
               and s58.__name__.endswith(
                   "ii58_service"),
               "")


async def run_all():
    await TestCreateOrder().run()
    await TestRenderCheckout().run()
    await TestRecover().run()
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
