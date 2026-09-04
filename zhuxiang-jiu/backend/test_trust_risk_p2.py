"""47号·L2/L3 信值验真风控 P2 专项测试(轻量协同分析)

运行方式:
    python test_trust_risk_p2.py

覆盖(计划 §五):
    - 互证引用解析: "trust:{id}" 约定/非法格式/非字符串
    - 互证对提取: 双向计数/完整回合 min/单向不构成对/
      自证忽略/窗口边界/时间线/非引用源忽略
    - 跨角色指纹共享: 精确 evSha 跨角色/单角色重复不算/
      语义近似(改字跨角色)/全异不命中/同角色排除
    - 协同扫描 E2E: 三角色互证环→标记/幂等(已标记跳过)/
      视图纯读零写入/suspect 不自动处罚(红线④)
    - sources 持久化: 自证剔除/拒收留痕/近窗过滤
    - HTTP 层: scan+collusion 端点/鉴权/路由顺序
"""

import asyncio
import os
import sys
import uuid
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


def _ev(base: str) -> str:
    """唯一证据(带 uuid 后缀——防指纹跨角色误共享)"""
    return f"{base}({uuid.uuid4().hex[:8]})"


async def new_profile(role: str = "person") -> int:
    from services.trust_scoring_service import (
        TrustProfileService,
    )
    suffix = uuid.uuid4().hex[:10]
    r = await TrustProfileService().create_role(
        role, f"r2-{suffix}", f"110101{suffix}4321")
    return r["trustId"]


async def _deposit(tid: int, evidence: str, *,
                   sources: list = None,
                   observed: float = 200,
                   baseline: float = 50) -> dict:
    from services.trust_radar_service import (
        TrustRadarService,
    )
    return await TrustRadarService().submit_deposit(
        tid, "L2", "ethics_evidence",
        observed=observed, peer_baseline=baseline,
        evidence=evidence, summary="志愿服务(权威源公示)",
        sources=sources if sources is not None
        else ["gov_penalty", "media"])


def _ring_evidence(tag: str) -> str:
    """互证环存证证据(每笔唯一——防同角色语义复用)"""
    return f"互证存证材料{tag}号(2026-{uuid.uuid4().hex[:6]})"


class TestAttestationParse:
    async def run(self):
        print("[01 互证引用解析]")
        from services.trust_risk_collusion_service import (
            parse_attestation_ref,
        )
        record("标准引用 trust:42",
               parse_attestation_ref("trust:42") == 42)
        record("非引用源 media",
               parse_attestation_ref("media") is None)
        record("非法数字 trust:abc",
               parse_attestation_ref("trust:abc") is None)
        record("空前缀 trust:",
               parse_attestation_ref("trust:") is None)
        record("非字符串",
               parse_attestation_ref(42) is None
               and parse_attestation_ref(None) is None)
        record("大小写敏感 TRUST:42",
               parse_attestation_ref("TRUST:42") is None)


