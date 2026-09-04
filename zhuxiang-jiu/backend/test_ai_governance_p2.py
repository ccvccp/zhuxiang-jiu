"""46号·AI 治理与合规中枢 P2 专项测试(公平性审计)

运行方式:
    python test_ai_governance_p2.py

覆盖(计划 §五):
    - 指标数学断言: 均值差异比/通过率差/空群体跳过/
      采样不足不出结论/单群体不 flag
    - 阈值边界: 20% 差异比/15pp 通过率差的命中与不命中
    - 采样管道: 批量上报/脱敏红线(个人标识字段拒绝)/
      参数校验(group/score 区间/passed 类型/上限)
    - 45号事件适配器: 双角色导入/幂等跳过/读取异常
      fail-soft
    - 审计报告: 触发落库/最新查询/历史列表/档案过滤/
      未入册拒绝
    - HTTP 层: 三端点结构与鉴权
"""

import asyncio
import os
import sys

os.environ["LOCK_MODE"] = "asyncio"
os.environ["STORE_MODE"] = "asyncio"
os.environ.pop("LLM_API_KEY", None)
os.environ["LLM_ENABLED"] = "off"

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


def samples(groups: dict) -> list[dict]:
    """{group: [(score, passed|None)]} → 采样列表"""
    out = []
    for g, items in groups.items():
        for score, passed in items:
            s = {"group": g, "score": score}
            if passed is not None:
                s["passed"] = passed
            out.append(s)
    return out


async def seed_registry():
    from services.ai_governance_service import (
        AiGovernanceService,
    )
    await AiGovernanceService().sync_registry()


class TestMetrics:
    async def run(self):
        print("[01 指标数学断言]")
        from services.ai_governance_fairness import (
            AiGovernanceFairnessService,
        )
        compute = AiGovernanceFairnessService.compute_metrics

        # 均值差异比: A=80×10, B=50×10 → meanAll=65,
        # max|diff|=15 → 15/65≈0.2308 > 20% → flagged
        m = compute(samples({"A": [(80, True)] * 10,
                             "B": [(50, False)] * 10}))
        record("均值差异比计算", m["meanDiffRatio"] == 0.2308,
               str(m["meanDiffRatio"]))
        record("差异比超阈值flag", m["flagged"] is True,
               str(m["flagged"]))
        record("通过率差计算", m["passRateGap"] == 100.0,
               str(m["passRateGap"]))
        record("群体统计n/mean",
               m["groups"][0]["n"] == 10
               and m["groups"][0]["mean"] == 80.0,
               str(m["groups"][:1]))
        record("meanAll计算", m["meanAll"] == 65.0,
               str(m["meanAll"]))

        # 差异比不超阈值: A=60, B=62 → 2/61≈3.3% 不flag
        m = compute(samples({"A": [(60, True)] * 10,
                             "B": [(62, True)] * 10}))
        record("差异比内不flag", m["flagged"] is False,
               str(m["flagged"]))

        # 通过率差: A 90% vs B 70% → 20pp > 15pp → flag
        m = compute(samples({
            "A": [(60, True)] * 9 + [(60, False)],
            "B": [(60, True)] * 7 + [(60, False)] * 3}))
        record("通过率差20pp命中", m["passRateGap"] == 20.0
               and m["flagged"] is True,
               f"gap={m['passRateGap']}")

        # 通过率差边界: A 85% vs B 70% → 15pp 不超(>15 才 flag)
        m = compute(samples({
            "A": [(60, True)] * 17 + [(60, False)] * 3,
            "B": [(60, True)] * 14 + [(60, False)] * 6}))
        record("通过率差15pp边界不命中",
               m["passRateGap"] == 15.0
               and m["flagged"] is False,
               f"gap={m['passRateGap']}")

        # passed 缺省: 通过率差不参与(仅均值指标)
        m = compute(samples({"A": [(80, None)] * 10,
                             "B": [(50, None)] * 10}))
        record("passed缺省仅均值指标",
               m["passRateGap"] == 0.0
               and m["flagged"] is True,
               str(m["passRateGap"]))

        # 采样不足: 19 条 < 20 → insufficient 不出结论
        m = compute(samples({"A": [(80, True)] * 10,
                             "B": [(50, False)] * 9}))
        record("采样不足不出结论", m["insufficient"] is True
               and m["flagged"] is False
               and "采样不足" in m["conclusion"],
               str(m["conclusion"])[:40])

        # 空群体跳过: C 组 3 条 < 5 → 跳过不参与
        m = compute(samples({"A": [(60, True)] * 10,
                             "B": [(60, True)] * 10,
                             "C": [(90, True)] * 3}))
        record("小群体跳过", "C" in m["skippedGroups"]
               and all(g["group"] != "C"
                       for g in m["groups"]
                       if g["eligible"]),
               str(m["skippedGroups"]))
        record("小群体跳过不误报",
               m["flagged"] is False,
               str(m["flagged"]))

        # 单一群体: 无对比基准不 flag
        m = compute(samples({"A": [(60, True)] * 25}))
        record("单群体不flag", m["flagged"] is False
               and m["groupCount"] == 1,
               str(m["groupCount"]))

        # 空采样
        m = compute([])
        record("空采样安全", m["sampleCount"] == 0
               and m["flagged"] is False,
               str(m["sampleCount"]))


