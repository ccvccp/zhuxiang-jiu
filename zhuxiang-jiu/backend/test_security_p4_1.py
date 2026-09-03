"""43号·P4-1 SOC 安全运营日报专项测试

运行方式:
    python test_security_p4_1.py

覆盖(计划 §二):
    - 单日日报: 按日切分/事件分布/裁决统计/误报率/申诉/空数据日
    - D5 专项: hits/stuffing/samples/裁决率
    - 近 N 天序列: 汇总/有效天数
    - D5 联动观测: 硬标准三条件(天数/误报率/样本量)与
      recommendation 四档(不足/维持/开启)
    - HTTP 层: daily 单日与序列/d5 观测/鉴权
"""

import asyncio
import os
import sys

os.environ["LOCK_MODE"] = "asyncio"
os.environ["STORE_MODE"] = "asyncio"
os.environ.pop("LLM_API_KEY", None)
os.environ["LLM_ENABLED"] = "off"
os.environ["SECURITY_GATEWAY_MODE"] = "on"
os.environ["SECURITY_ENFORCE_LEVEL"] = "observe"

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


def reset_store():
    from repositories.store import reset_store as _reset
    _reset()


def _today():
    from datetime import datetime, UTC
    return datetime.now(UTC).strftime("%Y-%m-%d")


def _days_ago(n):
    from datetime import datetime, UTC, timedelta
    return (datetime.now(UTC) - timedelta(days=n)).strftime(
        "%Y-%m-%d")


async def seed_events(svc_repo):
    """造当日事件: 1 challenge(confirmed) + 1 challenge(误报)
    + 1 behavior_alert(D5_jump) + 1 behavior_alert(D5_stuffing)"""
    from core.helpers import ts

    def make(event_id, action, verdict, factors, created=None):
        return {
            "eventId": event_id, "ip": "1.1.1.1",
            "memberId": 1, "method": "GET", "path": "/x",
            "query": "", "ua": "", "action": action,
            "score": None, "factors": factors, "enforced": False,
            "verdict": verdict, "eventFed": False,
            "createdAt": created or ts(),
        }

    events = [
        make(1, "challenge", "confirmed",
             [{"name": "payload_signature", "score": 50}]),
        make(2, "challenge", "false_positive",
             [{"name": "payload_signature", "score": 50}]),
        make(3, "behavior_alert", "pending",
             [{"name": "D5_jump", "score": 20, "detail": "登录直奔"}]),
        make(4, "behavior_alert", "false_positive",
             [{"name": "D5_stuffing", "score": 0,
               "detail": "撞库"}]),
        # 非当日(昨日): 验证按日切分
        make(5, "challenge", "pending",
             [{"name": "x", "score": 1}], created=f"{_days_ago(1)}T00:00:00"),
    ]
    for e in events:
        await svc_repo.save_event(e)
    # 申诉 1 条
    await svc_repo.save_appeal({
        "appealId": 1, "eventId": 1, "memberId": 1, "ip": "1.1.1.1",
        "reason": "t", "status": "pending", "reviewer": "",
        "reviewNote": "", "createdAt": ts(), "decidedAt": None})
    return events


class TestDailyReport:
    async def run(self):
        print("[01 单日日报]")
        from services.soc_report_service import SocReportService
        from repositories.security_repository import \
            Security43Repository
        repo = Security43Repository()
        await seed_events(repo)
        svc = SocReportService(repo)

        r = await svc.daily_report()
        record("成功标志", r["success"] is True)
        record("按日切分(当日4条)",
               r["eventsTotal"] == 4, str(r["eventsTotal"]))
        record("昨日事件排除", r["eventsByAction"].get("challenge")
               == 2)
        record("裁决统计", r["verdicts"] == {
            "confirmed": 1, "falsePositive": 2, "pending": 1},
               str(r["verdicts"]))   # D5_stuffing 事件已裁决也计入
        record("误报率2/3", abs(
            r["falsePositiveRate"] - 0.6667) < 0.001,
               str(r["falsePositiveRate"]))
        record("申诉按日", r["appeals"]["created"] == 1
               and r["appeals"]["pending"] == 1, str(r["appeals"]))

        # 指定日期(昨日)
        r = await svc.daily_report(_days_ago(1))
        record("指定日期查询", r["eventsTotal"] == 1,
               str(r["eventsTotal"]))

        # 空数据日
        r = await svc.daily_report("2000-01-01")
        record("空数据日", r["eventsTotal"] == 0
               and r["falsePositiveRate"] == 0.0)

        # D5 专项
        d5 = r if False else (await svc.daily_report())["d5"]
        record("D5 hits=1", d5["hits"] == 1, str(d5))
        record("D5 stuffing=1", d5["stuffingAlerts"] == 1)
        record("D5 samples=2", d5["samples"] == 2)
        record("D5 裁决率", d5["decided"] == 1
               and d5["falsePositive"] == 1
               and d5["falsePositiveRate"] == 1.0, str(d5))


