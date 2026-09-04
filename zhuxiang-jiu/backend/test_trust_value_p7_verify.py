"""45号·P7 真伪鉴别引擎 v2 专项测试

运行方式:
    python test_trust_value_p7_verify.py

覆盖(docs/45号_Value-UEBA真伪鉴别引擎指南.md):
    - 自适应权重档案: 行为子类型映射(kind 优先级/
      factor 映射/兜底)
    - 时序特征数学: burst_ratio(无历史/观察窗不足/
      突增计算)/temporal_score 线性映射
    - 融合数学: 加权融合(权重和归一化防御)/灰色地带
      区间判定(mock 态 LLM 不触发)
    - 风险标签: 四标签触发条件(single_source/
      content_quality_low/behavior_burst/
      performative_goodness)
    - 归因: 组件分明细+中文归因文案
    - deposit v2 E2E: 引擎字段/风险标签/灰地带行为
    - repair v2 E2E: 修复类重时序/突增刷分折损
    - v1 零影响回归保护
    - HTTP 透传 verifyMode
"""

import asyncio
import os
import sys
from datetime import UTC, datetime, timedelta

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
    import uuid
    return f"{base}({uuid.uuid4().hex[:8]})"


async def new_profile(role: str = "person") -> int:
    import uuid
    from services.trust_scoring_service import (
        TrustProfileService,
    )
    suffix = uuid.uuid4().hex[:10]
    r = await TrustProfileService().create_role(
        role, f"p7-{suffix}", f"110101{suffix}4321")
    return r["trustId"]


class TestProfiles:
    async def run(self):
        print("[01 自适应权重档案]")
        from services.trust_verify_v2 import (
            behavior_profile_of, WEIGHT_PROFILES,
        )
        record("修复kind→repair_action",
               behavior_profile_of("legal_restitution")
               == "repair_action")
        record("捐赠kind→donation",
               behavior_profile_of("charity_donation")
               == "donation")
        record("存证L3因子→volunteer",
               behavior_profile_of("deposit",
                                  factor="contribution_net")
               == "volunteer")
        record("存证L2因子→review",
               behavior_profile_of("deposit",
                                  factor="platform_conduct")
               == "review")
        record("未知兜底→review",
               behavior_profile_of("unknown") == "review")
        # 四档案权重和=1.0(模板口径)
        ok = all(abs(sum(w.values()) - 1.0) < 1e-9
                 for w in WEIGHT_PROFILES.values())
        record("四档案权重和为1", ok,
               str({k: round(sum(v.values()), 2)
                    for k, v in
                    WEIGHT_PROFILES.items()}))
        # 修复类重时序(模板: repair temporal 0.4)
        record("修复类重时序",
               WEIGHT_PROFILES["repair_action"]
               ["temporal"] == 0.4
               and WEIGHT_PROFILES["repair_action"]
               ["temporal"] > WEIGHT_PROFILES["donation"]
               ["temporal"],
               str(WEIGHT_PROFILES["repair_action"]))
        # 捐赠类重跨源(模板: donation cross_source 0.4)
        record("捐赠类重跨源",
               WEIGHT_PROFILES["donation"]
               ["cross_source"] == 0.4
               and WEIGHT_PROFILES["donation"]
               ["cross_source"] > WEIGHT_PROFILES["review"]
               ["cross_source"],
               str(WEIGHT_PROFILES["donation"]))


class TestBurst:
    async def run(self):
        print("[02 时序特征数学]")
        from services.trust_verify_v2 import (
            burst_ratio, temporal_score_of,
        )
        now = datetime.now(UTC)

        r, _ = burst_ratio([], now=now)
        record("无历史不判(ratio=1)", r == 1.0)
        # 观察窗不足
        r, note = burst_ratio(
            [(now - timedelta(days=5)).isoformat()] * 3,
            now=now)
        record("观察窗不足不判", r == 1.0
               and "观察窗不足" in note, note[:40])
        # 均匀行为(90 天 90 次, 近 7 天 7 次)→ ratio≈1
        ts = [(now - timedelta(days=d)).isoformat()
              for d in range(90)]
        r, note = burst_ratio(ts, now=now)
        record("均匀行为ratio≈1", 0.5 < r <= 1.5,
               f"ratio={r}")
        # 突增(90 天 90 次基线 + 近 7 天 60 次)
        ts2 = ts + [(now - timedelta(days=d)).isoformat()
                    for d in range(7)] * 8
        r2, _ = burst_ratio(ts2, now=now)
        record("突增行为ratio>5", r2 > 5, f"ratio={r2}")

        # temporal_score 线性映射
        record("temporal(1.0)=1.0",
               temporal_score_of(1.0)[0] == 1.0)
        record("temporal(3.0)=0.5",
               temporal_score_of(3.0)[0] == 0.5)
        record("temporal(5.0)=0.0(顶格)",
               temporal_score_of(5.0)[0] == 0.0)
        record("temporal(10.0)=0.0(夹取)",
               temporal_score_of(10.0)[0] == 0.0)


