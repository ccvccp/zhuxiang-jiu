"""55号·二维码AI智能管理模块 P2 专项测试
(全链追踪+决策回流)

运行方式:
    python test_qr55_p2.py

覆盖(55号计划 §六 P2):
    - 完成标记: record_completion 状态机+幂等
    - 七类信号: scan_completed/scan_abandoned/
      clarify_hit/clarify_inefficient/
      expired_unscanned/tamper_detected(+probe预置)
    - 幂等铁律: eventId 1:1(二次 collect 零重复)
    - 延迟态: pending_completion/pending_clarify
      T+1 窗口转正
    - 44号池双写: poolFeedbackId+因子快照
    - 45号信值结算: deposit 验真+settle 留痕
    - 过期清扫: sweep_expired_codes 状态翻转
    - 六指标: 意图满足率/渗透率/预算健康度/
      拦截有效率/满意度/澄清效率
    - 调度器: 开关/间隔下限
    - HTTP 层: codes/code/{id}/stats/collect+鉴权
"""

import asyncio
import os
import sys
import time
from datetime import datetime, timedelta

os.environ["LOCK_MODE"] = "asyncio"
os.environ["STORE_MODE"] = "asyncio"
os.environ.pop("LLM_API_KEY", None)
os.environ["LLM_ENABLED"] = "off"
os.environ["XIAOZHU_LLM_MODE"] = "off"
os.environ["XIAOZHU_PROACTIVE_MODE"] = "off"
os.environ["QR55_MODE"] = "off"
os.environ["QR55_LEARN_MODE"] = "off"

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


async def seed_member_grade(member_id: int,
                            grade: str):
    """种 45号信值档案(grade 四档)"""
    from repositories.trust_value_repository \
        import TrustValue45Repository
    repo = TrustValue45Repository()
    rec = await repo.get_profile(member_id) or {}
    rec.update({
        "trustId": member_id, "grade": grade,
        "score": 80 if grade == "healthy" else 40,
        "factors": {}, "role": "person",
        "l1Severity": {},
        "idDigest": f"seed-digest-{member_id}",
    })
    await repo.save_profile(rec)


async def age_events(hours: float = 30.0):
    """事件时间批量回拨(模拟 T+1 窗口流逝)"""
    from repositories.qr55_repository import (
        Qr55Repository,
    )
    repo = Qr55Repository()
    events = await repo.list_events(limit=1000)
    past = (datetime.now().astimezone()
            - timedelta(hours=hours)).isoformat()
    for e in events:
        if e.get("eventType") == "settle":
            continue
        e["createdAt"] = past
        await repo.add_event(e)


class TestCompletion:
    """01 完成标记(record_completion)"""

    async def run(self):
        print("[01 完成标记]")
        reset_all()
        from services.qr55_generate_service import (
            Qr55GenerateService,
        )
        from services.qr55_service import Qr55Service
        gen = Qr55GenerateService()
        svc = Qr55Service()
        os.environ["QR55_MODE"] = "on"
        await seed_member_grade(8901, "healthy")

        # off 铁律
        os.environ["QR55_MODE"] = "off"
        try:
            await svc.record_completion(1)
            ok, err = False, "未拒绝"
        except ValueError as e:
            ok, err = "off" in str(e), str(e)[:30]
        record("off 态完成标记拒绝", ok, err)
        os.environ["QR55_MODE"] = "on"

        # 状态机: active 码不可完成
        g = await gen.orchestrate(8901, "查政策解读")
        code_id = g["codeId"]
        try:
            await svc.record_completion(code_id)
            ok, err = False, "未拒绝"
        except ValueError as e:
            ok, err = "redeemed" in str(e), str(e)[:30]
        record("active 态完成拒绝(状态机)", ok, err)

        # 核销后完成
        from services.qr55_scan_service import (
            Qr55ScanService,
        )
        await Qr55ScanService().scan(
            g["code"], member_id=8901)
        r = await svc.record_completion(code_id)
        record("redeemed→completed",
               r.get("status") == "completed", str(r))

        # 幂等: 重复标记跳过
        r2 = await svc.record_completion(code_id)
        record("完成标记幂等",
               r2.get("status") == "already_completed",
               str(r2))

        # 不存在的码
        try:
            await svc.record_completion(99999)
            ok, err = False, "未拒绝"
        except KeyError:
            ok, err = True, ""
        record("不存在码 404 语义", ok, err)
        os.environ["QR55_MODE"] = "off"


