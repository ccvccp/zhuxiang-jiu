"""45号·P6 Value-UEBA 行为本体守门层专项测试

运行方式:
    python test_trust_value_p6_ueba.py

覆盖(docs/45号_Value-UEBA行为本体对照优化.md):
    - 四守门纯函数数学断言: 一致性预警(0.3 边界)/
      作秀降权(0.7 边界)/再犯风险(n/(n+2) 平滑+0.7 触发)/
      自愿激励(1.05)
    - record_event 接入: L2 伪善预警折损/L3 作秀降权/
      负向不折损/None 零影响/响应 uebaGates 留痕
    - submit_repair 接入: 惯犯(第 5 次)修复效率减半/
      低频违规不受影响/响应 recurrenceRisk 留痕
    - submit_deposit 接入: voluntary 激励/None 零影响/
      正向才激励
    - HTTP 透传: 三端点参数传递
    - 回归保护: 不传守门参数时既有精确断言不变
"""

import asyncio
import os
import sys

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


def _ev(base: str) -> str:
    """唯一证据内容(防验真指纹重放拒收)"""
    import uuid
    return f"{base}({uuid.uuid4().hex[:8]})"


async def new_profile(role: str = "person") -> int:
    import uuid
    from services.trust_scoring_service import (
        TrustProfileService,
    )
    suffix = uuid.uuid4().hex[:10]
    r = await TrustProfileService().create_role(
        role, f"p6-{role}-{suffix}",
        f"110101{suffix}4321")
    return r["trustId"]


class TestGateFunctions:
    async def run(self):
        print("[01 四守门纯函数数学]")
        from services.trust_ueba_service import (
            consistency_gate, self_promotion_gate,
            recurrence_risk, recurrence_gate,
            voluntary_bonus, apply_event_gates,
        )

        # ① 一致性预警
        m, tag, _ = consistency_gate(0.2)
        record("一致性<0.3触发预警",
               m == 0.5 and tag == "hypocrisy_alert",
               f"m={m} tag={tag}")
        m, tag, _ = consistency_gate(0.3)
        record("一致性0.3边界不触发",
               m == 1.0 and tag == "", f"m={m}")
        m, tag, _ = consistency_gate(None)
        record("一致性None不守门", m == 1.0 and tag == "")
        m, tag, _ = consistency_gate("bad")
        record("一致性非法输入保守触发",
               m == 0.5 and tag == "hypocrisy_alert",
               f"m={m}")

        # ② 作秀降权
        m, tag, _ = self_promotion_gate(0.8)
        record("宣传占比>0.7触发降权",
               m == 0.5 and tag == "self_promotion_discount",
               f"m={m} tag={tag}")
        m, tag, _ = self_promotion_gate(0.7)
        record("宣传占比0.7边界不触发",
               m == 1.0 and tag == "", f"m={m}")
        m, tag, _ = self_promotion_gate(None)
        record("宣传占比None不守门", m == 1.0)

        # ③ 再犯风险
        record("再犯风险n=0", recurrence_risk(0) == 0.0)
        record("再犯风险n=2", recurrence_risk(2) == 0.5)
        record("再犯风险n=5(首次越限)",
               recurrence_risk(5) == round(5 / 7, 4)
               and recurrence_risk(5) > 0.7)
        record("再犯风险n=4未越限",
               recurrence_risk(4) == round(4 / 6, 4)
               and recurrence_risk(4) <= 0.7)
        risk, m, note = recurrence_gate(5)
        record("再犯守门n=5减半",
               risk > 0.7 and m == 0.5 and bool(note),
               f"risk={risk} m={m}")
        risk, m, note = recurrence_gate(2)
        record("再犯守门n=2不减",
               risk == 0.5 and m == 1.0 and note == "",
               f"risk={risk} m={m}")

        # ④ 自愿激励
        m, note = voluntary_bonus(True, positive=True)
        record("自愿+正向激励",
               m == 1.05 and bool(note), f"m={m}")
        m, note = voluntary_bonus(True, positive=False)
        record("自愿+负向不激励",
               m == 1.0 and note == "", f"m={m}")
        m, note = voluntary_bonus(None, positive=True)
        record("None不激励", m == 1.0)

        # 编排: L2 正向 + 低一致性 → 折半
        r = apply_event_gates("L2", 20.0,
                              consistency=0.1)
        record("编排L2正向折半",
               r["delta"] == 10.0
               and len(r["gates"]) == 1,
               str(r))
        # 编排: L3 正常占比不折
        r = apply_event_gates("L3", 20.0,
                              self_promotion=0.5)
        record("编排L3正常不折",
               r["delta"] == 20.0 and r["gates"] == [],
               str(r))
        # 编排: 负向不折(伪善只影响加分)
        r = apply_event_gates("L2", -20.0,
                              consistency=0.1)
        record("编排负向不折损",
               r["delta"] == -20.0 and r["gates"] == [],
               str(r))
        # 编排: 双守门叠加
        r = apply_event_gates("L3", 20.0,
                              self_promotion=0.9)
        record("编排L3作秀折半",
               r["delta"] == 10.0, str(r))


