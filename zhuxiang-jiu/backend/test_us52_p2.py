"""52号·小竹语音可用性评估引擎 P2 专项测试
(安全韧性评估: 一票否决域五指标)

运行方式:
    python test_us52_p2.py

覆盖(52号计划 §七 P2):
    - 注入抵御率: 49号红队 14 用例+51号红队
      12 用例复用真跑(breached=0 → 1.0)
    - 声纹伪造识别率: tts_spoof 模式
      (mock 声纹域 proxy 满分)
    - 降级合规率: fallback 审计无内部状态泄露
      (注入泄露样本 → 指标下降验证)
    - 预算耗尽引导率: 预算 fallback 含引导话术
    - 跨会话隔离率: consent 五类拒绝分布观测
    - veto 语义: 韧性指标<基线 → release-gate 拒
    - off 铁律 + 端点 + 零影响(宪法断言)
"""

import asyncio
import os
import sys
import uuid

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


async def seed_fallback_audits(with_leak: bool = False,
                               count: int = 3):
    """种子: fallback 审计(可选含内部泄露)"""
    from repositories.xiaozhu_repository import (
        Xiaozhu48Repository,
    )
    xrepo = Xiaozhu48Repository()
    suffix = uuid.uuid4().hex[:6]
    for i in range(count):
        fc_id = await xrepo._next_id(
            xrepo.TABLE_FC_AUDIT)
        error = ("隐私预算不足(剩余 0.00, 需 0.08)——"
                 "请在设置中调整隐私偏好或明日再试"
                 if not with_leak or i % 2 == 0
                 else f"Traceback: internal stack "
                      f"leak {suffix}")
        await xrepo.save_record(
            xrepo.TABLE_FC_AUDIT, {
                "fcId": fc_id, "memberId": 5300,
                "sessionId": 0, "action": "trust.convert",
                "toolName": "trust_convert",
                "tier": "sensitive",
                "consentTokenHash": "",
                "privacyCost": 0.08,
                "latencyMs": 10.0,
                "kind": "fallback",
                "error": error[:120],
                "ts": "2026-09-05T10:00:00",
            })


class TestResilienceMetrics:
    """01 安全韧性五指标"""

    async def run(self):
        print("[01 安全韧性五指标]")
        reset_all()
        from services.us52_service import (
            Us52MetricsService,
        )
        svc = Us52MetricsService()

        # off 态拒绝
        try:
            await svc.compute_resilience_metrics()
            ok, err = False, "未拒绝"
        except ValueError as e:
            ok, err = "off" in str(e), str(e)[:30]
        record("off 态计算拒绝", ok, err)

        os.environ["US52_MODE"] = "on"
        # 种子: 预算 fallback 审计(含引导话术)
        await seed_fallback_audits()

        r = await svc.compute_resilience_metrics()
        metrics = r.get("metrics") or {}
        record("五指标齐备",
               set(metrics) == {
                   "injection_defense_rate",
                   "voiceprint_spoof_rate",
                   "degrade_compliance_rate",
                   "budget_exhausted_guide_rate",
                   "session_isolation_rate"},
               str(list(metrics)))

        # 注入抵御(红队复用——两报告 breached=0)
        detail = r.get("detail") or {}
        inj = (detail.get("injection") or {})
        record("注入抵御=1.0(49+51 红队零突破)",
               metrics.get(
                   "injection_defense_rate") == 1.0
               and isinstance(
                   inj.get("v49"), dict)
               and inj["v49"].get("breached") == 0
               and isinstance(
                   inj.get("v51"), dict)
               and inj["v51"].get("breached") == 0,
               str(inj))

        # 降级合规(无泄露样本=1.0)
        record("降级合规=1.0(无泄露样本)",
               metrics.get(
                   "degrade_compliance_rate") == 1.0,
               str(metrics.get(
                   "degrade_compliance_rate")))

        # 预算耗尽引导(种子含"偏好/明日"话术)
        record("预算耗尽引导率(引导话术命中)",
               (metrics.get(
                   "budget_exhausted_guide_rate")
                or 0) >= 0.5,
               str(metrics.get(
                   "budget_exhausted_guide_rate")))

        # 声纹伪造(mock 域满分)
        record("声纹伪造识别=1.0(proxy 满分)",
               metrics.get(
                   "voiceprint_spoof_rate") == 1.0,
               str(metrics.get(
                   "voiceprint_spoof_rate")))

        # 跨会话隔离(consent 观测)
        record("跨会话隔离=1.0(观测分布)",
               metrics.get(
                   "session_isolation_rate") == 1.0,
               str(metrics.get(
                   "session_isolation_rate")))
        iso = detail.get("isolation") or {}
        record("consent 拒绝分布可观测",
               isinstance(iso, dict)
               and all(k in iso for k in (
                   "notFound", "expired", "used",
                   "crossUser", "actionMismatch")),
               str(iso)[:60])

        # 注入泄露样本 → 降级合规下降
        reset_all()
        await seed_fallback_audits(
            with_leak=True, count=4)
        r2 = await svc.compute_resilience_metrics()
        m2 = r2.get("metrics") or {}
        record("泄露样本 → 降级合规下降",
               (m2.get("degrade_compliance_rate")
                or 1.0) < 1.0
               and (m2.get(
                   "degrade_compliance_rate")
                   or 0) >= 0.5,
               str(m2.get(
                   "degrade_compliance_rate")))

        # veto 语义: 韧性指标接入 release-gate
        from services.us52_registry import decide
        gate = decide(m2)
        record("韧性未达 → veto(一票否决)",
               gate["decision"] in (
                   "veto", "mandatory")
               and isinstance(
                   gate.get("vetoFailed"), list),
               str(gate.get("decision")))

        # 指标接入快照
        snap = await svc.compute_snapshot(m2)
        record("韧性五指标接入决策快照",
               (snap.get("snapshot") or {})
               .get("sampleCount") == 5,
               str((snap.get("snapshot") or {})
                   .get("sampleCount")))
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
            "/api/us52/metrics/resilience",
            headers=admin)
        record("off 态 resilience 409",
               resp.status_code == 409,
               str(resp.status_code))

        os.environ["US52_MODE"] = "on"
        resp = client.post(
            "/api/us52/metrics/resilience",
            headers=admin)
        body = resp.json() or {}
        record("HTTP resilience 200(五指标)",
               resp.status_code == 200
               and len(body.get("metrics")
                       or {}) == 5,
               str(resp.status_code))
        record("veto 域标注(detail.note)",
               "veto" in str(
                   body.get("detail") or {}),
               str((body.get("detail")
                    or {}).get("note")))

        resp = client.post(
            "/api/us52/metrics/resilience")
        record("resilience 无 Role 403",
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
    await TestResilienceMetrics().run()
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
