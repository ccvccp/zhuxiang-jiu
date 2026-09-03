"""43号·P6-2 告警通道多信号化专项测试

运行方式:
    python test_security_p6_2.py

覆盖(计划 §三):
    - S2 订阅降级: consecutiveFailures≥3 触达/2 次不触达/
      lastError 含消息/单源 rule
    - S3 基线异常: lastErrors 含 baseline_anomaly 触达/
      其他错误不触发
    - 三信号聚合: 三信号单封(站内信含全部 rule)/
      多信号标题/分组渲染
    - 去重: 三 rule 各自独立 24h 去重/force 跳过
    - 无信号零发送
    - 旧端点兼容: notify_redis_alerts 不含 intel/scheduler
      信号/Redis-only 标题兼容
    - 调度轨: ④ 三信号化(S3 滞后一轮口径)
    - HTTP 层: 新端点 403/200/signals 结构/旧端点结构
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


def _clear_dedupe():
    from repositories.backend import get_in_memory_store
    store = get_in_memory_store()
    store.pop("_security43_alert_dedupe", None)


# --------------------------------------------------------
# 体检结果 mock(专项聚焦告警链路; 采集逻辑 P4-4 测试覆盖)
# --------------------------------------------------------
FAKE_COLLECT = {
    "success": True, "mode": "asyncio", "memory": None,
    "dbSize": None, "keyFamilies": {}, "slowlog": [],
    "bigKeys": [{"key": "zhuxiang:security43:security_events:BIG",
                 "bytes": 150000, "human": "150.0KB"}],
    "alerts": [
        {"level": "warn", "rule": "单键 >100KB",
         "message": "1 个大 key(最大 150.0KB), 建议裁决归档清理"},
        {"level": "info", "rule": "碎片率 >1.5",
         "message": "内存碎片率 5.19, 关注 activedefrag"},
    ],
    "collectedAt": "2026-09-03T00:00:00",
}


def _mock_collect(fake: dict = None):
    """替换 RedisHealthService.collect(受控告警集)"""
    import services.redis_health_service as rhs

    async def _collect(self):
        return (fake if fake is not None else FAKE_COLLECT)
    rhs.RedisHealthService.collect = _collect


def _mock_collect_empty():
    _mock_collect({"success": True, "mode": "asyncio",
                   "memory": None, "dbSize": None,
                   "keyFamilies": {}, "slowlog": [],
                   "bigKeys": [], "alerts": [],
                   "collectedAt": "t"})


async def _seed_admins(count: int = 2) -> list[int]:
    """造管理员会员(带 role=admin, 幂等)"""
    from repositories.member_repository import MemberRepository
    repo = MemberRepository()
    ids = []
    for i in range(count):
        phone = f"1390000{8000 + i}"
        m = await repo.get_by_phone(phone)
        if m is None:
            m = await repo.create({
                "phone": phone, "nickname":
                    f"admin{i}", "role": "admin", "status": "active"})
        ids.append(int(m["id"]))
    return ids


async def _set_auto_state(failures: int = 0, error: str = ""):
    """设订阅自动状态(S2 信号源)"""
    from repositories.security_repository import \
        Security43Repository
    await Security43Repository().save_threatintel_auto_state({
        "lastAutoImportAt": "",
        "lastAutoStatus": "failed" if failures else "",
        "consecutiveFailures": failures,
        "lastError": error})


async def _set_sched_stats(errors: list = None):
    """设调度器统计(S3 信号源)"""
    from repositories.security_repository import \
        Security43Repository
    await Security43Repository().save_scheduler_stats({
        "runs": 1, "lastRunAt": "", "lastErrors": errors or []})


async def _latest_security_messages(user_id: int) -> list[dict]:
    """查管理员收件箱里 CATEGORY_SECURITY 最新消息"""
    from repositories.message_repository import MessageRepository
    msgs = await MessageRepository().list_messages(
        user_id=user_id, limit=30)
    return [m for m in (msgs or [])
            if m.get("category") == "security"]


class TestS2Intel:
    async def run(self):
        print("[01 S2 订阅降级]")
        from services.security_alert_service import SecurityAlertService
        svc = SecurityAlertService()
        _mock_collect_empty()
        admins = await _seed_admins(1)
        _clear_dedupe()

        # 2 次失败未达阈值 → 不触达
        await _set_auto_state(2, "模拟拉取失败")
        r = await svc.notify_security_alerts()
        record("2次失败不触达", r["eligible"] == 0
               and r["sent"] == 0, str(r))

        # 3 次失败 → S2 触达
        await _set_auto_state(3, "模拟拉取失败(超时)")
        _clear_dedupe()
        r = await svc.notify_security_alerts()
        record("3次失败触达", r["eligible"] == 1
               and r["sent"] == r["admins"] >= 1, str(r))
        record("signals.intel", r["signals"].get("intel") == 1,
               str(r["signals"]))
        record("signals.redis为0", r["signals"].get("redis", 0) == 0,
               str(r["signals"]))
        # 站内信内容含降级消息
        msgs = await _latest_security_messages(admins[0])
        record("降级消息内容", any(
            "威胁情报订阅连续失败 3 次" in str(m.get("content"))
            and "模拟拉取失败" in str(m.get("content"))
            for m in msgs), str(msgs[:1])[:150])


class TestS3Scheduler:
    async def run(self):
        print("[02 S3 基线异常]")
        from services.security_alert_service import SecurityAlertService
        svc = SecurityAlertService()
        _mock_collect_empty()
        await _seed_admins(1)
        _clear_dedupe()
        await _set_auto_state(0)

        # 其他错误不触发
        await _set_sched_stats(["rebuild:模拟异常", "posture:x"])
        r = await svc.notify_security_alerts()
        record("其他错误不触发", r["eligible"] == 0
               and r["sent"] == 0, str(r))

        # baseline_anomaly 触发
        await _set_sched_stats(["baseline_anomaly"])
        _clear_dedupe()
        r = await svc.notify_security_alerts()
        record("基线异常触达", r["eligible"] == 1
               and r["sent"] >= 1, str(r))
        record("signals.scheduler", r["signals"].get("scheduler") == 1,
               str(r["signals"]))


class TestThreeSignals:
    async def run(self):
        print("[03 三信号聚合]")
        from services.security_alert_service import SecurityAlertService
        svc = SecurityAlertService()
        _mock_collect()   # S1: 1 warn + 1 info
        admins = await _seed_admins(1)
        await _set_auto_state(3, "多源拉取失败")
        await _set_sched_stats(["baseline_anomaly"])
        _clear_dedupe()

        r = await svc.notify_security_alerts()
        record("三信号eligible=3", r["eligible"] == 3, str(r))
        record("三信号分组", r["signals"] == {
            "redis": 1, "intel": 1, "scheduler": 1}, str(r["signals"]))
        record("聚合单封送达", r["sent"] == r["admins"] >= 1, str(r))

        # 站内信: 多信号标题 + 三 rule 全含(分组渲染)
        msgs = await _latest_security_messages(admins[0])
        record("多信号标题", any(
            "安全告警" in str(m.get("title"))
            and "体检告警" not in str(m.get("title")) for m in msgs),
            str(msgs[:1])[:100])
        found = any(
            all(k in str(m.get("content")) for k in (
                "单键", "threatintel_degraded", "baseline_anomaly"))
            for m in msgs)
        record("三rule同封", found, str(msgs[:1])[:200])
        found_group = any(
            all(k in str(m.get("content")) for k in (
                "Redis 体检", "情报订阅", "调度器")) for m in msgs)
        record("信号分组渲染", found_group, str(msgs[:1])[:200])


class TestDedupe:
    async def run(self):
        print("[04 去重]")
        from services.security_alert_service import SecurityAlertService
        svc = SecurityAlertService()
        _mock_collect()
        await _seed_admins(1)
        await _set_auto_state(3, "失败")
        await _set_sched_stats(["baseline_anomaly"])
        _clear_dedupe()

        r1 = await svc.notify_security_alerts()
        record("首发送3条", r1["sent"] >= 1 and r1["deduped"] == 0,
               str(r1))
        r2 = await svc.notify_security_alerts()
        record("三rule独立去重", r2["deduped"] == 3
               and r2["sent"] == 0, str(r2))
        r3 = await svc.notify_security_alerts(force=True)
        record("force跳过", r3["sent"] >= 1, str(r3))


class TestNoSignal:
    async def run(self):
        print("[05 无信号零发送]")
        from services.security_alert_service import SecurityAlertService
        svc = SecurityAlertService()
        _mock_collect_empty()
        await _seed_admins(1)
        await _set_auto_state(0)
        await _set_sched_stats([])
        _clear_dedupe()
        r = await svc.notify_security_alerts()
        record("无信号零发送", r["eligible"] == 0 and r["sent"] == 0,
               str(r))
        record("无信号signals空", r["signals"] == {}, str(r["signals"]))


class TestLegacyCompat:
    async def run(self):
        print("[06 旧端点兼容]")
        from services.security_alert_service import SecurityAlertService
        svc = SecurityAlertService()
        _mock_collect()
        await _seed_admins(1)
        # S2/S3 信号在场——旧口径不应包含
        await _set_auto_state(3, "失败")
        await _set_sched_stats(["baseline_anomaly"])
        _clear_dedupe()

        r = await svc.notify_redis_alerts()
        record("旧口径仅Redis", r["signals"] == {"redis": 1},
               str(r["signals"]))
        record("旧口径eligible=1", r["eligible"] == 1, str(r))
        # Redis-only 标题兼容(P5-2 口径)
        from repositories.member_repository import \
            MemberRepository
        admin = await MemberRepository().get_by_phone("13900008000")
        msgs = await _latest_security_messages(int(admin["id"]))
        record("Redis-only标题兼容", any(
            "体检告警" in str(m.get("title")) for m in msgs),
            str(msgs[:1])[:100])


class TestSchedulerTrack:
    async def run(self):
        print("[07 调度轨三信号化]")
        from services.security_scheduler import (
            run_scheduled_security_tasks,
        )
        _mock_collect_empty()
        await _seed_admins(1)
        await _set_auto_state(0)
        _clear_dedupe()

        # 干净留痕 → 首轮无告警
        await _set_sched_stats([])
        stats = await run_scheduled_security_tasks()
        record("干净零告警", (stats.get("lastAlerts") or {}).get(
            "sent", 0) == 0, str(stats.get("lastAlerts")))

        # 上轮 baseline_anomaly → 本轮 ④ 触达(S3 滞后一轮口径)
        await _set_sched_stats(["baseline_anomaly"])
        _clear_dedupe()
        stats = await run_scheduled_security_tasks()
        record("S3调度轨触发", (stats.get("lastAlerts") or {}).get(
            "sent", 0) >= 1, str(stats.get("lastAlerts")))
        record("lastAlerts结构", set(stats.get("lastAlerts") or {})
               >= {"eligible", "deduped", "sent"},
               str(stats.get("lastAlerts")))


class TestHttpRoutes:
    async def run(self):
        print("[08 HTTP层]")
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from routes.security_routes import register_security_routes

        app = FastAPI()
        register_security_routes(app)
        client = TestClient(app)
        admin = {"X-Role": "admin"}

        resp = client.post("/api/security/admin/alerts/collect")
        record("缺Role403", resp.status_code == 403)

        # 无信号零发送
        _mock_collect_empty()
        await _set_auto_state(0)
        await _set_sched_stats([])
        _clear_dedupe()
        resp = client.post("/api/security/admin/alerts/collect",
                           headers=admin)
        body = resp.json()
        record("collect200", resp.status_code == 200
               and body.get("sent") == 0, str(body)[:80])
        record("signals结构", "signals" in body
               and isinstance(body["signals"], dict),
               str(body.get("signals")))

        # 三信号
        await _seed_admins(1)
        _mock_collect()
        await _set_auto_state(3, "失败")
        await _set_sched_stats(["baseline_anomaly"])
        _clear_dedupe()
        resp = client.post("/api/security/admin/alerts/collect",
                           headers=admin)
        body = resp.json()
        record("三信号触发", body.get("sent", 0) >= 1
               and body.get("signals") == {
                   "redis": 1, "intel": 1, "scheduler": 1},
               str(body))

        # 二次去重
        resp = client.post("/api/security/admin/alerts/collect",
                           headers=admin)
        body = resp.json()
        record("二次去重", body.get("deduped") == 3
               and body.get("sent") == 0, str(body))

        # force
        resp = client.post(
            "/api/security/admin/alerts/collect?force=true",
            headers=admin)
        record("force跳过", resp.json().get("sent", 0) >= 1,
               str(resp.json())[:80])

        # 旧端点结构兼容(S2/S3 在场但仅 Redis)
        _clear_dedupe()
        resp = client.post("/api/security/admin/redis/alert/test",
                           headers=admin)
        body = resp.json()
        record("旧端点200", resp.status_code == 200, str(resp.status_code))
        record("旧端点仅Redis信号", body.get("signals") == {
            "redis": 1}, str(body.get("signals")))


async def run_all():
    await TestS2Intel().run()
    await TestS3Scheduler().run()
    await TestThreeSignals().run()
    await TestDedupe().run()
    await TestNoSignal().run()
    await TestLegacyCompat().run()
    await TestSchedulerTrack().run()
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