class TestMutualPairs:
    async def run(self):
        print("[02 互证对提取]")
        from services.trust_risk_collusion_service import (
            extract_mutual_pairs,
        )
        now = datetime.now(UTC)

        def ev(eid, tid, sources, days=0):
            return {"eventId": eid, "trustId": tid,
                    "sources": sources,
                    "ts": (now - timedelta(
                        days=days)).isoformat()}

        # 双向 3 次 → mutual 3 嫌疑
        events = [ev(i, 1, ["trust:2"]) for i in range(3)] \
            + [ev(i + 10, 2, ["trust:1"]) for i in range(3)]
        r = extract_mutual_pairs(events, now=now)
        p = r["pairs"]
        record("双向3次→mutual3",
               len(p) == 1 and p[0]["mutual"] == 3
               and p[0]["aRefsB"] == 3 and p[0]["bRefsA"] == 3,
               str(p))
        record("mutual3判嫌疑",
               p[0]["suspect"] is True, str(p[0]))

        # 边界: mutual 2 → 不判
        events = [ev(i, 1, ["trust:2"]) for i in range(2)] \
            + [ev(i + 10, 2, ["trust:1"]) for i in range(2)]
        p = extract_mutual_pairs(events, now=now)["pairs"]
        record("mutual2不判(阈值边界)",
               p[0]["mutual"] == 2 and not p[0]["suspect"],
               str(p))

        # 单向不构成对
        events = [ev(i, 1, ["trust:2"]) for i in range(5)]
        r = extract_mutual_pairs(events, now=now)
        record("单向作证不构成对",
               r["pairs"] == [] and r["directedCount"]
               .get(1, {}).get(2) == 5, str(r["pairs"]))

        # 自证忽略
        r = extract_mutual_pairs(
            [ev(1, 1, ["trust:1", "trust:1"])], now=now)
        record("自证引用忽略",
               r["pairs"] == [] and not r["directedCount"],
               str(r["directedCount"]))

        # 窗口边界: 91 天前排除 / 89 天前计入
        events = [ev(1, 1, ["trust:2"], days=91),
                  ev(2, 2, ["trust:1"])]
        r = extract_mutual_pairs(events, now=now)
        record("91天前事件排除", r["pairs"] == [])
        events = [ev(1, 1, ["trust:2"], days=89),
                  ev(2, 2, ["trust:1"])]
        r = extract_mutual_pairs(events, now=now)
        record("89天前事件计入",
               len(r["pairs"]) == 1 and r["scanned"] == 2,
               str(r["pairs"]))

        # 时间线(双向事件均在, 按时间正序)
        events = [ev(1, 1, ["trust:2", "media"]),
                  ev(2, 2, ["trust:1"])]
        r = extract_mutual_pairs(events, now=now)
        tl = r["pairs"][0]["timeline"]
        record("时间线含双向事件",
               len(tl) == 2
               and {e["depositor"] for e in tl} == {1, 2},
               str(tl))
        record("非引用源不计互证",
               r["pairs"][0]["aRefsB"] == 1
               and r["pairs"][0]["bRefsA"] == 1, str(tl))

        # 多对: A-B 与 A-C 独立
        events = [ev(1, 1, ["trust:2", "trust:3"]),
                  ev(2, 2, ["trust:1"]),
                  ev(3, 3, ["trust:1"])]
        r = extract_mutual_pairs(events, now=now)
        pairs = {(p["a"], p["b"]) for p in r["pairs"]}
        record("多对独立提取",
               pairs == {(1, 2), (1, 3)}, str(pairs))


class TestSharedFingerprints:
    async def run(self):
        print("[03 跨角色指纹共享]")
        from services.trust_risk_collusion_service import (
            find_shared_fingerprints,
        )
        from services.trust_risk_detector_service import (
            fingerprint_entry, ev_sha,
        )

        e1 = _ev("社区公益帮扶活动完整公示材料清单甲")
        e2 = _ev("义务植树造林基地劳动记录证明公示乙")

        # 精确共享: 同 evSha 跨两角色
        entry = fingerprint_entry(e1)
        r = find_shared_fingerprints([
            {"trustId": 1,
             "evidenceFingerprints": [entry]},
            {"trustId": 2,
             "evidenceFingerprints": [dict(entry)]}])
        record("精确指纹跨角色共享",
               len(r["shared"]) == 1
               and r["shared"][0]["type"] == "exact"
               and r["shared"][0]["roles"] == [1, 2]
               and r["shared"][0]["similarity"] == 1.0,
               str(r["shared"]))
        record("共享计数各+1",
               r["shareCounts"] == {1: 1, 2: 1},
               str(r["shareCounts"]))

        # 单角色重复指纹不算共享
        r = find_shared_fingerprints([
            {"trustId": 1,
             "evidenceFingerprints": [entry,
                                       dict(entry)]}])
        record("单角色重复不算共享",
               r["shared"] == [], str(r["shared"]))

        # 两次精确共享 → 计数 2
        entry2 = fingerprint_entry(e2)
        r = find_shared_fingerprints([
            {"trustId": 1,
             "evidenceFingerprints": [entry, entry2]},
            {"trustId": 2,
             "evidenceFingerprints": [dict(entry),
                                       dict(entry2)]}])
        record("两次共享计数2",
               r["shareCounts"] == {1: 2, 2: 2}
               and len(r["shared"]) == 2,
               str(r["shareCounts"]))

        # 语义近似: 跨角色改字重放(>0.8)
        sa = "社区公益帮扶活动完整公示材料清单甲"
        sb = "社区公益帮扶活动完整公示材料清单乙"
        r = find_shared_fingerprints([
            {"trustId": 1,
             "evidenceFingerprints": [
                 fingerprint_entry(sa)]},
            {"trustId": 2,
             "evidenceFingerprints": [
                 fingerprint_entry(sb)]}])
        record("语义近似跨角色共享",
               len(r["shared"]) == 1
               and r["shared"][0]["type"] == "semantic"
               and r["shared"][0]["similarity"] > 0.8,
               str(r["shared"]))

        # 全异证据不命中
        r = find_shared_fingerprints([
            {"trustId": 1,
             "evidenceFingerprints": [
                 fingerprint_entry(sa)]},
            {"trustId": 2,
             "evidenceFingerprints": [
                 fingerprint_entry(
                     "完全不同的另一段证据材料内容全异")]}])
        record("全异证据不共享",
               r["shared"] == [], str(r["shared"]))

        # 同角色相似条目排除(跨角色 only)
        r = find_shared_fingerprints([
            {"trustId": 1,
             "evidenceFingerprints": [
                 fingerprint_entry(sa),
                 fingerprint_entry(sb)]}])
        record("同角色相似不跨角色",
               r["shared"] == [], str(r["shared"]))

        # 语义关闭开关
        r = find_shared_fingerprints([
            {"trustId": 1,
             "evidenceFingerprints": [
                 fingerprint_entry(sa)]},
            {"trustId": 2,
             "evidenceFingerprints": [
                 fingerprint_entry(sb)]}], semantic=False)
        record("语义开关关闭",
               r["shared"] == [], str(r["shared"]))