class TestFeedbackSignals:
    """02 七类信号真值标注"""

    async def run(self):
        print("[02 七类信号]")
        reset_all()
        from services.qr55_generate_service import (
            Qr55GenerateService,
        )
        from services.qr55_scan_service import (
            Qr55ScanService,
        )
        from services.qr55_service import Qr55Service
        from services.qr55_feedback_service import (
            Qr55FeedbackService,
        )
        from repositories.qr55_repository import (
            Qr55Repository,
        )
        gen = Qr55GenerateService()
        scan = Qr55ScanService()
        svc = Qr55Service()
        fb = Qr55FeedbackService()
        repo = Qr55Repository()
        os.environ["QR55_MODE"] = "on"

        # S1 scan_completed(生成→扫码→完成)
        await seed_member_grade(8902, "healthy")
        g1 = await gen.orchestrate(8902, "查政策解读")
        await scan.scan(g1["code"], member_id=8902)
        await svc.record_completion(g1["codeId"])

        # S2 scan_abandoned(扫码未完成——T+1 转正)
        await seed_member_grade(8903, "watch")
        g2 = await gen.orchestrate(8903, "查信值余额")
        await scan.scan(g2["code"], member_id=8903)

        # S3 clarify_hit(二次即中)
        await gen.orchestrate(8902, "看看天气怎么样")
        g3 = await gen.orchestrate(8902, "查政策解读")

        # S4 clarify_inefficient(≥3 轮才命中)
        await gen.orchestrate(8903, "帮我看看那个")
        await gen.orchestrate(8903, "还是不明白那个")
        g4 = await gen.orchestrate(8903, "查政策解读")

        # S5 expired_unscanned(过期未扫)
        await seed_member_grade(8904, "healthy")
        g5 = await gen.orchestrate(8904, "查政策解读")
        code_rec = await repo.get_code(g5["codeId"])
        code_rec["expiresAt"] = int(time.time()) - 10
        await repo.update_code(code_rec)

        # S6 tamper_detected(篡改)
        g6 = await gen.orchestrate(8904, "查积分明细")
        tampered = g6["code"][:-2] + "xx"
        await scan.scan(tampered, member_id=8904)

        # S7 probe_retry(P4 预置——直接种 probe 事件)
        event_id = await repo.next_event_id()
        await repo.add_event({
            "eventId": event_id, "codeId": 0,
            "memberId": 8904, "eventType": "probe",
            "detail": {"retrySucceeded": True},
            "createdAt": datetime.now().astimezone()
            .isoformat(),
        })

        # 首轮 collect(事件均"新鲜"——S1/S6/S7 即时,
        # S2/S3/S4 依事件链即判, S5 依 exp 时点即判)
        os.environ["QR55_MODE"] = "off"
        c1 = await fb.collect_feedback()
        signals = c1.get("signals") or {}
        record("scan_completed 标注(+1.0)",
               signals.get("scan_completed") == 1,
               str(signals))
        record("tamper_detected 标注(-1.0)",
               signals.get("tamper_detected") == 1,
               str(signals))
        record("probe_retry 标注(-0.5 预置)",
               signals.get("probe_retry") == 1,
               str(signals))
        record("expired_unscanned 标注(-0.4)",
               signals.get("expired_unscanned") == 1,
               str(signals))
        record("clarify_hit 标注(+0.8 二次即中)",
               signals.get("clarify_hit") == 1,
               str(signals))
        record("clarify_inefficient 标注(-0.6)",
               signals.get("clarify_inefficient") == 1,
               str(signals))
        # S2 扫码未完成 → 延迟态
        record("scan_abandoned 延迟态(pending)",
               (c1.get("deferred") or 0) >= 1,
               str(c1.get("deferred")))

        # 44号池双写
        feedback = await repo.list_feedback(limit=100)
        pooled = [f for f in feedback
                  if int(f.get("poolFeedbackId") or 0) > 0]
        record("44号池双写(poolFeedbackId)",
               len(pooled) >= 6
               and c1.get("poolSubmitted") == len(pooled),
               str((len(pooled),
                    c1.get("poolSubmitted"))))

        # reward 正负分布
        labeled = [f for f in feedback
                  if f.get("status") == "labeled"]
        pos = sum(1 for f in labeled
                  if float(f.get("reward") or 0) > 0)
        neg = sum(1 for f in labeled
                  if float(f.get("reward") or 0) < 0)
        record("reward 正负分布",
               pos >= 2 and neg >= 4,
               str((pos, neg)))

        # 45号信值结算(scan_completed 聚合 deposit)
        settles = [e for e in await repo.list_events(
            limit=1000)
            if e.get("eventType") == "settle"]
        record("45号信值结算(settle 留痕)",
               c1.get("settled") == 1
               and len(settles) == 1
               and (settles[0].get("detail") or {})
               .get("depositVerified") is True,
               str((c1.get("settled"),
                    settles[:1])))

        # 幂等: 二次 collect 零新增
        c2 = await fb.collect_feedback()
        record("幂等(labeled 1:1 不重标)",
               c2.get("labeled") == 0
               and (c2.get("signals") or {}) == {},
               str((c2.get("labeled"),
                    c2.get("signals"))))

        # T+1 转正: 事件回拨 30h → scan_abandoned
        await age_events(30.0)
        c3 = await fb.collect_feedback()
        record("T+1 转正 scan_abandoned(+0.3)",
               (c3.get("signals") or {})
               .get("scan_abandoned") == 1,
               str(c3.get("signals")))

        # 回流统计
        stats = await fb.feedback_stats()
        record("回流统计(bySource/延迟态)",
               (stats.get("bySource") or {})
               .get("scan_completed") == 1
               and (stats.get("pending") or {})
               .get("completion") == 0,
               str(stats.get("bySource")))


