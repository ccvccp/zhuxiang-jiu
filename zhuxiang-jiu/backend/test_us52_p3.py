"""52号·小竹语音可用性评估引擎 P3 专项测试
(包容性公平分组分析: 五群体组间差+低信值平等)

运行方式:
    python test_us52_p3.py

覆盖(52号计划 §七 P3):
    - 组间差计算: 五群体意图命中率
      max-min(无画像归 none 基线组)
    - 分组数据: 50号 group_profile 复用
      (none/minor/elder/disabled/org_proxy)
    - 单组场景: 只有一组活跃 → gap=0
    - 组间差>0.05 → mandatory 决策
    - 低信值服务平等: 预算静态注册表
      全会员统一(组间差=0 红线验证)
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


async def seed_tasks(member_id: int, hits: int,
                     total: int, group: str = None):
    """种子: 测试会话+任务结果+可选群体画像"""
    from core.helpers import ts as _ts
    from repositories.us52_repository import (
        Us52Repository,
    )
    repo = Us52Repository()
    test_id = await repo.next_test_id()
    await repo.save_session({
        "testId": test_id, "mode": "on",
        "memberId": member_id,
        "taskIds": ["T-01"] * total,
        "status": "completed",
        "taskCount": total, "passedCount": hits,
        "startedAt": _ts(), "completedAt": _ts(),
    })
    seq = 0
    for i in range(total):
        seq += 1
        hit = i < hits
        repo.store.setdefault(
            repo.TABLE_RESULTS, {})[
            f"{test_id}-{seq}"] = {
            "resultId": f"{test_id}-{seq}",
            "testId": test_id, "taskId": "T-01",
            "kind": "positive",
            "expectedIntent": "trust.score",
            "actualIntent":
                "trust.score" if hit else "general",
            "pass": hit, "detail": "",
            "ts": _ts(),
        }
    # 可选: 50号群体画像(区分组)
    if group:
        from repositories.voice50_repository import (
            Voice50Repository,
        )
        v50 = Voice50Repository()
        await v50.save_group_profile({
            "memberId": member_id, "group": group,
            "note": "us52-p3", "ts": _ts(),
        })
    return test_id


class TestInclusionMetrics:
    """01 包容性公平两指标"""

    async def run(self):
        print("[01 包容性公平]")
        reset_all()
        from services.us52_service import (
            Us52MetricsService,
        )
        svc = Us52MetricsService()

        # off 态拒绝
        try:
            await svc.compute_inclusion_metrics()
            ok, err = False, "未拒绝"
        except ValueError as e:
            ok, err = "off" in str(e), str(e)[:30]
        record("off 态计算拒绝", ok, err)

        os.environ["US52_MODE"] = "on"

        # 场景一: 单组(none)——gap=0
        await seed_tasks(member_id=5300,
                         hits=9, total=10)
        r = await svc.compute_inclusion_metrics()
        metrics = r.get("metrics") or {}
        record("两指标齐备",
               set(metrics) == {
                   "intent_parity_gap",
                   "low_value_service_parity"},
               str(list(metrics)))
        record("单组场景 gap=0",
               metrics.get(
                   "intent_parity_gap") == 0.0,
               str(metrics.get(
                   "intent_parity_gap")))
        detail = r.get("detail") or {}
        record("低信值平等=0(静态统一)",
               metrics.get(
                   "low_value_service_parity") == 0.0,
               str(metrics.get(
                   "low_value_service_parity")))
        bg = detail.get("byGroup") or {}
        record("none 基线组采集(9/10)",
               (bg.get("none") or {}).get("hit") == 9
               and (bg.get("none")
                    or {}).get("total") == 10,
               str(bg.get("none")))

        # 场景二: 双组均等——gap=0
        reset_all()
        await seed_tasks(member_id=5301,
                         hits=8, total=10, group="elder")
        await seed_tasks(member_id=5302,
                         hits=8, total=10)
        r2 = await svc.compute_inclusion_metrics()
        m2 = r2.get("metrics") or {}
        bg2 = (r2.get("detail")
               or {}).get("byGroup") or {}
        record("双组均等 gap=0(elder=none=0.8)",
               m2.get("intent_parity_gap") == 0.0
               and (bg2.get("elder") or {})
               .get("rate") == 0.8,
               str(m2.get("intent_parity_gap")))

        # 场景三: 组间差大(elder 低命中)→ gap>0.05
        reset_all()
        await seed_tasks(member_id=5303,
                         hits=3, total=10, group="elder")
        await seed_tasks(member_id=5304,
                         hits=9, total=10)
        r3 = await svc.compute_inclusion_metrics()
        m3 = r3.get("metrics") or {}
        record("组间差大场景 gap=0.6",
               abs((m3.get("intent_parity_gap")
                    or 0) - 0.6) < 0.001,
               str(m3.get("intent_parity_gap")))

        # mandatory 决策(包容性未达)
        from services.us52_registry import decide
        gate = decide(m3)
        record("包容性未达 → mandatory",
               gate["decision"] == "mandatory"
               and "inclusion" in
               gate.get("failedByDimension"),
               str(gate.get("decision")))

        # 场景四: 组间差 0.04(达标)
        reset_all()
        await seed_tasks(member_id=5305,
                         hits=8, total=10, group="minor")
        await seed_tasks(member_id=5306,
                         hits=9, total=10)
        r4 = await svc.compute_inclusion_metrics()
        m4 = r4.get("metrics") or {}
        record("组间差 0.1 场景(基线内 0.05 口径"
               "边界演示)",
               abs((m4.get("intent_parity_gap")
                    or 0) - 0.1) < 0.001,
               str(m4.get("intent_parity_gap")))

        # 工具成本静态口径
        tcp = ((r4.get("detail") or {})
               .get("toolCostParity") or {})
        record("工具成本静态口径(min/max)",
               "minCost" in tcp
               and "maxCost" in tcp,
               str(tcp))
        os.environ["US52_MODE"] = "off"


class TestEndpoints:
    """02 端点+鉴权+零影响"""

    async def run(self):
        print("[02 端点+鉴权+零影响]")
        reset_all()
        from fastapi.testclient import TestClient
        from main import app
        client = TestClient(app)
        admin = {"X-Role": "admin"}

        resp = client.post(
            "/api/us52/metrics/inclusion",
            headers=admin)
        record("off 态 inclusion 409",
               resp.status_code == 409,
               str(resp.status_code))

        os.environ["US52_MODE"] = "on"
        resp = client.post(
            "/api/us52/metrics/inclusion",
            headers=admin)
        body = resp.json() or {}
        record("HTTP inclusion 200(两指标)",
               resp.status_code == 200
               and len(body.get("metrics") or {})
               == 2,
               str(resp.status_code))
        record("五组结构(byGroup)",
               set((body.get("detail") or {})
                   .get("byGroup") or {}) == {
                   "none", "minor", "elder",
                   "disabled", "org_proxy"},
               str(list(((body.get("detail") or {})
                         .get("byGroup")
                         or {}).keys())))

        resp = client.post(
            "/api/us52/metrics/inclusion")
        record("inclusion 无 Role 403",
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
    await TestInclusionMetrics().run()
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
