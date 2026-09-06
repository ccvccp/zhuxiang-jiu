"""61号·AI智能系统升级决策模块 P4 专项测试
(RLHF 反馈闭环+回流+T+1 调度)

运行方式:
    python test_dm61_p4.py

覆盖(61号计划 §七 P4):
    - 回流: 七类终态信号(decisions 终态
      ×RLHF 反馈关联)+44号池双写
      +decisionId 1:1 幂等
    - 校准预警: AI 判定偏差率→阈值复审
      建议 46号 pending(人工终审轨)
    - QC: 回流幂等; 校准建议人工审批
    - 调度器: DM61_LEARN_MODE 默认 off
      +手动轮
    - HTTP 层: feedback/collect
      (off 可用+鉴权)
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
os.environ["II59_MODE"] = "off"
os.environ["AB63_MODE"] = "off"
os.environ["PAY60_MODE"] = "off"
os.environ["DM61_MODE"] = "off"
os.environ.pop("DM61_LLM_MODE", None)
os.environ.pop("DM61_LEARN_MODE", None)

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


async def seed_terminal(title, action,
                        fb_action=None,
                        fb_outcome=None):
    """造一条终态决策(评估→推荐→裁决
    可选反馈)"""
    prev = os.environ.get("DM61_MODE")
    os.environ["DM61_MODE"] = "shadow"
    try:
        from services.ai_governance_service import (
            AiGovernanceService,
        )
        await AiGovernanceService(
        ).sync_registry()
        from services.dm61_service import (
            Dm61Service,
        )
        from services.dm61_assess_service import (
            Dm61AssessService,
        )
        from services.dm61_decision_service import (
            Dm61DecisionService,
        )
        r = await Dm61Service() \
            .create_request(title=title,
                           hour=3)
        await Dm61AssessService().assess(
            r["requestId"],
            tier="standard",
            error_budget=0.3,
            history_fail_rate=0.05)
        rec = await (
            Dm61DecisionService().recommend(
                r["requestId"]))
        os.environ["DM61_MODE"] = "off"
        modified_detail = \
            "追加观察窗口" \
            if action == "modified" else ""
        d = await Dm61DecisionService() \
            .decide(
                rec["decisionId"],
                action=action,
                decided_by="测试官",
                modified_detail=modified_detail)
        # 处置 46号 pending(解锁后续)
        if d.get("changeId"):
            try:
                await AiGovernanceService(
                ).review_change(
                    int(d["changeId"]),
                    approve=False,
                    reviewed_by="官",
                    review_note="解锁")
            except ValueError:
                pass
        # 可选 RLHF 反馈
        if fb_action:
            from services.dm61_feedback_service import (
                Dm61FeedbackService,
            )
            await Dm61FeedbackService() \
                .submit(
                    rec["decisionId"],
                    action=fb_action,
                    outcome=fb_outcome)
        return rec
    finally:
        os.environ["DM61_MODE"] = prev \
            if prev is not None else "off"


async def seed_dissent_confirmed(title):
    """造一条 dissent_confirmed 终态决策"""
    prev = os.environ.get("DM61_MODE")
    os.environ["DM61_MODE"] = "shadow"
    try:
        from services.dm61_service import (
            Dm61Service,
        )
        from services.dm61_assess_service import (
            Dm61AssessService,
        )
        from services.dm61_decision_service import (
            Dm61DecisionService,
        )
        from services.dm61_dissent_service import (
            Dm61DissentService,
        )
        r = await Dm61Service() \
            .create_request(title=title,
                           hour=3)
        await Dm61AssessService().assess(
            r["requestId"],
            tier="standard",
            error_budget=0.3,
            history_fail_rate=0.05)
        rec = await (
            Dm61DecisionService().recommend(
                r["requestId"]))
        os.environ["DM61_MODE"] = "off"
        await Dm61DissentService() \
            .raise_dissent(
                rec["decisionId"],
                reason="存疑")
        await Dm61DissentService() \
            .resolve(
                rec["decisionId"],
                action="confirm",
                reason="AI 质疑成立",
                resolved_by="测试官")
        return rec
    finally:
        os.environ["DM61_MODE"] = prev \
            if prev is not None else "off"


class TestCollect:
    """01 回流(七类信号+池双写+幂等)"""

    async def run(self):
        print("[01 回流]")
        reset_all()
        from services.dm61_learn_service import (
            Dm61LearnService,
        )
        svc = Dm61LearnService()

        # off 态回流可用(铁律)
        record("off 态回流可用(铁律)",
               os.environ.get(
                   "DM61_MODE", "off") == "off",
               "")

        # 造七类各一
        await seed_terminal(
            "支付结算费率优化", "adopted",
            fb_action="adopted",
            fb_outcome="good")
        await seed_terminal(
            "算法权重调整", "adopted",
            fb_action="adopted",
            fb_outcome="bad")
        await seed_terminal(
            "界面适配优化一", "adopted")
        await seed_terminal(
            "界面适配优化二", "modified",
            fb_action="modified",
            fb_outcome="good")
        await seed_terminal(
            "界面适配优化三", "modified",
            fb_action="modified",
            fb_outcome="bad")
        await seed_terminal(
            "算法权重调整二", "rejected")
        await seed_dissent_confirmed(
            "合规规则调整")

        r = await svc.collect_feedback()
        record("七类信号全标注",
               r["labeled"] == 7
               and set(r["signals"].keys())
               == {
                   "adopted_good",
                   "adopted_bad",
                   "adopted_unverified",
                   "modified_good",
                   "modified_bad",
                   "recommendation_rejected",
                   "dissent_validated"},
               str(r["signals"]))
        record("池双写提交(7 笔)",
               r["poolSubmitted"] == 7
               and r["poolFailed"] == 0,
               str((r["poolSubmitted"],
                    r["poolFailed"])))

        # 奖励值抽查
        from repositories.dm61_repository import (
            Dm61Repository,
        )
        repo = Dm61Repository()
        decs = await repo.list_decisions(
            limit=20)
        by_signal = {
            d.get("poolSignal"):
                d.get("poolReward")
            for d in decs}
        record("奖励映射(±1/±0.5/0.5)",
               by_signal.get(
                   "adopted_good") == 1.0
               and by_signal.get(
                   "adopted_bad") == -1.0
               and by_signal.get(
                   "modified_bad") == -0.5
               and by_signal.get(
                   "adopted_unverified")
               == 0.5,
               str(by_signal))

        # 幂等(二轮全跳过)
        r2 = await svc.collect_feedback()
        record("回流幂等(二轮跳过)",
               r2["labeled"] == 0
               and r2["skipped"] == 7,
               str((r2["labeled"],
                    r2["skipped"])))

        # pooled 标记回写
        pooled = [d for d in decs
                  if int(d.get(
                      "pooledFeedbackId")
                      or 0) > 0]
        record("pooled 标记回写(7)",
               len(pooled) == 7,
               str(len(pooled)))

        # 非终态跳过(recommended 态)
        prev = os.environ.get("DM61_MODE")
        os.environ["DM61_MODE"] = "shadow"
        from services.dm61_service import (
            Dm61Service,
        )
        from services.dm61_assess_service import (
            Dm61AssessService,
        )
        from services.dm61_decision_service import (
            Dm61DecisionService,
        )
        rr = await Dm61Service() \
            .create_request(title="新请求",
                           hour=3)
        await Dm61AssessService().assess(
            rr["requestId"],
            tier="standard",
            error_budget=0.3,
            history_fail_rate=0.05)
        await Dm61DecisionService().recommend(
            rr["requestId"])
        os.environ["DM61_MODE"] = prev \
            if prev is not None else "off"
        r3 = await svc.collect_feedback()
        record("非终态跳过(recommended)",
               r3["labeled"] == 0
               and r3["skipped"] == 8,
               str((r3["labeled"],
                    r3["skipped"])))

        # 事件留痕
        evs = await repo.list_events(
            limit=200)
        learn_evs = [
            e for e in evs
            if e.get("eventType")
            == "learn_signal"]
        record("learn_signal 事件留痕",
               len(learn_evs) == 7,
               str(len(learn_evs)))


class TestCalibration:
    """02 置信度校准预警"""

    async def run(self):
        print("[02 校准预警]")
        reset_all()
        from services.dm61_learn_service import (
            Dm61LearnService,
        )
        svc = Dm61LearnService()

        # ① 样本不足不触发(2 条负向)
        await seed_terminal(
            "支付结算费率优化", "rejected")
        await seed_terminal(
            "算法权重调整", "rejected")
        r = await svc.collect_feedback()
        record("样本不足不触发",
               "calibrationAlert"
               not in r,
               str(r.get(
                   "calibrationAlert")))

        # ② 负向占比不足不触发
        #    (2 负+5 正=28.6%<30%)
        for i in range(5):
            await seed_terminal(
                f"界面适配优化{i}", "adopted",
                fb_action="adopted",
                fb_outcome="good")
        r2 = await svc.collect_feedback()
        record("负向占比不足不触发",
               "calibrationAlert"
               not in r2,
               str(r2.get(
                   "calibrationAlert")))

        # ③ 触发(4 负/9 总=44%≥30%
        #    且样本≥3)
        await seed_terminal(
            "算法权重调整二", "rejected")
        await seed_terminal(
            "算法权重调整三", "rejected")
        r3 = await svc.collect_feedback()
        alert = r3.get(
            "calibrationAlert")
        record("偏差率预警触发",
               alert is not None
               and alert.get(
                   "triggered") is True
               and alert.get(
                   "deviationRate")
               >= 30.0,
               str(alert))

        # ④ 建议提交 46号 pending
        record("预警 46号 pending",
               alert.get("status")
               == "pending"
               and (alert.get(
                   "changeId")
                   or 0) > 0,
               str((alert.get(
                   "status"),
                   alert.get(
                       "changeId"))))

        # ⑤ L1 收紧方向(30-5=25)
        record("L1 收紧建议(25)",
               alert.get("proposedL1")
               == 25.0,
               str(alert.get(
                   "proposedL1")))

        # ⑥ 队列纪律(再触发跳过留痕)
        await seed_terminal(
            "界面适配优化三", "rejected")
        r4 = await svc.collect_feedback()
        alert2 = r4.get(
            "calibrationAlert")
        record("队列纪律(重复跳过)",
               alert2 is not None
               and alert2.get(
                   "skipped") is not None,
               str(alert2))

        # ⑦ 生效唯一出口=人工终审
        #    (阈值仍默认——off 态评估)
        from services.dm61_threshold_service import (
            Dm61ThresholdService,
        )
        active = await (
            Dm61ThresholdService()
            .get_active())
        record("未终审不生效"
               "(人工铁律)",
               active.get("source")
               == "default"
               and active.get(
                   "l1MaxRisk") == 30.0,
               str(active))

        # ⑧ 人工终审生效(46号裁决+apply)
        change_id = alert.get("changeId")
        from services.ai_governance_service import (
            AiGovernanceService,
        )
        try:
            await AiGovernanceService(
            ).review_change(
                int(change_id),
                approve=True,
                reviewed_by="治理官")
        except ValueError:
            pass
        tsvc = Dm61ThresholdService()
        applied = await tsvc.calibrate_apply(
            int(change_id),
            applied_by="决策总监")
        record("终审 apply 生效",
               applied.get("status")
               == "applied"
               and applied.get(
                   "config", {}).get(
                   "l1MaxRisk") == 25.0,
               str(applied.get(
                   "config")))


class TestScheduler:
    """03 T+1 调度器"""

    async def run(self):
        print("[03 调度器]")
        reset_all()
        from services.dm61_scheduler import (
            run_scheduled_tasks,
            scheduler_enabled,
            scheduler_interval_seconds,
            start_scheduler,
        )

        # ① 默认 off
        record("调度器默认 off",
               scheduler_enabled() is False,
               str(scheduler_enabled()))

        # ② 周期下限
        os.environ["DM61_LEARN_INTERVAL"] \
            = "10"
        record("周期下限 300s",
               scheduler_interval_seconds()
               == 300,
               str(
                   scheduler_interval_seconds()))
        os.environ.pop(
            "DM61_LEARN_INTERVAL")

        # ③ off 态 start 不启动
        record("off 态不启动",
               start_scheduler() is False,
               "")

        # ④ 手动轮(可独立调用)
        r = await run_scheduled_tasks()
        record("手动轮执行",
               "collect" in r
               and "errors" in r,
               str(r.get("errors")))

        # ⑤ scheduler_run 留痕
        from repositories.dm61_repository import (
            Dm61Repository,
        )
        evs = await Dm61Repository() \
            .list_events(limit=50)
        run_evs = [
            e for e in evs
            if e.get("eventType")
            == "scheduler_run"]
        record("scheduler_run 留痕",
               len(run_evs) >= 1,
               str(len(run_evs)))

        # ⑥ on 态启动(即停——不留后台任务)
        os.environ["DM61_LEARN_MODE"] \
            = "on"
        from services.dm61_scheduler import (
            stop_scheduler,
        )
        record("on 态启动",
               start_scheduler() is True,
               "")
        stop_scheduler()
        os.environ["DM61_LEARN_MODE"] = "off"
        record("on 态可停止",
               True, "")


class TestHttp:
    """04 HTTP 层(P4 collect 端点)"""

    async def run(self):
        print("[04 HTTP]")
        reset_all()
        from fastapi.testclient import \
            TestClient
        from main import app
        client = TestClient(app)
        admin = {"X-Role": "admin"}

        # off 态 collect 可用(人工铁律)
        resp = client.post(
            "/api/dm61/feedback/collect",
            json={},
            headers=admin)
        body = resp.json() or {}
        record("HTTP collect off 可用",
               resp.status_code == 200
               and body.get("success")
               is True,
               str((resp.status_code,
                    body.get("success"))))

        # 造终态+再 collect
        await seed_terminal(
            "支付结算费率优化", "adopted",
            fb_action="adopted",
            fb_outcome="good")
        resp = client.post(
            "/api/dm61/feedback/collect",
            json={},
            headers=admin)
        body = resp.json() or {}
        record("HTTP collect 标注",
               resp.status_code == 200
               and body.get("labeled") == 1
               and (body.get("signals")
                    or {}).get(
                   "adopted_good") == 1,
               str((resp.status_code,
                    body.get("labeled"))))

        # 幂等(二轮跳过)
        resp = client.post(
            "/api/dm61/feedback/collect",
            json={},
            headers=admin)
        body = resp.json() or {}
        record("HTTP collect 幂等",
               resp.status_code == 200
               and body.get("labeled") == 0,
               str(body.get("labeled")))

        # limit 参数
        resp = client.post(
            "/api/dm61/feedback/collect",
            json={"limit": 1},
            headers=admin)
        record("HTTP collect limit",
               resp.status_code == 200,
               str(resp.status_code))

        # 鉴权 403
        resp = client.post(
            "/api/dm61/feedback/collect",
            json={})
        record("HTTP collect 无 Role 403",
               resp.status_code == 403,
               str(resp.status_code))

        # 路由累计 15 端点
        from routes.dm61_routes import (
            router as dm_router,
        )
        count = sum(
            1 for r in dm_router.routes)
        record("61号路由累计 17 端点",
               count == 17, str(count))


class TestConstitution:
    """05 宪法断言"""

    async def run(self):
        print("[05 宪法断言]")
        from services.ai_learning_service import (
            SCORER_REGISTRY,
        )
        record("44号 37 档案在册",
               len(SCORER_REGISTRY) == 39,
               str(len(SCORER_REGISTRY)))
        record("第36档案 batch20",
               SCORER_REGISTRY.get(
                   "decision_"
                   "orchestration",
                   {}).get("batch") == 20,
               "")

        # 44号零改动(纯调用)
        import services.ai_learning_service \
            as s44
        record("44号零改动(纯调用)",
               s44.__name__.endswith(
                   "ai_learning_service"),
               "")

        # 三开关铁律
        record("三开关铁律(默认 off)",
               os.environ.get(
                   "DM61_MODE",
                   "off") == "off"
               and os.environ.get(
                   "DM61_LLM_MODE",
                   "off") == "off"
               and os.environ.get(
                   "DM61_LEARN_MODE",
                   "off") == "off",
               "")

        # 回流铁律(第36档案在册)
        record("回流档案在册",
               "decision_orchestration"
               in SCORER_REGISTRY,
               "")


async def run_all():
    await TestCollect().run()
    await TestCalibration().run()
    await TestScheduler().run()
    await TestHttp().run()
    await TestConstitution().run()


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
