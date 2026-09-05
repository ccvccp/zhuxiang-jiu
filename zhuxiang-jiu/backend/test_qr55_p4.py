"""55号·二维码AI智能管理模块 P4 专项测试
(治理+安全+LLM 归因)

运行方式:
    python test_qr55_p4.py

覆盖(55号计划 §六 P4):
    - 治理联动: 46号三检测器只读+冻结守卫+
      动作建议+46号零改动
    - 拨测验证: 白名单 route 可达性+失败重试
      +probe 事件留痕+拨测失败不计预算
    - 篡改受害者信值补偿: 45号 L2 deposit
      +幂等 1:1
    - 儿童简化模式: apply 类二次确认+confirmed
      回传生成+childMode 留痕; 查询类直通
    - LLM 归因: mock 确定性模板+数字来自
      数据层+无事件 ValueError
    - HTTP 层: 4 端点+鉴权+18 端点计数
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


class TestGovernance:
    """01 治理联动(46号三检测器+冻结守卫)"""

    async def run(self):
        print("[01 治理联动]")
        reset_all()
        from services.qr55_governance_service import (
            Qr55GovernanceService,
        )
        svc = Qr55GovernanceService()

        # 健康视图(46号未初始化——冷态容错)
        r = await svc.governance_health()
        gov = r.get("governance") or {}
        record("治理健康视图(冷态容错)",
               r.get("success") is True
               and "healthScore" in gov
               and "signals" in gov,
               str(gov.get("error")))

        # 46号 sync 初始化后条目呈现
        from services.ai_governance_service import (
            AiGovernanceService,
        )
        await AiGovernanceService().sync_registry()
        r2 = await svc.governance_health()
        gov2 = r2.get("governance") or {}
        record("46号三检测器条目呈现",
               gov2.get("healthScore") is not None
               and gov2.get("healthLevel") in (
                   "healthy", "watch", "warning",
                   "risk"),
               str((gov2.get("healthScore"),
                        gov2.get("healthLevel"))))
        record("冻结守卫(未冻结开放)",
               (r2.get("freezeGuard") or {})
               .get("frozen") is False,
               str(r2.get("freezeGuard")))

        # 域内事实
        domain = r2.get("domain") or {}
        record("域内治理事实(事件/反馈)",
               "modelEvents" in domain
               and "labeledFeedback" in domain,
               str(domain))

        # freeze_guard 观测
        fg = await svc.freeze_guard()
        record("freeze_guard 观测(未冻结)",
               fg.get("frozen") is False
               and "effect" in fg,
               str(fg))

        # 46号冻结 → 守卫翻转+原因追溯
        change = await AiGovernanceService(
        ).submit_change(
            "qr_orchestration", "freeze", {},
            "p4-test 冻结验证")
        await AiGovernanceService().review_change(
            change["changeId"], True, "p4")
        fg2 = await svc.freeze_guard()
        record("冻结守卫翻转(frozen)",
               fg2.get("frozen") is True
               and "学习" in str(fg2.get("effect")),
               str(fg2))

        # 解冻(恢复现场)
        change2 = await AiGovernanceService(
        ).submit_change(
            "qr_orchestration", "unfreeze", {},
            "p4-test 解冻")
        await AiGovernanceService().review_change(
            change2["changeId"], True, "p4")

        # 46号零改动红线(只读消费无写入)
        # sync_registry 后台账含 30 档案
        reg = await AiGovernanceService(
        ).list_registry()
        record("46号档案数 ≥30(零改动红线)",
               (reg.get("total") or 0) >= 30,
               str(reg.get("total")))


class TestProbe:
    """02 拨测验证+信值补偿"""

    async def run(self):
        print("[02 拨测+补偿]")
        reset_all()
        from services.qr55_probe_service import (
            Qr55ProbeService,
        )
        from repositories.qr55_repository import (
            Qr55Repository,
        )
        svc = Qr55ProbeService()
        repo = Qr55Repository()

        # 全量拨测(12 项)
        r = await svc.run_probe()
        record("拨测 12 项全量",
               r.get("probed") == 12,
               str(r.get("probed")))
        record("白名单 route 全部可达",
               r.get("reachable") == 12
               and r.get("failed") == 0,
               str((r.get("reachable"),
                    r.get("failed"))))
        record("拨测失败不计预算(铁律声明)",
               "不计预算" in str(
                   r.get("budgetNote")),
               str(r.get("budgetNote")))

        # probe 事件留痕
        events = await repo.list_events(limit=100)
        probes = [e for e in events
                  if e.get("eventType") == "probe"]
        record("probe 事件留痕(12 条)",
               len(probes) == 12
               and all((e.get("detail") or {})
                      .get("reachable") is True
                      for e in probes),
               str(len(probes)))

        # 模型事件留痕(probe_run)
        model_events = await repo.list_model_events(
            limit=50)
        types = {e.get("eventType")
                 for e in model_events}
        record("probe_run 模型事件留痕",
               "probe_run" in types, str(types))

        # 信值补偿: 种 tamper 事件(会员 8901)
        await seed_member_grade(8901)
        event_id = await repo.next_event_id()
        await repo.add_event({
            "eventId": event_id, "codeId": 0,
            "memberId": 8901,
            "eventType": "tamper",
            "detail": {"reason": "p4-test"},
            "createdAt": "2026-09-06T00:00:00+08:00",
        })

        # 无登录态扫码者跳过
        event_id2 = await repo.next_event_id()
        await repo.add_event({
            "eventId": event_id2, "codeId": 0,
            "memberId": 0,
            "eventType": "tamper",
            "detail": {"reason": "anonymous"},
            "createdAt": "2026-09-06T00:00:00+08:00",
        })

        comp = await svc.compensate_tamper_victims()
        record("信值补偿(45号 L2 验真)",
               comp.get("compensated") == 1
               and comp.get("skipped") == 1,
               str((comp.get("compensated"),
                    comp.get("skipped"))))

        # 补偿留痕事件
        events = await repo.list_events(limit=100)
        compensations = [e for e in events
                         if e.get("eventType")
                         == "compensate"]
        record("compensate 事件留痕(1:1)",
               len(compensations) == 1
               and int((compensations[0].get("detail")
                       or {}).get("tamperEventId"))
               == event_id,
               str(len(compensations)))

        # 幂等: 二次补偿零新增
        comp2 = await svc.compensate_tamper_victims()
        record("补偿幂等(1:1 不重复申报)",
               comp2.get("compensated") == 0
               and comp2.get("skipped") == 2,
               str((comp2.get("compensated"),
                    comp2.get("skipped"))))

        # 45号因子落账(L2 platform_conduct)
        from repositories.trust_value_repository \
            import TrustValue45Repository
        prof = await TrustValue45Repository(
        ).get_profile(8901)
        pc = float((prof.get("factors") or {}).get(
            "platform_conduct") or 0)
        record("45号因子落账(platform_conduct)",
               pc > 0, str(pc))


class TestChildMode:
    """03 儿童简化模式(二次确认)"""

    async def run(self):
        print("[03 儿童模式]")
        reset_all()
        from services.qr55_generate_service import (
            Qr55GenerateService,
        )
        gen = Qr55GenerateService()
        os.environ["QR55_MODE"] = "on"
        await seed_member_grade(8902)

        # apply 类(高危): 二次确认拦截
        r = await gen.orchestrate(
            8902, "我要给老人办优待证",
            child_mode=True)
        record("apply 类儿童二次确认",
               r.get("status")
               == "child_confirm_required"
               and (r.get("childSafety") or {})
               .get("requireGuardian") is True,
               str(r.get("status")))

        # confirmed 回传 → 生成
        r2 = await gen.orchestrate(
            8902, "我要给老人办优待证",
            child_mode=True, confirmed=True)
        record("confirmed 回传生成",
               r2.get("status") == "generated",
               str(r2.get("status")))
        child_mode_view = (r2.get("personalization")
                           or {}).get("childMode") or {}
        record("childMode 千面标记(简化文案)",
               child_mode_view.get("simplifiedCopy")
               is True
               and child_mode_view.get(
                   "guardianConfirmed") is True,
               str(child_mode_view))

        # 码实例 childMode 留痕
        from repositories.qr55_repository import (
            Qr55Repository,
        )
        code = await Qr55Repository().get_code(
            r2.get("codeId"))
        record("码实例 childMode 留痕",
               code.get("childMode") is True,
               str(code.get("childMode")))

        # query 类(低危): 儿童模式直通
        r3 = await gen.orchestrate(
            8902, "查政策解读", child_mode=True)
        record("query 类儿童直通",
               r3.get("status") == "generated",
               str(r3.get("status")))

        # 非儿童模式: apply 类不受影响(P1 语义)
        r4 = await gen.orchestrate(
            8902, "我要给老人办优待证")
        record("非儿童模式 apply 直通(P1 语义)",
               r4.get("status") in (
                   "generated", "confirm_required"),
               str(r4.get("status")))
        os.environ["QR55_MODE"] = "off"


class TestAttribution:
    """04 LLM 归因报告"""

    async def run(self):
        print("[04 LLM 归因]")
        reset_all()
        from services.qr55_attribution_service import (
            Qr55AttributionService,
        )
        svc = Qr55AttributionService()

        # 无事件 → ValueError
        try:
            await svc.attribution()
            ok, err = False, "未拒绝"
        except ValueError as e:
            ok, err = True, str(e)[:40]
        record("无归因事件 ValueError", ok, err)

        # 种学习事件
        from repositories.qr55_repository import (
            Qr55Repository,
        )
        repo = Qr55Repository()
        event_id = await repo.next_model_event_id()
        await repo.save_model_event({
            "modelEventId": event_id,
            "eventType": "learning",
            "detail": {
                "scorerId": "qr_orchestration",
                "learnedFrom": 12,
                "parentVersion": "v1",
                "newVersion": "v2",
                "promoted": False,
                "weightDelta": {
                    "intent_confidence": 0.02,
                    "member_trust": -0.01,
                },
            },
            "createdAt": "2026-09-06T00:00:00+08:00",
        })

        # mock 归因
        r = await svc.attribution()
        record("mock 归因(LLM off)",
               r.get("mode") == "mock"
               and bool(r.get("attribution")),
               str(r.get("mode")))
        record("归因数字来自数据层",
               "12 条" in str(r.get("attribution"))
               and "v1" in str(r.get("attribution")),
               str(r.get("attribution"))[:80])
        record("护栏口径呈现",
               "0.5,2.0" in str(
                   r.get("attribution")),
               "")

        # facts 完整(版本对/权重变化)
        facts = r.get("facts") or {}
        record("facts 数据层事实(版本对+delta)",
               facts.get("parentVersion") == "v1"
               and facts.get("newVersion") == "v2"
               and facts.get("learnedFrom") == 12
               and bool(facts.get(
                   "topWeightChanges")),
               str(facts.get("parentVersion")))


class TestHttp:
    """05 HTTP 层"""

    async def run(self):
        print("[05 HTTP]")
        reset_all()
        from fastapi.testclient import TestClient
        from main import app
        client = TestClient(app)
        admin = {"X-Role": "admin"}

        # governance/health
        resp = client.get(
            "/api/qr55/governance/health",
            headers=admin)
        body = resp.json() or {}
        record("HTTP governance/health",
               resp.status_code == 200
               and "governance" in body
               and "freezeGuard" in body,
               str(resp.status_code))

        # probe
        resp = client.post("/api/qr55/probe",
                           headers=admin)
        body = resp.json() or {}
        record("HTTP probe(拨测)",
               resp.status_code == 200
               and body.get("probed") == 12,
               str((resp.status_code,
                    body.get("probed"))))

        # probe/compensate
        resp = client.post(
            "/api/qr55/probe/compensate",
            headers=admin)
        body = resp.json() or {}
        record("HTTP probe/compensate(幂等空转)",
               resp.status_code == 200
               and body.get("tamperEvents") == 0,
               str(body.get("tamperEvents")))

        # attribution: 无事件 409
        resp = client.get("/api/qr55/attribution",
                          headers=admin)
        record("HTTP attribution 无事件 409",
               resp.status_code == 409,
               str(resp.status_code))

        # attribution: 种事件后 200
        from repositories.qr55_repository import (
            Qr55Repository,
        )
        repo = Qr55Repository()
        event_id = await repo.next_model_event_id()
        await repo.save_model_event({
            "modelEventId": event_id,
            "eventType": "learning",
            "detail": {"scorerId": "qr_orchestration",
                       "learnedFrom": 5,
                       "parentVersion": "v1",
                       "newVersion": "v2",
                       "promoted": False,
                       "weightDelta": {}},
            "createdAt": "2026-09-06T00:00:00+08:00",
        })
        resp = client.get("/api/qr55/attribution",
                         headers=admin)
        record("HTTP attribution 200(mock)",
               resp.status_code == 200
               and (resp.json() or {})
               .get("mode") == "mock",
               str(resp.status_code))

        # 儿童模式 HTTP
        os.environ["QR55_MODE"] = "on"
        resp = client.post(
            "/api/qr55/generate",
            json={"memberId": 8903,
                  "text": "我要给老人办优待证",
                  "childMode": True},
            headers=admin)
        record("HTTP generate 儿童二次确认",
               (resp.json() or {}).get("status")
               == "child_confirm_required",
               str((resp.json() or {}).get("status")))
        os.environ["QR55_MODE"] = "off"

        # 鉴权
        for method, path in (
                ("GET",
                 "/api/qr55/governance/health"),
                ("POST", "/api/qr55/probe"),
                ("POST",
                 "/api/qr55/probe/compensate"),
                ("GET", "/api/qr55/attribution")):
            resp = client.request(method, path)
            record(f"HTTP {path.split('/')[-1]}"
                   f" 无 Role 403",
                   resp.status_code == 403,
                   str(resp.status_code))

        # 路由累计 18 端点
        from routes.qr55_routes import (
            router as qr_router,
        )
        count = sum(1 for r in qr_router.routes)
        # P5 新增 2 端点(dashboard/redteam)
        # → 18→20(基线语义: ≥18——P4 交付面不因
        # P5 演进破坏)
        record("55号路由累计 ≥18 端点(P5 扩至 20)",
               count >= 18, str(count))


async def run_all():
    await TestGovernance().run()
    await TestProbe().run()
    await TestChildMode().run()
    await TestAttribution().run()
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
