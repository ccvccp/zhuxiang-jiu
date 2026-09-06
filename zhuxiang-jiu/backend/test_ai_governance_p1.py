"""46号·AI 治理与合规中枢 P1 专项测试(档案健康度监控)

运行方式:
    python test_ai_governance_p1.py

覆盖(计划 §四):
    - 三检测器数学断言: 停滞(阈值边界/无事件回退
      观察起点/挑战者近作防停滞)、枯竭(未启动不判/
      学不动判定/充足不判)、漂移高(仅 high)
    - 健康分聚合: 100/60/70/30/0 扣减组合 +
      healthy/watch/risk 分层边界
    - 巡检快照: 29 档案全评估/落快照/排行升序/
      重扫新快照/retired 跳过
    - 治理告警: 三信号生成/字段完整/当日去重
      (occurrences 累加不新建)/信号与档案过滤/
      健康档案零告警
    - fail-soft: 单数据源(profile)异常不阻断
      其余信号(漂移仍检出, errors 留痕)
    - HTTP 层: 三端点结构与鉴权
"""

import asyncio
import os
import sys
from datetime import UTC, datetime, timedelta

os.environ["LOCK_MODE"] = "asyncio"
os.environ["STORE_MODE"] = "asyncio"
os.environ.pop("LLM_API_KEY", None)
os.environ["LLM_ENABLED"] = "off"

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


def _ago(days: float) -> str:
    return (datetime.now(UTC)
            - timedelta(days=days)).isoformat()


async def seed_feedback(scorer_id: str, days_ago: float,
                        count: int):
    """灌入指定天数的反馈(直接写 ai_learning 存储)"""
    from repositories.ai_learning_repository import (
        AiLearningRepository,
    )
    repo = AiLearningRepository()
    created = _ago(days_ago)
    for _ in range(count):
        await repo.add_feedback({
            "scorerId": scorer_id, "weightVersion": "v1",
            "scoreAtDecision": 50.0,
            "actualAction": "pass",
            "expectedAction": "pass", "correct": True,
            "factors": [], "note": "", "source": "manual",
            "status": "pending", "createdAt": created})


async def seed_profile(scorer_id: str, champion_days: float,
                       challenger_days: float = None):
    from repositories.ai_learning_repository import (
        AiLearningRepository,
    )
    profile = {
        "champion": {"version": "v1", "weights": {},
                     "source": "default", "parentVersion": "-",
                     "stats": {}, "note": "",
                     "createdAt": _ago(champion_days)},
    }
    if challenger_days is not None:
        profile["challenger"] = {
            "version": "v2", "weights": {},
            "source": "learning", "parentVersion": "v1",
            "stats": {}, "note": "",
            "createdAt": _ago(challenger_days)}
    await AiLearningRepository().save_profile(
        scorer_id, profile)


async def seed_drift(scorer_id: str, level: str):
    from repositories.ai_learning_repository import (
        AiLearningRepository,
    )
    await AiLearningRepository().save_drift(scorer_id, {
        "count": 5, "baselineScore": 50, "emaScore": 55,
        "baselineFactors": {}, "emaFactors": {},
        "driftScore": 0.31 if level == "high" else 0.05,
        "driftLevel": level, "lastFeedbackAt": _ago(1)})


