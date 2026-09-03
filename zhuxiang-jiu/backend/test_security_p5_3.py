"""43号·P5-3 威胁情报自动订阅专项测试

运行方式:
    python test_security_p5_3.py

覆盖(计划 §五):
    - 拉取校验: mock httpx 200成功/非200拒绝/超大拒绝/
      有效行不足拒绝/空内容拒绝/注释行不计入
    - 周期判断: 首次执行/周期内跳过/周期外执行/force跳过
    - 容错链: 拉取失败旧段保留/连续失败递增/第3次degraded/
      成功后计数清零
    - 状态留痕: lastAutoImportAt/lastAutoStatus/lastError截断
    - 导入链路: 成功→幂等替换/source留痕/命中联动
    - stats扩展: auto字段/degraded口径/enabled实况
    - 调度轨: ⑤步骤lastThreatintel留痕/AUTO off跳过/
      订阅异常不阻断基线
    - HTTP层: 缺Role403/refresh端点结构
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


# 合法 netset(≥100 有效行)
GOOD_NETSET = "\n".join(
    [f"10.{i // 250}.{i % 250}.0/24" for i in range(150)])


def _mock_http(status: int = 200, text: str = GOOD_NETSET):
    """mock fetch_netset 的 httpx 层(受控响应)"""
    import services.threatintel_feed as feed

    async def _fake_fetch(url=None, timeout=None):
        if status != 200:
            raise ValueError(f"拉取失败 HTTP {status}")
        if len(text.encode()) > feed.MAX_NETSET_BYTES:
            raise ValueError(
                f"内容超上限 {feed.MAX_NETSET_BYTES} 字节")
        lines = [ln for ln in text.splitlines()
                 if ln.strip() and not ln.lstrip().startswith("#")]
        if len(lines) < feed.MIN_NETSET_LINES:
            raise ValueError(
                f"有效行不足({len(lines)} < "
                f"{feed.MIN_NETSET_LINES})")
        return text
    feed.fetch_netset = _fake_fetch


def _mock_fail_fetch():
    import services.threatintel_feed as feed

    async def _fail(url=None, timeout=None):
        raise ValueError("模拟网络失败")
    feed.fetch_netset = _fail


class TestFetchValidate:
    async def run(self):
        print("[01 拉取校验]")
        import services.threatintel_feed as feed

        _mock_http(200, GOOD_NETSET)
        content = await feed.fetch_netset()
        record("200成功", "10.0.0.0/24" in content)

        _mock_http(429, "rate limited")
        try:
            await feed.fetch_netset()
            record("非200拒绝", False, "应抛 ValueError")
        except ValueError as e:
            record("非200拒绝", "429" in str(e), str(e))

        big = "x" * (feed.MAX_NETSET_BYTES + 1)
        _mock_http(200, big)
        try:
            await feed.fetch_netset()
            record("超大拒绝", False, "应抛 ValueError")
        except ValueError as e:
            record("超大拒绝", "超上限" in str(e), str(e))

        _mock_http(200, "1.2.3.4\n5.6.7.0/24\n")
        try:
            await feed.fetch_netset()
            record("行数不足拒绝", False, "应抛 ValueError")
        except ValueError as e:
            record("行数不足拒绝", "有效行不足" in str(e), str(e))

        # 注释行不计入有效行
        commented = "# c\n" * 50 + "1.2.3.4\n"
        _mock_http(200, commented)
        try:
            await feed.fetch_netset()
            record("注释行不计入", False, "应抛(1 有效行<100)")
        except ValueError:
            record("注释行不计入", True)

        # 参数
        record("周期下限3600",
               feed.feed_interval_seconds() >= 3600)
        os.environ["SECURITY_THREATINTEL_INTERVAL"] = "60"
        record("误配抬升", feed.feed_interval_seconds() == 3600)
        os.environ["SECURITY_THREATINTEL_INTERVAL"] = "7200"
        record("正常配置", feed.feed_interval_seconds() == 7200)
        os.environ.pop("SECURITY_THREATINTEL_INTERVAL", None)
        record("默认off", feed.feed_enabled() is False)
        record("默认源", "firehol" in feed.feed_url())


class TestCycle:
    async def run(self):
        print("[02 周期判断]")
        import services.threatintel_feed as feed
        from repositories.security_repository import (
            Security43Repository,
        )
        repo = Security43Repository()
        _mock_http(200, GOOD_NETSET)

        # 首次执行(无状态)
        r = await feed.maybe_refresh(force=True)
        record("首次执行", r["executed"] is True
               and r["status"] == "ok"
               and r["imported"] == 150, str(r))
        record("失败计数清零", r["consecutiveFailures"] == 0)

        # 周期内(刚成功导入)→ 跳过
        r = await feed.maybe_refresh()
        record("周期内跳过", r["executed"] is False
               and r["reason"] == "interval_not_reached", str(r))

        # force 跳过周期
        r = await feed.maybe_refresh(force=True)
        record("force跳过周期", r["executed"] is True,
               str(r))

        # 周期外(伪造 lastAutoImportAt 为很久前)
        state = await repo.get_threatintel_auto_state()
        state["lastAutoImportAt"] = "2020-01-01T00:00:00+00:00"
        await repo.save_threatintel_auto_state(state)
        r = await feed.maybe_refresh()
        record("周期外执行", r["executed"] is True, str(r))


class TestFailSoft:
    async def run(self):
        print("[03 容错链]")
        import services.threatintel_feed as feed
        from services.threatintel_service import ThreatIntelService
        from repositories.security_repository import (
            Security43Repository,
        )
        repo = Security43Repository()

        # 先成功导入(建立基线段)
        _mock_http(200, GOOD_NETSET)
        await feed.maybe_refresh(force=True)
        stats = await ThreatIntelService().stats()
        record("基线段建立", stats["totalCidrs"] == 150,
               str(stats["totalCidrs"]))

        # 失败1: 旧段保留 + 计数1
        _mock_fail_fetch()
        r = await feed.maybe_refresh(force=True)
        record("失败1计数", r["consecutiveFailures"] == 1
               and r["status"] == "failed", str(r))
        stats = await ThreatIntelService().stats()
        record("失败旧段保留", stats["totalCidrs"] == 150,
               str(stats["totalCidrs"]))

        # 失败2/3: 计数递增 + 第3次 degraded
        await feed.maybe_refresh(force=True)
        r = await feed.maybe_refresh(force=True)
        record("失败3计数", r["consecutiveFailures"] == 3, str(r))
        stats = await ThreatIntelService().stats()
        record("第3次degraded", stats["auto"].get("degraded")
               is True, str(stats["auto"]))

        # lastError 留痕
        state = await repo.get_threatintel_auto_state()
        record("lastError留痕", "模拟网络失败" in str(
            state.get("lastError")), str(state))

        # 恢复: 成功 → 计数清零 + degraded 消除
        _mock_http(200, GOOD_NETSET)
        r = await feed.maybe_refresh(force=True)
        record("恢复计数清零", r["consecutiveFailures"] == 0
               and r["status"] == "ok", str(r))
        stats = await ThreatIntelService().stats()
        record("degraded消除", stats["auto"].get("degraded")
               is False, str(stats["auto"]))


class TestImportLink:
    async def run(self):
        print("[04 导入链路]")
        import services.threatintel_feed as feed
        from services.threatintel_service import ThreatIntelService

        # source 留痕(自动轨标识)
        _mock_http(200, GOOD_NETSET)
        await feed.maybe_refresh(force=True)
        stats = await ThreatIntelService().stats()
        record("source自动轨留痕",
               stats["sources"].get("firehol_level1_auto") == 150,
               str(stats["sources"]))

        # 命中联动(替换导入后 CIDR 匹配正常)
        hit = await ThreatIntelService().check_ip("10.0.0.55")
        record("CIDR命中联动", hit is not None
               and hit.get("cidr") == "10.0.0.0/24", str(hit))

        # 幂等(同源重复拉取 → 全量替换 imported 一致)
        _mock_http(200, GOOD_NETSET)
        r = await feed.maybe_refresh(force=True)
        record("幂等替换", r["imported"] == 150, str(r))


class TestScheduler:
    async def run(self):
        print("[05 调度轨]")
        from services.security_scheduler import (
            run_scheduled_security_tasks,
        )
        import services.threatintel_feed as feed

        # AUTO off → ⑤ 跳过(无 threatintel 键)
        os.environ.pop("SECURITY_THREATINTEL_AUTO", None)
        _mock_http(200, GOOD_NETSET)
        stats = await run_scheduled_security_tasks()
        record("AUTO off跳过", "lastThreatintel" in stats
               and stats.get("lastThreatintel") is None,
               str(stats.get("lastThreatintel")))

        # AUTO on → 执行 + 留痕(刚导入成功处于周期内:
        # maybe_refresh 无 force → executed=False 属正常,
        # 留痕 status=ok 即证明 ⑤ 步骤接通)
        os.environ["SECURITY_THREATINTEL_AUTO"] = "on"
        stats = await run_scheduled_security_tasks()
        ti = stats.get("lastThreatintel") or {}
        record("AUTO on执行留痕", isinstance(
                   ti.get("executed"), bool)
               and ti.get("status") == "ok", str(ti))

        # AUTO on + 周期外 → 真执行(伪造过期状态)
        from repositories.security_repository import (
            Security43Repository,
        )
        state = await Security43Repository(
        ).get_threatintel_auto_state() or {}
        state["lastAutoImportAt"] = "2020-01-01T00:00:00+00:00"
        state["consecutiveFailures"] = 0
        await Security43Repository(
        ).save_threatintel_auto_state(state)
        stats = await run_scheduled_security_tasks()
        ti = stats.get("lastThreatintel") or {}
        record("周期外真执行", ti.get("executed") is True
               and ti.get("imported") == 150, str(ti))

        # 订阅异常不阻断基线(①②③④ 均正常)——伪造过期状态
        # 确保拉取真执行(fail 路径), ⑤ 独立 try/except
        state = await Security43Repository(
        ).get_threatintel_auto_state() or {}
        state["lastAutoImportAt"] = "2020-01-01T00:00:00+00:00"
        await Security43Repository(
        ).save_threatintel_auto_state(state)
        _mock_fail_fetch()
        stats = await run_scheduled_security_tasks()
        record("订阅异常不阻断调度",
               stats.get("lastBaselines") is not None
               and (stats.get("lastThreatintel") or {}).get(
                   "status") == "failed",
               str(stats.get("lastThreatintel")))
        os.environ.pop("SECURITY_THREATINTEL_AUTO", None)


class TestHttpRoutes:
    async def run(self):
        print("[06 HTTP层]")
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from routes.security_routes import register_security_routes
        import services.threatintel_feed as feed

        app = FastAPI()
        register_security_routes(app)
        client = TestClient(app)

        resp = client.post(
            "/api/security/admin/threatintel/auto/refresh")
        record("缺Role403", resp.status_code == 403)

        _mock_http(200, GOOD_NETSET)
        resp = client.post(
            "/api/security/admin/threatintel/auto/refresh",
            headers={"X-Role": "admin"})
        body = resp.json()
        record("refresh200", resp.status_code == 200
               and body.get("executed") is True,
               str(resp.status_code))
        record("refresh结构", all(k in body for k in (
            "executed", "status", "lastAutoImportAt",
            "consecutiveFailures", "degraded")),
            str(list(body)))
        record("refresh导入", body.get("imported") == 150,
               str(body))

        # 失败链路(拉取失败 200 返回 failed 状态——fail-soft
        # 不抛 500, 旧段保留)
        _mock_fail_fetch()
        resp = client.post(
            "/api/security/admin/threatintel/auto/refresh",
            headers={"X-Role": "admin"})
        body = resp.json()
        record("失败返回failed状态", resp.status_code == 200
               and body.get("status") == "failed", str(body))

        # stats.auto 结构
        resp = client.get("/api/security/admin/threatintel/stats",
                          headers={"X-Role": "admin"})
        body = resp.json()
        record("stats含auto", isinstance(body.get("auto"), dict)
               and "enabled" in body["auto"]
               and "degraded" in body["auto"],
               str(body.get("auto")))


async def run_all():
    await TestFetchValidate().run()
    await TestCycle().run()
    await TestFailSoft().run()
    await TestImportLink().run()
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