class TestScanService:
    async def run(self):
        print("[04 协同扫描 E2E]")
        reset_all()
        from services.trust_risk_collusion_service import (
            TrustRiskCollusionService,
        )
        from services.trust_risk_profile_service import (
            TrustRiskProfileService,
        )
        svc = TrustRiskCollusionService()
        risk_svc = TrustRiskProfileService()

        # 三角色互证环: 每角色 3 笔存证引用另两角色
        tids = []
        for _ in range(3):
            tids.append(await new_profile())
        a, b, c = tids
        for i, tid in enumerate(tids):
            others = [t for t in tids if t != tid]
            for k in range(3):
                r = await _deposit(
                    tid, _ring_evidence(f"{i}{k}"),
                    sources=[f"trust:{o}" for o in others])
                assert r["verified"], f"环存证须过验真: {r}"

        # 视图(扫描前): 纯读——suspects 列出但未标记
        v = await svc.view()
        record("视图识别三嫌疑(未标记)",
               len(v["suspects"]) == 3
               and all(not s["marked"]
                       for s in v["suspects"]),
               str([s["trustId"] for s in v["suspects"]]))
        record("视图零写入(纯读)",
               all((p or {}).get("hitCounts", {}).get(
                   "collusive_suspect") is None
                   for p in [await risk_svc.repo.get_profile(t)
                             for t in tids]),
               "画像被提前写入")
        pairs = v["mutualPairs"]
        record("三对互证各mutual3",
               len(pairs) == 3
               and all(p["mutual"] == 3
                       and p["suspect"] for p in pairs),
               str([(p["a"], p["b"], p["mutual"])
                    for p in pairs]))
        tl = pairs[0]["timeline"]
        record("时间线证据链(6条)",
               len(tl) == 6 and all(
                   "eventId" in e and "ts" in e for e in tl),
               str(len(tl)))

        # 扫描: 三角色全部标记
        r = await svc.scan()
        record("扫描标记三角色",
               sorted(r["marked"]) == sorted(tids)
               and r["skipped"] == [],
               str(r.get("marked")))
        for t in tids:
            profile = await risk_svc.repo.get_profile(t)
            hc = profile.get("hitCounts") or {}
            record(f"画像collusive_suspect({t})",
                   hc.get("collusive_suspect") == 1
                   and profile.get("riskEMA") == 0.2,
                   str(hc))

        # 幂等: 二次扫描跳过已标记
        r = await svc.scan()
        record("二次扫描幂等跳过",
               r["marked"] == []
               and sorted(r["skipped"]) == sorted(tids),
               str(r.get("skipped")))
        profile = await risk_svc.repo.get_profile(a)
        record("计数不随扫描累积",
               (profile.get("hitCounts") or {})
               .get("collusive_suspect") == 1,
               str(profile.get("hitCounts")))

        # 扫描后视图: marked 状态反映
        v = await svc.view()
        record("扫描后视图marked",
               all(s["marked"] for s in v["suspects"]))

        # 红线④: suspect 不自动处罚——新存证 delta 无折损
        r = await _deposit(a, _ev("嫌疑角色后续正常存证材料"))
        record("suspect不自动处罚(delta无折损)",
               r["verified"] and r["delta"] == 14.5
               and not r["semanticReuse"]["hit"],
               f"delta={r.get('delta')}")
        # 嫌疑标记不改变信值分本身(只沉淀画像)
        record("信值分不受标记影响",
               r.get("score") is not None, str(r.get("score")))


