"""43号·P5-1 D5 强制联动专项测试

运行方式:
    python test_security_p5_1.py

覆盖(计划 §二):
    - 开关与边界区解析: 默认 off / on / band 解析(合法/非法回退)
    - off 零影响: D5 命中行为与 P3-4 完全一致(无 d5_enforce 因子)
    - on 边界区升档: D5 命中+威胁分∈[25,50) → 至少 challenge
      + d5_enforce 因子留痕
    - 高分区不越级: D5 命中+威胁分≥50 → 无升档无因子
    - 低分区不因 D5 单独处置: <25 的 block 来自评分器非 D5
    - 与硬规则叠加: 注入特征 block 不因 D5 降档
    - 通行证豁免联动: D5 升档的 challenge 仍可被通行证豁免
    - 观测端点: d5Enforce 字段(off/on 实况+边界区)
    - HTTP 层: /admin/reports/d5 结构
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


async def _make_d5_hit(ip: str, member_id: int, hour: int,
                       rep_score: float,
                       rate_limit: str = "1",
                       path: str = "/api/admin/dashboard",
                       ua: str = "Mozilla/5.0") -> dict:
    """制造一次 D5 命中请求(登录会话 + 直奔 admin 模块)

    威胁分组成: rep×0.2 + rate×0.2 + payload×0.25 +
                path×0.1 + identity(D5=20)×0.15 + 时段×0.1
    rep=35 + 频次打满 + hour∈[0,5] ≈ 49 → 挑战边界区。
    频次预热走计数器直调(不经网关——常规流量请求会把 browsing
    模块记入会话序列, 破坏 D5 跳步检测的"无常规浏览"条件)。
    """
    os.environ["SECURITY_RATE_LIMIT"] = rate_limit
    from repositories.security_repository import (
        Security43Repository, reputation_status,
    )
    repo = Security43Repository()
    await repo.save_reputation({
        "ip": ip, "score": rep_score,
        "status": reputation_status(rep_score),
        "requestCount": 0, "attackCount": 0, "recoverCount": 0,
        "pinned": False, "lastPenaltyAt": None,
    })
    await repo.start_session_seq(member_id)   # 登录开启会话
    await repo.count_request(f"ip:{ip}", 60)   # 频次预热(计数器直调)
    # D5 命中请求: 登录后直奔 admin 模块(序列 [admin, __login__])
    return await _svc().process_request(
        ip, method="GET", path=path, ua=ua,
        member_id=member_id, hour=hour)


def _svc():
    from services.security_service import Security43Service
    return Security43Service()


def _factors(result: dict) -> list[dict]:
    return ((result.get("scoring") or {}).get("factors")) or []


class TestSwitchBand:
    def run(self):
        print("[01 开关与边界区]")
        from services.sequence_service import (
            d5_enforce_on, d5_enforce_band,
        )
        os.environ.pop("SECURITY_D5_ENFORCE", None)
        record("默认off", d5_enforce_on() is False)
        os.environ["SECURITY_D5_ENFORCE"] = "on"
        record("on生效", d5_enforce_on() is True)
        os.environ["SECURITY_D5_ENFORCE"] = "off"
        record("off生效", d5_enforce_on() is False)

        os.environ.pop("SECURITY_D5_ENFORCE_BAND", None)
        record("默认边界区25-50", d5_enforce_band() == (25.0, 50.0))
        os.environ["SECURITY_D5_ENFORCE_BAND"] = "50-70"
        record("自定义边界区", d5_enforce_band() == (50.0, 70.0))
        os.environ["SECURITY_D5_ENFORCE_BAND"] = "bad"
        record("非法回退默认", d5_enforce_band() == (25.0, 50.0))
        os.environ["SECURITY_D5_ENFORCE_BAND"] = "90-10"
        record("倒序回退默认", d5_enforce_band() == (25.0, 50.0))
        os.environ.pop("SECURITY_D5_ENFORCE_BAND", None)


class TestOffZeroImpact:
    async def run(self):
        print("[02 off 零影响]")
        os.environ.pop("SECURITY_D5_ENFORCE", None)
        r = await _make_d5_hit("10.1.1.10", 910, hour=3, rep_score=35)
        score = (r.get("scoring") or {}).get("score")
        record("边界区命中(分∈[25,50))",
               score is not None and 25 <= score < 50, str(score))
        record("off无d5_enforce因子", all(
            f.get("name") != "d5_enforce" for f in _factors(r)))
        # off 时处置与 P3-4 一致: 边界区评分器本身给 challenge
        record("off处置=评分器原档",
               (r.get("scoring") or {}).get("action") == "challenge",
               str((r.get("scoring") or {}).get("action")))


class TestOnBandUpgrade:
    async def run(self):
        print("[03 on 边界区升档]")
        os.environ["SECURITY_D5_ENFORCE"] = "on"
        r = await _make_d5_hit("10.1.1.11", 911, hour=3, rep_score=35)
        score = (r.get("scoring") or {}).get("score")
        record("边界区分值", score is not None and 25 <= score < 50,
               str(score))
        record("升档至少challenge",
               (r.get("scoring") or {}).get("action") in
               ("challenge", "block"),
               str((r.get("scoring") or {}).get("action")))
        enf = [f for f in _factors(r) if f.get("name") == "d5_enforce"]
        record("d5_enforce因子留痕", len(enf) == 1, str(enf)[:100])
        record("因子含边界区详情", enf and "边界区" in str(
            enf[0].get("detail")) and "challenge" in str(
            enf[0].get("detail")), str(enf)[:120])
        record("因子含D5详情", enf and "直奔敏感模块" in str(
            enf[0].get("detail")), str(enf)[:120])
        # 事件留痕(action=challenge 非 allow → 入事件流水)
        from repositories.security_repository import Security43Repository
        events = await Security43Repository().list_events(limit=20)
        ev = [e for e in events if e.get("ip") == "10.1.1.11"]
        record("事件流水留痕", len(ev) >= 1, f"{len(ev)}条")
        record("事件因子含d5_enforce", ev and any(
            f.get("name") == "d5_enforce"
            for f in (ev[0].get("factors") or [])),
            str(ev[:1])[:120])


class TestHighScoreNoUpgrade:
    async def run(self):
        print("[04 高分区不越级]")
        os.environ["SECURITY_D5_ENFORCE"] = "on"
        # rep 80(正常)+频次正常+时段14 → 高分; D5 命中仅降 identity
        r = await _make_d5_hit("10.1.1.12", 912, hour=14,
                               rep_score=80.0, rate_limit="999")
        score = (r.get("scoring") or {}).get("score")
        record("高分区(≥50)", score is not None and score >= 50,
               str(score))
        record("高分区不升档", (r.get("scoring") or {}).get("action")
               in ("allow", "throttle"),
               str((r.get("scoring") or {}).get("action")))
        record("高分区无d5因子", all(
            f.get("name") != "d5_enforce" for f in _factors(r)))


class TestLowScoreNoD5Block:
    async def run(self):
        print("[05 硬规则叠加+低分区]")
        os.environ["SECURITY_D5_ENFORCE"] = "on"
        # SQLi(query) + 扫描器 UA → 两类特征 → payload=0 → 硬规则 block
        inj = {"path": "/api/admin/dashboard",
               "ua": "sqlmap/1.2 Mozilla"}

        # Case A: 分∈边界区 + 硬规则 block → block 不因 D5 降档,
        #         d5_enforce 因子仍留痕(叠加口径)
        r = await _make_d5_hit("10.1.1.13", 913, hour=3,
                               rep_score=60.0,
                               path=inj["path"] + "?q=' OR 1=1--",
                               ua=inj["ua"])
        score = (r.get("scoring") or {}).get("score")
        record("A-叠加分∈边界区", score is not None
               and 25 <= score < 50, str(score))
        record("A-硬规则block不降档",
               (r.get("scoring") or {}).get("action") == "block",
               str((r.get("scoring") or {}).get("action")))
        record("A-叠加因子留痕", any(
            f.get("name") == "d5_enforce" for f in _factors(r)),
            str(_factors(r))[:120])

        # Case B: 分<25(低分区) → block 来自评分器/硬规则非 D5,
        #         无 d5_enforce 因子(不在边界区)
        r = await _make_d5_hit("10.1.1.15", 915, hour=3,
                               rep_score=35.0,
                               path=inj["path"] + "?q=' OR 1=1--",
                               ua=inj["ua"])
        score = (r.get("scoring") or {}).get("score")
        record("B-低分(<25)", score is not None and score < 25,
               str(score))
        record("B-block非D5单独处置",
               (r.get("scoring") or {}).get("action") == "block",
               str((r.get("scoring") or {}).get("action")))
        record("B-低分区无d5因子", all(
            f.get("name") != "d5_enforce" for f in _factors(r)))


class TestPassExempt:
    async def run(self):
        print("[06 通行证豁免联动]")
        os.environ["SECURITY_D5_ENFORCE"] = "on"
        from repositories.security_repository import (
            Security43Repository,
        )
        repo = Security43Repository()
        # 挑战通行证: TTL 内豁免挑战档(D5 升档的 challenge 同样可豁免)
        await repo.grant_challenge_pass("10.1.1.14", ttl=900)
        r = await _make_d5_hit("10.1.1.14", 914, hour=3, rep_score=35.0)
        score = (r.get("scoring") or {}).get("score")
        record("边界区命中", score is not None and 25 <= score < 50,
               str(score))
        record("D5升档challenge", (r.get("scoring") or {}).get("action")
               == "challenge",
               str((r.get("scoring") or {}).get("action")))
        record("通行证豁免放行", r.get("action") == "allow",
               str(r.get("action")))
        record("豁免留痕challenge_exempt", (r.get("event") or {}).get(
            "action") == "challenge_exempt",
            str(r.get("event"))[:100])


class TestObservation:
    async def run(self):
        print("[07 观测端点]")
        from services.soc_report_service import SocReportService
        svc = SocReportService()

        os.environ["SECURITY_D5_ENFORCE"] = "off"
        r = await svc.d5_observation()
        record("d5Enforce字段", "d5Enforce" in r, str(list(r)))
        record("off实况", r["d5Enforce"].get("active") is False)
        record("边界区口径", r["d5Enforce"].get("band") == "25-50",
               str(r["d5Enforce"]))

        os.environ["SECURITY_D5_ENFORCE"] = "on"
        r = await svc.d5_observation()
        record("on实况", r["d5Enforce"].get("active") is True)
        record("口径完整", set(r["d5Enforce"]) >= {
            "active", "band", "note"}, str(r["d5Enforce"]))
        os.environ["SECURITY_D5_ENFORCE"] = "off"


class TestHttpRoutes:
    async def run(self):
        print("[08 HTTP层]")
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from routes.security_routes import register_security_routes

        app = FastAPI()
        register_security_routes(app)
        client = TestClient(app)

        resp = client.get("/api/security/admin/reports/d5")
        record("缺Role403", resp.status_code == 403)

        resp = client.get("/api/security/admin/reports/d5",
                          headers={"X-Role": "admin"})
        body = resp.json()
        record("d5端点200", resp.status_code == 200
               and body.get("success") is True, str(resp.status_code))
        record("d5Enforce结构", isinstance(body.get("d5Enforce"), dict)
               and "active" in body["d5Enforce"]
               and "band" in body["d5Enforce"],
               str(body.get("d5Enforce")))


async def run_all():
    TestSwitchBand().run()
    await TestOffZeroImpact().run()
    await TestOnBandUpgrade().run()
    await TestHighScoreNoUpgrade().run()
    await TestLowScoreNoD5Block().run()
    await TestPassExempt().run()
    await TestObservation().run()
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