class TestSamplePipeline:
    async def run(self):
        print("[02 采样管道]")
        reset_all()
        await seed_registry()
        from services.ai_governance_fairness import (
            AiGovernanceFairnessService,
        )
        svc = AiGovernanceFairnessService()

        r = await svc.submit_samples("trust_value", samples({
            "A": [(80, True)] * 10,
            "B": [(50, False)] * 10}))
        record("批量上报20条", r["success"] is True
               and r["accepted"] == 20, str(r)[:60])
        count = await svc.repo.count_samples("trust_value")
        record("采样落库计数", count == 20, str(count))

        # 脱敏红线
        for name, s in (
                ("含idNumber拒绝", {"group": "A",
                                    "score": 60,
                                    "idNumber": "110101"}),
                ("含phone拒绝", {"group": "A", "score": 60,
                                 "phone": "13800000000"}),
                ("含userId拒绝", {"group": "A", "score": 60,
                                  "userId": 42})):
            try:
                await svc.submit_samples(
                    "trust_value", [s])
                record(name, False, "未抛")
            except ValueError as e:
                record(name, "个人标识" in str(e), str(e))

        # 参数校验
        for name, args in (
                ("空samples拒绝", ("trust_value", [])),
                ("非数组拒绝", ("trust_value", "not-list")),
                ("超上限拒绝", ("trust_value",
                                [{"group": "A", "score": 60}]
                                * 1001)),
        ):
            try:
                await svc.submit_samples(*args)
                record(name, False, "未抛")
            except ValueError:
                record(name, True)
        for name, s in (
                ("缺group拒绝", {"score": 60}),
                ("group超长拒绝", {"group": "x" * 51,
                                  "score": 60}),
                ("score非数值拒绝", {"group": "A",
                                    "score": "abc"}),
                ("score越界拒绝", {"group": "A",
                                  "score": 101}),
                ("passed非布尔拒绝", {"group": "A",
                                     "score": 60,
                                     "passed": "yes"}),
        ):
            try:
                await svc.submit_samples("trust_value", [s])
                record(name, False, "未抛")
            except ValueError:
                record(name, True)

        # 非法 source
        try:
            await svc.submit_samples(
                "trust_value",
                [{"group": "A", "score": 60}],
                source="bad")
            record("非法source拒绝", False, "未抛")
        except ValueError:
            record("非法source拒绝", True)

        # 未入册档案拒绝
        try:
            await svc.submit_samples(
                "unknown_scorer",
                [{"group": "A", "score": 60}])
            record("未入册拒绝", False, "未抛")
        except KeyError:
            record("未入册拒绝", True)


