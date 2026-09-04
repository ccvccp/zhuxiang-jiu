"""47号·L2/L3 信值验真风控 P3 专项测试
(先验回流与复核通道)

运行方式:
    python test_trust_risk_p3.py

覆盖(计划 §六):
    - 验真起点折扣数学: trust_prior 融合(prior×0.3+
      fusion×0.7)/None 零影响/边界夹取/收窄(拒收)与
      加速(通过)/归因呈现
    - 入分守门乘性: RISK_PRIOR_MODE 开关/四档乘数/
      红线② L1 不折损/零 delta 不触发/入口守门叠乘
      封底 ×0.4/命中入口守门不加速/信任加速×自愿
      封顶 ×1.15/负向不折损
    - 验真先验 E2E: v2+restricted 起点折扣拒收 → 证据
      质量爬回; 回包 trustPrior/riskPriorGate 呈现
    - 复核通道: 申诉创建(快照留痕)/理由校验/单待复核
      防刷/决定(approve→校准/reject→维持)/重复决定拒绝/
      未知复核 404
    - 零影响与 fail-soft: 默认 off 语义/画像读取异常
      存证照常
    - HTTP 层: 复核端点鉴权(申诉开放/决定 admin)/
      模式开关语义
"""

import asyncio
import os
import sys
import uuid

os.environ["LOCK_MODE"] = "asyncio"
os.environ["STORE_MODE"] = "asyncio"
os.environ.pop("LLM_API_KEY", None)
os.environ["LLM_ENABLED"] = "off"
os.environ["RISK_PRIOR_MODE"] = "off"

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


def set_mode(value: str):
    os.environ["RISK_PRIOR_MODE"] = value


def reset_all():
    from repositories.store import reset_store as _reset
    _reset()


def _ev(base: str) -> str:
    """唯一证据(带 uuid 后缀——防指纹重放误命中)"""
    return f"{base}({uuid.uuid4().hex[:8]})"


async def new_profile(role: str = "person") -> int:
    from services.trust_scoring_service import (
        TrustProfileService,
    )
    suffix = uuid.uuid4().hex[:10]
    r = await TrustProfileService().create_role(
        role, f"r3-{suffix}", f"110101{suffix}4321")
    return r["trustId"]


async def calibrate(tid: int, level: float):
    from services.trust_risk_profile_service import (
        TrustRiskProfileService,
    )
    await TrustRiskProfileService().calibrate(
        tid, level, "P3 测试分层设定")


async def _deposit(tid: int, evidence: str, *,
                   layer: str = "L2",
                   factor: str = "ethics_evidence",
                   observed: float = 200,
                   baseline: float = 50,
                   verify_mode: str = "v1",
                   voluntary=None) -> dict:
    from services.trust_radar_service import (
        TrustRadarService,
    )
    return await TrustRadarService().submit_deposit(
        tid, layer, factor, observed=observed,
        peer_baseline=baseline, evidence=evidence,
        summary="志愿服务(权威源公示)",
        sources=["gov_penalty", "media"],
        voluntary=voluntary,
        verify_mode=verify_mode)


BASE_DELTA = 14.5   # observed 200 / baseline 50 → net 145