class TestDetectors:
    async def run(self):
        print("[01 三检测器数学断言]")
        reset_all()
        from services.ai_governance_health import (
            AiGovernanceHealthService,
        )
        svc = AiGovernanceHealthService()
        now = datetime.now(UTC)

        def data(fb_times=(), events=(), drift=None,
                 total_cfg=10):
            return {"profile": None, "history": [],
                    "config": {"min_feedback": total_cfg},
                    "drift": drift,
                    "feedbackTimes": list(fb_times),
                    "versionEventTimes": list(events),
                    "errors": []}

        # 停滞
        h, d = svc.detect_stagnation(data(), now)
        record("无数据不判停滞", h is False, str(d))
        h, d = svc.detect_stagnation(
            data([_ago(40), _ago(1)], []), now)
        record("无事件回退观察起点(40天反馈→停滞)",
               h is True and d["daysSinceVersionEvent"] == 40.0,
               str(d))
        h, d = svc.detect_stagnation(
            data([_ago(10)], []), now)
        record("观察起点不足30天不判停滞", h is False,
               str(d))
        h, d = svc.detect_stagnation(
            data([_ago(1)], [_ago(40)]), now)
        record("近反馈+远事件判停滞",
               h is True and d["daysSinceVersionEvent"] == 40.0,
               str(d))
        h, d = svc.detect_stagnation(
            data([_ago(1)], [_ago(5)]), now)
        record("近事件不判停滞", h is False, str(d))
        h, d = svc.detect_stagnation(
            data([_ago(40)], [_ago(5)]), now)
        record("无近反馈不判停滞(空窗非停滞)",
               h is False, str(d))
        # 挑战者近作 → 版本在转(事件取 max)
        h, d = svc.detect_stagnation(
            data([_ago(1)], [_ago(40), _ago(3)]), now)
        record("挑战者近作防停滞", h is False, str(d))

        # 枯竭
        h, d = svc.detect_depletion(data(), now)
        record("未启动不判枯竭", h is False, str(d))
        h, d = svc.detect_depletion(
            data([_ago(1)] * 5), now)
        record("近30日反馈<阈值判枯竭(学不动)",
               h is True and d["feedback30d"] == 5
               and d["minFeedback"] == 10, str(d))
        h, d = svc.detect_depletion(
            data([_ago(1)] * 15), now)
        record("反馈充足不判枯竭", h is False, str(d))
        h, d = svc.detect_depletion(
            data([_ago(40)] * 15), now)
        record("历史有近期无判枯竭", h is True
               and d["feedback30d"] == 0
               and d["totalFeedback"] == 15, str(d))
        h, d = svc.detect_depletion(
            data([_ago(1)] * 5, total_cfg=3), now)
        record("阈值可配(5≥3不判枯竭)", h is False, str(d))

        # 漂移高
        h, d = svc.detect_drift_high(
            data(drift={"driftLevel": "high",
                        "driftScore": 0.31}))
        record("driftLevel=high判漂移高", h is True, str(d))
        h, d = svc.detect_drift_high(
            data(drift={"driftLevel": "medium",
                        "driftScore": 0.15}))
        record("medium不判漂移高", h is False, str(d))
        h, d = svc.detect_drift_high(data())
        record("无漂移统计不判漂移高", h is False, str(d))


class TestHealthScore:
    async def run(self):
        print("[02 健康分聚合]")
        from services.ai_governance_health import (
            AiGovernanceHealthService,
        )
        svc = AiGovernanceHealthService()
        record("全健康100", svc.health_score(
            {"stagnation": False, "depletion": False,
             "drift_high": False}) == 100)
        record("仅停滞60", svc.health_score(
            {"stagnation": True}) == 60)
        record("仅枯竭70", svc.health_score(
            {"depletion": True}) == 70)
        record("仅漂移70", svc.health_score(
            {"drift_high": True}) == 70)
        record("停滞+漂移30", svc.health_score(
            {"stagnation": True, "drift_high": True}) == 30)
        record("全命中0", svc.health_score(
            {"stagnation": True, "depletion": True,
             "drift_high": True}) == 0)
        record("分层边界90healthy",
               svc.health_level(90) == "healthy")
        record("分层边界60watch",
               svc.health_level(60) == "watch")
        record("分层边界59risk",
               svc.health_level(59) == "risk")
        record("分层边界0risk",
               svc.health_level(0) == "risk")


