"""61号·AI智能系统升级决策模块 P1 专项测试
(三级决策矩阵+人机协同)

运行方式:
    python test_dm61_p1.py

覆盖(61号计划 §七 P1):
    - 风险评估: 四因子 riskScore+容错
      预算域调节+L1/L2/L3 判定+窗口
      自动升级+先验检索
    - Top3 方案: 确定性规则模板+推荐项
      +推荐理由
    - 人类裁决流: adopted/modified/
      rejected 三态+46号总线提交+L3
      双人复核铁律
    - 状态机: tagged→assessed→
      recommended→decided/executed_track
    - HTTP 层: 3 新端点+鉴权+终审
      不受开关影响
    - QC: L3 永不自治; 裁决留痕
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


async def seed_request(title, source="manual",
                       hour=3):
    """造一条 tagged 态请求"""
    from services.dm61_service import (
        Dm61Service,
    )
    return await Dm61Service().create_request(
        title=title, source=source,
        hour=hour)


class TestAssess:
    """01 风险评估(四因子+三级判定)"""

    async def run(self):
        print("[01 风险评估]")
        reset_all()
        os.environ["DM61_MODE"] = "shadow"
        from services.dm61_assess_service import (
            Dm61AssessService,
        )
        svc = Dm61AssessService()

        # off 铁律
        os.environ["DM61_MODE"] = "off"
        try:
            await svc.assess(1)
            ok, err = False, "未拒绝"
        except ValueError:
            ok, err = True, ""
        record("off 铁律(评估拒绝)", ok, err)
        os.environ["DM61_MODE"] = "shadow"

        # ① L1 观测类低风险
        r1 = await seed_request("日常优化微调")
        a1 = await svc.assess(
            r1["requestId"],
            tier="trusted",
            error_budget=0.9,
            history_fail_rate=0.0)
        record("观测类→L1(低风险)",
               a1["level"] == "L1"
               and a1["riskScore"] < 30,
               str((a1["level"],
                    a1["riskScore"])))
        record("四因子齐备",
               set((a1.get("factors")
                    or {})) == {
                   "sensitivity", "impactScope",
                   "historyFailRate",
                   "confidence", "errorBudget"},
               str(sorted((a1.get(
                   "factors") or {}))))

        # ② 容错预算充足调节为负
        f_budget = (a1.get("factors")
                    or {}).get(
            "errorBudget") or {}
        record("预算充足调节-10",
               f_budget.get("adjust") == -10.0,
               str(f_budget.get("adjust")))

        # ③ 敏感级影响(支付→L2)
        r2 = await seed_request(
            "支付结算费率优化")
        a2 = await svc.assess(
            r2["requestId"],
            tier="standard",
            error_budget=0.3,
            history_fail_rate=0.05)
        record("支付敏感→L2",
               a2["level"] == "L2",
               str((a2["level"],
                    a2["riskScore"])))

        # ④ 铁律: 核心重构/权限→强制 L3
        r3 = await seed_request(
            "核心链路重构提案")
        a3 = await svc.assess(
            r3["requestId"],
            tier="trusted",
            error_budget=0.9,
            history_fail_rate=0.0)
        record("核心重构→强制 L3",
               a3["level"] == "L3"
               and a3["forcedL3Tag"]
               is True,
               str((a3["level"],
                    a3["forcedL3Tag"])))
        r4 = await seed_request(
            "后台权限角色调整")
        a4 = await svc.assess(
            r4["requestId"],
            tier="trusted",
            error_budget=0.9,
            history_fail_rate=0.0)
        record("权限变更→强制 L3",
               a4["level"] == "L3",
               str(a4["level"]))

        # ⑤ 容错预算耗尽→强制 L3
        r5 = await seed_request("日常优化微调二")
        a5 = await svc.assess(
            r5["requestId"],
            tier="trusted",
            error_budget=0.05,
            history_fail_rate=0.0)
        record("预算耗尽→强制 L3",
               a5["level"] == "L3"
               and a5["budgetForcedL3"]
               is True,
               str((a5["level"],
                    a5["budgetForcedL3"])))

        # ⑥ 窗口不适宜自动升一级
        r6 = await seed_request(
            "支付结算费率优化二", hour=20)
        a6 = await svc.assess(
            r6["requestId"],
            tier="standard",
            error_budget=0.3,
            history_fail_rate=0.05)
        record("窗口收紧→升一级",
               a6["upgradedByWindow"]
               is True,
               str((a6["level"],
                    a6["windowLevel"])))

        # ⑦ 置信度反向(restricted 加分)
        a7 = await svc.assess(
            (await seed_request(
                "算法权重调整"))["requestId"],
            tier="restricted",
            error_budget=0.3,
            history_fail_rate=0.05)
        f_conf = (a7.get("factors")
                  or {}).get(
            "confidence") or {}
        record("置信度反向(restricted=70)",
               f_conf.get("score") == 70.0,
               str(f_conf.get("score")))

        # ⑧ 状态机: tagged→assessed
        from repositories.dm61_repository import (
            Dm61Repository,
        )
        req = await Dm61Repository() \
            .get_request(r1["requestId"])
        record("状态机 tagged→assessed",
               req.get("status")
               == "assessed"
               and req.get("assessId") == 1,
               str(req.get("status")))

        # ⑨ 重复评估拒绝
        try:
            await svc.assess(
                r1["requestId"])
            ok, err = False, "未拒绝"
        except ValueError:
            ok, err = True, ""
        record("重复评估拒绝", ok, err)

        # ⑩ 评估不存在 404
        try:
            await svc.assess(999)
            ok, err = False, "未拒绝"
        except KeyError:
            ok, err = True, ""
        record("评估请求 404", ok, err)

        os.environ["DM61_MODE"] = "off"


class TestRecommend:
    """02 Top3 方案生成"""

    async def run(self):
        print("[02 Top3 方案]")
        reset_all()
        os.environ["DM61_MODE"] = "shadow"
        from services.dm61_assess_service import (
            Dm61AssessService,
        )
        from services.dm61_decision_service import (
            Dm61DecisionService,
        )
        assess_svc = Dm61AssessService()
        decision_svc = Dm61DecisionService()

        # off 铁律
        os.environ["DM61_MODE"] = "off"
        try:
            await decision_svc.recommend(1)
            ok, err = False, "未拒绝"
        except ValueError:
            ok, err = True, ""
        record("off 铁律(推荐拒绝)", ok, err)
        os.environ["DM61_MODE"] = "shadow"

        # ① 未评估推荐拒绝
        r1 = await seed_request(
            "支付结算费率优化")
        try:
            await decision_svc.recommend(
                r1["requestId"])
            ok, err = False, "未拒绝"
        except ValueError:
            ok, err = True, ""
        record("未评估推荐拒绝", ok, err)

        # ② L2 Top3 方案
        await assess_svc.assess(
            r1["requestId"],
            tier="standard",
            error_budget=0.3,
            history_fail_rate=0.05)
        rec = await decision_svc.recommend(
            r1["requestId"])
        record("Top3 方案齐备",
               len(rec.get("options")
                   or []) == 3,
               str(len(rec.get("options")
                        or [])))
        record("推荐项唯一",
               sum(1 for o in
                   rec.get("options")
                   if o.get(
                       "recommended")) == 1,
               "")
        record("L2 推荐灰度执行",
               (rec.get("options") or [{}])[
                   rec.get(
                       "recommendedIndex")
                   - 1].get("name")
               == "灰度执行",
               str(rec.get(
                   "recommendedIndex")))
        record("推荐理由确定性",
               "推荐" in str(
                   rec.get("reason"))
               and "风险分" in str(
                   rec.get("reason")),
               str(rec.get("reason"))[:40])

        # ③ L1 方案模板
        r2 = await seed_request("日常优化微调")
        await assess_svc.assess(
            r2["requestId"],
            tier="trusted",
            error_budget=0.9,
            history_fail_rate=0.0)
        rec2 = await decision_svc.recommend(
            r2["requestId"])
        record("L1 推荐直接执行",
               rec2.get("level") == "L1"
               and (rec2.get("options")
                    or [{}])[
                   rec2.get(
                       "recommendedIndex")
                   - 1].get("name")
               == "直接执行",
               str(rec2.get("level")))

        # ④ L3 方案模板
        r3 = await seed_request(
            "后台权限角色调整")
        await assess_svc.assess(
            r3["requestId"],
            tier="trusted",
            error_budget=0.9,
            history_fail_rate=0.0)
        rec3 = await decision_svc.recommend(
            r3["requestId"])
        record("L3 推荐深度复核",
               rec3.get("level") == "L3"
               and (rec3.get("options")
                    or [{}])[
                   rec3.get(
                       "recommendedIndex")
                   - 1].get("name")
               == "深度复核",
               str(rec3.get("level")))

        # ⑤ 状态机+决策留痕
        from repositories.dm61_repository import (
            Dm61Repository,
        )
        req = await Dm61Repository() \
            .get_request(r1["requestId"])
        record("状态机 assessed→recommended",
               req.get("status")
               == "recommended"
               and req.get("decisionId")
               == 1,
               str(req.get("status")))
        dec = await Dm61Repository() \
            .get_decision(1)
        record("决策留痕(attribution)",
               isinstance(
                   dec.get("attribution"),
                   dict)
               and dec.get(
                   "auditTrail"),
               "")

        # ⑥ 重复推荐拒绝
        try:
            await decision_svc.recommend(
                r1["requestId"])
            ok, err = False, "未拒绝"
        except ValueError:
            ok, err = True, ""
        record("重复推荐拒绝", ok, err)

        os.environ["DM61_MODE"] = "off"


class TestDecide:
    """03 人类裁决流(终审人工铁律)"""

    async def run(self):
        print("[03 人类裁决]")
        reset_all()
        # 46号入册(幂等)
        from services.ai_governance_service import (
            AiGovernanceService,
        )
        await AiGovernanceService(
        ).sync_registry()

        # 终审不受开关影响(off 亦可裁决)
        os.environ["DM61_MODE"] = "off"
        from services.dm61_assess_service import (
            Dm61AssessService,
        )
        from services.dm61_decision_service import (
            Dm61DecisionService,
        )

        # 造链: 创建→评估→推荐(off 下创建
        # 需 shadow——先切回)
        os.environ["DM61_MODE"] = "shadow"
        assess_svc = Dm61AssessService()
        decision_svc = Dm61DecisionService()

        # ① rejected 裁决(off 态终审可用)
        r1 = await seed_request(
            "支付结算费率优化")
        await assess_svc.assess(
            r1["requestId"],
            tier="standard",
            error_budget=0.3,
            history_fail_rate=0.05)
        rec1 = await decision_svc.recommend(
            r1["requestId"])
        os.environ["DM61_MODE"] = "off"
        r_dec = await decision_svc.decide(
            rec1["decisionId"],
            action="rejected",
            decided_by="风控官",
            note="影响面过大暂缓")
        record("rejected 裁决(off 可用)",
               r_dec["status"] == "decided"
               and r_dec["outcome"]
               == "rejected",
               str((r_dec["status"],
                    r_dec["outcome"])))

        # 请求状态→closed
        from repositories.dm61_repository import (
            Dm61Repository,
        )
        repo = Dm61Repository()
        req1 = await repo.get_request(
            r1["requestId"])
        record("请求联动 closed",
               req1.get("status")
               == "closed",
               str(req1.get("status")))

        # 重复裁决拒绝
        try:
            await decision_svc.decide(
                rec1["decisionId"],
                action="adopted")
            ok, err = False, "未拒绝"
        except ValueError:
            ok, err = True, ""
        record("重复裁决拒绝", ok, err)

        # ② adopted 裁决→46号总线
        os.environ["DM61_MODE"] = "shadow"
        r2 = await seed_request(
            "算法权重调整")
        await assess_svc.assess(
            r2["requestId"],
            tier="trusted",
            error_budget=0.3,
            history_fail_rate=0.05)
        rec2 = await decision_svc.recommend(
            r2["requestId"])
        os.environ["DM61_MODE"] = "off"
        r_dec2 = await decision_svc.decide(
            rec2["decisionId"],
            action="adopted",
            decided_by="技术负责人",
            note="灰度放量执行")
        record("adopted→46号总线提交",
               r_dec2["status"]
               == "executed_track"
               and r_dec2["changeId"] > 0,
               str((r_dec2["status"],
                    r_dec2["changeId"])))
        # 处置 pending(同档案防重复——
        # 46号驳回解锁留痕; config 执行
        # 走人工通道)
        await AiGovernanceService(
        ).review_change(
            r_dec2["changeId"],
            approve=False,
            reviewed_by="总线审批官",
            review_note="测试解锁")
        req2 = await repo.get_request(
            r2["requestId"])
        record("请求联动 executed_track",
               req2.get("status")
               == "executed_track"
               and req2.get("changeId")
               == r_dec2["changeId"],
               str(req2.get("status")))

        # ③ 方案选择(非推荐项)
        os.environ["DM61_MODE"] = "shadow"
        r3 = await seed_request(
            "界面适配优化调整")
        await assess_svc.assess(
            r3["requestId"],
            tier="standard",
            error_budget=0.9,
            history_fail_rate=0.0)
        rec3 = await decision_svc.recommend(
            r3["requestId"])
        os.environ["DM61_MODE"] = "off"
        r_dec3 = await decision_svc.decide(
            rec3["decisionId"],
            action="modified",
            decided_by="产品经理",
            option_index=2,
            modified_detail="改为影子观察"
                            "48h 后再评估",
            note="追加观察窗口")
        record("modified 裁决(选方案2)",
               r_dec3["chosen"].get(
                   "index") == 2
               and r_dec3["changeId"] > 0,
               str((r_dec3["chosen"]
                    .get("index"),
                    r_dec3["changeId"])))
        # 处置 pending(解锁后续 L3 裁决)
        await AiGovernanceService(
        ).review_change(
            r_dec3["changeId"],
            approve=False,
            reviewed_by="总线审批官",
            review_note="测试解锁")

        # ④ modified 缺修正内容拒绝
        os.environ["DM61_MODE"] = "shadow"
        r4 = await seed_request(
            "界面适配优化再调整")
        await assess_svc.assess(
            r4["requestId"],
            tier="standard",
            error_budget=0.9,
            history_fail_rate=0.0)
        rec4 = await decision_svc.recommend(
            r4["requestId"])
        os.environ["DM61_MODE"] = "off"
        try:
            await decision_svc.decide(
                rec4["decisionId"],
                action="modified",
                decided_by="x")
            ok, err = False, "未拒绝"
        except ValueError:
            ok, err = True, ""
        record("modified 缺内容拒绝", ok, err)

        # ⑤ L3 双人复核铁律
        os.environ["DM61_MODE"] = "shadow"
        r5 = await seed_request(
            "后台权限角色调整")
        await assess_svc.assess(
            r5["requestId"],
            tier="trusted",
            error_budget=0.9,
            history_fail_rate=0.0)
        rec5 = await decision_svc.recommend(
            r5["requestId"])
        os.environ["DM61_MODE"] = "off"
        try:
            await decision_svc.decide(
                rec5["decisionId"],
                action="adopted",
                decided_by="管理员甲")
            ok, err = False, "未拒绝"
        except ValueError as e:
            ok, err = "双人" in str(e), \
                str(e)[:30]
        record("L3 缺双人复核拒绝", ok, err)
        try:
            await decision_svc.decide(
                rec5["decisionId"],
                action="adopted",
                decided_by="管理员甲",
                co_reviewer="管理员甲")
            ok, err = False, "未拒绝"
        except ValueError:
            ok, err = True, ""
        record("L3 复核人同名拒绝", ok, err)
        r_dec5 = await decision_svc.decide(
            rec5["decisionId"],
            action="adopted",
            decided_by="管理员甲",
            co_reviewer="合规官乙",
            note="双人复核通过")
        record("L3 双人复核通过",
               r_dec5["coReviewer"]
               == "合规官乙"
               and r_dec5["changeId"] > 0,
               str(r_dec5["coReviewer"]))

        # ⑥ 裁决域外拒绝
        os.environ["DM61_MODE"] = "shadow"
        r6 = await seed_request(
            "算法权重调整二")
        await assess_svc.assess(
            r6["requestId"],
            tier="standard",
            error_budget=0.9,
            history_fail_rate=0.0)
        rec6 = await decision_svc.recommend(
            r6["requestId"])
        os.environ["DM61_MODE"] = "off"
        try:
            await decision_svc.decide(
                rec6["decisionId"],
                action="hacked")
            ok, err = False, "未拒绝"
        except ValueError:
            ok, err = True, ""
        record("裁决域外拒绝", ok, err)

        # ⑦ 方案域外拒绝
        try:
            await decision_svc.decide(
                rec6["decisionId"],
                action="adopted",
                option_index=9)
            ok, err = False, "未拒绝"
        except ValueError:
            ok, err = True, ""
        record("方案域外拒绝", ok, err)

        # ⑧ 裁决不存在 404
        try:
            await decision_svc.decide(
                999, action="adopted")
            ok, err = False, "未拒绝"
        except KeyError:
            ok, err = True, ""
        record("裁决 404", ok, err)

        # ⑨ 审计 trail 留痕
        dec5 = await repo.get_decision(
            rec5["decisionId"])
        trail = dec5.get("auditTrail") or []
        record("审计 trail(decide 步)",
               any(t.get("step") == "decide"
                   for t in trail)
               and dec5.get("decidedBy")
               == "管理员甲",
               str(len(trail)))


class TestHttp:
    """04 HTTP 层(P1 三新端点)"""

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

        # off 决策面 409
        for path in ("/api/dm61/assess",
                     "/api/dm61/recommend"):
            resp = client.post(
                path, json={"requestId": 1},
                headers=admin)
            record(f"HTTP {path.split('/')[-1]}"
                   f" off 409",
                   resp.status_code == 409,
                   str(resp.status_code))

        # shadow 全链
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
        body = resp.json() or {}
        record("HTTP assess 200(L2)",
               resp.status_code == 200
               and body.get("level") == "L2",
               str((resp.status_code,
                    body.get("level"))))

        resp = client.post(
            "/api/dm61/recommend",
            json={"requestId": rid},
            headers=admin)
        body = resp.json() or {}
        did = body.get("decisionId")
        record("HTTP recommend 200(Top3)",
               resp.status_code == 200
               and len(body.get("options")
                       or []) == 3,
               str(len(body.get("options")
                        or [])))

        # decide off 亦可用(人工铁律)
        os.environ["DM61_MODE"] = "off"
        resp = client.post(
            f"/api/dm61/decisions/{did}/decide",
            json={"action": "adopted",
                  "decidedBy": "HTTP 面裁决",
                  "note": "灰度执行"},
            headers=admin)
        body = resp.json() or {}
        record("HTTP decide off 可用",
               resp.status_code == 200
               and body.get("status")
               == "executed_track"
               and body.get("changeId") > 0,
               str((resp.status_code,
                    body.get("status"))))

        # 详情观测面联动
        resp = client.get(
            f"/api/dm61/requests/{rid}",
            headers=admin)
        body = resp.json() or {}
        record("HTTP 详情联动(评估+决策)",
               resp.status_code == 200
               and body.get(
                   "latestAssessment")
               is not None
               and body.get(
                   "latestDecision")
               is not None,
               str(resp.status_code))

        # decide 404
        resp = client.post(
            "/api/dm61/decisions/999/decide",
            json={"action": "adopted"},
            headers=admin)
        record("HTTP decide 404",
               resp.status_code == 404,
               str(resp.status_code))

        # 鉴权 403
        for method, path in (
                ("POST", "/api/dm61/assess"),
                ("POST",
                 "/api/dm61/recommend"),
                ("POST",
                 "/api/dm61/decisions/"
                 "1/decide")):
            resp = client.request(
                method, path, json={})
            record(f"HTTP {path.split('/')[-1]}"
                   f" 无 Role 403",
                   resp.status_code == 403,
                   str(resp.status_code))

        # 路由累计 14 端点(P0-P3)
        from routes.dm61_routes import (
            router as dm_router,
        )
        count = sum(
            1 for r in dm_router.routes)
        record("61号路由累计 14 端点",
               count == 14, str(count))


async def run_all():
    await TestAssess().run()
    await TestRecommend().run()
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
