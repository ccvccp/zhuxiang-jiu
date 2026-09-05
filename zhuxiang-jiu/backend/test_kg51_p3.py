"""51号·小竹可信知识图谱 P3 专项测试
(溯源与验真联动: 溯源链+RepairAction+verified_with)

运行方式:
    python test_kg51_p3.py

覆盖(51号计划 §八 P3):
    - 溯源链完整: settled 事件→settlement→deposit→
      证据链全路径(完整率 100% QC)
    - trace/credit: factor 分组/deposit 视图/
      未绑定拒绝
    - trace/event: 事件链渲染/turn 段(语音来源)/
      互证对象(45号 sources)/事件不存在 404/
      他人事件越权 409
    - 采集扩展: deposit Evidence 实体/
      RepairAction 实体(repair+rejected)/
      verified_with 互证三元组(47号纯函数复用)
    - 端点+鉴权+零影响(45/50号宪法断言)

种子: 45号档案+绑定+50号 settled 事件+settlement
(带 depositId)+45号 repair 留痕+互证 deposit 对。
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
os.environ["KG_MODE"] = "off"

PASS = 0
FAIL = 0
RESULTS = []
MEMBER = 9201
OTHER = 9299


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
    from services import kg51_query_cache
    kg51_query_cache.invalidate_all()


async def seed_full_chain() -> dict:
    """种子: 45号档案+绑定+50号事件+结算+repair+互证"""
    from core.helpers import ts as _ts
    from repositories.trust_value_repository import (
        TrustValue45Repository,
    )
    from repositories.voice50_repository import (
        Voice50Repository,
    )
    from repositories.xiaozhu_repository import (
        Xiaozhu48Repository,
    )
    suffix = uuid.uuid4().hex[:8]

    # 45号档案×2(主+互证对端)
    profiles = TrustValue45Repository()
    p_store = profiles.store.setdefault(
        "trust45_profiles", {})
    p_seq = profiles.store.get("_trust45_seq", 0)
    trust_ids = []
    for i in range(2):
        p_seq += 1
        p_store[p_seq] = {
            "trustId": p_seq, "role": "person",
            "name": f"p3-{suffix}-{i}",
            "idDigest": f"digest-{suffix}-{i}",
            "factors": {}, "l1Severity": {},
            "score": 0.0, "rawScore": 0.0,
            "grade": "C", "fused": False,
            "fusedLevel": "general", "frozen": False,
            "createdAt": _ts(), "updatedAt": _ts(),
        }
        trust_ids.append(p_seq)
    profiles.store["_trust45_seq"] = p_seq
    main_tid, peer_tid = trust_ids

    # 绑定
    await Xiaozhu48Repository().save_binding({
        "memberId": MEMBER, "trustId": main_tid,
        "boundAt": _ts(), "note": "p3-test"})

    # 50号 settled 事件(settledBatchId 回写)
    v50 = Voice50Repository()
    ev_id = await v50.next_event_id()
    await v50.save_event({
        "evId": ev_id, "memberId": MEMBER,
        "sessionId": 0, "turnSeq": 0,
        "behavior": "voice_polite", "layer": "L2",
        "voiceFactor": "",
        "targetFactor": "ethics_evidence",
        "voiceprintMode": "proxy",
        "baseScore": 1.0, "multipliers": {},
        "finalScore": 1.0, "cappedScore": 1.0,
        "overflowScore": 0.0, "status": "settled",
        "ref": f"voice50:{ev_id}",
        "note": f"seed-p3-{suffix}",
        "dayKey": "2026-09-05", "ts": _ts(),
        "settledBatchId": 1,
    })

    # 45号 deposit 事件(depositId=eventId 同计数器)
    e_store = profiles.store.setdefault(
        "trust45_events", {})
    e_seq = profiles.store.get("_trust45_seq2", 0) \
        if False else len(e_store)
    e_store[ev_id] = {
        "eventId": ev_id, "trustId": main_tid,
        "layer": "L2", "factor": "ethics_evidence",
        "delta": 0.5, "severity": "general",
        "source": "deposit",
        "sources": ["voice50_engine",
                    f"trust:{peer_tid}"],
        "summary": f"[存证] p3({suffix})",
        "ts": _ts(),
    }
    # 45号 repair 留痕(repairId=eventId)
    repair_id = ev_id + 500
    e_store[repair_id] = {
        "eventId": repair_id, "trustId": main_tid,
        "layer": "L1", "factor": "legal_record",
        "delta": 0.3, "severity": "general",
        "source": "repair",
        "summary": "[修复留痕] violation=1",
        "ts": _ts(),
    }
    rejected_repair_id = repair_id + 1
    e_store[rejected_repair_id] = {
        "eventId": rejected_repair_id,
        "trustId": main_tid,
        "layer": "L1", "factor": "legal_record",
        "delta": 0.0, "severity": "general",
        "source": "repair_rejected",
        "summary": "[修复留痕] rejected",
        "ts": _ts(),
    }
    # 互证对端 deposit(双向引用→互证对)
    peer_dep_id = repair_id + 2
    e_store[peer_dep_id] = {
        "eventId": peer_dep_id, "trustId": peer_tid,
        "layer": "L2", "factor": "ethics_evidence",
        "delta": 0.4, "severity": "general",
        "source": "deposit",
        "sources": ["self", f"trust:{main_tid}"],
        "summary": "[存证] peer",
        "ts": _ts(),
    }

    # 50号 settlement(depositId 指向 45号事件)
    s_store = v50.store.setdefault(
        "voice50_settlement", {})
    s_store[1] = {
        "batchId": 1, "dayKey": "2026-09-05",
        "memberId": MEMBER, "layer": "L2",
        "factor": "ethics_evidence",
        "credits": 1.0, "eventCount": 1,
        "status": "done", "reason": "",
        "depositId": ev_id, "depositVerified": True,
        "depositDelta": 0.5,
        "evidence": f"evidence-{suffix}",
        "operator": "manual", "ts": _ts(),
    }

    # 采集(图锚定)
    os.environ["KG_MODE"] = "on"
    from services.kg51_ingest_service import (
        Kg51IngestService,
    )
    await Kg51IngestService().run_ingest()
    os.environ["KG_MODE"] = "off"
    return {"evId": ev_id, "trustId": main_tid,
            "repairId": repair_id,
            "peerDepId": peer_dep_id}


class TestTraceCredit:
    """01 trace/credit 溯源链"""

    async def run(self):
        print("[01 trace/credit 溯源链]")
        reset_all()
        ctx = await seed_full_chain()
        from services.kg51_trace_service import (
            Kg51TraceService,
        )
        svc = Kg51TraceService()

        r = await svc.trace_credit(MEMBER)
        record("溯源成功(factor 分组)",
               r["success"] is True
               and r["factorCount"] >= 1,
               str(r.get("factorCount")))
        entries = (r["factors"]
                   .get("ethics_evidence") or [])
        record("ethics_evidence 溯源条目",
               len(entries) >= 1, str(len(entries)))
        entry = entries[0] if entries else {}
        record("deposit 段(delta/sources)",
               (entry.get("deposit") or {})
               .get("delta") == 0.5
               and f"trust:" in str(
                   (entry.get("deposit")
                    or {}).get("sources")),
               str(entry.get("deposit")))
        record("settlement 段(batchId=1)",
               (entry.get("settlement") or {})
               .get("batchId") == 1,
               str(entry.get("settlement")))
        events = entry.get("events") or []
        record("events 段(1 条含证据链)",
               len(events) == 1
               and len((events[0]
                        or {}).get("evidence")
                       or []) >= 1,
               str(len(events)))
        comp = r.get("completeness") or {}
        record("溯源完整率 100%",
               comp.get("withSettlement", 0) >= 1
               and comp.get("completeness") == 1.0,
               str(comp))

        try:
            await svc.trace_credit(OTHER)
            ok, err = False, "未拒绝"
        except ValueError:
            ok, err = True, ""
        record("未绑定会员拒绝", ok, err)


class TestTraceEvent:
    """02 trace/event 事件证据链"""

    async def run(self):
        print("[02 trace/event 事件链]")
        reset_all()
        ctx = await seed_full_chain()
        from services.kg51_trace_service import (
            Kg51TraceService,
        )
        svc = Kg51TraceService()

        r = await svc.trace_event(ctx["evId"],
                                  member_id=MEMBER)
        record("事件链渲染成功",
               r["success"] is True
               and r["event"]["evId"] == ctx["evId"],
               str(r.get("event")))
        record("settlement 段可见",
               (r.get("settlement") or {})
               .get("batchId") == 1,
               str(r.get("settlement")))
        record("deposit 段(45号事件)",
               (r.get("deposit") or {})
               .get("depositId") == ctx["evId"],
               str(r.get("deposit")))
        record("证据链段(attested_by)",
               len(r.get("evidence") or []) >= 1,
               str(len(r.get("evidence") or [])))
        record("互证对象(trust: 引用)",
               len(r.get("mutualAttestations")
                   or []) >= 1,
               str(r.get("mutualAttestations")))
        record("全链齐备(note)",
               "全链齐备" in (r.get("note") or ""),
               str(r.get("note")))

        try:
            await svc.trace_event(ctx["evId"],
                                  member_id=OTHER)
            ok, err = False, "未拒绝"
        except ValueError:
            ok, err = True, ""
        record("他人事件越权拒绝", ok, err)

        try:
            await svc.trace_event(999999,
                                  member_id=MEMBER)
            ok, err = False, "未拒绝"
        except KeyError:
            ok, err = True, ""
        record("事件不存在 404", ok, err)


class TestTraceIngest:
    """03 采集扩展(Evidence/RepairAction/互证)"""

    async def run(self):
        print("[03 采集扩展]")
        reset_all()
        ctx = await seed_full_chain()
        from repositories.kg51_repository import (
            Kg51Repository,
        )
        from services.kg51_trace_service import (
            deposit_evidence_id,
        )
        repo = Kg51Repository()

        # deposit Evidence 实体
        dep_ev = await repo.get_entity(
            deposit_evidence_id(ctx["evId"]))
        record("deposit Evidence 实体入库",
               dep_ev is not None
               and dep_ev["attrs"].get("kind")
               == "trust45_deposit",
               str(dep_ev and dep_ev["entityId"]))

        # RepairAction 实体(applied+rejected)
        repairs = await repo.list_entities(
            entity_type="RepairAction", limit=100)
        by_status = {r["attrs"].get("status")
                     for r in repairs}
        record("RepairAction 实体(双轨)",
               len(repairs) == 2
               and by_status == {"applied",
                                 "rejected"},
               str(len(repairs)))

        # verified_with 互证三元组
        vw = await repo.list_triples(
            predicate="verified_with", limit=100)
        record("verified_with 互证三元组",
               len(vw) == 1
               and vw[0]["status"] == "verified",
               str(len(vw)))
        record("互证对象为 45号 deposit Evidence",
               vw and vw[0]["subject"].startswith(
                   "evid:sha256:")
               and vw[0]["object"].startswith(
                   "evid:sha256:"),
               str(vw[:1]))


class TestEndpoints:
    """04 端点+鉴权+零影响"""

    async def run(self):
        print("[04 端点+鉴权+零影响]")
        reset_all()
        ctx = await seed_full_chain()
        from fastapi.testclient import TestClient
        from main import app
        client = TestClient(app)
        member_h = {"X-Member-Id": str(MEMBER)}
        other_h = {"X-Member-Id": str(OTHER)}
        admin_h = {"X-Role": "admin"}

        resp = client.get("/api/kg51/trace/credit",
                          headers=member_h)
        body = resp.json() or {}
        record("HTTP trace/credit 200(会员自查)",
               resp.status_code == 200
               and body.get("factorCount", 0) >= 1,
               str(resp.status_code))

        resp = client.get("/api/kg51/trace/credit")
        record("trace/credit 无身份 401",
               resp.status_code == 401,
               str(resp.status_code))

        resp = client.get(
            "/api/kg51/trace/credit"
            f"?member_id={MEMBER}", headers=admin_h)
        record("trace/credit admin 指定会员 200",
               resp.status_code == 200,
               str(resp.status_code))

        resp = client.get(
            f"/api/kg51/trace/event/{ctx['evId']}",
            headers=member_h)
        record("HTTP trace/event 200(属主)",
               resp.status_code == 200
               and (resp.json()
                    or {}).get("success") is True,
               str(resp.status_code))

        resp = client.get(
            f"/api/kg51/trace/event/{ctx['evId']}",
            headers=other_h)
        record("trace/event 他人 409",
               resp.status_code == 409,
               str(resp.status_code))

        resp = client.get(
            "/api/kg51/trace/event/999999",
            headers=admin_h)
        record("trace/event 不存在 404",
               resp.status_code == 404,
               str(resp.status_code))

        # 零影响: 宪法断言
        from services.trust_scoring_service import (
            TrustValueScorer,
        )
        from services.xiaozhu_voice50_rules import (
            VOICE_RULES,
        )
        record("45号九因子零改动",
               len(TrustValueScorer.LAYER_OF) == 9)
        record("50号14行为零改动",
               len(VOICE_RULES) == 14)
        os.environ["KG_MODE"] = "off"


async def run_all():
    await TestTraceCredit().run()
    await TestTraceEvent().run()
    await TestTraceIngest().run()
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
