"""58号·AI智能优化意图识别模块 P2 专项测试
(业务耦合+动态信任校准)

运行方式:
    python test_ii58_p2.py

覆盖(58号计划 §九 P2):
    - 识别即合规: minRole 前置校验+沙箱五级
      裁决+越界拦截(boundaryIntercepted+
      归因保留原始意图)+三态纯度
    - 槽位上下文预填: 会话上轮指代消解+
      页面状态(48号纯读取零写入)
    - 合规模板关联: 12 意图 10 模板+valueTags
    - 阈值配置域: calibrate→46号留痕+镜像
      pending→人工终审唯一出口+thresholds 全景
    - HTTP 层: 2 新端点+鉴权+13 端点计数
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
os.environ["II58_MODE"] = "off"
os.environ.pop("II58_LLM_MODE", None)

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


async def seed_corpus(intent_id: str, text: str,
                      sample_type: str = "positive"
                      ) -> int:
    """种 58号语料(active 直通)"""
    from core.helpers import ts
    from repositories.ii58_repository import (
        Ii58Repository,
    )
    repo = Ii58Repository()
    corpus_id = await repo.next_corpus_id()
    await repo.save_corpus({
        "corpusId": corpus_id,
        "corpusVersion": 1,
        "intentId": intent_id,
        "sampleType": sample_type,
        "text": text,
        "weight": 1.0,
        "source": "manual",
        "originRef": "",
        "confusableTarget": None,
        "humanVerified": True,
        "humanSuggested": False,
        "status": "active",
        "createdAt": ts(),
        "updatedAt": ts(),
    })
    return corpus_id


async def seed_xiaozhu_turn_with_subject(
        session_id: int, subject: str) -> str:
    """种 48号 turn(带 card.subject——预填源)"""
    import uuid
    from core.helpers import ts
    from repositories.xiaozhu_repository import (
        Xiaozhu48Repository,
    )
    repo = Xiaozhu48Repository()
    turn_id = f"t-{uuid.uuid4().hex[:8]}"
    seq = await repo.next_turn_seq(session_id)
    await repo.save_turn({
        "turnId": turn_id,
        "sessionId": session_id, "seq": seq,
        "channel": "text", "audioMeta": {},
        "rawText": "看看商品", "wake": True,
        "intent": "product.new",
        "action": "product.new", "reply": "ok",
        "card": {"subject": subject},
        "jump": None, "latencyMs": 100.0,
        "ts": ts(), "executed": True,
    })
    return turn_id


class TestCompliance:
    """01 识别即合规前置校验"""

    async def run(self):
        print("[01 识别即合规]")
        reset_all()
        from services.ii58_service import (
            Ii58Service,
        )
        svc = Ii58Service()
        os.environ["II58_MODE"] = "shadow"

        # 语料: 价格查询+兑换+越界域+竞争域
        await seed_corpus(
            "product.price_query", "多少钱")
        await seed_corpus(
            "trust.convert_intent", "兑换信值")
        await seed_corpus(
            "boundary.unauthorized",
            "删除所有会员数据")
        await seed_corpus(
            "product.new_query", "新品")
        # partial 构造域(3 条语料→FULL+PARTIAL×2
        # 加权 0.733×共识 1.15≈0.84——区间带)
        await seed_corpus(
            "promo.query", "优惠多少")
        await seed_corpus(
            "promo.query", "优惠多少呀")
        await seed_corpus(
            "promo.query", "优惠多少呢")

        # guest 越界(member 意图)
        r = await svc.evaluate(
            "多少钱", member_role="guest")
        comp = r.get("compliance") or {}
        record("guest 越界拦截(denied)",
               comp.get("decision") == "denied",
               str(comp.get("decision")))
        record("boundaryIntercepted 标记",
               r.get("boundaryIntercepted")
               is True,
               str(r.get("boundaryIntercepted")))
        record("输出改判(越界元意图)",
               r.get("intentId")
               == "boundary.unauthorized",
               str(r.get("intentId")))
        record("归因保留原始意图",
               comp.get("originalIntentId")
               == "product.price_query",
               str(comp.get("originalIntentId")))
        record("拒绝话术(refusalNote)",
               bool(comp.get("refusalNote")),
               str(comp.get("refusalNote"))[:40])

        # member/admin/staff 放行
        r2 = await svc.evaluate(
            "多少钱", member_role="member")
        record("member 放行(allow)",
               (r2.get("compliance")
                or {}).get("decision") == "allow"
               and r2.get("intentId")
               == "product.price_query",
               str((r2.get("compliance")
                    or {}).get("decision")))
        r3 = await svc.evaluate(
            "多少钱", member_role="staff")
        record("staff≥member 放行",
               (r3.get("compliance")
                or {}).get("decision") == "allow",
               "")
        r4 = await svc.evaluate(
            "多少钱", member_role="admin")
        record("admin 全通",
               (r4.get("compliance")
                or {}).get("decision") == "allow",
               "")

        # deny 沙箱(越界元意图域直接拦截)
        r5 = await svc.evaluate(
            "删除所有会员数据",
            member_role="admin")
        comp5 = r5.get("compliance") or {}
        record("deny 沙箱(admin 亦拦截)",
               comp5.get("decision") == "denied"
               and comp5.get("sandbox") == "deny",
               str((comp5.get("decision"),
                    comp5.get("sandbox"))))

        # sensitive 二次确认
        r6 = await svc.evaluate(
            "兑换信值", member_role="member")
        comp6 = r6.get("compliance") or {}
        record("sensitive→confirm_required",
               comp6.get("decision")
               == "confirm_required",
               str(comp6.get("decision")))
        record("requireConfirm+屏幕码语义",
               r6.get("requireConfirm") is True
               and "屏幕码" in str(
                   r6.get("note")),
               str(r6.get("note"))[:40])

        # 三态纯度: clarify 不校验权限
        r7 = await svc.evaluate(
            "完全无关的天气文本",
            member_role="guest")
        record("clarify 不校验权限(无交付)",
               r7.get("state") == "clarify"
               and "compliance" not in r7,
               str(r7.get("state")))

        # 三态纯度: partial 不校验权限
        # (0.7≤conf<0.9 区间带——无交付无权限语义)
        r8 = await svc.evaluate(
            "优惠多少哈", member_role="guest")
        record("partial 不校验权限",
               r8.get("state") == "partial"
               and "compliance" not in r8,
               str((r8.get("state"),
                    r8.get("confidence"))))

        # compliance 块结构
        record("compliance 结构四字段",
               all(k in comp6 for k in (
                   "minRole", "memberRole",
                   "sandbox", "template")),
               str(sorted(comp6.keys())))

        # 落库归因(evaluations.attribution.
        # compliance+boundaryIntercepted)
        from repositories.ii58_repository import (
            Ii58Repository,
        )
        stored = await Ii58Repository(
        ).get_evaluation(r["evalId"])
        attr = stored.get("attribution") or {}
        record("归因落库(compliance+拦截)",
               "compliance" in attr
               and stored.get(
                   "boundaryIntercepted") is True
               and stored.get("intentId")
               == "product.price_query",
               str((stored.get("intentId"),
                    stored.get(
                        "boundaryIntercepted"))))
        os.environ["II58_MODE"] = "off"


class TestSlotPrefill:
    """02 槽位上下文预填"""

    async def run(self):
        print("[02 槽位预填]")
        reset_all()
        from services.ii58_service import (
            Ii58Service,
        )
        svc = Ii58Service()
        os.environ["II58_MODE"] = "shadow"

        await seed_corpus(
            "product.price_query", "多少钱")
        await seed_corpus(
            "nav.page_jump", "跳转页面")

        # ① 指代消解预填(这个多少钱→上轮商品名)
        sid = 77
        await seed_xiaozhu_turn_with_subject(
            sid, "飞天茅台")
        r = await svc.evaluate(
            "这个多少钱", session_id=sid)
        slots = r.get("slots") or {}
        sources = (r.get("attribution")
                   or {}).get("slotSources") or {}
        record("指代词预填(上轮 card.subject)",
               slots.get("keyword") == "飞天茅台",
               str(slots.get("keyword")))
        record("预填来源标记(context_prefill)",
               (sources.get("keyword")
                or {}).get("source")
               == "context_prefill",
               str(sources.get("keyword")))

        # ② 显式抽取优先(引号——不预填)
        r2 = await svc.evaluate(
            "「茅台」多少钱", session_id=sid)
        slots2 = r2.get("slots") or {}
        sources2 = (r2.get("attribution")
                     or {}).get("slotSources") or {}
        record("显式抽取优先(不被预填覆盖)",
               slots2.get("keyword") == "茅台"
               and "keyword" not in sources2,
               str(slots2.get("keyword")))

        # ③ 无会话回退显式域
        r3 = await svc.evaluate("这个多少钱")
        slots3 = r3.get("slots") or {}
        record("无会话回退(无预填来源)",
               "keyword" in slots3
               and not ((r3.get("attribution")
                         or {}).get(
                   "slotSources") or {}),
               str(slots3.get("keyword")))

        # ④ 无指代词不预填
        r4 = await svc.evaluate(
            "多少钱", session_id=sid)
        sources4 = (r4.get("attribution")
                    or {}).get("slotSources") or {}
        record("无指代词不预填",
               "keyword" not in sources4,
               str(sources4))

        # ⑤ 页面状态预填(page 槽位)
        r5 = await svc.evaluate(
            "跳转页面", current_page="购物车")
        slots5 = r5.get("slots") or {}
        sources5 = (r5.get("attribution")
                    or {}).get("slotSources") or {}
        record("页面状态预填(currentPage)",
               slots5.get("page") == "购物车"
               and (sources5.get("page")
                    or {}).get("origin")
               == "currentPage",
               str((slots5.get("page"),
                    sources5.get("page"))))

        # ⑥ 48号 turns 纯读取零写入
        from repositories.xiaozhu_repository \
            import Xiaozhu48Repository
        turns = await Xiaozhu48Repository(
        ).list_turns(sid, limit=50)
        record("48号 turns 纯读取(1 条保持)",
               len(turns) == 1
               and (turns[0].get("card")
                    or {}).get("subject")
               == "飞天茅台",
               str(len(turns)))
        os.environ["II58_MODE"] = "off"


class TestTemplates:
    """03 合规模板关联"""

    async def run(self):
        print("[03 合规模板]")
        reset_all()
        from services.ii58_compliance import (
            COMPLIANCE_TEMPLATES,
            templates_covered,
        )

        # 启动自检式断言: 12 意图全覆盖
        coverage = templates_covered()
        record("模板全覆盖(12 意图 10 模板)",
               coverage["covered"] is True
               and coverage["templates"] == 10,
               str(coverage))

        # 模板结构
        t = COMPLIANCE_TEMPLATES.get(
            "trust_convert") or {}
        record("trust_convert 模板(二次确认)",
               "二次确认" in t.get("valueTags")
               and any("屏幕码" in g for g in
                       t.get("guardrails")),
               str(t.get("valueTags")))

        # 边界拒绝话术
        b = COMPLIANCE_TEMPLATES.get(
            "boundary_reject") or {}
        record("boundary_reject 拒绝话术",
               bool(b.get("refusal")),
               str(b.get("refusal"))[:30])

        # resolved+allow 携带模板
        from services.ii58_service import (
            Ii58Service,
        )
        os.environ["II58_MODE"] = "shadow"
        await seed_corpus(
            "product.price_query", "多少钱")
        r = await Ii58Service().evaluate("多少钱")
        comp = r.get("compliance") or {}
        tpl = comp.get("template") or {}
        record("allow 携带模板+valueTags",
               tpl.get("label") == "产品侧政策"
               and bool(tpl.get("valueTags")),
               str(tpl.get("label")))

        # 非交付域无模板
        r2 = await Ii58Service().evaluate(
            "无关文本")
        record("clarify 无模板(无交付)",
               "compliance" not in r2,
               str(r2.get("state")))
        os.environ["II58_MODE"] = "off"


class TestThreshold:
    """04 阈值配置域"""

    async def run(self):
        print("[04 阈值配置域]")
        reset_all()
        from services.ii58_service import (
            Ii58Service,
        )
        svc = Ii58Service()

        # off 拒绝(决策面)
        try:
            await svc.calibrate(0.92, 0.72, "测试")
            ok, err = False, "未拒绝"
        except ValueError as e:
            ok, err = "off" in str(e), str(e)[:30]
        record("off 态校准拒绝", ok, err)

        os.environ["II58_MODE"] = "shadow"

        # 参数校验
        for upper, lower, tag in (
                (0.7, 0.7, "lower≥upper"),
                (1.5, 0.7, "upper 越界"),
                (0.9, 0.4, "lower 越界")):
            try:
                await svc.calibrate(upper, lower,
                                    "测试")
                ok, err = False, "未拒绝"
            except ValueError as e:
                ok, err = "非法" in str(e), \
                    str(e)[:30]
            record(f"阈值域校验({tag})",
                   ok, err)
        try:
            await svc.calibrate(0.92, 0.72, "")
            ok, err = False, "未拒绝"
        except ValueError as e:
            ok, err = "理由" in str(e), str(e)[:30]
        record("理由必填校验", ok, err)

        # 46号前置: sync 档案
        from services.ai_governance_service \
            import AiGovernanceService
        gov = AiGovernanceService()
        if await gov.repo.get_gov(
                "intent_orchestration") is None:
            await gov.sync_registry()

        # 申请→pending 不生效
        r = await svc.calibrate(
            0.92, 0.72, "P2 校准测试")
        record("校准申请(pending+changeId)",
               r.get("status") == "pending"
               and int(r.get("changeId")
                       or 0) > 0,
               str(r.get("status")))
        record("申请不生效(effective 旧基线)",
               (r.get("effective")
                or {}).get("upper") == 0.9,
               str(r.get("effective")))

        # 46号 change 留痕(pending)
        changes = await gov.list_changes(
            scorer_id="intent_orchestration")
        matching = [
            c for c in changes.get("changes") or []
            if (c.get("payload") or {}).get(
                "scope") == "threshold_baseline"]
        record("46号留痕(config pending)",
               len(matching) == 1
               and matching[0].get("status")
               == "pending",
               str(len(matching)))

        # 重复 pending 拒绝
        try:
            await svc.calibrate(
                0.95, 0.75, "重复申请")
            ok, err = False, "未拒绝"
        except ValueError as e:
            ok, err = "先处置" in str(e), \
                str(e)[:30]
        record("重复申请拒绝(队列纪律)",
               ok, err)

        # 审批前 evaluate 仍走旧基线
        await seed_corpus(
            "product.price_query", "多少钱")
        ev = await svc.evaluate("多少钱")
        thresholds = (ev.get("attribution")
                      or {}).get(
            "thresholds") or {}
        record("审批前旧基线(0.9)",
               thresholds.get("upper") == 0.9,
               str(thresholds))

        # off 态终审亦可用(人工铁律)
        os.environ["II58_MODE"] = "off"
        rv = await svc.review_calibration(
            int(r.get("changeId")), approve=True,
            reviewer="admin")
        record("off 态终审亦可用(铁律)",
               rv.get("status") == "active",
               str(rv.get("status")))

        # 终审后生效
        os.environ["II58_MODE"] = "shadow"
        ev2 = await svc.evaluate("多少钱")
        thresholds2 = (ev2.get("attribution")
                       or {}).get(
            "thresholds") or {}
        record("终审后新基线(0.92)",
               thresholds2.get("upper") == 0.92,
               str(thresholds2))

        # 46号 change 已收口(离开 pending)
        changes2 = await gov.list_changes(
            scorer_id="intent_orchestration")
        pending_left = [
            c for c in
            changes2.get("changes") or []
            if c.get("status") == "pending"]
        record("46号队列收口(无 pending)",
               len(pending_left) == 0,
               str(len(pending_left)))

        # 驳回流
        r2 = await svc.calibrate(
            0.95, 0.75, "驳回测试")
        os.environ["II58_MODE"] = "off"
        rv2 = await svc.review_calibration(
            int(r2.get("changeId")),
            approve=False, note="不采纳")
        os.environ["II58_MODE"] = "shadow"
        record("驳回(rejected 不生效)",
               rv2.get("status") == "rejected"
               and (rv2.get("effective")
                    or {}).get("upper") == 0.92,
               str(rv2.get("status")))

        # 终审后新申请可提(队列已清)
        r3 = await svc.calibrate(
            0.93, 0.73, "队列复用测试")
        record("队列复用(驳回后再提)",
               r3.get("status") == "pending",
               str(r3.get("status")))
        await svc.review_calibration(
            int(r3.get("changeId")),
            approve=False)

        # changeId 不匹配
        r4 = await svc.calibrate(
            0.91, 0.71, "不匹配测试")
        try:
            await svc.review_calibration(
                int(r4.get("changeId")) + 999,
                approve=True)
            ok, err = False, "未拒绝"
        except ValueError as e:
            ok, err = "不匹配" in str(e), \
                str(e)[:30]
        record("changeId 不匹配拒绝", ok, err)
        await svc.review_calibration(
            int(r4.get("changeId")),
            approve=False)

        # 无 pending 终审 404(KeyError)
        try:
            await svc.review_calibration(
                999, approve=True)
            ok, err = False, "未拒绝"
        except KeyError:
            ok, err = True, ""
        record("无待终审 404", ok, err)

        # thresholds 全景(mirrorStatus=最后一次
        # 终审态; 有效基线=baseUpper 持续生效)
        view = await svc.thresholds_view()
        baseline = view.get("baseline") or {}
        by_tier = view.get("byTier") or {}
        record("阈值全景(基线+来源)",
               baseline.get("upper") == 0.92
               and baseline.get("source")
               == "mirror",
               str(baseline))
        record("byTier 四档(tier delta 计算)",
               set(by_tier) == {
                   "trusted", "standard",
                   "watched", "restricted"}
               and by_tier.get("trusted", {})
               .get("upper") == 0.87,
               str(by_tier.get("trusted")))

        # 事件留痕
        from repositories.ii58_repository import (
            Ii58Repository,
        )
        events = await Ii58Repository(
        ).list_events(
            event_type="threshold_change",
            limit=50)
        actions = [(e.get("detail")
                    or {}).get("action")
                   for e in events]
        record("threshold_change 事件链",
               "submit" in actions
               and "approve" in actions
               and "reject" in actions,
               str(actions[:6]))
        os.environ["II58_MODE"] = "off"


class TestHttp:
    """05 HTTP 层"""

    async def run(self):
        print("[05 HTTP]")
        reset_all()
        from fastapi.testclient import TestClient
        from main import app
        client = TestClient(app)
        admin = {"X-Role": "admin"}

        # 决策面 off 409
        resp = client.post(
            "/api/ii58/threshold/calibrate",
            json={"upper": 0.92, "lower": 0.72,
                  "reason": "测试"},
            headers=admin)
        record("HTTP calibrate off 409",
               resp.status_code == 409,
               str(resp.status_code))

        # 观测面 off 可用
        resp = client.get("/api/ii58/thresholds",
                          headers=admin)
        body = resp.json() or {}
        record("HTTP thresholds 观测面 200",
               resp.status_code == 200
               and (body.get("baseline")
                    or {}).get("source")
               == "code_default",
               str((resp.status_code,
                    (body.get("baseline")
                     or {}).get("source"))))

        # shadow 全链
        os.environ["II58_MODE"] = "shadow"
        await seed_corpus(
            "product.price_query", "多少钱")

        # 46号 sync
        from services.ai_governance_service \
            import AiGovernanceService
        gov = AiGovernanceService()
        if await gov.repo.get_gov(
                "intent_orchestration") is None:
            await gov.sync_registry()

        # 越界样例(HTTP evaluate guest)
        resp = client.post(
            "/api/ii58/evaluate",
            json={"text": "多少钱",
                  "memberRole": "guest"},
            headers=admin)
        body = resp.json() or {}
        record("HTTP evaluate 越界样例",
               resp.status_code == 200
               and body.get(
                   "boundaryIntercepted") is True
               and body.get("intentId")
               == "boundary.unauthorized",
               str((resp.status_code,
                    body.get("intentId"))))

        # 槽位预填(HTTP evaluate sessionId)
        await seed_xiaozhu_turn_with_subject(
            88, "五粮液")
        resp = client.post(
            "/api/ii58/evaluate",
            json={"text": "这个多少钱",
                  "sessionId": 88},
            headers=admin)
        body = resp.json() or {}
        record("HTTP 预填(sessionId)",
               (body.get("slots")
                or {}).get("keyword") == "五粮液",
               str(body.get("slots")))

        # calibrate 申请
        resp = client.post(
            "/api/ii58/threshold/calibrate",
            json={"upper": 0.88, "lower": 0.68,
                  "reason": "HTTP 测试"},
            headers=admin)
        body = resp.json() or {}
        cid = body.get("changeId")
        record("HTTP calibrate 200(pending)",
               resp.status_code == 200
               and body.get("status")
               == "pending",
               str((resp.status_code,
                    body.get("status"))))

        # thresholds pending 呈现
        resp = client.get(
            "/api/ii58/thresholds",
            headers=admin)
        body = resp.json() or {}
        record("HTTP thresholds pending 呈现",
               (body.get("pending") or {})
               .get("changeId") == cid,
               str(body.get("pending")))

        # off 态终审亦可用(人工铁律)
        os.environ["II58_MODE"] = "off"
        resp = client.post(
            "/api/ii58/threshold/calibrate",
            json={"changeId": cid, "approve": True,
                  "reviewer": "admin"},
            headers=admin)
        body = resp.json() or {}
        record("HTTP 终审 200(off 亦可用)",
               resp.status_code == 200
               and body.get("status") == "active",
               str((resp.status_code,
                    body.get("status"))))

        # 生效验证(evaluate 归因阈值)
        os.environ["II58_MODE"] = "shadow"
        resp = client.post(
            "/api/ii58/evaluate",
            json={"text": "多少钱"},
            headers=admin)
        body = resp.json() or {}
        record("HTTP 生效(evaluate 新基线)",
               ((body.get("attribution")
                 or {}).get("thresholds")
                or {}).get("upper") == 0.88,
               str((body.get("attribution")
                    or {}).get(
                   "thresholds")))

        # 409(阈值域非法)
        os.environ["II58_MODE"] = "shadow"
        resp = client.post(
            "/api/ii58/threshold/calibrate",
            json={"upper": 0.5, "lower": 0.5,
                  "reason": "非法"},
            headers=admin)
        record("HTTP 阈值域非法 409",
               resp.status_code == 409,
               str(resp.status_code))

        # 404(无待终审)
        resp = client.post(
            "/api/ii58/threshold/calibrate",
            json={"changeId": 999,
                  "approve": True},
            headers=admin)
        record("HTTP 终审无 pending 404",
               resp.status_code == 404,
               str(resp.status_code))

        # 鉴权 403
        for method, path in (
                ("POST",
                 "/api/ii58/threshold/calibrate"),
                ("GET", "/api/ii58/thresholds")):
            resp = client.request(
                method, path, json={})
            record(f"HTTP {path.split('/')[-1]}"
                   f" 无 Role 403",
                   resp.status_code == 403,
                   str(resp.status_code))

        # 路由累计 13 端点(P3 扩至 16——基线语义)
        from routes.ii58_routes import (
            router as ii_router,
        )
        count = sum(
            1 for r in ii_router.routes)
        record("58号路由累计 ≥13 端点",
               count >= 13, str(count))
        os.environ["II58_MODE"] = "off"


async def run_all():
    await TestCompliance().run()
    await TestSlotPrefill().run()
    await TestTemplates().run()
    await TestThreshold().run()
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