class TestScan:
    async def run(self):
        print("[03 巡检快照]")
        reset_all()
        from services.ai_governance_health import (
            AiGovernanceHealthService,
        )
        svc = AiGovernanceHealthService()

        # 灌数据: trust_value 三信号全命中;
        # order_risk 全健康(近事件+充足反馈)
        await seed_profile("trust_value", 40)
        await seed_feedback("trust_value", 40, 3)   # 全老反馈
        await seed_feedback("trust_value", 1, 2)    # 近反馈
        await seed_drift("trust_value", "high")
        await seed_profile("order_risk", 5)
        await seed_feedback("order_risk", 1, 15)
        await seed_drift("order_risk", "low")

        r = await svc.scan()
        record("巡检28档案", r["success"] is True
               and r["scorerCount"] == 36,
               f"scorerCount={r.get('scorerCount')}")
        entries = {e["scorerId"]: e
                   for e in r["entries"]}
        tv = entries.get("trust_value") or {}
        record("三信号全命中(trust_value)",
               tv.get("stagnation") is True
               and tv.get("depletion") is True
               and tv.get("drift_high") is True
               and len(tv.get("signals") or []) == 3,
               str(tv)[:80])
        record("全命中健康分0(risk)",
               tv.get("healthScore") == 0
               and tv.get("healthLevel") == "risk",
               str(tv.get("healthScore")))
        orr = entries.get("order_risk") or {}
        record("健康档案零信号(order_risk)",
               orr.get("signals") == []
               and orr.get("healthScore") == 100
               and orr.get("healthLevel") == "healthy",
               str(orr)[:80])

        # 排行升序(最需关注在前)
        scores = [e["healthScore"] for e in r["entries"]]
        record("排行升序", scores == sorted(scores),
               str(scores[:5]))

        # 快照落库 + 读回
        snap = await svc.repo.get_snapshot(r["scanId"])
        record("快照落库可读回",
               snap is not None
               and snap["scorerCount"] == 36
               and isinstance(snap.get("entries"), list)
               and len(snap["entries"]) == 36,
               str(snap)[:60] if snap else "None")
        latest = await svc.repo.get_latest_snapshot()
        record("最新快照即本轮",
               latest is not None
               and latest.get("scanId") == r["scanId"],
               str((latest or {}).get("scanId")))

        # 分层统计一致性
        record("hits统计一致",
               (r["hits"].get("stagnation") or 0) >= 1
               and (r["hits"].get("depletion") or 0) >= 1
               and (r["hits"].get("drift_high") or 0) >= 1,
               str(r.get("hits")))

        # 重扫 → 新快照
        r2 = await svc.scan()
        record("重扫新快照", r2["scanId"] > r["scanId"]
               and r2["scorerCount"] == 36,
               str(r2.get("scanId")))

        # retired 跳过(直接评估层验证——scan 内 sync 会
        # 复活 registry 内的 retired, 退役仅源于代码摘除)
        govs = await svc.repo.list_govs(limit=1000)
        target = govs[0]
        target["status"] = "retired"
        await svc.repo.save_gov(target)
        entries2, stats2 = await svc._assess_all()
        record("retired档案跳过",
               stats2["scorerCount"] == 35
               and all(e["scorerId"] != target["scorerId"]
                      for e in entries2),
               f"count={stats2['scorerCount']}")


class TestAlerts:
    async def run(self):
        print("[04 治理告警]")
        reset_all()
        from services.ai_governance_health import (
            AiGovernanceHealthService,
        )
        svc = AiGovernanceHealthService()
        await seed_profile("trust_value", 40)
        await seed_feedback("trust_value", 40, 3)
        await seed_feedback("trust_value", 1, 2)
        await seed_drift("trust_value", "high")
        await seed_profile("order_risk", 5)
        await seed_feedback("order_risk", 1, 15)

        r = await svc.scan()
        record("三信号告警生成",
               r.get("alertsNew") == 3
               and r.get("alertsUpdated") == 0,
               f"new={r.get('alertsNew')} "
               f"updated={r.get('alertsUpdated')}")
        alerts = await svc.repo.list_alerts(limit=100)
        record("告警队列3条", len(alerts) == 3,
               str(len(alerts)))
        tv_alerts = [a for a in alerts
                     if a["scorerId"] == "trust_value"]
        record("告警归属正确",
               len(tv_alerts) == 3
               and {a["signal"] for a in tv_alerts}
               == {"stagnation", "depletion",
                   "drift_high"},
               str([a.get("signal") for a in tv_alerts]))
        record("告警字段完整",
               all(a.get("level") == "warn"
                   and a.get("occurrences") == 1
                   and bool(a.get("message"))
                   and bool(a.get("day"))
                   and a.get("status") == "open"
                   for a in alerts), str(alerts[:1]))
        record("健康档案零告警",
               all(a["scorerId"] != "order_risk"
                   for a in alerts), "order_risk 有告警")

        # 当日去重: 再扫 → 不新建只累加
        r2 = await svc.scan()
        record("当日去重不新建",
               r2.get("alertsNew") == 0
               and r2.get("alertsUpdated") == 3,
               f"new={r2.get('alertsNew')} "
               f"updated={r2.get('alertsUpdated')}")
        alerts2 = await svc.repo.list_alerts(limit=100)
        record("队列仍3条", len(alerts2) == 3,
               str(len(alerts2)))
        record("occurrences累加",
               all(a.get("occurrences") == 2
                   for a in alerts2),
               str([a.get("occurrences")
                        for a in alerts2]))
        record("firstSeenAt不变",
               all(a.get("firstSeenAt")
                   == b.get("firstSeenAt")
                   for a, b in zip(
                       sorted(alerts2,
                              key=lambda x: x["alertId"]),
                       sorted(alerts,
                              key=lambda x: x["alertId"]))),
               "firstSeenAt 漂移")

        # 视图过滤
        view = await svc.list_alerts(signal="stagnation")
        record("信号过滤", view["total"] == 1
               and view["alerts"][0]["signal"]
               == "stagnation", str(view.get("total")))
        view = await svc.list_alerts(scorer_id="order_risk")
        record("档案过滤(空)", view["total"] == 0,
               str(view.get("total")))
        record("bySignal统计",
               (view.get("bySignal") or {}).get(
                   "stagnation") == 1,
               str(view.get("bySignal")))

        # live_health 视图
        live = await svc.live_health()
        record("live排行结构",
               live["success"] is True
               and live["scorerCount"] == 36
               and len(live["entries"]) == 36,
               str(live.get("scorerCount")))
        record("live含lastScan", (
            live.get("lastScan") or {}).get("scanId")
               == r2.get("scanId"),
               str(live.get("lastScan")))


