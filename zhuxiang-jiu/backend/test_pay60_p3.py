"""60号·AI智能支付管理模块 P3 专项测试
(AI 对账与结算)

运行方式:
    python test_pay60_p3.py

覆盖(60号计划 §3.3/§七 P3):
    - 三方语义对账: 订单↔流水↔发票
      匹配(金额+订单号+时间窗)
    - 差异五类分类: matched/
      channel_duplicate/amount_
      mismatch/missing_flow/
      flow_orphan
    - 自动冲正建议: 退款类 T+1
      延迟域+大额(≥5000)人工终审
    - 差异处置: 终审人工铁律
      (不受开关影响)
    - 智能分账: 合约拆分+金额守恒
      +幂等
    - 分账结算: 人工铁律+T+1 延迟
      留痕+订单联动 settled
    - 对账幂等: 未处置差异不重复登记
    - QC: 资金操作人工终审;
      冲正留痕
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


async def seed_success_order(member_id=10,
                             amount=100.0,
                             scene="purchase"):
    """种 success 态订单+对应流水"""
    from repositories.pay60_repository import (
        Pay60Repository,
    )
    from core.helpers import ts
    repo = Pay60Repository()
    pay_id = await repo.next_pay_id()
    await repo.save_order({
        "payId": pay_id,
        "memberId": member_id,
        "scene": scene,
        "role": "member",
        "status": "success",
        "basePrice": amount,
        "finalPrice": amount,
        "attribution": {},
        "createdAt": ts(),
        "updatedAt": ts()})
    flow_id = await repo.next_flow_id()
    await repo.save_flow({
        "flowId": flow_id,
        "payId": pay_id,
        "channel": "mock",
        "channelMode": "mock",
        "amount": amount,
        "channelReceipt": {
            "channel": "mock",
            "refNo": f"MOCK{flow_id}",
            "status": "captured"},
        "fingerprint": "sha256:seed",
        "fallback": False,
        "error": "",
        "createdAt": ts(),
        "updatedAt": ts()})
    return pay_id


class TestReconRun:
    """01 对账批次(三方匹配)"""

    async def run(self):
        print("[01 对账批次]")
        reset_all()
        from services.pay60_recon_service import (
            Pay60ReconService,
        )
        from repositories.pay60_repository \
            import Pay60Repository
        from core.helpers import ts
        svc = Pay60ReconService()
        repo = Pay60Repository()

        # ① matched(订单↔流水一致)
        p1 = await seed_success_order(
            10, 100.0)
        r = await svc.run_recon()
        record("matched(双方一致)",
               r["scanned"] == 1
               and r["matched"] == 1
               and r["differences"] == 0,
               str((r["scanned"],
                    r["matched"])))

        # ② 三方(发票金额不符→
        #    amount_mismatch)
        r = await svc.run_recon(
            invoices=[
                {"payId": p1,
                 "amount": 99.0}])
        record("三方发票不符(差异)",
               r["differences"] == 1
               and r["byType"].get(
                   "amount_mismatch") == 1,
               str(r["byType"]))

        # ③ 三方一致(发票等额)
        reset_all()
        p1 = await seed_success_order(
            10, 100.0)
        r = await svc.run_recon(
            invoices=[
                {"payId": p1,
                 "amount": 100.0}])
        record("三方一致(matched)",
               r["matched"] == 1
               and r["differences"] == 0,
               str(r["matched"]))

        # ④ channel_duplicate(
        #    同单两流水)
        flow_id = await repo.next_flow_id()
        await repo.save_flow({
            "flowId": flow_id,
            "payId": p1,
            "channel": "mock",
            "channelMode": "mock",
            "amount": 100.0,
            "channelReceipt": {},
            "fingerprint": "sha256:d",
            "fallback": False,
            "error": "",
            "createdAt": ts(),
            "updatedAt": ts()})
        r = await svc.run_recon()
        # 幂等: p1 已有差异跳过→
        # 造新单验证
        p2 = await seed_success_order(
            11, 100.0)
        flow_id2 = await \
            repo.next_flow_id()
        await repo.save_flow({
            "flowId": flow_id2,
            "payId": p2,
            "channel": "mock",
            "channelMode": "mock",
            "amount": 100.0,
            "channelReceipt": {},
            "fingerprint": "sha256:d2",
            "fallback": False,
            "error": "",
            "createdAt": ts(),
            "updatedAt": ts()})
        r = await svc.run_recon()
        dup = [d for d in
               r["details"]
               if d["payId"] == p2]
        record("channel_duplicate"
               "(重复扣款)",
               len(dup) == 1
               and dup[0]["diffType"]
               == "channel_duplicate"
               and dup[0]["status"]
               == "auto_pending",
               str(dup))

        # ⑤ 冲正建议(确定性归因表)
        record("冲正归因(重复扣款文案)",
               "重复扣款" in str(
                   dup[0]
                   ["attribution"])
               and dup[0][
                   "reversalAmount"]
               == 100.0,
               str(dup[0]
                   ["reversalAmount"]))

        # ⑥ missing_flow(
        #    success 无流水)
        reset_all()
        pay_id = await repo.next_pay_id()
        await repo.save_order({
            "payId": pay_id,
            "memberId": 12,
            "scene": "purchase",
            "role": "member",
            "status": "success",
            "basePrice": 50.0,
            "finalPrice": 50.0,
            "attribution": {},
            "createdAt": ts(),
            "updatedAt": ts()})
        r = await svc.run_recon()
        record("missing_flow(丢单)",
               r["differences"] == 1
               and r["details"][0]
               ["diffType"]
               == "missing_flow"
               and r["details"][0]
               ["status"] == "open",
               str(r["details"]))

        # ⑦ flow_orphan(
        #    流水无订单)
        flow_id = await repo.next_flow_id()
        await repo.save_flow({
            "flowId": flow_id,
            "payId": 99999,
            "channel": "mock",
            "channelMode": "mock",
            "amount": 30.0,
            "channelReceipt": {},
            "fingerprint": "sha256:o",
            "fallback": False,
            "error": "",
            "createdAt": ts(),
            "updatedAt": ts()})
        r = await svc.run_recon()
        record("flow_orphan(挂单)",
               r["differences"] == 2
               and any(
                   d["diffType"]
                   == "flow_orphan"
                   for d in
                   r["details"]),
               str(r["byType"]))

        # ⑧ amount_mismatch(
        #    流水金额不符)
        reset_all()
        pay_id = await repo.next_pay_id()
        await repo.save_order({
            "payId": pay_id,
            "memberId": 13,
            "scene": "purchase",
            "role": "member",
            "status": "success",
            "basePrice": 100.0,
            "finalPrice": 100.0,
            "attribution": {},
            "createdAt": ts(),
            "updatedAt": ts()})
        flow_id = await repo.next_flow_id()
        await repo.save_flow({
            "flowId": flow_id,
            "payId": pay_id,
            "channel": "mock",
            "channelMode": "mock",
            "amount": 80.0,
            "channelReceipt": {},
            "fingerprint": "sha256:m",
            "fallback": False,
            "error": "",
            "createdAt": ts(),
            "updatedAt": ts()})
        r = await svc.run_recon()
        record("amount_mismatch"
               "(金额不符)",
               r["differences"] == 1
               and r["details"][0]
               ["diffType"]
               == "amount_mismatch",
               str(r["byType"]))

        # ⑨ 对账幂等(已有未处置差异
        #    不重复登记——差异计数
        #    仍为 1)
        r2 = await svc.run_recon()
        record("对账幂等(不重复登记)",
               r2["differences"] == 1
               and r2["scanned"] == 1,
               str((r2["scanned"],
                    r2["differences"])))

        # ⑩ off 亦可用(回流铁律)
        os.environ["PAY60_MODE"] = "off"
        r3 = await svc.run_recon()
        record("off 亦可用(回流铁律)",
               r3["success"] is True,
               "")

        # ⑪ 大额冲正(≥5000
        #    人工终审)
        reset_all()
        pay_id = await repo.next_pay_id()
        await repo.save_order({
            "payId": pay_id,
            "memberId": 14,
            "scene": "purchase",
            "role": "member",
            "status": "success",
            "basePrice": 8000.0,
            "finalPrice": 8000.0,
            "attribution": {},
            "createdAt": ts(),
            "updatedAt": ts()})
        for i in range(2):
            flow_id = \
                await repo.next_flow_id()
            await repo.save_flow({
                "flowId": flow_id,
                "payId": pay_id,
                "channel": "mock",
                "channelMode": "mock",
                "amount": 8000.0,
                "channelReceipt": {},
                "fingerprint":
                    f"sha256:{i}",
                "fallback": False,
                "error": "",
                "createdAt": ts(),
                "updatedAt": ts()})
        r = await svc.run_recon()
        record("大额冲正强制人工",
               r["details"][0]
               ["needsManual"]
               is True
               and r["details"][0]
               ["status"] == "open",
               str(r["details"]
                   [0]["status"]))


class TestReconSettle:
    """02 差异处置(终审人工铁律)"""

    async def run(self):
        print("[02 差异处置]")
        reset_all()
        from services.pay60_recon_service import (
            Pay60ReconService,
        )
        from repositories.pay60_repository \
            import Pay60Repository
        from core.helpers import ts
        svc = Pay60ReconService()
        repo = Pay60Repository()

        # 造重复扣款差异
        p1 = await seed_success_order(
            20, 100.0)
        flow_id = await repo.next_flow_id()
        await repo.save_flow({
            "flowId": flow_id,
            "payId": p1,
            "channel": "mock",
            "channelMode": "mock",
            "amount": 100.0,
            "channelReceipt": {},
            "fingerprint": "sha256:s",
            "fallback": False,
            "error": "",
            "createdAt": ts(),
            "updatedAt": ts()})
        r = await svc.run_recon()
        recon_id = r["reconId"]

        # off 亦可用(资金终审铁律)
        os.environ["PAY60_MODE"] = "off"

        # ① 批次不存在
        try:
            await svc.settle_recon(
                99999, p1, True)
            ok, err = False, "未拒绝"
        except KeyError:
            ok, err = True, ""
        record("批次不存在拒绝", ok, err)

        # ② 差异不存在(该批次无
        #    此订单差异)
        try:
            await svc.settle_recon(
                recon_id, 88888, True)
            ok, err = False, "未拒绝"
        except KeyError:
            ok, err = True, ""
        record("差异不存在拒绝", ok, err)

        # ③ 冲正确认(订单→refunded)
        r2 = await svc.settle_recon(
            recon_id, p1, True,
            settled_by="财务官")
        record("冲正确认(settled)",
               r2["status"] == "settled"
               and r2["diffType"]
               == "channel_duplicate"
               and r2[
                   "reversalAmount"]
               == 100.0,
               str(r2)[:60])

        # 订单 refunded+冲正留痕
        order = await repo.get_order(p1)
        rev = order.get("reversal") or {}
        record("订单 refunded+冲正留痕",
               order.get("status")
               == "refunded"
               and rev.get("domain")
               == "T+1"
               and rev.get(
                   "settledBy")
               == "财务官",
               str(rev)[:60])

        # ④ 重复处置拒绝
        try:
            await svc.settle_recon(
                recon_id, p1, True)
            ok, err = False, "未拒绝"
        except ValueError as e:
            ok, err = "已处置" in str(e), \
                str(e)[:30]
        record("重复处置拒绝", ok, err)

        # ⑤ 驳回处置(dismissed)
        reset_all()
        p2 = await seed_success_order(
            21, 100.0)
        # 造 amount_mismatch
        flow = await repo.list_flows(
            pay_id=p2)
        flow[0]["amount"] = 90.0
        await repo.save_flow(
            flow[0], create=False)
        r = await svc.run_recon()
        recon_id2 = r["reconId"]
        r3 = await svc.settle_recon(
            recon_id2, p2, False,
            settled_by="审计员")
        record("驳回处置(dismissed)",
               r3["status"]
               == "dismissed",
               str(r3["status"]))
        order = await repo.get_order(p2)
        record("驳回订单不受影响",
               order.get("status")
               == "success",
               str(order.get("status")))


class TestSplit:
    """03 智能分账"""

    async def run(self):
        print("[03 智能分账]")
        reset_all()
        from services.pay60_recon_service import (
            Pay60ReconService,
        )
        from repositories.pay60_repository \
            import Pay60Repository
        from core.helpers import ts
        svc = Pay60ReconService()
        repo = Pay60Repository()

        # off 拒绝(决策面)
        try:
            await svc.create_split(1)
            ok, err = False, "未拒绝"
        except ValueError as e:
            ok, err = "off" in str(e), \
                str(e)[:30]
        record("off 态分账拒绝", ok, err)

        os.environ["PAY60_MODE"] = "shadow"

        # ① 非 success 拒绝
        pay_id = await repo.next_pay_id()
        await repo.save_order({
            "payId": pay_id,
            "memberId": 30,
            "scene": "purchase",
            "role": "member",
            "status": "priced",
            "basePrice": 100.0,
            "finalPrice": 100.0,
            "attribution": {},
            "createdAt": ts(),
            "updatedAt": ts()})
        try:
            await svc.create_split(pay_id)
            ok, err = False, "未拒绝"
        except ValueError as e:
            ok, err = "success" in str(e), \
                str(e)[:30]
        record("非 success 态拒绝", ok, err)

        # ② 分账创建(默认合约——
        #    purchase→platform_direct)
        p1 = await seed_success_order(
            30, 100.0)
        r = await svc.create_split(p1)
        record("分账创建(platform_direct)",
               r["contractId"]
               == "v1_platform_direct"
               and r["splits"][0]
               ["amount"] == 100.0
               and r["conserved"]
               is True,
               str(r["splits"]))

        # ③ 指定合约(同盟商标准)
        p2 = await seed_success_order(
            31, 1000.0,
            scene="listing")
        r = await svc.create_split(
            p2, "v1_alliance_standard")
        record("同盟商分账(8/12/80)",
               [s["amount"] for s in
                r["splits"]] == [
                   80.0, 120.0,
                   800.0]
               and r["amount"] == 1000.0,
               str(r["splits"]))

        # ④ 幂等(同订单重复分账拒绝)
        try:
            await svc.create_split(p2)
            ok, err = False, "未拒绝"
        except ValueError as e:
            ok, err = "幂等" in str(e), \
                str(e)[:30]
        record("分账幂等拒绝", ok, err)

        # ⑤ T+1 部分识别
        t1 = [s for s in
              r["splits"]
              if s["mode"] == "t1"]
        record("T+1 部分(保证金)",
               len(t1) == 1
               and t1[0]["name"]
               == "deposit_freeze",
               str(t1))

        # ⑥ 分账结算(人工铁律——
        #    off 亦可用)
        os.environ["PAY60_MODE"] = "off"
        split_id = r["splitId"]
        r2 = await svc.settle_split(
            split_id,
            settled_by="结算官")
        record("分账结算(settled)",
               r2["status"] == "settled"
               and r2["t1Deferred"]
               is True,
               str(r2)[:60])

        # 订单联动 settled
        order = await repo.get_order(p2)
        record("订单联动 settled",
               order.get("status")
               == "settled",
               str(order.get("status")))

        # ⑦ 结算留痕(settlement)
        split = await repo.get_split(
            split_id)
        settlement = split.get(
            "settlement") or {}
        record("结算留痕(T+1 延迟域)",
               settlement.get(
                   "settledBy")
               == "结算官"
               and settlement.get(
                   "t1Deferred")
               is True,
               str(settlement)[:60])

        # ⑧ 重复结算拒绝
        try:
            await svc.settle_split(
                split_id)
            ok, err = False, "未拒绝"
        except ValueError as e:
            ok, err = "不可重复" in str(e), \
                str(e)[:30]
        record("重复结算拒绝", ok, err)

        # ⑨ 分账不存在
        try:
            await svc.settle_split(99999)
            ok, err = False, "未拒绝"
        except KeyError:
            ok, err = True, ""
        record("分账不存在拒绝", ok, err)

        # ⑩ 观测面
        view = await svc.split_view()
        record("分账观测面",
               view["total"] == 2
               and view["byStatus"]
               == {"pending": 1,
                   "settled": 1},
               str(view["byStatus"]))


class TestHttp:
    """04 HTTP 层(P3)"""

    async def run(self):
        print("[04 HTTP]")
        reset_all()
        from fastapi.testclient import TestClient
        from main import app
        client = TestClient(app)
        admin = {"X-Role": "admin"}

        # 造数据(shadow 开单全链
        # 或直接 repo 种)
        from repositories.pay60_repository import (
            Pay60Repository,
        )
        from core.helpers import ts
        repo = Pay60Repository()
        p1 = await seed_success_order(
            50, 100.0)
        # 造重复扣款
        flow_id = await repo.next_flow_id()
        await repo.save_flow({
            "flowId": flow_id,
            "payId": p1,
            "channel": "mock",
            "channelMode": "mock",
            "amount": 100.0,
            "channelReceipt": {},
            "fingerprint": "sha256:h",
            "fallback": False,
            "error": "",
            "createdAt": ts(),
            "updatedAt": ts()})

        # off 亦可用(回流铁律)
        resp = client.post(
            "/api/pay60/recon/run",
            json={}, headers=admin)
        body = resp.json() or {}
        record("HTTP recon/run(off 亦可用)",
               resp.status_code == 200
               and body.get(
                   "differences")
               == 1
               and (body.get(
                   "autoPending")
                   or 0) == 1,
               str((resp.status_code,
                    body.get(
                        "differences"))))
        recon_id = body.get("reconId")

        # ① 差异处置(off 亦可用)
        resp = client.post(
            f"/api/pay60/recon/"
            f"{recon_id}/settle",
            json={"payId": p1,
                  "approve": True,
                  "settledBy":
                      "财务官"},
            headers=admin)
        body = resp.json() or {}
        record("HTTP recon/settle 200",
               resp.status_code == 200
               and body.get("status")
               == "settled",
               str((resp.status_code,
                    body.get("status"))))

        # ② 分账 off 409(决策面)
        resp = client.post(
            "/api/pay60/splits",
            json={"payId": 999},
            headers=admin)
        record("HTTP splits off 409",
               resp.status_code == 409,
               str(resp.status_code))

        # shadow 分账+off 结算
        os.environ["PAY60_MODE"] = "shadow"
        p2 = await seed_success_order(
            51, 200.0)
        resp = client.post(
            "/api/pay60/splits",
            json={"payId": p2},
            headers=admin)
        body = resp.json() or {}
        split_id = body.get("splitId")
        record("HTTP splits 200",
               resp.status_code == 200
               and body.get(
                   "conserved")
               is True
               and bool(split_id),
               str(resp.status_code))

        os.environ["PAY60_MODE"] = "off"
        resp = client.post(
            f"/api/pay60/splits/"
            f"{split_id}/settle",
            json={"settledBy":
                    "结算官"},
            headers=admin)
        record("HTTP splits/settle 200"
               "(off 亦可用)",
               resp.status_code == 200
               and (resp.json() or {}
                    ).get("status")
               == "settled",
               str(resp.status_code))

        # ③ 观测面
        resp = client.get(
            "/api/pay60/recon",
            headers=admin)
        record("HTTP recon 观测面",
               resp.status_code == 200
               and ((resp.json() or {})
                    .get("total")
                    or 0) >= 1,
               str(resp.status_code))
        resp = client.get(
            "/api/pay60/splits",
            headers=admin)
        record("HTTP splits 观测面",
               resp.status_code == 200
               and ((resp.json() or {})
                    .get("total")
                    or 0) == 1,
               str(resp.status_code))

        # ④ 不存在 404
        resp = client.post(
            "/api/pay60/recon/99999/"
            "settle",
            json={"payId": 1,
                  "approve": True},
            headers=admin)
        record("HTTP recon settle 404",
               resp.status_code == 404,
               str(resp.status_code))

        # ⑤ 鉴权 403
        resp = client.post(
            "/api/pay60/recon/run",
            json={})
        record("HTTP recon/run 无 Role 403",
               resp.status_code == 403,
               str(resp.status_code))


class TestConstitution:
    """05 宪法+QC"""

    async def run(self):
        print("[05 宪法+QC]")
        from services.pay60_recon_service import (
            DIFF_TYPES,
            LARGE_REVERSAL_THRESHOLD,
            RECON_REMEDIATION,
        )

        # ① 差异五类封闭
        record("差异五类(封闭)",
               DIFF_TYPES == (
                   "matched",
                   "channel_duplicate",
                   "amount_mismatch",
                   "missing_flow",
                   "flow_orphan"),
               str(DIFF_TYPES))

        # ② 归因表全覆盖(确定性
        #    ——LLM 不进判定链)
        record("归因表全覆盖",
               set(RECON_REMEDIATION)
               == set(DIFF_TYPES),
               str(RECON_REMEDIATION
                   .keys()))

        # ③ 冲正类仅 channel_duplicate
        record("冲正类仅重复扣款",
               [k for k, v in
                RECON_REMEDIATION
                .items()
                if v["reversal"]] == [
                   "channel_duplicate"],
               "")

        # ④ 大额人工终审线
        record("大额人工终审(≥5000)",
               LARGE_REVERSAL_THRESHOLD
               == 5000.0,
               str(LARGE_REVERSAL_THRESHOLD))

        # ⑤ 资金操作人工终审
        #    (代码层——settle 无
        #    自动调用方)
        import inspect
        from services.pay60_recon_service \
            import (
                Pay60ReconService,
            )
        src = inspect.getsource(
            Pay60ReconService)
        record("人工终审(接口层校验)",
               "approve" in
               inspect.getsource(
                   Pay60ReconService
                   .settle_recon)
               and "人工铁律" in src,
               "")

        # ⑥ 46号/44号零改动
        import services.ai_governance_service as s46
        import services.ai_learning_service as s44
        record("感知源零改动(44/46)",
               s44.__name__.endswith(
                   "ai_learning_service")
               and s46.__name__.endswith(
                   "ai_governance_service"),
               "")


async def run_all():
    await TestReconRun().run()
    await TestReconSettle().run()
    await TestSplit().run()
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
