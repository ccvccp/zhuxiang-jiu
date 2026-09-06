"""58号·AI智能优化意图识别模块 P3 专项测试
(反馈闭环+主动学习)

运行方式:
    python test_ii58_p3.py

覆盖(58号计划 §九 P3):
    - 显式反馈: 会员面 assist 门槛+短期表单
      (evalId+意图重选+自由文本)+高优先级
      入标注队列+PII 脱敏+属主校验
    - 隐式反馈转化: 48号 failures 三 kind
      纯读取→feedback 表+标注队列+去重
    - 主动学习: 0.4-0.7 区间自动入队
      (入队≠生效)+去重
    - 标注终审: decide 语料回流四类归类
      (ingest pending→review 联动 active
      ——优化永不自动生效)+驳回+off 态
      终审铁律
    - HTTP 层: 3 新端点+鉴权+16 端点计数
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


async def seed_corpus(intent_id: str, text: str
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
        "sampleType": "positive",
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


async def seed_xiaozhu_failure(kind: str,
                               raw_text: str
                               ) -> int:
    """种 48号 failure(隐式反馈输入)"""
    from core.helpers import ts
    from repositories.xiaozhu_repository import (
        Xiaozhu48Repository,
    )
    repo = Xiaozhu48Repository()
    case_id = await repo._next_id(
        "voice48_failures")
    await repo.save_record(
        "voice48_failures", {
            "caseId": case_id,
            "sessionId": 9,
            "memberId": 1,
            "rawText": raw_text,
            "kind": kind,
            "ts": ts(),
        })
    return case_id


class TestExplicitFeedback:
    """01 显式反馈(会员面)"""

    async def run(self):
        print("[01 显式反馈]")
        reset_all()
        from services.ii58_service import (
            Ii58Service,
        )
        from services.ii58_feedback_service import (
            Ii58FeedbackService,
        )
        svc = Ii58FeedbackService()

        os.environ["II58_MODE"] = "shadow"
        await seed_corpus(
            "product.price_query", "多少钱")
        ev = await Ii58Service().evaluate(
            "多少钱", member_id=1)

        # 会员面门槛(off/shadow 409)
        for mode in ("off", "shadow"):
            os.environ["II58_MODE"] = mode
            try:
                await svc.submit_feedback(
                    member_id=1,
                    eval_id=ev["evalId"],
                    text="不对")
                ok, err = False, "未拒绝"
            except ValueError as e:
                ok, err = "assist" in str(e), \
                    str(e)[:30]
            record(f"会员面门槛({mode} 409)",
                   ok, err)

        # assist 合法域
        os.environ["II58_MODE"] = "assist"

        # eval 404
        try:
            await svc.submit_feedback(
                member_id=1, eval_id=999,
                text="不对")
            ok, err = False, "未拒绝"
        except KeyError:
            ok, err = True, ""
        record("识别记录 404", ok, err)

        # 属主不匹配(eval.memberId=2)
        ev2 = await Ii58Service().evaluate(
            "多少钱", member_id=2)
        try:
            await svc.submit_feedback(
                member_id=1,
                eval_id=ev2["evalId"],
                text="不对")
            ok, err = False, "未拒绝"
        except ValueError as e:
            ok, err = "属主" in str(e), \
                str(e)[:30]
        record("属主不匹配拒绝", ok, err)

        # 重选意图不在册
        try:
            await svc.submit_feedback(
                member_id=1,
                eval_id=ev["evalId"],
                text="不对",
                corrected_intent_id="hack.intent")
            ok, err = False, "未拒绝"
        except ValueError as e:
            ok, err = "不在册" in str(e), \
                str(e)[:30]
        record("重选意图不在册拒绝", ok, err)

        # 合法提交(高优先级入队)
        r = await svc.submit_feedback(
            member_id=1,
            eval_id=ev["evalId"],
            text="手机 13800138000 查错了",
            corrected_intent_id=(
                "product.new_query"),
            note="其实是问新品")
        record("反馈受理(feedbackId+labelId)",
               int(r.get("feedbackId") or 0) > 0
               and int(r.get("labelId") or 0) > 0
               and r.get("status") == "pending",
               str((r.get("feedbackId"),
                    r.get("labelId"))))

        # feedback 记录结构
        from repositories.ii58_repository import (
            Ii58Repository,
        )
        repo = Ii58Repository()
        fb = await repo.get_feedback(
            r.get("feedbackId"))
        record("feedback 结构(kind=explicit)",
               fb.get("kind") == "explicit"
               and fb.get("correctedIntentId")
               == "product.new_query"
               and fb.get("memberId") == 1,
               str((fb.get("kind"),
                    fb.get(
                        "correctedIntentId"))))

        # PII 脱敏
        record("PII 脱敏(反馈文本)",
               "13800138000" not in str(
                   fb.get("text")),
               str(fb.get("text"))[:40])

        # label 高优先级
        lb = await repo.get_label(
            r.get("labelId"))
        record("label 高优先入队(explicit)",
               lb.get("source")
               == "explicit_feedback"
               and lb.get("priority") == "high"
               and lb.get(
                   "suggestedIntentId")
               == "product.price_query"
               and lb.get(
                   "correctedIntentId")
               == "product.new_query",
               str((lb.get("source"),
                    lb.get("priority"))))

        # 事件留痕
        events = await repo.list_events(
            event_type="feedback", limit=10)
        record("feedback 事件留痕",
               len(events) == 1
               and (events[0].get("detail")
                    or {}).get("kind")
               == "explicit",
               str(len(events)))
        os.environ["II58_MODE"] = "off"


class TestImplicitFeedback:
    """02 隐式反馈转化(48号 failures)"""

    async def run(self):
        print("[02 隐式反馈转化]")
        reset_all()
        from services.ii58_feedback_service import (
            Ii58FeedbackService,
        )
        svc = Ii58FeedbackService()

        # off 拒绝(决策面)
        try:
            await svc.mine_implicit()
            ok, err = False, "未拒绝"
        except ValueError as e:
            ok, err = "off" in str(e), str(e)[:30]
        record("off 态隐式转化拒绝", ok, err)

        os.environ["II58_MODE"] = "shadow"
        await seed_xiaozhu_failure(
            "negative", "这个回答不对")
        await seed_xiaozhu_failure(
            "fallback", "随便说点什么")
        await seed_xiaozhu_failure(
            "repeat", "再来一次")

        r = await svc.mine_implicit()
        record("三 kind 转化(3 条)",
               r.get("converted") == 3,
               str(r.get("converted")))
        record("byKind 统计",
               (r.get("byKind") or {})
               == {"negative": 1,
                   "fallback": 1,
                   "repeat": 1},
               str(r.get("byKind")))

        # feedback 结构+优先级
        from repositories.ii58_repository import (
            Ii58Repository,
        )
        repo = Ii58Repository()
        fbs = await repo.list_feedback(
            kind="implicit", limit=10)
        record("feedback kind=implicit(3)",
               len(fbs) == 3
               and all(
                   str(f.get("originRef"))
                   for f in fbs),
               str(len(fbs)))
        labels = await repo.list_labels(
            status="pending", limit=10)
        sources = sorted(
            str(lb.get("source"))
            for lb in labels)
        record("labels 入队(三来源)",
               sources == [
                   "implicit_fallback",
                   "implicit_negative",
                   "implicit_repeat"],
               str(sources))
        neg_lb = [lb for lb in labels
                  if lb.get("source")
                  == "implicit_negative"]
        record("negative 高优先(隐式)",
               neg_lb and neg_lb[0].get(
                   "priority") == "high",
               str(neg_lb[0].get("priority")
                   if neg_lb else None))

        # 去重(重复转化 0 新增)
        r2 = await svc.mine_implicit()
        record("重复转化去重(0 新增)",
               r2.get("converted") == 0,
               str(r2.get("converted")))

        # 48号 failures 纯读取
        from repositories.xiaozhu_repository \
            import Xiaozhu48Repository
        failures = await (
            Xiaozhu48Repository().list_records(
                "voice48_failures", limit=10))
        record("48号 failures 纯读取(3 保持)",
               len(failures) == 3,
               str(len(failures)))
        os.environ["II58_MODE"] = "off"


class TestAutoEnqueue:
    """03 主动学习自动入队"""

    async def run(self):
        print("[03 主动学习入队]")
        reset_all()
        from services.ii58_service import (
            Ii58Service,
        )
        from repositories.ii58_repository import (
            Ii58Repository,
        )
        repo = Ii58Repository()
        os.environ["II58_MODE"] = "shadow"

        # 语料: 高置信域+低置信构造域
        await seed_corpus(
            "product.price_query", "多少钱")
        await seed_corpus(
            "trust.balance_query", "余额查询")

        # ① 低置信区间(0.6 PARTIAL)→自动入队
        r = await Ii58Service().evaluate(
            "帮我查一下余额情况")
        record("低置信区间(0.4≤c<0.7)",
               0.4 <= (r.get("confidence")
                       or 0) < 0.7,
               str(r.get("confidence")))
        labels = await repo.list_labels(
            status="pending", limit=10)
        record("自动入队(source=auto_ambiguity)",
               len(labels) == 1
               and labels[0].get("source")
               == "auto_ambiguity"
               and labels[0].get(
                   "suggestedIntentId")
               == "trust.balance_query",
               str([(l.get("source"),
                     l.get(
                         "suggestedIntentId"))
                    for l in labels]))

        # ② 重复 evaluate 去重(同文本 pending)
        await Ii58Service().evaluate(
            "帮我查一下余额情况")
        labels2 = await repo.list_labels(
            status="pending", limit=10)
        record("重复 evaluate 去重",
               len(labels2) == 1,
               str(len(labels2)))

        # ③ 高置信(resolved)不入队
        await Ii58Service().evaluate("多少钱")
        labels3 = await repo.list_labels(
            status="pending", limit=10)
        record("高置信(resolved)不入队",
               len(labels3) == 1,
               str(len(labels3)))

        # ④ 零命中(<0.4)不入队
        await Ii58Service().evaluate(
            "今天天气如何")
        labels4 = await repo.list_labels(
            status="pending", limit=10)
        record("零命中(<0.4)不入队",
               len(labels4) == 1,
               str(len(labels4)))

        # ⑤ 入队≠生效(语料库无新增)
        corpus = await repo.list_corpus(
            limit=100)
        record("入队≠生效(语料库 2 不变)",
               len(corpus) == 2,
               str(len(corpus)))
        os.environ["II58_MODE"] = "off"


class TestDecide:
    """04 标注终审+语料回流"""

    async def run(self):
        print("[04 标注终审]")
        reset_all()
        from services.ii58_service import (
            Ii58Service,
        )
        from services.ii58_feedback_service import (
            Ii58FeedbackService,
        )
        svc = Ii58FeedbackService()
        os.environ["II58_MODE"] = "assist"

        await seed_corpus(
            "product.price_query", "多少钱")
        ev = await Ii58Service().evaluate(
            "多少钱", member_id=1)
        r = await svc.submit_feedback(
            member_id=1,
            eval_id=ev["evalId"],
            text="问的是新品价格",
            corrected_intent_id=(
                "product.new_query"))
        label_id = r.get("labelId")

        # 404
        try:
            await svc.decide(999, approve=True)
            ok, err = False, "未拒绝"
        except KeyError:
            ok, err = True, ""
        record("decide 404", ok, err)

        # off 态终审亦可用(人工铁律)——approve
        os.environ["II58_MODE"] = "off"
        rv = await svc.decide(
            label_id, approve=True,
            reviewer="annotator",
            note="修正为新品咨询")
        record("off 态终审亦可用(铁律)",
               rv.get("status") == "approved",
               str(rv.get("status")))
        record("语料回流(active 生效)",
               (rv.get("reflow") or {})
               .get("status") == "active"
               and int((rv.get("reflow")
                        or {}).get("corpusId")
                       or 0) > 0,
               str(rv.get("reflow")))

        # 回流语料结构
        from repositories.ii58_repository import (
            Ii58Repository,
        )
        repo = Ii58Repository()
        corpus = await repo.list_corpus(
            status="active", limit=10)
        reflowed = [c for c in corpus
                    if c.get("source")
                    == "label_reflow"]
        record("回流语料(修正意图+人工验证)",
               len(reflowed) == 1
               and reflowed[0].get("intentId")
               == "product.new_query"
               and reflowed[0].get(
                   "humanVerified") is True,
               str([(c.get("intentId"),
                     c.get("source"))
                    for c in corpus]))

        # label 状态翻转+留痕
        lb = await repo.get_label(label_id)
        record("label approved(状态机)",
               lb.get("status") == "approved"
               and lb.get("reviewer")
               == "annotator",
               str((lb.get("status"),
                    lb.get("reviewer"))))

        # 重复裁决拒绝(状态机)
        try:
            await svc.decide(label_id,
                             approve=True)
            ok, err = False, "未拒绝"
        except ValueError as e:
            ok, err = "已裁决" in str(e), \
                str(e)[:30]
        record("重复裁决拒绝(状态机)", ok, err)

        # 回流类型归类(negative)
        os.environ["II58_MODE"] = "assist"
        ev2 = await Ii58Service().evaluate(
            "多少钱", member_id=1)
        r2 = await svc.submit_feedback(
            member_id=1,
            eval_id=ev2["evalId"],
            text="价格查询失败案例")
        rv2 = await svc.decide(
            r2.get("labelId"), approve=True,
            target_sample_type="negative")
        neg_corpus = await repo.list_corpus(
            sample_type="negative",
            status="active", limit=10)
        record("回流类型归类(negative)",
               (rv2.get("reflow") or {})
               .get("sampleType") == "negative"
               and len(neg_corpus) == 1,
               str(len(neg_corpus)))

        # 驳回流
        ev3 = await Ii58Service().evaluate(
            "多少钱", member_id=1)
        r3 = await svc.submit_feedback(
            member_id=1,
            eval_id=ev3["evalId"],
            text="驳回样本文本")
        before = len(await repo.list_corpus(
            limit=100))
        rv3 = await svc.decide(
            r3.get("labelId"),
            approve=False, note="无效反馈")
        after = len(await repo.list_corpus(
            limit=100))
        record("驳回(不回流语料)",
               rv3.get("status") == "rejected"
               and before == after,
               str((rv3.get("status"),
                    before, after)))
        lb3 = await repo.get_label(
            r3.get("labelId"))
        record("驳回留痕(label rejected)",
               lb3.get("status") == "rejected",
               str(lb3.get("status")))

        # 非法回流类型拒绝
        ev4 = await Ii58Service().evaluate(
            "多少钱", member_id=1)
        r4 = await svc.submit_feedback(
            member_id=1,
            eval_id=ev4["evalId"],
            text="非法类型样本")
        try:
            await svc.decide(
                r4.get("labelId"),
                approve=True,
                target_sample_type="poison")
            ok, err = False, "未拒绝"
        except ValueError as e:
            ok, err = "非法" in str(e), \
                str(e)[:30]
        record("非法回流类型拒绝", ok, err)
        await svc.decide(
            r4.get("labelId"),
            approve=False)

        # 事件留痕(label approve/reject)
        events = await repo.list_events(
            event_type="label", limit=20)
        actions = sorted(
            (e.get("detail") or {}).get(
                "action")
            for e in events)
        record("label 事件留痕",
               "approve" in actions
               and "reject" in actions,
               str(actions))
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
        member = {"X-Member-Id": "1"}

        # 种子: assist 评估
        os.environ["II58_MODE"] = "assist"
        await seed_corpus(
            "product.price_query", "多少钱")
        resp = client.post(
            "/api/ii58/evaluate",
            json={"text": "多少钱",
                  "memberId": 1},
            headers=admin)
        eval_id = (resp.json() or {}
                   ).get("evalId")

        # 会员面门槛(off/shadow 409)
        for mode in ("off", "shadow"):
            os.environ["II58_MODE"] = mode
            resp = client.post(
                "/api/ii58/feedback",
                json={"evalId": eval_id,
                      "text": "不对"},
                headers=member)
            record(f"HTTP feedback {mode} 409",
                   resp.status_code == 409,
                   str(resp.status_code))

        # 无 X-Member-Id 403
        os.environ["II58_MODE"] = "assist"
        resp = client.post(
            "/api/ii58/feedback",
            json={"evalId": eval_id,
                  "text": "不对"})
        record("HTTP feedback 无 Member 403",
               resp.status_code == 403,
               str(resp.status_code))

        # assist 合法提交
        resp = client.post(
            "/api/ii58/feedback",
            json={"evalId": eval_id,
                  "text": "不对",
                  "correctedIntentId":
                      "product.new_query"},
            headers=member)
        body = resp.json() or {}
        record("HTTP feedback 200(assist)",
               resp.status_code == 200
               and int(body.get("labelId")
                       or 0) > 0,
               str((resp.status_code,
                    body.get("labelId"))))
        label_id = body.get("labelId")

        # 404(eval 不存在)
        resp = client.post(
            "/api/ii58/feedback",
            json={"evalId": 999, "text": "x"},
            headers=member)
        record("HTTP feedback 404",
               resp.status_code == 404,
               str(resp.status_code))

        # labels 观测面(off 可用)
        os.environ["II58_MODE"] = "off"
        resp = client.get(
            "/api/ii58/labels",
            headers=admin)
        body = resp.json() or {}
        record("HTTP labels 观测面 200",
               resp.status_code == 200
               and (body.get("total") or 0)
               >= 1
               and "explicit_feedback"
               in (body.get("bySource")
                   or {}),
               str((resp.status_code,
                    body.get("total"))))

        # decide off 亦可用(终审铁律)
        resp = client.post(
            f"/api/ii58/labels/{label_id}"
            f"/decide",
            json={"approve": True,
                  "reviewer": "admin"},
            headers=admin)
        body = resp.json() or {}
        record("HTTP decide off 200(铁律)",
               resp.status_code == 200
               and (body.get("reflow")
                    or {}).get("status")
               == "active",
               str((resp.status_code,
                    (body.get("reflow")
                     or {}).get(
                        "status"))))

        # decide 404
        resp = client.post(
            "/api/ii58/labels/999/decide",
            json={"approve": True},
            headers=admin)
        record("HTTP decide 404",
               resp.status_code == 404,
               str(resp.status_code))

        # 鉴权 403
        for method, path, hdrs in (
                ("POST", "/api/ii58/feedback",
                 None),
                ("GET", "/api/ii58/labels",
                 None),
                ("POST",
                 "/api/ii58/labels/1/decide",
                 None)):
            resp = client.request(
                method, path, json={},
                headers=hdrs)
            record(f"HTTP {path.split('/')[-1]}"
                   f" 无鉴权 403",
                   resp.status_code == 403,
                   str(resp.status_code))

        # 路由累计 16 端点(P4 扩至 17——基线语义)
        from routes.ii58_routes import (
            router as ii_router,
        )
        count = sum(
            1 for r in ii_router.routes)
        record("58号路由累计 ≥16 端点",
               count >= 16, str(count))
        os.environ["II58_MODE"] = "off"


async def run_all():
    await TestExplicitFeedback().run()
    await TestImplicitFeedback().run()
    await TestAutoEnqueue().run()
    await TestDecide().run()
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
