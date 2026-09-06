"""63号·AI智能后台管理模块 P4 专项测试
(审核反馈闭环+培训推送+回流)

运行方式:
    python test_ab63_p4.py

覆盖(63号计划 §九 P4):
    - 高频驳回点扫描(≥2 次同规则
      驳回→定向培训推送)
    - 培训 7 日转化窗口(pending→
      completed/expired)
    - 培训完成留痕+会员归属校验
    - 六类终态信号→44号池双写
      (subId 1:1 幂等)
    - 自动过审错误率预警(经 46号
      审批——人工终审轨)
    - T+1 调度器(默认 off 零影响)
    - HTTP 层+回归
    - QC: 回流幂等; 预警人工审批
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
os.environ.pop("AB63_LLM_MODE", None)
os.environ.pop("AB63_LEARN_MODE", None)

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


CLEAN = "居家养老服务 服务有效期90天 退改政策可退"
BAD = "全市最好的服务"
# 缺失条款内容(tip 级——可提交人工驳回)
MISSING = "这是一个普通描述"
# 单规则内容(夸大词但条款齐全——
# 仅触发 GUARD_EXAGGERATION)
EXAG_ONLY = ("全市最好的居家养老服务 "
             "服务有效期90天 退改政策可退")


async def seed_submission(svc, member_id,
                          content, tier,
                          approve=None,
                          tags=None):
    """造提交+可选人工裁决"""
    sub = await svc.submit(
        member_id, "ally_merchant",
        content, tier=tier, tags=tags)
    if approve is not None:
        await svc.review(
            sub["subId"], approve=approve,
            reviewer="审核员")
    return sub["subId"]


class TestTrainingPush:
    """01 高频驳回点+培训推送"""

    async def run(self):
        print("[01 培训推送]")
        reset_all()
        from services.ab63_submission_service import (
            Ab63SubmissionService,
        )
        from services.ab63_training_service import (
            Ab63TrainingService,
        )
        sub_svc = Ab63SubmissionService()
        train_svc = Ab63TrainingService()
        os.environ["AB63_MODE"] = "shadow"

        # off 拒绝
        os.environ["AB63_MODE"] = "off"
        try:
            await train_svc.push()
            ok, err = False, "未拒绝"
        except ValueError as e:
            ok, err = "off" in str(e), \
                str(e)[:30]
        record("off 态推送拒绝", ok, err)
        os.environ["AB63_MODE"] = "shadow"

        # ① 单次驳回(未达阈值不推送)
        await seed_submission(
            sub_svc, 10, BAD, "standard",
            approve=False)
        r = await train_svc.push()
        record("单次驳回不推送",
               r["pushed"] == 0,
               str(r["pushed"]))

        # ② 同会员同规则 2 次驳回
        #    (夸大词→高频点——单规则)
        await seed_submission(
            sub_svc, 10, EXAG_ONLY,
            "standard", approve=False)
        r = await train_svc.push()
        record("高频驳回点触发推送",
               r["pushed"] == 1
               and r["pushes"][0]
               ["ruleId"]
               == "GUARD_EXAGGERATION"
               and r["pushes"][0]
               ["rejectCount"] == 2,
               str(r["pushes"]))

        # ③ 幂等: 再推不重复
        r = await train_svc.push()
        record("推送幂等(不重复)",
               r["pushed"] == 0,
               str(r["pushed"]))

        # ④ 不同会员不相互影响
        await seed_submission(
            sub_svc, 11, EXAG_ONLY,
            "standard", approve=False)
        await seed_submission(
            sub_svc, 11, EXAG_ONLY,
            "standard", approve=False)
        r = await train_svc.push()
        record("多会员隔离推送",
               r["pushed"] == 1
               and r["pushes"][0]
               ["memberId"] == 11,
               str(r["pushes"]))

        # ⑤ 指定会员过滤
        await seed_submission(
            sub_svc, 12, BAD, "standard",
            approve=False)
        r = await train_svc.push(
            member_id=999)
        record("指定会员过滤(无命中)",
               r["pushed"] == 0,
               str(r["pushed"]))
        os.environ["AB63_MODE"] = "off"


class TestTrainingLifecycle:
    """02 培训生命周期+7 日转化"""

    async def run(self):
        print("[02 培训生命周期]")
        reset_all()
        from services.ab63_submission_service import (
            Ab63SubmissionService,
        )
        from services.ab63_training_service import (
            Ab63TrainingService,
        )
        sub_svc = Ab63SubmissionService()
        train_svc = Ab63TrainingService()
        os.environ["AB63_MODE"] = "shadow"

        # 造三条培训(member 10 夸大词
        # +缺失条款两规则/member 20
        # 缺失条款一规则——同次驳回
        # 多 finding 去重计 1)
        for mid, content in (
                (10, BAD),
                (20, MISSING)):
            await seed_submission(
                sub_svc, mid, content,
                "standard", approve=False)
            await seed_submission(
                sub_svc, mid, content,
                "standard", approve=False)
        r = await train_svc.push()
        tids = [p["trainingId"]
                for p in r["pushes"]]
        record("三培训推送(10×2 规则+20×1)",
               len(tids) == 3,
               str(r["pushes"]))

        # ① 培训完成
        r = await train_svc.complete(
            tids[0], member_id=10)
        record("培训完成(completed)",
               r["status"] == "completed"
               and bool(
                   r["completedAt"]),
               str(r))

        # ② 已完成不可再完成
        try:
            await train_svc.complete(tids[0])
            ok, err = False, "未拒绝"
        except ValueError as e:
            ok, err = "不可完成" in str(e), \
                str(e)[:30]
        record("已完成不可再完成", ok, err)

        # ③ 会员归属校验
        try:
            await train_svc.complete(
                tids[1], member_id=999)
            ok, err = False, "未拒绝"
        except ValueError as e:
            ok, err = "不匹配" in str(e), \
                str(e)[:30]
        record("会员归属校验", ok, err)

        # ④ 不存在 404 语义
        try:
            await train_svc.complete(99999)
            ok, err = False, "未拒绝"
        except KeyError:
            ok, err = True, ""
        record("培训不存在拒绝", ok, err)

        # ⑤ 窗口过期(手工调 expiresAt
        #    模拟超 7 日——tids[2] 为
        #    member 20 缺失条款)
        from repositories.ab63_repository \
            import Ab63Repository
        repo = Ab63Repository()
        t = await repo.get_training(tids[2])
        t["expiresAt"] = 1  # epoch 过去
        await repo.save_training(
            t, create=False)
        exp = await train_svc \
            .expire_overdue()
        record("7 日窗口过期(expired)",
               tids[2] in exp["ids"],
               str(exp))

        # ⑥ 转化视图(completed 1/
        #    expired 1=50%; tids[1]
        #    member10 缺失条款仍 pending)
        view = await train_svc \
            .training_view()
        record("转化视图(50% 完成率)",
               view["byStatus"]
               == {"pending": 1,
                   "completed": 1,
                   "expired": 1}
               and view["conversionRate"]
               == 50.0,
               str((view["byStatus"],
                    view["conversionRate"])))

        # ⑦ 规则分布
        record("规则分布(EXAG 1+MISSING 2)",
               view["byRule"]
               == {"GUARD_EXAGGERATION": 1,
                   "GUARD_MISSING_CLAUSE": 2},
               str(view["byRule"]))

        # ⑧ 过期+新驳回证据重推(member 20
        #    过期后新增第 3 次驳回→重推;
        #    member 10 完成后无新驳回→
        #    不重推——历史频次不重复推送)
        await seed_submission(
            sub_svc, 20, MISSING,
            "standard", approve=False)
        r = await train_svc.push()
        record("过期+新证据重推(防御)",
               r["pushed"] == 1
               and r["pushes"][0]
               ["memberId"] == 20
               and r["pushes"][0]
               ["rejectCount"] == 3,
               str(r["pushes"]))
        os.environ["AB63_MODE"] = "off"


class TestCollect:
    """03 六类信号→44号池双写"""

    async def run(self):
        print("[03 池双写]")
        reset_all()
        from services.ab63_submission_service import (
            Ab63SubmissionService,
        )
        from services.ab63_learn_service import (
            Ab63LearnService,
        )
        sub_svc = Ab63SubmissionService()
        learn_svc = Ab63LearnService()
        os.environ["AB63_MODE"] = "shadow"

        # 造六类信号各一:
        # ① l1_auto_clean
        await seed_submission(
            sub_svc, 10, CLEAN, "trusted")
        # ② human_approved(L2)
        sid_h = await seed_submission(
            sub_svc, 11, CLEAN, "standard")
        await sub_svc.review(
            sid_h, approve=True,
            reviewer="审核员")
        # ③ human_rejected
        await seed_submission(
            sub_svc, 12, BAD, "standard",
            approve=False)
        # ④ appeal_overturn(驳回→申诉翻转)
        sid_r = await seed_submission(
            sub_svc, 13, BAD, "standard",
            approve=False)
        await sub_svc.appeal(
            sid_r, appellant="member")
        await sub_svc.resolve_appeal(
            sid_r, overturn=True,
            adjudicator="合规官")

        # off 铁律: 回流不受开关影响
        os.environ["AB63_MODE"] = "off"
        r = await learn_svc.collect_feedback()
        record("回流 off 亦可用(铁律)",
               r["success"] is True,
               str(r.get("success")))
        record("四信号标注入池",
               r["labeled"] == 4
               and r["signals"].get(
                   "l1_auto_clean") == 1
               and r["signals"].get(
                   "human_approved") == 1
               and r["signals"].get(
                   "human_rejected") == 1
               and r["signals"].get(
                   "appeal_overturn") == 1,
               str(r["signals"]))
        record("池双写提交成功",
               r["poolSubmitted"] == 4
               and r["poolFailed"] == 0,
               str((r["poolSubmitted"],
                    r["poolFailed"])))

        # 幂等: 二轮全跳过
        r2 = await learn_svc.collect_feedback()
        record("回流幂等(二轮跳过)",
               r2["labeled"] == 0
               and r2["skipped"] == 4,
               str((r2["labeled"],
                    r2["skipped"])))

        # 回写标记读回
        from repositories.ab63_repository \
            import Ab63Repository
        repo = Ab63Repository()
        subs = await repo.list_submissions(
            limit=10)
        pooled = [s for s in subs
                  if int(s.get(
                      "pooledFeedbackId")
                      or 0) > 0]
        record("subId 1:1 回写标记",
               len(pooled) == 4
               and all(s.get("poolSignal")
                       in (
                           "l1_auto_clean",
                           "human_approved",
                           "human_rejected",
                           "appeal_overturn")
                       for s in pooled),
               str(len(pooled)))

        # learn_signal 事件留痕
        evs = await repo.list_events(
            event_type="learn_signal",
            limit=10)
        record("learn_signal 留痕",
               len(evs) == 4,
               str(len(evs)))


class TestSpotSignals:
    """04 抽检信号+预警"""

    async def run(self):
        print("[04 抽检信号+预警]")
        reset_all()
        from services.ab63_submission_service import (
            Ab63SubmissionService,
        )
        from services.ab63_learn_service import (
            Ab63LearnService,
        )
        from repositories.ab63_repository import (
            Ab63Repository,
        )
        from core.helpers import ts
        sub_svc = Ab63SubmissionService()
        learn_svc = Ab63LearnService()
        repo = Ab63Repository()
        os.environ["AB63_MODE"] = "shadow"

        # ① 真实抽检流: 20 连发 trusted
        #    → 抽检命中驳回(auto_error)
        spot_sid = None
        for i in range(20):
            sub = await sub_svc.submit(
                30 + i, "ally_merchant",
                CLEAN, tier="trusted")
            if sub["spotCheck"]:
                spot_sid = sub["subId"]
        record("抽检命中(20 连发)",
               spot_sid is not None,
               str(spot_sid))

        # 抽检驳回(auto_error)
        await sub_svc.review(
            spot_sid, approve=False,
            reviewer="复检员")

        r = await learn_svc.collect_feedback()
        record("auto_error 信号(抽检驳回)",
               r["signals"].get(
                   "auto_error") == 1
               and r["signals"].get(
                   "l1_auto_clean") == 19,
               str(r["signals"]))

        # ② 预警流: repo 直种子构造高
        #    错误率(2 抽检驳回/8 自动
        #    信号=25%≥20%)
        reset_all()
        # 46号档案入册(预警提交依赖)
        from services.ai_governance_service import (
            AiGovernanceService,
        )
        await AiGovernanceService(
        ).sync_registry()
        for i in range(6):
            sid = await repo.next_sub_id()
            await repo.save_submission({
                "subId": sid,
                "memberId": 60 + i,
                "role": "ally_merchant",
                "status": "auto_published",
                "spotCheck": False,
                "tier": "trusted",
                "publishScore": 100.0,
                "reviewTier": "L1",
                "evidence": {
                    "guardLevel": "clean"},
                "createdAt": ts(),
                "updatedAt": ts()})
        for i in range(2):
            sid = await repo.next_sub_id()
            await repo.save_submission({
                "subId": sid,
                "memberId": 70 + i,
                "role": "ally_merchant",
                "status": "rejected",
                "spotCheck": True,
                "spotCheckResult":
                    "rejected",
                "tier": "trusted",
                "publishScore": 100.0,
                "reviewTier": "L1",
                "evidence": {
                    "guardLevel": "clean"},
                "createdAt": ts(),
                "updatedAt": ts()})

        r = await learn_svc.collect_feedback()
        # 错误率 2/8=25%≥20% → 预警
        alert = r.get("calibrationAlert")
        record("错误率预警触发(≥20%)",
               alert is not None
               and alert.get(
                   "triggered") is True
               and bool(
                   alert.get("changeId")),
               str(alert)[:80])

        # 预警为 pending(46号人工终审)
        record("预警 pending(人工终审轨)",
               (alert or {}).get(
                   "status") == "pending",
               str((alert or {}).get(
                   "status")))

        # 二轮回流: 队列纪律(已有
        # pending——预警跳过)
        r2 = await learn_svc.collect_feedback()
        alert2 = r2.get(
            "calibrationAlert")
        record("队列纪律(预警跳过)",
               alert2 is None
               or alert2.get("skipped")
               is not None,
               str(alert2)[:60])
        os.environ["AB63_MODE"] = "off"


class TestScheduler:
    """05 T+1 调度器"""

    async def run(self):
        print("[05 调度器]")
        reset_all()
        from services.ab63_scheduler import (
            scheduler_enabled,
            scheduler_interval_seconds,
            run_scheduled_tasks,
            start_scheduler,
            stop_scheduler,
        )
        # 默认 off
        record("调度默认 off",
               scheduler_enabled() is False,
               "")
        record("默认周期 24h",
               scheduler_interval_seconds()
               == 86400,
               str(
                   scheduler_interval_seconds()))
        record("off 态不启动",
               start_scheduler() is False,
               "")

        # 手动轮(可独立调用)
        os.environ["AB63_LEARN_MODE"] = "on"
        r = await run_scheduled_tasks()
        record("手动轮可执行",
               "collect" in r
               and "training" in r,
               str(r)[:60])

        # on 态启动
        started = start_scheduler()
        stop_scheduler()
        record("on 态可启动",
               started is True,
               "")
        os.environ["AB63_LEARN_MODE"] = "off"


class TestHttp:
    """06 HTTP 层(P4)"""

    async def run(self):
        print("[06 HTTP]")
        reset_all()
        from fastapi.testclient import TestClient
        from main import app
        client = TestClient(app)
        admin = {"X-Role": "admin"}

        # off 409(决策面)
        resp = client.post(
            "/api/ab63/training/push",
            json={},
            headers=admin)
        record("HTTP training/push off 409",
               resp.status_code == 409,
               str(resp.status_code))

        # 造高频驳回+推送(shadow——
        # 单规则内容仅夸大词)
        os.environ["AB63_MODE"] = "shadow"
        from services.ab63_submission_service import (
            Ab63SubmissionService,
        )
        sub_svc = Ab63SubmissionService()
        for _ in range(2):
            await seed_submission(
                sub_svc, 70, EXAG_ONLY,
                "standard", approve=False)
        resp = client.post(
            "/api/ab63/training/push",
            json={"memberId": 70},
            headers=admin)
        body = resp.json() or {}
        tid = ((body.get("pushes")
                or [{}])[0]
               .get("trainingId"))
        record("HTTP training/push 200",
               resp.status_code == 200
               and body.get("pushed") == 1
               and bool(tid),
               str((resp.status_code,
                    body.get("pushed"))))

        # 培训完成(off 亦可用)
        os.environ["AB63_MODE"] = "off"
        resp = client.post(
            f"/api/ab63/training/"
            f"{tid}/complete",
            json={"memberId": 70},
            headers=admin)
        record("HTTP training/complete 200"
               "(off 亦可用)",
               resp.status_code == 200
               and (resp.json() or {}
                    ).get("status")
               == "completed",
               str((resp.status_code,
                    (resp.json() or {}
                     ).get("status"))))

        # 培训视图(观测面)
        resp = client.get(
            "/api/ab63/training",
            headers=admin)
        body = resp.json() or {}
        record("HTTP training 视图",
               resp.status_code == 200
               and body.get("total") == 1
               and body.get(
                   "conversionRate")
               == 100.0,
               str((resp.status_code,
                    body.get("total"))))

        # 决策回流(off 亦可用)
        resp = client.post(
            "/api/ab63/feedback/collect",
            json={},
            headers=admin)
        body = resp.json() or {}
        record("HTTP feedback/collect 200"
               "(off 亦可用)",
               resp.status_code == 200
               and body.get("labeled")
               == 2,
               str((resp.status_code,
                    body.get("labeled"))))

        # 培训不存在 404
        resp = client.post(
            "/api/ab63/training/99999/"
            "complete",
            json={},
            headers=admin)
        record("HTTP training 404",
               resp.status_code == 404,
               str(resp.status_code))

        # 鉴权 403
        resp = client.post(
            "/api/ab63/feedback/collect",
            json={})
        record("HTTP collect 无 Role 403",
               resp.status_code == 403,
               str(resp.status_code))


class TestConstitution:
    """07 宪法+QC"""

    async def run(self):
        print("[07 宪法+QC]")
        from services.ab63_training_service import (
            HIGH_FREQ_THRESHOLD,
            TRAINING_CATALOG,
            TRAINING_STATUSES,
            TRAINING_WINDOW_SECONDS,
        )
        from services.ab63_learn_service import (
            SIGNAL_REWARDS,
        )
        record("六类信号(封闭注册)",
               len(SIGNAL_REWARDS) == 6
               and set(
                   SIGNAL_REWARDS.values()
               ) <= {1.0, -1.0},
               str(SIGNAL_REWARDS))
        record("培训目录覆盖 8 规则",
               len(TRAINING_CATALOG) == 8
               and set(
                   TRAINING_CATALOG)
               == {"GUARD_SENSITIVE_WORD",
                   "GUARD_EXAGGERATION",
                   "GUARD_MISSING_CLAUSE",
                   "GUARD_FORM_REQUIRED",
                   "GUARD_FORM_LOGIC",
                   "GUARD_OVERCOLLECT",
                   "GUARD_PII_LEAK",
                   "GUARD_PRIVACY_BUDGET"},
               str(len(
                   TRAINING_CATALOG)))
        record("7 日窗口+阈值 2 次",
               TRAINING_WINDOW_SECONDS
               == 7 * 86400
               and HIGH_FREQ_THRESHOLD
               == 2,
               "")
        record("培训状态机三态",
               TRAINING_STATUSES == (
                   "pending", "completed",
                   "expired"),
               str(TRAINING_STATUSES))
        # 44号零改动(纯调用——
        # submit_feedback 接口)
        import services.ai_learning_service as s44
        record("44号模块可导入(零改动)",
               s44.__name__.endswith(
                   "ai_learning_service"),
               "")


async def run_all():
    await TestTrainingPush().run()
    await TestTrainingLifecycle().run()
    await TestCollect().run()
    await TestSpotSignals().run()
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