class TestFailSoft:
    async def run(self):
        print("[05 fail-soft 隔离]")
        reset_all()
        from services.ai_governance_health import (
            AiGovernanceHealthService,
        )
        from services.ai_governance_service import (
            AiGovernanceService,
        )
        svc = AiGovernanceHealthService()
        await AiGovernanceService(
            repo=svc.repo).sync_registry()
        await seed_drift("trust_value", "high")

        # profile 数据源异常 → 其余信号照常
        orig = svc.learn.get_profile

        async def _boom(scorer_id):
            raise RuntimeError("档案存储瞬断")
        svc.learn.get_profile = _boom
        try:
            entry = await svc._assess(
                await svc.repo.get_gov("trust_value"),
                datetime.now(UTC))
            record("单源失败不阻断其余",
                   "drift_high" in entry
                   and entry["drift_high"] is True,
                   str(entry)[:80])
            record("失败留痕errors",
                   "profile" in (entry.get("errors") or []),
                   str(entry.get("errors")))
        finally:
            svc.learn.get_profile = orig

        # 采集层整体异常 → 扫描不崩(跳过留痕)
        orig_collect = svc._collect

        async def _boom_collect(scorer_id):
            if scorer_id == "trust_value":
                raise RuntimeError("采集链路异常")
            return await orig_collect(scorer_id)
        svc._collect = _boom_collect
        try:
            r = await svc._assess_all()
            record("档案级异常跳过",
                   r[1]["scorerCount"] == 35
                   and r[1]["skipped"] == ["trust_value"],
                   f"skipped={r[1]['skipped']}")
        finally:
            svc._collect = orig_collect


class TestHttp:
    async def run(self):
        print("[06 HTTP 层]")
        reset_all()
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from routes.ai_governance_routes import (
            register_ai_governance_routes,
        )
        app = FastAPI()
        register_ai_governance_routes(app)
        client = TestClient(app)
        admin = {"X-Role": "admin"}

        # 鉴权
        resp = client.get("/api/ai-gov/health")
        record("健康视图缺Role403", resp.status_code == 403,
               str(resp.status_code))
        resp = client.post("/api/ai-gov/health/scan")
        record("巡检缺Role403", resp.status_code == 403,
               str(resp.status_code))
        resp = client.get("/api/ai-gov/alerts")
        record("告警缺Role403", resp.status_code == 403,
               str(resp.status_code))

        # 健康视图 200
        resp = client.get("/api/ai-gov/health",
                          headers=admin)
        body = resp.json()
        record("健康视图200", resp.status_code == 200
               and body.get("scorerCount") == 36
               and body.get("live") is True,
               str(body)[:70])
        record("健康视图分层统计",
               isinstance(body.get("byLevel"), dict)
               and isinstance(body.get("hits"), dict),
               str(body.get("byLevel")))

        # 巡检 200(落快照)
        resp = client.post("/api/ai-gov/health/scan",
                           headers=admin)
        body = resp.json()
        record("巡检200落快照", resp.status_code == 200
               and body.get("scanId", 0) >= 1
               and body.get("scorerCount") == 36,
               str(body)[:70])

        # 告警 200
        resp = client.get("/api/ai-gov/alerts",
                          headers=admin)
        body = resp.json()
        record("告警200", resp.status_code == 200
               and "bySignal" in body
               and "signals" in body,
               str(body)[:70])

        # 巡检幂等(再扫不报错, 快照追加)
        resp = client.post("/api/ai-gov/health/scan",
                           headers=admin)
        record("巡检幂等再扫",
               resp.status_code == 200
               and resp.json().get("scanId", 0) >= 2,
               str(resp.json().get("scanId")))


async def run_all():
    await TestDetectors().run()
    await TestHealthScore().run()
    await TestScan().run()
    await TestAlerts().run()
    await TestFailSoft().run()
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
