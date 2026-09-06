"""57号·AI智能知识库模块 P5 专项测试
(四区看板+红队七向量+宪法断言)

运行方式:
    python test_kb57_p5.py

覆盖(57号计划 §九 P5):
    - 四区看板: 度量五指标/种子库/合规/防御
    - 红队七向量: 知识投毒/白名单逃逸/PII 泄漏/
      种子污染/预算耗尽/越权/过期误导
    - 宪法断言: 44号 32 档案+注册表封闭+
      既有模块零改动
    - HTTP 层: 2 端点+鉴权+25 端点计数
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


async def seed_full_chain():
    """种一条全链数据(缺口→资源→鉴别→种子→
    发布→浏览→反馈→路径)"""
    import hashlib
    from core.helpers import ts
    from repositories.kb57_repository import (
        Kb57Repository,
    )
    repo = Kb57Repository()

    # 缺口(open)
    gap_id = await repo.next_gap_id()
    await repo.save_gap({
        "gapId": gap_id, "status": "open",
        "priority": "high", "topic": "p5-gap",
        "signalSnapshot": {
            "hits": [{"signalId": "kb_gap_open"}]},
        "suggestedSources": ["gov_policy_official"],
        "budgetCap": 0.1, "budgetSpent": 0.01,
        "createdAt": ts(), "updatedAt": ts(),
    })

    # 资源(compliant)+鉴别报告(passed)
    rid = await repo.next_resource_id()
    fp = "sha256:" + hashlib.sha256(
        f"p5-{rid}".encode("utf-8")
    ).hexdigest()[:32]
    await repo.save_resource({
        "resourceId": rid, "gapId": gap_id,
        "sourceId": "gov_policy_official",
        "sourceType": "authority",
        "sourceCredibility": 0.95,
        "license": "公开政务",
        "title": "p5-resource",
        "contentText": "content",
        "maskedText": "",
        "contentHash": "sha256:" + hashlib.sha256(
            f"ch-{rid}".encode("utf-8")
        ).hexdigest()[:32],
        "status": "compliant",
        "reviewRequired": False,
        "budgetHalted": False,
        "resourceVersion": 1,
        "complianceReports": [],
        "fingerprint": fp,
        "createdAt": ts(), "updatedAt": ts(),
    })
    cid = await repo.next_compliance_id()
    await repo.save_compliance({
        "complianceId": cid, "resourceId": rid,
        "gapId": gap_id, "verdict": "passed",
        "copyright": {"passed": True,
                      "violations": []},
        "privacy": {"passed": True,
                    "piiFound": 0},
        "contentSafety": {"passed": True,
                          "riskLevel": "low"},
        "gate": {"halted": False},
        "fingerprint": fp,
        "maskedFields": [],
        "budgetSpent": 0.01,
        "createdAt": ts(),
    })

    # 种子(published)+反馈
    sid = await repo.next_seed_id()
    await repo.save_seed({
        "seedId": sid, "seedVersion": 1,
        "type": "text", "title": "p5-seed",
        "content": {"text": "c", "mediaRef": None,
                    "transcript": None,
                    "keyframes": None, "alt": None},
        "contentHash": "sha256:x",
        "complianceFingerprint": fp,
        "valueTags": ["policy"],
        "sourceId": "gov_policy_official",
        "sourceCredibility": 0.95,
        "privacyCost": 0.002,
        "knowledgeReason": "p5",
        "humanVerified": True,
        "validUntil": "2099-01-01",
        "abTest": {"active": False,
                   "variantOf": None},
        "status": "published",
        "gapId": gap_id, "resourceId": rid,
        "viewCount": 3, "positiveCount": 2,
        "negativeCount": 0,
        "pooledFeedbackId": 1,
        "poolSignal": "seed_high_value",
        "poolReward": 0.8,
        "llmCalls": 0,
        "createdAt": ts(), "updatedAt": ts(),
    })
    fid = await repo.next_feedback_id()
    await repo.save_feedback({
        "feedbackId": fid, "seedId": sid,
        "memberId": 5001, "kind": "positive",
        "comment": "", "pooled": False,
        "createdAt": ts(),
    })

    # 路径(已完成)
    pid = await repo.next_path_id()
    await repo.save_path({
        "pathId": pid, "memberId": 5001,
        "title": "p5-course",
        "seedIds": [sid],
        "progress": {"completed": [sid],
                     "current": None},
        "completed": True,
        "createdAt": ts(), "updatedAt": ts(),
    })
    return {"gapId": gap_id, "resourceId": rid,
            "seedId": sid, "pathId": pid,
            "complianceId": cid}


class TestDashboard:
    """01 四区看板"""

    async def run(self):
        print("[01 四区看板]")
        reset_all()
        os.environ["KB57_MODE"] = "shadow"

        # 空态
        from services.kb57_dashboard_service import (
            Kb57DashboardService,
        )
        dash = Kb57DashboardService()
        empty = await dash.build()
        zones = empty.get("zones") or {}
        record("空态看板(四区齐备)",
               empty.get("success") is True
               and set(zones.keys()) == {
                   "metrics", "seeds",
                   "compliance", "defense"},
               str(sorted(zones.keys())))

        # 全链种子
        ids = await seed_full_chain()

        board = await dash.build()
        zones = board.get("zones") or {}

        # ① 度量区
        metrics = zones.get("metrics") or {}
        record("度量区(覆盖率+通过率)",
               metrics.get("coverageRate") == 0.0
               and metrics.get(
                   "compliancePassRate") == 1.0,
               str((metrics.get("coverageRate"),
                    metrics.get(
                        "compliancePassRate"))))
        record("度量区(有效率+转化率+增益)",
               metrics.get(
                   "seedEffectiveRate") == 1.0
               and metrics.get(
                   "learningConversionRate") == 1.0
               and metrics.get(
                   "valueGainTotal") == 1,
               str((metrics.get(
                         "seedEffectiveRate"),
                    metrics.get(
                        "valueGainTotal"))))
        basis = metrics.get("basis") or {}
        record("度量区基数(六维)",
               basis.get("gaps") == 1
               and basis.get("resources") == 1
               and basis.get("paths") == 1,
               str(basis))

        # ② 种子库区
        seeds_zone = zones.get("seeds") or {}
        record("种子库(1 种子+发布态)",
               seeds_zone.get("totalSeeds") == 1
               and (seeds_zone.get("byStatus")
                    or {}).get("published") == 1,
               str((seeds_zone.get("totalSeeds"),
                    seeds_zone.get("byStatus"))))
        record("种子库(浏览/反馈计数)",
               seeds_zone.get("viewCount") == 3
               and seeds_zone.get(
                   "positiveCount") == 2,
               str((seeds_zone.get("viewCount"),
                    seeds_zone.get(
                        "positiveCount"))))

        # ③ 合规区
        compliance = zones.get("compliance") or {}
        record("合规区(passed 1+零拦截)",
               (compliance.get("byVerdict") or {})
               .get("passed") == 1
               and compliance.get(
                   "copyrightBlocks") == 0
               and compliance.get(
                   "budgetHalts") == 0,
               str(compliance.get("byVerdict")))

        # ④ 防御区
        defense = zones.get("defense") or {}
        record("防御区(回流信号分布)",
               (defense.get("feedbackSignals")
                or {}).get("bySignal")
               == {"seed_high_value": 1},
               str((defense.get(
                        "feedbackSignals")
                    or {}).get("bySignal")))
        record("防御区(源集中度口径)",
               "topRatio" in str(
                   defense.get(
                       "sourceConcentration")
                   or {}),
               str(defense.get(
                   "sourceConcentration"))[:60])
        record("防御区(护栏健康第32档案)",
               (defense.get("guardrail") or {})
               .get("healthy") is True,
               str((defense.get("guardrail")
                    or {}).get("healthy")))

        # off 态看板亦可用(观测面)
        os.environ["KB57_MODE"] = "off"
        off_board = await dash.build()
        record("off 态看板亦可用(观测面)",
               off_board.get("success") is True
               and (off_board.get("zones")
                    or {}).get("metrics")
               is not None,
               str(off_board.get("success")))
        os.environ["KB57_MODE"] = "shadow"


class TestRedteam:
    """02 红队七向量"""

    async def run(self):
        print("[02 红队七向量]")
        reset_all()

        from services.kb57_redteam_service import (
            Kb57RedteamService,
        )
        rt = Kb57RedteamService()

        # off 拒绝(无攻击面)
        os.environ["KB57_MODE"] = "off"
        try:
            await rt.run_all()
            ok, err = False, "未拒绝"
        except ValueError as e:
            ok, err = "红队" in str(e), str(e)[:40]
        record("off 态红队拒绝(无攻击面)", ok, err)

        # 七向量全量(shadow 态——内部自行
        # 切换 assist 会员面向量)
        os.environ["KB57_MODE"] = "shadow"
        r = await rt.run_all()
        summary = r.get("summary") or {}
        vectors = r.get("vectors") or {}

        record("红队全量(七向量)",
               summary.get("total") == 7
               and len(vectors) == 7,
               str((summary.get("total"),
                    len(vectors))))
        record("七向量全防御(allDefended)",
               summary.get("allDefended") is True,
               str(summary))

        # 向量语义(七名齐备)
        record("七向量语义齐备",
               [v.get("vector") for v
                in vectors.values()] == [
                   "知识投毒(伪造缺口信号灌入)",
                   "白名单逃逸(未注册源)",
                   "PII 泄漏(未脱敏入库)",
                   "种子污染(非合规资源锻造)",
                   "预算耗尽(超支硬测)",
                   "入口越权(他人学习记录)",
                   "过期种子误导(有效期绕过)"],
               str([v.get("vector") for v
                    in vectors.values()]))

        # RT-01 投毒防御细节
        rt01 = vectors.get("RT-01") or {}
        record("RT-01 命中全白名单",
               rt01.get("defended") is True
               and all(
                   "backdoor" not in str(h)
                   for h in ((rt01.get("results")
                              or [{}])[0]
                             .get("hitSignalIds")
                             or [])),
               str((rt01.get("results") or [{}])
                   [0].get("hitSignalIds")))

        # RT-02 白名单三路
        rt02 = (vectors.get("RT-02")
                or {}).get("results") or []
        record("RT-02 三路(未注册拒+blocked+"
               "注册对照)",
               len(rt02) == 3
               and rt02[0].get("collected") == 0
               and rt02[1].get("verdict")
               == "blocked",
               str(rt02))

        # RT-03 PII 三路
        rt03 = (vectors.get("RT-03")
                or {}).get("results") or []
        record("RT-03 三路(脱敏+无泄漏+"
               "隔离拒浏览)",
               len(rt03) == 3
               and (rt03[0].get("maskedFields")
                    or 0) >= 3
               and rt03[1].get("leaked") is False
               and rt03[2].get("blocked") is True,
               str(rt03))

        # RT-04 污染三路
        rt04 = (vectors.get("RT-04")
                or {}).get("results") or []
        record("RT-04 三路全拒(quarantined/"
                "无指纹/rejected)",
               len(rt04) == 3
               and all(x.get("rejected")
                      for x in rt04),
               str(rt04))

        # RT-05 预算两路
        rt05 = (vectors.get("RT-05")
                or {}).get("results") or []
        record("RT-05 两路(halted+浏览拒)",
               len(rt05) == 2
               and rt05[0].get("verdict")
               == "halted"
               and rt05[1].get("rejected") is True,
               str(rt05))

        # RT-06 越权三路
        rt06 = (vectors.get("RT-06")
                or {}).get("results") or []
        record("RT-06 三路(推进拒+隔离+自域)",
               len(rt06) == 3
               and rt06[0].get("rejected") is True
               and rt06[1].get("isolated") is True,
               str(rt06))

        # RT-07 过期三路
        rt07 = (vectors.get("RT-07")
                or {}).get("results") or []
        record("RT-07 三路(降权+出池+入径拒)",
               len(rt07) == 3
               and rt07[0].get("status")
               == "downgraded"
               and rt07[1].get("inFeed") is False
               and rt07[2].get("rejected") is True,
               str(rt07))

        # 红队后注册表完整性
        from services.kb57_registry import (
            GAP_SIGNAL_REGISTRY,
        )
        record("红队后注册表完整(10 项)",
               len(GAP_SIGNAL_REGISTRY) == 10,
               str(len(GAP_SIGNAL_REGISTRY)))
        os.environ["KB57_MODE"] = "off"


class TestConstitution:
    """03 宪法断言"""

    async def run(self):
        print("[03 宪法断言]")
        reset_all()

        # 44号 32 档案
        from services.ai_learning_service import (
            SCORER_REGISTRY,
        )
        record("44号 32 档案",
               len(SCORER_REGISTRY) == 33,
               str(len(SCORER_REGISTRY)))
        record("第32档案在册",
               "knowledge_orchestration"
               in SCORER_REGISTRY,
               "")

        # 注册表封闭
        from services.kb57_registry import (
            GAP_SIGNAL_REGISTRY, SOURCE_REGISTRY,
        )
        sides = {v["side"] for v
                 in GAP_SIGNAL_REGISTRY.values()}
        weight_sum = sum(
            v["weight"] for v
            in GAP_SIGNAL_REGISTRY.values()
            if v.get("status") == "active")
        record("缺口信号 10 项五侧",
               len(GAP_SIGNAL_REGISTRY) == 10
               and sides == {
                   "business", "user",
                   "system", "compliance",
                   "model"},
               str((len(GAP_SIGNAL_REGISTRY),
                    sorted(sides))))
        record("权重和=1.0",
               abs(weight_sum - 1.0) < 1e-9,
               str(weight_sum))
        record("采集源 6 项封闭",
               len(SOURCE_REGISTRY) == 6,
               str(len(SOURCE_REGISTRY)))

        # 既有模块零改动
        from repositories.knowledge_repository \
            import KnowledgeRepository
        record("既有 knowledge_* 可读",
               hasattr(KnowledgeRepository(),
                       "list_gaps"),
               "")
        from services.kg51_ontology import (
            current_mode as kg51_mode,
        )
        record("51号 KG_MODE 在册",
               kg51_mode() in (
                   "off", "shadow", "assist"),
               str(kg51_mode()))


class TestHttp:
    """04 HTTP 层"""

    async def run(self):
        print("[04 HTTP]")
        reset_all()
        os.environ["KB57_MODE"] = "shadow"
        await seed_full_chain()

        from fastapi.testclient import TestClient
        from main import app
        client = TestClient(app)
        admin = {"X-Role": "admin"}

        # dashboard 200(观测面)
        resp = client.get("/api/kb57/dashboard",
                          headers=admin)
        body = resp.json() or {}
        zones = body.get("zones") or {}
        record("HTTP dashboard 200(四区)",
               resp.status_code == 200
               and set(zones.keys()) == {
                   "metrics", "seeds",
                   "compliance", "defense"},
               str((resp.status_code,
                    sorted(zones.keys()))))

        # off 态 dashboard 亦可用
        os.environ["KB57_MODE"] = "off"
        resp = client.get("/api/kb57/dashboard",
                          headers=admin)
        record("HTTP dashboard off 亦可用",
               resp.status_code == 200,
               str(resp.status_code))

        # off 态 redteam 409
        resp = client.post("/api/kb57/redteam",
                           headers=admin)
        record("HTTP redteam off 409",
               resp.status_code == 409,
               str(resp.status_code))

        # redteam 200(全防御)
        os.environ["KB57_MODE"] = "shadow"
        resp = client.post("/api/kb57/redteam",
                           headers=admin)
        body = resp.json() or {}
        record("HTTP redteam 200(全防御)",
               resp.status_code == 200
               and (body.get("summary") or {})
               .get("allDefended") is True,
               str((resp.status_code,
                    (body.get("summary") or {})
                    .get("allDefended"))))

        # 鉴权 403
        for method, path in (
                ("GET", "/api/kb57/dashboard"),
                ("POST", "/api/kb57/redteam")):
            resp = client.request(method, path)
            record(f"HTTP {path.split('/')[-1]}"
                   f" 无 Role 403",
                   resp.status_code == 403,
                   str(resp.status_code))

        # 路由累计 25 端点
        from routes.kb57_routes import (
            router as kb_router,
        )
        count = sum(1 for r in kb_router.routes)
        record("57号路由累计 25 端点",
               count == 25, str(count))
        os.environ["KB57_MODE"] = "off"


async def run_all():
    await TestDashboard().run()
    await TestRedteam().run()
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
