"""46号·AI 治理与合规中枢 P3 专项测试(决策回放与追溯)

运行方式:
    python test_ai_governance_p3.py

覆盖(计划 §六):
    - 日志上报: 脱敏引用红线(subjectRef 含个人标识
      拒绝)/因子校验(未知因子/非数值/超限)/score 区间/
      weightVersion 自动取当前
    - 通用重算公式: 因子×权重数学断言/漂移阈值边界
      (10 分)/版本变更但决策稳定/权重变更后漂移标记
    - 回放输出: 原分/重算分/漂移差/版本对比/中文归因
    - 45号申诉适配器: 已裁决导入/pending 跳过/幂等
    - 日志查询: 档案过滤/漂移标注/计数
    - 只读验证: 回放不修改档案
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


async def seed_registry():
    from services.ai_governance_service import (
        AiGovernanceService,
    )
    await AiGovernanceService().sync_registry()


def trust_factors(value: float = 50.0) -> list[dict]:
    from services.trust_scoring_service import (
        TrustValueScorer,
    )
    return [{"name": n, "value": value}
            for n in TrustValueScorer.WEIGHTS]


class TestSubmitLog:
    async def run(self):
        print("[01 日志上报]")
        reset_all()
        await seed_registry()
        from services.ai_governance_replay import (
            AiGovernanceReplayService,
        )
        svc = AiGovernanceReplayService()

        r = await svc.submit_log(
            "trust_value", "order:20260904-001",
            trust_factors(50.0), 50.0, action="grade")
        record("正常上报", r["success"] is True
               and r["replayId"] >= 1, str(r)[:60])

        # 脱敏红线
        for name, ref in (
                ("subjectRef含id=拒绝", "member:id=42"),
                ("subjectRef含phone拒绝",
                 "user:phone=138"),
        ):
            try:
                await svc.submit_log(
                    "trust_value", ref,
                    trust_factors(), 50.0)
                record(name, False, "未抛")
            except ValueError as e:
                record(name, "个人标识" in str(e), str(e))

        # subjectRef 长度
        try:
            await svc.submit_log(
                "trust_value", "x" * 101,
                trust_factors(), 50.0)
            record("subjectRef超长拒绝", False, "未抛")
        except ValueError:
            record("subjectRef超长拒绝", True)
        try:
            await svc.submit_log(
                "trust_value", "  ",
                trust_factors(), 50.0)
            record("subjectRef空拒绝", False, "未抛")
        except ValueError:
            record("subjectRef空拒绝", True)

        # 因子校验
        try:
            await svc.submit_log(
                "trust_value", "t:1",
                [{"name": "no_such_factor",
                  "value": 50}], 50.0)
            record("未知因子拒绝", False, "未抛")
        except ValueError as e:
            record("未知因子拒绝", "未知" in str(e),
                   str(e)[:50])
        try:
            await svc.submit_log(
                "trust_value", "t:1",
                [{"name": "legal_record",
                  "value": "abc"}], 50.0)
            record("因子非数值拒绝", False, "未抛")
        except ValueError:
            record("因子非数值拒绝", True)
        try:
            await svc.submit_log(
                "trust_value", "t:1", [], 50.0)
            record("空因子拒绝", False, "未抛")
        except ValueError:
            record("空因子拒绝", True)
        try:
            await svc.submit_log(
                "trust_value", "t:1",
                trust_factors() * 10, 50.0)
            record("因子超限拒绝", False, "未抛")
        except ValueError:
            record("因子超限拒绝", True)

        # score 校验
        for bad in (-1, 101, "abc"):
            try:
                await svc.submit_log(
                    "trust_value", "t:1",
                    trust_factors(), bad)
                record(f"score非法拒绝({bad})",
                       False, "未抛")
            except ValueError:
                record(f"score非法拒绝({bad})", True)

        # 未入册
        try:
            await svc.submit_log(
                "unknown_scorer", "t:1",
                trust_factors(), 50.0)
            record("未入册拒绝", False, "未抛")
        except KeyError:
            record("未入册拒绝", True)

        # weightVersion 自动取当前
        r = await svc.submit_log(
            "trust_value", "t:2", trust_factors(), 50.0)
        log = await svc.repo.get_replay_log(r["replayId"])
        record("weightVersion自动取当前",
               bool(log.get("weightVersion")),
               str(log.get("weightVersion")))


class TestReplayMath:
    async def run(self):
        print("[02 通用重算公式]")
        reset_all()
        await seed_registry()
        from services.ai_governance_replay import (
            AiGovernanceReplayService,
        )
        from services.trust_scoring_service import (
            TrustValueScorer,
        )
        from repositories.ai_learning_repository import (
            AiLearningRepository,
        )
        svc = AiGovernanceReplayService()
        w = TrustValueScorer.WEIGHTS
        factors = trust_factors(60.0)
        expected = round(sum(
            60.0 * w[f["name"]] for f in factors), 1)

        # 原分 = 重算分(权重未变) → 无漂移
        r = await svc.submit_log(
            "trust_value", "m:1", factors, expected)
        rep = await svc.replay(r["replayId"])
        record("重算数学断言(默认权重)",
               rep["rescored"] == expected
               and rep["delta"] == 0.0
               and rep["drifted"] is False,
               f"{rep['rescored']} vs {expected}")

        # 漂移边界: |diff| = 10 不漂移(>10 才漂)
        r = await svc.submit_log(
            "trust_value", "m:2", factors,
            round(expected - 10.0, 1))
        rep = await svc.replay(r["replayId"])
        record("漂移阈值边界10不漂",
               rep["delta"] == 10.0
               and rep["drifted"] is False,
               f"delta={rep['delta']}")

        # |diff| = 10.1 → 漂移
        r = await svc.submit_log(
            "trust_value", "m:3", factors,
            round(expected - 10.1, 1))
        rep = await svc.replay(r["replayId"])
        record("漂移阈值10.1漂移",
               rep["drifted"] is True
               and "决策漂移标记" in rep["attribution"],
               f"drifted={rep['drifted']}")

        # 权重变更后重算 → 漂移标记(版本对比)
        await AiLearningRepository().save_profile(
            "trust_value", {
                "champion": {
                    "version": "v9", "weights": {
                        k: round(v * 1.5, 4)
                        for k, v in w.items()},
                    "source": "manual",
                    "parentVersion": "v1",
                    "stats": {}, "note": "",
                    "createdAt": "2026-09-01T00:00:00"
                    "+00:00"}})
        r = await svc.submit_log(
            "trust_value", "m:4", factors, expected,
            weight_version="v1")
        rep = await svc.replay(r["replayId"])
        record("权重变更后漂移标记",
               rep["drifted"] is True
               and rep["versionChanged"] is True
               and rep["logVersion"] == "v1"
               and rep["currentVersion"] == "v9",
               f"drifted={rep['drifted']} "
               f"ver={rep['logVersion']}→"
               f"{rep['currentVersion']}")
        record("重算数学断言(新权重)",
               rep["rescored"] == round(expected * 1.5, 1),
               f"{rep['rescored']} vs "
               f"{round(expected * 1.5, 1)}")
        record("归因含主贡献因子",
               "主贡献因子" in rep["attribution"],
               rep["attribution"][:60])

        # 只读验证: 回放后档案不变
        profile = await AiLearningRepository(
        ).get_profile("trust_value")
        record("回放只读不修改档案",
               (profile.get("champion") or {})
               .get("version") == "v9",
               str((profile or {}).get("champion"))[:40])

        # 不存在日志
        try:
            await svc.replay(99999)
            record("不存在日志404", False, "未抛")
        except KeyError:
            record("不存在日志404", True)


class TestAppealAdapter:
    async def run(self):
        print("[03 45号申诉适配器]")
        reset_all()
        await seed_registry()
        from services.ai_governance_replay import (
            AiGovernanceReplayService,
        )
        from services.trust_scoring_service import (
            TrustValueScorer,
        )
        from repositories.trust_value_repository import (
            TrustValue45Repository,
        )
        svc = AiGovernanceReplayService()

        # 无申诉 → 0 导入
        r = await svc.import_trust45_appeals()
        record("无申诉导入0", r["imported"] == 0,
               str(r)[:60])

        # 灌已裁决申诉(upheld + overturned + pending)
        repo45 = TrustValue45Repository()
        snapshot = {n: 50.0
                    for n in TrustValueScorer.WEIGHTS}
        appeals = repo45.store.setdefault(
            "trust45_appeals", {})
        for i, status in enumerate(("upheld",
                                    "overturned",
                                    "pending")):
            appeals[i + 1] = {
                "appealId": i + 1, "trustId": 1,
                "eventId": 100 + i, "layer": "L1",
                "factor": "legal_record", "delta": -5.0,
                "scoreAtAppeal": 65.0,
                "factorSnapshot": dict(snapshot),
                "reason": f"r{i}", "status": status,
                "verdict": "", "reviewerNote": "",
                "appealFed": False,
                "createdAt": "2026-09-01T00:00:00+00:00",
                "decidedAt": ""}
        # 注入内存索引(内存模式 _list_appeals 走存储)
        r = await svc.import_trust45_appeals()
        record("已裁决导入2条(pending跳过)",
               r["imported"] == 2, str(r)[:60])
        logs = await svc.repo.list_replay_logs(
            scorer_id="trust_value", limit=10)
        record("申诉快照落日志", len(logs) == 2,
               str(len(logs)))
        refs = {l.get("subjectRef") for l in logs}
        record("subjectRef脱敏标识",
               refs == {"trust45:appeal:1",
                        "trust45:appeal:2"},
               str(refs))

        # 幂等
        r = await svc.import_trust45_appeals()
        record("适配器幂等", r["imported"] == 0,
               str(r)[:60])

        # 读取异常 fail-soft
        import services.trust_learning_service as tls
        orig = tls._list_appeals

        async def _boom(repo, status=None):
            raise RuntimeError("45号申诉存储瞬断")
        tls._list_appeals = _boom
        try:
            r = await svc.import_trust45_appeals()
            record("申诉读取fail-soft",
                   r["success"] is True
                   and "读取失败" in r.get("note", ""),
                   str(r)[:60])
        finally:
            tls._list_appeals = orig


class TestListLogs:
    async def run(self):
        print("[04 日志查询]")
        reset_all()
        await seed_registry()
        from services.ai_governance_replay import (
            AiGovernanceReplayService,
        )
        from services.ai_scoring_service import (
            OrderRiskScorer,
        )
        svc = AiGovernanceReplayService()
        await svc.submit_log("trust_value", "q:1",
                             trust_factors(60.0), 60.0)
        await svc.submit_log("trust_value", "q:2",
                             trust_factors(60.0), 95.0)
        # order_risk 全因子快照(权重和=1, 原分=60 无漂移)
        or_factors = [{"name": n, "value": 60.0}
                      for n in OrderRiskScorer.WEIGHTS]
        await svc.submit_log("order_risk", "q:3",
                             or_factors, 60.0)

        r = await svc.list_logs()
        record("全量查询3条", r["total"] == 3,
               str(r["total"]))
        record("漂移标注计数", r["driftedCount"] == 1,
               str(r["driftedCount"]))
        drifted = [l for l in r["logs"] if l["drifted"]]
        record("漂移标注正确",
               len(drifted) == 1
               and drifted[0]["subjectRef"] == "q:2",
               str(drifted)[:60])
        r = await svc.list_logs(
            scorer_id="trust_value")
        record("档案过滤2条", r["total"] == 2,
               str(r["total"]))
        record("查询含rescored字段",
               all("rescored" in l for l in r["logs"]),
               "缺 rescored")


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
        client.post("/api/ai-gov/registry/sync",
                    headers=admin)

        # 鉴权
        resp = client.post("/api/ai-gov/replay", json={
            "scorerId": "trust_value",
            "subjectRef": "h:1",
            "factors": trust_factors(),
            "score": 50.0})
        record("上报缺Role403", resp.status_code == 403,
               str(resp.status_code))
        resp = client.post("/api/ai-gov/replay/1")
        record("重放缺Role403", resp.status_code == 403,
               str(resp.status_code))
        resp = client.get("/api/ai-gov/replay")
        record("查询缺Role403", resp.status_code == 403,
               str(resp.status_code))

        # 上报 200
        resp = client.post("/api/ai-gov/replay", json={
            "scorerId": "trust_value",
            "subjectRef": "http:1",
            "factors": trust_factors(60.0),
            "score": 60.0, "action": "grade"},
            headers=admin)
        body = resp.json()
        record("上报200", resp.status_code == 200
               and body.get("replayId") >= 1,
               str(body)[:60])
        rid = body.get("replayId")

        # 重放 200
        resp = client.post(f"/api/ai-gov/replay/{rid}",
                           json={}, headers=admin)
        body = resp.json()
        record("重放200", resp.status_code == 200
               and body.get("rescored") == 60.0
               and body.get("drifted") is False,
               str(body)[:70])

        # 重放不存在 404
        resp = client.post("/api/ai-gov/replay/99999",
                           json={}, headers=admin)
        record("重放404", resp.status_code == 404,
               str(resp.status_code))

        # 上报脱敏 409
        resp = client.post("/api/ai-gov/replay", json={
            "scorerId": "trust_value",
            "subjectRef": "member:id=1",
            "factors": trust_factors(),
            "score": 50.0}, headers=admin)
        record("上报脱敏409", resp.status_code == 409,
               str(resp.status_code))

        # 查询 200
        resp = client.get("/api/ai-gov/replay"
                          "?scorerId=trust_value",
                          headers=admin)
        body = resp.json()
        record("查询200", resp.status_code == 200
               and body.get("total") == 1,
               str(body.get("total")))

        # P0/P1/P2 路由回归
        resp = client.get("/api/ai-gov/health",
                          headers=admin)
        record("P1健康回归", resp.status_code == 200,
               str(resp.status_code))
        resp = client.get("/api/ai-gov/fairness/report",
                          headers=admin)
        record("P2报告回归", resp.status_code == 200,
               str(resp.status_code))
        resp = client.get("/api/ai-gov/registry",
                          headers=admin)
        record("P0台账回归", resp.status_code == 200,
               str(resp.status_code))


async def run_all():
    await TestSubmitLog().run()
    await TestReplayMath().run()
    await TestAppealAdapter().run()
    await TestListLogs().run()
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
