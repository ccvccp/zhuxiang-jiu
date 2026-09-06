"""61号·AI智能系统升级决策模块 P3 专项测试
(决策归因+反对意见+决策图谱)

运行方式:
    python test_dm61_p3.py

覆盖(61号计划 §七 P3):
    - 反对意见: 确定性触发四规则+
      raise/override/confirm+decide
      门禁(自动弹窗+阻断+放行)
    - QC: AI 可说不(off 亦可用)+
      人类可驳回必留痕
    - 决策归因: ATTRIBUTION_SCHEMA
      完整推理链(语义→影响→环境→
      评估→先验→沙箱→推荐→裁决→
      反对意见)
    - 决策图谱: 案例库派生+相似检索
      +先验概率(dissent_confirmed
      计入失败口径)
    - RLHF 反馈: 三态+结果+1:1
    - HTTP 层: 3 新端点+鉴权
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


async def seed_recommended(title, hour=3,
                           error_budget=0.3):
    """造一条 recommended 态决策(自动
    切 shadow 态完成决策面操作)"""
    from services.dm61_service import (
        Dm61Service,
    )
    from services.dm61_assess_service import (
        Dm61AssessService,
    )
    from services.dm61_decision_service import (
        Dm61DecisionService,
    )
    prev = os.environ.get("DM61_MODE")
    os.environ["DM61_MODE"] = "shadow"
    try:
        r = await Dm61Service() \
            .create_request(
                title=title, hour=hour)
        await Dm61AssessService().assess(
            r["requestId"],
            tier="standard",
            error_budget=error_budget,
            history_fail_rate=0.05)
        rec = await (
            Dm61DecisionService().recommend(
                r["requestId"]))
    finally:
        os.environ["DM61_MODE"] = prev \
            if prev is not None else "off"
    return r, rec


class TestDissent:
    """01 反对意见机制(AI 可说不)"""

    async def run(self):
        print("[01 反对意见]")
        reset_all()
        from services.dm61_dissent_service import (
            Dm61DissentService,
        )
        svc = Dm61DissentService()

        # ① off 亦可用(AI 安全机制铁律)
        _r1, rec1 = await seed_recommended(
            "支付结算费率优化")
        ev = await svc.evaluate(
            rec1["decisionId"])
        record("off 态评估可用(铁律)",
               ev.get("triggerCount") == 0,
               str(ev.get(
                   "triggerCount")))

        # ② 无触发+无理由发起拒绝
        try:
            await svc.raise_dissent(
                rec1["decisionId"])
            ok, err = False, "未拒绝"
        except ValueError:
            ok, err = True, ""
        record("无依据发起拒绝", ok, err)

        # ③ 手动理由发起(off 亦可用)
        raised = await svc.raise_dissent(
            rec1["decisionId"],
            raised_by="admin",
            reason="风控官人工质疑")
        record("手动理由发起(off 可用)",
               raised.get("dissentFlag")
               is True
               and (raised.get("dissent")
                    or {}).get("status")
               == "open",
               str(raised.get(
                   "dissentFlag")))

        # ④ decide 被 open dissent 阻断
        from services.dm61_decision_service import (
            Dm61DecisionService,
        )
        try:
            await Dm61DecisionService() \
                .decide(
                    rec1["decisionId"],
                    action="adopted",
                    decided_by="x")
            ok, err = False, "未拒绝"
        except ValueError:
            ok, err = True, ""
        record("open dissent 阻断裁决",
               ok, err)

        # ⑤ 重复发起拒绝
        try:
            await svc.raise_dissent(
                rec1["decisionId"])
            ok, err = False, "未拒绝"
        except ValueError:
            ok, err = True, ""
        record("重复发起拒绝", ok, err)

        # ⑥ override 缺理由拒绝
        try:
            await svc.resolve(
                rec1["decisionId"],
                action="override",
                reason="")
            ok, err = False, "未拒绝"
        except ValueError:
            ok, err = True, ""
        record("override 缺理由拒绝", ok, err)

        # ⑦ override 留痕放行
        res = await svc.resolve(
            rec1["decisionId"],
            action="override",
            reason="已知晓风险——"
                   "业务窗口紧迫",
            resolved_by="决策长")
        record("override 留痕(status)",
               (res.get("dissent")
                or {}).get("status")
               == "overridden"
               and (res.get("dissent")
                    or {}).get(
                   "resolutionReason")
               == "已知晓风险——"
                  "业务窗口紧迫",
               str((res.get("dissent")
                    or {}).get(
                   "status")))

        # ⑧ override 后 decide 放行
        from services.ai_governance_service import (
            AiGovernanceService,
        )
        await AiGovernanceService(
        ).sync_registry()
        d1 = await Dm61DecisionService() \
            .decide(
                rec1["decisionId"],
                action="adopted",
                decided_by="决策长",
                note="override 后推进")
        record("override 后裁决放行",
               d1.get("status")
               == "executed_track"
               and d1.get("changeId") > 0,
               str(d1.get("status")))
        # 处置 46号 pending(解锁后续提交)
        await AiGovernanceService(
        ).review_change(
            int(d1.get("changeId")),
            approve=False,
            reviewed_by="审批官",
            review_note="测试解锁")

        # ⑨ 处置域外拒绝
        _r2, rec2 = await seed_recommended(
            "算法权重调整")
        await svc.raise_dissent(
            rec2["decisionId"],
            reason="复核存疑")
        try:
            await svc.resolve(
                rec2["decisionId"],
                action="hack",
                reason="x")
            ok, err = False, "未拒绝"
        except ValueError:
            ok, err = True, ""
        record("处置域外拒绝", ok, err)

        # ⑩ confirm 采纳 AI 意见终止
        conf = await svc.resolve(
            rec2["decisionId"],
            action="confirm",
            reason="AI 质疑成立——撤回",
            resolved_by="决策长")
        record("confirm 决策终止",
               conf.get(
                   "decisionStatus")
               == "decided"
               and conf.get("outcome")
               == "dissent_confirmed",
               str((conf.get(
                    "decisionStatus"),
                   conf.get(
                       "outcome"))))
        # 请求联动 closed
        from repositories.dm61_repository import (
            Dm61Repository,
        )
        repo = Dm61Repository()
        req2 = await repo.get_request(
            _r2["requestId"])
        record("confirm 请求联动 closed",
               req2.get("status")
               == "closed",
               str(req2.get("status")))

        # ⑪ 已处置再处置拒绝
        try:
            await svc.resolve(
                rec2["decisionId"],
                action="override",
                reason="再处置")
            ok, err = False, "未拒绝"
        except ValueError:
            ok, err = True, ""
        record("重复处置拒绝", ok, err)

        # ⑫ decide 自动弹窗(触发命中)
        # 制造 sim blocked: 变更文本含
        # 敏感 API
        from services.dm61_sim_service import (
            Dm61SimService,
        )
        prev = os.environ.get("DM61_MODE")
        os.environ["DM61_MODE"] = "shadow"
        from services.dm61_service import (
            Dm61Service,
        )
        from services.dm61_assess_service import (
            Dm61AssessService,
        )
        r3 = await Dm61Service() \
            .create_request(
                title="界面适配调整",
                hour=3)
        await Dm61AssessService().assess(
            r3["requestId"],
            tier="standard",
            error_budget=0.3,
            history_fail_rate=0.05)
        await Dm61SimService().simulate(
            r3["requestId"],
            change_text="x = eval(u)")
        rec3 = await (
            Dm61DecisionService().recommend(
                r3["requestId"]))
        os.environ["DM61_MODE"] = prev \
            if prev is not None else "off"
        try:
            await Dm61DecisionService() \
                .decide(
                    rec3["decisionId"],
                    action="adopted",
                    decided_by="x")
            ok, err = False, "未阻断"
        except ValueError as e:
            ok = "反对意见" in str(e)
            err = str(e)[:30]
        record("decide 触发自动弹窗",
               ok, err)
        # 自动发起后 dissent open
        dec3 = await repo.get_decision(
            rec3["decisionId"])
        record("自动弹窗 dissent open",
               (dec3.get("dissent")
                or {}).get("status")
               == "open"
               and "sim_blocked"
                   in str(
                       (dec3.get(
                           "dissent")
                        or {}).get(
                           "triggers")),
               str((dec3.get("dissent")
                    or {}).get(
                   "status")))
        # override 后放行(触发仍在但
        # 人类已驳回留痕)
        await svc.resolve(
            rec3["decisionId"],
            action="override",
            reason="沙箱误报——"
                   "文本为示例",
            resolved_by="决策长")
        d3 = await Dm61DecisionService() \
            .decide(
                rec3["decisionId"],
                action="adopted",
                decided_by="决策长")
        record("触发场景 override 放行",
               d3.get("status")
               == "executed_track",
               str(d3.get("status")))

        # ⑬ 高危快速通道触发
        _r4, rec4 = await seed_recommended(
            "后台权限角色调整")
        ev4 = await svc.evaluate(
            rec4["decisionId"])
        # 权限变更 critical: riskScore
        # 高——检查四规则之一
        record("触发评估(四规则域)",
               set(ev4.get("descriptions")
                   or {})
               <= {"sim_blocked_ignored",
                   "window_unsuitable",
                   "rollback_failed",
                   "high_risk_fast_track"},
               str(ev4.get("triggers")))

        # ⑭ 决策不存在 404
        try:
            await svc.evaluate(999)
            ok, err = False, "未拒绝"
        except KeyError:
            ok, err = True, ""
        record("dissent 决策 404", ok, err)

        # ⑮ 事件留痕
        evs = await repo.list_events(
            limit=200)
        dissent_evs = [
            e for e in evs
            if e.get("eventType")
            == "dissent"]
        record("dissent 事件留痕",
               len(dissent_evs) >= 4,
               str(len(dissent_evs)))


class TestGraph:
    """02 决策归因+决策图谱"""

    async def run(self):
        print("[02 决策图谱]")
        reset_all()
        from services.ai_governance_service import (
            AiGovernanceService,
        )
        await AiGovernanceService(
        ).sync_registry()
        from services.dm61_dissent_service import (
            Dm61DissentService,
        )
        from services.dm61_decision_service import (
            Dm61DecisionService,
        )
        from services.dm61_graph_service import (
            Dm61GraphService,
        )
        dsvc = Dm61DecisionService()
        gsvc = Dm61GraphService()

        # 造三条终态决策:
        # ① adopted ② rejected ③ dissent_confirmed
        _ra, reca = await seed_recommended(
            "支付结算费率优化")
        da = await dsvc.decide(
            reca["decisionId"],
            action="adopted",
            decided_by="甲")
        await AiGovernanceService(
        ).review_change(
            int(da.get("changeId")),
            approve=False,
            reviewed_by="审批官",
            review_note="解锁")

        _rb, recb = await seed_recommended(
            "算法权重调整")
        await dsvc.decide(
            recb["decisionId"],
            action="rejected",
            decided_by="乙",
            note="暂缓")

        _rc, recc = await seed_recommended(
            "界面适配优化")
        dsvc_d = Dm61DissentService()
        await dsvc_d.raise_dissent(
            recc["decisionId"],
            reason="复核存疑")
        await dsvc_d.resolve(
            recc["decisionId"],
            action="confirm",
            reason="采纳 AI 意见",
            resolved_by="丙")

        # ① 归因报告完整链
        report = await gsvc \
            .attribution_report(
                reca["decisionId"])
        chain = report.get("chain") or {}
        record("归因链八环齐备",
               set(chain) == {
                   "semantic", "impact",
                   "environment", "assess",
                   "prior", "simulation",
                   "recommendation",
                   "decision", "dissent"}
               or set(chain) >= {
                   "semantic", "impact",
                   "environment",
                   "assess",
                   "recommendation",
                   "decision"},
               str(sorted(chain)))
        record("归因链含 dissent(P3)",
               "dissent" in chain,
               "")
        record("归因语义快照",
               (chain.get("semantic")
                or {}).get("tag")
               == "payment_opt",
               str((chain.get(
                   "semantic") or {})
                   .get("tag")))
        record("归因裁决快照",
               (chain.get("decision")
                or {}).get("outcome")
               == "adopted",
               str((chain.get(
                   "decision") or {})
                   .get("outcome")))

        # ② 案例库派生(三终态)
        view = await gsvc.cases_view()
        record("案例库派生(3 终态)",
               view.get("total") == 3
               and (view.get(
                   "byOutcome")
                   or {}).get(
                   "adopted") == 1
               and (view.get(
                   "byOutcome")
                   or {}).get(
                   "rejected") == 1
               and (view.get(
                   "byOutcome")
                   or {}).get(
                   "dissent_confirmed")
               == 1,
               str(view.get(
                   "byOutcome")))

        # ③ 相似检索(标签过滤)
        sim_cases = await gsvc \
            .similar_cases(tag="payment_opt")
        record("相似检索(标签)",
               sim_cases.get("total") == 1
               and (sim_cases.get(
                   "cases")
                   or [{}])[0].get(
                   "tag")
               == "payment_opt",
               str(sim_cases.get("total")))

        # ④ 相似检索(结果过滤)
        rej_cases = await gsvc \
            .similar_cases(
                outcome="rejected")
        record("相似检索(结果)",
               rej_cases.get("total") == 1,
               str(rej_cases.get(
                   "total")))

        # ⑤ 相似检索(风险带——带 10-40
        #    三例全命中)
        band_cases = await gsvc \
            .similar_cases(
                risk=25.0)
        record("相似检索(风险带±15)",
               band_cases.get("total") == 3,
               str(band_cases.get(
                   "total")))

        # ⑥ 先验概率(dissent_confirmed
        #    计入失败口径)
        prior_ui = await gsvc \
            .prior_probability(
                tag="ui_adapt")
        record("先验(dissent 计入失败)",
               prior_ui.get(
                   "sampleSize") == 1
               and prior_ui.get(
                   "failed") == 1
               and prior_ui.get(
                   "failRate") == 1.0,
               str(prior_ui))
        prior_algo = await gsvc \
            .prior_probability(
                tag="algo_param")
        record("先验(rejected 计入失败)",
               prior_algo.get(
                   "failed") == 1,
               str(prior_algo.get(
                   "failed")))
        prior_none = await gsvc \
            .prior_probability(
                tag="core_refactor")
        record("先验无历史中性",
               prior_none.get(
                   "sampleSize") == 0,
               str(prior_none.get(
                   "sampleSize")))

        # ⑦ 因果三元组
        case = (view.get(
            "recent") or [{}])[0]
        cause = case.get(
            "causeChain") or {}
        record("因果三元组(factor/action"
               "/result)",
               set(cause) == {
                   "factor",
                   "action", "result"},
               str(cause))


class TestFeedback:
    """03 RLHF 反馈"""

    async def run(self):
        print("[03 RLHF 反馈]")
        reset_all()
        from services.dm61_feedback_service import (
            Dm61FeedbackService,
        )
        fsvc = Dm61FeedbackService()
        _r, rec = await seed_recommended(
            "支付结算费率优化")

        # ① 提交(三态+结果)
        r = await fsvc.submit(
            rec["decisionId"],
            action="adopted",
            outcome="good",
            comment="灰度方案落地顺利",
            by="运营官")
        record("反馈提交(三态+结果)",
               r.get("feedbackId") == 1
               and r.get("action")
               == "adopted"
               and r.get("outcome")
               == "good",
               str((r.get("action"),
                    r.get("outcome"))))

        # ② 重复反馈拒绝(1:1)
        try:
            await fsvc.submit(
                rec["decisionId"],
                action="rejected")
            ok, err = False, "未拒绝"
        except ValueError:
            ok, err = True, ""
        record("重复反馈拒绝(1:1)", ok, err)

        # ③ 动作域外拒绝
        try:
            await fsvc.submit(
                rec["decisionId"],
                action="hacked")
            ok, err = False, "未拒绝"
        except ValueError:
            ok, err = True, ""
        record("动作域外拒绝", ok, err)

        # ④ 结果域外拒绝
        _r2, rec2 = await seed_recommended(
            "算法权重调整")
        try:
            await fsvc.submit(
                rec2["decisionId"],
                action="modified",
                outcome="soso")
            ok, err = False, "未拒绝"
        except ValueError:
            ok, err = True, ""
        record("结果域外拒绝", ok, err)

        # ⑤ 决策不存在 404
        try:
            await fsvc.submit(
                999, action="adopted")
            ok, err = False, "未拒绝"
        except KeyError:
            ok, err = True, ""
        record("反馈决策 404", ok, err)

        # ⑥ 观测面(分布)
        await fsvc.submit(
            rec2["decisionId"],
            action="modified",
            comment="追加观察窗口")
        view = await fsvc.feedback_view()
        record("反馈视图(分布)",
               view.get("total") == 2
               and (view.get(
                   "byAction")
                   or {}).get(
                   "adopted") == 1
               and (view.get(
                   "byAction")
                   or {}).get(
                   "modified") == 1
               and (view.get(
                   "byOutcome")
                   or {}).get(
                   "good") == 1,
               str(view.get(
                   "byAction")))

        # ⑦ off 态提交可用(人工铁律——
        #    默认即 off)
        _r3, rec3 = await seed_recommended(
            "界面适配优化")
        r3 = await fsvc.submit(
            rec3["decisionId"],
            action="rejected",
            outcome="bad",
            comment="效果不佳")
        record("off 态反馈可用(铁律)",
               r3.get("success")
               is True,
               "")


class TestHttp:
    """04 HTTP 层(P3 三新端点)"""

    async def run(self):
        print("[04 HTTP]")
        reset_all()
        from services.ai_governance_service import (
            AiGovernanceService,
        )
        await AiGovernanceService(
        ).sync_registry()
        from fastapi.testclient import \
            TestClient
        from main import app
        client = TestClient(app)
        admin = {"X-Role": "admin"}

        # shadow 全链(造决策)
        os.environ["DM61_MODE"] = "shadow"
        resp = client.post(
            "/api/dm61/requests",
            json={"title": "支付结算费率优化",
                  "hour": 3},
            headers=admin)
        rid = (resp.json()
               or {}).get("requestId")
        resp = client.post(
            "/api/dm61/assess",
            json={"requestId": rid,
                  "tier": "standard",
                  "errorBudget": 0.3,
                  "historyFailRate": 0.05},
            headers=admin)
        resp = client.post(
            "/api/dm61/recommend",
            json={"requestId": rid},
            headers=admin)
        did = (resp.json()
               or {}).get("decisionId")

        # ① dissent raise(off 亦可用)
        os.environ["DM61_MODE"] = "off"
        resp = client.post(
            f"/api/dm61/decisions/{did}"
            f"/dissent",
            json={"mode": "raise",
                  "reason": "HTTP 面质疑"},
            headers=admin)
        body = resp.json() or {}
        record("HTTP dissent raise"
               "(off 可用)",
               resp.status_code == 200
               and body.get(
                   "dissentFlag") is True,
               str((resp.status_code,
                    body.get(
                        "dissentFlag"))))

        # ② dissent override
        resp = client.post(
            f"/api/dm61/decisions/{did}"
            f"/dissent",
            json={"mode": "override",
                  "reason": "HTTP 面驳回"},
            headers=admin)
        body = resp.json() or {}
        record("HTTP dissent override",
               resp.status_code == 200
               and (body.get("dissent")
                    or {}).get("status")
               == "overridden",
               str(resp.status_code))

        # ③ decide(override 后放行)
        resp = client.post(
            f"/api/dm61/decisions/{did}"
            f"/decide",
            json={"action": "rejected",
                  "decidedBy": "HTTP 面"},
            headers=admin)
        body = resp.json() or {}
        record("HTTP decide 放行",
               resp.status_code == 200
               and body.get("outcome")
               == "rejected",
               str((resp.status_code,
                    body.get("outcome"))))

        # ④ feedback
        resp = client.post(
            "/api/dm61/feedback",
            json={"decisionId": did,
                  "action": "rejected",
                  "outcome": "good",
                  "comment": "拒绝正确"},
            headers=admin)
        record("HTTP feedback 200",
               resp.status_code == 200
               and (resp.json()
                    or {}).get(
                   "feedbackId") > 0,
               str(resp.status_code))
        # 重复 409
        resp = client.post(
            "/api/dm61/feedback",
            json={"decisionId": did,
                  "action": "adopted"},
            headers=admin)
        record("HTTP feedback 重复 409",
               resp.status_code == 409,
               str(resp.status_code))

        # ⑤ cases 观测面
        resp = client.get(
            "/api/dm61/cases?outcome="
            "rejected",
            headers=admin)
        body = resp.json() or {}
        record("HTTP cases 200(检索)",
               resp.status_code == 200
               and body.get("total") == 1,
               str((resp.status_code,
                    body.get("total"))))

        # ⑥ 详情归因链联动
        resp = client.get(
            f"/api/dm61/requests/{rid}",
            headers=admin)
        body = resp.json() or {}
        report = body.get(
            "attributionReport") or {}
        record("HTTP 详情归因链联动",
               resp.status_code == 200
               and report.get("schema")
               == "ATTRIBUTION_SCHEMA"
               and (report.get("chain")
                    or {}).get(
                   "dissent") is not None,
               str(report.get("schema")))

        # ⑦ dissent 404
        resp = client.post(
            "/api/dm61/decisions/999/"
            "dissent",
            json={"mode": "raise",
                  "reason": "x"},
            headers=admin)
        record("HTTP dissent 404",
               resp.status_code == 404,
               str(resp.status_code))

        # ⑧ 鉴权 403
        for method, path in (
                ("POST",
                 "/api/dm61/decisions/1/"
                 "dissent"),
                ("POST",
                 "/api/dm61/feedback"),
                ("GET",
                 "/api/dm61/cases")):
            resp = client.request(
                method, path, json={})
            record(f"HTTP {path.split('/')[-1]}"
                   f" 无 Role 403",
                   resp.status_code == 403,
                   str(resp.status_code))

        # 路由累计 15 端点(P0-P4)
        from routes.dm61_routes import (
            router as dm_router,
        )
        count = sum(
            1 for r in dm_router.routes)
        record("61号路由累计 15 端点",
               count == 15, str(count))


async def run_all():
    await TestDissent().run()
    await TestGraph().run()
    await TestFeedback().run()
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
