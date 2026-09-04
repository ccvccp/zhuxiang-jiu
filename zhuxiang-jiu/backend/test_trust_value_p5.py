"""45号·信值模块 P5 专项测试(对外服务接口平台)

运行方式:
    python test_trust_value_p5.py

覆盖(计划 §八):
    - 防重放: nonce 长度/时间窗(过期拒绝)/重复 nonce 拒绝/
      窗口内通过
    - 信值查询 API: 脱敏(无证件摘要/无因子明细)/修复建议
      摘要/审计留痕/不存在拒绝
    - 兑换核销 API: 幂等键(首次执行/重复返回原结果
      idempotentReplay)/nonce 防重放/参数校验
    - 行为存证 API: 敏感词硬拦截(证件/病历/指纹)/
      正常存证透传验真
    - 信用分转换 API: nonce 校验/单向转换语义
    - 监管审计 API: 四视图(档案/事件/账本/访问日志)/
      只读留痕(自身访问入日志)
    - 看板聚合: 双角色分布/熔断率/资产聚合/自进化统计
    - HTTP 层: 六端点结构与鉴权
"""

import asyncio
import os
import sys
import time

os.environ["LOCK_MODE"] = "asyncio"
os.environ["STORE_MODE"] = "asyncio"
os.environ.pop("LLM_API_KEY", None)
os.environ["LLM_ENABLED"] = "off"

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


def _nonce(prefix="n"):
    return f"{prefix}-{int(time.time() * 1000)}-abcdef"


GOOD_EVIDENCE = "志愿服务 200 小时(编号ZY2026-088, 红十字会公示)"


class TestNonce:
    async def run(self):
        print("[01 防重放]")
        reset_all()
        from services.trust_gateway_service import (
            TrustGatewayService,
        )
        gw = TrustGatewayService()

        now = int(time.time())
        r = await gw.check_nonce(_nonce("ok"), now)
        record("窗口内通过", r["ok"] is True, str(r))

        # nonce 过短
        try:
            await gw.check_nonce("short", now)
            record("nonce过短拒绝", False, "未抛")
        except ValueError as e:
            record("nonce过短拒绝", "≥16" in str(e), str(e))

        # 时间窗外
        try:
            await gw.check_nonce(_nonce("old"), now - 400)
            record("时间窗拒绝", False, "未抛")
        except ValueError as e:
            record("时间窗拒绝", "重放" in str(e), str(e))

        # 重复 nonce
        nonce = _nonce("dup")
        await gw.check_nonce(nonce, now)
        try:
            await gw.check_nonce(nonce, now)
            record("重复nonce拒绝", False, "未抛")
        except ValueError as e:
            record("重复nonce拒绝",
                   "已使用" in str(e), str(e))

        # 非整数时间戳
        try:
            await gw.check_nonce(_nonce("ts"), "abc")
            record("非法时间戳拒绝", False, "未抛")
        except ValueError:
            record("非法时间戳拒绝", True)


class TestOpenQuery:
    async def run(self):
        print("[02 信值查询 API]")
        reset_all()
        from services.trust_scoring_service import (
            TrustProfileService,
        )
        from services.trust_gateway_service import (
            TrustGatewayService,
        )
        ps = TrustProfileService()
        gw = TrustGatewayService()
        p = await ps.create_role("person", "查询", "ID-OPQ-1")
        tid = p["trustId"]

        r = await gw.open_query(tid, "ac_test01")
        record("查询200", r["success"] is True, str(r)[:70])
        record("脱敏-无摘要", "idDigestMasked" not in r
               and "idDigest" not in r, "摘要泄漏")
        record("脱敏-层精简",
               all(set(v) == {"score", "weight",
                              "contribution"}
                   for v in (r.get("layers") or {}).values()),
               str(r.get("layers", {}).get("L1")))
        record("修复建议摘要",
               "repairableViolations" in
               (r.get("repairAdvice") or {}),
               str(r.get("repairAdvice"))[:60])
        record("脱敏声明", "脱敏" in r.get("note", ""),
               str(r.get("note"))[:40])

        # 审计留痕
        log = await gw.audit_log(tid)
        record("审计留痕", log["total"] == 1
               and log["entries"][0]["action"]
               == "open_query", str(log.get("total")))

        # 不存在拒绝
        try:
            await gw.open_query(99999, "ac_test01")
            record("查询不存在拒绝", False, "未抛")
        except KeyError:
            record("查询不存在拒绝", True)