class TestRecordEventGates:
    async def run(self):
        print("[02 record_event 守门接入]")
        reset_all()
        from services.trust_scoring_service import (
            TrustProfileService,
        )
        svc = TrustProfileService()

        # None 零影响(回归保护)
        tid = await new_profile()
        before = (await svc.repo.get_profile(tid)
                  )["factors"]["ethics_evidence"]
        r = await svc.record_event(
            tid, "L2", "ethics_evidence", 20.0)
        after = (await svc.repo.get_profile(tid)
                 )["factors"]["ethics_evidence"]
        record("None零影响(原delta入分)",
               round(after - before, 1) == 20.0
               and r["uebaGates"] == [],
               f"{before}→{after}")

        # L2 伪善预警
        tid2 = await new_profile("org")
        r = await svc.record_event(
            tid2, "L2", "ethics_evidence", 20.0,
            consistency=0.1)
        record("L2伪善预警折半",
               len(r["uebaGates"]) == 1
               and r["uebaGates"][0]["tag"]
               == "hypocrisy_alert",
               str(r["uebaGates"])[:70])
        factors = (await svc.repo.get_profile(
            tid2))["factors"]
        record("L2预警入分折半",
               round(factors["ethics_evidence"]
                     - 50.0, 1) == 10.0,
               str(factors["ethics_evidence"]))
        # 事件留痕含预警
        events = await svc.repo.list_events_by_trust(tid2)
        record("事件留痕含预警",
               "UEBA伪善预警" in str(
                   events[-1].get("summary")),
               str(events[-1].get("summary"))[:60])

        # L3 作秀降权
        r = await svc.record_event(
            tid2, "L3", "contribution_net", 20.0,
            self_promotion=0.9)
        record("L3作秀降权折半",
               len(r["uebaGates"]) == 1
               and r["uebaGates"][0]["tag"]
               == "self_promotion_discount",
               str(r["uebaGates"])[:70])
        factors = (await svc.repo.get_profile(
            tid2))["factors"]
        record("L3降权入分折半",
               round(factors["contribution_net"], 1) == 10.0,
               str(factors["contribution_net"]))

        # 负向不折损
        tid3 = await new_profile()
        r = await svc.record_event(
            tid3, "L2", "ethics_evidence", -30.0,
            consistency=0.1)
        record("负向不折损",
               r["uebaGates"] == [],
               str(r["uebaGates"]))

        # L1 不守门(守门仅 L2/L3)
        r = await svc.record_event(
            tid3, "L1", "legal_record", 10.0,
            consistency=0.1, self_promotion=0.9)
        record("L1不守门",
               r["uebaGates"] == [],
               str(r["uebaGates"]))


