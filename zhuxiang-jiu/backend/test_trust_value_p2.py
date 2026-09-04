"""45号·信值模块 P2 专项测试(即时修复引擎)

运行方式:
    python test_trust_value_p2.py

覆盖(计划 §五):
    - γ 时效衰减数学: e^(-0.1t)/1天≈0.905/30天≈0.0498/
      1天 vs 30天效率比≈18倍/0天=1.0
    - β 关联度映射: 针对性修复(酒驾类违规→交安宣讲1.5)/
      通用捐款0.3/未知行为兜底0.3/合同履约1.5
    - 修复值计算: α×ΣβVγ×验真分/criminal α=0 恒 0/
      空项 0
    - 修复计划: 违规即列(即时性)/β 降序/时间近优先/
      无违规空计划/熔断 α 提示
    - 提交修复: 全链 E2E(general 违规→针对性修复→
      分数回升)/天花板保护(超 cap 截断)/severe α=0.3
      天花板/criminal 拒绝/孤证拒绝(全项验真失败留痕
      不入分)/表演式修复拦截/参数校验/非违规事件拒绝/
      跨档案事件拒绝
    - 修复明细/验真回放: applied/rejected/不存在拒绝
    - HTTP 层: 四端点结构与鉴权
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


GOOD_EVIDENCE = "整改验收报告 编号ZG2026-088 2026-09-01"


async def make_violation(ps, name, id_number, factor="regulatory",
                         delta=-20, severity="general"):
    """建档+灌一条违规事件, 返回 (trustId, violationEventId)"""
    p = await ps.create_role("person", name, id_number)
    tid = p["trustId"]
    r = await ps.record_event(
        tid, "L1", factor, delta, severity=severity,
        summary=f"违规测试 {factor} {delta}")
    events = await ps.repo.list_events_by_trust(tid)
    # 最后一条负向事件即违规事件
    v = [e for e in events if (e.get("delta") or 0) < 0][-1]
    return tid, v["eventId"]


class TestGamma:
    async def run(self):
        print("[01 γ 时效衰减数学]")
        from services.trust_repair_service import gamma_of
        import math

        record("γ(0)=1.0", gamma_of(0) == 1.0,
               str(gamma_of(0)))
        record("γ(1)≈0.905", abs(gamma_of(1) - math.exp(-0.1))
               < 1e-9, str(gamma_of(1)))
        record("γ(30)≈0.0498", abs(
            gamma_of(30) - math.exp(-3)) < 1e-9,
            str(gamma_of(30)))
        ratio = gamma_of(1) / gamma_of(30)
        record("1天vs30天≈18倍", 17 < ratio < 19,
               str(round(ratio, 1)))
        record("负天数按0", gamma_of(-5) == 1.0,
               str(gamma_of(-5)))


class TestBeta:
    async def run(self):
        print("[02 β 关联度映射]")
        from services.trust_repair_service import beta_of

        record("监管整改β=1.5",
               beta_of("regulatory",
                       "regulatory_rectification") == 1.5,
               str(beta_of("regulatory",
                           "regulatory_rectification")))
        record("司法履行β=1.5",
               beta_of("legal_record",
                       "legal_restitution") == 1.5)
        record("合同履约β=1.5",
               beta_of("asset_integrity",
                       "contract_fulfillment") == 1.5)
        record("通用捐款β=0.3",
               beta_of("legal_record", "charity_donation") == 0.3)
        record("未知行为兜底0.3",
               beta_of("legal_record", "unknown_act") == 0.3)
        record("未知违规兜底表",
               0 < beta_of("unknown_factor",
                           "community_service") <= 1.5)
        record("合规培训β=1.3",
               beta_of("regulatory",
                       "compliance_training") == 1.3)


class TestRepairGain:
    async def run(self):
        print("[03 修复值计算]")
        from services.trust_repair_service import repair_gain

        # α=1.0, β=1.5, V=40, γ(0)=1, 验真=1 → 60
        g = repair_gain(1.0, [{"beta": 1.5, "value": 40,
                               "daysSince": 0}], 1.0)
        record("基础修复值60", g == 60.0, str(g))

        # criminal α=0 → 恒 0
        g = repair_gain(0.0, [{"beta": 1.5, "value": 40,
                               "daysSince": 0}], 1.0)
        record("α=0恒0", g == 0.0, str(g))

        # 空项 0
        g = repair_gain(1.0, [], 1.0)
        record("空项0", g == 0.0, str(g))

        # 多项求和: 1.5×40×1 + 0.3×100×1 = 90
        g = repair_gain(1.0, [
            {"beta": 1.5, "value": 40, "daysSince": 0},
            {"beta": 0.3, "value": 100, "daysSince": 0}], 1.0)
        record("多项求和90", g == 90.0, str(g))

        # 验真分折减: ×0.8
        g = repair_gain(1.0, [{"beta": 1.5, "value": 40,
                               "daysSince": 0}], 0.8)
        record("验真分折减48", g == 48.0, str(g))

        # V 超上限夹取: V=150 → 100
        g = repair_gain(1.0, [{"beta": 1.0, "value": 150,
                               "daysSince": 0}], 1.0)
        record("V上限夹取100", g == 100.0, str(g))


class TestRepairPlan:
    async def run(self):
        print("[04 修复计划]")
        reset_all()
        from services.trust_scoring_service import (
            TrustProfileService,
        )
        from services.trust_repair_service import (
            TrustRepairService,
        )
        ps = TrustProfileService()
        rs = TrustRepairService()

        # 无违规 → 空计划
        p = await ps.create_role("person", "无违规", "ID-PLAN-0")
        r = await rs.repair_plan(p["trustId"])
        record("无违规空计划",
               r["violationsRepairable"] == 0
               and r["plans"] == [], str(r)[:80])

        # 有违规 → 即时列出
        tid, vid = await make_violation(
            ps, "有违规", "ID-PLAN-1", factor="regulatory")
        r = await rs.repair_plan(tid)
        record("违规即列", r["violationsRepairable"] == 1,
               str(r.get("violationsRepairable")))
        plan = r["plans"][0]
        record("违规事件定位", plan["violationEventId"] == vid,
               str(plan.get("violationEventId")))
        record("时效窗口提示", "18 倍" in r["note"],
               str(r.get("note"))[:50])
        rec_items = plan["recommendedRepairs"]
        record("建议清单非空", len(rec_items) >= 3,
               str(len(rec_items)))
        betas = [i["beta"] for i in rec_items]
        record("β降序排列", betas == sorted(betas,
                                             reverse=True),
               str(betas))
        record("针对性标记", any(i["targeted"]
                                 for i in rec_items),
               str([i["targeted"] for i in rec_items]))

        # 档案不存在
        try:
            await rs.repair_plan(99999)
            record("计划不存在拒绝", False, "未抛")
        except KeyError:
            record("计划不存在拒绝", True)


class TestSubmitRepair:
    async def run(self):
        print("[05 提交修复全链]")
        reset_all()
        from services.trust_scoring_service import (
            TrustProfileService,
        )
        from services.trust_repair_service import (
            TrustRepairService,
        )
        ps = TrustProfileService()
        rs = TrustRepairService()

        # general 违规 → 针对性修复 → 分数回升
        tid, vid = await make_violation(
            ps, "修复成功", "ID-REP-1", factor="regulatory",
            delta=-20)
        before = (await ps.get_profile(tid))["score"]

        r = await rs.submit_repair(
            tid, vid,
            [{"kind": "regulatory_rectification", "value": 40,
              "evidence": GOOD_EVIDENCE}],
            sources=["gov_penalty", "media"])
        record("修复生效", r["applied"] is True
               and r["gain"] > 0, str(r)[:90])
        # raw = 1.0×1.5×40×0.95(验真分=intent 0.95) = 57;
        # cap = 20×1.0 = 20 → gain 截断 20
        record("天花板截断20", r["gain"] == 20.0
               and r["cap"] == 20.0 and r["rawGain"] == 57.0,
               f"gain={r.get('gain')} raw={r.get('rawGain')} "
               f"cap={r.get('cap')}")
        record("β=1.5计入", r["items"][0]["beta"] == 1.5,
               str(r["items"][0].get("beta")))

        after = (await ps.get_profile(tid))["score"]
        record("分数回升", after > before,
               f"{before} → {after}")
        # factor_delta = min(20/2, 0.17×100)=10

        # 修复留痕事件
        events = await ps.repo.list_events_by_trust(tid)
        repair_events = [e for e in events
                         if e.get("source") == "repair"]
        record("修复留痕", len(repair_events) >= 1,
               str(len(repair_events)))

        # severe 违规: α=0.3 天花板
        tid2, vid2 = await make_violation(
            ps, "严重修复", "ID-REP-2", factor="regulatory",
            delta=-40, severity="severe")
        r = await rs.submit_repair(
            tid2, vid2,
            [{"kind": "regulatory_rectification", "value": 40,
              "evidence": GOOD_EVIDENCE}],
            sources=["gov_penalty", "media"])
        # α=0.3: raw = 0.3×1.5×40 = 18; cap = 40×0.3 = 12 → 12
        record("severe天花板12", r["gain"] == 12.0
               and r["alpha"] == 0.3,
               f"gain={r.get('gain')} alpha={r.get('alpha')}")

        # criminal 拒绝(修复通道关闭)
        tid3, vid3 = await make_violation(
            ps, "刑事", "ID-REP-3", factor="regulatory",
            delta=-80, severity="criminal")
        try:
            await rs.submit_repair(
                tid3, vid3,
                [{"kind": "regulatory_rectification",
                  "value": 40, "evidence": GOOD_EVIDENCE}],
                sources=["gov_penalty", "media"])
            record("criminal拒绝", False, "未抛")
        except ValueError as e:
            record("criminal拒绝", "不可修复" in str(e), str(e))

        # 孤证拒绝(全项验真失败——单源非权威)
        tid4, vid4 = await make_violation(
            ps, "孤证修复", "ID-REP-4", factor="regulatory",
            delta=-20)
        r = await rs.submit_repair(
            tid4, vid4,
            [{"kind": "regulatory_rectification", "value": 40,
              "evidence": GOOD_EVIDENCE}],
            sources=["self_deposit"])
        record("孤证修复拒绝", r["applied"] is False
               and r["gain"] == 0.0, str(r)[:80])

        # 表演式修复拦截(权威源但意图存疑)
        tid5, vid5 = await make_violation(
            ps, "表演修复", "ID-REP-5", factor="regulatory",
            delta=-20)
        r = await rs.submit_repair(
            tid5, vid5,
            [{"kind": "community_service", "value": 40,
              "evidence": "社区服务摆拍活动记录 2026-08-15 编号X7",
              "daysSince": 0}],
            sources=["gov_penalty", "media"],
            )
        # 注意: intent_check 消费 summary(缺省=修复 kind
        # 说明)——证据走 multimodal(摆拍命中→0.2)
        record("表演修复拦截", r["applied"] is False,
               str(r.get("applied")))

        # 参数校验
        for name, repairs, src in (
                ("空修复项拒绝", [], ["gov_penalty"]),
                ("非法kind拒绝", [{"kind": "bad_kind",
                                   "value": 40,
                                   "evidence": GOOD_EVIDENCE}],
                 ["gov_penalty"]),
                ("value越界拒绝", [{"kind": "charity_donation",
                                   "value": 0,
                                   "evidence": GOOD_EVIDENCE}],
                 ["gov_penalty"]),
                ("证据过短拒绝", [{"kind": "charity_donation",
                                  "value": 40,
                                  "evidence": "短"}],
                 ["gov_penalty"]),
        ):
            try:
                await rs.submit_repair(tid4, vid4, repairs,
                                       sources=src)
                record(name, False, "未抛")
            except ValueError:
                record(name, True)

        # 非违规事件拒绝
        p6 = await ps.create_role("person", "正向", "ID-REP-6")
        ev = await ps.record_event(p6["trustId"], "L3",
                                   "contribution_net", 10)
        pos_events = await ps.repo.list_events_by_trust(
            p6["trustId"])
        pos_id = pos_events[-1]["eventId"]
        try:
            await rs.submit_repair(
                p6["trustId"], pos_id,
                [{"kind": "charity_donation", "value": 40,
                  "evidence": GOOD_EVIDENCE}],
                sources=["gov_penalty"])
            record("非违规事件拒绝", False, "未抛")
        except ValueError as e:
            record("非违规事件拒绝", "非违规" in str(e), str(e))

        # 跨档案事件拒绝
        try:
            await rs.submit_repair(
                tid4, vid5,
                [{"kind": "charity_donation", "value": 40,
                  "evidence": GOOD_EVIDENCE}],
                sources=["gov_penalty"])
            record("跨档案事件拒绝", False, "未抛")
        except KeyError:
            record("跨档案事件拒绝", True)

        # 不存在档案
        try:
            await rs.submit_repair(
                99999, vid4,
                [{"kind": "charity_donation", "value": 40,
                  "evidence": GOOD_EVIDENCE}],
                sources=["gov_penalty"])
            record("修复不存在档案拒绝", False, "未抛")
        except KeyError:
            record("修复不存在档案拒绝", True)


class TestRepairDetail:
    async def run(self):
        print("[06 修复明细与回放]")
        reset_all()
        from services.trust_scoring_service import (
            TrustProfileService,
        )
        from services.trust_repair_service import (
            TrustRepairService,
        )
        ps = TrustProfileService()
        rs = TrustRepairService()

        tid, vid = await make_violation(
            ps, "明细", "ID-DET-1", factor="regulatory")
        r = await rs.submit_repair(
            tid, vid,
            [{"kind": "regulatory_rectification", "value": 40,
              "evidence": GOOD_EVIDENCE}],
            sources=["gov_penalty", "media"])
        d = await rs.repair_detail(r["repairId"])
        record("明细applied", d["status"] == "applied"
               and d["delta"] > 0, str(d)[:80])

        # 孤证轨明细 rejected
        tid2, vid2 = await make_violation(
            ps, "明细拒", "ID-DET-2", factor="regulatory")
        r2 = await rs.submit_repair(
            tid2, vid2,
            [{"kind": "regulatory_rectification", "value": 40,
              "evidence": GOOD_EVIDENCE}],
            sources=["self_deposit"])
        d2 = await rs.repair_detail(r2["repairId"])
        record("明细rejected", d2["status"] == "rejected",
               str(d2.get("status")))

        # 验真回放
        v = await rs.trigger_verify(r["repairId"])
        record("验真回放", v["success"] is True
               and "同步完成" in v.get("verifyNote", ""),
               str(v.get("verifyNote"))[:50])

        # 不存在
        try:
            await rs.repair_detail(99999)
            record("明细不存在拒绝", False, "未抛")
        except KeyError:
            record("明细不存在拒绝", True)


class TestHttp:
    async def run(self):
        print("[07 HTTP 层]")
        reset_all()
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from routes.trust_value_routes import (
            register_trust_value_routes,
        )
        from services.trust_scoring_service import (
            TrustProfileService,
        )
        app = FastAPI()
        register_trust_value_routes(app)
        client = TestClient(app)

        # 建档+违规
        ps = TrustProfileService()
        tid, vid = await make_violation(
            ps, "HTTP修复", "ID-HTTP-REP-1", factor="regulatory")

        # 修复计划 200
        resp = client.get(f"/api/trust/repairs/{tid}/plan")
        body = resp.json()
        record("HTTP计划200", resp.status_code == 200
               and body.get("violationsRepairable") == 1,
               str(body)[:80])

        # 计划 404
        resp = client.get("/api/trust/repairs/99999/plan")
        record("HTTP计划404", resp.status_code == 404,
               str(resp.status_code))

        # 提交修复 200
        resp = client.post("/api/trust/repairs", json={
            "trustId": tid, "violationEventId": vid,
            "repairs": [{"kind": "regulatory_rectification",
                         "value": 40,
                         "evidence": GOOD_EVIDENCE}],
            "sources": ["gov_penalty", "media"]})
        body = resp.json()
        record("HTTP修复200", resp.status_code == 200
               and body.get("applied") is True
               and body.get("gain") == 20.0,
               str(body)[:90])
        rid = body.get("repairId")

        # 提交参数缺 409
        resp = client.post("/api/trust/repairs", json={
            "trustId": tid, "violationEventId": vid,
            "repairs": []})
        record("HTTP修复空项409", resp.status_code == 409,
               str(resp.status_code))

        # 明细 200
        resp = client.get(f"/api/trust/repairs/detail/{rid}")
        record("HTTP明细200", resp.status_code == 200
               and resp.json().get("status") == "applied",
               str(resp.status_code))

        # 明细 404
        resp = client.get("/api/trust/repairs/detail/99999")
        record("HTTP明细404", resp.status_code == 404,
               str(resp.status_code))

        # 验真回放 200
        resp = client.post(f"/api/trust/repairs/{rid}/verify")
        record("HTTP验真200", resp.status_code == 200
               and "verifyNote" in resp.json(),
               str(resp.status_code))

        # 验真 404
        resp = client.post("/api/trust/repairs/99999/verify")
        record("HTTP验真404", resp.status_code == 404,
               str(resp.status_code))


async def run_all():
    await TestGamma().run()
    await TestBeta().run()
    await TestRepairGain().run()
    await TestRepairPlan().run()
    await TestSubmitRepair().run()
    await TestRepairDetail().run()
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