class TestSweepScheduler:
    """03 过期清扫+调度器"""

    async def run(self):
        print("[03 清扫+调度器]")
        reset_all()
        from services.qr55_generate_service import (
            Qr55GenerateService,
        )
        from services.qr55_scheduler import (
            scheduler_enabled,
            scheduler_interval_seconds,
            sweep_expired_codes,
            run_scheduled_collect,
        )
        from repositories.qr55_repository import (
            Qr55Repository,
        )
        gen = Qr55GenerateService()
        repo = Qr55Repository()
        os.environ["QR55_MODE"] = "on"
        await seed_member_grade(8905, "healthy")

        # 生成两码: 一码过期, 一码活跃
        g1 = await gen.orchestrate(8905, "查政策解读")
        g2 = await gen.orchestrate(8905, "查政策解读")
        rec1 = await repo.get_code(g1["codeId"])
        rec1["expiresAt"] = int(time.time()) - 10
        await repo.update_code(rec1)

        # 清扫
        swept = await sweep_expired_codes()
        after1 = await repo.get_code(g1["codeId"])
        after2 = await repo.get_code(g2["codeId"])
        record("清扫状态翻转(expired)",
               swept.get("swept") == 1
               and after1.get("status") == "expired",
               str((swept, after1.get("status"))))
        record("活跃码不受清扫影响",
               after2.get("status") == "active",
               str(after2.get("status")))

        # expire 事件留痕
        events = await repo.list_events(limit=100)
        expire_evs = [e for e in events
                     if e.get("eventType") == "expire"
                     and int(e.get("codeId") or 0)
                     == g1["codeId"]]
        record("清扫 expire 事件留痕(codeId 挂链)",
               len(expire_evs) == 1,
               str(len(expire_evs)))

        # 幂等: 二次清扫零翻转
        swept2 = await sweep_expired_codes()
        record("清扫幂等",
               swept2.get("swept") == 0,
               str(swept2))

        # 调度主轮(清扫+补标+指标快照)
        os.environ["QR55_MODE"] = "off"
        run = await run_scheduled_collect()
        record("调度主轮三步齐备",
               run.get("sweep") is not None
               and run.get("collect") is not None
               and run.get("metrics") is not None
               and not run.get("errors"),
               str(run.get("errors")))

        # 指标快照留痕(model_events)
        model_events = await repo.list_model_events(
            limit=100)
        types = {e.get("eventType")
                 for e in model_events}
        record("模型事件留痕(collect+snapshot+run)",
               {"feedback_collect", "metrics_snapshot",
                "scheduler_run"} <= types,
               str(types))

        # 开关矩阵
        record("调度默认 off",
               scheduler_enabled() is False, "")
        os.environ["QR55_LEARN_INTERVAL"] = "10"
        record("间隔下限 300s",
               scheduler_interval_seconds() == 300,
               str(scheduler_interval_seconds()))
        os.environ["QR55_LEARN_INTERVAL"] = "86400"
        del os.environ["QR55_LEARN_INTERVAL"]


