"""52号·小竹语音可用性评估引擎 P0 专项测试
(指标注册表+决策规则引擎+快照框架)

运行方式:
    python test_us52_p0.py

覆盖(52号计划 §七 P0):
    - 注册表完整性: 五维 20 项(5/4/5/4/2)/
      基线域合法/veto 域仅 resilience(5 项)/
      proxy 指标带免责声明/数据源白名单/
      启动自检可重跑
    - 决策规则: 全 pass→pass/
      韧性 fail→veto(一票否决)/
      功能 fail→mandatory/
      信任 fail→priority/
      牺牲隐私→regression(铁律最高优先)/
      透明度 fail→mandatory
    - 单项判定: higher/lower 双向边界
    - 快照框架: 计算留痕/未注册指标拒绝/
      off 态拒绝/空指标拒绝/快照回溯/latest
    - release-gate: veto 判定+规则视图
    - 端点+鉴权+零影响(宪法断言)
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
os.environ["US52_MODE"] = "off"

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


def full_pass_metrics() -> dict:
    """全达标指标集(20 项)"""
    return {
        # functional(5)
        "intent_accuracy": 0.97,
        "fc_success_rate": 0.99,
        "explain_ref_rate": 1.0,
        "budget_accuracy": 1.0,
        "confirm_rate": 1.0,
        # transparency(4)
        "privacy_notice_rate": 0.95,
        "attribution_rate": 0.90,
        "error_clarity": 0.97,
        "data_purpose_rate": 0.85,
        # resilience(5)
        "injection_defense_rate": 1.0,
        "voiceprint_spoof_rate": 1.0,
        "degrade_compliance_rate": 1.0,
        "budget_exhausted_guide_rate": 1.0,
        "session_isolation_rate": 1.0,
        # trust(4)
        "trust_gain_index": 0.5,
        "control_sense_rate": 0.7,
        "ethics_negative_rate": 0.02,
        "feedback_health_ratio": 0.8,
        # inclusion(2)
        "intent_parity_gap": 0.03,
        "low_value_service_parity": 0.02,
    }


class TestRegistry:
    """01 注册表完整性"""

    def run(self):
        print("[01 注册表完整性]")
        from services.us52_registry import (
            USABILITY_REGISTRY, DATA_SOURCES,
            _validate_registry,
        )
        record("指标总数(20)",
               len(USABILITY_REGISTRY) == 20,
               str(len(USABILITY_REGISTRY)))
        by_dim: dict = {}
        for m in USABILITY_REGISTRY.values():
            by_dim[m["dimension"]] = \
                by_dim.get(m["dimension"], 0) + 1
        record("五维分布(5/4/5/4/2)",
               by_dim == {"functional": 5,
                          "transparency": 4,
                          "resilience": 5,
                          "trust": 4,
                          "inclusion": 2},
               str(by_dim))

        veto_keys = [k for k, v
                     in USABILITY_REGISTRY.items()
                     if v.get("veto")]
        record("veto 域仅安全韧性(5 项)",
               len(veto_keys) == 5
               and all(
                   USABILITY_REGISTRY[k]["dimension"]
                   == "resilience" for k in veto_keys),
               str(veto_keys))

        proxy_keys = [k for k, v
                      in USABILITY_REGISTRY.items()
                      if v.get("proxy")]
        record("proxy 指标带免责声明",
               all(USABILITY_REGISTRY[k].get("proxyNote")
                   for k in proxy_keys)
               and len(proxy_keys) >= 5,
               str(len(proxy_keys)))

        src_ok = all(
            s in DATA_SOURCES
            for m in USABILITY_REGISTRY.values()
            for s in m["sources"])
        record("数据源白名单(零侵入)",
               src_ok)

        base_ok = all(
            0 <= float(m["baseline"]) <= 1
            and m["direction"]
            in ("higher", "lower")
            for m in USABILITY_REGISTRY.values())
        record("基线域+方向合法", base_ok)

        try:
            _validate_registry()
            ok, err = True, ""
        except RuntimeError as e:
            ok, err = False, str(e)
        record("启动自检可重跑", ok, err)


class TestDecision:
    """02 决策规则引擎"""

    def run(self):
        print("[02 决策规则引擎]")
        from services.us52_registry import decide

        r = decide(full_pass_metrics())
        record("全达标→pass",
               r["decision"] == "pass"
               and r["passed"] is True, str(r))

        m = full_pass_metrics()
        m["injection_defense_rate"] = 0.95
        r = decide(m)
        record("韧性 fail→veto(一票否决)",
               r["decision"] == "veto"
               and r["passed"] is False
               and "injection_defense_rate"
               in r["vetoFailed"], str(r["decision"]))

        m = full_pass_metrics()
        m["intent_accuracy"] = 0.90
        r = decide(m)
        record("功能 fail→mandatory",
               r["decision"] == "mandatory"
               and "intent_accuracy" in
               r["failedByDimension"].get(
                   "functional", []),
               str(r["decision"]))

        m = full_pass_metrics()
        m["trust_gain_index"] = 0.0
        r = decide(m)
        record("信任 fail→priority",
               r["decision"] == "priority",
               str(r["decision"]))

        m = full_pass_metrics()
        m["privacy_notice_rate"] = 0.5
        r = decide(m)
        record("透明度 fail→mandatory",
               r["decision"] == "mandatory",
               str(r["decision"]))

        m = full_pass_metrics()
        m["intent_parity_gap"] = 0.10
        r = decide(m)
        record("包容性 fail→mandatory",
               r["decision"] == "mandatory",
               str(r["decision"]))

        # 铁律: 负向改进最高优先(即使全指标达标)
        r = decide(full_pass_metrics(),
                   sacrifice_flags=["privacy"])
        record("牺牲隐私→regression(铁律)",
               r["decision"] == "regression"
               and r["passed"] is False, str(r))

        # 铁律优先于 veto
        m = full_pass_metrics()
        m["injection_defense_rate"] = 0.5
        r = decide(m, sacrifice_flags=["fairness"])
        record("regression 优先于 veto",
               r["decision"] == "regression",
               str(r["decision"]))


class TestEvaluate:
    """03 单项判定"""

    def run(self):
        print("[03 单项判定]")
        from services.us52_registry import (
            evaluate_metric,
        )
        record("higher 方向(≥基线 pass)",
               evaluate_metric(
                   "intent_accuracy", 0.95) == "pass"
               and evaluate_metric(
                   "intent_accuracy", 0.949) == "fail")
        record("lower 方向(≤基线 pass)",
               evaluate_metric(
                   "ethics_negative_rate", 0.05)
               == "pass"
               and evaluate_metric(
                   "ethics_negative_rate", 0.051)
               == "fail")
        try:
            evaluate_metric("unknown_metric", 1.0)
            ok, err = False, "未拒绝"
        except KeyError:
            ok, err = True, ""
        record("未注册指标拒绝", ok, err)


class TestSnapshot:
    """04 快照框架"""

    async def run(self):
        print("[04 快照框架]")
        reset_all()
        from services.us52_service import (
            Us52MetricsService,
        )
        svc = Us52MetricsService()

        # off 态拒绝
        try:
            await svc.compute_snapshot(
                full_pass_metrics())
            ok, err = False, "未拒绝"
        except ValueError as e:
            ok, err = "off" in str(e), str(e)[:30]
        record("off 态计算拒绝(测试停铁律)",
               ok, err)

        os.environ["US52_MODE"] = "on"
        r = await svc.compute_snapshot(
            full_pass_metrics())
        snap = r.get("snapshot") or {}
        record("快照计算留痕(snapId=1)",
               snap.get("snapId") == 1
               and snap.get("passedCount") == 20,
               f"{snap.get('snapId')}/"
               f"{snap.get('passedCount')}")
        record("决策写入(pass)",
               snap.get("decision") == "pass")

        # veto 快照
        m = full_pass_metrics()
        m["session_isolation_rate"] = 0.9
        r2 = await svc.compute_snapshot(m)
        record("veto 快照(vetoFailed 留痕)",
               (r2.get("snapshot") or {})
               .get("decision") == "veto"
               and "session_isolation_rate" in
               (r2.get("snapshot")
                or {}).get("vetoFailed", []),
               str((r2.get("snapshot") or {})
                   .get("decision")))

        # 未注册指标
        try:
            await svc.compute_snapshot(
                {"unknown": 1.0})
            ok, err = False, "未拒绝"
        except ValueError:
            ok, err = True, ""
        record("未注册指标拒绝", ok, err)

        # 空指标
        try:
            await svc.compute_snapshot({})
            ok, err = False, "未拒绝"
        except ValueError:
            ok, err = True, ""
        record("空指标集拒绝", ok, err)

        # 回溯+latest
        r3 = await svc.list_snapshots()
        record("快照回溯(2 条最新在前)",
               (r3.get("total") or 0) == 2
               and (r3.get("snapshots")
                    or [{}])[0].get("snapId") == 2,
               str(r3.get("total")))
        r4 = await svc.latest_snapshot()
        record("latest 快照(snapId=2)",
               ((r4.get("snapshot") or {})
                .get("snapId")) == 2)
        os.environ["US52_MODE"] = "off"


class TestEndpoints:
    """05 端点+鉴权+零影响"""

    async def run(self):
        print("[05 端点+鉴权+零影响]")
        reset_all()
        from fastapi.testclient import TestClient
        from main import app
        client = TestClient(app)
        admin = {"X-Role": "admin"}

        resp = client.get("/api/us52/registry")
        record("registry 无 Role 403",
               resp.status_code == 403,
               str(resp.status_code))

        resp = client.get("/api/us52/registry",
                          headers=admin)
        body = resp.json() or {}
        record("registry admin 200(20 项)",
               resp.status_code == 200
               and body.get("metricCount") == 20
               and body.get("mode") == "off",
               str(resp.status_code))

        resp = client.get("/api/us52/dimensions",
                          headers=admin)
        body = resp.json() or {}
        record("dimensions 五维结构",
               resp.status_code == 200
               and len(body.get("dimensions")
                       or []) == 5,
               str(resp.status_code))

        resp = client.post(
            "/api/us52/metrics/compute", headers=admin,
            json={"metrics": full_pass_metrics()})
        record("off 态 compute 409",
               resp.status_code == 409,
               str(resp.status_code))

        os.environ["US52_MODE"] = "on"
        resp = client.post(
            "/api/us52/metrics/compute", headers=admin,
            json={"metrics": full_pass_metrics()})
        record("on 态 compute 200(pass)",
               resp.status_code == 200
               and ((resp.json() or {})
                    .get("snapshot") or {}
                    ).get("decision") == "pass",
               str(resp.status_code))

        resp = client.get("/api/us52/metrics/latest",
                          headers=admin)
        record("latest 200",
               resp.status_code == 200,
               str(resp.status_code))

        resp = client.get(
            "/api/us52/metrics/snapshots",
            headers=admin)
        record("snapshots 200(1 条)",
               resp.status_code == 200
               and (resp.json() or {})
               .get("total") == 1,
               str(resp.status_code))

        # release-gate
        resp = client.post(
            "/api/us52/release-gate", headers=admin,
            json={"metrics": full_pass_metrics()})
        record("release-gate 全达标 pass",
               resp.status_code == 200
               and (resp.json() or {}
                    ).get("gate") == "pass",
               str(resp.status_code))

        m = full_pass_metrics()
        m["degrade_compliance_rate"] = 0.9
        resp = client.post(
            "/api/us52/release-gate", headers=admin,
            json={"metrics": m})
        record("release-gate 韧性 fail→veto",
               (resp.json() or {}).get("gate")
               == "veto"
               and (resp.json() or {})
               .get("passed") is False,
               str(resp.status_code))

        resp = client.post(
            "/api/us52/release-gate", headers=admin,
            json={"metrics": full_pass_metrics(),
                  "sacrificeFlags": ["privacy"]})
        record("release-gate 牺牲→regression",
               (resp.json() or {}).get("gate")
               == "regression",
               str(resp.status_code))

        resp = client.post("/api/us52/release-gate",
                           headers=admin,
                           json={"metrics": {}})
        record("release-gate 空指标 200"
               "(无未达标项→pass 语义)",
               resp.status_code == 200,
               str(resp.status_code))

        # 零影响: 宪法断言
        from services.trust_scoring_service import (
            TrustValueScorer,
        )
        from services.xiaozhu_voice50_rules import (
            VOICE_RULES,
        )
        from services.xiaozhu_fc_registry import (
            TOOL_REGISTRY,
        )
        record("45号九因子零改动",
               len(TrustValueScorer.LAYER_OF) == 9)
        record("50号14行为零改动",
               len(VOICE_RULES) == 14)
        record("49号17工具零改动",
               len(TOOL_REGISTRY) == 17)
        os.environ["US52_MODE"] = "off"


async def run_all():
    TestRegistry().run()
    TestDecision().run()
    TestEvaluate().run()
    await TestSnapshot().run()
    await TestEndpoints().run()


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
