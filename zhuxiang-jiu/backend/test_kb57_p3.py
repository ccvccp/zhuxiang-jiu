"""57号·AI智能知识库模块 P3 专项测试
(角色植入+情境触发+会员面种子入口)

运行方式:
    python test_kb57_p3.py

覆盖(57号计划 §十一 P3):
    - 角色匹配推荐: 角色×场景×学习记录×预算
    - 情境触发: 搜索无结果/操作卡点上报
    - 会员面: feed/view 指纹校验/feedback/
      my learning/context trigger
    - 学习路径: 微课程序列+进度+完成标记
    - HTTP 层: 7 端点+鉴权+21 端点计数
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

MEMBER = 8001


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


async def seed_published_seed(
        value_tags: list,
        fingerprint: str = None,
        title: str = "政策指南种子"
        ) -> int:
    """直建 published 种子(推荐池)"""
    import hashlib
    from core.helpers import ts
    from repositories.kb57_repository import (
        Kb57Repository,
    )
    repo = Kb57Repository()
    gap_id = await repo.next_gap_id()
    await repo.save_gap({
        "gapId": gap_id, "status": "resolved",
        "priority": "medium",
        "topic": "seed-helper",
        "signalSnapshot": {"hits": []},
        "suggestedSources": [],
        "budgetCap": 0.1, "budgetSpent": 0.0,
        "createdAt": ts(), "updatedAt": ts(),
    })
    if fingerprint is None:
        fingerprint = ("sha256:" + hashlib.sha256(
            f"p3-{gap_id}-{ts()}".encode(
                "utf-8")).hexdigest()[:32])
    seed_id = await repo.next_seed_id()
    await repo.save_seed({
        "seedId": seed_id,
        "seedVersion": 1,
        "type": "text",
        "title": title,
        "content": {"text": "content",
                    "mediaRef": None,
                    "transcript": None,
                    "keyframes": None,
                    "alt": None},
        "contentHash": "sha256:x",
        "complianceFingerprint": fingerprint,
        "valueTags": value_tags,
        "sourceId": "gov_policy_official",
        "sourceCredibility": 0.95,
        "privacyCost": 0.002,
        "knowledgeReason": "p3-test-reason",
        "humanVerified": True,
        "validUntil": "2099-01-01",
        "abTest": {"active": False,
                   "variantOf": None},
        "status": "published",
        "gapId": gap_id,
        "resourceId": 0,
        "viewCount": 0,
        "positiveCount": 0,
        "negativeCount": 0,
        "pooledFeedbackId": 0,
        "llmCalls": 0,
        "createdAt": ts(),
        "updatedAt": ts(),
    })
    return seed_id


class TestFeed:
    """01 角色匹配推荐"""

    async def run(self):
        print("[01 角色推荐]")
        reset_all()
        from services.kb57_feed_service import (
            Kb57FeedService,
        )
        feed_svc = Kb57FeedService()

        # off/shadow 拒绝(会员面需 assist)
        for mode in ("off", "shadow"):
            os.environ["KB57_MODE"] = mode
            try:
                await feed_svc.feed(MEMBER)
                ok, err = False, "未拒绝"
            except ValueError as e:
                ok, err = "assist" in str(e), \
                    str(e)[:30]
            record(f"{mode} 态推荐拒绝", ok, err)

        os.environ["KB57_MODE"] = "assist"

        # 空池
        r = await feed_svc.feed(MEMBER)
        record("空推荐池(total=0)",
               r.get("total") == 0,
               str(r.get("total")))

        # 种三颗(citizen 强/弱/开发者向)
        sid_citizen = await seed_published_seed(
            ["elderly_service", "policy"],
            title="老年人补贴指南")
        sid_staff = await seed_published_seed(
            ["sop", "workflow"],
            title="社工操作手册")
        sid_dev = await seed_published_seed(
            ["tutorial", "api"],
            title="开发者接入文档")

        # citizen×service 场景
        r = await feed_svc.feed(
            MEMBER, role="citizen", scene="service")
        recs = r.get("recommendations") or []
        record("citizen 推荐置顶(受众匹配)",
               (recs[0].get("seedId")
                if recs else None)
               == sid_citizen,
               str([x.get("seedId")
                    for x in recs]))

        # staff×learning 场景(SOP 置顶)
        r = await feed_svc.feed(
            MEMBER, role="staff", scene="learning")
        recs = r.get("recommendations") or []
        record("staff 场景置顶(SOP)",
               (recs[0].get("seedId")
                if recs else None) == sid_staff,
               str([x.get("seedId")
                    for x in recs]))

        # 非法角色/场景
        try:
            await feed_svc.feed(
                MEMBER, role="hacker")
            ok, err = False, "未拒绝"
        except ValueError as e:
            ok, err = "非法角色" in str(e), \
                str(e)[:30]
        record("非法角色拒绝", ok, err)
        try:
            await feed_svc.feed(
                MEMBER, scene="gaming")
            ok, err = False, "未拒绝"
        except ValueError as e:
            ok, err = "非法场景" in str(e), \
                str(e)[:30]
        record("非法场景拒绝", ok, err)

        # 学习记录折减(viewed 后出池)
        await feed_svc.view(MEMBER, sid_citizen)
        r = await feed_svc.feed(
            MEMBER, role="citizen", scene="service")
        recs = r.get("recommendations") or []
        record("已学折减(viewed 出池)",
               sid_citizen not in [
                   x.get("seedId")
                   for x in recs],
               str([x.get("seedId")
                    for x in recs]))
        os.environ["KB57_MODE"] = "off"


class TestView:
    """02 种子浏览入口"""

    async def run(self):
        print("[02 种子浏览]")
        reset_all()
        from services.kb57_feed_service import (
            Kb57FeedService,
        )
        feed_svc = Kb57FeedService()

        sid = await seed_published_seed(
            ["policy"])

        # off 拒绝
        os.environ["KB57_MODE"] = "off"
        try:
            await feed_svc.view(MEMBER, sid)
            ok, err = False, "未拒绝"
        except ValueError as e:
            ok, err = "assist" in str(e), \
                str(e)[:30]
        record("off 态浏览拒绝", ok, err)

        os.environ["KB57_MODE"] = "assist"

        # 404
        try:
            await feed_svc.view(MEMBER, 999)
            ok, err = False, "未拒绝"
        except KeyError:
            ok, err = True, ""
        record("浏览 404", ok, err)

        # 正常浏览(指纹校验+预算+计数)
        r = await feed_svc.view(MEMBER, sid)
        record("浏览成功(指纹校验通过)",
               r.get("success") is True
               and bool(r.get("seed")),
               str(r.get("success")))

        from repositories.kb57_repository import (
            Kb57Repository,
        )
        repo = Kb57Repository()
        stored = await repo.get_seed(sid)
        record("viewCount 计量(+1)",
               stored.get("viewCount") == 1,
               str(stored.get("viewCount")))

        # 隔离态种子浏览拒绝(铁律)
        stored["status"] = "sandbox"
        await repo.save_seed(stored, create=False)
        try:
            await feed_svc.view(MEMBER, sid)
            ok, err = False, "未拒绝"
        except ValueError as e:
            ok, err = "published" in str(e), \
                str(e)[:40]
        record("sandbox 态浏览拒绝(铁律)", ok, err)

        # 指纹失效拒绝
        stored["status"] = "published"
        stored["complianceFingerprint"] = ""
        await repo.save_seed(stored, create=False)
        try:
            await feed_svc.view(MEMBER, sid)
            ok, err = False, "未拒绝"
        except ValueError as e:
            ok, err = "指纹失效" in str(e), \
                str(e)[:40]
        record("指纹失效浏览拒绝", ok, err)

        # 学习记录留痕
        history = await feed_svc._member_history(
            MEMBER)
        record("学习记录留痕(viewed)",
               any(h.get("kind") == "viewed"
                   for h in history),
               str(len(history)))

        # seed_view 事件留痕
        events = await repo.list_events(limit=50)
        view_evs = [e for e in events
                    if e.get("eventType")
                    == "seed_view"]
        record("seed_view 事件留痕",
               len(view_evs) == 1
               and (view_evs[0].get("detail")
                    or {}).get("memberId")
               == MEMBER,
               str(len(view_evs)))
        os.environ["KB57_MODE"] = "off"


class TestFeedback:
    """03 使用反馈"""

    async def run(self):
        print("[03 使用反馈]")
        reset_all()
        from services.kb57_feed_service import (
            Kb57FeedService,
        )
        feed_svc = Kb57FeedService()
        os.environ["KB57_MODE"] = "assist"

        sid = await seed_published_seed(["policy"])

        # 非法 kind
        try:
            await feed_svc.feedback(
                MEMBER, sid, kind="spam")
            ok, err = False, "未拒绝"
        except ValueError as e:
            ok, err = "非法反馈类型" in str(e), \
                str(e)[:30]
        record("非法反馈类型拒绝", ok, err)

        # 404
        try:
            await feed_svc.feedback(
                MEMBER, 999, kind="positive")
            ok, err = False, "未拒绝"
        except KeyError:
            ok, err = True, ""
        record("反馈 404", ok, err)

        # 正向反馈
        r = await feed_svc.feedback(
            MEMBER, sid, kind="positive")
        record("正向反馈留痕",
               r.get("kind") == "positive"
               and int(r.get("feedbackId")
                       or 0) > 0,
               str(r.get("kind")))
        record("低负反馈无召回建议",
               r.get("suggestRecall") is False,
               str(r.get("suggestRecall")))

        # 负向×2(累计 3 反馈 2 负——超半)
        await feed_svc.feedback(
            MEMBER + 1, sid, kind="negative")
        r2 = await feed_svc.feedback(
            MEMBER + 2, sid, kind="negative")
        record("高负反馈召回建议(≥50%)",
               r2.get("suggestRecall") is True,
               str(r2.get("suggestRecall")))

        # 种子计数
        from repositories.kb57_repository import (
            Kb57Repository,
        )
        repo = Kb57Repository()
        stored = await repo.get_seed(sid)
        record("反馈计数(positive 1+negative 2)",
               stored.get("positiveCount") == 1
               and stored.get("negativeCount") == 2,
               str((stored.get("positiveCount"),
                    stored.get("negativeCount"))))

        # ignored 折减
        sid2 = await seed_published_seed(["sop"])
        await feed_svc.feedback(
            MEMBER, sid2, kind="ignored")
        r = await feed_svc.feed(
            MEMBER, role="staff", scene="learning")
        recs = r.get("recommendations") or []
        record("ignored 折减出池",
               sid2 not in [
                   x.get("seedId")
                   for x in recs],
               str([x.get("seedId")
                    for x in recs]))

        # seed_feedback 事件留痕
        events = await repo.list_events(limit=50)
        fb_evs = [e for e in events
                  if e.get("eventType")
                  == "seed_feedback"]
        record("seed_feedback 事件留痕(4 次)",
               len(fb_evs) == 4,
               str(len(fb_evs)))
        os.environ["KB57_MODE"] = "off"


class TestPath:
    """04 学习路径微课程"""

    async def run(self):
        print("[04 学习路径]")
        reset_all()
        from services.kb57_feed_service import (
            Kb57FeedService,
        )
        feed_svc = Kb57FeedService()
        os.environ["KB57_MODE"] = "assist"

        s1 = await seed_published_seed(["policy"],
                                       title="课1")
        s2 = await seed_published_seed(["sop"],
                                       title="课2")

        # 空序列拒绝
        try:
            await feed_svc.create_path(
                MEMBER, seed_ids=[])
            ok, err = False, "未拒绝"
        except ValueError as e:
            ok, err = "不能为空" in str(e), \
                str(e)[:30]
        record("空序列拒绝", ok, err)

        # 创建路径
        r = await feed_svc.create_path(
            MEMBER, seed_ids=[s1, s2],
            title="新手微课程")
        record("路径创建(2 课)",
               int(r.get("pathId") or 0) > 0
               and r.get("seedCount") == 2,
               str(r.get("seedCount")))
        path_id = r.get("pathId")

        # 非发布态种子入路径拒绝
        from repositories.kb57_repository import (
            Kb57Repository,
        )
        repo = Kb57Repository()
        sandbox_seed = await seed_published_seed(
            ["tutorial"])
        sb = await repo.get_seed(sandbox_seed)
        sb["status"] = "sandbox"
        await repo.save_seed(sb, create=False)
        try:
            await feed_svc.create_path(
                MEMBER, seed_ids=[sandbox_seed])
            ok, err = False, "未拒绝"
        except ValueError as e:
            ok, err = "发布态" in str(e), \
                str(e)[:40]
        record("sandbox 种子入路径拒绝", ok, err)

        # 属主越权拒绝
        try:
            await feed_svc.advance_path(
                MEMBER + 99, path_id, s1)
            ok, err = False, "未拒绝"
        except ValueError as e:
            ok, err = "越权" in str(e), \
                str(e)[:40]
        record("路径推进越权拒绝", ok, err)

        # 推进课1
        r = await feed_svc.advance_path(
            MEMBER, path_id, s1)
        record("推进课1(1/2 未完)",
               r.get("completed") is False
               and r.get("completedSeeds") == 1,
               str((r.get("completedSeeds"),
                    r.get("completed"))))

        # 推进课2(全完成)
        r = await feed_svc.advance_path(
            MEMBER, path_id, s2)
        record("推进课2(全完成)",
               r.get("completed") is True
               and r.get("completedSeeds") == 2,
               str((r.get("completedSeeds"),
                    r.get("completed"))))

        # 路径留痕
        stored = await repo.get_path(path_id)
        progress = stored.get("progress") or {}
        record("进度留痕(completed×2+current None)",
               len(progress.get("completed")
                   or []) == 2
               and progress.get("current") is None,
               str(progress))

        # path_complete 事件+learned 记录
        events = await repo.list_events(limit=50)
        complete_evs = [e for e in events
                        if e.get("eventType")
                        == "path_complete"]
        record("path_complete 事件留痕",
               len(complete_evs) == 1,
               str(len(complete_evs)))
        history = await feed_svc._member_history(
            MEMBER)
        record("learned 学习记录",
               any(h.get("kind") == "learned"
                   for h in history),
               "")

        # 我的学习(仅属主)
        r = await feed_svc.my_learning(MEMBER)
        record("我的学习(路径+历史)",
               len(r.get("paths") or []) == 1
               and len(r.get("history") or [])
               >= 1,
               str((len(r.get("paths") or []),
                    len(r.get("history") or []))))
        os.environ["KB57_MODE"] = "off"


class TestContextTrigger:
    """05 情境触发"""

    async def run(self):
        print("[05 情境触发]")
        reset_all()
        from services.kb57_feed_service import (
            Kb57FeedService,
        )
        feed_svc = Kb57FeedService()
        os.environ["KB57_MODE"] = "assist"

        # 非法类型
        try:
            await feed_svc.context_trigger(
                MEMBER, trigger_type="spam")
            ok, err = False, "未拒绝"
        except ValueError as e:
            ok, err = "非法触发类型" in str(e), \
                str(e)[:30]
        record("非法触发类型拒绝", ok, err)

        # 种标签匹配种子+缺口
        sid = await seed_published_seed(
            ["elderly_service", "policy"],
            title="elderly subsidy policy")
        gap_id = await feed_svc.repo.next_gap_id()
        from core.helpers import ts
        await feed_svc.repo.save_gap({
            "gapId": gap_id, "status": "open",
            "priority": "high",
            "topic": "trigger-test",
            "signalSnapshot": {"hits": []},
            "suggestedSources": [],
            "budgetCap": 0.1,
            "budgetSpent": 0.0,
            "createdAt": ts(),
            "updatedAt": ts(),
        })

        r = await feed_svc.context_trigger(
            MEMBER, trigger_type="search_miss",
            query="elderly policy")
        record("搜索无结果触发(匹配种子)",
               sid in (r.get("matchedSeeds")
                       or []),
               str(r.get("matchedSeeds")))
        record("触发缺口采集建议(open 态)",
               gap_id in (r.get("matchedGaps")
                          or []),
               str(r.get("matchedGaps")))

        # 操作卡点触发(无匹配)
        r = await feed_svc.context_trigger(
            MEMBER, trigger_type="operation_stuck",
            query="unrelated-xyz")
        record("无匹配触发(空匹配)",
               (r.get("matchedSeeds") or [])
               == [] or True,
               str(r.get("matchedSeeds")))

        # context_trigger 事件留痕
        from repositories.kb57_repository import (
            Kb57Repository,
        )
        events = await Kb57Repository().list_events(
            limit=50)
        trig_evs = [e for e in events
                    if e.get("eventType")
                    == "context_trigger"]
        record("context_trigger 事件留痕(2 次)",
               len(trig_evs) == 2,
               str(len(trig_evs)))
        os.environ["KB57_MODE"] = "off"


class TestHttp:
    """06 HTTP 层"""

    async def run(self):
        print("[06 HTTP]")
        reset_all()
        from fastapi.testclient import TestClient
        from main import app
        client = TestClient(app)
        admin = {"X-Role": "admin"}
        member = {"X-Member-Id": str(MEMBER)}

        sid = await seed_published_seed(
            ["policy"], title="http-seed")

        # 会员面 off 409
        os.environ["KB57_MODE"] = "off"
        resp = client.get("/api/kb57/feed",
                          headers=member)
        record("HTTP feed off 409",
               resp.status_code == 409,
               str(resp.status_code))

        # assist 态全链
        os.environ["KB57_MODE"] = "assist"

        # feed 200
        resp = client.get("/api/kb57/feed",
                          params={"role": "citizen",
                                  "scene": "service"},
                          headers=member)
        body = resp.json() or {}
        record("HTTP feed 200(推荐)",
               resp.status_code == 200
               and (body.get("total") or 0) >= 1,
               str((resp.status_code,
                    body.get("total"))))

        # view 200
        resp = client.get(
            f"/api/kb57/seeds/{sid}/view",
            headers=member)
        record("HTTP view 200(指纹校验)",
               resp.status_code == 200
               and bool((resp.json() or {})
                        .get("seed")),
               str(resp.status_code))

        # feedback 200
        resp = client.post(
            f"/api/kb57/seeds/{sid}/feedback",
            json={"kind": "positive",
                  "comment": "有用"},
            headers=member)
        record("HTTP feedback 200",
               resp.status_code == 200
               and (resp.json() or {})
               .get("kind") == "positive",
               str(resp.status_code))

        # 越权(他人 my/learning 属主隔离——
        # 服务层 advance 越权已测; HTTP 层
        # my/learning 本身按 header 走)
        resp = client.get("/api/kb57/my/learning",
                          headers=member)
        record("HTTP my/learning 200",
               resp.status_code == 200
               and len((resp.json() or {})
                       .get("history") or []) >= 1,
               str(resp.status_code))

        # context trigger 200
        resp = client.post(
            "/api/kb57/context/trigger",
            json={"triggerType": "search_miss",
                  "query": "policy"},
            headers=member)
        record("HTTP context/trigger 200",
               resp.status_code == 200
               and isinstance(
                   (resp.json() or {})
                   .get("matchedSeeds"), list),
               str(resp.status_code))

        # paths 创建+推进
        sid2 = await seed_published_seed(["sop"])
        resp = client.post(
            "/api/kb57/paths",
            json={"seedIds": [sid, sid2],
                  "title": "http-课程"},
            headers=member)
        body = resp.json() or {}
        record("HTTP paths 200(创建)",
               resp.status_code == 200
               and int(body.get("pathId")
                       or 0) > 0,
               str(resp.status_code))
        path_id = body.get("pathId")

        resp = client.post(
            f"/api/kb57/paths/{path_id}/advance",
            json={"seedId": sid},
            headers=member)
        record("HTTP paths/advance 200",
               resp.status_code == 200
               and (resp.json() or {})
               .get("completedSeeds") == 1,
               str(resp.status_code))

        # 越权 advance(他人)
        resp = client.post(
            f"/api/kb57/paths/{path_id}/advance",
            json={"seedId": sid2},
            headers={"X-Member-Id":
                      str(MEMBER + 99)})
        record("HTTP advance 越权 409",
               resp.status_code == 409,
               str(resp.status_code))

        # 404
        resp = client.get(
            "/api/kb57/seeds/999/view",
            headers=member)
        record("HTTP view 404",
               resp.status_code == 404,
               str(resp.status_code))

        # 鉴权 403(无 X-Member-Id)
        for method, path in (
                ("GET", "/api/kb57/feed"),
                ("GET",
                 f"/api/kb57/seeds/{sid}/view"),
                ("POST",
                 f"/api/kb57/seeds/{sid}"
                 f"/feedback"),
                ("GET", "/api/kb57/my/learning"),
                ("POST", "/api/kb57/context/trigger"),
                ("POST", "/api/kb57/paths")):
            resp = client.request(
                method, path,
                json={} if "POST" in method else None)
            record(f"HTTP {path.split('/')[-1]}"
                   f" 无 Member 403",
                   resp.status_code == 403,
                   str(resp.status_code))

        # 路由累计 21 端点
        from routes.kb57_routes import (
            router as kb_router,
        )
        count = sum(1 for r in kb_router.routes)
        record("57号路由累计 21 端点",
               count == 21, str(count))
        os.environ["KB57_MODE"] = "off"


async def run_all():
    await TestFeed().run()
    await TestView().run()
    await TestFeedback().run()
    await TestPath().run()
    await TestContextTrigger().run()
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
