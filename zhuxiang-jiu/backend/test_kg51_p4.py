"""51号·小竹可信知识图谱 P4 专项测试
(治理与演进: 巡检+版本+公平桥+反馈+看板+机构采集)

运行方式:
    python test_kg51_p4.py

覆盖(51号计划 §八 P4):
    - 巡检三指标: 完整性/一致性/时效性结构+
      issues 留痕+latest 触发
    - 版本快照: 统计五元组+分组分布+只追加回溯
    - 公平桥: side-door 档案入册46号+
      三组 verified 占比+不足组不上报
    - 反馈闭环: 提交→reviews(feedback)入队+
      reviewId 回填+参数校验+重复 target 不重复入队
    - 看板: 五分区结构+预算分区(49号只读)
    - 机构采集: Institution 实体(16号 agents+
      21号 partners, 仅 active)
    - 调度器: off 默认/start 幂等/run_scheduled
    - 端点+鉴权+零影响(45/50号宪法断言)
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
os.environ["KG_INSPECT_MODE"] = "off"

PASS = 0
FAIL = 0
RESULTS = []
MEMBER = 9401


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


async def seed_graph() -> dict:
    """种子: 50号事件 → 采集(含三源+trace+institution)"""
    from core.helpers import ts as _ts
    from repositories.voice50_repository import (
        Voice50Repository,
    )
    from repositories.agent_repository import (
        AgentRepository,
    )
    from repositories.venue_repository import (
        VenueRepository,
    )
    suffix = uuid.uuid4().hex[:8]
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
        "note": f"seed-p4-{suffix}",
        "dayKey": "2026-09-05", "ts": _ts(),
        "settledBatchId": 0,
    })
    # 第二事件(L3——公平桥 system 组 ≥5 实体)
    ev_id2 = await v50.next_event_id()
    await v50.save_event({
        "evId": ev_id2, "memberId": MEMBER,
        "sessionId": 0, "turnSeq": 0,
        "behavior": "voice_community_qa", "layer": "L3",
        "voiceFactor": "",
        "targetFactor": "longtail_good",
        "voiceprintMode": "proxy",
        "baseScore": 2.0, "multipliers": {},
        "finalScore": 2.0, "cappedScore": 2.0,
        "overflowScore": 0.0, "status": "settled",
        "ref": f"voice50:{ev_id2}",
        "note": f"seed-p4b-{suffix}",
        "dayKey": "2026-09-05", "ts": _ts(),
        "settledBatchId": 0,
    })
    # 16号代理商种子(active+suspended 各一)
    arepo = AgentRepository()
    agents = arepo.store.setdefault("agents", {})
    seq = arepo.store.get("_agent_seq", 0)
    my_agent_ids = []
    for status, name in (("active", "活跃代理"),
                        ("suspended", "停用代理")):
        seq += 1
        my_agent_ids.append(seq)
        agents[seq] = {
            "id": seq, "name": name, "level": "B",
            "wallet": 0.0, "status": status,
            "contact_name": "", "contact_phone": "",
            "region": "华南", "address": "",
            "created_at": _ts(), "updated_at": _ts(),
            "total_sales": 0.0, "total_purchases": 0.0,
        }
    arepo.store["_agent_seq"] = seq
    # 21号合作商种子(active 一家)
    vrepo = VenueRepository()
    partners = vrepo.store.setdefault(
        "venue_partners", {})
    p_seq = vrepo.store.get("_venue_partner_seq", 0) \
        if "_venue_partner_seq" in vrepo.store else 0
    p_seq += 1
    partners[p_seq] = {
        "id": p_seq, "partnerType": "hotel",
        "partnerName": f"酒店{suffix}",
        "creditCode": "", "legalPerson": "",
        "contactPhone": "", "contactAddress": "",
        "longitude": 0.0, "latitude": 0.0,
        "starLevel": 4, "partnerLevel": "B",
        "agentId": 0, "supplyMode": "direct",
        "svipPriceUsed": True, "tastingRate": 0.0,
        "paylaterQuota": 0.0, "status": "active",
        "contractStart": "", "contractEnd": "",
        "blockchainHash": "", "statusHistory": [],
        "levelHistory": [],
        "createdAt": _ts(), "updatedAt": _ts(),
    }
    vrepo.store["_venue_partner_seq"] = p_seq
    os.environ["KG_MODE"] = "on"
    from services.kg51_ingest_service import (
        Kg51IngestService,
    )
    await Kg51IngestService().run_ingest()
    os.environ["KG_MODE"] = "off"
    return {"evId": ev_id,
            "myAgentActive": my_agent_ids[0],
            "myAgentSuspended": my_agent_ids[1]}


class TestInspection:
    """01 巡检三指标"""

    async def run(self):
        print("[01 巡检三指标]")
        reset_all()
        await seed_graph()
        from services.kg51_governance_service import (
            Kg51GovernanceService,
        )
        svc = Kg51GovernanceService()

        r = await svc.run_inspection()
        record("巡检执行(inspectionId=1)",
               r.get("inspectionId") == 1
               and r.get("entityCount", 0) > 0,
               str(r.get("inspectionId")))
        for metric in ("completeness", "consistency",
                       "freshness"):
            v = r.get(metric)
            record(f"三指标结构({metric} 0-1)",
                   isinstance(v, (int, float))
                   and 0.0 <= v <= 1.0, str(v))
        record("issues 留痕(list)",
               isinstance(r.get("issues"), list))

        r2 = await svc.latest_inspection()
        record("latest 巡检(命中缓存不新增)",
               (r2.get("inspection")
                or {}).get("inspectionId") == 1,
               str(r2.get("inspection")
                   and r2["inspection"]
                   .get("inspectionId")))

        # 二次巡检(只追加)
        r3 = await svc.run_inspection()
        record("巡检只追加(inspectionId=2)",
               r3.get("inspectionId") == 2,
               str(r3.get("inspectionId")))


class TestVersion:
    """02 版本快照"""

    async def run(self):
        print("[02 版本快照]")
        reset_all()
        await seed_graph()
        from services.kg51_governance_service import (
            Kg51GovernanceService,
        )
        svc = Kg51GovernanceService()

        r = await svc.snapshot_version(label="测试版")
        v = r.get("version") or {}
        record("快照执行(versionId=1+label)",
               v.get("versionId") == 1
               and v.get("versionLabel") == "测试版",
               str(v.get("versionId")))
        record("统计五元组(entity/triple/verified/"
               "unverified/retired)",
               all(k in v for k in
                   ("entityCount", "tripleCount",
                    "verifiedCount", "unverifiedCount",
                    "retiredCount")),
               str(list(v.keys())[:6]))
        record("verifiedRatio 口径",
               0.0 <= v.get("verifiedRatio", 0) <= 1.0)
        record("分组分布(bySourceType/byEntityType)",
               isinstance(v.get("bySourceType"), dict)
               and isinstance(v.get("byEntityType"),
                              dict))

        r2 = await svc.list_versions()
        versions = r2.get("versions") or []
        record("版本回溯(最新在前)",
               len(versions) == 1
               and versions[0].get("versionId") == 1,
               str(len(versions)))
        await svc.snapshot_version()
        r3 = await svc.list_versions()
        versions3 = r3.get("versions") or []
        record("快照只追加(v2 默认标签)",
               len(versions3) == 2
               and versions3[0].get("versionId") == 2
               and versions3[0].get("versionLabel")
               == "v2",
               str([x.get("versionLabel")
                    for x in versions3]))


class TestFairnessBridge:
    """03 公平桥→46号"""

    async def run(self):
        print("[03 公平桥]")
        reset_all()
        await seed_graph()
        from services.kg51_governance_service import (
            Kg51GovernanceService,
        )
        from repositories.ai_governance_repository \
            import AiGovernance46Repository

        svc = Kg51GovernanceService()
        r = await svc.bridge_fairness()
        record("公平桥上报(bridged=2 组: "
               "system+authority, user 不足)",
               r.get("success") is True
               and r.get("bridged") == 2
               and set(r.get("groups") or [])
               == {"authority", "system"},
               str(r))

        gov = await AiGovernance46Repository().get_gov(
            "kg51_verified_coverage")
        record("side-door 档案入册46号",
               gov is not None
               and gov.get("module")
               == "51可信知识图谱",
               str(gov and gov.get("scorerId")))

        samples = await (
            AiGovernance46Repository()
        ).list_samples("kg51_verified_coverage",
                       limit=100)
        record("采样落库(2 组, 不足组未上报)",
               len(samples) == 2
               and {s.get("group") for s in samples}
               == {"authority", "system"},
               str(len(samples)))

        # 空图: 不足组不上报
        from repositories.store import reset_store
        reset_store()
        r2 = await svc.bridge_fairness()
        record("空图不足组不上报(bridged=0)",
               r2.get("bridged") == 0
               and "暂不上报" in (r2.get("note")
                                   or ""),
               str(r2))


class TestFeedback:
    """04 反馈闭环(48号→修订队列)"""

    async def run(self):
        print("[04 反馈闭环]")
        reset_all()
        await seed_graph()
        from services.kg51_governance_service import (
            Kg51GovernanceService,
        )
        from repositories.kg51_repository import (
            Kg51Repository,
        )
        svc = Kg51GovernanceService()
        repo = Kg51Repository()

        target = (f"ev:voice50:1|attested_by|"
                  f"evid:sha256:abc")
        r = await svc.submit_feedback(
            member_id=MEMBER, turn_id="t-abc12345",
            target_triple=target,
            note="证据链指向有误")
        record("反馈提交(feedbackId=1)",
               r.get("feedbackId") == 1
               and r.get("status") == "pending",
               str(r))
        record("修订队列入队(reviewId 回填)",
               (r.get("reviewId") or 0) >= 1,
               str(r.get("reviewId")))

        pending = await repo.list_reviews(
            status="pending", reason="feedback",
            limit=100)
        record("reviews(feedback) 可见",
               len(pending) == 1
               and pending[0].get("target")
               == target,
               str(len(pending)))

        fb = await repo.list_feedback(limit=100)
        record("反馈台账(fromTurnId 留痕)",
               len(fb) == 1
               and fb[0].get("fromTurnId")
               == "t-abc12345",
               str(fb[:1]))

        # 重复 target 不重复入队
        r2 = await svc.submit_feedback(
            member_id=MEMBER, turn_id="t-def67890",
            target_triple=target)
        pending2 = await repo.list_reviews(
            status="pending", reason="feedback",
            limit=100)
        record("重复 target 不重复入队",
               len(pending2) == 1,
               str(len(pending2)))

        # 参数校验
        for bad_turn, bad_target in (
                ("", target),
                ("bad-prefix", target),
                ("t-ok123456", "invalid")):
            try:
                await svc.submit_feedback(
                    member_id=MEMBER,
                    turn_id=bad_turn,
                    target_triple=bad_target)
                ok, err = False, "未拒绝"
            except ValueError:
                ok, err = True, ""
            label = bad_turn[:8] or "空"
            record(f"参数校验拒绝({label}|"
                   f"{bad_target[:8]})", ok, err)


class TestDashboard:
    """05 看板五分区"""

    async def run(self):
        print("[05 看板]")
        reset_all()
        await seed_graph()
        from services.kg51_governance_service import (
            Kg51GovernanceService,
        )
        svc = Kg51GovernanceService()

        r = await svc.dashboard()
        record("看板五分区结构",
               all(k in r for k in
                   ("scale", "verified",
                    "reviewBacklog", "budget",
                    "versions")),
               str(list(r.keys())))
        record("规模分区(实体/三元组>0)",
               (r["scale"].get("entityCount") or 0)
               > 0
               and (r["scale"].get("tripleCount")
                    or 0) > 0,
               str(r["scale"]))
        record("verified 占比分区",
               0.0 <= (r["verified"]
                       .get("verifiedRatio")
                       or 0) <= 1.0)
        record("复核积压分区(byReason)",
               isinstance(
                   (r["reviewBacklog"]
                    or {}).get("byReason"), dict))
        record("预算分区(49号只读聚合)",
               "accounts" in (r.get("budget")
                              or {}),
               str(r.get("budget")))


class TestInstitution:
    """06 机构采集(Institution)"""

    async def run(self):
        print("[06 机构采集]")
        reset_all()
        ctx = await seed_graph()
        from repositories.kg51_repository import (
            Kg51Repository,
        )
        repo = Kg51Repository()

        institutions = await repo.list_entities(
            entity_type="Institution", limit=100)
        org_ids = {i["attrs"].get("orgId")
                   for i in institutions}
        record("Institution 实体入库"
               "(种子 active 代理+酒店)",
               f"agent-{ctx['myAgentActive']}"
               in org_ids
               and "venue-1" in org_ids,
               str(org_ids))
        record("suspended 代理不入库",
               f"agent-{ctx['myAgentSuspended']}"
               not in org_ids,
               str(org_ids))
        record("权威源 confidence=1.0",
               all(i["confidence"] == 1.0
                   for i in institutions))


class TestScheduler:
    """07 调度器"""

    async def run(self):
        print("[07 调度器]")
        reset_all()
        await seed_graph()
        from services import kg51_scheduler as sched

        record("默认 off(start 返回 False)",
               sched.start_scheduler() is False)
        record("off 态零 task",
               sched.scheduler_running() is False)

        stats = await sched.run_scheduled_inspection()
        record("手动单轮巡检(runs=1)",
               stats.get("runs") == 1
               and "lastInspection" in stats,
               str(stats))

        from repositories.kg51_repository import (
            Kg51Repository,
        )
        saved = await Kg51Repository(
        ).get_scheduler_stats()
        record("调度留痕(stats 落库)",
               saved is not None
               and saved.get("runs") == 1,
               str(saved and saved.get("runs")))


class TestEndpoints:
    """08 端点+鉴权+零影响"""

    async def run(self):
        print("[08 端点+鉴权+零影响]")
        reset_all()
        await seed_graph()
        from fastapi.testclient import TestClient
        from main import app
        client = TestClient(app)
        admin = {"X-Role": "admin"}
        member_h = {"X-Member-Id": str(MEMBER)}

        resp = client.post("/api/kg51/inspect/run",
                           headers=admin)
        record("HTTP 巡检触发 200",
               resp.status_code == 200
               and ((resp.json() or {})
                    .get("inspection")
                    or {}).get("inspectionId") == 1,
               str(resp.status_code))

        resp = client.get("/api/kg51/inspect/latest",
                          headers=admin)
        record("HTTP 最近巡检 200",
               resp.status_code == 200,
               str(resp.status_code))

        resp = client.post("/api/kg51/versions/snapshot",
                           headers=admin, json={})
        record("HTTP 版本快照 200",
               resp.status_code == 200
               and ((resp.json() or {})
                    .get("version")
                    or {}).get("versionId") == 1,
               str(resp.status_code))

        resp = client.get("/api/kg51/versions",
                          headers=admin)
        record("HTTP 版本回溯 200(1 条)",
               resp.status_code == 200
               and (resp.json() or {}).get("total")
               == 1,
               str(resp.status_code))

        resp = client.post("/api/kg51/fairness-bridge",
                           headers=admin)
        record("HTTP 公平桥 200(2 组)",
               resp.status_code == 200
               and (resp.json()
                    or {}).get("bridged") == 2,
               str(resp.status_code))

        resp = client.post("/api/kg51/feedback",
                           headers=member_h,
                           json={"turnId": "t-abc12345",
                                 "targetTriple":
                                     "a|b|c",
                                 "note": "测试"})
        record("HTTP 反馈提交 200",
               resp.status_code == 200
               and (resp.json()
                    or {}).get("feedbackId") == 1,
               str(resp.status_code))

        resp = client.get("/api/kg51/feedback",
                          headers=admin)
        record("HTTP 反馈台账 200",
               resp.status_code == 200,
               str(resp.status_code))

        resp = client.get("/api/kg51/dashboard",
                          headers=admin)
        record("HTTP 看板 200(五分区)",
               resp.status_code == 200
               and "budget" in (resp.json()
                                or {}),
               str(resp.status_code))

        # 鉴权
        for method, path in (
                ("POST", "/api/kg51/inspect/run"),
                ("GET", "/api/kg51/dashboard"),
                ("POST", "/api/kg51/feedback")):
            kwargs = ({"json": {"turnId": "t-x",
                                "targetTriple": "a|b|c"}}
                      if method == "POST"
                      and "feedback" in path else {})
            if method == "POST" \
                    and "feedback" not in path:
                kwargs = {}
            resp = client.request(method, path,
                                  **kwargs)
            record(f"{path.split('/')[-1]} 无鉴权"
                   f"401/403",
                   resp.status_code in (401, 403),
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
        os.environ["KG_INSPECT_MODE"] = "off"


async def run_all():
    await TestInspection().run()
    await TestVersion().run()
    await TestFairnessBridge().run()
    await TestFeedback().run()
    await TestDashboard().run()
    await TestInstitution().run()
    await TestScheduler().run()
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