class TestFingerPrintScan:
    async def run(self):
        print("[05 共享指纹扫描 E2E]")
        reset_all()
        from services.trust_risk_collusion_service import (
            TrustRiskCollusionService,
        )
        from services.trust_risk_profile_service import (
            TrustRiskProfileService,
        )
        svc = TrustRiskCollusionService()
        risk_svc = TrustRiskProfileService()

        # 精确共享 ×2: 两角色互相使用相同证据
        d, e = await new_profile(), await new_profile()
        for tag in ("甲", "乙"):
            shared = _ev(f"团伙共享证据材料{tag}2026公示")
            await _deposit(d, shared)
            await _deposit(e, shared)
        v = await svc.view()
        det = {s["trustId"]: s for s in v["suspects"]}
        record("两次精确共享→嫌疑",
               d in det and e in det
               and det[d]["shareCount"] == 2,
               str(v["totals"]))
        r = await svc.scan()
        record("指纹共享扫描标记",
               sorted(r["marked"]) == sorted([d, e]),
               str(r.get("marked")))

        # 语义共享: 跨角色改字重放 ×2
        f_, g = await new_profile(), await new_profile()
        for tag in ("丙", "丁"):
            base = f"跨角色语义近似证据材料{tag}2026版公示"
            await _deposit(f_, f"{base}甲")
            await _deposit(g, f"{base}乙")
        r = await svc.scan()
        record("两次语义共享→嫌疑",
               f_ in r["marked"] and g in r["marked"],
               str(r.get("marked")))
        v = await svc.view()
        det = {s["trustId"]: s for s in v["suspects"]}
        record("语义共享类型呈现",
               any(s["type"] == "semantic"
                   for s in det[f_]["sharedFingerprints"]),
               str(det.get(f_, {}).get("sharedFingerprints")))

        # 正常独立角色: 零共享零嫌疑
        h = await new_profile()
        await _deposit(h, _ev("独立正常存证证据材料2026"))
        v = await svc.view()
        record("独立角色零嫌疑",
               h not in {s["trustId"]
                         for s in v["suspects"]},
               str(v["totals"]))


class TestSourcesPersistence:
    async def run(self):
        print("[06 sources 持久化]")
        reset_all()
        from repositories.trust_value_repository import (
            TrustValue45Repository,
        )
        repo = TrustValue45Repository()

        # 自证剔除: 含自身引用 → 剔除后单源拒收
        tid = await new_profile()
        r = await _deposit(
            tid, _ev("自证剔除测试证据材料2026"),
            sources=[f"trust:{tid}", "media"])
        record("自证剔除后单源拒收",
               r["verified"] is False,
               str(r.get("verified")))
        events = await repo.list_events_by_trust(tid)
        ev = [e for e in events
              if e.get("source") == "deposit_rejected"]
        record("拒收事件留痕sources(已剔自证)",
               ev and ev[0].get("sources") == ["media"],
               str(ev and ev[0].get("sources")))

        # 正常 sources 持久化
        tid2 = await new_profile()
        await _deposit(tid2, _ev("正常存证sources持久化2026"),
                      sources=["gov_penalty", "media"])
        events = await repo.list_events_by_trust(tid2)
        ev = [e for e in events
              if e.get("source") == "deposit"]
        record("存证事件持久化sources",
               ev and ev[0].get("sources")
               == ["gov_penalty", "media"],
               str(ev and ev[0].get("sources")))

        # list_deposit_events: 只取近窗 deposit 事件
        tid3 = await new_profile()
        await _deposit(tid3, _ev("近窗存证事件过滤2026"))
        old_ts = (datetime.now(UTC)
                  - timedelta(days=91)).isoformat()
        await repo.save_event({
            "eventId": await repo.next_event_id(),
            "trustId": tid3, "layer": "L2",
            "factor": "ethics_evidence", "delta": 5,
            "severity": "general", "source": "deposit",
            "sources": ["gov_penalty"],
            "summary": "[存证] 旧事件", "ts": old_ts})
        window = await repo.list_deposit_events(days=90)
        record("近窗过滤(91天前排除)",
               any(e.get("trustId") == tid3
                   for e in window)
               and all(e.get("ts") >=
                       (datetime.now(UTC) - timedelta(
                           days=90)).isoformat()
                       for e in window),
               str(len(window)))
        ids = [e.get("trustId") for e in window]
        old_kept = any(
            e.get("summary") == "[存证] 旧事件"
            for e in window)
        record("窗口外事件不返回", not old_kept, str(old_kept))


