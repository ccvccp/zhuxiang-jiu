"""57号·AI智能知识库模块 P2 专项测试
(知识种子工坊+人类终审)

运行方式:
    python test_kb57_p2.py

覆盖(57号计划 §十一 P2):
    - 种子工坊: 多模态结构+KNOWLEDGE_REASON+
      无指纹不入库铁律
    - 版本化+A/B: 旧版降权不删除+variantOf 关联
    - 有效期元数据: 过期自动降权
    - 发布终审: published 唯一出口+off 亦可用
    - 召回: recalled+状态机
    - HTTP 层: 5 端点+鉴权+14 端点计数
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
os.environ["QR55_MODE"] = "off"
os.environ["QR55_LEARN_MODE"] = "off"
os.environ["AIUP56_MODE"] = "off"
os.environ["KB57_MODE"] = "off"

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


async def seed_compliant_resource(
        gap_id: int, fingerprint: str = None
        ) -> tuple:
    """种 compliant 资源+缺口(锻造输入)"""
    import hashlib
    from core.helpers import ts
    from repositories.kb57_repository import (
        Kb57Repository,
    )
    repo = Kb57Repository()
    if fingerprint is None:
        fingerprint = ("sha256:" + hashlib.sha256(
            f"p2-{gap_id}-{ts()}".encode(
                "utf-8")).hexdigest()[:32])
    resource_id = await repo.next_resource_id()
    await repo.save_resource({
        "resourceId": resource_id,
        "gapId": gap_id,
        "sourceId": "gov_policy_official",
        "sourceType": "authority",
        "sourceCredibility": 0.95,
        "license": "公开政务(署名标注)",
        "title": "老年人补贴申请要点",
        "contentText": "第一步准备材料; 第二步"
                       "提交申请; 第三步领取结果。",
        "maskedText": "",
        "contentHash": "sha256:" + hashlib.sha256(
            f"ch-{resource_id}".encode(
                "utf-8")).hexdigest()[:32],
        "status": "compliant",
        "reviewRequired": False,
        "budgetHalted": False,
        "resourceVersion": 1,
        "complianceReports": [],
        "fingerprint": fingerprint,
        "createdAt": ts(),
        "updatedAt": ts(),
    })
    return resource_id


async def seed_gap_with_signals() -> int:
    """种缺口(带信号快照——KNOWLEDGE_REASON 输入)"""
    from core.helpers import ts
    from repositories.kb57_repository import (
        Kb57Repository,
    )
    repo = Kb57Repository()
    gap_id = await repo.next_gap_id()
    await repo.save_gap({
        "gapId": gap_id,
        "status": "collecting",
        "priority": "high",
        "topic": "老年人补贴办理指南",
        "decision": "collect",
        "signalSnapshot": {
            "hits": [
                {"signalId": "kb_gap_open",
                 "value": 1, "evidence": "缺口未决"},
                {"signalId": "us52_inclusion_drop",
                 "value": 0.2, "evidence": "包容性降"}],
            "necessityScore": 35.0,
            "sideCoverage": 0.4},
        "necessityScore": 35.0,
        "trustScore": 62.0,
        "suggestedSources": ["gov_policy_official"],
        "budgetCap": 0.1,
        "budgetSpent": 0.01,
        "llmCalls": 0,
        "createdAt": ts(),
        "updatedAt": ts(),
    })
    return gap_id


class TestCraft:
    """01 种子工坊"""

    async def run(self):
        print("[01 种子工坊]")
        reset_all()
        from services.kb57_seed_service import (
            Kb57SeedService, SEED_TYPES,
        )
        workshop = Kb57SeedService()

        gap_id = await seed_gap_with_signals()
        resource_id = await \
            seed_compliant_resource(gap_id)

        # off 拒绝
        try:
            await workshop.craft(
                gap_id, resource_id)
            ok, err = False, "未拒绝"
        except ValueError as e:
            ok, err = "off" in str(e), str(e)[:30]
        record("off 态锻造拒绝", ok, err)

        os.environ["KB57_MODE"] = "shadow"

        # 非法类型
        try:
            await workshop.craft(
                gap_id, resource_id,
                seed_type="dark_pattern")
            ok, err = False, "未拒绝"
        except ValueError as e:
            ok, err = "非法种子类型" in str(e), \
                str(e)[:40]
        record("非法种子类型拒绝", ok, err)

        # 404(资源/缺口)
        try:
            await workshop.craft(gap_id, 999)
            ok, err = False, "未拒绝"
        except KeyError:
            ok, err = True, ""
        record("资源不存在 404", ok, err)
        try:
            await workshop.craft(999, resource_id)
            ok, err = False, "未拒绝"
        except KeyError:
            ok, err = True, ""
        record("缺口不存在 404", ok, err)

        # 非法合规态(quarantined 资源)
        from repositories.kb57_repository import (
            Kb57Repository,
        )
        repo = Kb57Repository()
        res = await repo.get_resource(resource_id)
        res["status"] = "quarantined"
        await repo.save_resource(res, create=False)
        try:
            await workshop.craft(
                gap_id, resource_id)
            ok, err = False, "未拒绝"
        except ValueError as e:
            ok, err = "compliant" in str(e), \
                str(e)[:40]
        record("quarantined 资源锻造拒绝(铁律)",
               ok, err)
        res["status"] = "compliant"
        await repo.save_resource(res, create=False)

        # 正常锻造(text)
        r = await workshop.craft(
            gap_id, resource_id,
            seed_type="text",
            value_tags=["elderly_service", "policy"])
        record("锻造成功(sandbox)",
               r.get("status") == "sandbox"
               and int(r.get("seedId") or 0) > 0,
               str(r.get("status")))
        seed_id = r.get("seedId")

        # 种子结构
        seed = await repo.get_seed(seed_id)
        record("种子结构(指纹继承+标签)",
               seed.get(
                   "complianceFingerprint") \
                   == res.get("fingerprint")
               and seed.get("valueTags")
               == ["elderly_service", "policy"],
               str(seed.get("valueTags")))
        record("KNOWLEDGE_REASON 注释即证据",
               "覆盖缺口" in str(
                   seed.get("knowledgeReason"))
               and "kb_gap_open" in str(
                   seed.get("knowledgeReason")),
               str(seed.get(
                   "knowledgeReason"))[:60])
        record("有效期元数据(validUntil)",
               bool(seed.get("validUntil")),
               str(seed.get("validUntil")))

        # 多模态 content(text)
        content = seed.get("content") or {}
        record("text 种子 content 形态",
               isinstance(content, dict)
               and bool(content.get("text")),
               str(type(content)))

        # qa_pair 形态
        r2 = await workshop.craft(
            gap_id, resource_id,
            seed_type="qa_pair")
        seed2 = await repo.get_seed(
            r2.get("seedId"))
        c2 = seed2.get("content") or {}
        record("qa_pair 形态(Q:/A: 前缀)",
               str(c2.get("text")
                   or "").startswith("Q:"),
               str(c2.get("text"))[:20])

        # video 形态(多模态对齐元数据)
        r3 = await workshop.craft(
            gap_id, resource_id,
            seed_type="video")
        seed3 = await repo.get_seed(
            r3.get("seedId"))
        c3 = seed3.get("content") or {}
        record("video 形态(mediaRef+转写+关键帧)",
               bool(c3.get("mediaRef"))
               and bool(c3.get("transcript"))
               and isinstance(
                   c3.get("keyframes"), list),
               str((c3.get("mediaRef"),
                    c3.get("keyframes"))))

        # image 形态(alt 无障碍)
        r4 = await workshop.craft(
            gap_id, resource_id,
            seed_type="image")
        seed4 = await repo.get_seed(
            r4.get("seedId"))
        c4 = seed4.get("content") or {}
        record("image 形态(mediaRef+alt)",
               bool(c4.get("mediaRef"))
               and "无障碍" in str(c4.get("alt")),
               str(c4.get("alt"))[:30])

        # 版本化(同缺口同类型递增——text 型 v2)
        os.environ["KB57_MODE"] = "shadow"
        r5 = await workshop.craft(
            gap_id, resource_id,
            seed_type="text")
        os.environ["KB57_MODE"] = "off"
        seed5 = await repo.get_seed(
            r5.get("seedId"))
        record("版本化(seedVersion 递增)",
               seed.get("seedVersion") == 1
               and seed5.get("seedVersion") == 2,
               str((seed.get("seedVersion"),
                    seed5.get("seedVersion"))))

        # seed_craft 事件留痕
        events = await repo.list_events(
            gap_id=gap_id, limit=20)
        craft_evs = [e for e in events
                     if e.get("eventType")
                     == "seed_craft"]
        record("seed_craft 事件留痕(5 次)",
               len(craft_evs) == 5,
               str(len(craft_evs)))
        os.environ["KB57_MODE"] = "off"


class TestReview:
    """02 发布终审"""

    async def run(self):
        print("[02 发布终审]")
        reset_all()
        from services.kb57_seed_service import (
            Kb57SeedService,
        )
        from services.kb57_review_service import (
            Kb57ReviewService,
        )
        workshop = Kb57SeedService()
        reviewer = Kb57ReviewService()

        # 种 v1+v2 两颗
        gap_id = await seed_gap_with_signals()
        rid1 = await seed_compliant_resource(gap_id)
        rid2 = await seed_compliant_resource(gap_id)
        os.environ["KB57_MODE"] = "shadow"
        s1 = await workshop.craft(gap_id, rid1)
        s2 = await workshop.craft(gap_id, rid2)
        os.environ["KB57_MODE"] = "off"
        from repositories.kb57_repository import (
            Kb57Repository,
        )
        repo = Kb57Repository()

        # 审批人空拒绝
        try:
            await reviewer.review(
                s1["seedId"], reviewer="", approved=True)
            ok, err = False, "未拒绝"
        except ValueError as e:
            ok, err = "必填" in str(e), str(e)[:30]
        record("审批人空拒绝", ok, err)

        # 404
        try:
            await reviewer.review(
                999, reviewer="admin", approved=True)
            ok, err = False, "未拒绝"
        except KeyError:
            ok, err = True, ""
        record("种子不存在 404", ok, err)

        # off 态终审亦可用(终审人工铁律)
        rv = await reviewer.review(
            s1["seedId"], reviewer="admin",
            approved=True, note="p2-发布")
        record("off 态终审亦可用(铁律)",
               rv.get("verdict") == "approved"
               and rv.get("status") == "published",
               str((rv.get("verdict"),
                    rv.get("status"))))

        # humanVerified 标记
        seed1 = await repo.get_seed(s1["seedId"])
        record("humanVerified 终审标记",
               seed1.get("humanVerified") is True,
               str(seed1.get("humanVerified")))

        # 重复终审拒绝(状态机 published)
        try:
            await reviewer.review(
                s1["seedId"], reviewer="admin",
                approved=True)
            ok, err = False, "未拒绝"
        except ValueError as e:
            ok, err = "sandbox" in str(e), \
                str(e)[:40]
        record("重复终审拒绝(状态机)", ok, err)

        # v2 发布→旧版 v1 自动降权(A/B 关联)
        rv2 = await reviewer.review(
            s2["seedId"], reviewer="admin",
            approved=True)
        record("v2 发布(旧版降权数 1)",
               rv2.get("demotedPrior") == 1,
               str(rv2.get("demotedPrior")))
        seed1b = await repo.get_seed(s1["seedId"])
        record("旧版 v1 降权不删除(downgraded)",
               seed1b.get("status") == "downgraded",
               str(seed1b.get("status")))
        ab = seed1b.get("abTest") or {}
        record("A/B 关联(variantOf 指向 v2)",
               ab.get("active") is True
               and int(ab.get("variantOf") or 0)
               == s2["seedId"],
               str(ab))

        # publish/reject 事件留痕
        events = await repo.list_events(
            gap_id=gap_id, limit=30)
        pub_evs = [e for e in events
                   if e.get("eventType")
                   == "seed_publish"]
        record("seed_publish 事件留痕(2 次)",
               len(pub_evs) == 2,
               str(len(pub_evs)))

        # 驳回流
        gap_id2 = await seed_gap_with_signals()
        rid3 = await seed_compliant_resource(gap_id2)
        os.environ["KB57_MODE"] = "shadow"
        s3 = await workshop.craft(
            gap_id2, rid3)
        os.environ["KB57_MODE"] = "off"
        rv3 = await reviewer.review(
            s3["seedId"], reviewer="admin",
            approved=False, note="质量不足")
        record("驳回(rejected 回工坊)",
               rv3.get("verdict") == "rejected"
               and rv3.get("status") == "rejected",
               str(rv3.get("verdict")))
        seed3 = await repo.get_seed(s3["seedId"])
        record("驳回后 humanVerified 复位",
               seed3.get("humanVerified") is False,
               str(seed3.get("humanVerified")))
        events = await repo.list_events(
            gap_id=gap_id2, limit=20)
        rej_evs = [e for e in events
                   if e.get("eventType")
                   == "seed_reject"]
        record("seed_reject 事件留痕",
               len(rej_evs) == 1,
               str(len(rej_evs)))

        # rejected 种子重新终审拒绝(状态机)
        try:
            await reviewer.review(
                s3["seedId"], reviewer="admin",
                approved=True)
            ok, err = False, "未拒绝"
        except ValueError as e:
            ok, err = "sandbox" in str(e), \
                str(e)[:40]
        record("rejected 终审拒绝(须重制)", ok, err)


class TestRecall:
    """03 紧急召回"""

    async def run(self):
        print("[03 紧急召回]")
        reset_all()
        from services.kb57_seed_service import (
            Kb57SeedService,
        )
        from services.kb57_review_service import (
            Kb57ReviewService,
        )
        workshop = Kb57SeedService()
        reviewer = Kb57ReviewService()

        # 发布一颗
        gap_id = await seed_gap_with_signals()
        rid = await seed_compliant_resource(gap_id)
        os.environ["KB57_MODE"] = "shadow"
        s = await workshop.craft(gap_id, rid)
        os.environ["KB57_MODE"] = "off"
        await reviewer.review(
            s["seedId"], reviewer="admin",
            approved=True)

        # 未发布种子召回拒绝
        gap_id2 = await seed_gap_with_signals()
        rid2 = await seed_compliant_resource(gap_id2)
        os.environ["KB57_MODE"] = "shadow"
        s2 = await workshop.craft(gap_id2, rid2)
        os.environ["KB57_MODE"] = "off"
        try:
            await reviewer.recall(s2["seedId"])
            ok, err = False, "未拒绝"
        except ValueError as e:
            ok, err = "published" in str(e), \
                str(e)[:40]
        record("sandbox 态召回拒绝(状态机)", ok, err)

        # 404
        try:
            await reviewer.recall(999)
            ok, err = False, "未拒绝"
        except KeyError:
            ok, err = True, ""
        record("召回 404", ok, err)

        # 正常召回
        from repositories.kb57_repository import (
            Kb57Repository,
        )
        repo = Kb57Repository()
        seed = await repo.get_seed(s["seedId"])
        seed["viewCount"] = 5
        await repo.save_seed(seed, create=False)

        r = await reviewer.recall(
            s["seedId"], reason="内容误导风险",
            affected_members=[101, 102])
        record("召回成功(recalled)",
               r.get("status") == "recalled",
               str(r.get("status")))
        record("召回原因留痕",
               "误导" in str(r.get("reason")),
               str(r.get("reason")))

        stored = await repo.get_seed(s["seedId"])
        record("召回留痕(recallReason)",
               stored.get("status") == "recalled"
               and "误导" in str(
                   stored.get("recallReason")),
               str(stored.get("recallReason"))[:30])
        record("受影响用户补偿接口(P4 预留)",
               (r.get("compensation") or {})
               .get("attempted") == 2,
               str(r.get("compensation")))

        # seed_recall 事件留痕
        events = await repo.list_events(
            gap_id=gap_id, limit=20)
        recall_evs = [e for e in events
                      if e.get("eventType")
                      == "seed_recall"]
        record("seed_recall 事件留痕",
               len(recall_evs) == 1
               and (recall_evs[0].get("detail")
                    or {}).get("viewCount") == 5,
               str(len(recall_evs)))

        # 重复召回拒绝(状态机)
        try:
            await reviewer.recall(s["seedId"])
            ok, err = False, "未拒绝"
        except ValueError as e:
            ok, err = "published" in str(e), \
                str(e)[:40]
        record("重复召回拒绝(状态机)", ok, err)


class TestFreshness:
    """04 有效期健康检查"""

    async def run(self):
        print("[04 有效期检查]")
        reset_all()
        from services.kb57_seed_service import (
            Kb57SeedService,
        )
        from services.kb57_review_service import (
            Kb57ReviewService,
        )
        workshop = Kb57SeedService()
        reviewer = Kb57ReviewService()

        # 发布两颗(分缺口隔离——降权联动
        # 仅作用于同缺口同类型)
        gap_id = await seed_gap_with_signals()
        gap_id2 = await seed_gap_with_signals()
        rid = await seed_compliant_resource(gap_id)
        rid_old = await seed_compliant_resource(
            gap_id2)
        os.environ["KB57_MODE"] = "shadow"
        s_fresh = await workshop.craft(
            gap_id, rid)
        s_old = await workshop.craft(
            gap_id2, rid_old)
        os.environ["KB57_MODE"] = "off"

        await reviewer.review(
            s_fresh["seedId"], reviewer="admin",
            approved=True)
        await reviewer.review(
            s_old["seedId"], reviewer="admin",
            approved=True)

        # 旧种子 validUntil 置为昨日
        from repositories.kb57_repository import (
            Kb57Repository,
        )
        from datetime import datetime, timedelta
        repo = Kb57Repository()
        old_seed = await repo.get_seed(
            s_old["seedId"])
        old_seed["validUntil"] = (
            datetime.utcnow() - timedelta(days=1)
        ).strftime("%Y-%m-%d")
        await repo.save_seed(old_seed,
                             create=False)

        # 健康检查
        r = await workshop.freshness_check()
        record("健康检查(过期 1+降权 1)",
               r.get("expired") == 1
               and r.get("demoted") == 1,
               str((r.get("expired"),
                    r.get("demoted"))))

        stored_old = await repo.get_seed(
            s_old["seedId"])
        record("过期种子自动降权(downgraded)",
               stored_old.get("status")
               == "downgraded",
               str(stored_old.get("status")))
        stored_fresh = await repo.get_seed(
            s_fresh["seedId"])
        record("未过期种子保持(published)",
               stored_fresh.get("status")
               == "published",
               str(stored_fresh.get("status")))

        # seed_expire 事件留痕(过期种子所在缺口)
        events = await repo.list_events(
            gap_id=gap_id2, limit=20)
        expire_evs = [e for e in events
                      if e.get("eventType")
                      == "seed_expire"]
        record("seed_expire 事件留痕",
               len(expire_evs) == 1,
               str(len(expire_evs)))


class TestHttp:
    """05 HTTP 层"""

    async def run(self):
        print("[05 HTTP]")
        reset_all()
        from fastapi.testclient import TestClient
        from main import app
        client = TestClient(app)
        admin = {"X-Role": "admin"}

        # 种+发布
        gap_id = await seed_gap_with_signals()
        rid = await seed_compliant_resource(gap_id)

        # 决策面 off 409
        resp = client.post(
            "/api/kb57/seeds/craft",
            json={"gapId": gap_id,
                  "resourceId": rid},
            headers=admin)
        record("HTTP craft off 409",
               resp.status_code == 409,
               str(resp.status_code))

        # shadow 态锻造
        os.environ["KB57_MODE"] = "shadow"
        resp = client.post(
            "/api/kb57/seeds/craft",
            json={"gapId": gap_id,
                  "resourceId": rid,
                  "type": "text",
                  "valueTags": ["policy"]},
            headers=admin)
        body = resp.json() or {}
        record("HTTP craft 200(sandbox)",
               resp.status_code == 200
               and body.get("status") == "sandbox",
               str((resp.status_code,
                    body.get("status"))))
        seed_id = body.get("seedId")

        # 观测面
        resp = client.get("/api/kb57/seeds",
                          headers=admin)
        body = resp.json() or {}
        record("HTTP seeds 列表 200",
               resp.status_code == 200
               and (body.get("total") or 0) == 1,
               str((resp.status_code,
                    body.get("total"))))
        resp = client.get(
            f"/api/kb57/seeds/{seed_id}",
            headers=admin)
        body = resp.json() or {}
        record("HTTP seeds/{id} 详情 200",
               resp.status_code == 200
               and bool((body.get("seed") or {})
                        .get("knowledgeReason")),
               str(resp.status_code))

        # off 态终审亦可用(铁律)
        os.environ["KB57_MODE"] = "off"
        resp = client.post(
            f"/api/kb57/seeds/{seed_id}/review",
            json={"reviewer": "admin",
                  "approved": True},
            headers=admin)
        body = resp.json() or {}
        record("HTTP review 200(off 亦可用)",
               resp.status_code == 200
               and body.get("status") == "published",
               str((resp.status_code,
                    body.get("status"))))

        # 召回
        resp = client.post(
            f"/api/kb57/seeds/{seed_id}/recall",
            json={"reason": "http-test",
                  "affectedMembers": []},
            headers=admin)
        body = resp.json() or {}
        record("HTTP recall 200(recalled)",
               resp.status_code == 200
               and body.get("status") == "recalled",
               str((resp.status_code,
                    body.get("status"))))

        # 404
        resp = client.get("/api/kb57/seeds/999",
                          headers=admin)
        record("HTTP seeds 404",
               resp.status_code == 404,
               str(resp.status_code))
        resp = client.post(
            "/api/kb57/seeds/999/review",
            json={"reviewer": "admin",
                  "approved": True},
            headers=admin)
        record("HTTP review 404",
               resp.status_code == 404,
               str(resp.status_code))

        # 状态机 409(已召回种子再 review)
        resp = client.post(
            f"/api/kb57/seeds/{seed_id}/review",
            json={"reviewer": "admin",
                  "approved": True},
            headers=admin)
        record("HTTP 已召回终审 409",
               resp.status_code == 409,
               str(resp.status_code))

        # 鉴权 403
        for method, path in (
                ("POST", "/api/kb57/seeds/craft"),
                ("GET", "/api/kb57/seeds"),
                ("POST",
                 f"/api/kb57/seeds/{seed_id}"
                 f"/review"),
                ("POST",
                 f"/api/kb57/seeds/{seed_id}"
                 f"/recall")):
            resp = client.request(
                method, path, json={})
            record(f"HTTP {path.split('/')[-1]}"
                   f" 无 Role 403",
                   resp.status_code == 403,
                   str(resp.status_code))

        # 路由累计 14 端点
        from routes.kb57_routes import (
            router as kb_router,
        )
        count = sum(1 for r in kb_router.routes)
        record("57号路由累计 14 端点",
               count == 14, str(count))


async def run_all():
    await TestCraft().run()
    await TestReview().run()
    await TestRecall().run()
    await TestFreshness().run()
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
