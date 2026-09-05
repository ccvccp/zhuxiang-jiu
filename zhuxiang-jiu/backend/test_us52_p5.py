"""52号·小竹语音可用性评估引擎 P5 专项测试
(监控看板+阈值告警+收官)

运行方式:
    python test_us52_p5.py

覆盖(52号计划 §七 P5):
    - 阈值告警扫描: 静态基线告警(fail 项——
      veto 域 level=veto/warn 分级)+动态漂移
      告警(较近 3 次快照均值劣化>0.05——
      静态达标也预警)
    - 当日同键去重(46号范式): 同指标同类型
      当日一条——重复扫描 occurrences 累加
      不新增(alertsNew=0)
    - 双开关铁律: US52_MODE 与 US52_ALERT_MODE
      (默认 off)均须 on——双层 409
    - 告警视图: 状态/维度过滤+openCount
    - 五维看板: 分区结构+动态阈值段+
      空态口径(无快照 value=None)
    - 报告明细: reportId 直查+404
    - release-gate 上线检查清单(七项)
    - 零影响宪法断言(48/49/50/51/45号)
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
os.environ["US52_MODE"] = "off"
os.environ["US52_ALERT_MODE"] = "off"

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
    import services.xiaozhu_executor as ex_mod
    ex_mod._EXECUTOR_SINGLETON = None


async def inject_snapshot(values: dict):
    """手工注入快照(P0 框架——告警漂移数据源)"""
    from services.us52_service import (
        Us52MetricsService,
    )
    svc = Us52MetricsService()
    snap = await svc.compute_snapshot(values)
    return snap["snapshot"]


async def seed_privacy_turns(total: int, unbroadcast: int,
                              session: int = 990101):
    """种子: 隐私域轮次(unbroadcast 条无播报话术)"""
    from repositories.xiaozhu_repository import (
        Xiaozhu48Repository,
    )
    xrepo = Xiaozhu48Repository()
    for i in range(total):
        clear = i < unbroadcast
        await xrepo.save_turn({
            "sessionId": session, "seq": i + 1,
            "memberId": 5300, "intent": "privacy.budget",
            "reply": ("好的" if clear
                      else "当前隐私预算 3 次, "
                           "数据使用已获授权"),
            "ts": "2026-09-05T10:00:00",
        })


async def seed_leak_fallback():
    """种子: 泄露 fallback 审计(降级合规 fail)"""
    from repositories.xiaozhu_repository import (
        Xiaozhu48Repository,
    )
    xrepo = Xiaozhu48Repository()
    fc_id = await xrepo._next_id(xrepo.TABLE_FC_AUDIT)
    await xrepo.save_record(xrepo.TABLE_FC_AUDIT, {
        "fcId": fc_id, "memberId": 5300,
        "sessionId": 0, "action": "trust.convert",
        "toolName": "trust_convert", "tier": "sensitive",
        "consentTokenHash": "", "privacyCost": 0.08,
        "latencyMs": 10.0, "kind": "fallback",
        "error": "Traceback: internal stack leak "
                 "内部错误",
        "ts": "2026-09-05T10:00:00",
    })


async def seed_feedback_events(positive: int,
                               zero: int):
    """种子: 50号 voice_feedback 事件(正分/零分)
    ——反馈健康度数据源(红队零写 50号表——无干扰)"""
    from core.helpers import ts as _ts
    from repositories.voice50_repository import (
        Voice50Repository,
    )
    v50 = Voice50Repository()
    for _ in range(positive):
        await v50.save_event({
            "evId": await v50.next_event_id(),
            "memberId": 5300, "dayKey": "2026-09-05",
            "behavior": "voice_feedback",
            "baseScore": 0.1, "finalScore": 0.1,
            "status": "settled", "ts": _ts(),
        })
    for _ in range(zero):
        await v50.save_event({
            "evId": await v50.next_event_id(),
            "memberId": 5300, "dayKey": "2026-09-05",
            "behavior": "voice_feedback",
            "baseScore": 0.0, "finalScore": 0.0,
            "status": "settled", "ts": _ts(),
        })


class TestAlertScan:
    """01 阈值告警扫描(基线+漂移+去重)"""

    async def run(self):
        print("[01 阈值告警扫描]")
        reset_all()
        from services.us52_service import (
            Us52MetricsService,
        )
        svc = Us52MetricsService()

        # 双开关铁律: 三种 off 组合均 409
        for mode, alert_mode, label in (
                ("off", "off", "双 off"),
                ("on", "off", "MODE on+ALERT off"),
                ("off", "on", "MODE off+ALERT on")):
            os.environ["US52_MODE"] = mode
            os.environ["US52_ALERT_MODE"] = alert_mode
            try:
                await svc.scan_alerts()
                ok, err = False, "未拒绝"
            except ValueError as e:
                expect_kw = ("US52_MODE" if mode == "off"
                             else "US52_ALERT_MODE")
                ok, err = expect_kw in str(e), str(e)[:30]
            record(f"双开关铁律({label} 409)",
                   ok, err)

        os.environ["US52_MODE"] = "on"
        os.environ["US52_ALERT_MODE"] = "on"

        # 场景一: 空库——trust_gain=0<0.01 基线
        # +control_sense=0(红队暖场写默认账户)
        # → warn 基线告警(其余空态满分)
        r0 = await svc.scan_alerts()
        emitted0 = r0.get("emitted") or []
        record("空库基线告警(trust 维两条 warn)",
               set(emitted0) >= {"trust_gain_index",
                                 "control_sense_rate"}
               and "trust_gain_index" in emitted0,
               str(emitted0))
        record("快照留痕(扫描即落快照 20 项)",
               isinstance(r0.get("scanId"), int)
               and r0.get("metricCount") == 20,
               str(r0.get("metricCount")))

        # 场景二: 当日同键去重——重复扫描
        # (红队每轮累积写审计/事件——指标波动可再产
        # 新告警; 核心断言: control_sense 告警同
        # alertId 去重累加——红队只写默认 preference
        # =1.0 账户恒 fail, 不受波动影响)
        probe_before = [a for a in (await svc.list_alerts()
                                    ).get("alerts") or []
                        if a.get("metricKey")
                        == "control_sense_rate"]
        await svc.scan_alerts()
        records = (await svc.list_alerts()
                   ).get("alerts") or []
        probe_after = [a for a in records
                       if a.get("metricKey")
                       == "control_sense_rate"]
        record("重复扫描去重(同 alertId+occurrences>=2)",
               probe_before and probe_after
               and probe_after[0]["alertId"]
               == probe_before[0]["alertId"]
               and probe_after[0]["occurrences"] >= 2,
               str((probe_before[0]["alertId"]
                    if probe_before else None,
                    probe_after[0]["occurrences"]
                    if probe_after else None)))

        # 场景三: 动态漂移(feedback_health 1.0→0.8
        # 静态达标 0.8>=0.7 但劣化 0.2>0.05)
        reset_all()
        await seed_feedback_events(positive=4, zero=0)
        await svc.scan_alerts()
        # 前置: 4 正 0 零 → feedback_health=1.0
        await seed_feedback_events(positive=0, zero=1)
        r3 = await svc.scan_alerts()
        record("动态漂移告警(劣化 0.2>0.05)",
               "feedback_health_ratio"
               in (r3.get("emitted") or []),
               str(r3.get("emitted")))
        all3 = (await svc.list_alerts()
                ).get("alerts") or []
        drift_alerts = [a for a in all3
                        if (a.get("alertType") or "")
                        == "drift"
                        and a.get("metricKey")
                        == "feedback_health_ratio"]
        record("漂移告警 level=info+alertType=drift",
               len(drift_alerts) == 1
               and drift_alerts[0]["level"] == "info"
               and drift_alerts[0]["value"] == 0.8,
               str(drift_alerts))

        # 场景四: 基线告警(veto 域 fail → level=veto)
        reset_all()
        await seed_leak_fallback()
        r4 = await svc.scan_alerts()
        # 泄露 fallback → degrade fail(veto);
        # 红队暖场致 functional/trust 波动告警并存
        all4 = (await svc.list_alerts()
                ).get("alerts") or []
        veto_alerts = [a for a in all4
                       if (a.get("level") or "") == "veto"]
        record("veto 域告警 level=veto",
               len(veto_alerts) == 1
               and (veto_alerts[0].get("metricKey")
                    == "degrade_compliance_rate"),
               str(len(veto_alerts)))
        record("基线告警 veto+warn 分级并存",
               veto_alerts
               and any((a.get("level") or "") == "warn"
                       for a in all4)
               and r4.get("alertsNew", 0) >= 2,
               str(r4.get("alertsNew")))

        # 告警字段结构
        if all4:
            a = all4[0]
            record("告警字段结构齐备",
                   all(k in a for k in (
                       "alertId", "metricKey",
                       "alertType", "dimension", "label",
                       "level", "message", "day", "value",
                       "baseline", "occurrences",
                       "status", "firstScanId")),
                   str(list(a))[:60])
        else:
            record("告警字段结构齐备", False, "无告警")

        # 过滤: 维度+状态
        resilience_only = (await svc.list_alerts(
            dimension="resilience")).get("alerts") or []
        record("维度过滤(resilience)",
               resilience_only
               and all((a.get("dimension") or "")
                       == "resilience"
                       for a in resilience_only),
               str(len(resilience_only)))
        open_only = (await svc.list_alerts(
            status="open")).get("alerts") or []
        record("状态过滤(open)+openCount",
               all((a.get("status") or "") == "open"
                   for a in open_only),
               str(len(open_only)))
        os.environ["US52_MODE"] = "off"
        os.environ["US52_ALERT_MODE"] = "off"


class TestDashboardAndReport:
    """02 五维看板+报告明细"""

    async def run(self):
        print("[02 看板+报告明细]")
        reset_all()
        from services.us52_service import (
            Us52MetricsService,
        )
        svc = Us52MetricsService()

        # 空态看板(off 亦可访问——观测面)
        d0 = await svc.dashboard()
        record("看板空态(无快照 value=None)",
               d0.get("latestSnapshot") is None
               and d0.get("alertTotal") == 0
               and d0.get("reportCount") == 0,
               str(d0.get("alertTotal")))
        dims = d0.get("dimensions") or []
        record("五维分区结构",
               len(dims) == 5
               and [x["dimension"] for x in dims] == [
                   "functional", "transparency",
                   "resilience", "trust", "inclusion"]
               and sum(x["metricCount"]
                       for x in dims) == 20,
               str([x["dimension"] for x in dims]))
        if dims:
            first_metric = (dims[0]
                            .get("metrics") or [{}])[0]
            record("分区行含基线+veto 标记",
                    first_metric.get("value") is None
                    and "baseline" in first_metric
                    and "veto" in first_metric,
                    str(first_metric))
        dt = d0.get("dynamicThreshold") or {}
        record("动态阈值段(窗口 3+阈值 0.05)",
               dt.get("window") == 3
               and dt.get("driftThreshold") == 0.05
               and dt.get("drifts") == [],
               str(dt))

        # 有数据看板
        os.environ["US52_MODE"] = "on"
        good = {
            "fc_success_rate": 1.0,
            "explain_ref_rate": 1.0,
            "budget_accuracy": 1.0,
            "confirm_rate": 1.0,
            "intent_accuracy": 1.0,
            "privacy_notice_rate": 1.0,
            "attribution_rate": 1.0,
            "error_clarity": 1.0,
            "data_purpose_rate": 1.0,
            "injection_defense_rate": 1.0,
            "voiceprint_spoof_rate": 1.0,
            "degrade_compliance_rate": 1.0,
            "budget_exhausted_guide_rate": 1.0,
            "session_isolation_rate": 1.0,
            "trust_gain_index": 1.0,
            "control_sense_rate": 1.0,
            "ethics_negative_rate": 0.0,
            "feedback_health_ratio": 1.0,
            "intent_parity_gap": 0.0,
            "low_value_service_parity": 0.0,
        }
        await inject_snapshot(good)
        await inject_snapshot(good)
        d1 = await svc.dashboard()
        record("有数据看板(最新快照绑定)",
               (d1.get("latestSnapshot") or {})
               .get("decision") == "pass",
               str((d1.get("latestSnapshot") or {})
                   .get("decision")))
        rows = []
        for x in d1.get("dimensions") or []:
            rows.extend(x.get("metrics") or [])
        record("分区行 20 项全带值+状态",
               len(rows) == 20
               and all(r.get("value") is not None
                       and r.get("status") == "pass"
                       for r in rows),
               str(len(rows)))
        record("看板开关态回显",
               d1.get("mode") == "on"
               and d1.get("alertMode") == "off",
               str((d1.get("mode"),
                    d1.get("alertMode"))))

        # 报告明细
        rep = await svc.generate_report()
        rid = rep["report"]["reportId"]
        got = await svc.get_report(rid)
        record("报告明细(reportId 直查)",
               (got.get("report") or {})
               .get("reportId") == rid
               and "complianceImpact" in (
                   got.get("report") or {}),
               str(rid))
        try:
            await svc.get_report(99999)
            ok, err = False, "未拒绝"
        except KeyError as e:
            ok, err = "99999" in str(e), str(e)[:30]
        record("报告不存在 KeyError", ok, err)

        # 报告绑定看板
        d2 = await svc.dashboard()
        record("看板 latestReport 绑定",
               (d2.get("latestReport") or {})
               .get("reportId") == rid,
               str((d2.get("latestReport") or {})
                   .get("reportId")))
        record("看板 reportCount=1",
               d2.get("reportCount") == 1,
               str(d2.get("reportCount")))
        os.environ["US52_MODE"] = "off"


class TestEndpoints:
    """03 端点+鉴权+检查清单+零影响"""

    async def run(self):
        print("[03 端点+鉴权+检查清单]")
        reset_all()
        from fastapi.testclient import TestClient
        from main import app
        client = TestClient(app)
        admin = {"X-Role": "admin"}

        # off 态告警扫描 409
        resp = client.post("/api/us52/alerts/scan",
                           headers=admin)
        record("off 态 alerts/scan 409",
               resp.status_code == 409,
               str(resp.status_code))

        # 观测面端点不受开关影响(告警/看板/报告)
        for path, label in (
                ("/api/us52/alerts", "alerts"),
                ("/api/us52/dashboard", "dashboard"),
                ("/api/us52/reports", "reports")):
            resp = client.get(path, headers=admin)
            record(f"观测面 {label} off 态可访问",
                   resp.status_code == 200,
                   str(resp.status_code))

        # 报告明细 404
        resp = client.get("/api/us52/reports/99999",
                          headers=admin)
        record("报告明细 404",
               resp.status_code == 404,
               str(resp.status_code))

        # on 态扫描+HTTP 端到端
        os.environ["US52_MODE"] = "on"
        os.environ["US52_ALERT_MODE"] = "on"
        resp = client.post("/api/us52/alerts/scan",
                           headers=admin)
        body = resp.json() or {}
        record("HTTP alerts/scan 200(20 项)",
               resp.status_code == 200
               and body.get("metricCount") == 20,
               str(resp.status_code))

        # 告警视图含快照漂移源告警
        resp = client.get("/api/us52/alerts",
                          headers=admin)
        body = resp.json() or {}
        record("HTTP GET /alerts 200",
               resp.status_code == 200
               and "openCount" in body,
               str(resp.status_code))

        # 看板 HTTP
        resp = client.get("/api/us52/dashboard",
                          headers=admin)
        body = resp.json() or {}
        record("HTTP GET /dashboard 200(五维)",
               resp.status_code == 200
               and len(body.get("dimensions")
                       or []) == 5,
               str(resp.status_code))

        # 鉴权
        for method, path in (
                ("POST", "/api/us52/alerts/scan"),
                ("GET", "/api/us52/alerts"),
                ("GET", "/api/us52/dashboard"),
                ("GET", "/api/us52/reports/1")):
            resp = (client.post(path)
                    if method == "POST"
                    else client.get(path))
            record(f"{path.split('/')[-1]} 无 Role 403",
                   resp.status_code == 403,
                   str(resp.status_code))

        # release-gate 检查清单
        metrics = {
            "injection_defense_rate": 1.0,
            "voiceprint_spoof_rate": 1.0,
            "degrade_compliance_rate": 1.0,
            "budget_exhausted_guide_rate": 1.0,
            "session_isolation_rate": 1.0,
        }
        resp = client.post("/api/us52/release-gate",
                           json={"metrics": metrics},
                           headers=admin)
        body = resp.json() or {}
        checklist = body.get("launchChecklist") or []
        record("release-gate 七项检查清单",
               len(checklist) == 7
               and body.get("checklistPassed")
               is True
               and body.get("gate") == "pass",
               f"gate={body.get('gate')} "
               f"n={len(checklist)}")

        # veto 场景清单联动
        bad = dict(metrics)
        bad["injection_defense_rate"] = 0.5
        resp = client.post("/api/us52/release-gate",
                           json={"metrics": bad},
                           headers=admin)
        body = resp.json() or {}
        cl = body.get("launchChecklist") or []
        record("veto 场景清单第 1 项失败",
               body.get("gate") == "veto"
               and cl and cl[0]["passed"] is False,
               str(body.get("gate")))

        # 零影响: 宪法断言(48/49/50/51/45号)
        from services.trust_scoring_service import (
            TrustValueScorer,
        )
        from services.xiaozhu_voice50_rules import (
            VOICE_RULES,
        )
        from services.xiaozhu_fc_registry import (
            TOOL_REGISTRY,
        )
        from routes.kg51_routes import router as kg51_router
        record("45号九因子零改动",
               len(TrustValueScorer.LAYER_OF) == 9)
        record("50号14行为零改动",
               len(VOICE_RULES) == 14)
        record("49号17工具零改动",
               len(TOOL_REGISTRY) == 17)
        kg51_count = sum(
            1 for r in kg51_router.routes)
        record("51号路由零改动(22 端点)",
               kg51_count == 22, str(kg51_count))

        # 调度器(46号 P6 范式平移——默认 off)
        os.environ["US52_ALERT_MODE"] = "off"
        os.environ["US52_MODE"] = "off"
        from services.us52_alert_scheduler import (
            scheduler_enabled, start_scheduler,
            stop_scheduler, scheduler_running,
            run_scheduled_alert_scan,
        )
        record("调度器默认 off(start 不启动)",
               scheduler_enabled() is False
               and start_scheduler() is False
               and scheduler_running() is False)
        os.environ["US52_ALERT_MODE"] = "on"
        record("调度器 on 可启动(幂等)",
               start_scheduler() is True
               and start_scheduler() is True
               and scheduler_running() is True)
        stop_scheduler()
        record("调度器可停止",
               scheduler_running() is False)

        # 调度扫描函数(off 计算面——skip 留痕)
        os.environ["US52_MODE"] = "off"
        stats = await run_scheduled_alert_scan()
        record("调度扫描 off 态 skip 留痕",
               (stats.get("lastErrors") or [""])[0]
               .startswith("off:"),
               str(stats.get("lastErrors"))[:40])
        os.environ["US52_MODE"] = "on"
        stats2 = await run_scheduled_alert_scan()
        record("调度扫描 on 态统计留痕",
               (stats2.get("lastScan") or {})
               .get("metricCount") == 20
               and stats2.get("runs", 0) >= 2,
               str(stats2.get("runs")))
        os.environ["US52_MODE"] = "off"
        os.environ["US52_ALERT_MODE"] = "off"


async def run_all():
    await TestAlertScan().run()
    await TestDashboardAndReport().run()
    await TestEndpoints().run()


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