class TestPriorMath:
    async def run(self):
        print("[01 验真起点折扣数学]")
        from services.trust_verify_v2 import (
            verify_pipeline_v2, _blend_trust_prior,
            TRUST_PRIOR_WEIGHT,
        )
        good = {"evidence": "社区公益帮扶活动完整公示材料2026",
                "sources": ["gov_penalty", "media"],
                "summary": "志愿服务(权威源公示)"}

        # None 零影响
        r0 = verify_pipeline_v2("deposit", **good)
        r1 = verify_pipeline_v2("deposit", trust_prior=None,
                                **good)
        record("prior=None零影响",
               r0["confidence"] == r1["confidence"]
               and r0["trustPrior"]["applied"] is False
               and r0["trustPrior"]["value"] is None,
               f"{r0['confidence']} vs {r1['confidence']}")
        record("None零影响verified不变",
               r0["verified"] == r1["verified"])

        # 融合数学
        record("融合数学(prior+fusion)",
               _blend_trust_prior(1.0, 0.3) == 0.79
               and _blend_trust_prior(0.7, 1.0) == 0.79
               and _blend_trust_prior(0.89, 0.2) == 0.683,
               str(_blend_trust_prior(0.89, 0.2)))

        # 边界夹取
        record("prior边界夹取[0,1]",
               _blend_trust_prior(1.0, 2.0) == 1.0
               and _blend_trust_prior(1.0, -0.5) == 0.7)

        # 收窄: restricted 先验 + 边际证据 → 拒收
        marginal = {"evidence": "社区公益帮扶活动完整公示材料",
                    "sources": ["gov_penalty", "media"],
                    "summary": "志愿服务(权威源公示)"}
        r_off = verify_pipeline_v2("deposit", **marginal)
        r_on = verify_pipeline_v2(
            "deposit", trust_prior=0.2, **marginal)
        record("收窄: 边际证据从过到拒",
               r_off["verified"] is True
               and r_on["verified"] is False,
               f"{r_off['confidence']}→{r_on['confidence']}")

        # 加速: trusted 先验 → 置信度上移
        r_acc = verify_pipeline_v2(
            "deposit", trust_prior=1.0, **marginal)
        record("加速: 置信度上移",
               r_acc["confidence"] > r_off["confidence"]
               and r_acc["verified"] is True,
               f"{r_acc['confidence']}")

        # 回包呈现
        record("回包trustPrior呈现",
               r_on["trustPrior"]["applied"] is True
               and r_on["trustPrior"]["value"] == 0.2
               and r_on["trustPrior"]["weight"]
               == TRUST_PRIOR_WEIGHT,
               str(r_on["trustPrior"]))
        record("归因含信任先验",
               "信任先验" in r_on["attribution"],
               r_on["attribution"][-40:])
        record("组件分不受先验影响",
               r_on["components"] == r_off["components"])


class TestDeltaGate:
    async def run(self):
        print("[02 入分守门乘性]")
        reset_all()
        from services.trust_risk_profile_service import (
            prior_mode_enabled,
        )

        # 默认 off 零影响(restricted 档 delta 不折)
        set_mode("off")
        record("默认off", prior_mode_enabled() is False)
        tid = await new_profile()
        await calibrate(tid, 0.2)   # restricted
        r = await _deposit(tid, _ev("默认关闭语义测试证据材料"))
        record("off零影响(restricted不折)",
               r["delta"] == BASE_DELTA
               and r["riskPriorGate"] is None,
               f"delta={r.get('delta')}")

        # on: 四档乘数
        set_mode("on")
        cases = [(0.2, "restricted", 0.5),
                 (0.4, "watched", 0.8),
                 (0.6, "standard", 1.0),
                 (0.9, "trusted", 1.1)]
        for level, tier, gate in cases:
            t = await new_profile()
            await calibrate(t, level)
            r = await _deposit(t, _ev(f"{tier}档乘数测试证据材料"))
            record(f"{tier}档×{gate}",
                   r["delta"] == round(BASE_DELTA * gate, 1)
                   and (r["riskPriorGate"] or {}).get(
                       "gateMultiplier") == gate,
                   f"delta={r.get('delta')} "
                   f"gate={r.get('riskPriorGate')}")

        # 红线②: L1 不折损
        t = await new_profile()
        await calibrate(t, 0.2)
        r = await _deposit(
            t, _ev("L1法治数据官方公示材料2026"),
            layer="L1", factor="regulatory")
        record("L1不折损(红线②)",
               r["delta"] == BASE_DELTA
               and r["riskPriorGate"] is None,
               f"delta={r.get('delta')}")

        # 零 delta 不触发守门
        t = await new_profile()
        await calibrate(t, 0.2)
        r = await _deposit(t, _ev("净贡献为零测试证据材料"),
                           observed=50)
        record("零delta不触发守门",
               r["delta"] == 0
               and r["riskPriorGate"] is None,
               f"delta={r.get('delta')}")

        # 信任加速×自愿 封顶 1.15
        t = await new_profile()
        await calibrate(t, 0.9)
        r = await _deposit(
            t, _ev("信任加速自愿披露测试材料"),
            voluntary=True)
        record("trusted×voluntary封顶1.15",
               r["delta"] == round(BASE_DELTA * 1.15, 1)
               and r["voluntaryBonus"] == 1.05,
               f"delta={r.get('delta')}")

        # watched×voluntary 无封顶(0.8×1.05)
        t = await new_profile()
        await calibrate(t, 0.4)
        r = await _deposit(
            t, _ev("观察档自愿披露测试材料"),
            voluntary=True)
        record("watched×voluntary无封顶",
               r["delta"] == round(round(BASE_DELTA * 0.8, 1)
                                   * 1.05, 1),
               f"delta={r.get('delta')}")

        # 回包呈现
        t = await new_profile()
        await calibrate(t, 0.4)
        r = await _deposit(t, _ev("回包守门呈现测试材料"))
        record("回包riskPriorGate呈现",
               (r["riskPriorGate"] or {}).get("tier")
               == "watched"
               and (r["riskPriorGate"] or {}).get(
                   "trustLevel") == 0.4
               and (r["riskPriorGate"] or {}).get(
                   "combinedMultiplier") == 0.8,
               str(r.get("riskPriorGate")))

        # 真实事件路径(EMA 收敛 restricted): 7 次守门命中
        from services.trust_scoring_service import (
            TrustProfileService,
        )
        t = await new_profile()
        for _ in range(7):
            await TrustProfileService().record_event(
                t, "L2", "ethics_evidence", 1.0,
                consistency=0.1)
        r = await _deposit(t, _ev("EMA收敛restricted测试材料"))
        record("EMA路径restricted(×0.5)",
               r["delta"] == round(BASE_DELTA * 0.5, 1),
               f"delta={r.get('delta')}")