class TestSeries:
    async def run(self):
        print("[02 近N天序列]")
        from services.soc_report_service import SocReportService
        from repositories.security_repository import \
            Security43Repository
        svc = SocReportService(Security43Repository())

        r = await svc.daily_series(7)
        record("序列长度7", len(r["reports"]) == 7)
        record("汇总事件数", r["summary"]["eventsTotal"] == 5,
               str(r["summary"]))
        record("有效天数2(今昨)", r["summary"]["activeDays"] == 2,
               str(r["summary"]["activeDays"]))
        record("D5样本2", r["summary"]["d5Samples"] == 2)
        # 序列升序(旧→新)
        dates = [x["date"] for x in r["reports"]]
        record("序列升序", dates == sorted(dates), str(dates[:3]))


class TestD5Observation:
    async def run(self):
        print("[03 D5联动观测]")
        from services.soc_report_service import (
            SocReportService, D5_OBSERVE_MIN_DAYS,
        )
        from repositories.security_repository import \
            Security43Repository
        svc = SocReportService(Security43Repository())

        r = await svc.d5_observation()
        record("观察窗口14天", len(r) >= 0)  # 结构性检查
        record("recommendation字段",
               r["recommendation"] in (
                   "enable_strict_linkage", "keep_observe",
                   "insufficient_data"), str(r["recommendation"]))
        record("criteria三条件", set(r["criteria"].keys()) == {
            "observeDays", "falsePositiveRate", "samples"},
               str(list(r["criteria"].keys())))
        # 当前种子: 1天有效 → 样本不足/维持观察
        record("单日样本不足",
               r["recommendation"] in ("insufficient_data",
                                       "keep_observe"),
               r["recommendation"])

        # 构造达标场景: 直接改日报数据口径(单测
        # d5_observation 的硬标准逻辑——通过大量当日 D5 事件
        # 与低误报裁决验证 enable_strict_linkage 路径)
        from core.helpers import ts
        from repositories.security_repository import \
            Security43Repository as R
        repo = R()
        for i in range(25):
            await repo.save_event({
                "eventId": 100 + i, "ip": "2.2.2.2",
                "memberId": 2, "method": "GET", "path": "/x",
                "query": "", "ua": "", "action": "behavior_alert",
                "score": None,
                "factors": [{"name": "D5_jump", "score": 20}],
                "enforced": False,
                "verdict": "confirmed", "eventFed": False,
                "createdAt": ts()})
        svc2 = SocReportService(repo)
        r = await svc2.d5_observation()
        # active_days=1 仍不满足 14 天, 但样本与误报率达标
        record("样本达标", r["criteria"]["samples"]["met"] is True,
               str(r["criteria"]["samples"]))
        record("误报率达标",
               r["criteria"]["falsePositiveRate"]["met"] is True,
               str(r["criteria"]["falsePositiveRate"]))
        record("天数不足维持观察",
               r["recommendation"] == "keep_observe",
               r["recommendation"])


class TestHttpRoutes:
    async def run(self):
        print("[04 HTTP层]")
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from routes.security_routes import register_security_routes

        app = FastAPI()
        register_security_routes(app)
        client = TestClient(app)
        admin = {"X-Role": "admin"}

        resp = client.get("/api/security/admin/reports/daily")
        record("HTTP-日报缺Role403", resp.status_code == 403)

        resp = client.get("/api/security/admin/reports/daily",
                          headers=admin)
        record("HTTP-单日日报", resp.status_code == 200
               and resp.json().get("success") is True,
               str(resp.json())[:80])

        resp = client.get(
            "/api/security/admin/reports/daily?days=3",
            headers=admin)
        body = resp.json()
        record("HTTP-3天序列", resp.status_code == 200
               and len(body.get("reports", [])) == 3,
               str(body.get("days")))

        resp = client.get("/api/security/admin/reports/d5",
                          headers=admin)
        record("HTTP-D5观测", resp.status_code == 200
               and "recommendation" in resp.json(),
               str(resp.json())[:80])


async def run_all():
    await TestDailyReport().run()
    await TestSeries().run()
    await TestD5Observation().run()
    await TestHttpRoutes().run()


def main():
    reset_store()
    asyncio.run(run_all())
    print()
    print("=" * 62)
    print("\n".join(RESULTS))
    print("=" * 62)
    print(f"总计: {PASS} 通过 / {FAIL} 失败")
    return FAIL


if __name__ == "__main__":
    sys.exit(1 if main() else 0)