class TestMetrics:
    """04 六指标管道"""

    async def run(self):
        print("[04 六指标]")
        reset_all()
        from services.qr55_generate_service import (
            Qr55GenerateService,
        )
        from services.qr55_scan_service import (
            Qr55ScanService,
        )
        from services.qr55_service import Qr55Service
        from services.qr55_metrics_service import (
            Qr55MetricsService,
        )
        from repositories.xiaozhu_repository \
            import Xiaozhu48Repository
        gen = Qr55GenerateService()
        scan = Qr55ScanService()
        svc = Qr55Service()
        os.environ["QR55_MODE"] = "on"
        await seed_member_grade(8906, "healthy")

        # 构造: 4 生成 / 3 扫码 / 1 完成 /
        # 1 spent + 1 degraded / 1 tamper + 1 replay
        codes = []
        for i in range(3):
            g = await gen.orchestrate(
                8906, "查积分明细记录" if i > 0
                else "查政策解读")
            codes.append(g)

        # 码1: L0 扫码+完成
        await scan.scan(codes[0]["code"],
                        member_id=8906)
        await svc.record_completion(codes[0]["codeId"])
        # 码2: L1 spent(积分明细)——耗尽前正常扣减
        await scan.scan(codes[1]["code"],
                        member_id=8906)
        # 码3: 预算耗尽 → degraded + 扫码
        # (dayKey 保持今日——防 49号 日切重置归零)
        pref_repo = Xiaozhu48Repository()
        rec = await pref_repo.get_privacy_budget(
            8906) or {}
        rec["usedToday"] = 1.0
        await pref_repo.save_privacy_budget(rec)
        await scan.scan(codes[2]["code"],
                        member_id=8906)

        # 异常扫码: tamper + replay(码1 二次扫)
        g_bad = await gen.orchestrate(
            8906, "查信值余额")
        await scan.scan(g_bad["code"][:-2] + "xx",
                        member_id=8906)
        await scan.scan(codes[0]["code"],
                        member_id=8906)

        os.environ["QR55_MODE"] = "off"
        snap = await Qr55MetricsService(
        ).compute_snapshot()
        m = snap.get("metrics") or {}
        basis = snap.get("basis") or {}

        # ① 意图满足率: 1 完成 / 4 生成
        record("意图满足率(1/4)",
               m.get("intentSatisfactionRate") == 0.25,
               str(m.get("intentSatisfactionRate")))
        # ② 渗透率: 3 扫码 / 4 生成
        record("渗透率(3/4)",
               m.get("penetrationRate") == 0.75,
               str((m.get("penetrationRate"),
                    basis.get("generatedCodes"),
                    basis.get("scannedCodes"))))
        # ③ 预算健康度: 1 spent / 2 成本扫码
        record("预算健康度(1/2)",
               m.get("budgetHealthRate") == 0.5,
               str((m.get("budgetHealthRate"),
                    basis.get("scanBudgetModes"))))
        # ④ 拦截有效率: (1 tamper + 1 replay) /
        #    (异常三态; expire=0)
        record("拦截有效率(2/2)",
               m.get("interceptEffectiveRate") == 1.0,
               str((m.get("interceptEffectiveRate"),
                    basis.get("abnormalScans"))))
        # ⑤ 澄清效率: 无澄清样本 → None(不造数)
        record("澄清效率(无样本 None)",
               m.get("clarifyEfficiency") is None,
               str(m.get("clarifyEfficiency")))
        # ⑥ 满意度: 有回流标注后可算(本轮未 collect)
        record("满意度(未回流 None)",
               m.get("satisfactionScore") is None,
               str(m.get("satisfactionScore")))

        # 指标元数据齐备
        record("六指标元数据齐备",
               set(m.keys()) == set((
                   "intentSatisfactionRate",
                   "penetrationRate",
                   "budgetHealthRate",
                   "interceptEffectiveRate",
                   "satisfactionScore",
                   "clarifyEfficiency")),
               str(sorted(m.keys())))

        # 回流后满意度可算
        from services.qr55_feedback_service import (
            Qr55FeedbackService,
        )
        await Qr55FeedbackService().collect_feedback()
        snap2 = await Qr55MetricsService(
        ).compute_snapshot()
        record("满意度(回流后可算)",
               (snap2.get("metrics") or {})
               .get("satisfactionScore") is not None,
               str((snap2.get("metrics") or {})
                   .get("satisfactionScore")))


