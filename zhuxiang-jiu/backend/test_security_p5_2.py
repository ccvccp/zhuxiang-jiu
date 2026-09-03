"""43号·P5-2 Redis 告警消息通道专项测试

运行方式:
    python test_security_p5_2.py

覆盖(计划 §五):
    - 服务单元: 无告警零发送/critical+warn触达/info过滤/
      聚合单封(标题含条数)/内容含规则+消息/P1优先级+SECURITY
    - 去重: 同规则24h第二次deduped/不同规则独立/force跳过/
      去重锁过期重新触达
    - 管理员触达: 多管理员逐一发送/无管理员零发送/
      单人send异常不阻断/角色过滤(member不收)
    - 调度轨: ④步骤alerts统计/告警异常不阻断基线/lastAlerts留痕
    - HTTP层: 缺Role403/force参数/返回结构
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


# --------------------------------------------------------
# 体检结果 mock(专项聚焦告警链路; 采集逻辑由 P4-4 测试覆盖)
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
    """替换 RedisHealthService.collect(返回受控告警集)"""
    import services.redis_health_service as rhs

    async def _collect(self):
        return (fake if fake is not None else FAKE_COLLECT)
    rhs.RedisHealthService.collect = _collect


def _clear_dedupe():
    from repositories.backend import get_in_memory_store
    store = get_in_memory_store()
    store.pop("_security43_alert_dedupe", None)


async def _seed_admins(count: int = 2) -> list[int]:
    """造管理员会员(带 role=admin, 幂等) + 一个普通会员"""
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
    # 普通会员(不应收到告警)
    member = await repo.get_by_phone("13900009999")
    if member is None:
        await repo.create({
            "phone": "13900009999", "nickname": "member",
            "role": "member", "status": "active"})
    return ids


async def _latest_security_messages(user_id: int) -> list[dict]:
    """查管理员收件箱里 CATEGORY_SECURITY 最新消息"""
    from repositories.message_repository import MessageRepository
    repo = MessageRepository()
    try:
        msgs = await repo.list_messages(user_id=user_id, limit=20) \
            if hasattr(repo, "list_messages") else []
    except Exception:
        msgs = []
    if not msgs and hasattr(repo, "list_by_user"):
        msgs = await repo.list_by_user(user_id, limit=20)
    return [m for m in (msgs or [])
            if m.get("category") == "security"]


class TestService:
    async def run(self):
        print("[01 服务单元]")
        from services.security_alert_service import SecurityAlertService
        svc = SecurityAlertService()

        # 无告警零发送(mock 空 alerts)
        _mock_collect({"success": True, "mode": "asyncio",
                       "memory": None, "dbSize": None,
                       "keyFamilies": {}, "slowlog": [],
                       "bigKeys": [], "alerts": [],
                       "collectedAt": "t"})
        r = await svc.notify_redis_alerts()
        record("无告警零发送", r["eligible"] == 0 and r["sent"] == 0,
               str(r))

        # 受控告警集: 1 warn + 1 info(仅 warn 触达)
        _mock_collect()
        admins = await _seed_admins(2)
        _clear_dedupe()
        r = await svc.notify_redis_alerts()
        # admins 含基础种子中的既有管理员 → 相对口径: 全员送达
        record("warn触达info过滤", r["eligible"] == 1
               and r["sent"] == r["admins"] >= 2, str(r))
        record("聚合单封(每管理员各1)", r["sent"] == r["admins"]
               and r["fresh"] == 1, str(r))
        # 收件箱验证(经仓储查最近 SECURITY 消息)
        msgs = await _latest_security_messages(admins[0])
        record("站内信落库", len(msgs) >= 1, f"{len(msgs)}条")
        record("标题含条数", msgs and "体检告警" in str(
            msgs[0].get("title")) and "(" in str(
            msgs[0].get("title")), str(msgs[:1])[:100])
        record("内容含规则", msgs and "100KB" in str(
            msgs[0].get("content")), str(msgs[:1])[:120])
        record("P1优先级", msgs and msgs[0].get("priority") == "P1",
               str(msgs[:1])[:100])

        # member 角色不收
        from repositories.member_repository import MemberRepository
        member = await MemberRepository().get_by_phone("13900009999")
        m_msgs = await _latest_security_messages(int(member["id"]))
        record("普通会员不收", len(m_msgs) == 0, f"{len(m_msgs)}条")


class TestDedupe:
    async def run(self):
        print("[02 规则级24h去重]")
        from services.security_alert_service import SecurityAlertService
        from repositories.backend import get_in_memory_store
        svc = SecurityAlertService()
        _mock_collect()
        await _seed_admins(1)
        _clear_dedupe()

        r1 = await svc.notify_redis_alerts()
        record("首发送", r1["sent"] >= 1 and r1["deduped"] == 0,
               str(r1))
        r2 = await svc.notify_redis_alerts()
        record("同规则24h去重", r2["deduped"] >= 1
               and r2["sent"] == 0, str(r2))
        r3 = await svc.notify_redis_alerts(force=True)
        record("force跳过去重", r3["sent"] >= 1, str(r3))

        # 去重锁过期后重新触达(规则仍在告警)
        import time as _time
        store = get_in_memory_store()
        bucket = store.setdefault("_security43_alert_dedupe", {})
        for k in list(bucket):
            bucket[k] = _time.time() - 1   # 已过期
        r5 = await svc.notify_redis_alerts()
        record("过期重新触达", r5["sent"] >= 1
               and r5["deduped"] == 0, str(r5))


class TestBroadcast:
    async def run(self):
        print("[03 管理员触达]")
        from services.security_alert_service import SecurityAlertService
        svc = SecurityAlertService()
        _mock_collect()
        await _seed_admins(1)
        _clear_dedupe()

        # 单人 send 异常不阻断: 拦截 send_message 抛错
        import services.message_service as ms
        orig = ms.MessageService.send_message
        ms.MessageService.send_message = _fail_send
        r = await svc.notify_redis_alerts()
        record("单人异常不阻断", r["failed"] >= 1 and r["sent"] == 0,
               str(r))
        ms.MessageService.send_message = orig

        # 无管理员零发送
        async def _empty_admins(self):
            return []
        orig_list = SecurityAlertService._list_admin_ids
        SecurityAlertService._list_admin_ids = _empty_admins
        _clear_dedupe()
        r = await svc.notify_redis_alerts()
        record("无管理员零发送", r["admins"] == 0 and r["sent"] == 0,
               str(r))
        SecurityAlertService._list_admin_ids = orig_list


async def _fail_send(self, *args, **kwargs):
    raise ValueError("模拟发送失败")


class TestScheduler:
    async def run(self):
        print("[04 调度轨]")
        from services.security_scheduler import (
            run_scheduled_security_tasks,
        )
        _mock_collect()
        await _seed_admins(1)
        _clear_dedupe()

        stats = await run_scheduled_security_tasks()
        record("调度含alerts统计", "lastAlerts" in stats
               and isinstance(stats["lastAlerts"], dict),
               str(stats.get("lastAlerts")))
        record("alerts计数正确",
               (stats["lastAlerts"] or {}).get("sent", 0) >= 1,
               str(stats.get("lastAlerts")))
        record("基线重建不受影响",
               stats.get("lastBaselines") is not None,
               str(stats.get("lastBaselines")))

        # 告警异常不阻断调度(拦截 collect 抛错)
        import services.redis_health_service as rhs

        async def _fail_collect(self):
            raise ValueError("模拟采集失败")
        orig = rhs.RedisHealthService.collect
        rhs.RedisHealthService.collect = _fail_collect
        stats = await run_scheduled_security_tasks()
        record("告警异常不阻断调度",
               stats.get("lastBaselines") is not None
               and any("alert" in str(e) for e in
                       stats.get("lastErrors", [])),
               str(stats.get("lastErrors")))
        rhs.RedisHealthService.collect = orig


class TestHttpRoutes:
    async def run(self):
        print("[05 HTTP层]")
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from routes.security_routes import register_security_routes

        app = FastAPI()
        register_security_routes(app)
        client = TestClient(app)

        resp = client.post("/api/security/admin/redis/alert/test")
        record("缺Role403", resp.status_code == 403)

        _mock_collect()
        await _seed_admins(1)
        _clear_dedupe()

        resp = client.post("/api/security/admin/redis/alert/test",
                           headers={"X-Role": "admin"})
        body = resp.json()
        record("手动轨200", resp.status_code == 200
               and body.get("success") is True, str(resp.status_code))
        record("返回结构", all(k in body for k in (
            "alerts", "eligible", "deduped", "admins", "sent",
            "failed", "collectedAt")), str(list(body)))
        record("发送计数", body.get("sent") >= 1, str(body))

        # 二次触发去重
        resp = client.post("/api/security/admin/redis/alert/test",
                           headers={"X-Role": "admin"})
        body = resp.json()
        record("二次去重", body.get("deduped") >= 1
               and body.get("sent") == 0, str(body))

        # force 跳过
        resp = client.post(
            "/api/security/admin/redis/alert/test?force=true",
            headers={"X-Role": "admin"})
        body = resp.json()
        record("force跳过", body.get("sent") >= 1, str(body))


async def run_all():
    await TestService().run()
    await TestDedupe().run()
    await TestBroadcast().run()
    await TestScheduler().run()
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