class TestOpenRedeem:
    async def run(self):
        print("[03 兑换核销 API]")
        reset_all()
        from services.trust_scoring_service import (
            TrustProfileService,
        )
        from services.trust_asset_service import (
            TrustAssetService,
        )
        from services.trust_gateway_service import (
            TrustGatewayService,
        )
        ps = TrustProfileService()
        assets = TrustAssetService()
        gw = TrustGatewayService()

        p = await ps.create_role("person", "核销", "ID-OPR-1")
        tid = p["trustId"]
        await assets.issue(tid, 100.0,
                           reserve_ref="test:seed")
        await assets.merchant_deposit_add("开放商户", 200.0)
        r = await assets.redeem(tid, 30.0, "开放商户", "商品")
        rid = r["redeemId"]

        now = int(time.time())
        # 首次核销
        r = await gw.open_redeem_confirm(
            rid, "开放商户", "idem-key-001",
            _nonce("rd1"), now)
        record("首次核销销毁", r["success"] is True
               and r["burned"] == 30.0
               and r["idempotentReplay"] is False,
               str(r)[:80])

        # 幂等重放: 同 key 返回原结果
        r = await gw.open_redeem_confirm(
            rid, "开放商户", "idem-key-001",
            _nonce("rd2"), now)
        record("幂等重放原结果", r["idempotentReplay"] is True
               and r["burned"] == 30.0, str(r)[:70])

        # 余额只扣一次
        b = await assets.balance(tid)
        record("余额只扣一次", b["balance"] == 70.0
               and b["burnedTotal"] == 30.0,
               str(b.get("balance")))

        # 重复核销(不同幂等键)拒绝
        try:
            await gw.open_redeem_confirm(
                rid, "开放商户", "idem-key-002",
                _nonce("rd3"), now)
            record("重复核销拒绝", False, "未抛")
        except ValueError:
            record("重复核销拒绝", True)

        # nonce 重放拒绝
        r2 = await assets.redeem(tid, 10.0, "开放商户")
        nonce = _nonce("rd4")
        try:
            await gw.open_redeem_confirm(
                r2["redeemId"], "开放商户", "idem-key-003",
                nonce, now)
            await gw.open_redeem_confirm(
                r2["redeemId"], "开放商户", "idem-key-004",
                nonce, now)   # 同 nonce 重放
            record("nonce重放拒绝", False, "未抛")
        except ValueError as e:
            record("nonce重放拒绝",
                   "已使用" in str(e), str(e))

        # 参数校验
        try:
            await gw.open_redeem_confirm(
                999, "开放商户", "x", _nonce("rd5"), now)
            record("幂等键过短拒绝", False, "未抛")
        except ValueError:
            record("幂等键过短拒绝", True)


class TestOpenDeposit:
    async def run(self):
        print("[04 行为存证 API]")
        reset_all()
        from services.trust_scoring_service import (
            TrustProfileService,
        )
        from services.trust_gateway_service import (
            TrustGatewayService,
        )
        ps = TrustProfileService()
        gw = TrustGatewayService()
        p = await ps.create_role("person", "存证", "ID-OPD-1")
        tid = p["trustId"]

        # 敏感词硬拦截(红线 2: 数据最小必要)
        for name, ev in (
                ("证件敏感拦截", "身份证号:110101...志愿服务"),
                ("病历敏感拦截", "病历:高血压 志愿服务证明"),
                ("指纹敏感拦截", "指纹:aaaa 志愿服务证明"),
        ):
            try:
                await gw.open_deposit(
                    tid, "L3", "contribution_net",
                    100, 0, ev, "测试",
                    sources=["gov_penalty", "media"])
                record(name, False, "未抛")
            except ValueError as e:
                record(name, "敏感信息" in str(e), str(e))

        # 正常存证透传(孤证拒绝语义保留)
        r = await gw.open_deposit(
            tid, "L3", "contribution_net", 100, 0,
            GOOD_EVIDENCE, "志愿服务",
            sources=["gov_penalty", "media"])
        record("正常存证透传", r["success"] is True
               and r["applied"] is True, str(r)[:70])

        # 审计留痕(4 次: 3 敏感拦截 + 1 成功)
        log = await gw.audit_log(tid)
        record("存证审计留痕", log["total"] == 4,
               str(log.get("total")))
        record("拦截留痕口径",
               len([e for e in log["entries"]
                    if e["action"]
                    == "open_deposit_rejected"]) == 3,
               "拦截未留痕")


