"""52号·小竹语音可用性评估引擎 P4 专项测试
(信任体验代理与评估报告任务)

运行方式:
    python test_us52_p4.py

覆盖(52号计划 §七 P4):
    - 透明度四指标: 隐私播报率/归因覆盖/
      错误解释合规(黑名单)/数据用途说明
      (空态=1.0 满分口径+违规场景=0)
    - 信任体验四指标(行为代理): 信任增益
      四源加权(采纳/翻转/授权/礼貌)+
      控制感(偏好调整)+伦理负面率+
      反馈健康度(空态默认口径)
    - 评估报告: 五维 20 项聚合+决策+
      信值合规影响评估章节(风险触发)+
      留痕递增+proxy 免责声明
    - off 铁律 + 端点 + 零影响(宪法断言)
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
    import services.xiaozhu_executor as ex_mod
    ex_mod._EXECUTOR_SINGLETON = None


async def seed_turn(intent: str, reply: str,
                    seq: int, session: int = 990001):
    """种子: 48号会话轮次(透明度管道数据源)"""
    from repositories.xiaozhu_repository import (
        Xiaozhu48Repository,
    )
    return await Xiaozhu48Repository().save_turn({
        "sessionId": session, "seq": seq,
        "memberId": 5300, "intent": intent,
        "reply": reply, "ts": "2026-09-05T10:00:00",
    })


async def seed_trust_sources():
    """种子: 50号 corpus+裁决+事件+48号隐私账户
    (信任体验管道数据源)

    四源配比: 采纳 4/5 + 翻转 2/4 +
    授权 10/10 + 礼貌 20/20 →
    trust_gain = 0.25*(0.8+0.5+1.0+1.0) = 0.825
    """
    from core.helpers import ts as _ts
    from repositories.voice50_repository import (
        Voice50Repository,
    )
    v50 = Voice50Repository()

    # corpus: 5 条(4 adopted; 1 条场景含负面词)
    for i, status in enumerate(
            ["adopted"] * 4 + ["pending"], 1):
        await v50.save_corpus({
            "corpusId": await v50.next_corpus_id(),
            "memberId": 5300, "sessionId": 990001,
            "scenario": "语音交互流畅"
            if i <= 4 else "对回复很不满",
            "utterance": f"样本{i}", "status": status,
            "ts": _ts(),
        })
    # 裁决: 4 条(2 overturned)
    for i, status in enumerate(
            ["overturned"] * 2 + ["upheld"] * 2, 1):
        await v50.save_adjudication({
            "adjId": await v50.next_adjudication_id(),
            "memberId": 5300, "pattern": "manual",
            "status": status, "ts": _ts(),
        })
    # 事件: 授权 10+礼貌 20+反馈 4(3 正 1 零)
    behaviors = (
        ["voice_privacy_grant"] * 10
        + ["voice_polite"] * 20
        + ["voice_feedback"] * 3)
    for b in behaviors:
        await v50.save_event({
            "evId": await v50.next_event_id(),
            "memberId": 5300, "dayKey": "2026-09-05",
            "behavior": b, "baseScore": 0.1,
            "finalScore": 0.1, "status": "settled",
            "ts": _ts(),
        })
    await v50.save_event({
        "evId": await v50.next_event_id(),
        "memberId": 5300, "dayKey": "2026-09-05",
        "behavior": "voice_feedback", "baseScore": 0.0,
        "finalScore": 0.0, "status": "settled",
        "ts": _ts(),
    })
    # 隐私账户: 2 个(1 个已调整偏好)
    from repositories.xiaozhu_repository import (
        Xiaozhu48Repository,
    )
    xrepo = Xiaozhu48Repository()
    await xrepo.save_privacy_budget({
        "memberId": 5300, "dayKey": "2026-09-05",
        "preference": 0.5, "budget": 1.0,
        "ts": _ts(),
    })
    await xrepo.save_privacy_budget({
        "memberId": 5301, "dayKey": "2026-09-05",
        "preference": 1.0, "budget": 1.0,
        "ts": _ts(),
    })


class TestTransparencyMetrics:
    """01 透明度四指标"""

    async def run(self):
        print("[01 透明度四指标]")
        reset_all()
        from services.us52_service import (
            Us52MetricsService,
        )
        svc = Us52MetricsService()

        # off 态拒绝
        try:
            await svc.compute_transparency_metrics()
            ok, err = False, "未拒绝"
        except ValueError as e:
            ok, err = "off" in str(e), str(e)[:30]
        record("off 态计算拒绝", ok, err)

        os.environ["US52_MODE"] = "on"

        # 空态: 无轮次=满分(未观测到违规)
        r0 = await svc.compute_transparency_metrics()
        m0 = r0.get("metrics") or {}
        record("四指标键齐备",
               set(m0) == {
                   "privacy_notice_rate",
                   "attribution_rate", "error_clarity",
                   "data_purpose_rate"},
               str(list(m0)))
        record("空态四指标=1.0(满分口径)",
               all(v == 1.0 for v in m0.values()),
               str(m0))

        # 合规场景: 四类话术全覆盖
        reset_all()
        await seed_turn("privacy.budget",
                        "当前隐私预算 3 次, 数据使用"
                        "已获授权", seq=1)
        await seed_turn("trust.score",
                        "您的信值分 85(源于正向存证)",
                        seq=2)
        await seed_turn("general",
                        "抱歉, 未能理解您的指令",
                        seq=3)
        await seed_turn("voice.score",
                        "语音积分用于身份验真", seq=4)
        r1 = await svc.compute_transparency_metrics()
        m1 = r1.get("metrics") or {}
        record("合规场景四指标=1.0",
               all(v == 1.0 for v in m1.values()),
               str(m1))

        # 违规场景: 隐私无播报+归因缺失+
        # 错误含技术术语+用途未说明
        # (privacy.budget 意图归隐私域但不入
        # 归因/用途统计——口径隔离)
        reset_all()
        await seed_turn("privacy.budget", "查询完成",
                        seq=1)
        await seed_turn("trust.balance", "", seq=2)
        await seed_turn("general",
                        "抱歉, exception: traceback "
                        "内部错误", seq=3)
        await seed_turn("voice.score", "", seq=4)
        r2 = await svc.compute_transparency_metrics()
        m2 = r2.get("metrics") or {}
        record("隐私无播报 → 播报率=0",
               m2.get("privacy_notice_rate") == 0.0,
               str(m2.get("privacy_notice_rate")))
        record("归因缺失 → 覆盖率=0",
               m2.get("attribution_rate") == 0.0,
               str(m2.get("attribution_rate")))
        record("技术术语泄露 → 错误合规=0",
               m2.get("error_clarity") == 0.0,
               str(m2.get("error_clarity")))
        record("用途未说明 → 认知素材=0",
               m2.get("data_purpose_rate") == 0.0,
               str(m2.get("data_purpose_rate")))

        # 透明度未达 → mandatory 决策
        from services.us52_registry import decide
        gate = decide(m2)
        record("透明度未达 → mandatory",
               gate["decision"] == "mandatory"
               and "transparency" in
               gate.get("failedByDimension"),
               str(gate.get("decision")))

        # detail 字段齐备
        d2 = r2.get("detail") or {}
        record("detail 统计字段齐备",
               all(k in d2 for k in (
                   "turnTotal", "privacyTurns",
                   "valueTurns", "errorTurns",
                   "purposeTurns")),
               str(list(d2)))
        os.environ["US52_MODE"] = "off"


class TestTrustMetrics:
    """02 信任体验四指标(行为代理)"""

    async def run(self):
        print("[02 信任体验四指标]")
        reset_all()
        from services.us52_service import (
            Us52MetricsService,
        )
        svc = Us52MetricsService()

        # off 态拒绝
        try:
            await svc.compute_trust_metrics()
            ok, err = False, "未拒绝"
        except ValueError as e:
            ok, err = "off" in str(e), str(e)[:30]
        record("off 态计算拒绝", ok, err)

        os.environ["US52_MODE"] = "on"

        # 空态默认口径
        r0 = await svc.compute_trust_metrics()
        m0 = r0.get("metrics") or {}
        record("四指标键齐备",
               set(m0) == {
                   "trust_gain_index",
                   "control_sense_rate",
                   "ethics_negative_rate",
                   "feedback_health_ratio"},
               str(list(m0)))
        record("空态默认口径(增益0/控制0.6/"
               "负面0/健康0.7)",
               m0.get("trust_gain_index") == 0.0
               and m0.get("control_sense_rate") == 0.6
               and m0.get("ethics_negative_rate") == 0.0
               and m0.get(
                   "feedback_health_ratio") == 0.7,
               str(m0))

        # 空态信任增益未达 → priority 决策
        from services.us52_registry import decide
        gate = decide(m0)
        record("信任未达 → priority 决策",
               gate["decision"] == "priority"
               and "trust" in
               gate.get("failedByDimension"),
               str(gate.get("decision")))

        # 种子场景: 四源加权 0.25*(0.8+0.5+1+1)
        reset_all()
        await seed_trust_sources()
        r1 = await svc.compute_trust_metrics()
        m1 = r1.get("metrics") or {}
        record("四源加权增益=0.825",
               abs((m1.get("trust_gain_index") or 0)
                   - 0.825) < 0.001,
               str(m1.get("trust_gain_index")))
        record("控制感=0.5(2账户1调整)",
               m1.get("control_sense_rate") == 0.5,
               str(m1.get("control_sense_rate")))
        record("伦理负面率=0.2(1/5)",
               m1.get("ethics_negative_rate") == 0.2,
               str(m1.get("ethics_negative_rate")))
        record("反馈健康度=0.75(3/4)",
               m1.get("feedback_health_ratio") == 0.75,
               str(m1.get("feedback_health_ratio")))

        # 四源明细
        src = (r1.get("detail") or {}).get(
            "trustSources") or {}
        record("四源明细齐备",
               set(src) == {
                   "adoptRatio", "overturnRatio",
                   "grantRatio", "politeRatio"}
               and src.get("adoptRatio") == 0.8
               and src.get("overturnRatio") == 0.5,
               str(src))

        # proxy 免责声明
        note = (r1.get("detail") or {}).get(
            "proxyNote") or ""
        record("行为代理显式声明",
               "行为代理" in note and "外部待办" in note,
               note[:40])
        os.environ["US52_MODE"] = "off"


class TestReport:
    """03 评估报告(五维聚合+信值合规影响评估)"""

    async def run(self):
        print("[03 评估报告]")
        reset_all()
        from services.us52_service import (
            Us52MetricsService,
        )
        svc = Us52MetricsService()

        # off 态拒绝
        try:
            await svc.generate_report()
            ok, err = False, "未拒绝"
        except ValueError as e:
            ok, err = "off" in str(e), str(e)[:30]
        record("off 态报告拒绝", ok, err)

        os.environ["US52_MODE"] = "on"

        # 种子: corpus 负面提及过半(触发合规风险
        # 章节——ethics_negative_rate 0.5>0.05;
        # 红队真跑会写轮次但零写 corpus——
        # 确定性口径)
        from repositories.voice50_repository import (
            Voice50Repository,
        )
        v50 = Voice50Repository()
        await v50.save_corpus({
            "corpusId": await v50.next_corpus_id(),
            "memberId": 5300, "sessionId": 990001,
            "scenario": "对回复很不满",
            "utterance": "负面样本", "status": "pending",
            "ts": "2026-09-05T10:00:00",
        })
        await v50.save_corpus({
            "corpusId": await v50.next_corpus_id(),
            "memberId": 5300, "sessionId": 990001,
            "scenario": "语音交互流畅",
            "utterance": "正面样本", "status": "pending",
            "ts": "2026-09-05T10:00:00",
        })

        r1 = await svc.generate_report()
        rep = r1.get("report") or {}
        record("报告结构齐备",
               all(k in rep for k in (
                   "reportId", "mode", "metricCount",
                   "metrics", "decision", "rationale",
                   "vetoFailed", "failedByDimension",
                   "complianceImpact",
                   "proxyDisclaimer", "createdAt")),
               str(list(rep))[:60])
        record("五维 20 项全量聚合",
               rep.get("metricCount") == 20
               and len(rep.get("metrics") or {}) == 20,
               str(rep.get("metricCount")))
        record("决策字段合法(四态+regression)",
               rep.get("decision") in {
                   "pass", "mandatory", "priority",
                   "veto", "regression"},
               str(rep.get("decision")))

        # 信值合规影响评估章节
        impact = rep.get("complianceImpact") or {}
        risks = impact.get("potentialRisks") or []
        mitig = impact.get("mitigations") or []
        record("信值合规影响评估章节齐备",
               bool(risks) and bool(mitig),
               str(impact)[:50])
        record("伦理风险触发(负面率>0.05)",
               any("伦理负面" in r for r in risks),
               str(risks)[:60])

        # proxy 免责声明
        record("proxy 免责声明标注",
               "行为代理" in str(
                   rep.get("proxyDisclaimer")),
               str(rep.get("proxyDisclaimer"))[:40])

        # 留痕递增
        rid1 = rep.get("reportId")
        r2 = await svc.generate_report()
        rid2 = (r2.get("report") or {}).get("reportId")
        record("报告留痕递增",
               isinstance(rid1, int)
               and isinstance(rid2, int)
               and rid2 > rid1,
               f"{rid1} → {rid2}")
        os.environ["US52_MODE"] = "off"


class TestEndpoints:
    """04 端点+鉴权+零影响"""

    async def run(self):
        print("[04 端点+鉴权+零影响]")
        reset_all()
        from fastapi.testclient import TestClient
        from main import app
        client = TestClient(app)
        admin = {"X-Role": "admin"}

        # off 态三端点 409
        for path in ("/api/us52/metrics/transparency",
                     "/api/us52/metrics/trust",
                     "/api/us52/reports/generate"):
            resp = client.post(path, headers=admin)
            record(f"off 态 {path.rsplit('/', 1)[-1]}"
                   f" 409",
                   resp.status_code == 409,
                   str(resp.status_code))

        os.environ["US52_MODE"] = "on"

        resp = client.post(
            "/api/us52/metrics/transparency",
            headers=admin)
        record("HTTP transparency 200(四指标)",
               resp.status_code == 200
               and len(resp.json().get("metrics")
                       or {}) == 4,
               str(resp.status_code))

        resp = client.post(
            "/api/us52/metrics/trust",
            headers=admin)
        record("HTTP trust 200(四指标)",
               resp.status_code == 200
               and len(resp.json().get("metrics")
                       or {}) == 4,
               str(resp.status_code))

        resp = client.post(
            "/api/us52/reports/generate",
            headers=admin)
        body = resp.json() or {}
        record("HTTP reports/generate 200"
               "(含合规章节)",
               resp.status_code == 200
               and "complianceImpact" in (
                   body.get("report") or {}),
               str(resp.status_code))

        resp = client.get("/api/us52/reports",
                          headers=admin)
        body = resp.json() or {}
        record("GET /reports 列表含留痕",
               resp.status_code == 200
               and body.get("total", 0) >= 1,
               str(body.get("total")))

        # 鉴权
        resp = client.post(
            "/api/us52/metrics/trust")
        record("trust 无 Role 403",
               resp.status_code == 403,
               str(resp.status_code))
        resp = client.get("/api/us52/reports")
        record("reports 无 Role 403",
               resp.status_code == 403,
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
    await TestTransparencyMetrics().run()
    await TestTrustMetrics().run()
    await TestReport().run()
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