class TestGateFloor:
    async def run(self):
        print("[03 入口×画像叠乘封底]")
        reset_all()
        set_mode("on")

        # semantic + restricted → 封底 0.4
        # (证据须够长: 单字改动 J>0.8 需 >27 个 gram——
        #  与 P1 测试同款 uuid 后缀构造)
        u = uuid.uuid4().hex[:8]
        e1 = f"志愿服务官方公示记录材料(编号ZY2026088{u})"
        e2 = f"志愿服务官方公示记录材料(编号ZY2026089{u})"
        t = await new_profile()
        await calibrate(t, 0.2)
        await _deposit(t, e1)
        r = await _deposit(t, e2)
        record("semantic+restricted封底0.4",
               r["semanticReuse"]["hit"] is True
               and r["delta"] == round(BASE_DELTA * 0.4, 1)
               and (r["riskPriorGate"] or {}).get(
                   "combinedMultiplier") == 0.4,
               f"delta={r.get('delta')} "
               f"{r.get('riskPriorGate')}")

        # semantic + standard → 封底 0.4(总折损≤60%)
        t2 = await new_profile()
        await calibrate(t2, 0.6)
        await _deposit(t2, e1)
        r = await _deposit(t2, e2)
        record("semantic+standard封底0.4",
               r["delta"] == round(BASE_DELTA * 0.4, 1),
               f"delta={r.get('delta')}")

        # semantic + trusted → 命中入口守门不加速(0.4)
        t3 = await new_profile()
        await calibrate(t3, 0.9)
        await _deposit(t3, e1)
        r = await _deposit(t3, e2)
        record("semantic+trusted不加速(0.4)",
               r["delta"] == round(BASE_DELTA * 0.4, 1),
               f"delta={r.get('delta')}")

        # value mismatch(v2) + watched → 封底 0.4
        # (L3/volunteer 权重下 fusion 0.89, watched 0.4 起点
        #  可过验真; observed 500 → base delta 30; restricted
        #  起点则同证据整体拒收——收窄语义本体)
        t4 = await new_profile()
        await calibrate(t4, 0.4)
        r = await _deposit(
            t4, "社区公益帮扶活动完整公示材料清单公示存档备查专用材料",
            layer="L3", factor="contribution_net",
            observed=500, verify_mode="v2")
        record("value+watched封底0.4",
               r["valueMismatch"]["hit"] is True
               and r["delta"] == round(30.0 * 0.4, 1)
               and (r["riskPriorGate"] or {}).get(
                   "combinedMultiplier") == 0.4,
               f"delta={r.get('delta')} "
               f"{r.get('riskPriorGate')}")

        # restricted 先验下同证据被整体拒收(收窄本体)
        t5 = await new_profile()
        await calibrate(t5, 0.2)
        r = await _deposit(
            t5, "社区公益帮扶活动完整公示材料清单公示存档备查专用材料",
            layer="L3", factor="contribution_net",
            observed=500, verify_mode="v2")
        record("restricted先验整体拒收",
               r["verified"] is False
               and r["applied"] is False,
               f"verified={r.get('verified')}")