class TestFusion:
    async def run(self):
        print("[03 融合决策数学]")
        from services.trust_verify_v2 import (
            fuse_scores, verify_pipeline_v2, GRAY_ZONE,
        )
        # 加权融合
        comps = {"content": 1.0, "temporal": 0.5,
                 "cross_source": 1.0, "intent": 1.0}
        w = {"content": 0.2, "temporal": 0.4,
             "cross_source": 0.3, "intent": 0.1}
        expect = round(1.0 * 0.2 + 0.5 * 0.4
                      + 1.0 * 0.3 + 1.0 * 0.1, 4)
        record("加权融合数学",
               fuse_scores(comps, w) == expect,
               f"{fuse_scores(comps, w)} vs {expect}")
        # 非标权重防御(和≠1 归一化)
        r = fuse_scores(comps, {"content": 2.0,
                                "temporal": 2.0})
        record("非标权重归一化防御",
               0 < r <= 1, str(r))

        # v2 主入口: 双源+充分证据 → 高分通过
        v = verify_pipeline_v2(
            "legal_restitution",
            "法院执行和解证明材料(编号2026-889)",
            ["gov_penalty", "media"], "司法履行",
            event_timestamps=[])
        record("v2双源高分通过",
               v["verified"] is True
               and v["confidence"] >= 0.9,
               str(v["confidence"]))
        record("v2引擎标记", v["engine"] == "v2")
        record("v2组件分齐备",
               set(v["components"]) == {
                   "content", "temporal",
                   "cross_source", "intent"},
               str(v["components"]))
        record("v2归因输出",
               "融合分" in v["attribution"]
               and "内容鉴别" in v["attribution"],
               v["attribution"][:60])
        record("v2灰色地带不触发LLM(mock)",
               v["llmUsed"] is False
               and GRAY_ZONE == (0.3, 0.8),
               str(v["llmUsed"]))

        # 孤证 → single_source 标签 + 不通过
        v = verify_pipeline_v2(
            "legal_restitution", _ev("材料证明(编号889)"),
            ["self_deposit"], "司法履行",
            event_timestamps=[])
        record("v2孤证不通过",
               v["verified"] is False
               and "single_source" in v["riskTags"],
               str(v["riskTags"]))
        # 摆拍 → content_quality_low
        v = verify_pipeline_v2(
            "community_service", _ev("摆拍现场照片说明"),
            ["gov_penalty", "media"], "社区服务",
            event_timestamps=[])
        record("v2摆拍风险标签",
               "content_quality_low" in v["riskTags"],
               str(v["riskTags"]))
        # 表演式意图 → performative_goodness
        v = verify_pipeline_v2(
            "charity_donation", _ev("捐赠凭证(编号2026-55)"),
            ["gov_penalty", "media"], "作秀宣传稿",
            event_timestamps=[])
        record("v2表演式意图标签",
               "performative_goodness" in v["riskTags"],
               str(v["riskTags"]))


