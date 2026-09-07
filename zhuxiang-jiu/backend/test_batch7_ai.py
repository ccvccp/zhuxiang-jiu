"""全站批次七·全站融合收官 专项测试

运行方式:
    python test_batch7_ai.py

覆盖(《全站AI智能混合架构升级总计划》批次七):
    - 44号学习域全景(panorama: 55 评分器×模式分布×
      批次分布×健康度聚合)
    - 47号中枢统一调度(hub: 消费方注册表+tier 分布+
      45/44/46号三联动子系统+dispatch 四步调度)
    - 跨模块红队七向量(RT-X1~X7: 攻击链贯穿
      23/44/46/47/60/64号多模块)
    - 模式闸门+种子自清理+环境变量恢复
"""

import asyncio
import os
import sys

os.environ["LOCK_MODE"] = "asyncio"
os.environ["STORE_MODE"] = "asyncio"
os.environ.pop("LLM_API_KEY", None)
os.environ["LLM_ENABLED"] = "off"
os.environ["AI_ENFORCE_MODE"] = "observe"
os.environ["CROSS_RT_MODE"] = "on"

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


class TestPanorama:
    """01 44号学习域全景"""

    async def run(self):
        print("[01 44号学习域全景]")
        reset_all()
        from services.ai_learning_service import (
            SCORER_REGISTRY, panorama,
        )

        p = await panorama()
        record("全景评分器总数 56",
               p["scorerCount"] == 56
               == len(SCORER_REGISTRY),
               f"got {p['scorerCount']}")
        record("模式分布计数守恒",
               sum(p["modeDistribution"].values()) == 56,
               str(p["modeDistribution"]))
        record("默认全 observe",
               p["modeDistribution"]["observe"] == 56
               and p["modeDistribution"]["shadow"] == 0
               and p["modeDistribution"]["enforce"] == 0,
               str(p["modeDistribution"]))
        record("全局模式读环境变量",
               p["globalMode"] == "observe",
               p["globalMode"])
        record("scope 列表默认空",
               p["enforceScopes"] == [],
               str(p["enforceScopes"]))
        record("每评分器含 mode 字段",
               all("mode" in s and s["mode"] == "observe"
                   for s in p["scorers"]))
        record("批次分布含新范式区",
               "1" in p["batchDistribution"]
               and "39" in p["batchDistribution"],
               str(p["batchDistribution"]))
        record("健康度聚合字段齐备",
               all(k in p["health"] for k in (
                   "totalFeedback", "pendingFeedback",
                   "driftAlerts", "learnableScorers",
                   "schedulerRuns", "lastRunAt")))
        record("可学习档案 45/56",
               p["health"]["learnableScorers"] == 45,
               str(p["health"]["learnableScorers"]))

        # scope 边界: enforce+scopes → 分布变化
        os.environ["AI_ENFORCE_MODE"] = "enforce"
        os.environ["AI_ENFORCE_SCOPES"] = "order_risk"
        try:
            p2 = await panorama()
            record("scope 内 enforce",
                   p2["modeDistribution"]["enforce"] >= 1,
                   str(p2["modeDistribution"]))
            record("scope 外降级 shadow",
                   p2["modeDistribution"]["shadow"] >= 54,
                   str(p2["modeDistribution"]))
            record("scope 外不越权 enforce",
                   p2["modeDistribution"]["enforce"] == 1,
                   str(p2["modeDistribution"]))
            record("全局模式跟踪",
                   p2["globalMode"] == "enforce"
                   and p2["enforceScopes"] == ["order_risk"],
                   f"{p2['globalMode']}/{p2['enforceScopes']}")
        finally:
            os.environ["AI_ENFORCE_MODE"] = "observe"
            os.environ.pop("AI_ENFORCE_SCOPES", None)

        p3 = await panorama()
        record("模式恢复 observe",
               p3["modeDistribution"]["observe"] == 56,
               str(p3["modeDistribution"]))


