"""51号·小竹可信知识图谱 P2 专项测试
(存储与服务: 查询面+权限矩阵+预算织入+缓存)

运行方式:
    python test_kg51_p2.py

覆盖(51号计划 §八 P2):
    - 邻域查询: depth1 双向展开/verified only/
      空主体零消耗
    - 权限矩阵: member 查自身 digest OK/查他人 409/
      查 L0 OK/admin 任意 OK/无身份 401
    - 预算织入: 查询后 usedToday 增加 0.0x/
      L0 查询零成本/预算耗尽 409/
      admin 不扣预算
    - grounding: keyword 命中产品/条款/
      零成本(预算不变)/无鉴权 200/
      关键词边界拒绝
    - 缓存: 同口径二次命中 cached=true/
      写事件失效(采集后缓存穿透)
    - off 态: query/grounding 空态 200(fail-soft)
    - 端点+零影响(45/50号宪法断言)
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
    from services import kg51_query_cache
    kg51_query_cache.invalidate_all()


async def seed_and_ingest():
    """种子: 50号事件 + 18号条款 + 权威源
    → 采集(返回主体上下文)"""
    from repositories.voice50_repository import (
        Voice50Repository,
    )
    from repositories.agreement_repository import (
        AgreementRepository,
    )
    from core.helpers import ts as _ts
    from services.kg51_ingest_service import (
        Kg51IngestService, member_digest,
    )
    v50 = Voice50Repository()
    member_id = 9101
    ev_id = await v50.next_event_id()
    await v50.save_event({
        "evId": ev_id, "memberId": member_id,
        "sessionId": 0, "turnSeq": 0,
        "behavior": "voice_polite", "layer": "L2",
        "voiceFactor": "",
        "targetFactor": "ethics_evidence",
        "voiceprintMode": "proxy",
        "baseScore": 1.0, "multipliers": {},
        "finalScore": 1.0, "cappedScore": 1.0,
        "overflowScore": 0.0, "status": "settled",
        "ref": f"voice50:{ev_id}",
        "note": f"seed-p2-{uuid.uuid4().hex[:6]}",
        "dayKey": "2026-09-05", "ts": "2026-09-05T10:00:00",
        "settledBatchId": 77,
    })
    # 18号条款种子(published + draft 各一)
    arepo = AgreementRepository()
    agreements = arepo.store.setdefault(
        "agreements", {})
    seq = arepo.store.get("_agreement_seq", 0)
    for status, no in (("published", "T-KG51-PUB"),
                      ("draft", "T-KG51-DRF")):
        seq += 1
        agreements[seq] = {
            "id": seq, "agreementNo": no,
            "name": f"条款{no}", "type": "term",
            "applicableRole": "member",
            "legalBasis": "", "currentVersion": "v1.0",
            "content": "", "changeLog": "",
            "status": status, "effectiveDate": None,
            "versionHistory": [],
            "createdAt": _ts(), "updatedAt": _ts(),
        }
    arepo.store["_agreement_seq"] = seq
    os.environ["KG_MODE"] = "on"
    await Kg51IngestService().run_ingest()
    # 保持 on(查询面语义)——各测试类末尾自行复位 off
    return {
        "memberId": member_id,
        "digest": member_digest(member_id),
        "subject": f"member:sha256:"
                   f"{member_digest(member_id)}",
    }


class TestNeighborhood:
    """01 邻域查询正确性"""

    async def run(self):
        print("[01 邻域查询]")
        reset_all()
        ctx = await seed_and_ingest()
        from services.kg51_query_service import (
            Kg51QueryService,
        )
        svc = Kg51QueryService()

        r = await svc.neighborhood_query(
            subject=ctx["subject"],
            member_id=ctx["memberId"])
        record("自身 digest 邻域(depth1)",
               r["success"] is True
               and r["tripleCount"] >= 1
               and r["mode"] == "on",
               str(r.get("tripleCount")))
        preds = {t["predicate"]
                 for t in r["triples"]}
        record("双向展开含 performed_by",
               "performed_by" in preds, str(preds))
        record("verified only(unverified 隔离)",
               all(t["status"] == "verified"
                   for t in r["triples"]))

        # depth2 全链(事件→证据→因子)
        r2 = await svc.neighborhood_query(
            subject=ctx["subject"],
            member_id=ctx["memberId"], depth=2)
        types = {e["entityType"]
                 for e in r2["entities"]}
        record("depth2 全链类型"
               "(Member/Evidence/EV/Factor)",
               {"Member", "Evidence",
                "VoiceBehaviorEvent",
                "TrustFactor"} <= types,
               str(types))
        preds2 = {t["predicate"]
                  for t in r2["triples"]}
        record("depth2 含 attested_by+credit",
               "attested_by" in preds2
               and "contributes_to_credit"
               in preds2, str(preds2))

        # 空主体: 无实体无三元组 → 零消耗
        # (admin 查询他人视角的空节点)
        r = await svc.neighborhood_query(
            subject="member:sha256:deadbeef",
            admin=True)
        record("空主体零结果零消耗",
               r["tripleCount"] == 0
               and r["privacyCost"] == 0.0,
               str(r.get("privacyCost")))

        # depth 边界
        try:
            await svc.neighborhood_query(
                subject=ctx["subject"],
                member_id=ctx["memberId"], depth=3)
            ok, err = False, "未拒绝"
        except ValueError:
            ok, err = True, ""
        record("depth 越界拒绝(>2)", ok, err)
        try:
            await svc.neighborhood_query(
                subject="", member_id=ctx["memberId"])
            ok, err = False, "未拒绝"
        except ValueError:
            ok, err = True, ""
        record("空 subject 拒绝", ok, err)


class TestPermissionMatrix:
    """02 权限矩阵(admin/会员/公开三级)"""

    async def run(self):
        print("[02 权限矩阵]")
        reset_all()
        ctx = await seed_and_ingest()
        from services.kg51_query_service import (
            Kg51QueryService,
        )
        svc = Kg51QueryService()

        # member 查他人 digest → 409 越权
        other = "member:sha256:" + "f" * 16
        try:
            await svc.neighborhood_query(
                subject=other,
                member_id=ctx["memberId"])
            ok, err = False, "未拒绝"
        except ValueError:
            ok, err = True, ""
        record("member 查他人主体拒绝(越权)",
               ok, err)

        # member 查 L0 公开实体(product) → OK
        r = await svc.neighborhood_query(
            subject="product:sku:ZX42-2026L07",
            member_id=ctx["memberId"])
        record("member 查 L0 公开实体 OK",
               r["success"] is True
               and r["privacyCost"] == 0.0,
               str(r.get("privacyCost")))

        # admin 任意主体
        r = await svc.neighborhood_query(
            subject=ctx["subject"], admin=True)
        record("admin 任意主体 OK",
               r["success"] is True
               and "budget" not in r,
               "budget" in r and "admin 不应扣预算")
        os.environ["KG_MODE"] = "off"


class TestBudget:
    """03 隐私预算织入(49号 check_and_spend)"""

    async def run(self):
        print("[03 预算织入]")
        reset_all()
        ctx = await seed_and_ingest()
        from services.kg51_query_service import (
            Kg51QueryService,
        )
        from services.xiaozhu_privacy_service import (
            XiaozhuPrivacyService,
        )
        svc = Kg51QueryService()
        privacy = XiaozhuPrivacyService()

        before = await privacy.budget_view(
            ctx["memberId"])
        r = await svc.neighborhood_query(
            subject=ctx["subject"],
            member_id=ctx["memberId"], depth=2)
        after = await privacy.budget_view(
            ctx["memberId"])
        spent = round(
            after["usedToday"] - before["usedToday"], 4)
        record("查询后预算扣减(depth2 成本 0.045)"
               "(49号 round2 舍入 0.04)",
               abs(spent - 0.04) < 0.0001
               and r["privacyCost"] == 0.045,
               f"spent={spent} cost="
               f"{r.get('privacyCost')}")

        # L0 查询零成本
        b2 = await privacy.budget_view(
            ctx["memberId"])
        await svc.neighborhood_query(
            subject="product:sku:ZX42-2026L07",
            member_id=ctx["memberId"])
        a2 = await privacy.budget_view(
            ctx["memberId"])
        record("L0 查询零成本(预算不变)",
               a2["usedToday"] == b2["usedToday"],
               f"{b2['usedToday']}"
               f"->{a2['usedToday']}")

        # 预算耗尽 → 409(ValueError)
        await privacy.set_preference(
            ctx["memberId"], 0.5)
        acc = await privacy._account(
            ctx["memberId"])
        acc["usedToday"] = round(
            acc["usedToday"] + 0.49, 2)
        from repositories.xiaozhu_repository import (
            Xiaozhu48Repository,
        )
        await Xiaozhu48Repository(
        ).save_privacy_budget(acc)
        try:
            await svc.neighborhood_query(
                subject=ctx["subject"],
                member_id=ctx["memberId"])
            ok, err = False, "未拒绝"
        except ValueError as e:
            ok, err = "隐私预算不足" in str(e), str(e)[:40]
        record("预算耗尽查询拒绝(409 语义)", ok, err)


class TestGrounding:
    """04 grounding(公开面 L0 零成本)"""

    async def run(self):
        print("[04 grounding]")
        reset_all()
        ctx = await seed_and_ingest()
        from services.kg51_query_service import (
            Kg51QueryService,
        )
        from services.xiaozhu_privacy_service import (
            XiaozhuPrivacyService,
        )
        from repositories.kg51_repository import (
            Kg51Repository,
        )
        svc = Kg51QueryService()
        privacy = XiaozhuPrivacyService()

        before = await privacy.budget_view(
            ctx["memberId"])
        r = await svc.grounding_search(
            keyword="竹")
        record("grounding 命中产品(种子库)",
               r["success"] is True
               and r["anchorCount"] >= 1
               and r["privacyCost"] == 0.0,
               str(r.get("anchorCount")))
        anchors = r["anchors"]
        record("锚点含话术(供小竹引用)",
               all("anchor" in a for a in anchors),
               str(anchors[:1]))
        after = await privacy.budget_view(
            ctx["memberId"])
        record("grounding 零成本(预算不变)",
               after["usedToday"]
               == before["usedToday"])

        r = await svc.grounding_search(keyword="条款")
        all_ents = await Kg51Repository().list_entities(
            limit=5000)
        by_type = {}
        for e in all_ents:
            by_type[e.get("entityType")] = \
                by_type.get(e.get("entityType"), 0) + 1
        record("grounding 命中条款",
               any(a["entityType"] == "PolicyClause"
                   for a in r["anchors"]),
               f"anchors={r.get('anchorCount')} "
               f"types={by_type}")

        # 非公开类型不入 grounding(Member/L3 不可检索)
        r = await svc.grounding_search(
            keyword=ctx["digest"])
        record("敏感实体不入 grounding",
               all(a["entityType"]
                   in ("Product", "PolicyClause",
                       "VoiceAnswer")
                   for a in r["anchors"]))

        try:
            await svc.grounding_search(keyword="")
            ok, err = False, "未拒绝"
        except ValueError:
            ok, err = True, ""
        record("空关键词拒绝", ok, err)
        try:
            await svc.grounding_search(
                keyword="x" * 65)
            ok, err = False, "未拒绝"
        except ValueError:
            ok, err = True, ""
        record("超长关键词拒绝(>64)", ok, err)
        os.environ["KG_MODE"] = "off"


class TestCache:
    """05 查询缓存(TTL+写失效)"""

    async def run(self):
        print("[05 查询缓存]")
        reset_all()
        ctx = await seed_and_ingest()
        from services import kg51_query_cache
        from services.kg51_query_service import (
            Kg51QueryService,
        )
        svc = Kg51QueryService()

        r1 = await svc.grounding_search(keyword="竹")
        r2 = await svc.grounding_search(keyword="竹")
        record("二次查询缓存命中",
               r1["cached"] is False
               and r2["cached"] is True,
               f"{r1['cached']}/{r2['cached']}")
        record("缓存命中结果一致",
               r1["anchorCount"]
               == r2["anchorCount"])

        stats = kg51_query_cache.cache_stats()
        record("缓存观测(entries>0)",
               stats["entries"] >= 1, str(stats))

        # 写事件失效: 新建实体 → 缓存穿透
        from services.kg51_ingest_service import (
            Kg51IngestService,
        )
        from repositories.agreement_repository import (
            AgreementRepository,
        )
        from core.helpers import ts as _ts
        arepo = AgreementRepository()
        agreements = arepo.store.setdefault(
            "agreements", {})
        seq = arepo.store.get("_agreement_seq", 0) + 1
        agreements[seq] = {
            "id": seq, "agreementNo": "T-KG51-CACHE",
            "name": "缓存失效测试条款", "type": "term",
            "applicableRole": "member",
            "legalBasis": "", "currentVersion": "v1.0",
            "content": "", "changeLog": "",
            "status": "published", "effectiveDate": None,
            "versionHistory": [],
            "createdAt": _ts(), "updatedAt": _ts(),
        }
        arepo.store["_agreement_seq"] = seq
        os.environ["KG_MODE"] = "on"
        await Kg51IngestService().run_ingest(
            sources=["authority"])
        r3 = await svc.grounding_search(keyword="竹")
        record("写事件缓存失效(cached=false)",
               r3["cached"] is False)
        r4 = await svc.grounding_search(
            keyword="缓存失效")
        record("新实体可检索(失效后可见)",
               r4["anchorCount"] >= 1,
               str(r4.get("anchorCount")))
        os.environ["KG_MODE"] = "off"


class TestOffAndEndpoints:
    """06 off 态 + 端点 + 零影响"""

    async def run(self):
        print("[06 off 态+端点+零影响]")
        reset_all()
        os.environ["KG_MODE"] = "off"
        from fastapi.testclient import TestClient
        from main import app
        client = TestClient(app)
        admin = {"X-Role": "admin"}
        member = {"X-Member-Id": "9101"}

        # off 态: 查询面空态 200(fail-soft)
        resp = client.get(
            "/api/kg51/query?subject=member:sha256:x",
            headers=member)
        body = resp.json() or {}
        record("off 态 query 空态 200",
               resp.status_code == 200
               and body.get("tripleCount") == 0,
               str(resp.status_code))

        resp = client.get("/api/kg51/grounding"
                          "?keyword=竹")
        body = resp.json() or {}
        record("off 态 grounding 空态 200",
               resp.status_code == 200
               and body.get("anchorCount") == 0,
               str(resp.status_code))

        # on 态端点(先 seed——空图 L0 查询会 409 越权)
        await seed_and_ingest()
        os.environ["KG_MODE"] = "on"
        resp = client.get(
            "/api/kg51/query?subject=product:sku:"
            "ZX42-2026L07", headers=member)
        record("HTTP query L0(会员面)",
               resp.status_code == 200
               and (resp.json()
                    or {}).get("privacyCost") == 0.0,
               f"{resp.status_code} "
               f"{str(resp.json())[:120]}")

        resp = client.get("/api/kg51/query"
                          "?subject=member:sha256:x")
        record("query 无身份 401",
               resp.status_code == 401,
               str(resp.status_code))

        resp = client.get(
            "/api/kg51/query?subject=member:sha256:x",
            headers=member)
        record("query 他人主体 409",
               resp.status_code == 409,
               str(resp.status_code))

        resp = client.get(
            "/api/kg51/query?subject=member:sha256:x"
            "&depth=1", headers=admin)
        record("query admin 任意主体 200",
               resp.status_code == 200,
               str(resp.status_code))

        resp = client.get("/api/kg51/grounding"
                          "?keyword=竹")
        record("grounding 公开面无鉴权 200",
               resp.status_code == 200,
               str(resp.status_code))

        resp = client.get("/api/kg51/grounding")
        record("grounding 缺 keyword 422",
               resp.status_code == 422,
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
    await TestNeighborhood().run()
    await TestPermissionMatrix().run()
    await TestBudget().run()
    await TestGrounding().run()
    await TestCache().run()
    await TestOffAndEndpoints().run()


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
