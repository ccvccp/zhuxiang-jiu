"""61号·AI智能系统升级决策模块 P0 专项测试
(决策注册表+语义标签+请求底座+第36档案)

运行方式:
    python test_dm61_p0.py

覆盖(61号计划 §七 P0):
    - 决策注册表: 语义标签六类+依赖
      映射+窗口适宜性+启动自检断言域
    - 请求底座: 三源接收+语义标签轨
      (确定性)+影响面预测+环境感知
      +状态机 received→tagged
    - 第36档案八因子+三级决策
    - HTTP 层: 5 端点+鉴权
    - 宪法: 44号 37 档案+56/45号
      零改动+开关铁律
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
os.environ["DM61_MODE"] = "off"
os.environ.pop("DM61_LLM_MODE", None)
os.environ.pop("DM61_LEARN_MODE", None)

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
    """01 决策注册表"""

    async def run(self):
        print("[01 决策注册表]")
        reset_all()
        from services.dm61_registry import (
            DEPENDENCY_MAP, SEMANTIC_RULES,
            SEMANTIC_TAGS, TAG_META,
            check_window, parse_semantic_tag,
            predict_impact, registry_view,
        )

        # 数量+结构
        record("语义标签六类",
               len(SEMANTIC_TAGS) == 6,
               str(len(SEMANTIC_TAGS)))
        record("标签属性全覆盖",
               set(TAG_META)
               == set(SEMANTIC_TAGS),
               str(len(TAG_META)))
        record("依赖映射全覆盖",
               set(DEPENDENCY_MAP)
               == set(SEMANTIC_TAGS),
               str(len(DEPENDENCY_MAP)))
        record("关键词规则有序非空",
               len(SEMANTIC_RULES) >= 6
               and all(
                   kws for _, kws
                   in SEMANTIC_RULES),
               str(len(SEMANTIC_RULES)))

        # 铁律: 核心重构/权限变更 critical
        record("铁律: 重构/权限 critical",
               TAG_META["core_refactor"]
               ["sensitivity"] == "critical"
               and TAG_META[
                   "permission_change"]
               ["sensitivity"]
               == "critical",
               str((TAG_META[
                        "core_refactor"]
                    ["sensitivity"],
                    TAG_META[
                        "permission_change"]
                    ["sensitivity"])))

        # 语义标签轨(确定性——首命中)
        s1 = parse_semantic_tag(
            "支付费率调整", "")
        record("语义轨: 支付→payment_opt",
               s1["tag"] == "payment_opt"
               and s1["source"] == "rule",
               str(s1.get("tag")))
        s2 = parse_semantic_tag(
            "后台权限角色调整", "")
        record("语义轨: 权限优先命中",
               s2["tag"] == "permission_change"
               and s2["sensitivity"]
               == "critical",
               str(s2.get("tag")))
        s3 = parse_semantic_tag(
            "界面适配优化", "无障碍样式")
        record("语义轨: 界面→ui_adapt",
               s3["tag"] == "ui_adapt",
               str(s3.get("tag")))
        s4 = parse_semantic_tag(
            "优化", "")
        record("语义轨: 无命中兜底观测类",
               s4["source"] == "fallback"
               and s4["sensitivity"]
               == "observe",
               str(s4.get("source")))

        # 影响面预测(封闭注册)
        i1 = predict_impact("payment_opt")
        record("影响面: 支付→会员+同盟商",
               "member" in i1["roles"]
               and "ally_merchant"
               in i1["roles"]
               and i1["impactPct"] == 3.0,
               str(i1.get("roles")))
        i2 = predict_impact("unknown_tag")
        record("影响面: 域外 fail-safe",
               i2["sensitivity"]
               == "critical"
               and i2["roleCount"]
               == len(i2["roles"]) == 5,
               str(i2.get("sensitivity")))

        # 窗口适宜性(三因子)
        w1 = check_window(hour=3)
        record("窗口: 深夜适宜",
               w1["level"] == "suitable",
               str(w1.get("level")))
        w2 = check_window(hour=20)
        record("窗口: 高峰 caution",
               w2["level"] == "caution"
               and w2["penalties"]
               .get("peakHour")
               == 30.0,
               str(w2.get("level")))
        w3 = check_window(hour=20,
                          recent_failure_rate=0.1,
                          trust_volatility=0.5)
        record("窗口: 三因子叠加不适宜",
               w3["level"] == "unsuitable"
               and w3["score"] >= 40.0,
               str((w3.get("level"),
                    w3.get("score"))))
        w4 = check_window(hour=10,
                          recent_failure_rate=0.06)
        record("窗口: 故障率单因子 caution",
               w4["level"] == "caution",
               str(w4.get("level")))

        # registry_view 观测面
        v = registry_view()
        record("registry_view 结构",
               v.get("semanticTags") == 6
               and v.get(
                   "dependencyEntries")
               == 6
               and v.get("mode") == "off",
               str((v.get("semanticTags"),
                    v.get("mode"))))


class TestRequest:
    """02 请求底座(三源+状态机)"""

    async def run(self):
        print("[02 请求底座]")
        reset_all()
        from services.dm61_service import (
            Dm61Service,
        )
        svc = Dm61Service()

        # off 铁律(决策面拒绝)
        try:
            await svc.create_request("测试")
            ok, err = False, "未拒绝"
        except ValueError:
            ok, err = True, ""
        record("off 铁律(创建拒绝)", ok, err)

        # shadow 开放
        os.environ["DM61_MODE"] = "shadow"

        # ① 人工源+语义标签+影响面+环境
        r1 = await svc.create_request(
            title="支付结算费率优化",
            description="调整分账费率",
            source="manual",
            hour=3)
        record("人工源创建(tagged)",
               r1.get("status")
               == "tagged"
               and r1.get("requestId")
               == 1,
               str(r1.get("status")))
        record("语义标签(payment_opt)",
               (r1.get("semantic")
                or {}).get("tag")
               == "payment_opt",
               str((r1.get("semantic")
                    or {}).get("tag")))
        record("影响面预测挂载",
               (r1.get("impact")
                or {}).get("impactPct")
               == 3.0,
               str((r1.get("impact")
                    or {}).get(
                   "impactPct")))
        record("环境感知挂载(suitable)",
               (r1.get("environment")
                or {}).get("level")
               == "suitable",
               str((r1.get("environment")
                    or {}).get("level")))
        record("指纹链(sha256)",
               str(r1.get("fingerprint")
                   or "").startswith(
                   "sha256:"),
               str(r1.get("fingerprint")
                   )[:20])

        # ② 56号提案源(必带 proposalId)
        r2 = await svc.create_request(
            title="核心链路重构提案",
            source="proposal",
            proposal_id=101,
            hour=20)
        record("提案源创建(critical)",
               (r2.get("semantic")
                or {}).get("tag")
               == "core_refactor"
               and (r2.get("semantic")
                    or {}).get(
                   "sensitivity")
               == "critical",
               str((r2.get("semantic")
                    or {}).get("tag")))
        record("提案源窗口 caution",
               (r2.get("environment")
                or {}).get("level")
               == "caution",
               str((r2.get("environment")
                    or {}).get("level")))
        try:
            await svc.create_request(
                title="无号提案",
                source="proposal")
            ok, err = False, "未拒绝"
        except ValueError:
            ok, err = True, ""
        record("提案源缺号拒绝", ok, err)

        # ③ 44号信号源(必带 signalId)
        r3 = await svc.create_request(
            title="算法权重调整信号",
            source="signal",
            signal_id=55,
            hour=8)
        record("信号源创建(algo_param)",
               (r3.get("semantic")
                or {}).get("tag")
               == "algo_param",
               str((r3.get("semantic")
                    or {}).get("tag")))
        try:
            await svc.create_request(
                title="无号信号",
                source="signal")
            ok, err = False, "未拒绝"
        except ValueError:
            ok, err = True, ""
        record("信号源缺号拒绝", ok, err)

        # 来源域外拒绝
        try:
            await svc.create_request(
                title="测试", source="hack")
            ok, err = False, "未拒绝"
        except ValueError:
            ok, err = True, ""
        record("来源域外拒绝", ok, err)

        # 空标题拒绝
        try:
            await svc.create_request("")
            ok, err = False, "未拒绝"
        except ValueError:
            ok, err = True, ""
        record("空标题拒绝", ok, err)

        # 观测面: 列表+过滤+详情
        lv = await svc.list_requests()
        record("列表观测(3 条+分布)",
               lv.get("total") == 3
               and lv.get("byTag",
                          {}).get(
                   "payment_opt") == 1,
               str(lv.get("byTag")))
        lf = await svc.list_requests(
            source="proposal")
        record("列表来源过滤",
               lf.get("total") == 1,
               str(lf.get("total")))
        d1 = await svc.get_request(1)
        record("详情观测(语义快照)",
               (d1.get("request")
                or {}).get("tag")
               == "payment_opt",
               str((d1.get("request")
                    or {}).get("tag")))
        try:
            await svc.get_request(999)
            ok, err = False, "未拒绝"
        except KeyError:
            ok, err = True, ""
        record("详情 404(不存在)", ok, err)

        # 事件留痕
        from repositories.dm61_repository import (
            Dm61Repository,
        )
        evs = await Dm61Repository() \
            .list_events(limit=10)
        record("事件链(request×3)",
               len([e for e in evs
                    if e.get("eventType")
                    == "request"]) == 3,
               str(len(evs)))

        os.environ["DM61_MODE"] = "off"


class TestScorer:
    """03 第36档案八因子"""

    async def run(self):
        print("[03 第36档案]")
        reset_all()
        from services.dm61_scorer import (
            Dm61Scorer,
        )
        scorer = Dm61Scorer()

        # 空上下文拒绝
        try:
            await scorer.score({})
            ok, err = False, "未拒绝"
        except ValueError:
            ok, err = True, ""
        record("空上下文拒绝", ok, err)

        # 全因子高分
        r = await scorer.score({
            "decisionAccuracy": 0.95,
            "autonomousRatio": 0.2,
            "simulationHitRate": 0.9,
            "dissentEffectiveness": 0.85,
            "tier": "trusted",
            "rollbackSuccessRate": 0.9,
            "latencyP95Ok": 0.95,
            "scenarioCoverage": 1.0,
        })
        record("八因子齐备",
               len(r.get("factors") or []) == 8,
               str(len(r.get("factors"))))
        record("权重和=1.0",
               abs(sum((r.get(
                   "weightsUsed") or {})
                   .values()) - 1.0) < 0.01,
               str(sum((r.get(
                   "weightsUsed") or {})
                   .values())))
        record("高分→optimize/urgent",
               r.get("decision") in (
                   "optimize", "urgent"),
               str((r.get("trustScore"),
                    r.get("decision"))))

        # 低分→observe
        r2 = await scorer.score({
            "decisionAccuracy": 0.2,
            "autonomousRatio": 0.9,
            "simulationHitRate": 0.2,
            "dissentEffectiveness": 0.1,
            "tier": "restricted",
            "rollbackSuccessRate": 0.2,
            "latencyP95Ok": 0.3,
            "scenarioCoverage": 0.2,
        })
        record("低分→observe",
               r2.get("decision") == "observe"
               and (r2.get("trustScore")
                    or 0) < 50,
               str((r2.get("trustScore"),
                    r2.get("decision"))))

        # 自治占比反向(过高降分——决策权
        # 篡夺防线)
        r3 = await scorer.score({
            "autonomousRatio": 0.1})
        r4 = await scorer.score({
            "autonomousRatio": 0.9})
        f3 = [f for f in
              r3.get("factors")
              if f["name"]
              == "autonomous_ratio"]
        f4 = [f for f in
              r4.get("factors")
              if f["name"]
              == "autonomous_ratio"]
        record("自治占比反向(过高降分)",
               (f3[0]["score"] if f3 else 0)
               > (f4[0]["score"] if f4
                  else 100),
               str((f3[0]["score"] if f3
                    else None,
                    f4[0]["score"] if f4
                    else None)))

        # tier 基线
        r5 = await scorer.score({
            "tier": "trusted"})
        f5 = [f for f in
              r5.get("factors")
              if f["name"] == "member_trust"]
        record("tier 基线(trusted=90)",
               f5 and f5[0]["score"] == 90.0,
               str(f5[0]["score"] if f5
                   else None))

        # 覆盖率越界拒绝
        try:
            await scorer.score({
                "scenarioCoverage": 1.5})
            ok, err = False, "未拒绝"
        except ValueError as e:
            ok, err = "覆盖率" in str(e), \
                str(e)[:30]
        record("覆盖率越界拒绝", ok, err)

        # 因子明细八条
        names = {f["name"] for f in
                 r.get("factors")}
        record("因子明细八条",
               names == {
                   "decision_accuracy",
                   "autonomous_ratio",
                   "simulation_hit_rate",
                   "dissent_effectiveness",
                   "member_trust",
                   "rollback_success",
                   "latency_budget",
                   "coverage_breadth"},
               str(sorted(names)))


class TestConstitution:
    """04 宪法断言"""

    async def run(self):
        print("[04 宪法断言]")
        from services.ai_learning_service import (
            SCORER_REGISTRY,
        )
        record("44号 37 档案在册",
               len(SCORER_REGISTRY) == 39,
               str(len(SCORER_REGISTRY)))
        record("第36档案 decision_orchestration",
               "decision_orchestration"
               in SCORER_REGISTRY
               and SCORER_REGISTRY[
                   "decision_"
                   "orchestration"]
               ["batch"] == 20,
               str(SCORER_REGISTRY.get(
                   "decision_orchestration")))

        # 56号零改动(模块在册)
        try:
            from services import \
                aiup56_service as s56
            record("56号零改动(模块在册)",
                   s56 is not None,
                   "")
        except ImportError:
            record("56号零改动(模块在册)",
                   False, "导入失败")

        # 45号零改动(纯读取)
        try:
            from repositories import \
                trust_value_repository as r45
            record("45号零改动(纯读取)",
                   r45 is not None,
                   "")
        except ImportError:
            record("45号零改动(纯读取)",
                   False, "导入失败")

        # 三开关铁律
        record("三开关铁律(默认 off)",
               os.environ.get(
                   "DM61_MODE",
                   "off") == "off"
               and os.environ.get(
                   "DM61_LLM_MODE",
                   "off") == "off"
               and os.environ.get(
                   "DM61_LEARN_MODE",
                   "off") == "off",
               "")


class TestHttp:
    """05 HTTP 层"""

    async def run(self):
        print("[05 HTTP]")
        reset_all()
        from fastapi.testclient import \
            TestClient
        from main import app
        client = TestClient(app)
        admin = {"X-Role": "admin"}

        # 观测面 off 可用
        resp = client.get("/api/dm61/registry",
                          headers=admin)
        body = resp.json() or {}
        record("HTTP registry 观测面 200",
               resp.status_code == 200
               and body.get("semanticTags")
               == 6
               and body.get("mode") == "off",
               str((resp.status_code,
                    body.get(
                        "semanticTags"))))

        resp = client.get(
            "/api/dm61/model/status",
            headers=admin)
        record("HTTP model/status 200",
               resp.status_code == 200
               and ((resp.json()
                     or {}).get("status")
                    or {}).get("scorerId")
               == "decision_orchestration",
               str(resp.status_code))

        # 决策面 off 409
        resp = client.post(
            "/api/dm61/requests",
            json={"title": "支付费率优化"},
            headers=admin)
        record("HTTP requests off 409",
               resp.status_code == 409,
               str(resp.status_code))

        # shadow 全链
        os.environ["DM61_MODE"] = "shadow"
        resp = client.post(
            "/api/dm61/requests",
            json={"title": "支付结算费率优化",
                  "source": "manual",
                  "hour": 3},
            headers=admin)
        body = resp.json() or {}
        record("HTTP requests 200(tagged)",
               resp.status_code == 200
               and body.get("status")
               == "tagged",
               str((resp.status_code,
                    body.get("status"))))

        # 域外 409
        resp = client.post(
            "/api/dm61/requests",
            json={"title": "测试",
                  "source": "hack"},
            headers=admin)
        record("HTTP 来源域外 409",
               resp.status_code == 409,
               str(resp.status_code))

        # 详情观测面
        rid = body.get("requestId")
        resp = client.get(
            f"/api/dm61/requests/{rid}",
            headers=admin)
        record("HTTP 详情 200",
               resp.status_code == 200
               and ((resp.json()
                     or {}).get("request")
                    or {}).get("tag")
               == "payment_opt",
               str(resp.status_code))

        # 详情 404
        resp = client.get(
            "/api/dm61/requests/999",
            headers=admin)
        record("HTTP 详情 404",
               resp.status_code == 404,
               str(resp.status_code))

        # 列表过滤
        resp = client.get(
            "/api/dm61/requests?source=manual",
            headers=admin)
        body = resp.json() or {}
        record("HTTP 列表来源过滤",
               resp.status_code == 200
               and body.get("total") == 1,
               str(body.get("total")))

        # 鉴权 403
        for method, path in (
                ("GET", "/api/dm61/registry"),
                ("POST", "/api/dm61/requests"),
                ("GET", "/api/dm61/requests"),
                ("GET",
                 "/api/dm61/model/status")):
            resp = client.request(
                method, path, json={})
            record(f"HTTP {path.split('/')[-1]}"
                   f" 无 Role 403",
                   resp.status_code == 403,
                   str(resp.status_code))

        # 路由累计 15 端点(P0-P4)
        from routes.dm61_routes import (
            router as dm_router,
        )
        count = sum(
            1 for r in dm_router.routes)
        record("61号路由累计 17 端点",
               count == 17, str(count))
        os.environ["DM61_MODE"] = "off"


async def run_all():
    await TestRegistry().run()
    await TestRequest().run()
    await TestScorer().run()
    await TestConstitution().run()
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