class TestDepositV2:
    async def run(self):
        print("[04 存证v2 E2E]")
        reset_all()
        from services.trust_radar_service import (
            TrustRadarService,
        )
        radar = TrustRadarService()

        # v1 默认零影响(delta==14.5 既有口径)
        tid = await new_profile()
        r = await radar.submit_deposit(
            tid, "L3", "contribution_net",
            observed=200, peer_baseline=50,
            evidence=_ev("志愿服务官方公示记录材料"),
            summary="志愿服务(权威源公示)",
            sources=["gov_penalty", "media"])
        record("v1默认零影响",
               r["delta"] == 14.5
               and r.get("verifyEngine") == "v1",
               f"delta={r['delta']} "
               f"engine={r.get('verifyEngine')}")

        # v2 激活
        tid2 = await new_profile("org")
        r = await radar.submit_deposit(
            tid2, "L3", "contribution_net",
            observed=200, peer_baseline=50,
            evidence=_ev("志愿服务官方公示记录材料"),
            summary="志愿服务(权威源公示)",
            sources=["gov_penalty", "media"],
            verify_mode="v2")
        record("v2引擎激活(volunteer档案)",
               r.get("verifyEngine") == "v2",
               str(r.get("verifyEngine")))
        record("v2存证通过",
               r["applied"] is True
               and r["confidence"] >= 0.9,
               str(r.get("confidence")))
        record("v2归因字段",
               "融合分" in str(r.get("attribution")),
               str(r.get("attribution"))[:60])
        record("v2风险标签字段",
               isinstance(r.get("riskTags"), list),
               str(r.get("riskTags")))

        # v2 孤证 → 不通过 + 标签
        tid3 = await new_profile()
        r = await radar.submit_deposit(
            tid3, "L2", "ethics_evidence",
            observed=100, peer_baseline=0,
            evidence=_ev("伦理行为自述材料(编号77)"),
            summary="自述行为",
            sources=["self_deposit"],
            verify_mode="v2")
        record("v2孤证拒收",
               r["verified"] is False
               and r["applied"] is False
               and "single_source" in (r.get("riskTags")
                                       or []),
               str(r.get("riskTags")))


class TestRepairV2:
    async def run(self):
        print("[05 修复v2 E2E]")
        reset_all()
        from services.trust_scoring_service import (
            TrustProfileService,
        )
        from services.trust_repair_service import (
            TrustRepairService,
        )
        svc = TrustProfileService()
        repair_svc = TrustRepairService()

        # v2 首犯修复(无突增) → 时序满分通过
        tid = await new_profile()
        await svc.record_event(
            tid, "L1", "legal_record", -10.0)
        v = None
        for e in await svc.repo.list_events_by_trust(tid):
            if (e.get("delta") or 0) < 0:
                v = e.get("eventId")
        r = await repair_svc.submit_repair(
            tid, v,
            [{"kind": "legal_restitution", "value": 30.0,
              "evidence": _ev("法院执行和解证明材料"
                             "原件")}],
            sources=["gov_penalty", "media"],
            verify_mode="v2")
        item = (r.get("items") or [{}])[0]
        record("v2修复引擎激活",
               item.get("verifyEngine") == "v2",
               str(item.get("verifyEngine")))
        record("v2首犯修复通过",
               r["applied"] is True
               and item.get("confidence", 0) >= 0.9,
               str(item.get("confidence")))
        record("v2修复归因",
               "融合分" in str(item.get("attribution")),
               str(item.get("attribution"))[:60])

        # v1 默认零影响(gain 口径不变: cap=10)
        tid2 = await new_profile()
        await svc.record_event(
            tid2, "L1", "legal_record", -10.0)
        v2 = None
        for e in await svc.repo.list_events_by_trust(tid2):
            if (e.get("delta") or 0) < 0:
                v2 = e.get("eventId")
        r = await repair_svc.submit_repair(
            tid2, v2,
            [{"kind": "legal_restitution", "value": 30.0,
              "evidence": _ev("法院执行和解证明材料"
                             "原件")}],
            sources=["gov_penalty", "media"])
        record("v1默认零影响(gain口径)",
               r["applied"] is True
               and r["gain"] == 10.0
               and r["recurrenceRisk"] == 0.3333,
               f"gain={r['gain']}")

        # v2 突增刷分 → 时序折损(behavior_burst 标签)
        tid3 = await new_profile()
        # 灌 90 天均匀 30 次事件 + 近 7 天 30 次突增
        from datetime import timedelta as td
        from core.helpers import ts as _ts
        repo = svc.repo
        for d in range(90):
            await repo.save_event({
                "eventId": await repo.next_event_id(),
                "trustId": tid3, "layer": "L2",
                "factor": "ethics_evidence",
                "delta": 0.0, "severity": "general",
                "source": "manual",
                "summary": "历史行为",
                "ts": (datetime.now(UTC)
                       - td(days=d)).isoformat()})
        for _ in range(30):
            await repo.save_event({
                "eventId": await repo.next_event_id(),
                "trustId": tid3, "layer": "L2",
                "factor": "ethics_evidence",
                "delta": 0.0, "severity": "general",
                "source": "manual",
                "summary": "近期密集行为",
                "ts": (datetime.now(UTC)
                       - td(days=1)).isoformat()})
        await svc.record_event(
            tid3, "L1", "legal_record", -10.0)
        v3 = None
        for e in await repo.list_events_by_trust(tid3):
            if e.get("factor") == "legal_record" \
                    and (e.get("delta") or 0) < 0:
                v3 = e.get("eventId")
        r = await repair_svc.submit_repair(
            tid3, v3,
            [{"kind": "legal_restitution", "value": 30.0,
              "evidence": _ev("法院执行和解证明材料"
                             "原件")}],
            sources=["gov_penalty", "media"],
            verify_mode="v2")
        item = (r.get("items") or [{}])[0]
        record("v2突增行为折损",
               item.get("confidence", 1.0) < 0.9,
               f"conf={item.get('confidence')}")
        record("v2突增风险标签",
               "behavior_burst" in (item.get("riskTags")
                                    or []),
               str(item.get("riskTags")))