class TestTrust45Adapter:
    async def run(self):
        print("[03 45号事件适配器]")
        reset_all()
        await seed_registry()
        from services.ai_governance_fairness import (
            AiGovernanceFairnessService,
        )
        from repositories.trust_value_repository import (
            TrustValue45Repository,
        )
        svc = AiGovernanceFairnessService()

        # 空数据导入
        r = await svc.import_trust45()
        record("空数据导入安全", r["success"] is True
               and r["imported"] == 0, str(r)[:60])

        # 灌 45号双角色档案
        repo45 = TrustValue45Repository()
        for i, role in enumerate(("person", "person",
                                  "org")):
            tid = i + 1
            await repo45.save_profile({
                "trustId": tid, "role": role,
                "name": f"t{tid}", "idDigest": f"d{tid}",
                "factors": {}, "score": 60 + i * 10,
                "rawScore": 60 + i * 10, "grade": "C",
                "fused": False, "fusedLevel": "",
                "frozen": False, "l1Severity": {},
                "createdAt": "2026-09-01T00:00:00+00:00",
                "updatedAt": "2026-09-01T00:00:00+00:00"})
        r = await svc.import_trust45()
        record("双角色导入3条", r["success"] is True
               and r["imported"] == 3, str(r)[:60])
        sams = await svc.repo.list_samples("trust_value")
        record("来源标记trust45",
               all(s.get("source") == "trust45"
                   for s in sams) and len(sams) == 3,
               str(len(sams)))
        roles = {s["group"] for s in sams}
        record("角色分组正确", roles == {"person", "org"},
               str(roles))

        # 幂等: 再导入跳过
        r = await svc.import_trust45()
        record("幂等跳过", r["imported"] == 0
               and "幂等" in r.get("note", ""),
               str(r)[:60])
        sams = await svc.repo.list_samples("trust_value")
        record("重复导入不膨胀", len(sams) == 3,
               str(len(sams)))

        # fail-soft: 45号读取异常
        from repositories import trust_value_repository \
            as tmod
        orig = tmod.TrustValue45Repository.list_profiles

        async def _boom(self, limit=5000):
            raise RuntimeError("45号存储瞬断")
        tmod.TrustValue45Repository.list_profiles = _boom
        try:
            r = await svc.import_trust45()
            record("45号读取异常fail-soft",
                   r["success"] is True
                   and "读取失败" in r.get("note", ""),
                   str(r)[:60])
        finally:
            tmod.TrustValue45Repository.list_profiles = orig


class TestAuditReport:
    async def run(self):
        print("[04 审计报告]")
        reset_all()
        await seed_registry()
        from services.ai_governance_fairness import (
            AiGovernanceFairnessService,
        )
        svc = AiGovernanceFairnessService()

        # 未上报采样先审计 → insufficient 报告
        r = await svc.run_audit("trust_value")
        record("无采样审计insufficient",
               r["success"] is True
               and r["insufficient"] is True,
               str(r)[:70])

        # 灌偏差数据审计 → flagged
        await svc.submit_samples("trust_value", samples({
            "A": [(80, True)] * 10,
            "B": [(50, False)] * 10}))
        r = await svc.run_audit("trust_value")
        record("偏差审计flagged", r["flagged"] is True
               and r["reportId"] >= 1
               and "偏疑标记" in r["conclusion"],
               str(r.get("flagged")))
        rid = r["reportId"]

        # 报告落库读回
        stored = await svc.repo.get_report(rid)
        record("报告落库读回", stored is not None
               and stored["flagged"] is True
               and isinstance(stored.get("groups"), list)
               and len(stored["groups"]) == 2,
               str(stored)[:70] if stored else "None")

        # 最新报告查询
        view = await svc.get_report(scorer_id="trust_value")
        record("最新报告查询",
               (view.get("report") or {}).get("reportId")
               == rid, str(view.get("report"))[:50])
        record("阈值随报告返回",
               (view.get("thresholds") or {})
               .get("meanDiffRatio") == 0.20,
               str(view.get("thresholds")))

        # 灌均衡数据再审计 → 新报告不 flag(历史可追溯)
        from repositories.store import reset_store
        # 清采样重灌(保留报告表验证历史)
        svc.repo.store.pop(
            svc.repo.TABLE_FAIRNESS_SAMPLES, None)
        svc.repo.store.pop("_ai46_fairness_index", None)
        svc.repo.store.pop("_ai46_sample_seq", None)
        await svc.submit_samples("trust_value", samples({
            "A": [(60, True)] * 10,
            "B": [(62, True)] * 10}))
        r2 = await svc.run_audit("trust_value")
        record("均衡审计不flag", r2["flagged"] is False
               and r2["reportId"] > rid,
               str(r2.get("flagged")))

        # 历史列表
        hist = await svc.get_report(scorer_id="trust_value",
                                    history=True)
        record("报告历史列表",
               hist["total"] >= 2
               and all(h.get("reportId") for h in
                       hist["reports"]),
               str(hist.get("total")))

        # 全档案过滤
        all_r = await svc.repo.list_reports(limit=100)
        record("报告档案过滤",
               all(r.get("scorerId") == "trust_value"
                   for r in all_r),
               str(len(all_r)))

        # 未入册审计拒绝
        try:
            await svc.run_audit("unknown_scorer")
            record("未入册审计拒绝", False, "未抛")
        except KeyError:
            record("未入册审计拒绝", True)

        # 空档案最新报告(无历史)
        view = await svc.get_report(scorer_id="order_risk")
        record("无报告档案note提示",
               view.get("report") is None
               and "暂无" in view.get("note", ""),
               str(view.get("note"))[:40])