class TestHttp:
    """05 HTTP 层"""

    async def run(self):
        print("[05 HTTP]")
        reset_all()
        from fastapi.testclient import TestClient
        from main import app
        client = TestClient(app)
        admin = {"X-Role": "admin"}

        # 种一码(off 亦可用 code/generate? off 拒绝
        # ——用仓储直种观测数据)
        from repositories.qr55_repository import (
            Qr55Repository,
        )
        repo = Qr55Repository()
        code_id = await repo._next_seq("codes")
        await repo.save_code({
            "codeId": code_id, "eventId": 0,
            "memberId": 8907, "serviceId": "policy_search",
            "label": "政策解读", "code": "ZXBJ-QR55:x",
            "nonce": f"n{code_id}", "params": {},
            "status": "active", "privacyCost": 0.0,
            "accessibility": False, "scanCount": 0,
            "createdAt": "2026-09-06T00:00:00+08:00",
            "expiresAt": int(time.time()) + 300,
        })

        # GET /codes
        resp = client.get("/api/qr55/codes",
                          headers=admin)
        body = resp.json() or {}
        record("HTTP codes 列表",
               resp.status_code == 200
               and body.get("total") == 1,
               str((resp.status_code,
                    body.get("total"))))
        resp = client.get(
            "/api/qr55/codes?status=redeemed",
            headers=admin)
        record("HTTP codes 状态过滤",
               (resp.json() or {}).get("total") == 0,
               str(resp.json()))

        # GET /code/{id}
        resp = client.get(
            f"/api/qr55/code/{code_id}",
            headers=admin)
        body = resp.json() or {}
        record("HTTP code 详情+事件链",
               resp.status_code == 200
               and (body.get("code") or {})
               .get("codeId") == code_id,
               str(resp.status_code))
        resp = client.get("/api/qr55/code/99999",
                          headers=admin)
        record("HTTP code 404",
               resp.status_code == 404,
               str(resp.status_code))

        # GET /stats
        resp = client.get("/api/qr55/stats",
                          headers=admin)
        body = resp.json() or {}
        record("HTTP stats 六指标",
               resp.status_code == 200
               and "metrics" in body
               and len(body.get("metrics") or {})
               == 6,
               str(resp.status_code))

        # POST /feedback/collect(off 亦可用)
        resp = client.post(
            "/api/qr55/feedback/collect",
            headers=admin)
        body = resp.json() or {}
        record("HTTP collect(off 亦可用)",
               resp.status_code == 200
               and body.get("success") is True,
               str((resp.status_code,
                    body.get("success"))))

        # 鉴权
        for method, path in (
                ("GET", "/api/qr55/codes"),
                ("GET", "/api/qr55/stats"),
                ("POST", "/api/qr55/feedback/collect")):
            resp = client.request(method, path)
            record(f"HTTP {path} 无 Role 403",
                   resp.status_code == 403,
                   str(resp.status_code))

        # 路由累计 11 端点
        from routes.qr55_routes import (
            router as qr_router,
        )
        count = sum(1 for r in qr_router.routes)
        # P3 新增 3 端点(learn/promote/rollback)
        # → 11→14(基线语义: ≥11——P2 交付面不因
        # P3 演进破坏)
        record("55号路由累计 ≥11 端点(P3 扩至 14)",
               count >= 11, str(count))


async def run_all():
    await TestCompletion().run()
    await TestFeedbackSignals().run()
    await TestSweepScheduler().run()
    await TestMetrics().run()
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
