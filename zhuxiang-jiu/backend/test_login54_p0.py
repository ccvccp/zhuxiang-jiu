"""54号·小竹AI智能登录引擎大模型 P0 专项测试
(模型注册+评分器+双模型接入)

运行方式:
    python test_login54_p0.py

覆盖(54号计划 §六 P0):
    - 档案注册: SCORER_REGISTRY 第 29 档案+
      DECISION_THRESHOLDS 四级+default_weights 八因子
    - 评分器: 八因子→信任分(0-100)→四级响应;
      边界值/输入非法/置信度/缺省中性
    - 双模型合成: max 合成铁律(54号等价风险分
      vs 43号 auth_risk——互补不替换)
    - 影子评分预览(不落库)
    - 模型状态视图(44号复用)
    - off 铁律+端点+零影响(44号既有 29 档案
      零改动+43号 auth_risk 零改动宪法断言)
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
os.environ["LOGIN54_MODE"] = "off"

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
    """01 档案注册"""

    async def run(self):
        print("[01 档案注册]")
        reset_all()
        from services.ai_learning_service import (
            SCORER_REGISTRY, DECISION_THRESHOLDS,
            default_weights,
        )
        meta = SCORER_REGISTRY.get(
            "login_orchestration")
        record("第 29 档案注册(login_orchestration)",
               meta is not None
               and meta.get("batch") == 13
               and meta.get("module") == "54登录大模型",
               str(meta))
        thresholds = DECISION_THRESHOLDS.get(
            "login_orchestration")
        record("四级阈值注册",
               thresholds == [
                   (75.0, "silent"), (50.0, "one_tap"),
                   (25.0, "step_up"), (0.0, "enhanced")],
               str(thresholds))

        weights = default_weights("login_orchestration")
        record("八因子默认权重(和=1.0)",
               len(weights) == 8
               and abs(sum(weights.values()) - 1.0)
               < 1e-9,
               str(len(weights)))
        record("权重键齐备",
               set(weights) == {
                   "channel_success",
                   "credential_quality",
                   "device_match", "budget_sufficiency",
                   "member_maturity", "fail_history",
                   "voice_confidence", "portal_state"},
               str(list(weights)))

        # 44号既有档案零改动(宪法断言)
        record("44号档案总数 ≥29(54号前 28+54号)",
               len(SCORER_REGISTRY) >= 29,
               str(len(SCORER_REGISTRY)))
        record("43号 auth_risk 档案零改动",
               SCORER_REGISTRY.get("auth_risk")
               .get("module") == "30用户认证")
        # 43号评分器权重零改动
        from services.ai_scoring_auth_service import (
            AuthRiskScorer,
        )
        record("43号 auth_risk 八因子零改动",
               len(AuthRiskScorer.WEIGHTS) == 8)


class TestScorer:
    """02 评分器"""

    async def run(self):
        print("[02 评分器]")
        reset_all()
        from services.login54_scorer import (
            Login54Scorer, TIER_SILENT,
        )
        scorer = Login54Scorer()

        # 高信任场景(全优上下文→silent)
        good_ctx = {
            "channelSuccess": 0.98,
            "channel": "passkey",
            "baselineMatch": 1.0,
            "budgetRemaining": 1.0,
            "accountAgeDays": 365,
            "loginFrequency": 20,
            "channelFailCount": 0,
            "voiceConfidence": 0.95,
            "portalState": "active",
        }
        r = await scorer.score(good_ctx)
        record("高信任→silent 档",
               r.get("tier") == TIER_SILENT
               and r.get("trustScore") >= 75,
               str((r.get("tier"),
                    r.get("trustScore"))))
        record("八因子齐备(factors)",
               len(r.get("factors") or []) == 8,
               str(len(r.get("factors") or [])))
        record("风险分等价(100-trust)",
               abs((r.get("riskScoreEquivalent") or 0)
                   + (r.get("trustScore") or 0)
                   - 100) < 0.2,
               str(r.get("riskScoreEquivalent")))
        record("高信任置信度=1.0",
               r.get("confidence") == 1.0,
               str(r.get("confidence")))

        # 低信任场景(高危+失败+差通道→enhanced)
        bad_ctx = {
            "channelSuccess": 0.1,
            "channel": "qr",
            "baselineMatch": 0.0,
            "budgetRemaining": 0.0,
            "accountAgeDays": 1,
            "loginFrequency": 0,
            "channelFailCount": 4,
            "portalState": "high_risk",
        }
        r2 = await scorer.score(bad_ctx)
        record("低信任→enhanced 档",
               r2.get("tier") == "enhanced"
               and r2.get("trustScore") < 25,
               str((r2.get("tier"),
                    r2.get("trustScore"))))

        # 边界: one_tap 档
        mid_ctx = dict(good_ctx)
        mid_ctx.update({
            "channelSuccess": 0.5,
            "channelFailCount": 2,
            "portalState": "new",
            "baselineMatch": 0.7,
        })
        r3 = await scorer.score(mid_ctx)
        record("中间场景档位合法(四态)",
               r3.get("tier") in (
                   "silent", "one_tap",
                   "step_up", "enhanced"),
               str(r3.get("tier")))

        # 缺省中性(仅必填——置信度按必填完整度
        # =1.0; 缺必填才降; 与 43号 _confidence 语义一致)
        r4 = await scorer.score({
            "channelSuccess": 0.5, "channel": "voice",
            "portalState": "new"})
        record("必填齐全置信度=1.0(43号同语义)",
               r4.get("confidence") == 1.0,
               str(r4.get("confidence")))
        # 缺必填(portalState)→置信度<1
        r5 = await scorer.score({
            "channelSuccess": 0.5, "channel": "voice"})
        record("缺必填置信度<1",
               0.3 <= r5.get("confidence", 0) < 1.0,
               str(r5.get("confidence")))

        # 输入非法
        for field, bad in (("channelSuccess", 1.5),
                           ("baselineMatch", -0.1),
                           ("channelFailCount", -1)):
            try:
                ctx = dict(good_ctx)
                ctx[field] = bad
                await scorer.score(ctx)
                ok, err = False, "未拒绝"
            except ValueError:
                ok, err = True, ""
            record(f"输入非法拒绝({field})", ok, err)
        try:
            await scorer.score({})
            ok, err = False, "未拒绝"
        except ValueError:
            ok, err = True, ""
        record("空上下文拒绝", ok, err)

        # 权重版本回退(44号 champion 读取)
        record("权重版本标注(v1 默认)",
               r.get("weightVersion") in ("v1", ""),
               str(r.get("weightVersion")))


class TestDualScore:
    """03 双模型 max 合成"""

    async def run(self):
        print("[03 双模型合成]")
        reset_all()
        from services.login54_service import (
            Login54Service,
        )
        svc = Login54Service()

        # off 态拒绝
        try:
            await svc.dual_score({"channel": "passkey",
                                  "channelSuccess": 0.9,
                                  "portalState": "active"})
            ok, err = False, "未拒绝"
        except ValueError as e:
            ok, err = "off" in str(e), str(e)[:30]
        record("off 态双模型拒绝", ok, err)

        os.environ["LOGIN54_MODE"] = "on"

        good_ctx = {
            "channelSuccess": 0.98, "channel": "passkey",
            "baselineMatch": 1.0, "accountAgeDays": 365,
            "loginFrequency": 20, "channelFailCount": 0,
            "portalState": "active",
        }

        # 仅 54号(无 auth_risk 传入)
        r = await svc.dual_score(good_ctx)
        record("仅54号(source=login54_only)",
               r.get("source") == "login54_only",
               str(r.get("source")))

        # 双模型: 54号低险+43号高险→取 max(auth 主导)
        r2 = await svc.dual_score(good_ctx, {
            "score": 55.0, "action": "step_up"})
        record("max 合成(auth 主导取高)",
               r2.get("combinedRiskScore") == 55.0
               and r2.get("source")
               == "auth_risk_dominant",
               str((r2.get("combinedRiskScore"),
                    r2.get("source"))))
        record("合成档位(step_up)",
               r2.get("tier") == "step_up",
               str(r2.get("tier")))

        # 双模型: 54号高险+43号低险→取 max(54号主导)
        r3 = await svc.dual_score({
            "channelSuccess": 0.05, "channel": "qr",
            "baselineMatch": 0.0, "channelFailCount": 4,
            "portalState": "high_risk"}, {
            "score": 10.0, "action": "allow"})
        record("max 合成(54号主导取高)",
               r3.get("source") == "dual_max"
               and r3.get("combinedRiskScore")
               > 60.0,
               str((r3.get("source"),
                    r3.get("combinedRiskScore"))))
        record("auth_risk 结果原样透传(零侵入)",
               (r2.get("authRisk") or {})
               .get("score") == 55.0,
               "透传失败")

        # 影子预览(不落库)
        p = await svc.score_preview(good_ctx)
        record("影子预览(preview+不生效标注)",
               (p.get("preview") or {})
               .get("scorer")
               == "login_orchestration"
               and "不落库" in p.get("note", ""),
               str((p.get("preview") or {}).get(
                   "scorer")))
        os.environ["LOGIN54_MODE"] = "off"


class TestStatusAndEndpoints:
    """04 状态视图+端点+零影响"""

    async def run(self):
        print("[04 状态+端点]")
        reset_all()
        from services.login54_service import (
            Login54Service,
        )
        svc = Login54Service()

        # 状态视图(观测面——off 可访问)
        s = await svc.model_status()
        st = s.get("status") or {}
        record("模型状态(44号复用 champion)",
               st.get("scorerId")
               == "login_orchestration"
               and "champion" in st,
               str(list(st))[:50])
        record("状态含八因子元数据",
               len(st.get("factorsMeta") or {}) == 8,
               str(len(st.get("factorsMeta") or {})))

        # 模型事件留痕(基础能力)
        ev = await svc.record_model_event(
            "test_event", {"note": "p0"})
        h = await svc.model_history()
        record("模型事件留痕+历史",
               ev.get("modelEventId") == 1
               and (h.get("total") or 0) == 1,
               str(h.get("total")))

        # registry 自描述(观测面)
        reg = Login54Service.registry()
        record("registry 自描述(八因子+双模型)",
               len((reg.get("scorer") or {})
                   .get("weights") or {}) == 8
               and (reg.get("dualModel") or {})
               .get("authRiskIndependent") is True,
               str(len((reg.get("scorer") or {})
                   .get("weights") or {})))

        # HTTP 端点
        from fastapi.testclient import TestClient
        from main import app
        client = TestClient(app)
        admin = {"X-Role": "admin"}

        resp = client.get("/api/login54/registry",
                          headers=admin)
        record("HTTP registry 200(off 可访问)",
               resp.status_code == 200
               and len(((resp.json() or {})
                        .get("scorer") or {})
                       .get("weights") or {}) == 8,
               str(resp.status_code))

        resp = client.get("/api/login54/model/status",
                          headers=admin)
        record("HTTP model/status 200",
               resp.status_code == 200,
               str(resp.status_code))

        resp = client.post(
            "/api/login54/score/preview",
            json={"ctx": {"channelSuccess": 0.9,
                          "channel": "passkey",
                          "portalState": "active"}},
            headers=admin)
        record("HTTP preview off 409",
               resp.status_code == 409,
               str(resp.status_code))

        os.environ["LOGIN54_MODE"] = "on"
        resp = client.post(
            "/api/login54/score/preview",
            json={"ctx": {"channelSuccess": 0.9,
                          "channel": "passkey",
                          "portalState": "active"}},
            headers=admin)
        record("HTTP preview on 200(信任分)",
               resp.status_code == 200
               and (((resp.json() or {})
                     .get("preview") or {})
                    .get("trustScore") or 0) > 0,
               str(resp.status_code))

        resp = client.get("/api/login54/model/history",
                          headers=admin)
        record("HTTP model/history 200",
               resp.status_code == 200,
               str(resp.status_code))

        # 鉴权
        resp = client.get("/api/login54/registry")
        record("registry 无 Role 403",
               resp.status_code == 403,
               str(resp.status_code))

        # 零影响: 宪法断言
        from services.ai_learning_service import (
            run_learning_cycle,
        )
        record("44号学习循环接口零改动",
               callable(run_learning_cycle))
        from routes.login53_routes import (
            router as login53_router,
        )
        count53 = sum(1 for r in login53_router.routes)
        record("53号路由零改动(20 端点)",
               count53 == 20, str(count53))
        os.environ["LOGIN54_MODE"] = "off"


async def run_all():
    await TestRegistry().run()
    await TestScorer().run()
    await TestDualScore().run()
    await TestStatusAndEndpoints().run()


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
