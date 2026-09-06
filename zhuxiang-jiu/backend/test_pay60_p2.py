"""60号·AI智能支付管理模块 P2 专项测试
(信值融合风控)

运行方式:
    python test_pay60_p2.py

覆盖(60号计划 §3.2/§七 P2):
    - riskTier 四级直通: pass 无感
      (trusted+小额+设备可信)/
      light OTP mock/strong 屏幕码/
      block 阻断
    - 行为序列校验: 跳跃式操作
      (无浏览直接支付)升一档
    - 合规前置: 禁令命中即 block
    - AML 确定性环路检测: 资金环+
      同设备多账户+快进快出
    - 验证流: 令牌签发(TTL 一次性
      ——48号 confirmToken 语义)
    - 阈值配置域: 46号审批 submit/
      apply 双模
    - execute: verified→executing→
      success/failed+归因链附加回执
    - fail-soft: 感知源异常降
      standard 不阻断
    - QC: fail-soft 不阻断; 高信值
      无感直通
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


async def seed_order(member_id=10,
                     amount=100.0,
                     tier="standard"):
    """种 priced 态订单"""
    from repositories.pay60_repository import (
        Pay60Repository,
    )
    from core.helpers import ts
    repo = Pay60Repository()
    pay_id = await repo.next_pay_id()
    await repo.save_order({
        "payId": pay_id,
        "memberId": member_id,
        "scene": "purchase",
        "role": "member",
        "status": "priced",
        "basePrice": amount,
        "finalPrice": amount,
        "tier": tier,
        "attribution": {},
        "createdAt": ts(),
        "updatedAt": ts()})
    return pay_id


class TestAssessRules:
    """01 riskTier 四级规则"""

    async def run(self):
        print("[01 riskTier 规则]")
        from services.pay60_registry import (
            AML_RULES,
            COMPLIANCE_BANS,
            LIGHT_MAX_AMOUNT,
            PASS_MAX_AMOUNT,
            RISK_TIERS,
            VERIFY_METHODS,
            assess_risk_tier,
        )

        # ① 四级域
        record("四级域(pass/light/strong/block)",
               RISK_TIERS == ("pass", "light",
                              "strong",
                              "block"),
               str(RISK_TIERS))
        record("验证方式域(四档)",
               set(VERIFY_METHODS) == {
                   "confirm_token",
                   "otp_mock",
                   "screen_code", "none"},
               str(VERIFY_METHODS))
        record("金额阈值(2000/5000)",
               LIGHT_MAX_AMOUNT == 2000.0
               and PASS_MAX_AMOUNT
               == 5000.0,
               str((LIGHT_MAX_AMOUNT,
                    PASS_MAX_AMOUNT)))

        # ② pass: trusted+小额+设备可信
        r = assess_risk_tier(
            "trusted", 3000.0,
            device_trusted=True)
        record("pass 无感直通",
               r["riskTier"] == "pass"
               and r["verifyMethod"]
               == "confirm_token",
               str(r["riskTier"]))

        # ③ pass 不可达: 设备不可信
        r = assess_risk_tier(
            "trusted", 3000.0,
            device_trusted=False)
        record("trusted 无设备→light",
               r["riskTier"] == "light",
               str(r["riskTier"]))

        # ④ strong: 大额(>5000)
        r = assess_risk_tier(
            "trusted", 6000.0,
            device_trusted=True)
        record("大额→strong",
               r["riskTier"] == "strong"
               and r["verifyMethod"]
               == "screen_code",
               str(r["riskTier"]))

        # ⑤ strong: watched
        r = assess_risk_tier(
            "watched", 100.0)
        record("watched→strong",
               r["riskTier"] == "strong",
               str(r["riskTier"]))

        # ⑥ block: restricted
        r = assess_risk_tier(
            "restricted", 100.0)
        record("restricted→block",
               r["riskTier"] == "block",
               str(r["riskTier"]))

        # ⑦ block: 合规禁令
        r = assess_risk_tier(
            "trusted", 100.0,
            compliance_flags=[
                "industry_ban"])
        record("合规禁令→block",
               r["riskTier"] == "block"
               and "industry_ban"
               in str(r["reasons"]),
               str(r["reasons"]))

        # ⑧ block: AML 命中
        r = assess_risk_tier(
            "trusted", 100.0,
            aml_hits=["fund_loop"])
        record("AML 命中→block",
               r["riskTier"] == "block"
               and "fund_loop"
               in str(r["reasons"]),
               str(r["reasons"]))

        # ⑨ light: standard 小额
        r = assess_risk_tier(
            "standard", 100.0)
        record("standard→light",
               r["riskTier"] == "light"
               and r["verifyMethod"]
               == "otp_mock",
               str(r["riskTier"]))

        # ⑩ 行为跳跃升档
        r = assess_risk_tier(
            "standard", 100.0,
            behavior_sequence=[
                "pay"])
        record("跳跃升档(无 browse)",
               r["riskTier"] == "strong"
               and r["escalatedBy"]
               == "behavior_jump",
               str((r["riskTier"],
                    r["escalatedBy"])))

        # ⑪ 正常序列不升档
        r = assess_risk_tier(
            "standard", 100.0,
            behavior_sequence=[
                "browse", "order",
                "pay"])
        record("正常序列不升档",
               r["riskTier"] == "light",
               str(r["riskTier"]))

        # ⑫ AML 三规则域
        record("AML 三规则(封闭)",
               AML_RULES == (
                   "fund_loop",
                   "device_multi_account",
                   "fast_in_fast_out"),
               str(AML_RULES))
        record("合规禁令域(封闭)",
               COMPLIANCE_BANS == (
                   "industry_ban",
                   "tax_violation",
                   "sanction_list"),
               str(COMPLIANCE_BANS))


class TestVerify:
    """02 风控验证主链"""

    async def run(self):
        print("[02 风控验证]")
        reset_all()
        from services.pay60_risk_service import (
            Pay60RiskService,
        )
        from repositories.pay60_repository \
            import Pay60Repository
        svc = Pay60RiskService()
        repo = Pay60Repository()

        # off 拒绝
        try:
            await svc.verify(1)
            ok, err = False, "未拒绝"
        except ValueError as e:
            ok, err = "off" in str(e), \
                str(e)[:30]
        record("off 态验证拒绝", ok, err)

        os.environ["PAY60_MODE"] = "shadow"

        # 状态机: 非 priced 拒绝
        pay_id = await seed_order()
        await repo.save_order({
            "payId": pay_id,
            "memberId": 10,
            "status": "created",
            "basePrice": 100.0,
            "finalPrice": 100.0,
            "createdAt": "",
            "updatedAt": ""},
            create=False)
        try:
            await svc.verify(pay_id)
            ok, err = False, "未拒绝"
        except ValueError as e:
            ok, err = "priced" in str(e), \
                str(e)[:30]
        record("非 priced 态拒绝", ok, err)

        # ① light 档验证+令牌签发
        pay_id = await seed_order(
            member_id=10, amount=100.0)
        r = await svc.verify(
            pay_id,
            behavior_sequence=[
                "browse", "order", "pay"])
        record("light 档(OTP 令牌)",
               r["riskTier"] == "light"
               and r["verifyMethod"]
               == "otp_mock"
               and str(r["verifyToken"])
               .startswith("VT")
               and r["verifyTtl"] == 60,
               str((r["riskTier"],
                    r["verifyToken"]
                    [:8])))

        # ② 状态机流转 priced→verified
        order = await repo.get_order(pay_id)
        record("priced→verified",
               order.get("status")
               == "verified",
               str(order.get("status")))

        # ③ 验证事件留痕
        evs = await repo.list_verifications(
            pay_id=pay_id)
        record("验证事件留痕",
               len(evs) == 1
               and evs[0]["riskTier"]
               == "light"
               and evs[0]["status"]
               == "pending",
               str(len(evs)))

        # ④ block 档(合规禁令)
        pay_id2 = await seed_order(
            member_id=11, amount=100.0)
        r = await svc.verify(
            pay_id2,
            compliance_flags=[
                "sanction_list"])
        record("block 档(整改指引)",
               r["riskTier"] == "block"
               and r["verified"] is False
               and "整改" in str(
                   r["remediation"]),
               str(r["riskTier"]))

        # block 不流转(留 priced)
        order = await repo.get_order(pay_id2)
        record("block 不流转(留 priced)",
               order.get("status")
               == "priced",
               str(order.get("status")))

        # ⑤ strong 档(大额)
        pay_id3 = await seed_order(
            member_id=12, amount=6000.0)
        r = await svc.verify(pay_id3)
        record("strong 档(屏幕码)",
               r["riskTier"] == "strong"
               and r["verifyMethod"]
               == "screen_code"
               and "FIDO" in str(
                   r["tokenHint"]),
               str((r["riskTier"],
                    r["verifyMethod"])))

        # ⑥ 行为跳跃升档(
        #    trusted+设备→strong)
        pay_id4 = await seed_order(
            member_id=13, amount=100.0,
            tier="trusted")
        r = await svc.verify(
            pay_id4, device_trusted=True,
            behavior_sequence=["pay"])
        record("跳跃升档(pass→strong)",
               r["riskTier"] == "strong"
               and r["escalatedBy"]
               == "behavior_jump",
               str((r["riskTier"],
                    r["escalatedBy"])))

        # ⑦ AML 快进快出(≥10 万
        #    无设备)
        pay_id5 = await seed_order(
            member_id=14, amount=150000.0)
        r = await svc.verify(pay_id5)
        record("AML 快进快出→block",
               r["riskTier"] == "block"
               and "fast_in_fast_out"
               in (r.get("amlHits")
                   or []),
               str(r.get("amlHits")))

        # ⑧ 订单不存在
        try:
            await svc.verify(99999)
            ok, err = False, "未拒绝"
        except KeyError:
            ok, err = True, ""
        record("订单不存在拒绝", ok, err)
        os.environ["PAY60_MODE"] = "off"


class TestConfirm:
    """03 令牌核销(会员面)"""

    async def run(self):
        print("[03 令牌核销]")
        reset_all()
        from services.pay60_risk_service import (
            Pay60RiskService,
        )
        from repositories.pay60_repository \
            import Pay60Repository
        svc = Pay60RiskService()
        repo = Pay60Repository()

        # 造 verified 态+pending 令牌
        os.environ["PAY60_MODE"] = "shadow"
        pay_id = await seed_order(
            member_id=20, amount=100.0)
        v = await svc.verify(pay_id)

        # ① 非 assist 拒绝(会员面)
        try:
            await svc.confirm(
                pay_id,
                v["verifyToken"])
            ok, err = False, "未拒绝"
        except ValueError as e:
            ok, err = "assist" in str(e), \
                str(e)[:30]
        record("非 assist 拒绝(会员面)",
               ok, err)

        # ② assist 态核销
        os.environ["PAY60_MODE"] = "assist"
        r = await svc.confirm(
            pay_id, v["verifyToken"])
        record("令牌核销(confirmed)",
               r["status"] == "confirmed"
               and r["riskTier"]
               == "light",
               str(r["status"]))

        # ③ 一次性消费(重复拒绝)
        try:
            await svc.confirm(
                pay_id,
                v["verifyToken"])
            ok, err = False, "未拒绝"
        except ValueError as e:
            ok, err = "无待核销" in str(e), \
                str(e)[:30]
        record("令牌一次性消费", ok, err)

        # ④ 错误令牌拒绝
        pay_id2 = await seed_order(
            member_id=21, amount=100.0)
        await svc.verify(pay_id2)
        try:
            await svc.confirm(
                pay_id2, "VTwrong")
            ok, err = False, "未拒绝"
        except ValueError as e:
            ok, err = "无效" in str(e), \
                str(e)[:30]
        record("错误令牌拒绝", ok, err)

        # ⑤ 归因链更新(riskTier)
        order = await repo.get_order(pay_id)
        record("归因链 riskTier 更新",
               (order.get("attribution")
                or {}).get("riskTier")
               == "light",
               str(order.get(
                   "attribution")))
        os.environ["PAY60_MODE"] = "off"


class TestExecute:
    """04 渠道执行"""

    async def run(self):
        print("[04 渠道执行]")
        reset_all()
        from services.pay60_risk_service import (
            Pay60RiskService,
        )
        from repositories.pay60_repository \
            import Pay60Repository
        svc = Pay60RiskService()
        repo = Pay60Repository()

        # ① 未核销拒绝
        os.environ["PAY60_MODE"] = "shadow"
        pay_id = await seed_order(
            member_id=30, amount=100.0)
        await svc.verify(pay_id)
        try:
            await svc.execute(pay_id)
            ok, err = False, "未拒绝"
        except ValueError as e:
            ok, err = "未核销" in str(e), \
                str(e)[:30]
        record("未核销拒绝执行", ok, err)

        # ② 全链: verify→confirm→execute
        #    (新订单——pay_id 已 verified)
        os.environ["PAY60_MODE"] = "assist"
        pay_id = await seed_order(
            member_id=33, amount=100.0)
        v = await svc.verify(pay_id)
        await svc.confirm(
            pay_id, v["verifyToken"])
        r = await svc.execute(pay_id)
        record("执行成功(mock 回执)",
               r["status"] == "success"
               and r["receipt"]
               ["channel"] == "mock",
               str((r["status"],
                    r["receipt"].get(
                        "channel"))))

        # ③ 归因链附加回执
        order = await repo.get_order(pay_id)
        attr = order.get("attribution") or {}
        record("归因链附加回执",
               attr.get(
                   "channelReceipt",
                   {}).get("channel")
               == "mock"
               and str(attr.get(
                   "flowFingerprint"))
               .startswith("sha256:"),
               str(attr)[:60])

        # ④ 状态机全链(priced→verified
        #    →executing→success)
        record("全链状态(success)",
               order.get("status")
               == "success",
               str(order.get("status")))

        # ⑤ 非 verified 拒绝
        pay_id2 = await seed_order(
            member_id=31, amount=100.0)
        try:
            await svc.execute(pay_id2)
            ok, err = False, "未拒绝"
        except ValueError as e:
            ok, err = "verified" in str(e), \
                str(e)[:30]
        record("非 verified 拒绝", ok, err)

        # ⑥ 渠道失败→failed(
        #    real 无凭证 fail-hard)
        pay_id3 = await seed_order(
            member_id=32, amount=100.0)
        v3 = await svc.verify(pay_id3)
        await svc.confirm(
            pay_id3, v3["verifyToken"])
        r = await svc.execute(
            pay_id3, channel_mode="real")
        record("real 失败→failed",
               r["status"] == "failed"
               and "error" in str(
                   r["receipt"]),
               str(r["status"]))

        # ⑦ 验证观测面
        view = await svc.verification_view()
        record("验证观测面(riskTier 分布)",
               view["total"] >= 3
               and "byTier" in view,
               str(view["byTier"]))
        os.environ["PAY60_MODE"] = "off"


class TestThreshold:
    """05 阈值配置域(46号双模)"""

    async def run(self):
        print("[05 阈值配置]")
        reset_all()
        from services.pay60_risk_service import (
            Pay60RiskService,
        )
        from services.ai_governance_service import (
            AiGovernanceService,
        )
        # 46号入册(幂等)
        await AiGovernanceService(
        ).sync_registry()
        svc = Pay60RiskService()

        # ① 阈值非法拒绝
        try:
            await svc.calibrate_submit(
                3000, 6000)
            ok, err = False, "未拒绝"
        except ValueError as e:
            ok, err = "非法" in str(e), \
                str(e)[:30]
        record("阈值非法拒绝", ok, err)

        # ② submit→46号 pending
        r = await svc.calibrate_submit(
            8000, 3000,
            requested_by="风控官",
            reason="大额单量增长放宽")
        record("校准提交 46号(pending)",
               r["status"] == "pending"
               and (r.get("changeId")
                    or 0) > 0,
               str(r)[:60])

        # ③ 未裁决不可生效
        try:
            await svc.calibrate_apply(
                r["changeId"])
            ok, err = False, "未拒绝"
        except ValueError as e:
            ok, err = "未经" in str(e) \
                or "人工裁决" in str(e), \
                str(e)[:30]
        record("未经裁决不可生效", ok, err)

        # ④ 46号人工裁决留痕(
        #    config 执行器预期抛
        #    "执行失败"但 reviewedBy
        #    已留痕)
        try:
            await AiGovernanceService(
            ).review_change(
                int(r["changeId"]),
                approve=True,
                reviewed_by="治理官")
        except ValueError:
            pass

        # ⑤ apply 生效
        r2 = await svc.calibrate_apply(
            r["changeId"],
            applied_by="风控总监")
        record("裁决后生效(apply)",
               r2["config"] == {
                   "passMaxAmount": 8000.0,
                   "lightMaxAmount":
                       3000.0},
               str(r2["config"]))

        # ⑥ 重复生效拒绝
        try:
            await svc.calibrate_apply(
                r["changeId"])
            ok, err = False, "未拒绝"
        except ValueError as e:
            ok, err = "勿重复" in str(e), \
                str(e)[:30]
        record("重复生效拒绝", ok, err)

        # ⑦ 阈值视图(46号留痕)
        view = await svc.thresholds_view()
        record("阈值视图(生效值+留痕)",
               view["active"] == {
                   "passMaxAmount": 8000.0,
                   "lightMaxAmount":
                       3000.0}
               and view["approval"]
               ["appliedBy"]
               == "风控总监",
               str(view["active"]))


class TestFailSoft:
    """06 fail-soft 铁律"""

    async def run(self):
        print("[06 fail-soft]")
        reset_all()
        from services.pay60_risk_service import (
            Pay60RiskService,
        )
        svc = Pay60RiskService()
        os.environ["PAY60_MODE"] = "shadow"

        # 感知源异常(47号抛错被
        # fail-soft 捕获→standard
        # 不阻断业务——且 pass 不可达)
        tier, source = await \
            svc._member_tier(99999)
        record("fail-soft(降 standard)",
               tier == "standard"
               and source in (
                   "47号", "failsoft"),
               str((tier, source)))

        # 未建档会员正常走 light
        pay_id = await seed_order(
            member_id=88888,
            amount=100.0)
        r = await svc.verify(pay_id)
        record("未建档→light(不阻断)",
               r["riskTier"] == "light"
               and r["verified"]
               is False,
               str(r["riskTier"]))
        os.environ["PAY60_MODE"] = "off"


class TestHttp:
    """07 HTTP 层(P2)"""

    async def run(self):
        print("[07 HTTP]")
        reset_all()
        from fastapi.testclient import TestClient
        from main import app
        client = TestClient(app)
        admin = {"X-Role": "admin"}

        # off 409(决策面)
        resp = client.post(
            "/api/pay60/orders/1/verify",
            json={}, headers=admin)
        record("HTTP verify off 409",
               resp.status_code == 409,
               str(resp.status_code))

        # 造 priced 订单(shadow)
        os.environ["PAY60_MODE"] = "shadow"
        resp = client.post(
            "/api/pay60/orders",
            json={"memberId": 60,
                  "scene": "purchase",
                  "role": "member",
                  "basePrice": 100.0,
                  "tier": "standard"},
            headers=admin)
        pay_id = (resp.json() or {}
                  ).get("payId")

        # ① verify 200(light)
        resp = client.post(
            f"/api/pay60/orders/"
            f"{pay_id}/verify",
            json={"behaviorSequence": [
                "browse", "pay"]},
            headers=admin)
        body = resp.json() or {}
        record("HTTP verify 200(light)",
               resp.status_code == 200
               and body.get("riskTier")
               == "light"
               and str(body.get(
                   "verifyToken"))
               .startswith("VT"),
               str((resp.status_code,
                    body.get(
                        "riskTier"))))

        # ② confirm 非 assist 409
        resp = client.post(
            f"/api/pay60/orders/"
            f"{pay_id}/confirm",
            json={"verifyToken":
                  body.get("verifyToken")},
            headers=admin)
        record("HTTP confirm 非 assist 409",
               resp.status_code == 409,
               str(resp.status_code))

        # ③ assist 全链
        os.environ["PAY60_MODE"] = "assist"
        resp = client.post(
            f"/api/pay60/orders/"
            f"{pay_id}/confirm",
            json={"verifyToken":
                  body.get("verifyToken")},
            headers=admin)
        record("HTTP confirm 200",
               resp.status_code == 200
               and (resp.json() or {}
                    ).get("status")
               == "confirmed",
               str(resp.status_code))
        resp = client.post(
            f"/api/pay60/orders/"
            f"{pay_id}/execute",
            json={}, headers=admin)
        body = resp.json() or {}
        record("HTTP execute 200",
               resp.status_code == 200
               and body.get("status")
               == "success",
               str((resp.status_code,
                    body.get("status"))))

        # ④ 阈值视图(观测面)
        resp = client.get(
            "/api/pay60/thresholds",
            headers=admin)
        record("HTTP thresholds 观测面",
               resp.status_code == 200
               and (resp.json() or {}
                    ).get("active")
               is not None,
               str(resp.status_code))

        # ⑤ 验证观测面
        resp = client.get(
            "/api/pay60/verifications",
            headers=admin)
        record("HTTP verifications 观测面",
               resp.status_code == 200
               and ((resp.json()
                     or {}).get("total")
                    or 0) >= 1,
               str(resp.status_code))

        # ⑥ 阈值校准 200(46号 pending)
        #    (需 46号档案入册)
        from services.ai_governance_service import (
            AiGovernanceService,
        )
        await AiGovernanceService(
        ).sync_registry()
        resp = client.post(
            "/api/pay60/threshold/"
            "calibrate",
            json={"mode": "submit",
                  "passMaxAmount": 8000,
                  "lightMaxAmount":
                      3000},
            headers=admin)
        record("HTTP calibrate 200",
               resp.status_code == 200
               and (resp.json() or {}
                    ).get("status")
               == "pending",
               str(resp.status_code))

        # ⑦ registry 风控视图
        resp = client.get(
            "/api/pay60/registry",
            headers=admin)
        risk = (resp.json() or {}
                ).get("risk") or {}
        record("HTTP registry 风控视图",
               resp.status_code == 200
               and risk.get(
                   "riskTiers") == [
                   "pass", "light",
                   "strong", "block"]
               and risk.get(
                   "amlRules")
               == ["fund_loop",
                   "device_multi_account",
                   "fast_in_fast_out"],
               str(risk.get(
                   "riskTiers")))

        # ⑧ 鉴权 403
        resp = client.post(
            "/api/pay60/orders/1/verify",
            json={})
        record("HTTP verify 无 Role 403",
               resp.status_code == 403,
               str(resp.status_code))
        os.environ["PAY60_MODE"] = "off"


async def run_all():
    await TestAssessRules().run()
    await TestVerify().run()
    await TestConfirm().run()
    await TestExecute().run()
    await TestThreshold().run()
    await TestFailSoft().run()
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
