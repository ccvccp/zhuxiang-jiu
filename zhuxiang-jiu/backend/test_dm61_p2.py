"""61号·AI智能系统升级决策模块 P2 专项测试
(影子沙箱+阈值配置域)

运行方式:
    python test_dm61_p2.py

覆盖(61号计划 §七 P2):
    - 影子沙箱: 静态校验(敏感 API/PII
      红线)+指标回放推演(漂移方向)
      +灰度方案建议(阶梯+advisoryOnly)
      +回滚预案校验(56号预案消费+
      5 分钟可恢复断言)
    - QC: 沙箱零代码执行; 建议不执行
    - 阈值配置域: 46号审批双模
      (submit pending/apply 生效)+
      未经裁决不可生效+assess 阈值联动
    - 状态机: assessed→simulated→
      recommended 衔接
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


async def seed_assessed(title, source="manual",
                        hour=3, proposal_id=None):
    """造一条 assessed 态请求"""
    from services.dm61_service import (
        Dm61Service,
    )
    from services.dm61_assess_service import (
        Dm61AssessService,
    )
    r = await Dm61Service().create_request(
        title=title, source=source,
        hour=hour, proposal_id=proposal_id)
    await Dm61AssessService().assess(
        r["requestId"],
        tier="standard",
        error_budget=0.3,
        history_fail_rate=0.05)
    return r


async def seed_proposal(with_rollback=True):
    """造 56号提案(含/缺回滚预案)"""
    from repositories.aiup56_repository import (
        Aiup56Repository,
    )
    repo = Aiup56Repository()
    pid = await repo.next_proposal_id()
    if with_rollback:
        tasks = [{
            "taskId": 1,
            "title": "参数调优",
            "objective": "评分参数调整",
            "rollbackPlan": {
                "strategy": "配置还原(旧参数快照)",
                "steps": ["恢复注册表参数基线"],
                "dataCleanup":
                    "新增配置项需同步清理引用",
            },
        }]
    else:
        tasks = [{
            "taskId": 1,
            "title": "参数调优",
            "objective": "评分参数调整",
            "rollbackPlan": {
                "strategy": "",
                "steps": [],
                "dataCleanup": "",
            },
        }]
    await repo.save_proposal({
        "proposalId": pid,
        "status": "coded",
        "decision": "propose",
        "tasks": tasks,
        "necessityScore": 50.0,
        "trustScore": 60.0,
        "summary": "测试提案",
        "budgetCap": 10.0,
        "budgetSpent": 0.0,
        "estimatedGain": 0.5,
        "actualGain": 0.0,
        "createdAt": "2026-01-01T00:00:00",
        "updatedAt": "2026-01-01T00:00:00",
    })
    return pid


class TestSimulate:
    """01 影子沙箱推演"""

    async def run(self):
        print("[01 影子沙箱]")
        reset_all()
        from services.dm61_sim_service import (
            Dm61SimService,
        )
        svc = Dm61SimService()

        # off 铁律
        try:
            await svc.simulate(1)
            ok, err = False, "未拒绝"
        except ValueError:
            ok, err = True, ""
        record("off 铁律(推演拒绝)", ok, err)
        os.environ["DM61_MODE"] = "shadow"

        # ① 正常推演(静态通过)
        r1 = await seed_assessed(
            "支付结算费率优化")
        s1 = await svc.simulate(
            r1["requestId"])
        record("正常推演 passed",
               s1["verdict"] == "passed",
               str(s1["verdict"]))
        record("静态关通过",
               (s1["staticGate"]
                or {}).get("passed") is True,
               str((s1["staticGate"]
                    or {}).get("passed")))

        # ② 指标回放(无历史中性)
        replay = s1.get("replay") or {}
        record("回放无历史中性",
               replay.get("sampleSize") == 0
               and replay.get(
                   "direction")
               == "neutral",
               str((replay.get(
                   "sampleSize"),
                   replay.get(
                       "direction"))))

        # ③ 指标回放(有历史漂移)
        r2 = await seed_assessed(
            "支付结算费率再优化")
        s2 = await svc.simulate(
            r2["requestId"])
        replay2 = s2.get("replay") or {}
        record("回放同标签历史",
               replay2.get("sampleSize") == 1
               and replay2.get(
                   "historyAvgRisk")
               is not None,
               str(replay2.get(
                   "sampleSize")))

        # ④ 灰度方案(建议域)
        gray = s1.get("grayscale") or {}
        stages = gray.get("stages") or []
        record("灰度四阶梯(1/5/20/100)",
               [s.get("rolloutPct")
                for s in stages]
               == [1, 5, 20, 100],
               str([s.get("rolloutPct")
                    for s in stages]))
        record("灰度建议域(advisoryOnly)",
               gray.get("advisoryOnly")
               is True
               and bool(gray.get(
                   "pauseRules")),
               str(gray.get(
                   "advisoryOnly")))
        record("灰度指标集封闭",
               (stages[0].get("metrics")
                if stages else [])
               == ["决策准确率",
                   "自治占比",
                   "预警有效率"],
               str((stages[0]
                    or {}).get("metrics")))

        # ⑤ 状态机 assessed→simulated
        from repositories.dm61_repository import (
            Dm61Repository,
        )
        req = await Dm61Repository() \
            .get_request(r1["requestId"])
        record("状态机 assessed→simulated",
               req.get("status")
               == "simulated"
               and req.get("simId") == 1,
               str(req.get("status")))

        # ⑥ 重复推演拒绝
        try:
            await svc.simulate(
                r1["requestId"])
            ok, err = False, "未拒绝"
        except ValueError:
            ok, err = True, ""
        record("重复推演拒绝", ok, err)

        # ⑦ simulated→recommend 衔接
        from services.dm61_decision_service import (
            Dm61DecisionService,
        )
        rec = await (
            Dm61DecisionService().recommend(
                r1["requestId"]))
        record("simulated→recommend 衔接",
               len(rec.get("options")
                   or []) == 3,
               str(len(rec.get("options")
                        or [])))

        # ⑧ 静态关阻断(敏感 API)
        r3 = await seed_assessed(
            "界面适配调整")
        s3 = await svc.simulate(
            r3["requestId"],
            change_text="草案: result = "
                        "eval(user_input)")
        record("敏感 API 阻断",
               s3["verdict"] == "blocked"
               and "eval"
               in str((s3["staticGate"]
                       or {}).get(
                   "violations")),
               str(s3["verdict"]))

        # ⑨ 静态关阻断(PII 明文)
        r4 = await seed_assessed(
            "界面适配再调整")
        s4 = await svc.simulate(
            r4["requestId"],
            change_text="联系 13812345678")
        record("PII 明文阻断",
               s4["verdict"] == "blocked",
               str((s4["staticGate"]
                    or {}).get(
                   "violations")))

        # ⑩ 推演请求 404
        try:
            await svc.simulate(999)
            ok, err = False, "未拒绝"
        except KeyError:
            ok, err = True, ""
        record("推演请求 404", ok, err)

        # ⑪ 未评估推演拒绝
        from services.dm61_service import (
            Dm61Service,
        )
        r5 = await Dm61Service() \
            .create_request(
                title="新请求",
                hour=3)
        try:
            await svc.simulate(
                r5["requestId"])
            ok, err = False, "未拒绝"
        except ValueError:
            ok, err = True, ""
        record("未评估推演拒绝", ok, err)

        os.environ["DM61_MODE"] = "off"


class TestRollback:
    """02 回滚预案校验(56号纯消费)"""

    async def run(self):
        print("[02 回滚预案]")
        reset_all()
        os.environ["DM61_MODE"] = "shadow"
        from services.dm61_sim_service import (
            Dm61SimService,
        )
        svc = Dm61SimService()

        # ① 完整预案通过
        pid = await seed_proposal(
            with_rollback=True)
        r1 = await seed_assessed(
            "参数调优", source="proposal",
            proposal_id=pid)
        s1 = await svc.simulate(
            r1["requestId"])
        rb = s1.get("rollback") or {}
        record("完整预案校验通过",
               rb.get("required") is True
               and rb.get("passed") is True
               and rb.get("plansChecked")
               == 1,
               str(rb.get("passed")))

        # ② 缺失预案阻断
        pid2 = await seed_proposal(
            with_rollback=False)
        r2 = await seed_assessed(
            "参数调优二", source="proposal",
            proposal_id=pid2)
        s2 = await svc.simulate(
            r2["requestId"])
        rb2 = s2.get("rollback") or {}
        record("缺失预案阻断",
               s2["verdict"] == "blocked"
               and rb2.get("passed")
               is False
               and bool(rb2.get("issues")),
               str(rb2.get("passed")))

        # ③ 非提案源(无预案建议)
        r3 = await seed_assessed(
            "界面适配优化")
        s3 = await svc.simulate(
            r3["requestId"])
        rb3 = s3.get("rollback") or {}
        record("非提案源(建议性通过)",
               rb3.get("required")
               is False
               and rb3.get("passed")
               is True,
               str(rb3.get("required")))

        # ④ 提案不存在阻断
        r4 = await seed_assessed(
            "参数调优三", source="proposal",
            proposal_id=999)
        s4 = await svc.simulate(
            r4["requestId"])
        rb4 = s4.get("rollback") or {}
        record("提案不存在阻断",
               s4["verdict"] == "blocked"
               and rb4.get("passed")
               is False,
               str(s4["verdict"]))

        os.environ["DM61_MODE"] = "off"


class TestThreshold:
    """03 阈值配置域(46号审批双模)"""

    async def run(self):
        print("[03 阈值配置域]")
        reset_all()
        from services.ai_governance_service import (
            AiGovernanceService,
        )
        await AiGovernanceService(
        ).sync_registry()
        from services.dm61_threshold_service import (
            Dm61ThresholdService,
        )
        svc = Dm61ThresholdService()

        # off 铁律(submit 决策面)
        try:
            await svc.calibrate_submit(
                40, 60, reason="x")
            ok, err = False, "未拒绝"
        except ValueError:
            ok, err = True, ""
        record("off 铁律(submit 拒绝)",
               ok, err)
        os.environ["DM61_MODE"] = "shadow"

        # ① 非法阈值拒绝
        try:
            await svc.calibrate_submit(
                70, 60, reason="倒置")
            ok, err = False, "未拒绝"
        except ValueError:
            ok, err = True, ""
        record("非法阈值拒绝(L1≥L3)", ok, err)

        # ② 缺理由拒绝
        try:
            await svc.calibrate_submit(
                40, 60)
            ok, err = False, "未拒绝"
        except ValueError:
            ok, err = True, ""
        record("缺理由拒绝", ok, err)

        # ③ submit→46号 pending
        r = await svc.calibrate_submit(
            35, 60,
            requested_by="决策官",
            reason="观测样本扩大微调")
        record("submit→46号 pending",
               r["status"] == "pending"
               and r["changeId"] > 0
               and r["config"] == {
                   "l1MaxRisk": 35.0,
                   "l3MinRisk": 60.0},
               str(r["config"]))

        # ③b 重复 pending 拒绝(apply 前)
        try:
            await svc.calibrate_submit(
                40, 60, reason="再调")
            ok, err = False, "未拒绝"
        except ValueError:
            ok, err = True, ""
        record("重复 pending 拒绝", ok, err)

        # ④ 未裁决不可生效(终审不受开关
        #    影响——off 亦可)
        os.environ["DM61_MODE"] = "off"
        try:
            await svc.calibrate_apply(
                r["changeId"])
            ok, err = False, "未拒绝"
        except ValueError:
            ok, err = True, ""
        record("未经裁决不可生效", ok, err)

        # ⑤ 46号裁决(approve——config
        #    执行器抛异常但 reviewedBy
        #    已留痕)
        try:
            await AiGovernanceService(
            ).review_change(
                int(r["changeId"]),
                approve=True,
                reviewed_by="治理官")
        except ValueError:
            pass

        # ⑥ apply 生效
        r2 = await svc.calibrate_apply(
            r["changeId"],
            applied_by="决策总监")
        record("裁决后生效(apply)",
               r2["status"] == "applied"
               and r2["config"] == {
                   "l1MaxRisk": 35.0,
                   "l3MinRisk": 60.0},
               str(r2["config"]))

        # ⑦ 重复生效拒绝
        try:
            await svc.calibrate_apply(
                r["changeId"])
            ok, err = False, "未拒绝"
        except ValueError:
            ok, err = True, ""
        record("重复生效拒绝", ok, err)

        # ⑧ 阈值视图
        view = await svc.thresholds_view()
        record("阈值视图(生效值+留痕)",
               view["active"] == {
                   "l1MaxRisk": 35.0,
                   "l3MinRisk": 60.0,
                   "source": "applied"}
               and view["registry"].get(
                   "appliedBy")
               == "决策总监",
               str(view["active"]))

        # ⑨ assess 阈值联动(L1 线收紧
        #    30→35——高风险观测类仍 L2)
        os.environ["DM61_MODE"] = "shadow"
        from services.dm61_service import (
            Dm61Service,
        )
        from services.dm61_assess_service import (
            Dm61AssessService,
        )
        # 观测类 riskScore~4.5 < 35 仍 L1
        rr = await Dm61Service() \
            .create_request(title="文案微调",
                           hour=3)
        a = await Dm61AssessService() \
            .assess(rr["requestId"],
                    tier="trusted",
                    error_budget=0.9,
                    history_fail_rate=0.0)
        record("联动后观测类仍 L1",
               a["level"] == "L1",
               str(a["level"]))

        # ⑩ get_active fail-soft 回落
        view2 = await (
            Dm61ThresholdService()
            .get_active())
        record("生效读取(applied 源)",
               view2["source"] == "applied"
               and view2["l1MaxRisk"]
               == 35.0,
               str(view2))

        os.environ["DM61_MODE"] = "off"


class TestHttp:
    """04 HTTP 层(P2 三新端点)"""

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
        resp = client.post(
            "/api/dm61/simulate",
            json={"requestId": 1},
            headers=admin)
        record("HTTP simulate off 409",
               resp.status_code == 409,
               str(resp.status_code))
        resp = client.post(
            "/api/dm61/threshold/calibrate",
            json={"mode": "submit",
                  "l1MaxRisk": 40,
                  "l3MinRisk": 60,
                  "reason": "x"},
            headers=admin)
        record("HTTP calibrate off 409",
               resp.status_code == 409,
               str(resp.status_code))

        # 观测面 off 可用
        resp = client.get(
            "/api/dm61/thresholds",
            headers=admin)
        body = resp.json() or {}
        record("HTTP thresholds 观测面 200",
               resp.status_code == 200
               and body.get("active",
                            {}).get(
                   "l1MaxRisk") == 30.0,
               str((resp.status_code,
                    (body.get("active")
                     or {}).get(
                        "l1MaxRisk"))))

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
        resp = client.post(
            "/api/dm61/simulate",
            json={"requestId": rid},
            headers=admin)
        body = resp.json() or {}
        record("HTTP simulate 200(passed)",
               resp.status_code == 200
               and body.get("verdict")
               == "passed"
               and (body.get(
                   "grayscale")
                   or {}).get(
                   "advisoryOnly")
               is True,
               str((resp.status_code,
                    body.get("verdict"))))

        # calibrate submit 200
        resp = client.post(
            "/api/dm61/threshold/calibrate",
            json={"mode": "submit",
                  "l1MaxRisk": 25,
                  "l3MinRisk": 70,
                  "requestedBy": "HTTP 面",
                  "reason": "回归测试"},
            headers=admin)
        body = resp.json() or {}
        record("HTTP calibrate submit 200",
               resp.status_code == 200
               and body.get("status")
               == "pending",
               str((resp.status_code,
                    body.get("status"))))

        # 非法阈值 409
        resp = client.post(
            "/api/dm61/threshold/calibrate",
            json={"mode": "submit",
                  "l1MaxRisk": 80,
                  "l3MinRisk": 60,
                  "reason": "倒置"},
            headers=admin)
        record("HTTP calibrate 非法 409",
               resp.status_code == 409,
               str(resp.status_code))

        # 鉴权 403
        for method, path in (
                ("POST", "/api/dm61/simulate"),
                ("POST",
                 "/api/dm61/threshold/"
                 "calibrate"),
                ("GET",
                 "/api/dm61/thresholds")):
            resp = client.request(
                method, path, json={})
            record(f"HTTP {path.split('/')[-1]}"
                   f" 无 Role 403",
                   resp.status_code == 403,
                   str(resp.status_code))

        # 路由累计 11 端点
        from routes.dm61_routes import (
            router as dm_router,
        )
        count = sum(
            1 for r in dm_router.routes)
        record("61号路由累计 11 端点",
               count == 11, str(count))
        os.environ["DM61_MODE"] = "off"


async def run_all():
    await TestSimulate().run()
    await TestRollback().run()
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