class TestPriorE2E:
    async def run(self):
        print("[04 验真先验 E2E(v2)]")
        reset_all()
        set_mode("on")
        marginal = "社区公益帮扶活动完整公示材料清单公示存档备查"

        async def _v2(tid, evidence):
            return await _deposit(
                tid, evidence, layer="L3",
                factor="contribution_net",
                verify_mode="v2")

        # restricted(0.2) + v2 → 起点折扣拒收
        # (volunteer 权重 fusion 0.89; 0.2×0.3+0.89×0.7=0.683)
        t = await new_profile()
        await calibrate(t, 0.2)
        r = await _v2(t, marginal)
        record("v2起点折扣拒收(restricted)",
               r["verified"] is False
               and r["confidence"] == 0.683
               and (r["trustPrior"] or {}).get("applied")
               is True
               and (r["trustPrior"] or {}).get("value")
               == 0.2,
               f"conf={r.get('confidence')} "
               f"{r.get('trustPrior')}")

        # trusted(0.9) + v2 同证据 → 通过(爬回/加速)
        t2 = await new_profile()
        await calibrate(t2, 0.9)
        r = await _v2(t2, marginal)
        record("v2信任先验通过(trusted)",
               r["verified"] is True
               and r["confidence"] == round(
                   0.9 * 0.3 + 0.89 * 0.7, 4),
               f"conf={r.get('confidence')}")

        # off + v2 → 无先验
        set_mode("off")
        t3 = await new_profile()
        await calibrate(t3, 0.2)
        r = await _v2(t3, marginal)
        record("off+v2无先验(0.89过)",
               r["verified"] is True
               and (r["trustPrior"] or {}).get("applied")
               is False
               and r["riskPriorGate"] is None,
               f"conf={r.get('confidence')}")
        set_mode("on")


