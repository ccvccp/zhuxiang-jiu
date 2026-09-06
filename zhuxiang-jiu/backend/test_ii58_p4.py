"""58号·AI智能优化意图识别模块 P4 专项测试
(决策回流+信值联动+T+1 调度)

运行方式:
    python test_ii58_p4.py

覆盖(58号计划 §九 P4):
    - 六类真值信号: 终态判定七信号源
      (correct_executed/boundary_correct/
      adversarial_confusion/high_conf_error/
      weak_negative/coverage_gap/
      clarify_completed)+partial 非终态跳过
    - 44号池双写: submit_feedback 第33档案
      (reward+note 溯源)
    - 幂等: evaluationId 1:1
      (pooledFeedbackId 终态跳过)
    - 高置信错误预警: ≥3 →校准建议自动提交
      (pending 不生效——人工终审唯一出口)
    - T+1 调度器: 开关默认 off+手动触发轨
    - HTTP 层: collect 端点+鉴权+17 端点计数
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
os.environ.pop("II58_LEARN_MODE", None)

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


async def seed_adversarial(intent_id: str, text: str,
                           target: str) -> int:
    """种对抗语料(触发 adversarialPenalty)"""
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
        "sampleType": "adversarial",
        "text": text,
        "weight": 1.0,
        "source": "manual",
        "originRef": "",
        "confusableTarget": target,
        "humanVerified": True,
        "humanSuggested": False,
        "status": "active",
        "createdAt": ts(),
        "updatedAt": ts(),
    })
    return corpus_id


class TestCollectSignals:
    """01 六类真值信号判定+池双写"""

    async def run(self):
        print("[01 六类真值信号]")
        reset_all()
        from services.ii58_service import (
            Ii58Service,
        )
        from services.ii58_feedback_service import (
            Ii58FeedbackService,
        )
        from services.ii58_learn_service import (
            Ii58LearnService,
        )
        learn = Ii58LearnService()
        os.environ["II58_MODE"] = "shadow"

        # 语料: 高置信域+对抗域+竞争域
        await seed_corpus(
            "product.price_query", "多少钱")
        # 对抗触发前置: positive 命中后 adversarial
        # FULL 否决降权(adversarialPenalty 标记)
        await seed_corpus(
            "product.price_query", "修改价格")
        await seed_adversarial(
            "product.price_query", "修改价格",
            "product.new_query")
        await seed_corpus(
            "boundary.unauthorized",
            "删除所有会员数据")
        # partial 构造域(弱满足——中低置信反馈)
        await seed_corpus(
            "promo.query", "优惠多少")
        await seed_corpus(
            "promo.query", "优惠多少呀")
        await seed_corpus(
            "promo.query", "优惠多少呢")

        # ① resolved(识别正确+执行成功)
        ev1 = await Ii58Service().evaluate("多少钱")

        # ② 越界拦截(guest 角色)
        ev2 = await Ii58Service().evaluate(
            "删除所有会员数据",
            member_role="guest")

        # ③ 对抗混淆(FULL 命中对抗文本降权)
        ev3 = await Ii58Service().evaluate("修改价格")

        # ④ clarify(语料覆盖缺口)
        ev4 = await Ii58Service().evaluate(
            "完全无关文本")

        # ⑤ 高置信错误+⑥ 弱满足(显式反馈)
        os.environ["II58_MODE"] = "assist"
        ev5 = await Ii58Service().evaluate(
            "多少钱", member_id=5)
        await Ii58FeedbackService(
        ).submit_feedback(
            member_id=5, eval_id=ev5["evalId"],
            text="问的是新品",
            corrected_intent_id="product.new_query")
        # 弱满足: partial 态(中低置信)+显式纠正
        ev6 = await Ii58Service().evaluate(
            "优惠多少哈", member_id=6)
        await Ii58FeedbackService(
        ).submit_feedback(
            member_id=6, eval_id=ev6["evalId"],
            text="问的是优惠活动",
            corrected_intent_id="product.new_query")
        os.environ["II58_MODE"] = "shadow"

        # ⑦ partial+approved 标注(澄清后完成)
        from repositories.ii58_repository import (
            Ii58Repository,
        )
        repo = Ii58Repository()
        # 手工种 partial 识别记录+approved 标注
        eval_id_p = await repo.next_eval_id()
        await repo.save_evaluation({
            "evalId": eval_id_p,
            "text": "优惠多少哈",
            "intentId": "promo.query",
            "state": "partial", "confidence": 0.8,
            "candidates": [],
            "attribution": {
                "corpusIds": [1], "track": "corpus",
                "tier": "standard",
                "thresholds": {"upper": 0.9,
                               "lower": 0.7}},
            "slots": {},
            "memberId": 0, "memberRole": "member",
            "corpusHits": 1,
            "boundaryIntercepted": False,
            "pooledFeedbackId": 0, "evalCount": 0,
            "createdAt": "", "updatedAt": "",
        })
        label_id = await repo.next_label_id()
        await repo.save_label({
            "labelId": label_id,
            "evalId": eval_id_p, "feedbackId": 0,
            "memberId": 0, "source": "auto_ambiguity",
            "priority": "low", "text": "优惠多少哈",
            "suggestedIntentId": "promo.query",
            "correctedIntentId": "",
            "status": "approved", "reviewer": "admin",
            "decidedAt": "",
            "detail": {}, "createdAt": "",
        })

        # ⑧ partial 无标注(非终态跳过)
        eval_id_np = await repo.next_eval_id()
        await repo.save_evaluation({
            "evalId": eval_id_np,
            "text": "无标注 partial",
            "intentId": "promo.query",
            "state": "partial", "confidence": 0.6,
            "candidates": [],
            "attribution": {
                "corpusIds": [], "track": "corpus",
                "tier": "standard",
                "thresholds": {"upper": 0.9,
                               "lower": 0.7}},
            "slots": {},
            "memberId": 0, "memberRole": "member",
            "corpusHits": 1,
            "boundaryIntercepted": False,
            "pooledFeedbackId": 0, "evalCount": 0,
            "createdAt": "", "updatedAt": "",
        })

        # 44号 sync(池前置)
        from services.ai_governance_service \
            import AiGovernanceService
        gov = AiGovernanceService()
        if await gov.repo.get_gov(
                "intent_orchestration") is None:
            await gov.sync_registry()

        # collect(off 态回流管理面亦可用)
        os.environ["II58_MODE"] = "off"
        r = await learn.collect_feedback()
        signals = r.get("signals") or {}

        # 信号判定断言
        record("识别正确+执行成功(+1.0)",
               signals.get("correct_executed") == 1,
               str(signals))
        record("越界拦截正确(+0.6)",
               signals.get("boundary_correct") == 1,
               str(signals))
        record("对抗混淆命中(-0.6)",
               signals.get("adversarial_confusion")
               == 1,
               str(signals))
        record("澄清拒绝/覆盖缺口(-0.5)",
               signals.get("coverage_gap") == 1,
               str(signals))
        record("高置信错误(-0.8)",
               signals.get("high_conf_error") == 1,
               str(signals))
        record("弱满足(+0.3)",
               signals.get("weak_negative") == 1,
               str(signals))
        record("澄清后完成(+0.8)",
               signals.get("clarify_completed") == 1,
               str(signals))
        record("partial 无标注跳过(非终态)",
               r.get("skipped") == 1,
               str(r.get("skipped")))

        # 44号池双写验证
        from repositories.ai_learning_repository \
            import AiLearningRepository
        pool = AiLearningRepository()
        pool_fbs = await pool.list_feedback(
            "intent_orchestration", limit=100)
        notes = [str(f.get("note") or "")
                 for f in pool_fbs]
        rewards = {f.get("reward") for f
                   in pool_fbs}
        record("44号池双写(第33档案 7 条)",
               len(pool_fbs) == 7,
               str(len(pool_fbs)))
        record("池 reward 域(七信号值)",
               {1.0, 0.6, -0.6, -0.5, -0.8,
                0.3, 0.8}.issubset(rewards),
               str(sorted(rewards)))
        record("池 note 溯源(evalId)",
               any(":evalId=" in n for n in notes),
               str(notes[:2]))

        # evaluations 回写(pooled 标记)
        stored1 = await repo.get_evaluation(
            ev1["evalId"])
        record("pooled 回写(标记+信号+reward)",
               stored1.get("pooledFeedbackId") > 0
               and stored1.get("poolSignal")
               == "correct_executed"
               and stored1.get("poolReward") == 1.0,
               str((stored1.get("poolSignal"),
                    stored1.get("poolReward"))))

        # 幂等(重复 collect 0 新增)
        r2 = await learn.collect_feedback()
        record("幂等(重复 collect 0 新增)",
               r2.get("labeled") == 0
               and (r2.get("skipped") or 0) >= 7,
               str((r2.get("labeled"),
                    r2.get("skipped"))))
        pool2 = await pool.list_feedback(
            "intent_orchestration", limit=100)
        record("池幂等(仍 7 条)",
               len(pool2) == 7,
               str(len(pool2)))

        # learn_signal 事件留痕
        events = await repo.list_events(
            event_type="learn_signal", limit=20)
        record("learn_signal 事件留痕",
               len(events) == 7,
               str(len(events)))


class TestCalibrationAlert:
    """02 高置信错误校准预警"""

    async def run(self):
        print("[02 校准预警]")
        reset_all()
        from services.ii58_service import (
            Ii58Service,
        )
        from services.ii58_feedback_service import (
            Ii58FeedbackService,
        )
        from services.ii58_learn_service import (
            CALIBRATION_ALERT_STEP,
            Ii58LearnService,
        )
        from repositories.ii58_repository import (
            Ii58Repository,
        )
        repo = Ii58Repository()
        learn = Ii58LearnService()

        # 种 3 条高置信错误(resolved+显式纠正)
        os.environ["II58_MODE"] = "assist"
        await seed_corpus(
            "product.price_query", "多少钱")
        svc = Ii58Service()
        for i in (1, 2, 3):
            ev = await svc.evaluate(
                "多少钱", member_id=i)
            await Ii58FeedbackService(
            ).submit_feedback(
                member_id=i, eval_id=ev["evalId"],
                text=f"纠正{i}",
                corrected_intent_id=(
                    "product.new_query"))

        # 44号 sync
        from services.ai_governance_service \
            import AiGovernanceService
        gov = AiGovernanceService()
        if await gov.repo.get_gov(
                "intent_orchestration") is None:
            await gov.sync_registry()

        os.environ["II58_MODE"] = "off"
        r = await learn.collect_feedback()
        signals = r.get("signals") or {}
        alert = r.get("calibrationAlert") or {}

        record("3 条高置信错误判定",
               signals.get("high_conf_error") == 3,
               str(signals))
        record("预警触发(triggered)",
               alert.get("triggered") is True
               and alert.get("highConfErrors") == 3,
               str(alert)[:60])
        record("预警建议(upper+0.02)",
               alert.get("proposedUpper") == 0.92,
               str(alert.get("proposedUpper")))

        # 建议不直接生效(基线仍 0.9)
        view = await svc.thresholds_view()
        record("预警不生效(基线 0.9 不变)",
               (view.get("baseline")
                or {}).get("upper") == 0.9,
               str(view.get("baseline")))

        # 镜像 pending+46号留痕
        mirror = await repo.get_threshold(
            "baseline")
        record("镜像 pending(changeId)",
               mirror.get("status") == "pending"
               and int(mirror.get("changeId")
                       or 0) > 0,
               str(mirror.get("status")))
        changes = await gov.list_changes(
            scorer_id="intent_orchestration")
        cfg = [c for c in changes.get("changes")
               or [] if (c.get("payload")
                         or {}).get("scope")
               == "threshold_baseline"]
        record("46号留痕(预警建议 config)",
               len(cfg) == 1
               and cfg[0].get("status") == "pending",
               str(len(cfg)))

        # 阈值预警步长常量
        record("预警步长(0.02)",
               CALIBRATION_ALERT_STEP == 0.02,
               str(CALIBRATION_ALERT_STEP))

        # 幂等(队列纪律): 再种 3 条高置信错误
        # →第二轮 collect 达阈值但 pending 占用 → skip
        os.environ["II58_MODE"] = "assist"
        for i in (7, 8, 9):
            ev = await svc.evaluate(
                "多少钱", member_id=i)
            await Ii58FeedbackService(
            ).submit_feedback(
                member_id=i, eval_id=ev["evalId"],
                text=f"纠正{i}",
                corrected_intent_id=(
                    "product.new_query"))
        os.environ["II58_MODE"] = "off"
        r2 = await learn.collect_feedback()
        alert2 = r2.get("calibrationAlert") or {}
        record("预警幂等(队列纪律跳过)",
               r2.get("labeled") == 3
               and alert2.get("skipped") is not None,
               str((r2.get("labeled"),
                    alert2))[:80])

        # 人工终审后生效(唯一出口)
        rv = await svc.review_calibration(
            int(mirror.get("changeId")),
            approve=True, reviewer="admin")
        view2 = await svc.thresholds_view()
        record("人工终审后生效(0.92)",
               rv.get("status") == "active"
               and (view2.get("baseline")
                    or {}).get("upper") == 0.92,
               str((rv.get("status"),
                    (view2.get("baseline")
                     or {}).get("upper"))))

        # 阈值下预警未达阈值(2 条不触发)
        reset_all()
        os.environ["II58_MODE"] = "assist"
        await seed_corpus(
            "product.price_query", "多少钱")
        for i in (1, 2):
            ev = await Ii58Service().evaluate(
                "多少钱", member_id=i)
            await Ii58FeedbackService(
            ).submit_feedback(
                member_id=i, eval_id=ev["evalId"],
                text=f"纠正{i}",
                corrected_intent_id=(
                    "product.new_query"))
        os.environ["II58_MODE"] = "off"
        r3 = await learn.collect_feedback()
        record("未达阈值(2 条不预警)",
               "calibrationAlert" not in r3
               and (r3.get("signals")
                    or {}).get("high_conf_error")
               == 2,
               str(r3.get("signals")))


class TestScheduler:
    """03 T+1 调度器"""

    async def run(self):
        print("[03 调度器]")
        reset_all()
        from services.ii58_scheduler import (
            run_scheduled_tasks,
            scheduler_enabled,
            scheduler_interval_seconds,
        )

        # 默认 off
        record("调度开关默认 off",
               scheduler_enabled() is False,
               str(scheduler_enabled()))

        # 周期默认 24h+下限 300
        record("周期默认 86400",
               scheduler_interval_seconds() == 86400,
               str(scheduler_interval_seconds()))
        os.environ["II58_LEARN_INTERVAL"] = "10"
        record("周期下限 300(防忙循环)",
               scheduler_interval_seconds() == 300,
               str(scheduler_interval_seconds()))
        os.environ.pop("II58_LEARN_INTERVAL", None)

        # 手动触发轨(空库)
        result = await run_scheduled_tasks()
        collect = result.get("collect") or {}
        record("手动触发(空库 collect)",
               collect.get("scanned") == 0
               and collect.get("labeled") == 0,
               str(collect.get("scanned")))

        # scheduler_run 事件留痕
        from repositories.ii58_repository import (
            Ii58Repository,
        )
        events = await Ii58Repository(
        ).list_events(
            event_type="scheduler_run", limit=10)
        record("scheduler_run 事件留痕",
               len(events) == 1,
               str(len(events)))

        # on 态触发(start 不实际运行——
        # interval 300 下限, 手动轨验证执行语义)
        os.environ["II58_LEARN_MODE"] = "on"
        from services.ii58_scheduler import (
            start_scheduler,
        )
        started = start_scheduler()
        record("on 态启动(started)",
               started is True,
               str(started))
        from services.ii58_scheduler import (
            stop_scheduler,
        )
        stop_scheduler()
        os.environ.pop("II58_LEARN_MODE", None)


class TestHttp:
    """04 HTTP 层"""

    async def run(self):
        print("[04 HTTP]")
        reset_all()
        from fastapi.testclient import TestClient
        from main import app
        client = TestClient(app)
        admin = {"X-Role": "admin"}

        # 种识别记录(shadow)
        os.environ["II58_MODE"] = "shadow"
        await seed_corpus(
            "product.price_query", "多少钱")
        resp = client.post(
            "/api/ii58/evaluate",
            json={"text": "多少钱"},
            headers=admin)
        record("种子评估(resolved)",
               (resp.json() or {}).get("state")
               == "resolved",
               str((resp.json() or {}).get(
                   "state")))

        # collect off 亦可用(回流管理面)
        os.environ["II58_MODE"] = "off"
        resp = client.post(
            "/api/ii58/feedback/collect",
            json={}, headers=admin)
        body = resp.json() or {}
        record("HTTP collect 200(off 亦可用)",
               resp.status_code == 200
               and body.get("labeled") == 1,
               str((resp.status_code,
                    body.get("labeled"))))

        # 幂等(HTTP 重复 collect)
        resp2 = client.post(
            "/api/ii58/feedback/collect",
            json={}, headers=admin)
        body2 = resp2.json() or {}
        record("HTTP collect 幂等(0 新增)",
               resp2.status_code == 200
               and body2.get("labeled") == 0,
               str(body2.get("labeled")))

        # learn_signal 事件留痕
        from repositories.ii58_repository import (
            Ii58Repository,
        )
        events = await Ii58Repository(
        ).list_events(
            event_type="learn_signal", limit=10)
        record("HTTP 回流事件留痕",
               len(events) == 1,
               str(len(events)))

        # 鉴权 403
        resp = client.post(
            "/api/ii58/feedback/collect", json={})
        record("HTTP collect 无 Role 403",
               resp.status_code == 403,
               str(resp.status_code))

        # 路由累计 17 端点(P5 扩至 19——基线语义)
        from routes.ii58_routes import (
            router as ii_router,
        )
        count = sum(
            1 for r in ii_router.routes)
        record("58号路由累计 ≥17 端点",
               count >= 17, str(count))


async def run_all():
    await TestCollectSignals().run()
    await TestCalibrationAlert().run()
    await TestScheduler().run()
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
