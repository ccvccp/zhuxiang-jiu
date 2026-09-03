"""43号·P4-4 Redis 实况监控专项测试

运行方式:
    python test_security_p4_4.py

覆盖(计划 §五):
    - 服务单元(内存模式): collect 结构/键族计数/告警空态
    - 键族归类: 表键/TTL键/seq计数器/未知键
    - 阈值告警: 大key/rate泄漏/内存水位/碎片率
    - 人类可读: _human 字节格式
    - 慢日志解析: list/dict 双口径
    - HTTP 层: redis/health 鉴权+结构
    - 日报序列端点回归(⑦区数据源)
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


class TestCollect:
    async def run(self):
        print("[01 服务单元(内存模式)]")
        from services.redis_health_service import (
            RedisHealthService, FAMILIES,
        )
        svc = RedisHealthService()
        r = await svc.collect()
        record("collect成功", r.get("success") is True, str(r)[:80])
        record("内存模式标识", r.get("mode") == "asyncio",
               str(r.get("mode")))
        record("键族12类齐全",
               set(FAMILIES) <= set(r.get("keyFamilies", {})),
               str(sorted(r.get("keyFamilies", {}))))
        record("告警空态", r.get("alerts") == [], str(r.get("alerts")))
        record("采集时间留痕", bool(r.get("collectedAt")))
        record("内存模式memory为空", r.get("memory") is None)

        # 制造数据: 网关请求(events/reputation/rate) + 情报导入
        from services.security_service import Security43Service
        from services.threatintel_service import ThreatIntelService
        await ThreatIntelService().import_netset("203.0.113.0/24\n")
        await Security43Service().process_request(
            "203.0.113.9", method="GET", path="/api/product/list",
            ua="Mozilla/5.0", hour=14)
        r2 = await svc.collect()
        fam = r2.get("keyFamilies", {})
        record("事件族计数", fam.get("security_events", 0) >= 1,
               str(fam))
        record("信誉族计数", fam.get("security_ip_reputation", 0) >= 1,
               str(fam))
        record("频次族计数", fam.get("rate", 0) >= 1, str(fam))
        record("情报族计数", fam.get("threatintel") == 1, str(fam))


class TestClassify:
    def run(self):
        print("[02 键族归类]")
        from services.redis_health_service import _classify
        prefix = len("zhuxiang:security43:")
        cases = [
            ("zhuxiang:security43:security_events:12",
             "security_events"),
            ("zhuxiang:security43:security_ip_reputation:1.2.3.4",
             "security_ip_reputation"),
            ("zhuxiang:security43:security_blocks:1.2.3.4",
             "security_blocks"),
            ("zhuxiang:security43:threatintel:5.6.7.0/24",
             "threatintel"),
            ("zhuxiang:security43:rate:ip:1.2.3.4", "rate"),
            ("zhuxiang:security43:challpass:1.2.3.4", "challpass"),
            ("zhuxiang:security43:behavior:5", "behavior"),
            ("zhuxiang:security43:geo:5", "geo"),
            ("zhuxiang:security43:session:5", "session"),
            ("zhuxiang:security43:event:seq", "seq"),
            ("zhuxiang:security43:appeal:seq", "seq"),
            ("zhuxiang:security43:weird:1", "other"),
        ]
        for key, expect in cases:
            record(f"归类[{key.split(':', 3)[-1]}]",
                   _classify(key, prefix) == expect,
                   f"expect={expect}")


class TestAlerts:
    def run(self):
        print("[03 阈值告警]")
        from services.redis_health_service import RedisHealthService
        svc = RedisHealthService()

        # 大 key 告警
        alerts = svc._alerts(
            {}, None, [{"key": "k", "bytes": 150000,
                        "human": "150.0KB"}])
        record("大key告警", len(alerts) == 1
               and alerts[0]["level"] == "warn"
               and "大 key" in alerts[0]["message"], str(alerts))

        # rate 泄漏告警
        alerts = svc._alerts({"rate": 150_000}, None, [])
        record("rate泄漏告警", any("泄漏" in a["message"]
                                  for a in alerts), str(alerts))

        # 内存水位告警(critical)
        mem = {"maxBytes": 100, "usedBytes": 90, "usedPct": 0.9,
               "usedHuman": "90B", "maxHuman": "100B",
               "fragmentationRatio": 1.1}
        alerts = svc._alerts({}, mem, [])
        record("水位critical", any(a["level"] == "critical"
                                   for a in alerts), str(alerts))

        # 碎片率 info 告警
        mem2 = {"maxBytes": 100, "usedBytes": 10, "usedPct": 0.1,
                "fragmentationRatio": 2.0}
        alerts = svc._alerts({}, mem2, [])
        record("碎片率info", any(a["level"] == "info"
                                 and "碎片" in a["message"]
                                 for a in alerts), str(alerts))

        # 无超限 → 无告警
        mem3 = {"maxBytes": 100, "usedBytes": 10, "usedPct": 0.1,
                "fragmentationRatio": 1.0}
        record("无超限零告警",
               svc._alerts({"rate": 100}, mem3, []) == [])

        # 内存无上限(maxBytes=0) → 不告水位
        mem4 = {"maxBytes": 0, "usedPct": None}
        record("无上限不告水位",
               svc._alerts({}, mem4, []) == [])


class TestHuman:
    def run(self):
        print("[04 人类可读]")
        from services.redis_health_service import _human
        record("100B", _human(100) == "100B", _human(100))
        record("100.0KB", _human(102_400) == "100.0KB",
               _human(102_400))
        record("1.5MB", _human(1_572_864) == "1.5MB",
               _human(1_572_864))
        record("2.0GB", _human(2 * 1024 ** 3) == "2.0GB",
               _human(2 * 1024 ** 3))


class TestSlowlogParse:
    def run(self):
        print("[05 慢日志解析]")
        from services.redis_health_service import _parse_slowlog
        # list 风格: [id, ts, duration_us, [args]]
        out = _parse_slowlog([[1, 1700000000, 25000,
                               ["KEYS", "zhuxiang:*"]]])
        record("list口径", len(out) == 1 and out[0]["durationMs"] == 25.0
               and "KEYS" in out[0]["command"], str(out))
        # dict 风格(RESP3/新版)
        out = _parse_slowlog([{"id": 2, "duration": 5000,
                               "command": ["GET", "k"]}])
        record("dict口径", len(out) == 1 and out[0]["durationMs"] == 5.0
               and "GET" in out[0]["command"], str(out))
        record("空列表", _parse_slowlog([]) == [])
        record("畸形条目跳过", _parse_slowlog([["bad"]]) == [])


class TestHttpRoutes:
    async def run(self):
        print("[06 HTTP层]")
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from routes.security_routes import register_security_routes

        app = FastAPI()
        register_security_routes(app)
        client = TestClient(app)
        admin = {"X-Role": "admin"}

        resp = client.get("/api/security/admin/redis/health")
        record("HTTP-缺Role403", resp.status_code == 403,
               str(resp.status_code))

        resp = client.get("/api/security/admin/redis/health",
                          headers=admin)
        body = resp.json()
        record("HTTP-体检200", resp.status_code == 200
               and body.get("success") is True, str(body)[:80])
        record("HTTP-结构齐全", all(
            k in body for k in ("mode", "memory", "dbSize",
                                "keyFamilies", "slowlog", "bigKeys",
                                "alerts", "collectedAt")),
            str(list(body)))
        record("HTTP-键族返回", isinstance(body.get("keyFamilies"), dict)
               and "security_events" in body["keyFamilies"])

        # ⑦区数据源: 日报序列回归
        resp = client.get("/api/security/admin/reports/daily?days=14",
                          headers=admin)
        body = resp.json()
        record("HTTP-日报序列14天", resp.status_code == 200
               and len(body.get("reports", [])) == 14,
               str(resp.status_code))
        record("HTTP-日报summary",
               all(k in body.get("summary", {})
                   for k in ("eventsTotal", "falsePositiveRate",
                             "activeDays", "d5Samples")),
               str(body.get("summary")))


async def run_all():
    await TestCollect().run()
    TestClassify().run()
    TestAlerts().run()
    TestHuman().run()
    TestSlowlogParse().run()
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