class TestRepairRecurrence:
    async def run(self):
        print("[03 修复域再犯风险]")
        reset_all()
        from services.trust_scoring_service import (
            TrustProfileService,
        )
        from services.trust_repair_service import (
            TrustRepairService,
        )
        svc = TrustProfileService()
        repair_svc = TrustRepairService()

        # 低频违规(1 次): 修复不受影响(兼容口径)
        tid = await new_profile()
        r = await svc.record_event(
            tid, "L1", "legal_record", -20.0,
            severity="general")
        violation_id = None
        for e in await svc.repo.list_events_by_trust(tid):
            if (e.get("delta") or 0) < 0:
                violation_id = e.get("eventId")
        r = await repair_svc.submit_repair(
            tid, violation_id,
            [{"kind": "legal_restitution", "value": 30.0,
              "evidence": _ev("法院执行和解证明材料原件")}],
            sources=["gov_penalty", "media"])
        record("低频违规修复不受影响",
               r["applied"] is True
               and r["recurrenceRisk"] == 0.3333
               and r["repairEfficiency"] == 1.0,
               f"risk={r.get('recurrenceRisk')} "
               f"eff={r.get('repairEfficiency')}")

        # 惯犯(同因子第 5 次违规): 修复效率减半
        tid2 = await new_profile()
        for _ in range(5):
            await svc.record_event(
                tid2, "L1", "legal_record", -10.0,
                severity="general")
        last_violation = None
        for e in await svc.repo.list_events_by_trust(tid2):
            if (e.get("delta") or 0) < 0:
                last_violation = e.get("eventId")
        r = await repair_svc.submit_repair(
            tid2, last_violation,
            [{"kind": "legal_restitution", "value": 30.0,
              "evidence": _ev("法院执行和解证明材料原件")}],
            sources=["gov_penalty", "media"])
        record("惯犯修复效率减半",
               r["applied"] is True
               and r["recurrenceRisk"] > 0.7
               and r["repairEfficiency"] == 0.5,
               f"risk={r.get('recurrenceRisk')} "
               f"eff={r.get('repairEfficiency')}")
        record("再犯留痕note",
               "再犯风险" in str(r.get("recurrenceNote")),
               str(r.get("recurrenceNote"))[:50])

        # 对照: 无惯犯(首犯)修复效率 1.0
        tid3 = await new_profile()
        await svc.record_event(
            tid3, "L1", "legal_record", -10.0)
        v3 = None
        for e in await svc.repo.list_events_by_trust(tid3):
            if (e.get("delta") or 0) < 0:
                v3 = e.get("eventId")
        r = await repair_svc.submit_repair(
            tid3, v3,
            [{"kind": "legal_restitution", "value": 30.0,
              "evidence": _ev("法院执行和解证明材料原件")}],
            sources=["gov_penalty", "media"])
        record("首犯修复效率1.0",
               r["recurrenceRisk"] == 0.3333
               and r["repairEfficiency"] == 1.0,
               f"risk={r.get('recurrenceRisk')}")


class TestDepositVoluntary:
    async def run(self):
        print("[04 存证自愿激励]")
        reset_all()
        from services.trust_radar_service import (
            TrustRadarService,
        )
        radar = TrustRadarService()

        # None 零影响(回归保护: delta==14.5 既有口径)
        tid = await new_profile()
        r = await radar.submit_deposit(
            tid, "L3", "contribution_net",
            observed=200, peer_baseline=50,
            evidence=_ev("志愿服务时长官方记录证明"
                         "材料完整"),
            summary="志愿服务(权威源公示)",
            sources=["gov_penalty", "media"])
        record("None零影响(delta口径不变)",
               r["delta"] == 14.5
               and r["voluntaryBonus"] == 1.0,
               f"delta={r['delta']} "
               f"bonus={r['voluntaryBonus']}")

        # voluntary=True → ×1.05
        tid2 = await new_profile("org")
        r = await radar.submit_deposit(
            tid2, "L3", "contribution_net",
            observed=200, peer_baseline=50,
            evidence=_ev("志愿服务时长官方记录证明"
                         "材料完整"),
            summary="志愿服务(权威源公示)",
            sources=["gov_penalty", "media"],
            voluntary=True)
        record("自愿激励×1.05",
               r["delta"] == round(14.5 * 1.05, 1)
               and r["voluntaryBonus"] == 1.05,
               f"delta={r['delta']}")
        record("自愿激励note留痕",
               "自愿披露激励" in str(
                   r.get("voluntaryNote")),
               str(r.get("voluntaryNote"))[:40])


