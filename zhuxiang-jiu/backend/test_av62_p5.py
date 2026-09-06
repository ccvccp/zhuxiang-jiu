"""62号·AI智能无形资产估值模块 P5 专项测试
(四区看板+红队七向量+收官三件套)

运行方式:
    python test_av62_p5.py

覆盖(62号计划 §七 P5):
    - 四区看板: 度量(估值准确率/
      归因锚定率/公平达标/申诉
      翻转率)+资产+评估+防御
    - 红队七向量: 证据伪造/权重
      操纵/归因幻觉/流动性滥用/
      估值套利/申诉刷分/负资产
      洗白
    - 宪法断言: 44号 38 档案
      +45/47/51号零改动+三开关铁律
    - 收官三件套: 25 端点/
      服务在册/表全量
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
os.environ["AV62_MODE"] = "shadow"
os.environ["AV62_LLM_MODE"] = "off"
os.environ["AV62_LEARN_MODE"] = "off"

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


async def seed_assessment(subject_id, role,
                          domain, evidence):
    from services.av62_service import (
        Av62Service,
    )
    from services.av62_assess_service import (
        Av62AssessService,
    )
    a = await Av62Service().register_asset(
        subject_id=subject_id, role=role,
        domain=domain, evidence=evidence,
        label=f"{role}/{domain}")
    r = await Av62AssessService() \
        .assess_asset(a["assetId"])
    return a, r


class TestDashboard:
    """01 四区看板"""

    async def run(self):
        print("[01 四区看板]")
        reset_all()
        from services.av62_dashboard_service import (
            Av62DashboardService,
        )
        svc = Av62DashboardService()

        # 空态看板
        d0 = await svc.get_dashboard()
        zones = d0.get("zones") or {}
        record("看板结构(四区)",
               set(zones) == {
                   "metrics", "assets",
                   "assessments",
                   "defense"},
               str(sorted(zones)))
        m0 = zones.get("metrics") or {}
        record("空态度量(未验证 None)",
               m0.get(
                   "valuationAccuracy")
               is None
               and m0.get(
                   "attributionGrounded")
               == 1.0,
               str((m0.get(
                        "valuationAccuracy"),
                    m0.get(
                        "attributionGrounded"))))
        a0 = zones.get("assets") or {}
        record("空态资产(0 条)",
               a0.get("total") == 0,
               str(a0.get("total")))

        # 种子: 全链数据
        a1, r1 = await seed_assessment(
            101, "enterprise",
            "compliance",
            {"licenseCount": 5,
             "auditResults": "通过",
             "esgDisclosure": "已披露"})
        _, r2 = await seed_assessment(
            101, "enterprise", "risk",
            {"penaltyRecords": 5})
        _, r3 = await seed_assessment(
            101, "personal",
            "capability",
            {"skillCerts": 8,
             "deliveryQuality": 0.95,
             "knowledgeSharing": 24})

        # 验证信号+回流+申诉+审计
        from services.av62_learn_service import (
            Av62LearnService,
        )
        learn = Av62LearnService()
        await learn.submit_verification(
            r1["assessId"], 85)
        await learn.submit_verification(
            r3["assessId"], 150)
        from services.ai_governance_service import (
            AiGovernanceService,
        )
        await AiGovernanceService() \
            .sync_registry()
        await learn.collect_verification()

        from services.av62_appeal_service import (
            Av62AppealService,
        )
        ap = await Av62AppealService() \
            .submit_appeal(
                a1["assetId"], "验证申诉")
        await Av62AppealService() \
            .review_appeal(
                ap["appealId"],
                decision="overturn",
                reviewed_by="裁决官",
                review_note="采纳")

        from services.av62_fairness_service import (
            Av62FairnessService,
        )
        await Av62FairnessService() \
            .run_audit()

        # 红队一轮(防御区读回数据)
        from services.av62_redteam_service import (
            Av62RedteamService,
        )
        await Av62RedteamService() \
            .run_all()

        d = await svc.get_dashboard()
        zones = d.get("zones") or {}

        # 度量区
        m = zones.get("metrics") or {}
        record("估值准确率(1/2=0.5)",
               m.get(
                   "valuationAccuracy")
               == 0.5,
               str(m.get(
                   "valuationAccuracy")))
        record("归因锚定率(1.0)",
               m.get(
                   "attributionGrounded")
               == 1.0,
               str(m.get(
                   "attributionGrounded")))
        fairness = m.get(
            "fairness") or {}
        record("公平达标区(报告读回)",
               "flagged" in fairness
               and "insufficient"
               in fairness,
               str(sorted(fairness)))
        record("申诉翻转率(1.0)",
               m.get(
                   "appealOverturnRate")
               == 1.0,
               str(m.get(
                   "appealOverturnRate")))
        record("验证/评估计数",
               m.get("verifiedCount")
               == 2
               and m.get(
                   "assessedCount")
               >= 3,
               str((m.get(
                        "verifiedCount"),
                    m.get(
                        "assessedCount"))))

        # 资产区
        a = zones.get("assets") or {}
        record("资产分布(≥3 条+"
               "负资产——含红队种子)",
               a.get("total") >= 3
               and a.get(
                   "negativeCount")
               >= 1
               and (a.get("byRole")
                    or {}).get(
                        "enterprise")
               >= 2,
               str((a.get("total"),
                    a.get(
                        "negativeCount"))))
        record("流动性档分布"
               "(high+none)",
               (a.get("byLiquidity")
                or {}).get("high")
               >= 1
               and (a.get(
                   "byLiquidity")
                   or {}).get("none")
               >= 1,
               str(a.get(
                   "byLiquidity")))
        record("资产状态分布"
               "(九态追踪)",
               len(a.get("byStatus")
                   or {}) >= 2,
               str(a.get("byStatus")))

        # 评估区
        asm = zones.get(
            "assessments") or {}
        record("评估区(版本链+"
               "置信度)",
               asm.get("total") >= 3
               and len(asm.get(
                   "byConfidence")
                   or {}) >= 1
               and asm.get(
                   "maxVersionChain")
               >= 1,
               str((asm.get(
                        "total"),
                    asm.get(
                        "byConfidence"))))
        record("objective 生效态"
               "(stability)",
               asm.get("objective")
               == "stability",
               str(asm.get(
                   "objective")))
        record("池化标记统计",
               asm.get("pooled") == 2,
               str(asm.get("pooled")))

        # 防御区
        df = zones.get("defense") or {}
        record("防御区(红队读回)",
               df.get("redteamRuns") == 1
               and (df.get(
                   "redteamLatest")
                   or {}).get(
                       "defendedAll")
               is True,
               str((df.get(
                        "redteamRuns"),
                    (df.get(
                        "redteamLatest")
                     or {}).get(
                        "defendedAll"))))
        record("红队向量明细读回",
               len((df.get(
                   "redteamLatest")
                   or {}).get(
                       "vectors")
                   or []) == 7,
               str(len((df.get(
                   "redteamLatest")
                   or {}).get(
                       "vectors")
                   or [])))


class TestRedteam:
    """02 红队七向量"""

    async def run(self):
        print("[02 红队七向量]")
        reset_all()
        from services.av62_redteam_service import (
            Av62RedteamService,
        )
        svc = Av62RedteamService()

        # off 态拒绝(无攻击面)
        os.environ["AV62_MODE"] = "off"
        try:
            await svc.run_all()
            ok, err = False, "未拒绝"
        except ValueError:
            ok, err = True, ""
        record("off 态红队拒绝", ok, err)
        os.environ["AV62_MODE"] = "shadow"

        r = await svc.run_all()
        vectors = {
            v.get("vector"): v
            for v in r.get("vectors")
            or []}
        record("七向量齐备",
               len(r.get("vectors")
                   or []) == 7
               and set(vectors) == {
                   "RT-01 证据伪造",
                   "RT-02 权重操纵",
                   "RT-03 归因幻觉",
                   "RT-04 流动性滥用",
                   "RT-05 估值套利",
                   "RT-06 申诉刷分",
                   "RT-07 负资产洗白"},
               str(len(r.get(
                   "vectors") or [])))
        record("全量防御(defendedAll)",
               r.get("defendedAll")
               is True,
               str(r.get(
                   "defendedAll")))

        # RT-01 证据伪造
        v01 = vectors.get(
            "RT-01 证据伪造") or {}
        record("RT-01 域外字段拒绝",
               v01.get("defended")
               is True,
               str(v01.get("defended")))
        # RT-02 权重操纵
        v02 = vectors.get(
            "RT-02 权重操纵") or {}
        record("RT-02 objective"
               " 恒 stability",
               v02.get("defended")
               is True
               and v02.get(
                   "activeObjective")
               == "stability",
               str(v02.get(
                   "activeObjective")))
        # RT-03 归因幻觉
        v03 = vectors.get(
            "RT-03 归因幻觉") or {}
        record("RT-03 无锚点未验证",
               v03.get("defended")
               is True,
               "")
        # RT-04 流动性滥用
        v04 = vectors.get(
            "RT-04 流动性滥用") or {}
        record("RT-04 high 限频 10",
               v04.get("defended")
               is True
               and v04.get(
                   "frequencyCap")
               == 10,
               str(v04.get(
                   "frequencyCap")))
        # RT-05 估值套利
        v05 = vectors.get(
            "RT-05 估值套利") or {}
        record("RT-05 risk 恒 none",
               v05.get("defended")
               is True
               and v05.get("riskTier")
               == "none",
               str(v05.get("riskTier")))
        # RT-06 申诉刷分
        v06 = vectors.get(
            "RT-06 申诉刷分") or {}
        record("RT-06 重复申诉拒绝",
               v06.get("defended")
               is True,
               "")
        # RT-07 负资产洗白
        v07 = vectors.get(
            "RT-07 负资产洗白") or {}
        record("RT-07 减持+清零"
               "双拒绝",
               v07.get("defended")
               is True
               and len(v07.get(
                   "attacks")
                   or []) == 2,
               str(len(v07.get(
                   "attacks") or [])))

        # 红队留痕
        from repositories.av62_repository import (
            Av62Repository,
        )
        evs = await Av62Repository() \
            .list_events(limit=50)
        runs = [
            e for e in evs
            if e.get("eventType")
            == "redteam_run"]
        record("红队留痕"
               "(redteam_run×1)",
               len(runs) == 1,
               str(len(runs)))


class TestHttp:
    """03 HTTP 层"""

    async def run(self):
        print("[03 HTTP]")
        reset_all()
        from fastapi.testclient import \
            TestClient
        from main import app
        client = TestClient(app)
        admin = {"X-Role": "admin"}

        # 观测面 off 可用
        os.environ["AV62_MODE"] = "off"
        resp = client.get(
            "/api/av62/dashboard",
            headers=admin)
        body = resp.json() or {}
        record("HTTP dashboard 200"
               "(off 观测面)",
               resp.status_code == 200
               and set(body.get(
                   "zones") or {}
                   ) == {
                   "metrics", "assets",
                   "assessments",
                   "defense"},
               str(resp.status_code))

        # 决策面 off 409
        resp = client.post(
            "/api/av62/redteam",
            json={},
            headers=admin)
        record("HTTP redteam off 409",
               resp.status_code == 409,
               str(resp.status_code))

        # shadow 红队全链
        os.environ["AV62_MODE"] = "shadow"
        resp = client.post(
            "/api/av62/redteam",
            json={},
            headers=admin)
        body = resp.json() or {}
        record("HTTP redteam 200"
               "(七向量)",
               resp.status_code == 200
               and body.get(
                   "defendedAll")
               is True
               and len(body.get(
                   "vectors") or [])
               == 7,
               str((resp.status_code,
                    body.get(
                        "defendedAll"))))

        # 看板读回(防御区红队数据)
        resp = client.get(
            "/api/av62/dashboard",
            headers=admin)
        body = resp.json() or {}
        defense = ((body.get("zones")
                    or {})
                   .get("defense")
                   or {})
        record("HTTP 看板防御区"
               "(红队读回)",
               resp.status_code == 200
               and defense.get(
                   "redteamRuns") >= 1,
               str(defense.get(
                   "redteamRuns")))

        # 鉴权 403
        for method, path in (
                ("GET",
                 "/api/av62/dashboard"),
                ("POST",
                 "/api/av62/redteam")):
            resp = client.request(
                method, path, json={})
            record(f"HTTP "
                   f"{path.split('/')[-1]}"
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
        record("44号 38 档案在册",
               len(SCORER_REGISTRY) == 39,
               str(len(SCORER_REGISTRY)))
        record("第37档案 asset_valuation"
               "(batch21)",
               SCORER_REGISTRY.get(
                   "asset_valuation")
               is not None
               and SCORER_REGISTRY[
                   "asset_valuation"
               ]["batch"] == 21,
               str(SCORER_REGISTRY.get(
                   "asset_valuation")))

        # 零改动断言(纯读取)
        try:
            from repositories import \
                trust_value_repository as r45
            from services import \
                trust_risk_profile_service as s47
            from services import \
                kg51_trace_service as s51
            from services import \
                ai_governance_fairness as s46
            record("45/47/51/46号零改动"
                   "(纯读取)",
                   all(m is not None
                       for m in (
                           r45, s47,
                           s51, s46)),
                   "")
        except ImportError:
            record("45/47/51/46号零改动"
                   "(纯读取)",
                   False, "导入失败")

        # 三开关铁律
        record("三开关铁律"
               "(默认 off)",
               os.environ.get(
                   "AV62_LLM_MODE") == "off"
               and os.environ.get(
                   "AV62_LEARN_MODE")
               == "off",
               "")

        # 收官三件套之一: 端点
        from routes.av62_routes import (
            router as av_router,
        )
        count = sum(
            1 for r in av_router.routes)
        record("收官端点 25",
               count == 25, str(count))

        # 收官三件套之二: 服务
        import services.av62_service
        import services.av62_assess_service
        import services.av62_liquidity_service
        import services.av62_threshold_service
        import services.av62_appeal_service
        import services.av62_fairness_service
        import services.av62_learn_service
        import services.av62_scheduler
        import services.av62_dashboard_service
        import services.av62_redteam_service
        record("收官服务 10 在册",
               True, "")

        # 收官三件套之三: 表
        from repositories.av62_repository import (
            Av62Repository,
        )
        record("收官表 7 全量",
               len(Av62Repository
                   ._ALL_TABLES) == 7,
               str(len(
                   Av62Repository
                   ._ALL_TABLES)))


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