class TestHttp:
    async def run(self):
        print("[06 HTTP 透传]")
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

        resp = client.post("/api/trust/roles", json={
            "role": "person", "name": "p7-http",
            "idNumber": "110101p7http4321"})
        tid = resp.json().get("trustId")

        # 存证 v2 透传
        resp = client.post("/api/trust/deposits", json={
            "trustId": tid, "layer": "L3",
            "factor": "contribution_net",
            "observed": 200, "peerBaseline": 50,
            "evidence": _ev("志愿服务官方公示记录材料"),
            "summary": "志愿服务(权威源公示)",
            "sources": ["gov_penalty", "media"],
            "verifyMode": "v2"})
        body = resp.json()
        record("存证v2透传",
               resp.status_code == 200
               and body.get("verifyEngine") == "v2",
               str(body.get("verifyEngine")))

        # 修复 v2 透传
        resp = client.post(
            f"/api/trust/roles/{tid}/events", json={
                "layer": "L1", "factor": "legal_record",
                "delta": -20.0}, headers=admin)
        resp = client.get(
            f"/api/trust/repairs/{tid}/plan")
        vid = ((resp.json().get("plans") or [{}])[0]
               .get("violationEventId"))
        resp = client.post("/api/trust/repairs", json={
            "trustId": tid, "violationEventId": vid,
            "repairs": [{
                "kind": "legal_restitution",
                "value": 30.0,
                "evidence": _ev("法院执行和解证明"
                                "材料原件")}],
            "sources": ["gov_penalty", "media"],
            "verifyMode": "v2"})
        body = resp.json()
        item = (body.get("items") or [{}])[0]
        record("修复v2透传",
               resp.status_code == 200
               and item.get("verifyEngine") == "v2",
               str(item.get("verifyEngine")))

        # 默认 v1(不传 verifyMode)
        resp = client.post(
            f"/api/trust/roles/{tid}/events", json={
                "layer": "L1", "factor": "legal_record",
                "delta": -20.0}, headers=admin)
        resp = client.get(
            f"/api/trust/repairs/{tid}/plan")
        vid2 = ((resp.json().get("plans") or [{}])[0]
                .get("violationEventId"))
        resp = client.post("/api/trust/repairs", json={
            "trustId": tid, "violationEventId": vid2,
            "repairs": [{
                "kind": "legal_restitution",
                "value": 30.0,
                "evidence": _ev("法院执行和解证明"
                                "材料原件")}],
            "sources": ["gov_penalty", "media"]})
        item = ((resp.json().get("items") or [{}])[0])
        record("缺省v1零影响",
               item.get("verifyEngine") == "v1",
               str(item.get("verifyEngine")))


async def run_all():
    await TestProfiles().run()
    await TestBurst().run()
    await TestFusion().run()
    await TestDepositV2().run()
    await TestRepairV2().run()
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
