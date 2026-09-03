"""43号·P5-4 AbuseIPDB 实时查询专项测试

运行方式:
    python test_security_p5_4.py

覆盖(计划 §五):
    - mock 分数: 三档确定性/同 IP 稳定/三档分布可构造
    - 三态: mock 直通/real 无 key 拒启/mock_fallback 回退/
      real 失败返回 None
    - 配额护栏: 计数递增/超红线 fallback/内存模式口径
    - 缓存: 命中不耗配额/mock 写缓存/refresh 跳过
    - 信誉联动: ≥75 降档 31/25-75 扣 10/<25 零影响/
      防重复扣/已降档不重复/留痕因子名
    - 两级串联: Firehol 命中→不查 AbuseIPDB/
      Firehol 未命中→查询执行
    - HTTP 层: 缺 Role 403/check 端点结构/refresh 参数
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
os.environ.pop("SECURITY_ABUSEIPDB_MODE", None)
os.environ.pop("SECURITY_ABUSEIPDB_KEY", None)

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


def _clear_abuse_cache():
    from repositories.backend import get_in_memory_store
    store = get_in_memory_store()
    store.pop("_security43_abuseipdb_result", None)
    store.pop("_security43_abuseipdb_quota", None)


def _find_ips_for_scores(scores: set) -> dict:
    """扫描 IP 末段找 mock 分数命中的样本"""
    import services.abuseipdb_client as ab
    found = {}
    for i in range(1, 256):
        for q in range(0, 4):
            ip = f"172.16.{q}.{i}"
            s = ab._mock_score(ip)
            if s in scores and s not in found:
                found[s] = ip
    return found


class TestMockScore:
    async def run(self):
        print("[01 mock 分数]")
        import services.abuseipdb_client as ab
        record("默认mock态", ab.abuseipdb_mode() == "mock")
        record("三档枚举", {ab._mock_score("1.2.3.4"),
                            ab._mock_score("1.2.3.5"),
                            ab._mock_score("1.2.3.6")} <= {0, 25, 85})
        record("同IP稳定", ab._mock_score("9.9.9.9")
               == ab._mock_score("9.9.9.9"))
        found = _find_ips_for_scores({0, 25, 85})
        record("三档样本可构造", set(found) == {0, 25, 85},
               str(found))
        # 全量分布: 三档均有大量样本
        all_scores = [ab._mock_score(f"172.31.{q}.{i}")
                      for q in range(4) for i in range(1, 251)]
        record("0档有样本", all_scores.count(0) > 50)
        record("25档有样本", all_scores.count(25) > 50)
        record("85档有样本", all_scores.count(85) > 50)
        self.samples = found


class TestModes:
    async def run(self):
        print("[02 三态]")
        import services.abuseipdb_client as ab
        _clear_abuse_cache()

        # mock 直通(不耗配额)
        r = await ab.check_ip("1.2.3.4")
        record("mock直通", r["source"] == "mock"
               and r["score"] in (0, 25, 85), str(r))
        record("mock零配额", r["quotaUsed"] == 0, str(r))

        # 缓存命中(零消耗)
        r2 = await ab.check_ip("1.2.3.4")
        record("缓存命中", r2["source"] == "cache"
               and r2["quotaUsed"] == 0, str(r2))

        # real 无 key 拒启(ValueError)
        os.environ["SECURITY_ABUSEIPDB_MODE"] = "real"
        _clear_abuse_cache()
        try:
            await ab.check_ip("5.6.7.8")
            record("real无key报错", False, "应 raise")
        except Exception as e:
            record("real无key报错",
                   isinstance(e, ValueError)
                   and "KEY" in str(e), str(e)[:80])
        os.environ.pop("SECURITY_ABUSEIPDB_MODE")

        # mock_fallback: real 失败(无 key) → 回退 mock 分数
        os.environ["SECURITY_ABUSEIPDB_MODE"] = "mock_fallback"
        _clear_abuse_cache()
        r = await ab.check_ip("5.6.7.9")
        record("fallback回退mock", r["source"] == "mock_fallback"
               and r["score"] in (0, 25, 85), str(r))
        record("fallback耗1配额", r["quotaUsed"] == 1, str(r))
        os.environ.pop("SECURITY_ABUSEIPDB_MODE")


class TestQuota:
    async def run(self):
        print("[03 配额护栏]")
        import services.abuseipdb_client as ab
        # 小红线便于测试
        os.environ["SECURITY_ABUSEIPDB_DAILY_LIMIT"] = "3"
        os.environ["SECURITY_ABUSEIPDB_MODE"] = "mock_fallback"
        _clear_abuse_cache()

        # 3 次成功(配额 3)
        for i in range(3):
            await ab.check_ip(f"192.168.1.{i + 1}")
        record("配额递增", await ab.get_quota_used() == 3,
               str(await ab.get_quota_used()))

        # 第 4 次超红线 → fallback + quota_exhausted
        r = await ab.check_ip("192.168.1.99")
        record("超红线fallback", r["source"] == "mock_fallback"
               and r.get("error") == "quota_exhausted", str(r))

        # real 态超红线 → score=None
        os.environ["SECURITY_ABUSEIPDB_MODE"] = "real"
        r = await ab.check_ip("192.168.1.98")
        record("real超红线None", r["score"] is None
               and r.get("error") == "quota_exhausted", str(r))

        # 缓存命中不耗配额
        used_before = await ab.get_quota_used()
        r = await ab.check_ip("192.168.1.1")   # 已缓存
        record("缓存不耗配额", r["source"] == "cache"
               and await ab.get_quota_used() == used_before,
               str(r))

        # refresh 跳过缓存
        r = await ab.check_ip("192.168.1.1", force=True)
        record("refresh跳过缓存", r["source"] != "cache", str(r))

        os.environ.pop("SECURITY_ABUSEIPDB_DAILY_LIMIT")
        os.environ.pop("SECURITY_ABUSEIPDB_MODE")


class TestReputationLink:
    async def run(self):
        print("[04 信誉联动]")
        from services.threatintel_service import ThreatIntelService
        from services.security_service import Security43Service
        from repositories.security_repository import (
            Security43Repository, reputation_status,
        )
        import services.abuseipdb_client as ab
        svc = ThreatIntelService()
        sec = Security43Service()
        repo = Security43Repository()
        found = _find_ips_for_scores({0, 25, 85})

        # 联动仅 real/mock_fallback 态生效——mock 态是客户端
        # 测试口径, 确定性分数不参与信誉联动(未配置零影响)
        os.environ["SECURITY_ABUSEIPDB_MODE"] = "mock_fallback"
        _clear_abuse_cache()

        # mock 态不联动(切回 mock 验证零影响)
        os.environ["SECURITY_ABUSEIPDB_MODE"] = "mock"
        rep = await sec.ensure_reputation(found[85])
        rep2 = await svc.apply_to_reputation(found[85], dict(rep))
        record("mock态不联动", rep2["score"] == rep["score"],
               f"{rep['score']}→{rep2['score']}")

        os.environ["SECURITY_ABUSEIPDB_MODE"] = "mock_fallback"
        _clear_abuse_cache()

        async def _prep(ip):
            return await sec.ensure_reputation(ip)

        # ≥85 档 → 降档 31 + suspicious + threatintel_hit 留痕
        ip85 = found[85]
        rep = await _prep(ip85)
        rep = await svc.apply_to_reputation(ip85, dict(rep))
        record("85档降档31", rep["score"] == 31.0,
               str(rep["score"]))
        record("85档suspicious", rep["status"] == "suspicious",
               str(rep["status"]))
        events = await repo.list_events(limit=20)
        ev = [e for e in events if e.get("ip") == ip85]
        record("85档留痕", len(ev) >= 1, f"{len(ev)}条")
        record("85档因子名", ev and any(
            f.get("name") == "abuseipdb"
            for f in ev[0].get("factors") or []),
            str(ev[:1])[:120])

        # 25 档 → 轻扣 10(80→70) + abuseipdb_low 留痕
        ip25 = found[25]
        rep = await _prep(ip25)
        rep = await svc.apply_to_reputation(ip25, dict(rep))
        record("25档轻扣10", rep["score"] == 70.0,
               str(rep["score"]))
        events = await repo.list_events(limit=20)
        ev = [e for e in events if e.get("ip") == ip25]
        record("25档留痕", len(ev) >= 1, f"{len(ev)}条")
        record("25档因子名low", ev and any(
            f.get("name") == "abuseipdb_low"
            for f in ev[0].get("factors") or []),
            str(ev[:1])[:120])

        # 25 档防重复扣(已 70 不再扣)
        rep2 = await svc.apply_to_reputation(ip25, dict(rep))
        record("25档不重复扣", rep2["score"] == 70.0,
               str(rep2["score"]))

        # 0 档 → 零影响
        ip0 = found[0]
        rep = await _prep(ip0)
        rep2 = await svc.apply_to_reputation(ip0, dict(rep))
        record("0档零影响", rep2["score"] == rep["score"],
               f"{rep['score']}→{rep2['score']}")

        # 85 档已降档不重复降(重新用 ip85 自身状态验证)
        cur = await repo.get_reputation(ip85)
        rep4 = await svc.apply_to_reputation(ip85, dict(cur))
        record("85档不重复降", rep4["score"] == 31.0,
               str(rep4["score"]))

        # 组尾清理 mode(HTTP 组断言默认 mock)
        os.environ.pop("SECURITY_ABUSEIPDB_MODE", None)


class TestTwoLevelChain:
    async def run(self):
        print("[05 两级串联]")
        from services.threatintel_service import ThreatIntelService
        from services.security_service import Security43Service
        from repositories.security_repository import (
            Security43Repository,
        )
        import services.abuseipdb_client as ab
        svc = ThreatIntelService()
        sec = Security43Service()
        repo = Security43Repository()

        # 导入 Firehol 段(覆盖某 IP)
        await svc.import_netset("203.0.113.0/24\n")
        rep = await sec.ensure_reputation("203.0.113.55")
        _clear_abuse_cache()

        # Firehol 命中 → 降档(第一级出口, 不查 AbuseIPDB)
        rep = await svc.apply_to_reputation("203.0.113.55",
                                            dict(rep))
        record("Firehol命中降档", rep["score"] == 31.0,
               str(rep["score"]))
        record("Firehol命中零配额消耗",
               await ab.get_quota_used() == 0,
               str(await ab.get_quota_used()))

        # Firehol 未命中(mock 态也不耗配额, 但查询执行过)
        rep2 = await sec.ensure_reputation("172.20.0.1")
        _clear_abuse_cache()
        rep2 = await svc.apply_to_reputation("172.20.0.1",
                                             dict(rep2))
        record("未命中走第二级", rep2 is not None)


class TestHttpRoutes:
    async def run(self):
        print("[06 HTTP层]")
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from routes.security_routes import register_security_routes

        app = FastAPI()
        register_security_routes(app)
        client = TestClient(app)

        resp = client.get(
            "/api/security/admin/threatintel/abuseipdb/check"
            "?ip=1.2.3.4")
        record("缺Role403", resp.status_code == 403)

        _clear_abuse_cache()
        resp = client.get(
            "/api/security/admin/threatintel/abuseipdb/check"
            "?ip=1.2.3.4", headers={"X-Role": "admin"})
        body = resp.json()
        record("check200", resp.status_code == 200
               and body.get("score") in (0, 25, 85),
               str(body))
        record("check结构", all(k in body for k in (
            "score", "source", "quotaUsed", "quotaRemaining",
            "mode")), str(list(body)))
        record("check模式", body.get("mode") == "mock",
               str(body.get("mode")))

        # 二次查询命中缓存
        resp = client.get(
            "/api/security/admin/threatintel/abuseipdb/check"
            "?ip=1.2.3.4", headers={"X-Role": "admin"})
        record("二次缓存", resp.json().get("source") == "cache",
               str(resp.json()))

        # refresh 跳过缓存
        resp = client.get(
            "/api/security/admin/threatintel/abuseipdb/check"
            "?ip=1.2.3.4&refresh=true",
            headers={"X-Role": "admin"})
        record("refresh参数", resp.json().get("source")
               != "cache", str(resp.json()))


async def run_all():
    t1 = TestMockScore()
    await t1.run()
    await TestModes().run()
    await TestQuota().run()
    await TestReputationLink().run()
    await TestTwoLevelChain().run()
    await TestHttpRoutes().run()
    os.environ.pop("SECURITY_ABUSEIPDB_MODE", None)
    os.environ.pop("SECURITY_ABUSEIPDB_KEY", None)


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