class TestOpenConvert:
    async def run(self):
        print("[05 信用分转换 API]")
        reset_all()
        from services.trust_scoring_service import (
            TrustProfileService,
        )
        from services.trust_gateway_service import (
            TrustGatewayService,
        )
        ps = TrustProfileService()
        gw = TrustGatewayService()
        p = await ps.create_role("person", "转换", "ID-OPC-1")
        tid = p["trustId"]

        now = int(time.time())
        r = await gw.open_convert(
            tid, 55, 100.0, _nonce("cv1"), now)
        record("开放转换1TV", r["success"] is True
               and r["amount"] == 1.0, str(r)[:70])

        # nonce 缺失
        try:
            await gw.open_convert(tid, 55, 100.0, "", now)
            record("nonce缺失拒绝", False, "未抛")
        except ValueError:
            record("nonce缺失拒绝", True)

        # 审计留痕
        log = await gw.audit_log(tid)
        record("转换审计留痕", log["total"] == 1
               and log["entries"][0]["action"]
               == "open_convert", str(log.get("total")))


class TestOpenAudit:
    async def run(self):
        print("[06 监管审计 API]")
        reset_all()
        from services.trust_scoring_service import (
            TrustProfileService,
        )
        from services.trust_gateway_service import (
            TrustGatewayService,
        )
        ps = TrustProfileService()
        gw = TrustGatewayService()
        p = await ps.create_role("person", "审计", "ID-OPA-1")
        tid = p["trustId"]
        await ps.record_event(tid, "L3",
                              "contribution_net", 20,
                              summary="志愿 20h 编号V1")

        r = await gw.open_audit(tid, "regulator-01")
        record("审计四视图", all(k in r for k in
               ("profile", "events", "ledger",
                "accessLog")), str(list(r))[:80])
        record("档案脱敏", "idDigestMasked" not in
               (r.get("profile") or {}), "摘要泄漏")
        record("事件含归因口径",
               "scoreBefore" in (r.get("events")
                                 or [{}])[-1],
               "scoreBefore 缺失")
        record("只读留痕", (r.get("accessLog") or [])
               [0].get("action") == "open_audit",
               "访问未留痕")

        # 不存在拒绝
        try:
            await gw.open_audit(99999, "regulator-01")
            record("审计不存在拒绝", False, "未抛")
        except KeyError:
            record("审计不存在拒绝", True)


class TestDashboard:
    async def run(self):
        print("[07 看板聚合]")
        reset_all()
        from services.trust_scoring_service import (
            TrustProfileService,
        )
        from services.trust_gateway_service import (
            TrustGatewayService,
        )
        ps = TrustProfileService()
        gw = TrustGatewayService()

        await ps.create_role("person", "甲", "ID-DASH-1")
        await ps.create_role("person", "乙", "ID-DASH-2")
        await ps.create_role("org", "丙公司", "ID-DASH-3")
        p4 = await ps.create_role("person", "丁", "ID-DASH-4")
        await ps.record_event(p4["trustId"], "L1",
                              "legal_record", -50,
                              severity="severe",
                              summary="严重违法")

        r = await gw.dashboard()
        ov = r["overview"]
        record("总览分布", ov["total"] == 4
               and ov["persons"] == 3
               and ov["orgs"] == 1,
               str(ov))
        record("熔断率", ov["fused"] == 1
               and ov["fusedRate"] == 0.25,
               str(ov.get("fusedRate")))
        record("档位分布", ov["byGrade"].get("critical")
               == 1, str(ov.get("byGrade")))
        record("雷达事件统计",
               r["radar"]["eventsTotal"] >= 1,
               str(r.get("radar")))
        record("资产区块", "issuedTotal" in r["assets"]
               and "reserveCoverage" in r["assets"],
               str(r.get("assets"))[:60])
        record("自进化区块", "appeals" in r["evolution"]
               and "patches" in r["evolution"],
               str(r.get("evolution")))