class TestHub:
    """02 47号中枢统一调度"""

    async def run(self):
        print("[02 47号中枢统一调度]")
        reset_all()
        from services.trust_hub_service import (
            TrustHubService, CONSUMERS,
        )

        hub = TrustHubService()
        record("消费方注册表 13 项",
               len(CONSUMERS) == 13, str(len(CONSUMERS)))

        ov = await hub.hub_overview()
        record("总览消费方计数一致",
               ov["consumerCount"] == 13)
        channels = ov["consumerChannels"]
        record("三通道齐备",
               set(channels.keys()) == {
                   "sync_gate", "passive", "feedback"},
               str(channels))
        record("通道计数守恒",
               sum(c["count"] for c in channels.values())
               == 13, str(channels))
        record("五区块齐备",
               all(z in ov["zones"] for z in (
                   "tiers", "linkage45", "linkage44",
                   "linkage46", "consumers")),
               str(list(ov["zones"].keys())))
        record("区块零异常(fail-soft 不触发)",
               all("error" not in ov["zones"][z]
                   for z in ov["zones"]),
               str({z: ov["zones"][z] for z in ov["zones"]
                    if "error" in ov["zones"][z]}))
        record("44号联动评分器 56",
               ov["zones"]["linkage44"]["scorerCount"] == 56,
               str(ov["zones"]["linkage44"]))
        record("45号联动档案计数",
               isinstance(ov["zones"]["linkage45"]
                          .get("totalProfiles"), int))
        record("46号联动台账结构",
               "byStatus" in ov["zones"]["linkage46"])
        record("tier 分布区块结构",
               "distribution" in ov["zones"]["tiers"]
               and "totalProfiles"
               in ov["zones"]["tiers"])
        record("先验模式默认 off",
               ov["meta"]["priorMode"] is False)
        record("零自动处置红线标注",
               "画像不处罚" in ov["meta"]["redline"])

        # dispatch 四步调度
        d = await hub.dispatch()
        record("调度四步全成功",
               d["stepOk"] == d["stepTotal"] == 4,
               f"{d['stepOk']}/{d['stepTotal']}")
        record("调度步骤齐备",
               set(d["steps"].keys()) == {
                   "scan_tiers", "collusion_scan",
                   "fairness_bridge", "learning_summary"},
               str(list(d["steps"].keys())))
        record("调度零自动处置声明",
               "零自动处置" in d["note"])
        d2 = await hub.dispatch()
        record("调度幂等可重放",
               d2["stepOk"] == d2["stepTotal"] == 4)

        # 种子 watched 画像 dispatch 后不处置
        from services.trust_risk_profile_service import (
            TrustRiskProfileService,
        )
        from repositories.trust_risk_repository import (
            TrustRisk47Repository,
        )
        from repositories.trust_value_repository import (
            TrustValue45Repository,
        )
        tid = 9961
        await TrustValue45Repository().save_profile({
            "trustId": tid, "role": "person",
            "name": "rtx-9961", "idDigest": "rtx-9961",
            "factors": {}, "score": 500.0,
            "rawScore": 500.0, "grade": "C",
            "fused": False, "frozen": False,
            "createdAt": "2026-01-01T00:00:00",
            "updatedAt": "2026-01-01T00:00:00"})
        await TrustRisk47Repository().save_profile({
            "trustId": tid, "riskEMA": 0.55,
            "hitCounts": {}, "eventCount": 0,
            "calibrateOverride": "", "calibrateNote": "",
            "calibrateAt": "", "createdAt": "2026-01-01T00:00:00",
            "lastUpdated": "2026-01-01T00:00:00",
            "riskHistory": []})
        before = await TrustRiskProfileService() \
            .get_profile(tid)
        await hub.dispatch()
        after = await TrustRiskProfileService() \
            .get_profile(tid)
        record("watched 画像调度零处置",
               before["tier"] == "watched"
               and after["tier"] == "watched",
               f"{before['tier']}→{after['tier']}")
        await TrustRisk47Repository().save_profile({
            "trustId": tid, "riskEMA": 0.0,
            "hitCounts": {}, "eventCount": 0,
            "calibrateOverride": "", "calibrateNote": "",
            "calibrateAt": "", "createdAt": "2026-01-01T00:00:00",
            "lastUpdated": "2026-01-01T00:00:00",
            "riskHistory": []})


