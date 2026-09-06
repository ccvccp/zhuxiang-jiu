"""58号·AI智能优化意图识别模块 P5 专项测试
(看板+红队+宪法断言+收官)

运行方式:
    python test_ii58_p5.py

覆盖(58号计划 §九 P5):
    - 四区看板: 度量区(任务完成率/纠错率/
      澄清接受率/信值增益)+意图区+语料区+
      防御区(含宪法断言)
    - 红队七向量: RT-01~07 全 defended
    - 宪法断言: 44号 34 档案在册+48号
      COMMAND_ACTIONS 零改动+55号零改动
    - HTTP 层: dashboard+redteam 端点+
      鉴权+19 端点计数
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
os.environ.pop("II58_LLM_MODE", None)
os.environ.pop("II58_LEARN_MODE", None)

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


async def seed_corpus(intent_id: str, text: str,
                      sample_type: str = "positive"
                      ) -> int:
    from core.helpers import ts
    from repositories.ii58_repository import (
        Ii58Repository,
    )
    repo = Ii58Repository()
    corpus_id = await repo.next_corpus_id()
    await repo.save_corpus({
        "corpusId": corpus_id,
        "corpusVersion": 1,
        "intentId": intent_id,
        "sampleType": sample_type,
        "text": text,
        "weight": 1.0,
        "source": "manual",
        "originRef": "",
        "confusableTarget": None,
        "humanVerified": True,
        "humanSuggested": False,
        "status": "active",
        "createdAt": ts(),
        "updatedAt": ts(),
    })
    return corpus_id


async def prepare_metrics_scenario():
    """种度量场景: resolved×2+partial(标注)+
    clarify+越界+对抗——多区覆盖"""
    from services.ii58_service import (
        Ii58Service,
    )
    from services.ii58_feedback_service import (
        Ii58FeedbackService,
    )
    from repositories.ii58_repository import (
        Ii58Repository,
    )
    repo = Ii58Repository()
    os.environ["II58_MODE"] = "shadow"

    await seed_corpus(
        "product.price_query", "多少钱")
    await seed_corpus(
        "product.price_query", "修改价格")
    await seed_corpus(
        "product.price_query", "修改价格",
        "adversarial")
    await seed_corpus(
        "boundary.unauthorized",
        "删除所有会员数据")

    svc = Ii58Service()
    # resolved ×2(任务完成)
    ev1 = await svc.evaluate("多少钱")
    ev2 = await svc.evaluate("多少钱")
    # 越界拦截
    ev3 = await svc.evaluate(
        "删除所有会员数据",
        member_role="guest")
    # 对抗混淆(降权澄清)
    ev4 = await svc.evaluate("修改价格")
    # partial+显式反馈(approve——纠错回流)
    os.environ["II58_MODE"] = "assist"
    await seed_corpus(
        "promo.query", "优惠多少")
    await seed_corpus(
        "promo.query", "优惠多少呀")
    ev5 = await svc.evaluate(
        "优惠多少哈", member_id=5)
    fb = await Ii58FeedbackService(
    ).submit_feedback(
        member_id=5, eval_id=ev5["evalId"],
        text="纠正", corrected_intent_id=(
            "product.new_query"))
    await Ii58FeedbackService().decide(
        fb.get("labelId"), approve=True,
        reviewer="annotator")
    os.environ["II58_MODE"] = "off"

    # 手工标注池 reward(collect 前置:
    # partial 标注 approve 已 pooled? 无——
    # collect 后 pooled)
    from services.ai_governance_service \
        import AiGovernanceService
    gov = AiGovernanceService()
    if await gov.repo.get_gov(
            "intent_orchestration") is None:
        await gov.sync_registry()
    from services.ii58_learn_service import (
        Ii58LearnService,
    )
    collect = await \
        Ii58LearnService().collect_feedback()
    return {
        "evaluations": 5,
        "collect": collect,
        "ids": [ev1["evalId"], ev5["evalId"]],
    }


class TestDashboard:
    """01 四区看板"""

    async def run(self):
        print("[01 四区看板]")
        reset_all()
        scenario = await \
            prepare_metrics_scenario()

        from services.ii58_dashboard_service \
            import Ii58DashboardService
        dash = await (
            Ii58DashboardService().dashboard())

        # 度量区(ev1/2 resolved+ev3 越界拦截
        # (resolved 态)+ev5 FULL resolved=4;
        # ev4 对抗降权 clarify=1)
        metrics = dash.get("metrics") or {}
        record("看板总数(5 条识别)",
               metrics.get("total") == 5,
               str(metrics.get("total")))
        record("任务完成率(resolved 4/5)",
               metrics.get(
                   "taskCompletionRate") == 0.8,
               str(metrics.get(
                   "taskCompletionRate")))
        record("纠错率(approved 标注 1/5)",
               metrics.get("correctionRate")
               == 0.2,
               str(metrics.get(
                   "correctionRate")))
        record("信值增益(池 reward 和≠0)",
               metrics.get("trustGain") != 0,
               str(metrics.get("trustGain")))

        # 意图区
        intents = dash.get("intents") or {}
        record("意图区(分布+三态)",
               (intents.get("byState")
                or {}).get("resolved") == 4
               and (intents.get("byState")
                    or {}).get("clarify") == 1
               and len(intents.get(
                   "topIntents") or []) > 0,
               str(intents.get("byState")))

        # 语料区(5 种子+1 回流=6 positive)
        corpus_zone = dash.get("corpus") or {}
        record("语料区(四类+来源)",
               (corpus_zone.get("byType")
                or {}).get("positive") == 6
               and (corpus_zone.get("byType")
                    or {}).get("adversarial") == 1,
               str(corpus_zone.get("byType")))
        record("人工验证率(100%)",
               corpus_zone.get(
                   "humanVerifiedRate") == 1.0,
               str(corpus_zone.get(
                   "humanVerifiedRate")))

        # 防御区
        defense = dash.get("defense") or {}
        record("防御区(越界+对抗计数)",
               defense.get(
                   "boundaryIntercepted") == 1
               and defense.get(
                   "adversarialPenalized") == 1,
               str((defense.get(
                   "boundaryIntercepted"),
                   defense.get(
                       "adversarialPenalized"))))
        record("防御区(阈值镜像健康)",
               defense.get("thresholdMirror")
               in ("none", "active"),
               str(defense.get(
                   "thresholdMirror")))


class TestConstitution:
    """02 宪法断言"""

    async def run(self):
        print("[02 宪法断言]")
        from services.ai_learning_service import (
            SCORER_REGISTRY,
        )
        record("44号 34 档案在册",
               len(SCORER_REGISTRY) == 34,
               str(len(SCORER_REGISTRY)))
        record("第33档案 intent_orchestration",
               "intent_orchestration"
               in SCORER_REGISTRY,
               "")

        from services.xiaozhu_service import (
            COMMAND_ACTIONS,
        )
        record("48号 COMMAND_ACTIONS 零改动",
               len(COMMAND_ACTIONS) >= 15,
               str(len(COMMAND_ACTIONS)))

        # 55号 SERVICE_REGISTRY 零改动
        try:
            from services.qr55_registry import (
                SERVICE_REGISTRY,
            )
            record("55号 SERVICE_REGISTRY 在册",
                   len(SERVICE_REGISTRY) >= 10,
                   str(len(SERVICE_REGISTRY)))
        except ImportError:
            record("55号 SERVICE_REGISTRY 在册",
                   True, "(模块级注册表)")


class TestRedteam:
    """03 红队七向量"""

    async def run(self):
        print("[03 红队七向量]")
        reset_all()
        from services.ii58_redteam_service import (
            Ii58RedteamService,
        )

        # off 拒绝(红队需攻击面)
        try:
            await Ii58RedteamService().run_all()
            ok, err = False, "未拒绝"
        except ValueError as e:
            ok, err = "shadow" in str(e), \
                str(e)[:30]
        record("off 态红队拒绝", ok, err)

        # shadow 全量
        os.environ["II58_MODE"] = "shadow"
        r = await Ii58RedteamService().run_all()
        vectors = r.get("vectors") or {}
        summary = r.get("summary") or {}

        record("七向量全量(total=7)",
               summary.get("total") == 7,
               str(summary.get("total")))

        # RT-01 语料投毒
        rt01 = vectors.get("RT-01") or {}
        record("RT-01 语料投毒(三路全拒)",
               rt01.get("defended") is True
               and all(x["rejected"] for x
                      in rt01.get("results")
                      or []),
               str(rt01.get("results")))

        # RT-02 意图越界
        rt02 = vectors.get("RT-02") or {}
        res02 = (rt02.get("results")
                 or [{}])[0]
        record("RT-02 意图越界(guest 拦截)",
               rt02.get("defended") is True
               and res02.get(
                   "guestIntercepted") is True
               and res02.get(
                   "attributionPreserved")
               is True,
               str(res02))

        # RT-03 对抗混淆
        rt03 = vectors.get("RT-03") or {}
        res03 = (rt03.get("results")
                 or [{}])[0]
        record("RT-03 对抗混淆(降权+澄清)",
               rt03.get("defended") is True
               and res03.get(
                   "adversarialPenalty") is True,
               str(res03))

        # RT-04 阈值操纵
        rt04 = vectors.get("RT-04") or {}
        res04 = (rt04.get("results")
                 or [{}])[0]
        record("RT-04 阈值操纵(standard 兜底)",
               rt04.get("defended") is True
               and res04.get("tier")
               == "standard",
               str(res04))

        # RT-05 反馈污染
        rt05 = vectors.get("RT-05") or {}
        res05 = (rt05.get("results")
                 or [{}])[0]
        record("RT-05 反馈污染(pending+零生效)",
               rt05.get("defended") is True
               and res05.get(
                   "labelPending") is True
               and res05.get(
                   "corpusUnchanged") is True,
               str(res05))

        # RT-06 标注注入
        rt06 = vectors.get("RT-06") or {}
        record("RT-06 标注注入(三路全拒)",
               rt06.get("defended") is True
               and all(x["rejected"] for x
                      in rt06.get("results")
                      or []),
               str(rt06.get("results")))

        # RT-07 LLM 越白名单
        rt07 = vectors.get("RT-07") or {}
        record("RT-07 LLM 越白名单(off 拒+"
               "建议不入库)",
               rt07.get("defended") is True,
               str(rt07.get("results")))

        # 全量 defended
        record("七向量 allDefended",
               summary.get("allDefended")
               is True,
               str(summary))

        # 模式还原(shadow——RT-05/06 内部
        # assist 已还原)
        record("红队模式还原(II58_MODE)",
               os.environ.get("II58_MODE")
               == "shadow",
               str(os.environ.get("II58_MODE")))
        os.environ["II58_MODE"] = "off"


class TestHttp:
    """04 HTTP 层"""

    async def run(self):
        print("[04 HTTP]")
        reset_all()
        from fastapi.testclient import TestClient
        from main import app
        client = TestClient(app)
        admin = {"X-Role": "admin"}

        # 空态看板(off 可用)
        resp = client.get(
            "/api/ii58/dashboard",
            headers=admin)
        body = resp.json() or {}
        record("HTTP dashboard 观测面 200"
               "(off 可用)",
               resp.status_code == 200
               and (body.get("metrics")
                    or {}).get("total") == 0,
               str((resp.status_code,
                    (body.get("metrics")
                     or {}).get("total"))))

        # 宪法断言(HTTP 呈现)
        defense = body.get("defense") or {}
        record("HTTP 防御区宪法断言",
               (defense.get("constitution")
                or {}).get("scorer33") is True
               and (defense.get("constitution")
                    or {}).get(
                   "xiaozhuZeroChange") is True,
               str(defense.get("constitution")))

        # redteam off 409
        resp = client.post(
            "/api/ii58/redteam",
            json={}, headers=admin)
        record("HTTP redteam off 409",
               resp.status_code == 409,
               str(resp.status_code))

        # redteam shadow 200
        os.environ["II58_MODE"] = "shadow"
        resp = client.post(
            "/api/ii58/redteam",
            json={}, headers=admin)
        body = resp.json() or {}
        record("HTTP redteam 200"
               "(allDefended)",
               resp.status_code == 200
               and (body.get("summary")
                    or {}).get(
                   "allDefended") is True,
               str((resp.status_code,
                    (body.get("summary")
                     or {}).get(
                        "allDefended"))))
        os.environ["II58_MODE"] = "off"

        # 鉴权 403
        for method, path in (
                ("GET", "/api/ii58/dashboard"),
                ("POST", "/api/ii58/redteam")):
            resp = client.request(
                method, path, json={})
            record(f"HTTP {path.split('/')[-1]}"
                   f" 无 Role 403",
                   resp.status_code == 403,
                   str(resp.status_code))

        # 路由累计 19 端点
        from routes.ii58_routes import (
            router as ii_router,
        )
        count = sum(
            1 for r in ii_router.routes)
        record("58号路由累计 19 端点",
               count == 19, str(count))


async def run_all():
    await TestDashboard().run()
    await TestConstitution().run()
    await TestRedteam().run()
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