class TestHttp:
    async def run(self):
        print("[08 HTTP 层]")
        reset_all()
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from routes.trust_value_routes import (
            register_trust_value_routes,
        )
        from services.trust_scoring_service import (
            TrustProfileService,
        )
        from services.trust_asset_service import (
            TrustAssetService,
        )
        app = FastAPI()
        register_trust_value_routes(app)
        client = TestClient(app)

        ps = TrustProfileService()
        assets = TrustAssetService()
        p = await ps.create_role("person", "HTTP开放",
                                 "ID-HTTP-P5-1")
        tid = p["trustId"]
        await assets.issue(tid, 100.0,
                           reserve_ref="http:test")
        await assets.merchant_deposit_add("HTTP商户", 200.0)
        r = await assets.redeem(tid, 30.0, "HTTP商户", "货")
        rid = r["redeemId"]

        # ① 查询 200(脱敏)
        resp = client.get(
            f"/api/trust/open/query/{tid}",
            headers={"X-App-Code": "ac_http01"})
        body = resp.json()
        record("HTTP查询200", resp.status_code == 200
               and body.get("score") == 55.0,
               str(body)[:70])
        record("HTTP查询脱敏",
               "idDigest" not in str(body), "摘要泄漏")

        # 查询 404
        resp = client.get("/api/trust/open/query/99999")
        record("HTTP查询404", resp.status_code == 404,
               str(resp.status_code))

        # ② 核销(幂等+nonce)
        now = int(time.time())
        payload = {"redeemId": rid, "merchant": "HTTP商户",
                   "idempotencyKey": "http-idem-1",
                   "nonce": _nonce("hr1"), "timestamp": now}
        resp = client.post("/api/trust/open/redeem/confirm",
                           json=payload)
        body = resp.json()
        record("HTTP核销200", resp.status_code == 200
               and body.get("burned") == 30.0,
               str(body)[:70])
        # 幂等重放
        payload["nonce"] = _nonce("hr2")
        resp = client.post("/api/trust/open/redeem/confirm",
                           json=payload)
        record("HTTP幂等重放",
               resp.json().get("idempotentReplay") is True,
               str(resp.json())[:60])
        # nonce 重放
        payload["idempotencyKey"] = "http-idem-2"
        resp = client.post("/api/trust/open/redeem/confirm",
                           json=payload)
        record("HTTP重放409", resp.status_code == 409
               and "已使用" in str(resp.json().get(
                   "detail", "")), str(resp.status_code))

        # ③ 存证(敏感拦截 + 正常)
        resp = client.post("/api/trust/open/deposits", json={
            "trustId": tid, "layer": "L3",
            "factor": "contribution_net", "observed": 100,
            "peerBaseline": 0,
            "evidence": "病历:xx 志愿服务 100 小时"})
        record("HTTP敏感409", resp.status_code == 409,
               str(resp.status_code))
        resp = client.post("/api/trust/open/deposits", json={
            "trustId": tid, "layer": "L3",
            "factor": "contribution_net", "observed": 100,
            "peerBaseline": 0,
            "evidence": GOOD_EVIDENCE,
            "summary": "志愿服务(权威公示)",
            "sources": ["gov_penalty", "media"]})
        record("HTTP存证200", resp.status_code == 200
               and resp.json().get("applied") is True,
               str(resp.json())[:70])

        # ④ 转换
        resp = client.post("/api/trust/open/convert", json={
            "trustId": tid, "userId": 33,
            "creditPoints": 100.0,
            "nonce": _nonce("hc1"), "timestamp": now})
        record("HTTP转换200", resp.status_code == 200
               and resp.json().get("amount") == 1.0,
               str(resp.json())[:70])
        # nonce 缺失
        resp = client.post("/api/trust/open/convert", json={
            "trustId": tid, "userId": 33,
            "creditPoints": 100.0, "timestamp": now})
        record("HTTP转换nonce409", resp.status_code == 409,
               str(resp.status_code))

        # ⑤ 审计
        resp = client.get(
            f"/api/trust/open/audit/{tid}",
            headers={"X-App-Code": "ac_regulator"})
        record("HTTP审计200", resp.status_code == 200
               and "profile" in resp.json(),
               str(resp.status_code))
        resp = client.get("/api/trust/open/audit/99999")
        record("HTTP审计404", resp.status_code == 404,
               str(resp.status_code))

        # ⑥ 看板
        resp = client.get("/api/trust/open/dashboard")
        record("HTTP看板200", resp.status_code == 200
               and "overview" in resp.json(),
               str(resp.status_code))


async def run_all():
    await TestNonce().run()
    await TestOpenQuery().run()
    await TestOpenRedeem().run()
    await TestOpenDeposit().run()
    await TestOpenConvert().run()
    await TestOpenAudit().run()
    await TestDashboard().run()
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
