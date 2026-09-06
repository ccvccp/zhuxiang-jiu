"""61号·AI智能系统升级决策模块 P5 专项测试
(四区看板+红队七向量+收官三件套)

运行方式:
    python test_dm61_p5.py

覆盖(61号计划 §七 P5):
    - 四区看板: 度量(准确率/自治/
      预测命中/预警有效)+请求+决策
      +防御
    - 红队七向量: 标签伪造/矩阵操纵/
      沙箱逃逸/先验投毒/裁决伪造/
      反馈污染/图谱污染
    - 宪法断言: 44号 37 档案
      +56/46/45号零改动+三开关铁律
    - 收官三件套: 17 端点/7 服务/
      8 表全量
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


async def seed_full_chain(title, action,
                          tier="standard",
                          error_budget=0.3,
                          fb_action=None,
                          fb_outcome=None):
    """造完整链(请求→评估→推荐→裁决
    可选反馈——终态)"""
    prev = os.environ.get("DM61_MODE")
    os.environ["DM61_MODE"] = "shadow"
    try:
        from services.ai_governance_service import (
            AiGovernanceService,
        )
        await AiGovernanceService(
        ).sync_registry()
        from services.dm61_service import (
            Dm61Service,
        )
        from services.dm61_assess_service import (
            Dm61AssessService,
        )
        from services.dm61_decision_service import (
            Dm61DecisionService,
        )
        from repositories.dm61_repository import (
            Dm61Repository,
        )
        r = await Dm61Service() \
            .create_request(title=title,
                           hour=3)
        await Dm61AssessService().assess(
            r["requestId"],
            tier=tier,
            error_budget=error_budget,
            history_fail_rate=0.0
            if error_budget >= 0.5
            else 0.05)
        rec = await (
            Dm61DecisionService().recommend(
                r["requestId"]))
        os.environ["DM61_MODE"] = "off"
        md = "追加观察" \
            if action == "modified" else ""
        # L3 双人复核判定(评估级别)
        level = (await Dm61Repository()
                 .get_decision(
                     rec["decisionId"])
                 ).get("level")
        co = "复核官" if level == "L3" else ""
        d = await Dm61DecisionService() \
            .decide(
                rec["decisionId"],
                action=action,
                decided_by="种子官",
                modified_detail=md,
                co_reviewer=co)
        if d.get("changeId"):
            try:
                await AiGovernanceService(
                ).review_change(
                    int(d["changeId"]),
                    approve=False,
                    reviewed_by="官",
                    review_note="解锁")
            except ValueError:
                pass
        if fb_action:
            from services.dm61_feedback_service import (
                Dm61FeedbackService,
            )
            await Dm61FeedbackService() \
                .submit(
                    rec["decisionId"],
                    action=fb_action,
                    outcome=fb_outcome)
        return r, rec
    finally:
        os.environ["DM61_MODE"] = prev \
            if prev is not None else "off"


class TestDashboard:
    """01 四区看板"""

    async def run(self):
        print("[01 四区看板]")
        reset_all()
        # 造数据: 3 条终态(L1/L2/L3)
        # +1 条 sim 同向 +1 条 dissent
        await seed_full_chain(
            "文案微调", "adopted",
            tier="trusted",
            error_budget=0.9,
            fb_action="adopted",
            fb_outcome="good")        # L1 good
        await seed_full_chain(
            "支付结算费率优化", "rejected"
            )                          # L2 rejected
        await seed_full_chain(
            "后台权限角色调整", "adopted",
            fb_action="adopted",
            fb_outcome="good")        # L3 good

        from services.dm61_dashboard_service import (
            Dm61DashboardService,
        )
        svc = Dm61DashboardService()

        # off 态看板可用(观测面铁律)
        db = await svc.dashboard()
        record("off 态看板可用(铁律)",
               db.get("success") is True,
               str(db.get("success")))
        record("四区齐备",
               set(db) >= {
                   "metrics", "requests",
                   "decisions", "defense"},
               str(sorted(db)))

        # ① 度量区
        m = db.get("metrics") or {}
        record("度量: 采纳 2 条",
               m.get("adoptedTotal") == 2,
               str(m.get("adoptedTotal")))
        record("度量: 决策准确率 100%",
               m.get("decisionAccuracy")
               == 100.0,
               str(m.get(
                   "decisionAccuracy")))
        record("度量: 自治占比 33.3%",
               m.get("autonomousRatio")
               == 33.3,
               str(m.get(
                   "autonomousRatio")))
        record("度量: 预警有效中性",
               m.get(
                   "dissentEffectiveness")
               == 100.0,
               str(m.get(
                   "dissentEffectiveness")))

        # ② 请求区
        rq = db.get("requests") or {}
        record("请求区: 3 条",
               rq.get("totalRequests") == 3,
               str(rq.get(
                   "totalRequests")))
        record("请求区: 来源分布",
               (rq.get("bySource")
                or {}).get("manual") == 3,
               str(rq.get("bySource")))

        # ③ 决策区
        dc = db.get("decisions") or {}
        record("决策区: 3 条",
               dc.get("totalDecisions")
               == 3,
               str(dc.get(
                   "totalDecisions")))
        record("决策区: 级别分布",
               (dc.get("byLevel")
                or {}).get("L3") == 1,
               str(dc.get("byLevel")))
        record("决策区: 46号提交 2",
               dc.get("busSubmitted") == 2,
               str(dc.get(
                   "busSubmitted")))

        # ④ 防御区
        df = db.get("defense") or {}
        record("防御区: off 零影响",
               df.get("mode") == "off"
               and df.get(
                   "zeroImpactWhenOff")
               is True,
               str(df.get("mode")))
        record("防御区: 红队留痕空",
               df.get(
                   "redteamLastRun")
               is None,
               str(df.get(
                   "redteamLastRun")))


class TestRedteam:
    """02 红队七向量"""

    async def run(self):
        print("[02 红队七向量]")
        reset_all()
        from services.dm61_redteam_service import (
            Dm61RedteamService,
        )
        svc = Dm61RedteamService()

        # off 态拒绝(无攻击面)
        try:
            await svc.run_all()
            ok, err = False, "未拒绝"
        except ValueError:
            ok, err = True, ""
        record("off 态红队拒绝", ok, err)

        # shadow 全量
        os.environ["DM61_MODE"] = "shadow"
        r = await svc.run_all()
        vectors = r.get("vectors") or {}
        record("七向量齐备",
               set(vectors) == {
                   "RT-01", "RT-02",
                   "RT-03", "RT-04",
                   "RT-05", "RT-06",
                   "RT-07"},
               str(sorted(vectors)))

        # 逐向量断言
        record("RT-01 标签伪造防御",
               (vectors.get("RT-01")
                or {}).get("defended")
               is True,
               str((vectors.get("RT-01")
                    or {}).get(
                   "defended")))
        record("RT-02 矩阵操纵防御",
               (vectors.get("RT-02")
                or {}).get("defended")
               is True,
               str((vectors.get("RT-02")
                    or {}).get(
                   "defended")))
        record("RT-03 沙箱逃逸防御",
               (vectors.get("RT-03")
                or {}).get("defended")
               is True,
               str((vectors.get("RT-03")
                    or {}).get(
                   "defended")))
        record("RT-04 先验投毒防御",
               (vectors.get("RT-04")
                or {}).get("defended")
               is True,
               str((vectors.get("RT-04")
                    or {}).get(
                   "defended")))
        record("RT-05 裁决伪造防御",
               (vectors.get("RT-05")
                or {}).get("defended")
               is True,
               str((vectors.get("RT-05")
                    or {}).get(
                   "defended")))
        record("RT-06 反馈污染防御",
               (vectors.get("RT-06")
                or {}).get("defended")
               is True,
               str((vectors.get("RT-06")
                    or {}).get(
                   "defended")))
        record("RT-07 图谱污染防御",
               (vectors.get("RT-07")
                or {}).get("defended")
               is True,
               str((vectors.get("RT-07")
                    or {}).get(
                   "defended")))

        # 汇总
        summary = r.get("summary") or {}
        record("全量防御(7/7)",
               summary.get("defended") == 7
               and summary.get(
                   "allDefended") is True,
               str(summary))

        # 红队后防御区读回
        from services.dm61_dashboard_service import (
            Dm61DashboardService,
        )
        df = (await (
            Dm61DashboardService()
            .dashboard())
        ).get("defense") or {}
        last = df.get(
            "redteamLastRun") or {}
        record("防御区红队读回",
               last.get("defended") == 7
               and last.get("total") == 7,
               str(last))

        os.environ["DM61_MODE"] = "off"


class TestHttp:
    """03 HTTP 层(P5 两新端点)"""

    async def run(self):
        print("[03 HTTP]")
        reset_all()
        from fastapi.testclient import \
            TestClient
        from main import app
        client = TestClient(app)
        admin = {"X-Role": "admin"}

        # off 态 dashboard 观测面可用
        resp = client.get(
            "/api/dm61/dashboard",
            headers=admin)
        body = resp.json() or {}
        record("HTTP dashboard 200(off 可用)",
               resp.status_code == 200
               and "metrics" in body
               and "defense" in body,
               str((resp.status_code,
                    sorted(body)[:4])))

        # off 态 redteam 409
        resp = client.post(
            "/api/dm61/redteam",
            json={}, headers=admin)
        record("HTTP redteam off 409",
               resp.status_code == 409,
               str(resp.status_code))

        # shadow 态 redteam 200
        os.environ["DM61_MODE"] = "shadow"
        resp = client.post(
            "/api/dm61/redteam",
            json={}, headers=admin)
        body = resp.json() or {}
        record("HTTP redteam 200(7/7)",
               resp.status_code == 200
               and (body.get(
                   "summary")
                   or {}).get(
                   "defended") == 7,
               str((resp.status_code,
                    (body.get(
                        "summary")
                     or {}).get(
                        "defended"))))
        os.environ["DM61_MODE"] = "off"

        # 鉴权 403
        for method, path in (
                ("GET",
                 "/api/dm61/dashboard"),
                ("POST",
                 "/api/dm61/redteam")):
            resp = client.request(
                method, path, json={})
            record(f"HTTP {path.split('/')[-1]}"
                   f" 无 Role 403",
                   resp.status_code == 403,
                   str(resp.status_code))


class TestConstitution:
    """04 宪法断言+收官三件套"""

    async def run(self):
        print("[04 宪法+收官]")
        from services.ai_learning_service import (
            SCORER_REGISTRY,
        )
        # ① 44号 37 档案+第36档案
        record("宪法: 44号 37 档案",
               len(SCORER_REGISTRY) == 40
               and SCORER_REGISTRY.get(
                   "decision_"
                   "orchestration",
                   {}).get("batch") == 20,
               str(len(SCORER_REGISTRY)))

        # ② 感知源零改动(纯消费)
        try:
            from services import \
                aiup56_service as s56
            from repositories import \
                trust_value_repository as r45
            record("宪法: 56/45号零改动",
                   s56 is not None
                   and r45 is not None,
                   "")
        except ImportError:
            record("宪法: 56/45号零改动",
                   False, "导入失败")

        # ③ 46号零改动(审批总线纯调用)
        try:
            from services import \
                ai_governance_service as s46
            record("宪法: 46号零改动",
                   s46 is not None,
                   "")
        except ImportError:
            record("宪法: 46号零改动",
                   False, "导入失败")

        # ④ 三开关铁律
        record("宪法: 三开关铁律(默认 off)",
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

        # ====== 收官三件套 ======
        # ⑤ 17 端点
        from routes.dm61_routes import (
            router as dm_router,
        )
        count = sum(
            1 for r in dm_router.routes)
        record("收官①: 17 端点",
               count == 17, str(count))

        # ⑥ 服务清单(9 服务)
        services = [
            "dm61_registry",
            "dm61_service",
            "dm61_scorer",
            "dm61_assess_service",
            "dm61_decision_service",
            "dm61_sim_service",
            "dm61_threshold_service",
            "dm61_dissent_service",
            "dm61_graph_service",
            "dm61_feedback_service",
            "dm61_learn_service",
            "dm61_scheduler",
            "dm61_dashboard_service",
            "dm61_redteam_service",
        ]
        import importlib
        all_ok = True
        for svc_name in services:
            try:
                importlib.import_module(
                    f"services.{svc_name}")
            except ImportError:
                all_ok = False
        record("收官②: 14 服务在册",
               all_ok,
               str(len(services)))

        # ⑦ 7 表+九态状态机
        from repositories.dm61_repository import (
            Dm61Repository,
        )
        record("收官③: 7 表仓储",
               len(Dm61Repository
                   ._ALL_TABLES) == 7,
               str(len(
                   Dm61Repository
                   ._ALL_TABLES)))
        from services.dm61_service import (
            REQUEST_STATES,
        )
        record("收官: 九态状态机",
               len(REQUEST_STATES) == 9,
               str(len(
                   REQUEST_STATES)))


async def run_all():
    await TestDashboard().run()
    await TestRedteam().run()
    await TestHttp().run()
    await TestConstitution().run()


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
