"""58号·AI智能优化意图识别模块 P0 专项测试
(意图注册表+置信度引擎+识别底座)

运行方式:
    python test_ii58_p0.py

覆盖(58号计划 §九 P0):
    - 意图注册表: 12 项三位一体+启动自检红线
    - 第33档案八因子评分器: 三级决策切档
    - 置信度引擎: 语料匹配+动态阈值+三态响应
    - off 拒绝+空语料 clarify+归因链
    - 宪法: 44号 35 档案+48/55号零改动
    - HTTP 层: 5 端点+鉴权
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


async def seed_corpus(intent_id: str, texts: list,
                      weight: float = 1.0,
                      sample_type: str = "positive",
                      confusable_target: str = None
                      ) -> list:
    """种语料(匹配测试输入)"""
    from core.helpers import ts
    from repositories.ii58_repository import (
        Ii58Repository,
    )
    repo = Ii58Repository()
    ids = []
    for text in texts:
        corpus_id = await repo.next_corpus_id()
        await repo.save_corpus({
            "corpusId": corpus_id,
            "corpusVersion": 1,
            "intentId": intent_id,
            "sampleType": sample_type,
            "text": text,
            "weight": weight,
            "source": "manual",
            "originRef": "",
            "confusableTarget":
                confusable_target,
            "humanVerified": True,
            "humanSuggested": False,
            "status": "active",
            "createdAt": ts(),
            "updatedAt": ts(),
        })
        ids.append(corpus_id)
    return ids


class TestRegistry:
    """01 意图注册表"""

    async def run(self):
        print("[01 意图注册表]")
        reset_all()

        from services.ii58_registry import (
            INTENT_REGISTRY, INTENT_SIDES,
            SANDBOX_LEVELS, ROLE_VALUES,
            registry_view, get_intent,
            active_intents,
        )
        sides = {v["side"] for v
                 in INTENT_REGISTRY.values()}
        record("意图 12 项",
               len(INTENT_REGISTRY) == 12,
               str(len(INTENT_REGISTRY)))
        record("四侧覆盖",
               sides == set(INTENT_SIDES),
               str(sorted(sides)))
        record("active 域封闭",
               len(active_intents()) == 12,
               str(len(active_intents())))
        record("白名单外查询 None",
               get_intent("backdoor") is None,
               "")

        # 三位一体结构
        record("三位一体(权限+沙箱+模板齐备)",
               all(v.get("minRole")
                   and v.get("sandbox")
                   in SANDBOX_LEVELS
                   and v.get("complianceTemplate")
                   for v in
                   INTENT_REGISTRY.values()),
               "")
        record("minRole 域合法",
               all(v.get("minRole")
                   in ROLE_VALUES
                   for v in
                   INTENT_REGISTRY.values()),
               "")

        # 易混淆对双向对称(启动自检已验证——
        # 抽查一对)
        pq = INTENT_REGISTRY.get(
            "product.price_query") or {}
        record("易混淆对双向对称"
               "(price_query↔new_query)",
               "product.new_query"
               in (pq.get("confusableWith")
                   or []),
               str(pq.get(
                   "confusableWith")))

        # 观测面视图
        view = registry_view()
        record("registry 视图(混淆对呈现)",
               view.get("total") == 12
               and len(view.get(
                   "confusablePairs") or [])
               >= 2
               and (view.get("meta") or {})
               .get("sandboxLevels")
               == list(SANDBOX_LEVELS),
               str(len(view.get(
                   "confusablePairs") or [])))

        # 越界元意图
        boundary = INTENT_REGISTRY.get(
            "boundary.unauthorized") or {}
        record("越界元意图(deny 沙箱)",
               boundary.get("sandbox") == "deny",
               str(boundary.get("sandbox")))

        # service 层 registry 视图(off 可用)
        from services.ii58_service import (
            Ii58Service,
        )
        svc_view = Ii58Service.registry()
        record("service registry(含评分器+置信度)",
               (svc_view.get("scorer") or {})
               .get("factors") == 8
               and (svc_view.get("confidence")
                    or {}).get("baseUpper") == 0.9,
               str((svc_view.get("scorer")
                    or {}).get("factors")))


class TestScorer:
    """02 第33档案八因子评分器"""

    async def run(self):
        print("[02 八因子评分器]")
        reset_all()
        from services.ii58_scorer import (
            Ii58Scorer,
        )
        scorer = Ii58Scorer()

        # 八因子齐备
        record("八因子齐备",
               set(Ii58Scorer.WEIGHTS.keys()) == {
                   "corpus_quality",
                   "intent_confidence",
                   "member_trust",
                   "boundary_clarity",
                   "history_success",
                   "compliance_posture",
                   "latency_budget",
                   "coverage_breadth"},
               str(sorted(
                   Ii58Scorer.WEIGHTS.keys())))
        record("权重和=1.0",
               abs(sum(Ii58Scorer.WEIGHTS.values())
                   - 1.0) < 1e-9,
               str(sum(
                   Ii58Scorer.WEIGHTS.values())))

        # urgent 切档
        r = await scorer.score({
            "positiveRatio": 0.9,
            "humanVerifiedRatio": 0.95,
            "avgConfidence": 0.92,
            "tier": "trusted",
            "boundaryAccuracy": 0.95,
            "historySuccessRate": 0.9,
            "boundaryInterceptRate": 0.95,
            "latencyP95Ok": 0.98,
            "intentCoverage": 0.9})
        record("urgent 切档(≥80)",
               r.get("decision") == "urgent"
               and r.get("trustScore") >= 80,
               str((r.get("decision"),
                    r.get("trustScore"))))
        record("因子明细八条",
               len(r.get("factors") or []) == 8,
               str(len(r.get("factors")
                        or [])))

        # optimize 切档
        r = await scorer.score({
            "avgConfidence": 0.75,
            "tier": "standard"})
        record("optimize 切档(≥50)",
               r.get("decision") == "optimize",
               str((r.get("decision"),
                    r.get("trustScore"))))

        # observe 切档(低置信+restricted 拉低)
        r = await scorer.score(
            {"avgConfidence": 0.1,
             "tier": "restricted"})
        record("observe 切档(<50)",
               r.get("decision") == "observe",
               str(r.get("trustScore")))

        # tier 基线映射
        r = await scorer.score({"tier": "trusted"})
        factors = {f["name"]: f for f in
                   r.get("factors") or []}
        record("tier 基线(trusted=90)",
               factors.get("member_trust",
                           {}).get("score") == 90.0,
               str(factors.get(
                   "member_trust")))

        # 越界拒绝
        try:
            await scorer.score({"tier": "hacker"})
            ok = True   # tier 未知走中性——合法
        except ValueError:
            ok = False
        record("未知 tier 中性(不拒)",
               ok, "")
        try:
            await scorer.score(
                {"intentCoverage": 1.5})
            ok, err = False, "未拒绝"
        except ValueError as e:
            ok, err = "[0,1]" in str(e), str(e)[:30]
        record("覆盖率越界拒绝", ok, err)
        try:
            await scorer.score({})
            ok, err = False, "未拒绝"
        except ValueError as e:
            ok, err = "不能为空" in str(e), \
                str(e)[:30]
        record("空上下文拒绝", ok, err)


class TestEngine:
    """03 置信度引擎"""

    async def run(self):
        print("[03 置信度引擎]")
        reset_all()
        from services.ii58_service import (
            Ii58Service,
        )
        svc = Ii58Service()

        # off 拒绝
        try:
            await svc.evaluate("查价格")
            ok, err = False, "未拒绝"
        except ValueError as e:
            ok, err = "off" in str(e), str(e)[:30]
        record("off 态评估拒绝", ok, err)

        # 空文本拒绝
        os.environ["II58_MODE"] = "shadow"
        try:
            await svc.evaluate("  ")
            ok, err = False, "未拒绝"
        except ValueError as e:
            ok, err = "不能为空" in str(e), \
                str(e)[:30]
        record("空文本拒绝", ok, err)

        # 空语料 clarify(澄清优于错误执行)
        r = await svc.evaluate("这个产品多少钱")
        record("空语料 clarify(铁律)",
               r.get("state") == "clarify"
               and r.get("confidence") == 0.0,
               str((r.get("state"),
                    r.get("confidence"))))
        record("空语料意图(unknown 域)",
               r.get("intentId")
               == "unknown.unrecognized",
               str(r.get("intentId")))

        # 种语料(强正样本×3——共识加成)
        await seed_corpus(
            "product.price_query",
            ["多少钱", "价格", "多少钱一台"])

        # resolved(全含命中)
        r = await svc.evaluate(
            "这个产品多少钱")
        record("resolved(全含+共识)",
               r.get("state") == "resolved",
               str((r.get("state"),
                    r.get("confidence"))))
        record("意图命中(price_query)",
               r.get("intentId")
               == "product.price_query",
               str(r.get("intentId")))

        # partial(弱命中)
        r = await svc.evaluate(
            "价钱大概是怎么样")
        record("partial/clarify(弱命中)",
               r.get("state") in ("partial",
                                  "clarify"),
               str(r.get("state")))

        # 无关文本 clarify
        r = await svc.evaluate(
            "今天天气不错啊")
        record("无关文本 clarify",
               r.get("state") == "clarify",
               str(r.get("state")))

        # 归因链
        from repositories.ii58_repository import (
            Ii58Repository,
        )
        repo = Ii58Repository()
        r2 = await svc.evaluate(
            "这个产品多少钱")
        stored = await repo.get_evaluation(
            r2.get("evalId"))
        attribution = stored.get(
            "attribution") or {}
        record("归因链(corpusIds+track+tier)",
               len(attribution.get(
                   "corpusIds") or []) >= 1
               and attribution.get("track")
               == "corpus"
               and attribution.get("tier")
               == "standard",
               str(attribution)[:60])
        record("归因阈值快照(upper/lower)",
               (attribution.get(
                   "thresholds") or {})
               .get("upper") == 0.9,
               str(attribution.get(
                   "thresholds")))

        # 多意图竞争(候选澄清)
        await seed_corpus(
            "trust.balance_query", ["余额"])
        r3 = await svc.evaluate("多少钱")
        # price_query 语料强命中——仍 resolved
        record("多意图竞争(强意图胜出)",
               r3.get("intentId")
               == "product.price_query",
               str(r3.get("intentId")))

        # 对抗样本否决(price_modify 对抗降权)
        await seed_corpus(
            "product.price_query",
            ["修改价格"],
            sample_type="adversarial",
            confusable_target=(
                "product.price_modify"))
        # 注: 对抗样本 text=修改价格 意图归
        # price_query——输入"修改价格"会命中
        # 对抗文本+price_query——按否决逻辑
        # price_query 是混淆方之一被降权
        r4 = await svc.evaluate("修改价格")
        # 期望: 置信度被对抗否决拉低(≤原值)
        record("对抗否决(置信度受抑)",
               r4.get("confidence")
               <= r3.get("confidence"),
               str((r4.get("confidence"),
                    r3.get("confidence"))))

        # 动态阈值(trusted 会员——需 47号档案)
        # tier 联动 fail-soft: 无档案走 standard
        r5 = await svc.evaluate(
            "这个产品多少钱",
            member_id=99999)
        record("tier fail-soft(standard 基线)",
               r5.get("tier") == "standard",
               str(r5.get("tier")))

        # evaluate 事件留痕
        events = await repo.list_events(
            event_type="evaluate", limit=50)
        record("evaluate 事件留痕",
               len(events) >= 5,
               str(len(events)))

        # 识别记录列表(观测面)
        listing = await svc.list_evaluations()
        record("识别记录列表(≥5)",
               (listing.get("total") or 0) >= 5,
               str(listing.get("total")))
        listing2 = await \
            svc.list_evaluations(state="resolved")
        record("按状态过滤(resolved)",
               all(e.get("state") == "resolved"
                   for e in (
                       listing2.get(
                           "evaluations")
                       or [])),
               str(listing2.get("total")))

        # 模型状态(44号复用)
        status = await svc.model_status()
        record("模型状态(第33档案)",
               (status.get("status") or {})
               .get("scorerId")
               == "intent_orchestration",
               str((status.get("status") or {})
                   .get("scorerId")))

        # 槽位抽取(keyword 引号)
        r6 = await svc.evaluate(
            "查一下「红米手机」多少钱")
        record("槽位抽取(keyword 引号)",
               (r6.get("slots") or {})
               .get("keyword") == "红米手机",
               str(r6.get("slots")))
        os.environ["II58_MODE"] = "off"


class TestConstitution:
    """04 宪法断言"""

    async def run(self):
        print("[04 宪法断言]")
        reset_all()

        # 44号 35 档案
        from services.ai_learning_service import (
            SCORER_REGISTRY,
        )
        record("44号 35 档案",
               len(SCORER_REGISTRY) == 35,
               str(len(SCORER_REGISTRY)))
        record("第33档案在册"
               "(intent_orchestration)",
               "intent_orchestration"
               in SCORER_REGISTRY,
               "")

        # 48号零改动(COMMAND_ACTIONS 在册)
        from services.xiaozhu_service import (
            COMMAND_ACTIONS,
        )
        record("48号指令轨零改动"
               "(COMMAND_ACTIONS 在册)",
               len(COMMAND_ACTIONS) >= 15,
               str(len(COMMAND_ACTIONS)))

        # 55号零改动(意图常量在册)
        from services.qr55_registry import (
            SERVICE_REGISTRY,
        )
        record("55号意图引擎零改动"
               "(SERVICE_REGISTRY 在册)",
               len(SERVICE_REGISTRY) >= 10,
               str(len(SERVICE_REGISTRY)))


class TestHttp:
    """05 HTTP 层"""

    async def run(self):
        print("[05 HTTP]")
        reset_all()
        from fastapi.testclient import TestClient
        from main import app
        client = TestClient(app)
        admin = {"X-Role": "admin"}

        # 观测面(off 可用)
        resp = client.get("/api/ii58/registry",
                          headers=admin)
        record("HTTP registry 200",
               resp.status_code == 200
               and (resp.json() or {}).get("total")
               == 12,
               str(resp.status_code))
        resp = client.get(
            "/api/ii58/evaluations",
            headers=admin)
        record("HTTP evaluations 200(空)",
               resp.status_code == 200
               and (resp.json() or {})
               .get("total") == 0,
               str(resp.status_code))
        resp = client.get(
            "/api/ii58/model/status",
            headers=admin)
        record("HTTP model/status 200",
               resp.status_code == 200
               and ((resp.json() or {})
                    .get("status") or {})
               .get("scorerId")
               == "intent_orchestration",
               str(resp.status_code))

        # 决策面 off 409
        resp = client.post(
            "/api/ii58/evaluate",
            json={"text": "查价格"},
            headers=admin)
        record("HTTP evaluate off 409",
               resp.status_code == 409,
               str(resp.status_code))

        # shadow 态全链
        os.environ["II58_MODE"] = "shadow"
        await seed_corpus(
            "product.price_query",
            ["多少钱", "价格"])
        resp = client.post(
            "/api/ii58/evaluate",
            json={"text": "这个产品多少钱"},
            headers=admin)
        body = resp.json() or {}
        record("HTTP evaluate 200(resolved)",
               resp.status_code == 200
               and body.get("state") == "resolved",
               str((resp.status_code,
                    body.get("state"))))
        eval_id = body.get("evalId")

        resp = client.get(
            "/api/ii58/evaluations",
            headers=admin)
        record("HTTP evaluations 200(1 条)",
               resp.status_code == 200
               and (resp.json() or {})
               .get("total") == 1,
               str((resp.json() or {})
                   .get("total")))

        resp = client.get(
            f"/api/ii58/evaluations/{eval_id}",
            headers=admin)
        record("HTTP evaluations/{id} 200",
               resp.status_code == 200
               and bool(
                   (resp.json() or {})
                   .get("evaluation")),
               str(resp.status_code))

        # 404
        resp = client.get(
            "/api/ii58/evaluations/999",
            headers=admin)
        record("HTTP 详情 404",
               resp.status_code == 404,
               str(resp.status_code))

        # 鉴权 403
        for method, path in (
                ("GET", "/api/ii58/registry"),
                ("POST", "/api/ii58/evaluate"),
                ("GET", "/api/ii58/evaluations"),
                ("GET",
                 "/api/ii58/model/status")):
            resp = client.request(
                method, path, json={})
            record(f"HTTP {path.split('/')[-1]}"
                   f" 无 Role 403",
                   resp.status_code == 403,
                   str(resp.status_code))

        # 路由累计 5 端点(P1 扩至 11——基线语义)
        from routes.ii58_routes import (
            router as ii_router,
        )
        count = sum(1 for r in ii_router.routes)
        record("58号路由累计 ≥5 端点",
               count >= 5, str(count))
        os.environ["II58_MODE"] = "off"


async def run_all():
    await TestRegistry().run()
    await TestScorer().run()
    await TestEngine().run()
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
