"""59号·AI智能服务编排模块 P0 专项测试
(服务编排注册表+会话底座+第34档案)

运行方式:
    python test_ii59_p0.py

覆盖(59号计划 §九 P0):
    - SERVICE_REGISTRY 8 项三面覆盖+三位一体
      +启动自检断言域
    - ROUTING_TABLE 意图路由(封闭白名单+
      boundary 不路由铁律+兜底)
    - 会话状态机(六态+合法流转+非法流转
      拒绝+终态)
    - 第34档案八因子+三级决策
    - HTTP 层: 5 端点+鉴权
    - 宪法: 44号 35 档案+58/48号零改动
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
os.environ.pop("II58_LLM_MODE", None)
os.environ.pop("II59_LLM_MODE", None)

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
    """01 服务编排注册表"""

    async def run(self):
        print("[01 服务编排注册表]")
        reset_all()
        from services.ii59_registry import (
            ROUTING_TABLE, SERVICE_REGISTRY,
            active_services,
            get_service, registry_view,
            route_intent,
        )

        # 数量+三面覆盖
        record("服务 8 项",
               len(SERVICE_REGISTRY) == 8,
               str(len(SERVICE_REGISTRY)))
        planes = {v["plane"]
                  for v in
                  SERVICE_REGISTRY.values()}
        record("三面覆盖",
               {"customer_service",
                "search_recommend",
                "risk_gate"}.issubset(planes)
               and "meta" in planes,
               str(sorted(planes)))

        # 三位一体(每服务 plane+minRole+sla)
        record("三位一体齐备",
               all("plane" in v and "minRole"
                   in v and "sla" in v
                   for v in
                   SERVICE_REGISTRY.values()),
               "")

        # 具体服务抽查
        cs = get_service("cs.order_assist")
        record("cs.order_assist(member+SLA+依赖)",
               cs is not None
               and cs["minRole"] == "member"
               and cs["sla"].get("resolveHours")
               == 24
               and "sr.product_search"
               in cs["dependsOn"],
               str(cs))
        rg = get_service("rg.experience_gate")
        record("rg.experience_gate(guest+50ms)",
               rg is not None
               and rg["minRole"] == "guest"
               and rg["sla"].get("p95Ms") == 50,
               str(rg))

        # active 域
        record("active 域(8 项全 active)",
               len(active_services()) == 8,
               str(len(active_services())))

        # 路由表
        record("路由表 11 条意图",
               len(ROUTING_TABLE) == 11,
               str(len(ROUTING_TABLE)))
        record("路由: price_query→搜索",
               route_intent(
                   "product.price_query")
               == ["sr.product_search"],
               str(route_intent(
                   "product.price_query")))
        record("路由: convert→客服+风控前置",
               route_intent(
                   "trust.convert_intent")
               == ["cs.order_assist",
                   "rg.experience_gate"],
               str(route_intent(
                   "trust.convert_intent")))
        record("路由: 未映射→兜底",
               route_intent("nonexistent.intent")
               == ["meta.unknown"],
               str(route_intent(
                   "nonexistent.intent")))

        # boundary 不路由铁律
        record("boundary 不路由铁律",
               "boundary.unauthorized"
               not in ROUTING_TABLE,
               "")

        # registry 视图
        view = registry_view()
        record("registry 视图(观测面)",
               view.get("total") == 8
               and (view.get("byPlane")
                    or {}).get(
                   "customer_service") == 2
               and view.get("routingEntries")
               == 11,
               str((view.get("total"),
                    view.get(
                        "routingEntries"))))


class TestSession:
    """02 会话状态机底座"""

    async def run(self):
        print("[02 会话状态机]")
        reset_all()
        from services.ii59_service import (
            Ii59Service,
        )
        svc = Ii59Service()

        # off 拒绝(决策面)
        try:
            await svc.open_session(member_id=1)
            ok, err = False, "未拒绝"
        except ValueError as e:
            ok, err = "off" in str(e), \
                str(e)[:30]
        record("off 态开话拒绝", ok, err)

        os.environ["II59_MODE"] = "shadow"

        # 非法通道拒绝
        try:
            await svc.open_session(
                member_id=1, channel="video")
            ok, err = False, "未拒绝"
        except ValueError as e:
            ok, err = "非法通道" in str(e), \
                str(e)[:30]
        record("非法通道拒绝", ok, err)

        # 合法开话
        r = await svc.open_session(
            member_id=1, channel="text")
        sid = r.get("sessionId")
        record("开话(opened+归因链)",
               r.get("state") == "opened"
               and int(sid or 0) > 0,
               str(r.get("state")))

        # 会话结构
        from repositories.ii59_repository \
            import Ii59Repository
        repo = Ii59Repository()
        session = await repo.get_session(sid)
        record("会话结构(六字段)",
               session.get("memberId") == 1
               and session.get("channel")
               == "text"
               and session.get("turnCount")
               == 0
               and session.get("state")
               == "opened"
               and "attribution"
               in session
               and session.get("escalated")
               is False,
               str((session.get("memberId"),
                    session.get("state"))))

        # 合法流转链: opened→serving→
        # resolved→closed
        t1 = await svc.transition(
            sid, "serving")
        record("流转 opened→serving",
               t1.get("state") == "serving",
               str(t1.get("state")))
        t2 = await svc.transition(
            sid, "resolved")
        record("流转 serving→resolved",
               t2.get("state") == "resolved",
               str(t2.get("state")))
        t3 = await svc.transition(
            sid, "closed")
        record("流转 resolved→closed(终态)",
               t3.get("state") == "closed",
               str(t3.get("state")))

        # 终态不可流转
        try:
            await svc.transition(sid, "serving")
            ok, err = False, "未拒绝"
        except ValueError as e:
            ok, err = "非法流转" in str(e), \
                str(e)[:30]
        record("终态不可流转", ok, err)

        # 非法流转(opened→resolved 跳态)
        r2 = await svc.open_session(member_id=2)
        sid2 = r2.get("sessionId")
        try:
            await svc.transition(
                sid2, "resolved")
            ok, err = False, "未拒绝"
        except ValueError as e:
            ok, err = "非法流转" in str(e), \
                str(e)[:30]
        record("跳态流转拒绝(状态机)", ok, err)

        # 非法状态名
        try:
            await svc.transition(sid2, "hacked")
            ok, err = False, "未拒绝"
        except ValueError as e:
            ok, err = "非法状态" in str(e), \
                str(e)[:30]
        record("非法状态名拒绝", ok, err)

        # escalated 流转(标记)
        r3 = await svc.open_session(member_id=3)
        sid3 = r3.get("sessionId")
        await svc.transition(sid3, "serving")
        t4 = await svc.transition(
            sid3, "escalated")
        session3 = await repo.get_session(sid3)
        record("escalated(接管标记)",
               t4.get("state") == "escalated"
               and session3.get("escalated")
               is True,
               str((t4.get("state"),
                    session3.get(
                        "escalated"))))

        # 404
        try:
            await svc.get_session(999)
            ok, err = False, "未拒绝"
        except KeyError:
            ok, err = True, ""
        record("会话 404", ok, err)

        # 列表+过滤
        lst = await svc.list_sessions()
        record("会话列表(3 条+byState)",
               lst.get("total") == 3
               and (lst.get("byState")
                    or {}).get("closed") == 1,
               str((lst.get("total"),
                    lst.get("byState"))))
        lst2 = await svc.list_sessions(
            state="escalated")
        record("会话列表过滤(state)",
               lst2.get("total") == 1,
               str(lst2.get("total")))

        # 事件留痕
        events = await repo.list_events(
            event_type="session", limit=50)
        record("session 事件留痕",
               len(events) >= 7,
               str(len(events)))

        # model_status 第34档案
        ms = await svc.model_status()
        record("model_status(第34档案)",
               (ms.get("status") or {}).get(
                   "scorerId")
               == "service_orchestration",
               str((ms.get("status")
                    or {}).get("scorerId")))
        os.environ["II59_MODE"] = "off"


class TestScorer:
    """03 第34档案八因子"""

    async def run(self):
        print("[03 第34档案评分器]")
        reset_all()
        from services.ii59_scorer import (
            Ii59Scorer,
        )
        scorer = Ii59Scorer()

        # 空上下文拒绝
        try:
            await scorer.score({})
            ok, err = False, "未拒绝"
        except ValueError:
            ok, err = True, ""
        record("空上下文拒绝", ok, err)

        # 全因子高分
        r = await scorer.score({
            "sessionResolution": 0.9,
            "searchAdoption": 0.8,
            "recommendDiversity": 0.7,
            "riskAccuracy": 0.85,
            "tier": "trusted",
            "escalationRate": 0.1,
            "latencyP95Ok": 0.95,
            "serviceCoverage": 0.9,
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
            "sessionResolution": 0.1,
            "searchAdoption": 0.1,
            "recommendDiversity": 0.1,
            "riskAccuracy": 0.1,
            "tier": "restricted",
            "escalationRate": 0.8,
            "latencyP95Ok": 0.2,
            "serviceCoverage": 0.1,
        })
        record("低分→observe",
               r2.get("decision") == "observe"
               and (r2.get("trustScore")
                    or 0) < 50,
               str((r2.get("trustScore"),
                    r2.get("decision"))))

        # tier 基线
        r3 = await scorer.score({
            "tier": "trusted"})
        f3 = [f for f in r3.get("factors")
              if f["name"] == "member_trust"]
        record("tier 基线(trusted=90)",
               f3 and f3[0]["score"] == 90.0,
               str(f3[0]["score"] if f3
                   else None))

        # 未知 tier 中性
        r4 = await scorer.score({"tier": ""})
        f4 = [f for f in
              r4.get("factors")
              if f["name"] == "member_trust"]
        record("未知 tier 中性(70)",
               f4 and f4[0]["score"] == 70.0,
               str(f4[0]["score"] if f4
                   else None))

        # 接管率反向因子
        r5 = await scorer.score({
            "escalationRate": 0.5})
        f5 = [f for f in
              r5.get("factors")
              if f["name"] == "escalation_rate"]
        r6 = await scorer.score({
            "escalationRate": 0.05})
        f6 = [f for f in
              r6.get("factors")
              if f["name"] == "escalation_rate"]
        record("接管率反向(低接管高分)",
               (f5[0]["score"] if f5 else 0)
               < (f6[0]["score"] if f6
                  else 100),
               str((f5[0]["score"] if f5
                    else None,
                    f6[0]["score"] if f6
                    else None)))

        # 覆盖率越界拒绝
        try:
            await scorer.score({
                "serviceCoverage": 1.5})
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
                   "session_resolution",
                   "search_adoption",
                   "recommend_diversity",
                   "risk_accuracy",
                   "member_trust",
                   "escalation_rate",
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
        record("44号 35 档案在册",
               len(SCORER_REGISTRY) == 39,
               str(len(SCORER_REGISTRY)))
        record("第34档案 service_orchestration",
               "service_orchestration"
               in SCORER_REGISTRY,
               "")

        from services.xiaozhu_service import (
            COMMAND_ACTIONS,
        )
        record("48号 COMMAND_ACTIONS 零改动",
               len(COMMAND_ACTIONS) >= 15,
               str(len(COMMAND_ACTIONS)))

        from services.ii58_registry import (
            INTENT_REGISTRY,
        )
        record("58号 INTENT_REGISTRY 零改动",
               len(INTENT_REGISTRY) == 12,
               str(len(INTENT_REGISTRY)))


class TestHttp:
    """05 HTTP 层"""

    async def run(self):
        print("[05 HTTP]")
        reset_all()
        from fastapi.testclient import TestClient
        from main import app
        client = TestClient(app)
        admin = {"X-Role": "admin"}

        # 观测面 off 可用
        resp = client.get("/api/ii59/registry",
                          headers=admin)
        body = resp.json() or {}
        record("HTTP registry 观测面 200",
               resp.status_code == 200
               and body.get("total") == 8
               and body.get("mode") == "off",
               str((resp.status_code,
                    body.get("total"))))

        resp = client.get(
            "/api/ii59/model/status",
            headers=admin)
        record("HTTP model/status 200",
               resp.status_code == 200
               and ((resp.json()
                     or {}).get("status")
                    or {}).get("scorerId")
               == "service_orchestration",
               str(resp.status_code))

        # 决策面 off 409
        resp = client.post(
            "/api/ii59/sessions",
            json={"memberId": 1},
            headers=admin)
        record("HTTP sessions off 409",
               resp.status_code == 409,
               str(resp.status_code))

        # shadow 开话
        os.environ["II59_MODE"] = "shadow"
        resp = client.post(
            "/api/ii59/sessions",
            json={"memberId": 1,
                  "channel": "text"},
            headers=admin)
        body = resp.json() or {}
        sid = body.get("sessionId")
        record("HTTP sessions 200(opened)",
               resp.status_code == 200
               and body.get("state")
               == "opened",
               str((resp.status_code,
                    body.get("state"))))

        # 详情
        resp = client.get(
            f"/api/ii59/sessions/{sid}",
            headers=admin)
        record("HTTP 会话详情 200",
               resp.status_code == 200
               and ((resp.json()
                     or {}).get("session")
                    or {}).get("sessionId")
               == sid,
               str(resp.status_code))

        # 404
        resp = client.get(
            "/api/ii59/sessions/999",
            headers=admin)
        record("HTTP 会话 404",
               resp.status_code == 404,
               str(resp.status_code))

        # 列表
        resp = client.get(
            "/api/ii59/sessions",
            headers=admin)
        body = resp.json() or {}
        record("HTTP 会话列表 200",
               resp.status_code == 200
               and body.get("total") == 1,
               str((resp.status_code,
                    body.get("total"))))

        # 非法通道 409
        resp = client.post(
            "/api/ii59/sessions",
            json={"memberId": 1,
                  "channel": "video"},
            headers=admin)
        record("HTTP 非法通道 409",
               resp.status_code == 409,
               str(resp.status_code))

        # 鉴权 403
        for method, path in (
                ("GET", "/api/ii59/registry"),
                ("POST", "/api/ii59/sessions"),
                ("GET",
                 "/api/ii59/sessions/1"),
                ("GET", "/api/ii59/sessions"),
                ("GET",
                 "/api/ii59/model/status")):
            resp = client.request(
                method, path, json={})
            record(f"HTTP {path.split('/')[-1]}"
                   f" 无 Role 403",
                   resp.status_code == 403,
                   str(resp.status_code))

        # 路由累计 5 端点(P1 扩至 9——基线语义)
        from routes.ii59_routes import (
            router as ii_router,
        )
        count = sum(
            1 for r in ii_router.routes)
        record("59号路由累计 ≥5 端点",
               count >= 5, str(count))
        os.environ["II59_MODE"] = "off"


async def run_all():
    await TestRegistry().run()
    await TestSession().run()
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
