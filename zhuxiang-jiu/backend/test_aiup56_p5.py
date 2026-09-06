"""56号·AI智能升级管理模块 P5 专项测试
(四区看板+红队六向量+宪法断言)

运行方式:
    python test_aiup56_p5.py

覆盖(56号计划 §九 P5):
    - 四区看板: 提案漏斗/资产产出/审计合规/
      回滚防御
    - 红队六向量: 提案投毒/预算耗尽/审批绕过/
      资产注入/信号伪造/回滚破坏
    - 宪法断言: 44号 31 档案+注册表封闭
    - HTTP 层: 2 端点+鉴权+20 端点计数
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

PASS = 0
FAIL = 0
RESULTS = []

ALL_CONFIRMATIONS = (
    "readAuditReport", "reviewedSandbox",
    "acknowledgedRollback", "acknowledgedBudget")


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


async def seed_full_chain_proposal(rolled_back: bool = False
                                   ) -> int:
    """种一个全链提案(evaluate→…→交付;
    rolled_back=True 再回滚)"""
    from core.helpers import ts
    from repositories.qr55_repository import (
        Qr55Repository,
    )
    repo55 = Qr55Repository()
    for snap in (
            {"satisfactionScore": 80.0,
             "clarifyEfficiency": 0.8,
             "penetrationRate": 0.7},
            {"satisfactionScore": 60.0,
             "clarifyEfficiency": 0.5,
             "penetrationRate": 0.4}):
        meid = await repo55.next_model_event_id()
        await repo55.save_model_event({
            "modelEventId": meid,
            "eventType": "metrics_snapshot",
            "detail": {"metrics": snap},
            "createdAt": ts(),
        })
    from services.aiup56_service import Aiup56Service
    from services.aiup56_plan_service import (
        Aiup56PlanService,
    )
    from services.aiup56_code_service import (
        Aiup56CodeService,
    )
    from services.aiup56_test_service import (
        Aiup56TestService,
    )
    from services.aiup56_audit_service import (
        Aiup56AuditService,
    )
    from services.aiup56_review_service import (
        Aiup56ReviewService,
    )
    from services.aiup56_deliver_service import (
        Aiup56DeliverService,
    )
    r = await Aiup56Service().evaluate_and_propose()
    pid = r["proposalId"]
    await Aiup56PlanService().plan(pid)
    await Aiup56CodeService().code(pid)
    await Aiup56TestService().test(pid)
    await Aiup56AuditService().audit(pid)
    await Aiup56ReviewService().review(
        pid, reviewer="admin", approved=True,
        confirmations=list(ALL_CONFIRMATIONS))
    await Aiup56DeliverService().deliver(pid)
    if rolled_back:
        await Aiup56DeliverService().rollback(
            pid, reason="p5-看板种子")
    return pid


class TestDashboard:
    """01 四区看板"""

    async def run(self):
        print("[01 四区看板]")
        reset_all()
        os.environ["AIUP56_MODE"] = "shadow"

        # 空态看板
        from services.aiup56_dashboard_service import (
            Aiup56DashboardService,
        )
        dash = Aiup56DashboardService()
        empty = await dash.build()
        zones = empty.get("zones") or {}
        record("空态看板(四区齐备)",
               empty.get("success") is True
               and set(zones.keys()) == {
                   "funnel", "assets",
                   "compliance", "defense"},
               str(sorted(zones.keys())))
        record("空态漏斗(total=0)",
               (zones.get("funnel") or {})
               .get("total") == 0,
               str((zones.get("funnel") or {})
                   .get("total")))

        # 全链种子(1 交付+1 回滚)+回流补标
        await seed_full_chain_proposal()
        await seed_full_chain_proposal(
            rolled_back=True)
        from services.aiup56_feedback_service import (
            Aiup56FeedbackService,
        )
        await Aiup56FeedbackService(
        ).collect_feedback()

        board = await dash.build()
        zones = board.get("zones") or {}

        # ① 漏斗区
        funnel = zones.get("funnel") or {}
        record("漏斗区(2 提案+九态分布)",
               funnel.get("total") == 2
               and len(funnel.get("byStatus")
                       or {}) >= 2,
               str((funnel.get("total"),
                    funnel.get("byStatus"))))
        record("漏斗转化(曾抵达交付 1.0+回滚 0.5)",
               (funnel.get("conversion") or {})
               .get("delivered") == 1.0
               and (funnel.get("conversion")
                    or {}).get("rolledBack") == 0.5,
               str(funnel.get("conversion")))
        record("漏斗决策分布(propose)",
               (funnel.get("decisions") or {})
               .get("propose") == 2,
               str(funnel.get("decisions")))

        # ② 资产区
        assets_zone = zones.get("assets") or {}
        record("资产区(2 资产+草稿)",
               assets_zone.get("totalAssets") == 2
               and (assets_zone.get("draftsTotal")
                    or 0) >= 2,
               str((assets_zone.get("totalAssets"),
                    assets_zone.get("draftsTotal"))))
        record("资产区(VALUE_REASON 证据)",
               (assets_zone.get("valueReasonsTotal")
                or 0) >= 2,
               str(assets_zone.get(
                   "valueReasonsTotal")))
        record("资产区(沙箱 passed 分布)",
               (assets_zone.get("sandboxByVerdict")
                or {}).get("passed") == 2,
               str(assets_zone.get(
                   "sandboxByVerdict")))

        # ③ 合规区
        compliance = zones.get("compliance") or {}
        record("合规区(审计 passed)",
               (compliance.get("auditVerdicts")
                or {}).get("passed") == 2,
               str(compliance.get("auditVerdicts")))
        record("合规区(一票否决 0)",
               compliance.get("vetoCount") == 0,
               str(compliance.get("vetoCount")))
        record("合规区(审批 approved+确认完整率 1.0)",
               (compliance.get("reviewVerdicts")
                or {}).get("approved") == 2
               and compliance.get(
                   "confirmationCompleteRate") == 1.0,
               str((compliance.get(
                        "reviewVerdicts"),
                    compliance.get(
                        "confirmationCompleteRate"))))

        # ④ 防御区
        defense = zones.get("defense") or {}
        record("防御区(回滚 1)",
               defense.get("rollbacks") == 1,
               str(defense.get("rollbacks")))
        record("防御区(回流信号分布)",
               (defense.get("feedbackSignals")
                or {}).get("bySignal")
               == {"rollback_after_deliver": 1,
                   "deliver_success": 1},
               str((defense.get("feedbackSignals")
                    or {}).get("bySignal")))
        record("防御区(护栏健康口径)",
               "healthy" in str(
                   defense.get("guardrail") or {}),
               str(defense.get("guardrail"))[:60])

        # off 态看板亦可用(观测面)
        os.environ["AIUP56_MODE"] = "off"
        off_board = await dash.build()
        record("off 态看板亦可用(观测面)",
               off_board.get("success") is True
               and (off_board.get("zones")
                    or {}).get("funnel") is not None,
               str(off_board.get("success")))
        os.environ["AIUP56_MODE"] = "shadow"


class TestRedteam:
    """02 红队六向量"""

    async def run(self):
        print("[02 红队六向量]")
        reset_all()

        from services.aiup56_redteam_service import (
            Aiup56RedteamService,
        )
        rt = Aiup56RedteamService()

        # off 拒绝(无攻击面)
        os.environ["AIUP56_MODE"] = "off"
        try:
            await rt.run_all()
            ok, err = False, "未拒绝"
        except ValueError as e:
            ok, err = "红队" in str(e), str(e)[:40]
        record("off 态红队拒绝(无攻击面)", ok, err)

        # 六向量全量
        os.environ["AIUP56_MODE"] = "shadow"
        r = await rt.run_all()
        summary = r.get("summary") or {}
        vectors = r.get("vectors") or {}

        record("红队全量(六向量)",
               summary.get("total") == 6
               and len(vectors) == 6,
               str((summary.get("total"),
                    len(vectors))))
        record("六向量全防御(allDefended)",
               summary.get("allDefended") is True,
               str(summary))

        # 向量语义(六名齐备)
        record("六向量语义齐备",
               [v.get("vector") for v
                in vectors.values()] == [
                   "提案投毒(伪造信号源灌入)",
                   "预算耗尽攻击(超支硬测)",
                   "审批绕过(三路攻击)",
                   "资产注入(草稿含恶意载荷)",
                   "信号伪造(注册表外信号)",
                   "回滚破坏(状态机三路)"],
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

        # RT-02 熔断细节
        rt02 = (vectors.get("RT-02")
                or {}).get("results") or [{}]
        record("RT-02 熔断(spent 0.5>cap 0.1)",
               (rt02[0].get("verdict")
                == "budget_halted")
               and rt02[0].get("status")
               == "blocked"
               and rt02[0].get("spent") == 0.5,
               str(rt02[0]))

        # RT-03 三路拒绝
        rt03 = (vectors.get("RT-03")
                or {}).get("results") or []
        record("RT-03 三路全拒",
               len(rt03) == 3
               and all(x.get("rejected")
                      for x in rt03),
               str(rt03))

        # RT-04 恶意载荷三型检出
        rt04 = (vectors.get("RT-04")
                or {}).get("results") or [{}]
        violations = rt04[0].get("violations") or []
        record("RT-04 三型检出(blocked)",
               rt04[0].get("status") == "blocked"
               and len(violations) >= 3,
               str(len(violations)))

        # RT-05 注册表封闭
        rt05 = (vectors.get("RT-05")
                or {}).get("results") or []
        record("RT-05 白名单外拒绝+自检触发",
               len(rt05) >= 2
               and all(x.get("rejected")
                      for x in rt05[:2]),
               str(rt05))

        # RT-06 状态机三路
        rt06 = (vectors.get("RT-06")
                or {}).get("results") or []
        record("RT-06 状态机三路全拒",
               len(rt06) == 3
               and all(x.get("rejected")
                      for x in rt06),
               str(rt06))

        # 红队后注册表完整性(注入已恢复)
        from services.aiup56_registry import (
            SIGNAL_REGISTRY,
        )
        record("红队后注册表完整(10 项)",
               len(SIGNAL_REGISTRY) == 10
               and "backdoor_signal"
               not in SIGNAL_REGISTRY,
               str(len(SIGNAL_REGISTRY)))


class TestConstitution:
    """03 宪法断言"""

    async def run(self):
        print("[03 宪法断言]")
        reset_all()

        # 44号 ≥31 档案保持
        from services.ai_learning_service import (
            SCORER_REGISTRY,
        )
        record("44号 ≥31 档案保持",
               len(SCORER_REGISTRY) >= 31,
               str(len(SCORER_REGISTRY)))

        # 第31档案在册
        record("第31档案在册(upgrade_orchestration)",
               "upgrade_orchestration"
               in SCORER_REGISTRY,
               "")

        # 注册表封闭(10 项+四侧+权重和)
        from services.aiup56_registry import (
            SIGNAL_REGISTRY,
        )
        sides = {v["side"] for v
                 in SIGNAL_REGISTRY.values()}
        weight_sum = sum(
            v["weight"] for v
            in SIGNAL_REGISTRY.values()
            if v.get("status") == "active")
        record("注册表 10 项四侧",
               len(SIGNAL_REGISTRY) == 10
               and sides == {
                   "model", "user",
                   "system", "compliance"},
               str((len(SIGNAL_REGISTRY),
                    sorted(sides))))
        record("注册表权重和=1.0",
               abs(weight_sum - 1.0) < 1e-9,
               str(weight_sum))


class TestHttp:
    """04 HTTP 层"""

    async def run(self):
        print("[04 HTTP]")
        reset_all()
        os.environ["AIUP56_MODE"] = "shadow"
        await seed_full_chain_proposal(
            rolled_back=True)

        from fastapi.testclient import TestClient
        from main import app
        client = TestClient(app)
        admin = {"X-Role": "admin"}

        # dashboard 200(观测面)
        resp = client.get("/api/aiup56/dashboard",
                          headers=admin)
        body = resp.json() or {}
        zones = body.get("zones") or {}
        record("HTTP dashboard 200(四区)",
               resp.status_code == 200
               and set(zones.keys()) == {
                   "funnel", "assets",
                   "compliance", "defense"},
               str((resp.status_code,
                    sorted(zones.keys()))))
        record("HTTP 漏斗数字(1 提案)",
               (zones.get("funnel") or {})
               .get("total") == 1,
               str((zones.get("funnel") or {})
                   .get("total")))

        # off 态 dashboard 亦可用(观测面)
        os.environ["AIUP56_MODE"] = "off"
        resp = client.get("/api/aiup56/dashboard",
                          headers=admin)
        record("HTTP dashboard off 亦可用",
               resp.status_code == 200,
               str(resp.status_code))

        # off 态 redteam 409
        resp = client.post("/api/aiup56/redteam",
                           headers=admin)
        record("HTTP redteam off 409",
               resp.status_code == 409,
               str(resp.status_code))

        # redteam 200(全防御)
        os.environ["AIUP56_MODE"] = "shadow"
        resp = client.post("/api/aiup56/redteam",
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
                ("GET", "/api/aiup56/dashboard"),
                ("POST", "/api/aiup56/redteam")):
            resp = client.request(method, path)
            record(f"HTTP {path.split('/')[-1]}"
                   f" 无 Role 403",
                   resp.status_code == 403,
                   str(resp.status_code))

        # 路由累计 20 端点
        from routes.aiup56_routes import (
            router as aiup_router,
        )
        count = sum(1 for r in aiup_router.routes)
        record("56号路由累计 20 端点",
               count == 20, str(count))
        os.environ["AIUP56_MODE"] = "off"


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
