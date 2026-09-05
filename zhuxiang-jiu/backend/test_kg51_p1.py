"""51号·小竹可信知识图谱 P1 专项测试
(证据链采集与抽取: 三源管道+去重+冲突+复核)

运行方式:
    python test_kg51_p1.py

覆盖(51号计划 §八 P1):
    - 三源采集: 系统源(50号 events)实体+四类三元组/
      权威源(published 条款+产品)/用户源(48号 turns 候选)
    - 14 行为映射: L2/L3 有 contributes_to_credit,
      L1 不映射(50号红线)
    - 证据链: attested_by 强制/evidence_bundle 结构
    - 置信分级: settled 0.98 verified/pending 0.85
      unverified/user 0.6 unverified
    - 幂等: 重复采集 skipped/实体首次为准
    - 状态迁移: pending→settled 重采置信升级
    - 冲突解决: 单值谓词权威>系统>用户
    - unverified 物理隔离(verified 视图零泄漏)
    - 复核流: confidence 队列/approve→verified/
      reject→retired/重复裁决拒绝
    - off 铁律: ingest 拒绝/观测不受影响
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
os.environ["KG_MODE"] = "off"

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


_SEED = {}


async def seed_system_events() -> dict:
    """种子: 50号事件 settled L2/settled L1/pending L3"""
    from repositories.voice50_repository import (
        Voice50Repository,
    )
    repo = Voice50Repository()
    suffix = uuid.uuid4().hex[:8]
    members = [9001, 9002, 9003]
    events = []
    specs = [
        # (memberId, behavior, layer, targetFactor, status)
        (9001, "voice_polite", "L2", "ethics_evidence",
         "settled"),
        (9002, "voice_login", "L1", "", "settled"),
        (9003, "voice_community_qa", "L3",
         "longtail_good", "pending"),
    ]
    for i, (mid, behavior, layer, factor, status) in \
            enumerate(specs, start=1):
        ev_id = await repo.next_event_id()
        record_ = {
            "evId": ev_id, "memberId": mid,
            "sessionId": 0, "turnSeq": 0,
            "behavior": behavior, "layer": layer,
            "voiceFactor": "voice_login" if layer == "L1"
            else "",
            "targetFactor": factor,
            "voiceprintMode": "proxy",
            "baseScore": 1.0, "multipliers": {},
            "finalScore": 1.0, "cappedScore": 1.0,
            "overflowScore": 0.0,
            "status": status,
            "ref": f"voice50:{ev_id}",
            "note": f"seed-{suffix}-{i}",
            "dayKey": "2026-09-05", "ts": "2026-09-05T10:00:00",
        }
        if status == "settled":
            record_["settledBatchId"] = 77
        await repo.save_event(record_)
        events.append(record_)
    _SEED["events"] = events
    _SEED["members"] = members
    return {"events": events, "members": members}


async def _last_seed_events() -> list:
    return _SEED.get("events") or []


async def seed_authority() -> int:
    """种子: 18号条款(published + draft 各一)"""
    from repositories.agreement_repository import (
        AgreementRepository,
    )
    from core.helpers import ts as _ts
    repo = AgreementRepository()
    agreements = repo.store.setdefault("agreements", {})
    seq = repo.store.get("_agreement_seq", 0)
    last_id = 0
    for status, no in (("published", "T-KG51-PUB"),
                      ("draft", "T-KG51-DRF")):
        seq += 1
        agreements[seq] = {
            "id": seq,
            "agreementNo": no,
            "name": f"条款{no}",
            "type": "term", "applicableRole": "member",
            "legalBasis": "", "currentVersion": "v1.0",
            "content": "", "changeLog": "",
            "status": status, "effectiveDate": None,
            "versionHistory": [],
            "createdAt": _ts(), "updatedAt": _ts(),
        }
        last_id = seq
    repo.store["_agreement_seq"] = seq
    return last_id


async def seed_user_turns() -> str:
    """种子: 48号有效意图轮次 + 噪音轮次"""
    from repositories.xiaozhu_repository import (
        Xiaozhu48Repository,
    )
    repo = Xiaozhu48Repository()
    session_id = repo.store.get("_kg51_test_session", 0) + 1
    repo.store["_kg51_test_session"] = session_id
    for seq, intent in ((1, "product.query"),
                        (2, "not_woken"),
                        (3, "asr_failed")):
        turn_id = f"t-{uuid.uuid4().hex[:8]}"
        await repo.save_turn({
            "turnId": turn_id, "sessionId": session_id,
            "seq": seq, "channel": "voice",
            "audioMeta": {}, "rawText": "测试话语(已脱敏)",
            "wake": True, "intent": intent,
            "action": None, "reply": "测试回复",
            "card": {}, "jump": None, "latencyMs": 100.0,
            "ts": "2026-09-05T10:00:00",
        })
    return f"answer:voice48:t-{session_id}"


class TestSystemSource:
    """01 系统源采集(50号 events)"""

    async def run(self):
        print("[01 系统源采集]")
        reset_all()
        await seed_system_events()
        from repositories.kg51_repository import (
            Kg51Repository,
        )
        from services.kg51_ingest_service import (
            Kg51IngestService, member_digest,
            evidence_digest,
        )
        repo = Kg51Repository()
        os.environ["KG_MODE"] = "on"
        svc = Kg51IngestService()
        report = await svc.run_ingest(sources=["system"])
        sys_stat = report["sources"]["system"]
        record("采集统计(scanned=3)",
               sys_stat["scanned"] == 3,
               str(sys_stat))

        # 实体: 3 事件 + 3 会员 + 3 证据 + 2 因子 = 11
        entities = await repo.list_entities(limit=1000)
        record("实体数(11: 3ev+3member+3evid+2factor)",
               len(entities) == 11, str(len(entities)))

        # 会员 digest-only: 不含 memberId 明文
        m = await repo.get_entity(
            f"member:sha256:{member_digest(9001)}")
        record("Member 实体 digest 标识",
               m is not None
               and m["attrs"].get("digest")
               == member_digest(9001)
               and "9001" not in m["entityId"],
               str(m and m["entityId"]))

        # 证据链: attested_by 三元组带 evidence_bundle
        triples = await repo.list_triples(limit=1000)
        attested = [t for t in triples
                    if t["predicate"] == "attested_by"]
        record("attested_by 三元组(每事件一条)",
               len(attested) == 3, str(len(attested)))
        ev_ok = all(
            t.get("evidence", {}).get("verifier")
            in ("system", "settle")
            and t.get("evidence", {}).get("sourceRef")
            for t in attested)
        record("evidence_bundle 结构(verifier+sourceRef)",
               ev_ok,
               str(attested[0]["evidence"]
                   if attested else None))

        # L2/L3 映射 / L1 不映射
        contributes = [t for t in triples
                       if t["predicate"]
                       == "contributes_to_credit"]
        record("contributes_to_credit 仅 L2/L3(2 条)",
               len(contributes) == 2,
               str(len(contributes)))
        l1_subjects = {f"ev:voice50:{e['evId']}"
                       for e in (await
                                 _last_seed_events())
                       if e["layer"] == "L1"}
        record("L1 不映射 45号因子(红线)",
               all(not (t["subject"] in l1_subjects
                        and t["predicate"]
                        == "contributes_to_credit")
                   for t in triples))

        # 置信分级
        by_status = {}
        for t in triples:
            by_status[t["status"]] = \
                by_status.get(t["status"], 0) + 1
        record("verified/unverified 分级(5/3)",
               by_status.get("verified") == 5
               and by_status.get("unverified") == 3,
               str(by_status))

        # 幂等: 重采全 skip
        report2 = await svc.run_ingest(
            sources=["system"])
        stat2 = report2["sources"]["system"]
        record("重复采集幂等(entities=0/triples=0)",
               stat2["entities"] == 0
               and stat2["triples"] == 0
               and stat2["skipped"] > 0, str(stat2))

        # 状态迁移: pending→settled 重采置信升级
        from repositories.voice50_repository import (
            Voice50Repository,
        )
        v50 = Voice50Repository()
        pending_ev = [e for e in
                      await v50.list_events(limit=100)
                      if e["status"] == "pending"][0]
        pending_ev["status"] = "settled"
        pending_ev["settledBatchId"] = 88
        await v50.save_event(pending_ev)
        report3 = await svc.run_ingest(
            sources=["system"])
        stat3 = report3["sources"]["system"]
        record("pending→settled 重采走更新",
               stat3["updated"] >= 3, str(stat3))
        up_triples = await repo.list_triples(
            subject=f"ev:voice50:"
                    f"{pending_ev['evId']}",
            limit=10)
        record("升级后三元组转 verified",
               all(t["status"] == "verified"
                   for t in up_triples),
               str([(t["predicate"], t["status"])
                    for t in up_triples]))
        os.environ["KG_MODE"] = "off"


class TestAuthoritySource:
    """02 权威源(published 条款+产品)"""

    async def run(self):
        print("[02 权威源采集]")
        reset_all()
        await seed_authority()
        from repositories.kg51_repository import (
            Kg51Repository,
        )
        from services.kg51_ingest_service import (
            Kg51IngestService,
        )
        repo = Kg51Repository()
        os.environ["KG_MODE"] = "on"
        report = await Kg51IngestService().run_ingest(
            sources=["authority"])
        stat = report["sources"]["authority"]
        clauses = await repo.list_entities(
            entity_type="PolicyClause", limit=100)
        record("published 条款入库(1 条)",
               len(clauses) == 1, str(len(clauses)))
        record("draft 条款不入(权威效力红线)",
               all(c["sourceRef"]
                   != "agreement:2" for c in clauses))
        products = await repo.list_entities(
            entity_type="Product", limit=100)
        record("产品种子全量入库(11 款)",
               len(products) == 11, str(len(products)))
        record("权威源 confidence=1.0",
               all(c["confidence"] == 1.0
                   for c in clauses + products))
        os.environ["KG_MODE"] = "off"


class TestUserSource:
    """03 用户源(48号 turns 低置信候选)"""

    async def run(self):
        print("[03 用户源采集]")
        reset_all()
        await seed_user_turns()
        from repositories.kg51_repository import (
            Kg51Repository,
        )
        from services.kg51_ingest_service import (
            Kg51IngestService,
        )
        repo = Kg51Repository()
        os.environ["KG_MODE"] = "on"
        report = await Kg51IngestService().run_ingest(
            sources=["user"])
        stat = report["sources"]["user"]
        answers = await repo.list_entities(
            entity_type="VoiceAnswer", limit=100)
        record("有效意图轮次入候选(仅 1)",
               len(answers) == 1, str(len(answers)))
        record("噪音意图过滤(not_woken/asr_failed)",
               all(a["attrs"].get("intent")
                   == "product.query"
                   for a in answers))
        record("用户源 confidence=0.6",
               all(a["confidence"] == 0.6
                   for a in answers))
        record("rawText 不入图谱属性(隐私最小化)",
               all("rawText" not in a["attrs"]
                   for a in answers))
        reviews = await repo.list_reviews(
            status="pending", limit=100)
        record("用户源候选进复核队列(confidence)",
               len(reviews) == 1
               and reviews[0]["queueReason"]
               == "confidence", str(len(reviews)))
        os.environ["KG_MODE"] = "off"


class TestConflict:
    """04 冲突解决(单值谓词优先级)"""

    async def run(self):
        print("[04 冲突解决]")
        reset_all()
        from repositories.kg51_repository import (
            Kg51Repository,
        )
        from services.kg51_ingest_service import (
            Kg51IngestService, triple_fingerprint,
        )
        repo = Kg51Repository()
        os.environ["KG_MODE"] = "on"
        svc = Kg51IngestService()
        subject = "ev:voice50:999"

        # 先入用户源 performed_by
        await svc._upsert_triple(
            subject, "performed_by",
            "member:sha256:userA", "user", 0.6,
            {"verifier": "user"}, {"triples": 0,
                                   "reviews": 0})
        # 系统源同 subject 不同 object → 应胜出
        await svc._upsert_triple(
            subject, "performed_by",
            "member:sha256:sysB", "system", 0.98,
            {"verifier": "system"}, {"triples": 0,
                                     "reviews": 0})
        triples = await repo.list_triples(
            predicate="performed_by", subject=subject,
            limit=10)
        active = [t for t in triples
                  if t["status"] != "retired"]
        record("系统源覆盖用户源(高优先级胜出)",
               len(active) == 1
               and active[0]["object"]
               == "member:sha256:sysB",
               str([(t["object"], t["status"])
                    for t in triples]))

        # 用户源再入同 subject → 低优先级拒绝+冲突复核
        stat = {"reviews": 0}
        await svc._upsert_triple(
            subject, "performed_by",
            "member:sha256:userC", "user", 0.6,
            {"verifier": "user"}, stat)
        triples = await repo.list_triples(
            predicate="performed_by", subject=subject,
            limit=10)
        active = [t for t in triples
                  if t["status"] != "retired"]
        record("低优先级新值不生效(仅 1 active)",
               len(active) == 1)
        record("冲突进复核队列(conflict)",
               stat["reviews"] == 1, str(stat))
        os.environ["KG_MODE"] = "off"


class TestReviewFlow:
    """05 复核裁决(unverified 隔离闭环)"""

    async def run(self):
        print("[05 复核裁决]")
        reset_all()
        await seed_system_events()
        from repositories.kg51_repository import (
            Kg51Repository,
        )
        from services.kg51_ingest_service import (
            Kg51IngestService, Kg51ReviewService,
            triple_fingerprint,
        )
        repo = Kg51Repository()
        os.environ["KG_MODE"] = "on"
        await Kg51IngestService().run_ingest(
            sources=["system"])
        os.environ["KG_MODE"] = "off"

        # pending 事件 → unverified + 复核
        reviews = await repo.list_reviews(
            status="pending", reason="confidence",
            limit=100)
        record("pending 事件低置信进复核(≥2)",
               len(reviews) >= 2, str(len(reviews)))

        svc = Kg51ReviewService()
        r = await svc.decide_review(
            reviews[0]["reviewId"], approve=True,
            decision_note="人工采信")
        record("裁决 approve→verified",
               r["status"] == "approved"
               and r["flipped"]["status"] == "verified",
               str(r))
        target = reviews[0]["target"]
        parts = target.split("|")
        after = await repo.find_triple_by_fp(
            triple_fingerprint(*parts))
        record("复核后三元组 verified",
               after["status"] == "verified",
               str(after and after["status"]))

        try:
            await svc.decide_review(
                reviews[0]["reviewId"], approve=False)
            ok, err = False, "未拒绝"
        except ValueError:
            ok, err = True, ""
        record("重复裁决拒绝", ok, err)

        # reject → retired
        r = await svc.decide_review(
            reviews[1]["reviewId"], approve=False)
        record("裁决 reject→retired",
               r["flipped"]["status"] == "retired",
               str(r))

        # unverified 物理隔离: verified 视图零泄漏
        verified = await repo.list_triples(
            status="verified", limit=1000)
        unv = await repo.list_triples(
            status="unverified", limit=1000)
        record("unverified 物理隔离(verified 视图)",
               all(t["status"] == "verified"
                   for t in verified)
               and all(t["status"] != "verified"
                       for t in unv))


class TestOffGate:
    """06 off 铁律 + 端点 + 零影响"""

    async def run(self):
        print("[06 off 铁律+端点+零影响]")
        reset_all()
        from fastapi.testclient import TestClient
        from main import app
        client = TestClient(app)
        admin = {"X-Role": "admin"}

        resp = client.post("/api/kg51/ingest/run",
                           headers=admin)
        record("off 态采集拒绝(409)",
               resp.status_code == 409,
               str(resp.status_code))

        resp = client.get("/api/kg51/ingest/status",
                          headers=admin)
        body = resp.json() or {}
        record("off 态观测可用(空态)",
               resp.status_code == 200
               and body.get("mode") == "off"
               and body.get("tripleCount") == 0,
               str(resp.status_code))

        resp = client.post("/api/kg51/ingest/run",
                          json={})
        record("ingest 无 Role 403",
               resp.status_code == 403,
               str(resp.status_code))

        resp = client.get("/api/kg51/triples",
                          headers=admin)
        record("triples 查询 200(空)",
               resp.status_code == 200, str(
                   resp.status_code))

        resp = client.get("/api/kg51/reviews",
                          headers=admin)
        record("reviews 查询 200(空)",
               resp.status_code == 200,
               str(resp.status_code))

        resp = client.post(
            "/api/kg51/reviews/999/decide",
            headers=admin,
            json={"approve": True})
        record("裁决不存在 404",
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
    await TestSystemSource().run()
    await TestAuthoritySource().run()
    await TestUserSource().run()
    await TestConflict().run()
    await TestReviewFlow().run()
    await TestOffGate().run()


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