class TestReviewChannel:
    async def run(self):
        print("[05 复核通道]")
        reset_all()
        set_mode("off")
        from services.trust_risk_profile_service import (
            TrustRiskProfileService,
        )
        svc = TrustRiskProfileService()
        from services.trust_scoring_service import (
            TrustProfileService,
        )

        # 画像误判场景: 4 次守门命中 → watched
        # (EMA=1−0.8⁴=0.5904 → trustLevel 0.4096)
        t = await new_profile()
        for _ in range(4):
            await TrustProfileService().record_event(
                t, "L2", "ethics_evidence", 20.0,
                consistency=0.1)

        # 申诉创建(含风险快照)
        r = await svc.submit_review_request(
            t, "互证系正常业务互助, 非团伙刷分, 申请复核")
        rid = r["reviewId"]
        record("申诉创建",
               r["success"] is True
               and r["status"] == "pending"
               and r["tierAtRequest"] == "watched"
               and r["riskEmaAtRequest"] > 0,
               str(r)[:80])

        # 理由校验
        for name, reason in (
                ("理由过短拒绝", "太短"),
                ("理由空白拒绝", "   ")):
            try:
                await svc.submit_review_request(t, reason)
                record(name, False, "未抛")
            except ValueError:
                record(name, True)

        # 重复申诉(单待复核防刷)
        try:
            await svc.submit_review_request(
                t, "再次申诉测试理由内容八个字以上")
            record("重复申诉拒绝", False, "未抛")
        except ValueError as e:
            record("重复申诉拒绝", "待复核" in str(e))

        # 未知档案 404
        try:
            await svc.submit_review_request(
                99999, "未知档案申诉测试理由内容")
            record("申诉未建档404", False, "未抛")
        except KeyError:
            record("申诉未建档404", True)

        # 画像视图呈现复核队列
        v = await svc.get_profile(t)
        record("画像含复核队列",
               v["pendingReview"] is True
               and len(v["reviewRequests"]) == 1,
               str(v.get("pendingReview")))

        # decide: approve 需 trustLevel
        try:
            await svc.decide_review(
                t, rid, True, "误判但缺目标值")
            record("approve缺目标拒绝", False, "未抛")
        except ValueError:
            record("approve缺目标拒绝", True)

        # decide: 误判确认 → 校准生效 + 留痕
        r = await svc.decide_review(
            t, rid, True, "复核确认互证系正常互助",
            trust_level=0.85)
        record("误判确认→校准生效",
               r["trustLevel"] == 0.85
               and r["calibrated"] is True
               and r["pendingReview"] is False,
               f"level={r.get('trustLevel')}")
        review = r["reviewRequests"][0]
        record("复核留痕(calibrated)",
               review["status"] == "calibrated"
               and review["calibratedTo"] == 0.85
               and review["reviewer"] == "admin",
               str(review)[:90])

        # 重复决定拒绝
        try:
            await svc.decide_review(
                t, rid, False, "再处理测试")
            record("重复决定拒绝", False, "未抛")
        except ValueError as e:
            record("重复决定拒绝", "已处理" in str(e))

        # reject 路径: 维持原判(画像不动)
        t2 = await new_profile()
        r = await svc.submit_review_request(
            t2, "第二次申诉维持原判测试理由")
        r = await svc.decide_review(
            t2, r["reviewId"], False, "证据充分, 维持")
        record("维持原判(画像不动)",
               r["reviewRequests"][0]["status"]
               == "rejected"
               and r["trustLevel"]
               == r["trustLevelComputed"],
               str(r["reviewRequests"][0])[:80])

        # 未知复核 404
        try:
            await svc.decide_review(
                t2, "rv-unknown", False, "未知")
            record("未知复核404", False, "未抛")
        except KeyError:
            record("未知复核404", True)

        # 复核后可再申诉(队列流转)
        r = await svc.submit_review_request(
            t2, "复核完成后再申诉测试理由")
        record("决定后可再申诉",
               r["status"] == "pending", str(r)[:60])


class TestFailSoft:
    async def run(self):
        print("[06 零影响与 fail-soft]")
        set_mode("on")
        reset_all()

        # 画像读取异常 → 存证照常(无守门)
        import services.trust_risk_profile_service as rmod
        orig = rmod.TrustRiskProfileService.get_profile

        async def _boom(self, *a, **kw):
            raise RuntimeError("画像存储瞬断")
        rmod.TrustRiskProfileService.get_profile = _boom
        try:
            t = await new_profile()
            await calibrate_orig(t, 0.2)
        except Exception:
            pass
        finally:
            rmod.TrustRiskProfileService.get_profile = orig
        # 注: calibrate 也走 get_profile——用事件路径建风险
        from services.trust_scoring_service import (
            TrustProfileService,
        )
        for _ in range(7):
            await TrustProfileService().record_event(
                t, "L2", "ethics_evidence", 1.0,
                consistency=0.1)
        rmod.TrustRiskProfileService.get_profile = _boom
        try:
            r = await _deposit(t, _ev("画像异常存证照常材料"))
            record("画像读取异常存证照常",
                   r["verified"] is True
                   and r["delta"] == BASE_DELTA
                   and r["riskPriorGate"] is None,
                   f"delta={r.get('delta')}")
        finally:
            rmod.TrustRiskProfileService.get_profile = orig

        # 决定/视图恢复
        v = await rmod.TrustRiskProfileService().get_profile(t)
        record("异常后画像可读",
               v["tier"] == "restricted", str(v.get("tier")))
        set_mode("off")


async def calibrate_orig(tid: int, level: float):
    from services.trust_risk_profile_service import (
        TrustRiskProfileService,
    )
    await TrustRiskProfileService().calibrate(
        tid, level, "fail-soft 前置设定")


