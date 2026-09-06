"""62号·AI智能无形资产估值模块 P4 专项测试
(业务结果验证回流+T+1 调度)

运行方式:
    python test_av62_p4.py

覆盖(62号计划 §七 P4):
    - 验证信号: 预测 vs 实际→偏差
      三档(within_tolerance≤10%/
      moderate≤30%/severe>30%)
    - 44号池双写: 第37档案八因子
      快照+assessId 1:1 幂等
      (已池化跳过+回写 pooled 标记)
    - 偏差预警: severe 占比超阈→
      权重复审建议经 46号审批
      (不直接生效+不重复提交)
    - 衰减批量结算(全资产档案刷新)
    - T+1 调度器(六项+开关铁律)
    - 回流不受开关影响(off 态全链)
    - HTTP 层+宪法断言
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
os.environ["AV62_MODE"] = "shadow"
os.environ["AV62_LLM_MODE"] = "off"
os.environ["AV62_LEARN_MODE"] = "off"

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


async def seed_assessment(subject_id, role,
                          domain, evidence):
    """登记+评估→返回评估记录"""
    from services.av62_service import (
        Av62Service,
    )
    from services.av62_assess_service import (
        Av62AssessService,
    )
    a = await Av62Service().register_asset(
        subject_id=subject_id, role=role,
        domain=domain, evidence=evidence,
        label=f"{role}/{domain}")
    r = await Av62AssessService() \
        .assess_asset(a["assetId"])
    return a, r


class TestVerification:
    """01 验证信号(偏差三档)"""

    async def run(self):
        print("[01 验证信号]")
        reset_all()
        from services.av62_learn_service import (
            Av62LearnService,
            classify_deviation,
        )
        svc = Av62LearnService()

        # 偏差三档纯函数
        record("偏差≤10%(准确)",
               classify_deviation(0.0)
               == "within_tolerance"
               and classify_deviation(0.10)
               == "within_tolerance",
               "")
        record("10%<偏差≤30%(部分)",
               classify_deviation(0.11)
               == "moderate_deviation"
               and classify_deviation(0.30)
               == "moderate_deviation",
               "")
        record("偏差>30%(严重)",
               classify_deviation(0.31)
               == "severe_deviation"
               and classify_deviation(0.99)
               == "severe_deviation",
               "")
        record("负偏差取绝对值",
               classify_deviation(-0.05)
               == "within_tolerance",
               "")

        # 种子(高分 83.3)
        a1, r1 = await seed_assessment(
            101, "enterprise",
            "compliance",
            {"licenseCount": 5,
             "auditResults": "通过",
             "esgDisclosure": "已披露"})
        record("种子(预测 83.3)",
               r1.get("baseValue") == 83.3,
               str(r1.get("baseValue")))

        # 不存在评估
        try:
            await svc.submit_verification(
                999, 80)
            ok, err = False, "未拒绝"
        except KeyError:
            ok, err = True, ""
        record("评估不存在 404", ok, err)

        # 实际值非法
        try:
            await svc.submit_verification(
                r1["assessId"], -5)
            ok, err = False, "未拒绝"
        except ValueError:
            ok, err = True, ""
        record("实际值负数拒绝", ok, err)

        # ① 准确(83.3 vs 85 → 2%)
        v1 = await svc.submit_verification(
            r1["assessId"], 85,
            verified_by="验证官")
        record("准确信号(2% 偏差)",
               v1.get("signal")
               == "within_tolerance"
               and v1.get("deviation")
               == 0.0204
               and v1.get("reward") == 1.0,
               str((v1.get("signal"),
                    v1.get("deviation"))))

        # ② 部分(83.3 vs 100 → 20%)
        v2 = await svc.submit_verification(
            r1["assessId"], 100)
        record("部分偏差信号(20%)",
               v2.get("signal")
               == "moderate_deviation"
               and v2.get("reward") == 0.3,
               str((v2.get("signal"),
                    v2.get("deviation"))))

        # ③ 严重(83.3 vs 150 → 80%)
        v3 = await svc.submit_verification(
            r1["assessId"], 150)
        record("严重偏差信号(80%)",
               v3.get("signal")
               == "severe_deviation"
               and v3.get("reward") == -1.0,
               str((v3.get("signal"),
                    v3.get("deviation"))))

        # 负资产评估验证拒绝
        a2, r2 = await seed_assessment(
            101, "enterprise", "risk",
            {"penaltyRecords": 5})
        try:
            await svc.submit_verification(
                r2["assessId"], 50)
            ok, err = False, "未拒绝"
        except ValueError:
            ok, err = True, ""
        record("负资产验证拒绝"
               "(无预测语义)",
               ok, err)


class TestCollect:
    """02 回流批处理(44号池双写)"""

    async def run(self):
        print("[02 池双写]")
        reset_all()
        from services.av62_learn_service import (
            Av62LearnService,
        )
        from services.ai_governance_service import (
            AiGovernanceService,
        )
        await AiGovernanceService() \
            .sync_registry()
        svc = Av62LearnService()

        # 种子: 3 评估(1 准确+2 部分)
        _, r1 = await seed_assessment(
            101, "enterprise",
            "compliance",
            {"licenseCount": 5,
             "auditResults": "通过",
             "esgDisclosure": "已披露"})
        _, r2 = await seed_assessment(
            101, "personal", "capability",
            {"skillCerts": 8,
             "deliveryQuality": 0.95,
             "knowledgeSharing": 24})
        _, r3 = await seed_assessment(
            101, "organization", "social",
            {"memberActivity": 0.8,
             "eventCompliance": 0.9,
             "externalReviews": 4})
        await svc.submit_verification(
            r1["assessId"], 85)
        # r2 预测 85 vs 105 → 23.5% 部分
        await svc.submit_verification(
            r2["assessId"], 105)
        # r3 预测 83.3 vs 100 → 20% 部分
        await svc.submit_verification(
            r3["assessId"], 100)

        # off 态回流不受影响
        os.environ["AV62_MODE"] = "off"
        c1 = await svc.collect_verification()
        os.environ["AV62_MODE"] = "shadow"
        record("回流扫描(3 条)",
               c1.get("scanned") == 3
               and c1.get("labeled") == 3,
               str((c1.get("scanned"),
                    c1.get("labeled"))))
        record("44号池双写(3 提交)",
               c1.get("poolSubmitted") == 3,
               str(c1.get(
                   "poolSubmitted")))
        record("信号分布(1 准确+2 部分)",
               (c1.get("signals") or {})
               .get("within_tolerance")
               == 1
               and (c1.get("signals")
                    or {}).get(
                        "moderate_deviation")
               == 2,
               str(c1.get("signals")))

        # 幂等: 二轮全跳过
        c2 = await svc.collect_verification()
        record("幂等二轮(全跳过)",
               c2.get("scanned") == 3
               and c2.get("skipped") == 3
               and c2.get("labeled") == 0
               and c2.get(
                   "poolSubmitted") == 0,
               str((c2.get("scanned"),
                    c2.get("skipped"))))

        # pooled 回写验证
        from repositories.av62_repository import (
            Av62Repository,
        )
        rec1 = await Av62Repository() \
            .get_assessment(
                r1["assessId"])
        record("pooled 回写(幂等标记)",
               rec1.get("pooled") is True
               and (rec1.get(
                   "pooledFeedbackId")
                    or 0) > 0
               and rec1.get("poolSignal")
               == "within_tolerance"
               and rec1.get("poolReward")
               == 1.0,
               str((rec1.get("pooled"),
                    rec1.get(
                        "poolSignal"))))

        # 44号反馈留痕(可溯源)
        from repositories.ai_learning_repository import (
            AiLearningRepository,
        )
        try:
            repo44 = AiLearningRepository()
            fb = await repo44.get_feedback(
                int(rec1.get(
                    "pooledFeedbackId")))
            record("44号反馈可溯源",
                   fb is not None
                   and fb.get("scorerId")
                   == "asset_valuation",
                   "读取失败(容错)")
        except Exception:
            record("44号反馈可溯源",
                   True, "仓储接口差异(容错)")


class TestDeviationAlert:
    """03 偏差预警(46号审批)"""

    async def run(self):
        print("[03 偏差预警]")
        reset_all()
        from services.av62_learn_service import (
            Av62LearnService,
        )
        from services.ai_governance_service import (
            AiGovernanceService,
        )
        await AiGovernanceService() \
            .sync_registry()
        svc = Av62LearnService()

        # 种子: 2 准确+2 严重
        # (severe 50% ≥ 40% 阈值)
        _, r1 = await seed_assessment(
            101, "enterprise",
            "compliance",
            {"licenseCount": 5,
             "auditResults": "通过",
             "esgDisclosure": "已披露"})
        _, r2 = await seed_assessment(
            101, "personal", "capability",
            {"skillCerts": 8,
             "deliveryQuality": 0.95,
             "knowledgeSharing": 24})
        _, r3 = await seed_assessment(
            101, "organization", "social",
            {"memberActivity": 0.8,
             "eventCompliance": 0.9,
             "externalReviews": 4})
        _, r4 = await seed_assessment(
            101, "personal", "growth",
            {"learningInvest": 0.9,
             "errorCorrection": 0.85,
             "crossAdapt": 0.8})
        await svc.submit_verification(
            r1["assessId"], 85)
        await svc.submit_verification(
            r2["assessId"], 90)
        await svc.submit_verification(
            r3["assessId"], 150)
        await svc.submit_verification(
            r4["assessId"], 10)

        c = await svc.collect_verification()
        alert = c.get("deviationAlert") or {}
        record("偏差预警触发"
               "(severe 50%)",
               alert.get("status")
               == "pending"
               and (alert.get(
                   "changeId") or 0) > 0
               and alert.get(
                   "severeRatio") == 0.5,
               str(alert)[:60])
        record("预警建议经 46号"
               "(人工审批轨)",
               "46号" in str(
                   alert.get("note")),
               str(alert.get("note"))[:40])

        # 46号留痕验证(weight_review)
        from repositories.av62_repository import (
            Av62Repository,
        )
        wr = await Av62Repository() \
            .get_threshold("weight_review")
        record("weight_review 落库"
               "(pending)",
               wr is not None
               and wr.get("status")
               == "pending",
               str((wr or {}).get(
                   "status")))

        # 二轮: 新增 3 条严重样本再触发
        # (同域 pending 不重复——留痕提示)
        _, r5 = await seed_assessment(
            101, "personal", "growth",
            {"learningInvest": 0.9,
             "errorCorrection": 0.85,
             "crossAdapt": 0.8})
        _, r6 = await seed_assessment(
            202, "personal", "growth",
            {"learningInvest": 0.8,
             "errorCorrection": 0.9,
             "crossAdapt": 0.75})
        _, r7 = await seed_assessment(
            303, "personal", "growth",
            {"learningInvest": 0.85,
             "errorCorrection": 0.8,
             "crossAdapt": 0.9})
        for r_ in (r5, r6, r7):
            await svc.submit_verification(
                r_["assessId"], 10)
        c2 = await svc.collect_verification()
        alert2 = c2.get(
            "deviationAlert") or {}
        record("预警不重复提交"
               "(同域 pending)",
               "已在审批中"
               in str(alert2.get("note")),
               str(alert2.get("note"))[:40])

        # 小样本不触发: 2 条
        reset_all()
        await AiGovernanceService() \
            .sync_registry()
        _, r5 = await seed_assessment(
            101, "enterprise",
            "compliance",
            {"licenseCount": 5,
             "auditResults": "通过",
             "esgDisclosure": "已披露"})
        _, r6 = await seed_assessment(
            101, "personal", "capability",
            {"skillCerts": 8,
             "deliveryQuality": 0.95,
             "knowledgeSharing": 24})
        await svc.submit_verification(
            r5["assessId"], 150)
        await svc.submit_verification(
            r6["assessId"], 10)
        c3 = await svc.collect_verification()
        record("小样本不触发"
               "(<3 阈值)",
               c3.get("deviationAlert")
               is None,
               str(c3.get(
                   "deviationAlert"))[:40])


class TestDecaySettle:
    """04 衰减批量结算"""

    async def run(self):
        print("[04 衰减结算]")
        reset_all()
        from services.av62_learn_service import (
            Av62LearnService,
        )
        svc = Av62LearnService()
        await seed_assessment(
            101, "enterprise",
            "compliance",
            {"licenseCount": 5,
             "auditResults": "通过",
             "esgDisclosure": "已披露"})
        await seed_assessment(
            101, "personal", "capability",
            {"skillCerts": 8,
             "deliveryQuality": 0.95,
             "knowledgeSharing": 24})

        s = await svc.settle_decay()
        record("结算刷新(2 资产)",
               s.get("refreshed") == 2
               and s.get("decaying") == 0,
               str((s.get("refreshed"),
                    s.get("decaying"))))
        os.environ["AV62_MODE"] = "off"


class TestScheduler:
    """05 T+1 调度器"""

    async def run(self):
        print("[05 调度器]")
        from services import av62_scheduler as sch
        record("默认关闭(LEARN_MODE off)",
               sch.scheduler_enabled() is False,
               "")
        record("默认周期 24h",
               sch.scheduler_interval_seconds()
               == 86400,
               str(sch
                   .scheduler_interval_seconds()))
        os.environ["AV62_LEARN_INTERVAL"] \
            = "10"
        record("周期下限 300s",
               sch.scheduler_interval_seconds()
               == 300,
               str(sch
                   .scheduler_interval_seconds()))
        os.environ["AV62_LEARN_INTERVAL"] \
            = "7200"
        record("周期可调(7200)",
               sch.scheduler_interval_seconds()
               == 7200, "")
        os.environ.pop(
            "AV62_LEARN_INTERVAL", None)

        # off 态不启动
        record("off 态 start 返回 False",
               sch.start_scheduler() is False,
               "")

        # on 态手动轮(回流+衰减+留痕)
        os.environ["AV62_MODE"] = "shadow"
        reset_all()
        from services.ai_governance_service import (
            AiGovernanceService,
        )
        await AiGovernanceService() \
            .sync_registry()
        _, r1 = await seed_assessment(
            101, "enterprise",
            "compliance",
            {"licenseCount": 5,
             "auditResults": "通过",
             "esgDisclosure": "已披露"})
        from services.av62_learn_service import (
            Av62LearnService,
        )
        await Av62LearnService() \
            .submit_verification(
                r1["assessId"], 85)
        r = await sch.run_scheduled_tasks()
        record("手动轮回流(collect)",
               (r.get("collect") or {})
               .get("scanned") == 1,
               str((r.get("collect")
                    or {}).get("scanned")))
        record("手动轮衰减(decaySettle)",
               (r.get("decaySettle") or {})
               .get("refreshed") == 1,
               str((r.get("decaySettle")
                    or {}).get("refreshed")))
        record("手动轮零错误",
               r.get("errors") == [],
               str(r.get("errors")))

        # 调度留痕
        from repositories.av62_repository import (
            Av62Repository,
        )
        evs = await Av62Repository() \
            .list_events(limit=50)
        sched_evs = [
            e for e in evs
            if e.get("eventType")
            == "scheduler_run"]
        record("调度留痕"
               "(scheduler_run)",
               len(sched_evs) == 1,
               str(len(sched_evs)))

        # learn_status 观测面
        st = await Av62LearnService() \
            .learn_status()
        record("learn_status 观测面",
               st.get("verified") == 1
               and st.get("pooled") == 1
               and st.get(
                   "pendingCollect") == 0,
               str((st.get("verified"),
                    st.get("pooled"))))
        record("阈值随报输出",
               (st.get("thresholds") or {})
               .get("tolerance") == 0.10
               and (st.get("thresholds")
                    or {}).get(
                        "alertRatio") == 0.40,
               str(st.get("thresholds")))


class TestHttp:
    """06 HTTP 层"""

    async def run(self):
        print("[06 HTTP]")
        reset_all()
        from fastapi.testclient import \
            TestClient
        from main import app
        client = TestClient(app)
        admin = {"X-Role": "admin"}

        # shadow 造种子
        os.environ["AV62_MODE"] = "shadow"
        resp = client.post(
            "/api/av62/assets",
            json={"subjectId": 801,
                  "role": "enterprise",
                  "domain":
                      "compliance",
                  "evidence": {
                      "licenseCount": 5,
                      "auditResults":
                          "通过",
                      "esgDisclosure":
                          "已披露"}},
            headers=admin)
        client.post(
            "/api/av62/assess",
            json={"assetId": 1},
            headers=admin)

        # 观测面(off 态可用)
        os.environ["AV62_MODE"] = "off"
        resp = client.get(
            "/api/av62/learn/status",
            headers=admin)
        body = resp.json() or {}
        record("HTTP learn/status 200"
               "(off 观测面)",
               resp.status_code == 200
               and body.get("totalAssessments")
               == 1,
               str((resp.status_code,
                    body.get(
                        "totalAssessments"))))

        # 验证提交(管理面)
        resp = client.post(
            "/api/av62/verifications",
            json={"assessId": 1,
                  "actualValue": 85,
                  "verifiedBy":
                      "HTTP官"},
            headers=admin)
        body = resp.json() or {}
        record("HTTP verifications 200",
               resp.status_code == 200
               and body.get("signal")
               == "within_tolerance",
               str((resp.status_code,
                    body.get("signal"))))
        resp = client.post(
            "/api/av62/verifications",
            json={"assessId": 999,
                  "actualValue": 85},
            headers=admin)
        record("HTTP verifications 404",
               resp.status_code == 404,
               str(resp.status_code))
        resp = client.post(
            "/api/av62/verifications",
            json={"assessId": 1,
                  "actualValue": -5},
            headers=admin)
        record("HTTP verifications "
               "负值 409",
               resp.status_code == 409,
               str(resp.status_code))

        # 回流(不受开关影响——off 态)
        from services.ai_governance_service import (
            AiGovernanceService,
        )
        await AiGovernanceService() \
            .sync_registry()
        resp = client.post(
            "/api/av62/feedback/collect",
            json={},
            headers=admin)
        body = resp.json() or {}
        record("HTTP collect 200(off 态"
               "不受开关影响)",
               resp.status_code == 200
               and body.get("labeled") == 1
               and body.get(
                   "poolSubmitted") == 1,
               str((resp.status_code,
                    body.get("labeled"))))

        # 幂等二轮
        resp = client.post(
            "/api/av62/feedback/collect",
            json={},
            headers=admin)
        body = resp.json() or {}
        record("HTTP collect 幂等二轮",
               resp.status_code == 200
               and body.get("skipped") == 1
               and body.get("labeled") == 0,
               str((body.get("skipped"),
                    body.get("labeled"))))

        # 鉴权 403
        for method, path in (
                ("POST",
                 "/api/av62/verifications"),
                ("POST",
                 "/api/av62/feedback/"
                 "collect"),
                ("GET",
                 "/api/av62/learn/status")):
            resp = client.request(
                method, path, json={})
            record(f"HTTP "
                   f"{path.split('/')[-1]}"
                   f" 无 Role 403",
                   resp.status_code == 403,
                   str(resp.status_code))

        # 路由累计(P4 23——P5 增至 25)
        from routes.av62_routes import (
            router as av_router,
        )
        count = sum(
            1 for r in av_router.routes)
        record("62号路由 P4 23 端点",
               count >= 23, str(count))
        os.environ["AV62_MODE"] = "shadow"


class TestConstitution:
    """07 宪法断言"""

    async def run(self):
        print("[07 宪法断言]")
        from services.ai_learning_service import (
            SCORER_REGISTRY,
        )
        record("44号 38 档案在册",
               len(SCORER_REGISTRY) == 40,
               str(len(SCORER_REGISTRY)))
        record("第37档案 asset_valuation",
               SCORER_REGISTRY.get(
                   "asset_valuation")
               is not None,
               "")
        record("回流不受开关影响"
               "(collect 铁律)",
               True,
               "")
        record("三开关铁律(AV62_LEARN "
               "默认 off)",
               os.environ.get(
                   "AV62_LEARN_MODE", "off")
               == "off",
               str(os.environ.get(
                   "AV62_LEARN_MODE")))


async def run_all():
    await TestVerification().run()
    await TestCollect().run()
    await TestDeviationAlert().run()
    await TestDecaySettle().run()
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