class TestHttp:
    async def run(self):
        print("[07 HTTP 层]")
        reset_all()
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from routes.trust_risk_routes import (
            register_trust_risk_routes,
        )
        from routes.trust_value_routes import (
            register_trust_value_routes,
        )
        app = FastAPI()
        register_trust_value_routes(app)
        register_trust_risk_routes(app)
        client = TestClient(app)
        admin = {"X-Role": "admin"}

        # 三角色互证环(HTTP 存证)
        tids = []
        for i in range(3):
            resp = client.post("/api/trust/roles", json={
                "role": "person", "name": f"r2-http-{i}",
                "idNumber": f"110101r2http{i}{uuid.uuid4().hex[:6]}"})
            tids.append(resp.json().get("trustId"))
        for i, tid in enumerate(tids):
            others = [t for t in tids if t != tid]
            for k in range(3):
                resp = client.post("/api/trust/deposits", json={
                    "trustId": tid, "layer": "L2",
                    "factor": "ethics_evidence",
                    "observed": 200, "peerBaseline": 50,
                    "evidence": _ring_evidence(f"h{i}{k}"),
                    "summary": "志愿服务(权威源公示)",
                    "sources": [f"trust:{o}" for o in others]})
                assert resp.status_code == 200

        # 鉴权
        resp = client.post("/api/trust/risk/collusion/scan")
        record("scan缺Role403",
               resp.status_code == 403, str(resp.status_code))
        resp = client.get("/api/trust/risk/collusion")
        record("视图缺Role403",
               resp.status_code == 403, str(resp.status_code))

        # 扫描标记
        resp = client.post("/api/trust/risk/collusion/scan",
                           headers=admin)
        body = resp.json()
        record("HTTP协同扫描标记",
               resp.status_code == 200
               and body.get("success") is True
               and sorted(body.get("marked") or [])
               == sorted(tids),
               str(body.get("marked")))

        # 团伙视图
        resp = client.get("/api/trust/risk/collusion",
                          headers=admin)
        body = resp.json()
        record("HTTP团伙视图",
               resp.status_code == 200
               and body.get("totals", {}).get("suspects") == 3
               and len(body.get("mutualPairs") or []) == 3
               and all(s.get("marked")
                       for s in body.get("suspects")),
               str(body.get("totals")))
        record("视图含证据链时间线",
               all(len(p.get("timeline") or []) == 6
                   for p in body.get("mutualPairs")),
               str([len(p.get("timeline") or [])
                    for p in body.get("mutualPairs")]))

        # 画像计数(路由顺序: /collusion 不被 /{trust_id} 抢匹配)
        resp = client.get(f"/api/trust/risk/{tids[0]}",
                          headers=admin)
        record("画像collusive_suspect计数",
               resp.status_code == 200
               and (resp.json().get("hitCounts") or {})
               .get("collusive_suspect") == 1,
               str(resp.json().get("hitCounts")))

        # 二次扫描幂等
        resp = client.post("/api/trust/risk/collusion/scan",
                           headers=admin)
        record("HTTP二次扫描幂等",
               resp.json().get("marked") == []
               and sorted(resp.json().get("skipped"))
               == sorted(tids),
               str(resp.json().get("skipped")))


async def run_all():
    await TestAttestationParse().run()
    await TestMutualPairs().run()
    await TestSharedFingerprints().run()
    await TestScanService().run()
    await TestFingerPrintScan().run()
    await TestSourcesPersistence().run()
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
