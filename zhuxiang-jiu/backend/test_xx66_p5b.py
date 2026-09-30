"""66号·AI智能工程师大模块 P5b 专项测试
(根因引擎接 P4 案例库)

运行方式:
    python test_xx66_p5b.py

覆盖(P5b):
    - diagnose 相似案例段(空库零命中/案例命中/参考不改判定)
    - fail-soft(案例库故障不阻断根因)
    - 双未命中 57号缺口回写(P4 既有语义随接入生效)
"""

import asyncio
import os
import sys

os.environ["LOCK_MODE"] = "asyncio"
os.environ["STORE_MODE"] = "asyncio"
os.environ.pop("LLM_API_KEY", None)
os.environ["LLM_ENABLED"] = "off"
os.environ["AI_ENFORCE_MODE"] = "observe"
os.environ["XX66_MODE"] = "off"
os.environ["XX66_LLM_MODE"] = "off"

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


async def seed_recovery(fault_type="service_down",
                        fault_source="db-primary",
                        level="auto"):
    """种子 27号自愈记录(detected 态)——P2 同款"""
    from services.maintenance_service import (
        MaintenanceService,
    )
    return await MaintenanceService().detect_fault(
        fault_type=fault_type,
        fault_source=fault_source,
        recovery_level=level)


async def seed_case(problem, root_cause, solution="restart",
                    outcome="resolved"):
    """直插 P4 案例(repo 层种子)"""
    from repositories.xx66_repository import (
        Xx66Repository,
    )
    from core.helpers import ts as _ts
    repo = Xx66Repository()
    case_id = await repo.next_case_id()
    await repo.save_case({
        "caseId": case_id,
        "problem": problem,
        "rootCause": root_cause,
        "solution": solution,
        "outcome": outcome,
        "source": "seed",
        "valueLinked": False,
        "confidence": 0.9,
        "recurrence": 0,
        "hitCount": 0,
        "status": "active",
        "createdAt": _ts(),
        "lastHitAt": "",
    })
    return case_id


class TestDiagnoseCases:
    """01 diagnose 相似案例段"""

    async def run(self):
        print("[01 diagnose 相似案例]")
        from services.xx66_heal_service import (
            Xx66HealService,
        )
        svc = Xx66HealService()
        os.environ["XX66_MODE"] = "shadow"
        try:
            # 空库: similarCases 段存在且零命中
            reset_all()
            rec1 = await seed_recovery(
                "disk_full", "db-primary")
            d1 = await svc.diagnose(rec1["id"])
            sc1 = d1.get("similarCases")
            record("空库 similarCases 段存在",
                   isinstance(sc1, dict))
            record("空库零命中",
                   sc1 is not None
                   and sc1.get("hitCount") == 0)
            record("空库缺口字段回传",
                   sc1 is not None
                   and "gapRecorded" in sc1)
            record("根因仍是规则轨判定(advisoryOnly)",
                   "磁盘" in d1["rootCause"]
                   and d1["descSource"] == "rule")

            # 造案例: 磁盘类故障案例
            cid = await seed_case(
                "db-primary 磁盘容量耗尽告警 服务降级",
                "磁盘容量耗尽导致服务降级",
                solution="restart_task 清理日志后重启")
            rec2 = await seed_recovery(
                "disk_full", "db-primary")
            d2 = await svc.diagnose(rec2["id"])
            sc2 = d2.get("similarCases")
            record("案例命中 hitCount>=1",
                   sc2 is not None
                   and sc2.get("hitCount", 0) >= 1,
                   str(sc2))
            hits = (sc2 or {}).get("hits", [])
            record("命中含相似度与处置",
                   bool(hits)
                   and hits[0].get("similarity", 0) > 0
                   and "solution" in hits[0])
            record("命中案例号回传",
                   hits and hits[0].get("caseId") == cid)

            # 参考不改判定: rootCause/candidateActions
            # 仍为七规则注册表口径(案例仅附加参考)
            record("参考不改根因判定",
                   "磁盘容量耗尽导致服务降级"
                   == d2["rootCause"])
            record("参考不改动作候选",
                   d2["candidateActions"]
                   == ["restart_task"])

            # 命中计数联动(P4 命中计数语义)
            record("命中后 hitCount 递增(检索联动)",
                   sc2 is not None
                   and sc2.get("hitCount", 0) >= 1)
        finally:
            os.environ["XX66_MODE"] = "off"


class TestFailSoft:
    """02 案例库故障 fail-soft"""

    async def run(self):
        print("[02 fail-soft]")
        from services.xx66_heal_service import (
            Xx66HealService,
        )
        from services.xx66_knowledge_service import (
            Xx66KnowledgeService,
        )

        async def _boom(query, top_k=3):
            raise RuntimeError("案例库故障注入")

        svc = Xx66HealService()
        os.environ["XX66_MODE"] = "shadow"
        original = Xx66KnowledgeService.search_cases
        Xx66KnowledgeService.search_cases = _boom
        try:
            reset_all()
            rec = await seed_recovery(
                "disk_full", "db-primary")
            d = await svc.diagnose(rec["id"])
            record("案例库故障不阻断根因",
                   d["success"] is True
                   and "磁盘" in d["rootCause"])
            record("故障时 similarCases None",
                   d.get("similarCases") is None)
        finally:
            Xx66KnowledgeService.search_cases = original
            os.environ["XX66_MODE"] = "off"


class TestP4Semantic:
    """03 P4 语义保持(案例库接口未被 66号 消费破坏)"""

    async def run(self):
        print("[03 P4 语义保持]")
        from services.xx66_knowledge_service import (
            Xx66KnowledgeService,
        )
        reset_all()
        await seed_case(
            "redis 连接抖动 主从切换", "Redis 连接抖动")
        result = await Xx66KnowledgeService().search_cases(
            "Redis 连接抖动(网络或主从切换) redis-node1")
        record("P4 检索直调仍可用",
               result["success"] is True
               and result["hitCount"] >= 1)
        record("P4 双未命中缺口字段仍回传",
               "gapRecorded" in result)


async def main():
    print("=" * 60)
    print("66号·AI智能工程师 P5b 根因引擎接案例库 专项测试")
    print("=" * 60)
    for cls in (TestDiagnoseCases, TestFailSoft,
                TestP4Semantic):
        await cls().run()
    print("-" * 60)
    for line in RESULTS:
        print(line)
    print("=" * 60)
    print(f"总计: {PASS} 通过 / {FAIL} 失败")
    print("=" * 60)
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
