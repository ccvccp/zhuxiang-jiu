"""43号·P6-3 enforce 就绪度自动化专项测试

运行方式:
    python test_security_p6_3.py

覆盖(计划 §四):
    - 观察期检查: 空 store not passed / 7 个不同日期事件 passed
    - 误报率检查: 20% 未达标 / 4% 达标 / 零裁决保守不判 ready
    - 积压检查: pending 未清零 / 全裁决清零
    - 申诉探活: 正常 passed / mock 抛异常 not passed(fail-soft)
    - 白名单检查: GATEWAY_WHITELIST 含健康检查路径
    - overall 判定: 五检查全过 ready / 任一不过 holding
    - blockers: 未过项中文摘要逐条 / 全过空列表
    - 三信号: d5 含 criteria+d5Enforce / threatintel 含
      totalCidrs+auto.degraded / geo.available 布尔 /
      abuseipdb.mode
    - 结构与铁律: enforceLevel 反映灰度态 / note 含
      "只评估不切换" / checkedAt 在位
    - HTTP 层: 缺 Role 403 / 200 结构 / 服务异常 500 包装
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
os.environ["GEOIP_DB_PATH"] = "/nonexistent/GeoLite2-City.mmdb"

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


def _days_ago(n):
    from datetime import datetime, UTC, timedelta
    return (datetime.now(UTC) - timedelta(days=n)).strftime(
        "%Y-%m-%d")


async def _seed_events(repo, specs):
    """造事件: specs = [(id, verdict, days_ago)]"""
    from core.helpers import ts
    for event_id, verdict, days in specs:
        await repo.save_event({
            "eventId": event_id, "ip": "1.1.1.1", "memberId": 1,
            "method": "GET", "path": "/x", "query": "", "ua": "",
            "action": "challenge", "score": None,
            "factors": [{"name": "payload_signature", "score": 50}],
            "enforced": False, "verdict": verdict,
            "eventFed": False,
            "createdAt": f"{_days_ago(days)}T12:00:00"})

    # 保证 ts() 引用不丢(与 p4_1 同范式)
    _ = ts()


def _checks_by_id(r):
    return {c["id"]: c for c in r["checks"]}


async def evaluate():
    from services.enforce_readiness_service import (
        EnforceReadinessService,
    )
    return await EnforceReadinessService().evaluate()


class TestObserveDays:
    async def run(self):
        print("[01 观察期检查]")
        from repositories.security_repository import \
            Security43Repository
        repo = Security43Repository()

        r = await evaluate()
        c = _checks_by_id(r)["observe_days"]
        record("空store不足7天", c["passed"] is False
               and c["actual"] == "0天", str(c))

        # 7 个不同日期事件(近 7 天)
        reset_store()
        await _seed_events(repo, [
            (i, "confirmed", i) for i in range(1, 8)])
        r = await evaluate()
        c = _checks_by_id(r)["observe_days"]
        record("7个活跃日达标", c["passed"] is True
               and c["actual"] == "7天", str(c))


class TestFalsePositiveRate:
    async def run(self):
        print("[02 误报率检查]")
        from repositories.security_repository import \
            Security43Repository
        repo = Security43Repository()

        # 20% 误报(1 false / 4 confirmed, 单日)
        reset_store()
        await _seed_events(repo, [
            (1, "false_positive", 0),
            (2, "confirmed", 0), (3, "confirmed", 0),
            (4, "confirmed", 0), (5, "confirmed", 0)])
        r = await evaluate()
        c = _checks_by_id(r)["false_positive_rate"]
        record("20%未达标", c["passed"] is False
               and c["actual"] == "20.0%", str(c))

        # 4% 误报(1 false / 24 confirmed)
        reset_store()
        await _seed_events(repo, [
            (1, "false_positive", 0)]
            + [(i, "confirmed", 0) for i in range(2, 26)])
        r = await evaluate()
        c = _checks_by_id(r)["false_positive_rate"]
        record("4%达标", c["passed"] is True
               and c["actual"] == "4.0%", str(c))

        # 零裁决(全 pending)——保守口径不判达标
        reset_store()
        await _seed_events(repo, [
            (i, "pending", 0) for i in range(1, 8)])
        r = await evaluate()
        c = _checks_by_id(r)["false_positive_rate"]
        record("零裁决不达标", c["passed"] is False
               and c["actual"] == "无裁决数据", str(c))


class TestPendingBacklog:
    async def run(self):
        print("[03 积压检查]")
        from repositories.security_repository import \
            Security43Repository
        repo = Security43Repository()

        reset_store()
        await _seed_events(repo, [
            (1, "pending", 0), (2, "pending", 1),
            (3, "confirmed", 0)])
        r = await evaluate()
        c = _checks_by_id(r)["pending_backlog"]
        record("积压2件未清零", c["passed"] is False
               and c["actual"] == "2件", str(c))

        reset_store()
        await _seed_events(repo, [
            (1, "confirmed", 0), (2, "false_positive", 1)])
        r = await evaluate()
        c = _checks_by_id(r)["pending_backlog"]
        record("全裁决清零", c["passed"] is True
               and c["actual"] == "0件", str(c))


class TestAppealChannel:
    async def run(self):
        print("[04 申诉探活]")
        reset_store()
        r = await evaluate()
        c = _checks_by_id(r)["appeal_channel"]
        record("正常探活passed", c["passed"] is True
               and "队列读写正常" in c["actual"], str(c))

        # mock 抛异常 → fail-soft not passed
        from repositories.security_repository import \
            Security43Repository
        from services.enforce_readiness_service import (
            EnforceReadinessService,
        )
        svc = EnforceReadinessService()
        orig = svc._security.repo.list_appeals

        async def _boom(member_id=None, limit=200):
            raise RuntimeError("存储瞬断")
        svc._security.repo.list_appeals = _boom
        ok, detail = await svc._probe_appeals()
        record("探活异常fail-soft", ok is False
               and "探活异常" in detail, str(detail))
        svc._security.repo.list_appeals = orig


class TestWhitelist:
    async def run(self):
        print("[05 白名单检查]")
        r = await evaluate()
        c = _checks_by_id(r)["health_whitelist"]
        record("健康检查在白名单", c["passed"] is True,
               str(c))


class TestOverall:
    async def run(self):
        print("[06 overall 判定]")
        from repositories.security_repository import \
            Security43Repository
        repo = Security43Repository()

        # 全过: 7 活跃日 + 0% 误报 + 零积压 + 申诉正常 + 白名单
        reset_store()
        await _seed_events(repo, [
            (i, "confirmed", i) for i in range(1, 8)])
        r = await evaluate()
        record("全过ready", r["overall"] == "ready"
               and r["blockers"] == [], str(r["blockers"]))

        # 任一不过(积压 1 件)
        await _seed_events(repo, [(99, "pending", 0)])
        r = await evaluate()
        record("积压holding", r["overall"] == "holding"
               and any("待裁决积压" in b for b in r["blockers"]),
               str(r["blockers"]))


class TestBlockers:
    async def run(self):
        print("[07 blockers 文案]")
        from repositories.security_repository import \
            Security43Repository
        repo = Security43Repository()

        # 空 store: 观察期+误报率两项不过
        reset_store()
        r = await evaluate()
        record("观察期不足文案", any(
            b.startswith("观察期 0天 不足") for b in r["blockers"]),
            str(r["blockers"]))
        record("误报率无数据文案", any(
            "误报率 无裁决数据 未达标" in b
            for b in r["blockers"]), str(r["blockers"]))

        # 积压文案
        await _seed_events(repo, [(1, "pending", 0)])
        r = await evaluate()
        record("积压未清零文案", any(
            "待裁决积压 1件 未清零" in b for b in r["blockers"]),
            str(r["blockers"]))


class TestSignals:
    async def run(self):
        print("[08 三信号汇总]")
        from repositories.security_repository import \
            Security43Repository
        repo = Security43Repository()
        reset_store()
        await _seed_events(repo, [
            (i, "confirmed", i) for i in range(1, 8)])
        await _seed_events(repo, [
            (90, "behavior_alert_pending", 0)])   # 占位不参与

        r = await evaluate()
        sig = r["signals"]
        d5 = sig.get("d5") or {}
        record("d5含criteria三条件",
               "criteria" in d5 and "d5Enforce" in d5
               and "observeDays" in d5, str(d5)[:120])
        ti = sig.get("threatintel") or {}
        record("threatintel含段数", "totalCidrs" in ti
               and "degraded" in ti, str(ti)[:120])
        geo = sig.get("geo") or {}
        record("geo布尔", isinstance(geo.get("available"), bool),
               str(geo))
        ab = sig.get("abuseipdb") or {}
        record("abuseipdb mode", ab.get("mode") in (
            "mock", "real", "mock_fallback"), str(ab))


class TestStructure:
    async def run(self):
        print("[09 结构与铁律]")
        reset_store()
        r = await evaluate()

        record("enforceLevel灰度态",
               r["enforceLevel"] == "observe", str(r["enforceLevel"]))
        record("note只评估不切换",
               "只评估不切换" in r["note"], str(r["note"])[:100])
        record("checkedAt在位", bool(r["checkedAt"]),
               str(r["checkedAt"]))
        record("五检查齐", [c["id"] for c in r["checks"]] == [
            "observe_days", "false_positive_rate",
            "pending_backlog", "appeal_channel",
            "health_whitelist"],
            str([c["id"] for c in r["checks"]]))


class TestHttp:
    async def run(self):
        print("[10 HTTP层]")
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from routes.security_routes import register_security_routes

        app = FastAPI()
        register_security_routes(app)
        client = TestClient(app)

        # 缺 Role 403
        resp = client.get("/api/security/admin/enforce/readiness")
        record("缺Role403", resp.status_code == 403,
               str(resp.status_code))

        # 200 结构
        resp = client.get("/api/security/admin/enforce/readiness",
                          headers={"X-Role": "admin"})
        body = resp.json()
        record("200五检查结构", resp.status_code == 200
               and len(body.get("checks") or []) == 5
               and "signals" in body and "blockers" in body
               and "overall" in body, str(body)[:120])

        # 服务层异常 → 500 包装
        import services.enforce_readiness_service as ers
        orig = ers.EnforceReadinessService.evaluate

        async def _boom(self):
            raise RuntimeError("评估异常")
        ers.EnforceReadinessService.evaluate = _boom
        resp = client.get("/api/security/admin/enforce/readiness",
                          headers={"X-Role": "admin"})
        ers.EnforceReadinessService.evaluate = orig
        record("服务异常500", resp.status_code == 500,
               str(resp.status_code))


async def run_all():
    await TestObserveDays().run()
    await TestFalsePositiveRate().run()
    await TestPendingBacklog().run()
    await TestAppealChannel().run()
    await TestWhitelist().run()
    await TestOverall().run()
    await TestBlockers().run()
    await TestSignals().run()
    await TestStructure().run()
    await TestHttp().run()


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
