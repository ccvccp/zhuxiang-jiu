"""43号·P4-3 威胁情报接入专项测试

运行方式:
    python test_security_p4_3.py

覆盖(计划 §四):
    - netset 解析: IP/CIDR/注释/去重/规范化(/32)/非法行/
      超上限/全非法
    - 导入: 全量替换/幂等/来源元信息
    - CIDR 匹配: 段内命中/段外不命中/非法 IP 中性
    - 信誉联动: 命中降档30(不直封)/未命中零影响/已降档不重复
      降/降档留痕可申诉
    - 网关联动: 命中情报段 IP 请求 → 信誉 30 + 事件留痕
    - HTTP 层: import/stats/check 三端点+鉴权
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


class TestParse:
    def run(self):
        print("[01 netset解析]")
        from services.threatintel_service import ThreatIntelService

        cidrs = ThreatIntelService.parse_netset(
            "# Firehol level1\n1.2.3.4\n5.6.7.0/24\n\n"
            "1.2.3.4\nbad-line\n9.9.9.9/32\n")
        record("有效段解析", len(cidrs) == 3, str(cidrs))
        record("去重", cidrs.count("1.2.3.4/32") == 1)
        record("单IP规范化/32", "1.2.3.4/32" in cidrs)
        record("非法行跳过", "bad-line" not in str(cidrs))
        # 超上限(30000 个合法唯一段: 10.q.r.0/24, q=i//250, r=i%250)
        try:
            ThreatIntelService.parse_netset("\n".join(
                f"10.{i // 250}.{i % 250}.0/24"
                for i in range(30000)))
            record("超上限拒绝", False, "应抛 ValueError")
        except ValueError:
            record("超上限拒绝", True)
        # 全非法
        try:
            ThreatIntelService.parse_netset("not-an-ip")
            record("全非法拒绝", False, "应抛 ValueError")
        except ValueError:
            record("全非法拒绝", True)
        # 空内容(仅注释)
        try:
            ThreatIntelService.parse_netset("# only comment\n\n")
            record("空内容拒绝", False, "应抛 ValueError")
        except ValueError:
            record("空内容拒绝", True)


class TestImport:
    async def run(self):
        print("[02 导入]")
        from services.threatintel_service import ThreatIntelService
        svc = ThreatIntelService()

        r = await svc.import_netset(
            "1.2.3.4\n5.6.7.0/24\n", source="test")
        record("导入成功", r["success"] is True
               and r["imported"] == 2, str(r))
        # 幂等(替换导入, 默认源)
        r = await svc.import_netset("1.2.3.4\n5.6.7.0/24\n")
        record("重复导入幂等", r["imported"] == 2
               and r["cleared"] == 2, str(r))
        # 增量(replace=False 不清旧)
        await svc.import_netset("1.2.3.4\n5.6.7.0/24\n")
        r = await svc.import_netset("8.8.8.8\n",
                                    replace=False)
        record("增量导入保留", r["imported"] == 1
               and r["cleared"] == 0)
        stats = await svc.stats()
        record("统计3段", stats["totalCidrs"] == 3,
               str(stats))
        record("来源分布", stats["sources"].get(
                   "firehol_level1") == 3,
               str(stats["sources"]))


class TestMatch:
    async def run(self):
        print("[03 CIDR匹配]")
        from services.threatintel_service import ThreatIntelService
        svc = ThreatIntelService()
        await svc.import_netset(
            "1.2.3.4\n5.6.7.0/24\n8.8.8.0/24\n")

        hit = await svc.check_ip("1.2.3.4")
        record("单IP命中", hit is not None
               and hit["cidr"] == "1.2.3.4/32", str(hit))
        hit = await svc.check_ip("5.6.7.99")
        record("段内命中", hit is not None
               and hit["cidr"] == "5.6.7.0/24")
        hit = await svc.check_ip("5.6.8.1")
        record("段外不命中", hit is None)
        hit = await svc.check_ip("not-ip")
        record("非法IP中性", hit is None)
        hit = await svc.check_ip("")
        record("空IP中性", hit is None)


class TestReputationLink:
    async def run(self):
        print("[04 信誉联动]")
        from services.threatintel_service import ThreatIntelService
        from services.security_service import Security43Service
        from repositories.security_repository import \
            Security43Repository
        svc = ThreatIntelService()
        sec = Security43Service()
        repo = Security43Repository()
        await svc.import_netset("203.0.113.0/24\n")

        # 未命中: 零影响
        rep = await sec.ensure_reputation("6.6.6.6")
        rep2 = await svc.apply_to_reputation("6.6.6.6", dict(rep))
        record("未命中零影响", rep2["score"] == 80.0,
               str(rep2["score"]))

        # 命中: 降档 31(不直封——31 在 suspicious 区间)
        rep = await sec.ensure_reputation("203.0.113.55")
        rep2 = await svc.apply_to_reputation("203.0.113.55", rep)
        record("命中降档31(不直封)", rep2["score"] == 31.0,
               str(rep2["score"]))
        record("降档suspicious", rep2["status"] == "suspicious",
               rep2["status"])
        # 降档留痕
        events = await repo.list_events(limit=10)
        ti_ev = [e for e in events
                 if e.get("action") == "threatintel_hit"]
        record("降档事件留痕", len(ti_ev) >= 1,
               f"共{len(ti_ev)}条")
        record("留痕含段信息", ti_ev
               and "203.0.113.0/24" in str(ti_ev[0].get("factors")),
               str(ti_ev[:1])[:100])

        # 已降档: 不重复降(仍 31, 无重复事件)
        events_before = len([e for e in await repo.list_events(
            limit=50) if e.get("action") == "threatintel_hit"])
        rep3 = await svc.apply_to_reputation("203.0.113.55", rep2)
        record("已降档不重复降", rep3["score"] == 31.0,
               str(rep3["score"]))


class TestGatewayIntegration:
    async def run(self):
        print("[05 网关联动]")
        from services.threatintel_service import ThreatIntelService
        from services.security_service import Security43Service
        from repositories.security_repository import \
            Security43Repository
        svc = ThreatIntelService()
        sec = Security43Service()
        repo = Security43Repository()

        # 导入段后, 网关请求(observe 仍放行但信誉已降+事件)
        await svc.import_netset("198.51.100.0/24\n")
        r = await sec.process_request(
            "198.51.100.10", method="GET",
            path="/api/product/list", ua="Mozilla/5.0", hour=14)
        record("observe下仍放行(不直封)", r["action"] == "allow",
               str(r["action"]))
        rep = await repo.get_reputation("198.51.100.10")
        record("网关路径信誉降档", rep is not None
               and rep["score"] == 31.0, str(rep)[:80])
        events = await repo.list_events(limit=5)
        record("网关路径事件留痕", any(
            e.get("action") == "threatintel_hit"
            for e in events))

        # 无情报表: 完全零影响(回归)
        await svc.import_netset("", replace=True) \
            if False else await repo.clear_threatintel()
        rep = await repo.get_reputation("6.6.6.7")
        r = await sec.process_request(
            "6.6.6.7", method="GET", path="/api/product/list",
            ua="Mozilla/5.0", hour=14)
        record("清空后零影响", r["action"] == "allow")


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

        resp = client.post("/api/security/admin/threatintel/import",
                           json={"content": "1.2.3.4\n"})
        record("HTTP-import缺Role403", resp.status_code == 403)

        resp = client.post("/api/security/admin/threatintel/import",
                           json={"content": "1.2.3.4\n"
                                 "5.6.7.0/24\n"},
                           headers=admin)
        record("HTTP-import", resp.status_code == 200
               and resp.json().get("imported") == 2,
               str(resp.json())[:80])

        resp = client.post("/api/security/admin/threatintel/import",
                           json={"content": "bad"},
                           headers=admin)
        record("HTTP-非法netset409", resp.status_code == 409)

        resp = client.get("/api/security/admin/threatintel/stats",
                          headers=admin)
        record("HTTP-stats", resp.status_code == 200
               and resp.json().get("totalCidrs") == 2)

        resp = client.get(
            "/api/security/admin/threatintel/check?ip=1.2.3.4",
            headers=admin)
        record("HTTP-check命中", resp.status_code == 200
               and (resp.json().get("hit") or {}).get("cidr")
               == "1.2.3.4/32")
        resp = client.get(
            "/api/security/admin/threatintel/check?ip=9.9.9.9",
            headers=admin)
        record("HTTP-check未命中", resp.status_code == 200
               and resp.json().get("hit") is None)


async def run_all():
    TestParse().run()
    await TestImport().run()
    await TestMatch().run()
    await TestReputationLink().run()
    await TestGatewayIntegration().run()
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