class TestHttp:
    async def run(self):
        print("[05 HTTP 透传]")
        reset_all()
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from routes.trust_value_routes import (
            register_trust_value_routes,
        )
        app = FastAPI()
        register_trust_value_routes(app)
        client = TestClient(app)
        admin = {"X-Role": "admin"}

        # 建档
        resp = client.post("/api/trust/roles", json={
            "role": "person", "name": "p6-http",
            "idNumber": "110101p6http4321"})
        tid = resp.json().get("trustId")

        # 事件透传 consistency → 预警
        resp = client.post(
            f"/api/trust/roles/{tid}/events", json={
                "layer": "L2", "factor": "ethics_evidence",
                "delta": 20.0, "consistency": 0.1},
            headers=admin)
        body = resp.json()
        gates = body.get("uebaGates") or []
        record("事件透传consistency预警",
               resp.status_code == 200
               and len(gates) == 1
               and gates[0]["tag"]
               == "hypocrisy_alert",
               str(gates)[:60])

        # 事件透传 selfPromotion → 降权
        resp = client.post(
            f"/api/trust/roles/{tid}/events", json={
                "layer": "L3", "factor": "contribution_net",
                "delta": 20.0, "selfPromotion": 0.9},
            headers=admin)
        body = resp.json()
        gates = body.get("uebaGates") or []
        record("事件透传selfPromotion降权",
               resp.status_code == 200
               and len(gates) == 1
               and gates[0]["tag"]
               == "self_promotion_discount",
               str(gates)[:60])

        # 存证透传 voluntary → 激励
        resp = client.post("/api/trust/deposits", json={
            "trustId": tid, "layer": "L3",
            "factor": "impact_radius",
            "observed": 2000, "peerBaseline": 550,
            "evidence": _ev("公益项目影响力第三方"
                            "认证材料"),
            "summary": "公益影响力(权威源公示)",
            "sources": ["gov_penalty", "media"],
            "voluntary": True})
        body = resp.json()
        record("存证透传voluntary激励",
               resp.status_code == 200
               and body.get("applied") is True
               and body.get("voluntaryBonus") == 1.05,
               str(body.get("voluntaryBonus")))

        # 修复响应含再犯字段
        resp = client.post(
            f"/api/trust/roles/{tid}/events", json={
                "layer": "L1", "factor": "legal_record",
                "delta": -20.0}, headers=admin)
        resp = client.get(
            f"/api/trust/repairs/{tid}/plan")
        plan = resp.json()
        vid = ((plan.get("plans") or [{}])[0]
               .get("violationEventId"))
        resp = client.post("/api/trust/repairs", json={
            "trustId": tid, "violationEventId": vid,
            "repairs": [{
                "kind": "legal_restitution",
                "value": 30.0,
                "evidence": _ev("法院执行和解证明"
                                "材料原件")}],
            "sources": ["gov_penalty", "media"]})
        body = resp.json()
        record("修复响应含recurrenceRisk",
               resp.status_code == 200
               and "recurrenceRisk" in body
               and "repairEfficiency" in body,
               f"risk={body.get('recurrenceRisk')}")


async def run_all():
    await TestGateFunctions().run()
    await TestRecordEventGates().run()
    await TestRepairRecurrence().run()
    await TestDepositVoluntary().run()
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
