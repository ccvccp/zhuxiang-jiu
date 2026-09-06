"""57号·AI智能知识库模块 P0 专项测试
(缺口信号注册表+趋势诊断引擎+种子底座)

运行方式:
    python test_kb57_p0.py

覆盖(57号计划 §十一 P0):
    - 注册表自检: 10 项五侧+权重和=1.0+启动自检红线
    - 采集源注册表: 6 源+类型+可信度复审线
    - 第32档案八因子评分器: 三级决策切档+越界拒绝
    - off 拒绝+空环境 defer 留痕
    - 强信号缺口创建(结构/快照/优先级/建议源/预算)
    - 宪法: 44号 32 档案+既有模块零改动
    - HTTP 层: 6 端点+鉴权
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


async def seed_signals():
    """种强信号: knowledge 缺口+52号包容性降+55号满意度降"""
    from core.helpers import ts
    # ① knowledge_* 开放缺口(业务侧)
    from repositories.knowledge_repository import (
        KnowledgeRepository,
    )
    krepo = KnowledgeRepository()
    gid = await krepo.next_gap_id()
    await krepo.save_gap({
        "id": gid,
        "question": "老年人补贴如何在线申请?",
        "normQuestion": "老年人补贴如何在线申请",
        "sessionId": 0,
        "askCount": 3,
        "status": "open",
        "entryId": 0,
        "createdAt": ts(),
        "lastAskedAt": ts(),
        "resolvedAt": "",
    })
    # ② 52号五维两帧(inclusion 下降 0.4)
    from repositories.us52_repository import (
        Us52Repository,
    )
    urepo = Us52Repository()
    for inclusion in (0.8, 0.4):
        sid = await urepo.next_snap_id()
        await urepo.save_snapshot({
            "snapId": sid,
            "mode": "kb57-test",
            "sampleCount": 10,
            "passedCount": 8,
            "metrics": {
                "inclusion": {
                    "value": inclusion,
                    "baseline": 0.8,
                    "direction": "higher_better",
                },
            },
            "decision": "pass",
            "createdAt": ts(),
        })
    # ③ 55号六指标两帧(满意度降 20)
    from repositories.qr55_repository import (
        Qr55Repository,
    )
    qrepo = Qr55Repository()
    for sat in (80.0, 60.0):
        meid = await qrepo.next_model_event_id()
        await qrepo.save_model_event({
            "modelEventId": meid,
            "eventType": "metrics_snapshot",
            "detail": {"metrics": {
                "satisfactionScore": sat,
                "clarifyEfficiency": 0.8,
                "penetrationRate": 0.7}},
            "createdAt": ts(),
        })


class TestRegistry:
    """01 注册表自检"""

    async def run(self):
        print("[01 注册表自检]")
        reset_all()

        from services.kb57_registry import (
            GAP_SIGNAL_REGISTRY, SIGNAL_SIDES,
            SOURCE_REGISTRY, SOURCE_TYPES,
            CREDIBILITY_REVIEW_LINE,
            registry_view, sources_view,
            get_signal, active_signals,
        )
        sides = {v["side"] for v
                 in GAP_SIGNAL_REGISTRY.values()}
        weight_sum = sum(
            v["weight"] for v
            in GAP_SIGNAL_REGISTRY.values()
            if v.get("status") == "active")
        record("信号 10 项",
               len(GAP_SIGNAL_REGISTRY) == 10,
               str(len(GAP_SIGNAL_REGISTRY)))
        record("五侧覆盖",
               sides == set(SIGNAL_SIDES),
               str(sorted(sides)))
        record("权重和=1.0",
               abs(weight_sum - 1.0) < 1e-9,
               str(weight_sum))
        record("active 域封闭",
               len(active_signals()) == 10,
               str(len(active_signals())))
        record("白名单外查询 None",
               get_signal("backdoor") is None,
               "")

        # 观测面视图(off 态可用——service 层组装)
        from services.kb57_service import Kb57Service
        view = Kb57Service.registry()
        record("registry 视图(含源+评分器)",
               view.get("total") == 10
               and len(view.get("sources") or [])
               == 6
               and (view.get("scorer") or {})
               .get("factors") == 8,
               str((view.get("total"),
                    len(view.get("sources") or []))))

        # 采集源
        record("采集源 6 项",
               len(SOURCE_REGISTRY) == 6,
               str(len(SOURCE_REGISTRY)))
        record("源类型合法",
               all(v["sourceType"] in SOURCE_TYPES
                   for v in
                   SOURCE_REGISTRY.values()),
               "")
        record("低可信度强制复审线",
               (SOURCE_REGISTRY.get(
                   "media_whitelist") or {})
               .get("credibility", 1.0)
               < CREDIBILITY_REVIEW_LINE,
               str(CREDIBILITY_REVIEW_LINE))
        src_view = sources_view()
        record("sources 视图",
               src_view.get("total") == 6
               and src_view.get(
                   "credibilityReviewLine")
               == CREDIBILITY_REVIEW_LINE,
               str(src_view.get("total")))


class TestScorer:
    """02 第32档案八因子评分器"""

    async def run(self):
        print("[02 八因子评分器]")
        reset_all()
        from services.kb57_scorer import (
            Kb57Scorer, SCORER_ID,
        )
        scorer = Kb57Scorer()

        # 八因子齐备
        record("八因子齐备",
               set(Kb57Scorer.WEIGHTS.keys()) == {
                   "signal_quality",
                   "necessity_score",
                   "budget_sufficiency",
                   "risk_posture",
                   "source_health",
                   "history_success",
                   "compliance_posture",
                   "human_load"},
               str(sorted(Kb57Scorer.WEIGHTS)))
        record("权重和=1.0",
               abs(sum(Kb57Scorer.WEIGHTS.values())
                   - 1.0) < 1e-9,
               str(sum(Kb57Scorer.WEIGHTS.values())))

        # urgent 切档(高必要性+多信号)
        r = await scorer.score({
            "signalHits": 6, "sideCoverage": 0.8,
            "necessityScore": 85,
            "budgetRemaining": 0.9,
            "sourceHealth": 0.95,
            "historySuccessRate": 0.9,
            "compliancePassRate": 0.95,
            "humanReviewQueue": 2,
        })
        record("urgent 切档(≥80)",
               r.get("decision") == "urgent"
               and r.get("trustScore") >= 80,
               str((r.get("decision"),
                    r.get("trustScore"))))
        record("因子明细八条",
               len(r.get("factors") or []) == 8,
               str(len(r.get("factors") or [])))

        # collect 切档
        r = await scorer.score({
            "signalHits": 3, "sideCoverage": 0.4,
            "necessityScore": 35,
        })
        record("collect 切档(≥50)",
               r.get("decision") == "collect",
               str((r.get("decision"),
                    r.get("trustScore"))))

        # defer 切档(低信号+低健康上下文)
        r = await scorer.score({
            "signalHits": 0, "necessityScore": 10,
            "budgetRemaining": 0.0,
            "sourceHealth": 0.1,
            "historySuccessRate": 0.1,
            "compliancePassRate": 0.1,
            "humanReviewQueue": 80,
            "alertDensity": 0.9})
        record("defer 切档(<50)",
               r.get("decision") == "defer",
               str(r.get("trustScore")))

        # 越界拒绝
        try:
            await scorer.score({
                "necessityScore": 150})
            ok, err = False, "未拒绝"
        except ValueError as e:
            ok, err = "[0,100]" in str(e), str(e)[:30]
        record("必要性越界拒绝", ok, err)
        try:
            await scorer.score({
                "sideCoverage": 2.0})
            ok, err = False, "未拒绝"
        except ValueError as e:
            ok, err = "[0,1]" in str(e), str(e)[:30]
        record("覆盖率越界拒绝", ok, err)
        try:
            await scorer.score({})
            ok, err = False, "未拒绝"
        except ValueError as e:
            ok, err = "不能为空" in str(e), str(e)[:30]
        record("空上下文拒绝", ok, err)


class TestDiagnose:
    """03 趋势诊断引擎"""

    async def run(self):
        print("[03 趋势诊断]")
        reset_all()
        from services.kb57_service import (
            Kb57Service, NECESSITY_GATE,
        )
        svc = Kb57Service()

        # off 拒绝
        try:
            await svc.scan_signals()
            ok, err = False, "未拒绝"
        except ValueError as e:
            ok, err = "off" in str(e), str(e)[:30]
        record("off 态诊断拒绝", ok, err)

        # 空环境 defer
        os.environ["KB57_MODE"] = "shadow"
        r = await svc.diagnose_and_plan()
        record("空环境 defer 留痕",
               r.get("decision") == "defer"
               and "gapId" not in r,
               str(r.get("decision")))
        from repositories.kb57_repository import (
            Kb57Repository,
        )
        events = await Kb57Repository().list_events(
            event_type="gap_scan", limit=10)
        record("defer 留痕(gap_scan 事件)",
               len(events) >= 1
               and (events[0].get("detail")
                    or {}).get("deferred") is True,
               str(len(events)))

        # 强信号 → 缺口创建
        reset_all()
        await seed_signals()
        r = await svc.diagnose_and_plan()
        record("强信号 collect 决策",
               r.get("decision") == "collect",
               str((r.get("decision"),
                    r.get("necessityScore"))))

        gap_id = r.get("gapId")
        record("缺口创建(gapId)",
               int(gap_id or 0) > 0,
               str(gap_id))

        gap = await Kb57Repository().get_gap(
            int(gap_id))
        record("缺口结构(open+优先级+预算)",
               gap.get("status") == "open"
               and gap.get("priority") in (
                   "high", "medium")
               and gap.get("budgetCap") == 0.1,
               str((gap.get("status"),
                    gap.get("priority"))))
        snap = gap.get("signalSnapshot") or {}
        record("信号快照留痕",
               len(snap.get("hits") or []) >= 3,
               str(len(snap.get("hits") or [])))
        record("建议采集源(白名单内)",
               len(gap.get("suggestedSources")
                   or []) >= 1,
               str(gap.get("suggestedSources")))
        record("缺口主题(knowledge 问题)",
               "补贴" in str(gap.get("topic")),
               str(gap.get("topic"))[:40])

        # gap_create 事件留痕
        events = await Kb57Repository().list_events(
            gap_id=int(gap_id), limit=10)
        create_evs = [e for e in events
                      if e.get("eventType")
                      == "gap_create"]
        record("gap_create 事件留痕",
               len(create_evs) == 1,
               str(len(create_evs)))

        # 缺口清单(观测面)
        listing = await svc.list_gaps()
        record("缺口清单(优先级排序)",
               (listing.get("total") or 0) == 1
               and (listing.get("gaps")
                    or [{}])[0].get("gapId")
               == gap_id,
               str(listing.get("total")))

        # 模型状态(44号复用)
        status = await svc.model_status()
        record("模型状态(第32档案)",
               (status.get("status") or {})
               .get("scorerId")
               == "knowledge_orchestration",
               str((status.get("status") or {})
                   .get("scorerId")))

        # 合规 negative 抑制(告警未决
        # → necessity 抑制仍可 defer)
        reset_all()
        os.environ["KB57_MODE"] = "shadow"
        from core.helpers import ts as _ts
        from repositories.ai_governance_repository \
            import AiGovernance46Repository
        grepo = AiGovernance46Repository()
        aid = await grepo.next_alert_id()
        await grepo.save_alert({
            "alertId": aid,
            "scorerId": "member_profile",
            "signal": "test",
            "day": _ts()[:10],
            "alertType": "test",
            "severity": "medium",
            "message": "kb57-negative-test",
            "status": "open",
            "createdAt": _ts(),
        })
        r = await svc.diagnose_and_plan()
        record("合规告警抑制(defer)",
               r.get("decision") == "defer",
               str((r.get("decision"),
                    r.get("necessityScore"))))
        os.environ["KB57_MODE"] = "off"


class TestSourceRegister:
    """04 采集源动态注册"""

    async def run(self):
        print("[04 采集源注册]")
        reset_all()
        from fastapi.testclient import TestClient
        from main import app
        client = TestClient(app)
        admin = {"X-Role": "admin"}

        # off 态注册拒绝(决策面)
        os.environ["KB57_MODE"] = "off"
        resp = client.post(
            "/api/kb57/sources/register",
            json={"sourceKey": "test_src",
                  "label": "测试源",
                  "sourceType": "partner",
                  "credibility": 0.8,
                  "license": "测试授权"},
            headers=admin)
        record("off 态注册 409",
               resp.status_code == 409,
               str(resp.status_code))

        # shadow 态注册
        os.environ["KB57_MODE"] = "shadow"
        resp = client.post(
            "/api/kb57/sources/register",
            json={"sourceKey": "test_src",
                  "label": "测试源",
                  "sourceType": "partner",
                  "credibility": 0.8,
                  "license": "测试授权"},
            headers=admin)
        body = resp.json() or {}
        record("注册 200(sourceId)",
               resp.status_code == 200
               and int(body.get("sourceId") or 0)
               > 0,
               str((resp.status_code,
                    body.get("sourceId"))))

        # 重复注册拒绝(封闭域幂等)
        resp = client.post(
            "/api/kb57/sources/register",
            json={"sourceKey": "test_src",
                  "label": "测试源2",
                  "sourceType": "partner",
                  "credibility": 0.8,
                  "license": "测试授权"},
            headers=admin)
        record("重复注册 409",
               resp.status_code == 409,
               str(resp.status_code))

        # 非法类型 422
        resp = client.post(
            "/api/kb57/sources/register",
            json={"sourceKey": "bad_src",
                  "label": "坏源",
                  "sourceType": "darkweb",
                  "credibility": 0.8,
                  "license": "无"},
            headers=admin)
        record("非法源类型 422",
               resp.status_code == 422,
               str(resp.status_code))

        # 低可信度复审标记
        resp = client.post(
            "/api/kb57/sources/register",
            json={"sourceKey": "low_src",
                  "label": "低可信源",
                  "sourceType": "media",
                  "credibility": 0.5,
                  "license": "标注转载"},
            headers=admin)
        body = resp.json() or {}
        record("低可信度强制复审",
               resp.status_code == 200
               and body.get("reviewRequired")
               is True,
               str(body.get("reviewRequired")))

        # 动态源出现在观测面
        resp = client.get("/api/kb57/sources",
                          headers=admin)
        body = resp.json() or {}
        record("动态源观测面(合并)",
               resp.status_code == 200
               and body.get("dynamicTotal") == 2,
               str(body.get("dynamicTotal")))

        # source_register 事件留痕
        from repositories.kb57_repository import (
            Kb57Repository,
        )
        events = await Kb57Repository().list_events(
            event_type="source_register", limit=10)
        record("source_register 事件留痕",
               len(events) == 2,
               str(len(events)))
        os.environ["KB57_MODE"] = "off"


class TestConstitution:
    """05 宪法断言"""

    async def run(self):
        print("[05 宪法断言]")
        reset_all()

        # 44号 32 档案
        from services.ai_learning_service import (
            SCORER_REGISTRY,
        )
        record("44号 32 档案",
               len(SCORER_REGISTRY) == 37,
               str(len(SCORER_REGISTRY)))
        record("第32档案在册",
               "knowledge_orchestration"
               in SCORER_REGISTRY,
               "")

        # 既有 knowledge_* 零改动(接口在册)
        from repositories.knowledge_repository \
            import KnowledgeRepository
        krepo = KnowledgeRepository()
        record("既有 knowledge_* 仓储可读",
               hasattr(krepo, "list_gaps")
               and hasattr(krepo, "stats"),
               "")

        # 51号零改动(KG_MODE 在册)
        from services.kg51_ontology import (
            current_mode as kg51_mode,
        )
        record("51号 KG_MODE 在册",
               kg51_mode() in (
                   "off", "shadow", "assist"),
               str(kg51_mode()))


class TestHttp:
    """06 HTTP 层"""

    async def run(self):
        print("[06 HTTP]")
        reset_all()
        from fastapi.testclient import TestClient
        from main import app
        client = TestClient(app)
        admin = {"X-Role": "admin"}

        # 观测面(off 可用)
        resp = client.get("/api/kb57/registry",
                          headers=admin)
        record("HTTP registry 200",
               resp.status_code == 200
               and (resp.json() or {}).get("total")
               == 10,
               str(resp.status_code))
        resp = client.get("/api/kb57/sources",
                          headers=admin)
        record("HTTP sources 200",
               resp.status_code == 200,
               str(resp.status_code))
        resp = client.get("/api/kb57/gaps",
                          headers=admin)
        record("HTTP gaps 200(空)",
               resp.status_code == 200
               and (resp.json() or {})
               .get("total") == 0,
               str(resp.status_code))
        resp = client.get("/api/kb57/model/status",
                          headers=admin)
        record("HTTP model/status 200",
               resp.status_code == 200
               and ((resp.json() or {})
                    .get("status") or {})
               .get("scorerId")
               == "knowledge_orchestration",
               str(resp.status_code))

        # 决策面 off 409
        resp = client.post("/api/kb57/gaps/scan",
                           headers=admin)
        record("HTTP gaps/scan off 409",
               resp.status_code == 409,
               str(resp.status_code))

        # shadow 态全链
        os.environ["KB57_MODE"] = "shadow"
        await seed_signals()
        resp = client.post("/api/kb57/gaps/scan",
                           headers=admin)
        body = resp.json() or {}
        record("HTTP gaps/scan 200(collect)",
               resp.status_code == 200
               and body.get("decision")
               == "collect"
               and int(body.get("gapId") or 0) > 0,
               str((resp.status_code,
                    body.get("decision"))))

        resp = client.get("/api/kb57/gaps",
                          headers=admin)
        record("HTTP gaps 200(1 缺口)",
               resp.status_code == 200
               and (resp.json() or {})
               .get("total") == 1,
               str((resp.json() or {}).get("total")))

        # 鉴权 403
        for method, path in (
                ("GET", "/api/kb57/registry"),
                ("GET", "/api/kb57/sources"),
                ("POST", "/api/kb57/gaps/scan"),
                ("GET", "/api/kb57/gaps"),
                ("GET", "/api/kb57/model/status")):
            resp = client.request(method, path)
            record(f"HTTP {path.split('/')[-1]}"
                   f" 无 Role 403",
                   resp.status_code == 403,
                   str(resp.status_code))
        os.environ["KB57_MODE"] = "off"


async def run_all():
    await TestRegistry().run()
    await TestScorer().run()
    await TestDiagnose().run()
    await TestSourceRegister().run()
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
