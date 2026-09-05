"""55号·二维码AI智能管理模块 P5 专项测试
(看板+红队+三件套收官)

运行方式:
    python test_qr55_p5.py

覆盖(55号计划 §六 P5):
    - 四区看板: 码量/服务分布/回流漏斗/漂移+防御
    - 红队六向量: 载荷伪造/重放/篡改/参数越权/
      白名单逃逸/预算绕过+投毒洪流
    - 宪法断言: 44/46/48/51号零改动
    - HTTP 层: dashboard/redteam+鉴权+20 端点
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


async def seed_member_grade(member_id: int,
                            grade: str = "healthy"):
    from repositories.trust_value_repository \
        import TrustValue45Repository
    repo = TrustValue45Repository()
    rec = await repo.get_profile(member_id) or {}
    rec.update({
        "trustId": member_id, "grade": grade,
        "score": 80, "factors": {}, "role": "person",
        "l1Severity": {},
        "idDigest": f"seed-digest-{member_id}",
    })
    await repo.save_profile(rec)


async def seed_full_chain(member_id: int):
    """全链种子: 生成→扫码→完成 + collect 回流"""
    from services.qr55_generate_service import (
        Qr55GenerateService,
    )
    from services.qr55_scan_service import (
        Qr55ScanService,
    )
    from services.qr55_service import Qr55Service
    from services.qr55_feedback_service import (
        Qr55FeedbackService,
    )
    g = await Qr55GenerateService().orchestrate(
        member_id, "查政策解读")
    await Qr55ScanService().scan(
        g["code"], member_id=member_id)
    await Qr55Service().record_completion(g["codeId"])
    os.environ["QR55_MODE"] = "off"
    await Qr55FeedbackService().collect_feedback()
    os.environ["QR55_MODE"] = "on"
    return g


class TestDashboard:
    """01 四区看板"""

    async def run(self):
        print("[01 四区看板]")
        reset_all()
        await seed_member_grade(8971)
        os.environ["QR55_MODE"] = "on"
        await seed_full_chain(8971)
        # 补一个过期码(状态分布)
        from services.qr55_generate_service import (
            Qr55GenerateService,
        )
        from repositories.qr55_repository import (
            Qr55Repository,
        )
        import time
        g2 = await Qr55GenerateService().orchestrate(
            8971, "查信值余额")
        rec = await Qr55Repository().get_code(
            g2["codeId"])
        rec["status"] = "expired"
        await Qr55Repository().update_code(rec)
        # 多信号源种子(集中度断言——防单源 100% 告警):
        # 篡改事件(tamper_detected 源)
        g3 = await Qr55GenerateService().orchestrate(
            8971, "查政策解读")
        from services.qr55_scan_service import (
            Qr55ScanService,
        )
        await Qr55ScanService().scan(
            g3["code"][:-2] + "xx", member_id=8971)
        os.environ["QR55_MODE"] = "off"
        from services.qr55_feedback_service import (
            Qr55FeedbackService,
        )
        await Qr55FeedbackService().collect_feedback()
        os.environ["QR55_MODE"] = "on"

        from services.qr55_dashboard_service import (
            Qr55DashboardService,
        )
        d = await Qr55DashboardService().build()
        zones = d.get("zones") or {}

        # 四区齐备
        record("四区齐备",
               set(zones.keys()) == {
                   "volume", "services", "funnel",
                   "defense"},
               str(sorted(zones.keys())))

        # ① 码量区(3 码: redeemed+expired+active)
        volume = zones.get("volume") or {}
        record("码量区(总量+状态分布)",
               volume.get("totalCodes") == 3
               and (volume.get("byStatus") or {}
                    ).get("redeemed") == 1
               and (volume.get("byStatus") or {}
                    ).get("expired") == 1
               and (volume.get("byStatus") or {}
                    ).get("active") == 1,
               str(volume.get("byStatus")))
        record("码量区(事件计数)",
               "generate" in (volume.get(
                   "eventCounts") or {}),
               str(volume.get("eventCounts")))

        # ② 服务分布区(policy_search ×2: 全链码+
        # tamper 攻击码; trust_balance ×1: 过期码)
        services = zones.get("services") or {}
        by_svc = services.get("byService") or {}
        record("服务分布区(byService)",
               by_svc.get("policy_search", {}
                          ).get("generated") == 2
               and by_svc.get("trust_balance", {}
                              ).get("generated") == 1,
               str(list(by_svc.keys())))
        record("服务分布区(模板+敏感度)",
               "query" in (services.get("byTemplate")
                          or {})
               and "L0" in (services.get(
                   "bySensitivity") or {}),
               str(services.get("byTemplate")))

        # ③ 回流漏斗区(3 生成/1 扫码/1 完成——
        # 多源种子后 g3 为 tamper 攻击码不入扫码率)
        funnel = zones.get("funnel") or {}
        f = funnel.get("funnel") or {}
        record("回流漏斗(生成→扫码→完成)",
               f.get("generated") == 3
               and f.get("scanned") == 1
               and f.get("completed") == 1
               and f.get("completeRate") == 1.0,
               str(f))
        record("回流漏斗(信号+池+结算)",
               (funnel.get("signals") or {}).get(
                   "scan_completed") == 1
               and (funnel.get("signals") or {}).get(
                   "tamper_detected") == 1
               and (funnel.get("signals") or {}).get(
                   "expired_unscanned") == 1
               and funnel.get("poolSubmitted") == 3
               and funnel.get("trustSettled") == 1,
               str(funnel.get("signals")))

        # ④ 防御区
        defense = zones.get("defense") or {}
        record("防御区(护栏健康)",
               (defense.get("guardrail") or {}
                ).get("healthy") is True,
               str(defense.get("guardrail")))
        conc = defense.get(
            "sourceConcentration") or {}
        record("防御区(集中度呈现+无告警)",
               conc.get("topRatio") == 0.3333
               and conc.get("alert") is False,
               str(conc))
        record("防御区(版本链+漂移视图)",
               "versionChain" in defense
               and "metricsDrift" in defense,
               str(list(defense.keys())))


class TestRedteam:
    """02 红队六向量"""

    async def run(self):
        print("[02 红队六向量]")
        reset_all()
        await seed_member_grade(8972)
        os.environ["QR55_MODE"] = "on"

        # off 态拒绝
        from services.qr55_redteam_service import (
            Qr55RedteamService,
        )
        os.environ["QR55_MODE"] = "off"
        try:
            await Qr55RedteamService().run_all()
            ok, err = False, "未拒绝"
        except ValueError as e:
            ok, err = "on" in str(e), str(e)[:30]
        record("off 态红队拒绝", ok, err)

        os.environ["QR55_MODE"] = "on"
        r = await Qr55RedteamService().run_all(
            member_id=8972)
        vectors = r.get("vectors") or {}
        summary = r.get("summary") or {}

        record("六向量+投毒全量(7 项)",
               len(vectors) == 7,
               str(len(vectors)))

        # 各向量防御成立
        expectations = {
            "RT-01": "载荷伪造",
            "RT-02": "重放",
            "RT-03": "篡改",
            "RT-04": "参数越权",
            "RT-05": "白名单逃逸",
            "RT-06": "预算绕过",
            "RT-07": "投毒洪流",
        }
        for key, label in expectations.items():
            record(f"红队 {key} {label} 防御成立",
                   (vectors.get(key) or {})
                   .get("defended") is True,
                   str((vectors.get(key) or {})
                       .get("results")))

        record("红队总账(allDefended)",
               summary.get("allDefended") is True
               and summary.get("defended") == 7,
               str(summary))
        os.environ["QR55_MODE"] = "off"

        # RT-07 洪流集中度(看板防御区复核)
        from repositories.ai_learning_repository \
            import AiLearningRepository
        pending = await AiLearningRepository(
        ).list_feedback(
            "qr_orchestration", status="pending",
            limit=1000)
        flood = [f for f in pending
                 if f.get("source")
                 == "attacker_flood"]
        record("洪流注入(30 条 attacker_flood)",
               len(flood) == 30, str(len(flood)))


class TestConstitution:
    """03 宪法断言(零改动红线)"""

    async def run(self):
        print("[03 宪法断言]")
        # 44号: SCORER_REGISTRY 30 档案+batch14 含
        # qr_orchestration(第30档案在册)
        from services.ai_learning_service import (
            SCORER_REGISTRY,
        )
        record("44号零改动(30 档案+qr 在册)",
               len(SCORER_REGISTRY) == 30
               and "qr_orchestration"
               in SCORER_REGISTRY,
               str(len(SCORER_REGISTRY)))

        # 46号: 治理台账 30 档案(55号只读消费)
        from services.ai_governance_service import (
            AiGovernanceService,
        )
        await AiGovernanceService().sync_registry()
        reg = await AiGovernanceService().list_registry()
        record("46号零改动(台账 30 档案)",
               (reg.get("total") or 0) == 30,
               str(reg.get("total")))

        # 48号: 小竹意图轨零侵入(55号并行——
        # qr55 意图引擎独立, xiaozhu 未 import qr55)
        import services.qr55_intent_service as qi
        src = open(qi.__file__,
                   encoding="utf-8").read()
        record("48号零侵入(意图轨独立)",
               "xiaozhu" not in src.lower(),
               "")

        # 51号: 本体封闭注册表(9 实体零改动——
        # 55号自建 SERVICE_REGISTRY 12 项)
        from services.qr55_registry import (
            SERVICE_REGISTRY,
        )
        record("51号零改动(自建白名单 12 项)",
               len(SERVICE_REGISTRY) == 12,
               str(len(SERVICE_REGISTRY)))

        # 49号: 预算接口纯调用(check_and_spend
        # 未被覆写)
        from services.xiaozhu_privacy_service \
            import XiaozhuPrivacyService
        record("49号零改动(纯调用式)",
               hasattr(XiaozhuPrivacyService,
                       "check_and_spend"),
               "")


class TestHttp:
    """04 HTTP 层"""

    async def run(self):
        print("[04 HTTP]")
        reset_all()
        from fastapi.testclient import TestClient
        from main import app
        client = TestClient(app)
        admin = {"X-Role": "admin"}

        # dashboard(off 亦可用——观测面)
        resp = client.get("/api/qr55/dashboard",
                          headers=admin)
        body = resp.json() or {}
        record("HTTP dashboard(off 亦可用)",
               resp.status_code == 200
               and "zones" in body,
               str(resp.status_code))

        # redteam off 409
        resp = client.post("/api/qr55/redteam",
                           headers=admin)
        record("HTTP redteam off 409",
               resp.status_code == 409,
               str(resp.status_code))

        # redteam on 200
        await seed_member_grade(8973)
        os.environ["QR55_MODE"] = "on"
        resp = client.post(
            "/api/qr55/redteam?memberId=8973",
            headers=admin)
        body = resp.json() or {}
        record("HTTP redteam on 200(全防御)",
               resp.status_code == 200
               and (body.get("summary") or {})
               .get("allDefended") is True,
               str((resp.status_code,
                    body.get("summary"))))
        os.environ["QR55_MODE"] = "off"

        # 鉴权
        for method, path in (
                ("GET", "/api/qr55/dashboard"),
                ("POST", "/api/qr55/redteam")):
            resp = client.request(method, path)
            record(f"HTTP {path.split('/')[-1]}"
                   f" 无 Role 403",
                   resp.status_code == 403,
                   str(resp.status_code))

        # 路由累计 20 端点
        from routes.qr55_routes import (
            router as qr_router,
        )
        count = sum(1 for r in qr_router.routes)
        record("55号路由累计 20 端点(收官)",
               count == 20, str(count))


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