class TestHttp:
    async def run(self):
        print("[07 HTTP 层]")
        reset_all()
        set_mode("on")
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from routes.trust_risk_routes import (
            register_trust_risk_routes,
        )
        from routes.trust_value_routes import (
            register_trust_value_routes,
        )
        app = FastAPI()
        register_trust_value_routes(app)
        register_trust_risk_routes(app)
        client = TestClient(app)
        admin = {"X-Role": "admin"}

        # 建档 + restricted 校准
        resp = client.post("/api/trust/roles", json={
            "role": "person", "name": "r3-http",
            "idNumber": f"110101r3http{uuid.uuid4().hex[:8]}"})
        tid = resp.json().get("trustId")
        resp = client.post(
            f"/api/trust/risk/{tid}/calibrate",
            json={"trustLevel": 0.2, "note": "HTTP 分层设定"},
            headers=admin)

        # 守门 E2E(HTTP)
        body = {"trustId": tid, "layer": "L2",
                "factor": "ethics_evidence",
                "observed": 200, "peerBaseline": 50,
                "evidence": _ev("HTTP守门E2E测试证据材料"),
                "summary": "志愿服务(权威源公示)",
                "sources": ["gov_penalty", "media"]}
        resp = client.post("/api/trust/deposits", json=body)
        r = resp.json()
        record("HTTP守门×0.5",
               resp.status_code == 200
               and r["delta"] == round(BASE_DELTA * 0.5, 1)
               and (r["riskPriorGate"] or {}).get("tier")
               == "restricted",
               f"delta={r.get('delta')}")

        # 复核通道: 申诉开放(无 X-Role)
        resp = client.post(
            f"/api/trust/risk/{tid}/review-request",
            json={"reason": "HTTP申诉: 画像误判申请复核"})
        body = resp.json()
        record("HTTP申诉开放200",
               resp.status_code == 200
               and body.get("status") == "pending",
               str(resp.status_code))
        rid = body.get("reviewId")

        # 重复申诉 409
        resp = client.post(
            f"/api/trust/risk/{tid}/review-request",
            json={"reason": "HTTP重复申诉测试理由"})
        record("HTTP重复申诉409",
               resp.status_code == 409, str(resp.status_code))

        # decide 鉴权
        resp = client.post(
            f"/api/trust/risk/{tid}/reviews/{rid}/decide",
            json={"approve": True, "trustLevel": 0.8,
                  "note": "x"})
        record("decide缺Role403",
               resp.status_code == 403, str(resp.status_code))

        # decide 200(误判确认→校准)
        resp = client.post(
            f"/api/trust/risk/{tid}/reviews/{rid}/decide",
            json={"approve": True, "trustLevel": 0.8,
                  "note": "HTTP复核: 误判确认"},
            headers=admin)
        body = resp.json()
        record("HTTP误判确认→校准",
               resp.status_code == 200
               and body.get("trustLevel") == 0.8
               and body.get("pendingReview") is False,
               f"level={body.get('trustLevel')}")

        # 校准后通道恢复(trusted ×1.1)
        resp = client.post("/api/trust/deposits", json={
            "trustId": tid, "layer": "L2",
            "factor": "ethics_evidence",
            "observed": 200, "peerBaseline": 50,
            "evidence": _ev("HTTP复核恢复通道测试材料"),
            "summary": "志愿服务(权威源公示)",
            "sources": ["gov_penalty", "media"]})
        r = resp.json()
        record("复核恢复通道(×1.1)",
               r["delta"] == round(BASE_DELTA * 1.1, 1),
               f"delta={r.get('delta')}")

        # 未知复核 404
        resp = client.post(
            f"/api/trust/risk/{tid}/reviews/rv-none/decide",
            json={"approve": False, "note": "x"},
            headers=admin)
        record("HTTP未知复核404",
               resp.status_code == 404, str(resp.status_code))

        # 画像视图含复核队列
        resp = client.get(f"/api/trust/risk/{tid}",
                          headers=admin)
        body = resp.json()
        record("HTTP画像含复核队列",
               resp.status_code == 200
               and len(body.get("reviewRequests") or []) == 1
               and body["reviewRequests"][0]["status"]
               == "calibrated",
               str(body.get("reviewRequests"))[:60])
        set_mode("off")


async def run_all():
    await TestPriorMath().run()
    await TestDeltaGate().run()
    await TestGateFloor().run()
    await TestPriorE2E().run()
    await TestReviewChannel().run()
    await TestFailSoft().run()
    await TestHttp().run()
    set_mode("off")


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