class TestHttp:
    async def run(self):
        print("[05 HTTP 层]")
        reset_all()
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from routes.ai_governance_routes import (
            register_ai_governance_routes,
        )
        app = FastAPI()
        register_ai_governance_routes(app)
        client = TestClient(app)
        admin = {"X-Role": "admin"}

        # 鉴权
        resp = client.post("/api/ai-gov/fairness/samples",
                           json={"scorerId": "trust_value",
                                 "samples": []})
        record("采样缺Role403", resp.status_code == 403,
               str(resp.status_code))
        resp = client.post("/api/ai-gov/fairness/audit",
                           json={"scorerId": "trust_value"})
        record("审计缺Role403", resp.status_code == 403,
               str(resp.status_code))
        resp = client.get("/api/ai-gov/fairness/report")
        record("报告缺Role403", resp.status_code == 403,
               str(resp.status_code))

        # 同步台账
        client.post("/api/ai-gov/registry/sync",
                    headers=admin)

        # 采样 200
        resp = client.post(
            "/api/ai-gov/fairness/samples",
            json={"scorerId": "trust_value",
                  "samples": [
                      {"group": "A", "score": 80,
                       "passed": True}] * 10 +
                      [{"group": "B", "score": 50,
                       "passed": False}] * 10},
            headers=admin)
        body = resp.json()
        record("采样200", resp.status_code == 200
               and body.get("accepted") == 20,
               str(body)[:60])

        # 采样含个人标识 409
        resp = client.post(
            "/api/ai-gov/fairness/samples",
            json={"scorerId": "trust_value",
                  "samples": [{"group": "A", "score": 60,
                               "phone": "138"}]},
            headers=admin)
        record("采样脱敏409", resp.status_code == 409,
               str(resp.status_code))

        # 审计 200
        resp = client.post(
            "/api/ai-gov/fairness/audit",
            json={"scorerId": "trust_value"}, headers=admin)
        body = resp.json()
        record("审计200落库", resp.status_code == 200
               and body.get("flagged") is True,
               str(body)[:70])

        # 报告 200
        resp = client.get(
            "/api/ai-gov/fairness/report"
            "?scorerId=trust_value", headers=admin)
        body = resp.json()
        record("报告200", resp.status_code == 200
               and (body.get("report") or {})
               .get("flagged") is True,
               str(body)[:60])

        # 报告历史 200
        resp = client.get(
            "/api/ai-gov/fairness/report"
            "?scorerId=trust_value&history=true",
            headers=admin)
        record("报告历史200",
               resp.status_code == 200
               and resp.json().get("total") >= 1,
               str(resp.status_code))

        # 未入册采样 404
        resp = client.post(
            "/api/ai-gov/fairness/samples",
            json={"scorerId": "no_such",
                  "samples": [{"group": "A",
                               "score": 60}]},
            headers=admin)
        record("未入册采样404", resp.status_code == 404,
               str(resp.status_code))

        # 全档案审计(含 importTrust45)
        resp = client.post(
            "/api/ai-gov/fairness/audit",
            json={"importTrust45": True}, headers=admin)
        record("全档案审计200",
               resp.status_code == 200
               and resp.json().get("success") is True,
               str(resp.status_code))

        # P1/P0 路由回归
        resp = client.get("/api/ai-gov/health",
                          headers=admin)
        record("P1健康路由回归", resp.status_code == 200,
               str(resp.status_code))
        resp = client.get("/api/ai-gov/registry",
                          headers=admin)
        record("P0台账路由回归", resp.status_code == 200,
               str(resp.status_code))


async def run_all():
    await TestMetrics().run()
    await TestSamplePipeline().run()
    await TestTrust45Adapter().run()
    await TestAuditReport().run()
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