class TestRedteam:
    """03 跨模块红队七向量"""

    async def run(self):
        print("[03 跨模块红队七向量]")
        reset_all()
        from services.crossmodule_redteam_service import (
            CrossModuleRedteamService,
            RT_TRUST_BASE, require_active_mode,
        )

        rt = await CrossModuleRedteamService() \
            .run_all()
        record("七向量齐备",
               rt["total"] == 7
               and [v["vector"] for v in rt["vectors"]]
               == [f"RT-X{i}" for i in range(1, 8)],
               str([v["vector"] for v in rt["vectors"]]))
        record("红队全防住(7/7)",
               rt["allDefended"] is True
               and rt["defended"] == 7,
               f"{rt['defended']}/7")
        vec = {v["vector"]: v for v in rt["vectors"]}
        record("RT-X1 伪信用跨域注入防住",
               vec["RT-X1"]["defended"]
               and vec["RT-X1"]["evidence"]["tierAfter"]
               == "watched")
        record("RT-X2 学习域反馈伪造防住",
               vec["RT-X2"]["defended"]
               and vec["RT-X2"]["evidence"]
               ["unknownScorerRejected"])
        record("RT-X3 scope 越权防住",
               vec["RT-X3"]["defended"]
               and vec["RT-X3"]["evidence"]
               ["outScopeMode"] == "shadow")
        record("RT-X4 治理冻结旁路防住",
               vec["RT-X4"]["defended"]
               and "冻结" in str(
                   vec["RT-X4"]["evidence"]["message"]))
        record("RT-X5 tier 分裂读防住",
               vec["RT-X5"]["defended"]
               and vec["RT-X5"]["evidence"]["via64Tier"]
               == "watched")
        record("RT-X6 tier 字段注入防住",
               vec["RT-X6"]["defended"]
               and vec["RT-X6"]["evidence"]
               ["effectiveTier"] == "watched")
        record("RT-X7 中枢调度零处置防住",
               vec["RT-X7"]["defended"]
               and vec["RT-X7"]["evidence"]["tierAfter"]
               == "watched")
        record("环境变量恢复(mode)",
               os.environ.get("AI_ENFORCE_MODE")
               == "observe",
               os.environ.get("AI_ENFORCE_MODE"))
        record("环境变量恢复(scopes 清空)",
               os.environ.get("AI_ENFORCE_SCOPES") is None,
               os.environ.get("AI_ENFORCE_SCOPES"))
        record("隔离域 9971+ 不与 64/65号冲突",
               RT_TRUST_BASE == 9971
               and RT_TRUST_BASE not in (9801, 9881))

        # 种子自清理: 997x 画像全部复位(riskEMA=0)
        from repositories.trust_risk_repository import (
            TrustRisk47Repository,
        )
        residue = [
            p for p in await TrustRisk47Repository()
            .list_profiles(limit=500)
            if 9971 <= int(p.get("trustId") or 0) <= 9980
            and float(p.get("riskEMA") or 0) > 0
        ]
        record("红队种子自清理(画像复位)",
               len(residue) == 0, str(residue))

        # 治理档案恢复 active
        from repositories.ai_governance_repository import (
            AiGovernance46Repository,
        )
        gov = await AiGovernance46Repository() \
            .get_gov("ticket_quality")
        record("治理档案恢复非冻结",
               gov is None or gov.get("status") != "frozen",
               str(gov and gov.get("status")))


class TestModeGate:
    """04 模式闸门"""

    async def run(self):
        print("[04 模式闸门]")
        reset_all()
        from services.crossmodule_redteam_service import (
            require_active_mode, current_mode,
        )
        os.environ["CROSS_RT_MODE"] = "off"
        try:
            record("off 态模式可读",
                   current_mode() == "off")
            try:
                require_active_mode()
                rejected = False
            except ValueError:
                rejected = True
            record("off 态红队拒绝", rejected)
        finally:
            os.environ["CROSS_RT_MODE"] = "on"
        try:
            require_active_mode()
            allowed = True
        except ValueError:
            allowed = False
        record("on 态红队放行", allowed)


async def main():
    print("=" * 62)
    print("全站批次七·全站融合收官 专项测试")
    print("=" * 62)
    for cls in (TestPanorama, TestHub,
                TestRedteam, TestModeGate):
        await cls().run()
    print(f"\n{'=' * 62}")
    print(f"总计: {PASS} 通过 / {FAIL} 失败")
    print("=" * 62)
    for line in RESULTS:
        print(line)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
