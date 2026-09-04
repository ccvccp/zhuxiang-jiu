"""47号·L2/L3 信值验真风控 P0 专项测试(角色风险画像)

运行方式:
    python test_trust_risk_p0.py

覆盖(计划 §三):
    - EMA 数学: 更新公式/边界夹取/连续命中收敛
    - 信任分层: trustLevel=1−riskEMA/四档边界
    - 单事件风险分: 信号计数封顶/组件折算封顶/取大
    - 画像回流管道: P6 守门命中沉淀/P7 组件沉淀/
      verified 通过不加分(职责分离)/hitCounts 累计/
      riskHistory 滚动截断
    - 回流零侵入: 画像服务异常主流程照常(fail-soft)
    - 人工校准: 覆盖生效/理由必填/越界拒绝/清除恢复/
      校准不被回流冲掉
    - 画像视图: 未建档 404/排行分层统计
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
        role, f"r0-{suffix}", f"110101{suffix}4321")
    return r["trustId"]


class TestMath:
    async def run(self):
        print("[01 EMA 与分层数学]")
        from services.trust_risk_profile_service import (
            update_ema, trust_level_of, tier_of,
            risk_event_score,
        )
        record("EMA首次(0→1.0)=0.2",
               update_ema(0.0, 1.0) == 0.2)
        record("EMA连续收敛(5次1.0)",
               round(update_ema(
                   update_ema(update_ema(
                       update_ema(update_ema(
                           0.0, 1.0), 1.0), 1.0), 1.0),
                   1.0), 4) == 0.6723,
               str(update_ema(update_ema(update_ema(
                   update_ema(update_ema(
                       0.0, 1.0), 1.0), 1.0), 1.0), 1.0)))
        record("EMA零风险回落",
               update_ema(0.5, 0.0) == 0.4,
               str(update_ema(0.5, 0.0)))
        record("EMA边界夹取",
               update_ema(2.0, 2.0) == 1.0)
        record("trustLevel=1−risk",
               trust_level_of(0.36) == 0.64
               and trust_level_of(0.0) == 1.0)
        for risk, tier in ((0.1, "trusted"),
                          (0.35, "standard"),
                          (0.6, "watched"),
                          (0.95, "restricted")):
            record(f"分层边界({tier})",
                   tier_of(trust_level_of(risk)) == tier,
                   f"risk={risk}")
        # 单事件风险分
        record("单信号=1.0",
               risk_event_score(["hypocrisy"]) == 1.0)
        record("多信号封顶1.0",
               risk_event_score(["hypocrisy",
                                 "self_promotion",
                                 "recurrence"]) == 1.0)
        record("组件两低维=0.5",
               risk_event_score(components={
                   "content": 0.3, "temporal": 0.4,
                   "cross_source": 0.9,
                   "intent": 0.9}) == 0.5)
        record("组件全高=0",
               risk_event_score(components={
                   "content": 0.8, "temporal": 0.8,
                   "cross_source": 0.9,
                   "intent": 0.95}) == 0.0)
        record("组件四低封顶1.0",
               risk_event_score(components={
                   "content": 0.1, "temporal": 0.1,
                   "cross_source": 0.1,
                   "intent": 0.1}) == 1.0)
        record("取大语义(信号>组件)",
               risk_event_score(
                   ["hypocrisy"],
                   {"content": 0.9, "temporal": 0.9,
                    "cross_source": 0.9,
                    "intent": 0.9}) == 1.0)
        record("未知信号不计",
               risk_event_score(["unknown_sig"]) == 0.0)


class TestBackflow:
    async def run(self):
        print("[02 回流管道]")
        reset_all()
        from services.trust_scoring_service import (
            TrustProfileService,
        )
        from services.trust_risk_profile_service import (
            TrustRiskProfileService,
        )
        svc = TrustProfileService()
        risk_svc = TrustRiskProfileService()

        # P6 守门命中 → 画像沉淀
        tid = await new_profile()
        r = await svc.record_event(
            tid, "L2", "ethics_evidence", 20.0,
            consistency=0.1)
        profile = await risk_svc.repo.get_profile(tid)
        record("守门命中沉淀画像",
               profile is not None
               and (profile.get("hitCounts") or {})
               .get("hypocrisy") == 1,
               str(profile)[:70])
        record("风险指数上升",
               float(profile.get("riskEMA") or 0) == 0.2,
               str(profile.get("riskEMA")))
        record("事件计数",
               profile.get("eventCount") == 1,
               str(profile.get("eventCount")))

        # 守门不命中(consistency 正常) → 零风险沉淀
        tid2 = await new_profile()
        await svc.record_event(
            tid2, "L2", "ethics_evidence", 20.0,
            consistency=0.9)
        profile2 = await risk_svc.repo.get_profile(tid2)
        record("正常事件零风险沉淀",
               profile2 is not None
               and float(profile2.get("riskEMA") or 0)
               == 0.0
               and profile2.get("eventCount") == 1,
               str(profile2)[:70] if profile2 else "None")

        # 再犯(第 5 次违规+修复提交) → recurrence 沉淀
        # (recurrence_gate 在修复路径——单纯违规不触发)
        from services.trust_repair_service import (
            TrustRepairService,
        )
        tid3 = await new_profile()
        for _ in range(5):
            await svc.record_event(
                tid3, "L1", "legal_record", -10.0)
        last_violation = None
        for e in await svc.repo.list_events_by_trust(tid3):
            if (e.get("delta") or 0) < 0:
                last_violation = e.get("eventId")
        await TrustRepairService().submit_repair(
            tid3, last_violation,
            [{"kind": "legal_restitution", "value": 30.0,
              "evidence": _ev("法院执行和解证明材料原件")}],
            sources=["gov_penalty", "media"])
        profile3 = await risk_svc.repo.get_profile(tid3)
        record("再犯沉淀画像(修复路径)",
               (profile3.get("hitCounts") or {})
               .get("recurrence") == 1
               and float(profile3.get("riskEMA") or 0)
               > 0,
               str((profile3 or {}).get("hitCounts")))

        # P7 存证验真组件沉淀(v2 引擎)
        from services.trust_radar_service import (
            TrustRadarService,
        )
        tid4 = await new_profile()
        await TrustRadarService().submit_deposit(
            tid4, "L3", "contribution_net",
            observed=200, peer_baseline=50,
            evidence=_ev("摆拍现场照片说明材料"),
            summary="志愿服务(权威源公示)",
            sources=["gov_penalty", "media"],
            verify_mode="v2")
        profile4 = await risk_svc.repo.get_profile(tid4)
        record("P7组件沉淀画像",
               profile4 is not None
               and (profile4.get("hitCounts") or {})
               .get("content_quality_low") is None
               and float(profile4.get("riskEMA") or 0)
               > 0,
               str((profile4 or {})
                   .get("riskEMA")))

        # verified 通过不加分: 正常存证(v2) 组件全高
        tid5 = await new_profile()
        await TrustRadarService().submit_deposit(
            tid5, "L3", "contribution_net",
            observed=200, peer_baseline=50,
            evidence=_ev("志愿服务官方公示记录材料完整"),
            summary="志愿服务(权威源公示)",
            sources=["gov_penalty", "media"],
            verify_mode="v2")
        profile5 = await risk_svc.repo.get_profile(tid5)
        record("验真通过不加分(职责分离)",
               profile5 is not None
               and float(profile5.get("riskEMA") or 0)
               == 0.0,
               str((profile5 or {}).get("riskEMA")))

        # riskHistory 滚动截断(25 次事件 → 20 条)
        tid6 = await new_profile()
        for i in range(25):
            await svc.record_event(
                tid6, "L2", "ethics_evidence", 5.0,
                consistency=0.1)
        profile6 = await risk_svc.repo.get_profile(tid6)
        record("历史滚动截断20",
               len((profile6 or {})
                   .get("riskHistory") or []) == 20,
               str(len((profile6 or {})
                   .get("riskHistory") or [])))


class TestFailSoft:
    async def run(self):
        print("[03 回流零侵入]")
        reset_all()
        from services.trust_scoring_service import (
            TrustProfileService,
        )
        svc = TrustProfileService()

        # 画像服务异常 → 主流程照常
        import services.trust_risk_profile_service as rmod
        orig = rmod.TrustRiskProfileService \
            .record_risk_event

        async def _boom(self, *a, **kw):
            raise RuntimeError("画像存储瞬断")
        rmod.TrustRiskProfileService.record_risk_event \
            = _boom
        try:
            tid = await new_profile()
            r = await svc.record_event(
                tid, "L2", "ethics_evidence", 20.0,
                consistency=0.1)
            record("画像异常主流程照常",
                   r.get("score") is not None,
                   str(r)[:60])
            factors = (await svc.repo.get_profile(
                tid))["factors"]
            record("入分不受影响",
                   round(factors["ethics_evidence"]
                         - 50.0, 1) == 10.0,
                   str(factors["ethics_evidence"]))
        finally:
            rmod.TrustRiskProfileService \
                .record_risk_event = orig


class TestCalibrate:
    async def run(self):
        print("[04 人工校准]")
        reset_all()
        from services.trust_risk_profile_service import (
            TrustRiskProfileService,
        )
        svc = TrustRiskProfileService()
        tid = await new_profile()

        # 无画像可直接校准(隐式建档)
        r = await svc.calibrate(tid, 0.9, "复核确认诚信")
        record("校准覆盖生效",
               r["trustLevel"] == 0.9
               and r["calibrated"] is True,
               str(r.get("trustLevel")))
        record("计算值仍可见",
               r["trustLevelComputed"] == 1.0
               and r["trustLevel"] != r["trustLevelComputed"],
               str(r.get("trustLevelComputed")))

        # 校准不被回流冲掉
        from services.trust_scoring_service import (
            TrustProfileService,
        )
        await TrustProfileService().record_event(
            tid, "L2", "ethics_evidence", 20.0,
            consistency=0.1)
        r = await svc.get_profile(tid)
        record("回流不冲掉校准",
               r["trustLevel"] == 0.9,
               str(r.get("trustLevel")))

        # 参数校验
        for name, args in (
                ("越界拒绝", (tid, 1.5, "理由")),
                ("负值拒绝", (tid, -0.1, "理由")),
                ("空理由拒绝", (tid, 0.5, "  ")),
                ("超长理由拒绝", (tid, 0.5, "x" * 301)),
        ):
            try:
                await svc.calibrate(*args)
                record(name, False, "未抛")
            except ValueError:
                record(name, True)

        # 清除校准
        r = await svc.clear_calibration(tid, "复核更新")
        record("清除回到计算值",
               r["calibrated"] is False
               and r["trustLevel"]
               == r["trustLevelComputed"],
               str(r.get("trustLevel")))
        record("清除留痕",
               "[清除]" in str(r.get("calibrateNote")),
               str(r.get("calibrateNote"))[:40])


class TestViews:
    async def run(self):
        print("[05 画像视图]")
        reset_all()
        from services.trust_scoring_service import (
            TrustProfileService,
        )
        from services.trust_risk_profile_service import (
            TrustRiskProfileService,
        )
        svc = TrustProfileService()
        risk_svc = TrustRiskProfileService()

        # 风险角色 vs 干净角色
        tid_risk = await new_profile()
        for _ in range(3):
            await svc.record_event(
                tid_risk, "L2", "ethics_evidence", 20.0,
                consistency=0.1)
        tid_clean = await new_profile()
        await svc.record_event(
            tid_clean, "L2", "ethics_evidence", 20.0,
            consistency=0.9)

        r = await risk_svc.get_profile(tid_risk)
        record("风险画像视图",
               r["trustId"] == tid_risk
               and r["eventCount"] == 3
               and (r["hitCounts"] or {})
               .get("hypocrisy") == 3,
               str(r)[:70])
        r2 = await risk_svc.get_profile(tid_clean)
        record("干净画像trusted",
               r2["tier"] == "trusted"
               and r2["riskEMA"] == 0.0,
               str(r2.get("tier")))

        # 排行
        r = await risk_svc.list_profiles()
        record("排行最高风险在前",
               r["total"] == 2
               and (r["profiles"][0]["trustId"]
                    == tid_risk),
               str(r.get("total")))
        record("分层统计",
               (r["byTier"] or {}).get("trusted") == 1,
               str(r.get("byTier")))

        # 未建档 404
        try:
            await risk_svc.get_profile(99999)
            record("未建档404", False, "未抛")
        except KeyError:
            record("未建档404", True)


class TestHttp:
    async def run(self):
        print("[06 HTTP 层]")
        reset_all()
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from routes.trust_risk_routes import (
            register_trust_risk_routes,
        )
        app = FastAPI()
        register_trust_risk_routes(app)
        client = TestClient(app)
        admin = {"X-Role": "admin"}

        # 建档
        from routes.trust_value_routes import (
            register_trust_value_routes,
        )
        register_trust_value_routes(app)
        resp = client.post("/api/trust/roles", json={
            "role": "person", "name": "r0-http",
            "idNumber": "110101r0http4321"})
        tid = resp.json().get("trustId")

        # 鉴权
        resp = client.get(f"/api/trust/risk/{tid}")
        record("画像缺Role403", resp.status_code == 403,
               str(resp.status_code))
        resp = client.get("/api/trust/risk")
        record("排行缺Role403", resp.status_code == 403,
               str(resp.status_code))

        # 画像 200(触发守门事件沉淀画像供排行)
        resp = client.post(
            f"/api/trust/roles/{tid}/events", json={
                "layer": "L2", "factor": "ethics_evidence",
                "delta": 20.0, "consistency": 0.1},
            headers=admin)
        resp = client.get(f"/api/trust/risk/{tid}",
                          headers=admin)
        body = resp.json()
        record("画像200", resp.status_code == 200
               and body.get("trustId") == tid
               and body.get("tier") == "trusted",
               str(body)[:70])

        # 排行 200
        resp = client.get("/api/trust/risk", headers=admin)
        record("排行200", resp.status_code == 200
               and resp.json().get("total") == 1,
               str(resp.json().get("total")))

        # 校准 200
        resp = client.post(
            f"/api/trust/risk/{tid}/calibrate",
            json={"trustLevel": 0.4,
                  "note": "HTTP 校准测试"}, headers=admin)
        body = resp.json()
        record("校准200", resp.status_code == 200
               and body.get("trustLevel") == 0.4,
               str(body)[:60])
        # 校准生效于画像视图
        resp = client.get(f"/api/trust/risk/{tid}",
                          headers=admin)
        record("校准反映画像",
               resp.json().get("tier") == "watched",
               str(resp.json().get("tier")))

        # 校准参数 409
        resp = client.post(
            f"/api/trust/risk/{tid}/calibrate",
            json={"trustLevel": 2.0, "note": "x"},
            headers=admin)
        record("校准越界409", resp.status_code == 409,
               str(resp.status_code))
        resp = client.post(
            f"/api/trust/risk/{tid}/calibrate",
            json={"note": "x"}, headers=admin)
        record("缺字段409", resp.status_code == 409,
               str(resp.status_code))

        # 清除校准 200
        resp = client.post(
            f"/api/trust/risk/{tid}/calibrate/clear",
            json={"note": "HTTP 清除"}, headers=admin)
        record("清除校准200",
               resp.status_code == 200
               and resp.json().get("calibrated")
               is False,
               str(resp.status_code))

        # 未建档 404
        resp = client.get("/api/trust/risk/99999",
                          headers=admin)
        record("画像404", resp.status_code == 404,
               str(resp.status_code))


async def run_all():
    await TestMath().run()
    await TestBackflow().run()
    await TestFailSoft().run()
    await TestCalibrate().run()
    await TestViews().run()
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
