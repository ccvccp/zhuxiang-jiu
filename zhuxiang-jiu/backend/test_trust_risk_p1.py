"""47号·L2/L3 信值验真风控 P1 专项测试
(语义近似指纹 + 价值分布)

运行方式:
    python test_trust_risk_p1.py

覆盖(计划 §四):
    - 3-gram 数学: 切分/短文本/空集/Jaccard 恒等式
    - 语义指纹: 精确重放(1.0)/改字重放(>0.8)/阈值边界
      (=0.8 严格不命中)/全异不命中/桶滚动截断
    - 小额高频: 命中构造(基线外参照)/次数不足/无基线
      不判/单笔超阈值/窗口边界
    - 价值错配: 高申报低证据命中/v1 组件 None 零影响/
      组件高分不命中/申报=P90 严格不命中
    - 存证接入 E2E: 指纹沉淀/复用折损(×0.3)/精确重放/
      拒收不沉淀/错配折损(×0.5)/复用+错配叠乘(×0.15)
    - 扫描端点: 命中沉淀/幂等(二次扫描不重复计数)/
      增量重判/正常分布零沉淀/未建档 404
    - HTTP 层: 存证改字重放回包/scan 鉴权/404/画像计数
"""

import asyncio
import os
import sys
import uuid

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
    """唯一证据(带 uuid 后缀——防指纹重放误命中)"""
    return f"{base}({uuid.uuid4().hex[:8]})"


async def new_profile(role: str = "person") -> int:
    from services.trust_scoring_service import (
        TrustProfileService,
    )
    suffix = uuid.uuid4().hex[:10]
    r = await TrustProfileService().create_role(
        role, f"r1-{suffix}", f"110101{suffix}4321")
    return r["trustId"]


async def _deposit(tid: int, evidence: str, *,
                   observed: float = 200,
                   baseline: float = 50,
                   verify_mode: str = "v1",
                   factor: str = "contribution_net",
                   layer: str = "L3") -> dict:
    from services.trust_radar_service import (
        TrustRadarService,
    )
    return await TrustRadarService().submit_deposit(
        tid, layer, factor, observed=observed,
        peer_baseline=baseline, evidence=evidence,
        summary="志愿服务(权威源公示)",
        sources=["gov_penalty", "media"],
        verify_mode=verify_mode)


class TestGramsMath:
    async def run(self):
        print("[01 3-gram 数学]")
        from services.trust_risk_detector_service import (
            char_grams, jaccard, semantic_similarity,
        )
        record("标准3-gram切分",
               char_grams("abcd") == {"abc", "bcd"},
               str(char_grams("abcd")))
        record("短文本整段成集",
               char_grams("ab") == {"ab"}
               and char_grams("a") == {"a"},
               str(char_grams("ab")))
        record("空文本空集", char_grams("") == set()
               and char_grams(None) == set(),
               str(char_grams("")))
        record("Jaccard恒等=1",
               jaccard({"a", "b"}, {"a", "b"}) == 1.0)
        record("Jaccard交集空=0",
               jaccard({"a"}, {"b"}) == 0.0)
        record("Jaccard空集=0",
               jaccard(set(), {"a"}) == 0.0
               and jaccard({"a"}, set()) == 0.0)
        record("Jaccard已知值2/3",
               jaccard({"abc", "bcd"},
                       {"abc", "bcd", "cde"}) == 0.6667,
               str(jaccard({"abc", "bcd"},
                           {"abc", "bcd", "cde"})))
        record("语义相似度对称",
               semantic_similarity("甲乙丙丁", "甲乙丙戊")
               == semantic_similarity("甲乙丙戊", "甲乙丙丁"))


class TestSemanticReuse:
    async def run(self):
        print("[02 语义指纹(改字重放识别)]")
        from services.trust_risk_detector_service import (
            check_semantic_reuse, fingerprint_entry,
            char_grams, ev_sha,
            FINGERPRINT_BUCKET_MAX,
        )
        u = uuid.uuid4().hex[:8]
        e1 = f"志愿服务官方公示记录材料(编号ZY2026088{u})"
        e2 = f"志愿服务官方公示记录材料(编号ZY2026089{u})"

        # 精确重放(SHA 命中 → similarity 1.0)
        r = check_semantic_reuse(
            e1, [fingerprint_entry(e1)])
        record("精确重放命中(1.0)",
               r["hit"] and r["similarity"] == 1.0
               and "精确重放" in r["reason"], str(r))

        # 改字重放(1 字之差 → >0.8 命中)
        r = check_semantic_reuse(
            e2, [fingerprint_entry(e1)])
        record("改字重放命中(>0.8)",
               r["hit"] and r["similarity"] > 0.8,
               str(r))

        # 阈值边界: 相似度恰为 0.8 → 严格大于才命中
        bucket = [{"grams": sorted(char_grams("abcdef")),
                   "evSha": ev_sha("abcdef"), "ts": ""}]
        r = check_semantic_reuse("abcdefg", bucket)
        record("阈值边界(=0.8不命中)",
               not r["hit"] and r["similarity"] == 0.8,
               str(r))

        # 全异证据不命中
        r = check_semantic_reuse(
            "社区公益帮扶活动完整公示材料", bucket)
        record("全异证据不命中",
               not r["hit"] and r["similarity"] < 0.3,
               str(r))

        # 空桶不命中
        r = check_semantic_reuse(e1, [])
        record("空桶不命中", not r["hit"])

        # fingerprint_entry 结构
        entry = fingerprint_entry(e1)
        record("指纹条目结构(sha+grams)",
               entry["evSha"] == ev_sha(e1)
               and set(entry["grams"])
               <= char_grams(e1) and entry["ts"],
               str(entry)[:70])

        # 长证据 grams 截断 60(防膨胀)
        long_ev = "志愿" * 60 + f"编号{u}"
        record("长证据grams截断60",
               len(fingerprint_entry(long_ev)["grams"])
               <= 60)

        # 指纹桶滚动截断(105 条存证 → 100)
        from services.trust_risk_profile_service import (
            TrustRiskProfileService,
        )
        reset_all()
        tid = await new_profile()
        risk_svc = TrustRiskProfileService()
        for _ in range(FINGERPRINT_BUCKET_MAX + 5):
            await risk_svc.record_risk_event(
                tid, "deposit", signals=[],
                evidence=_ev("指纹桶滚动截断测试证据材料"))
        profile = await risk_svc.repo.get_profile(tid)
        record("指纹桶滚动截断100",
               len(profile.get("evidenceFingerprints")
                   or []) == FINGERPRINT_BUCKET_MAX,
               str(len(profile.get(
                   "evidenceFingerprints") or [])))


class TestValueDistribution:
    async def run(self):
        print("[03 价值分布检测器]")
        from datetime import UTC, datetime, timedelta
        from services.trust_risk_detector_service import (
            detect_small_high_frequency,
            detect_value_mismatch,
        )
        now = datetime.now(UTC)

        def _dep(net, days):
            return {"net": net,
                    "ts": (now - timedelta(
                        days=days)).isoformat()}

        # 小额高频命中: 近30日 6 次小额 + 窗口外大额基线
        deps = [_dep(5, d) for d in range(1, 7)] \
            + [_dep(100, 40), _dep(100, 45)]
        r = detect_small_high_frequency(deps, now=now)
        record("小额高频命中构造",
               r["hit"] and r["count"] == 6
               and r["median"] == 100.0
               and r["threshold"] == 50.0, str(r))

        # 次数不足(5 次)
        deps = [_dep(5, d) for d in range(1, 6)] \
            + [_dep(100, 40)]
        r = detect_small_high_frequency(deps, now=now)
        record("次数不足不判(<6)",
               not r["hit"] and r["count"] == 5,
               str(r))

        # 无窗口外基线不判(新角色防误伤)
        deps = [_dep(5, d) for d in range(1, 7)]
        r = detect_small_high_frequency(deps, now=now)
        record("无基线不判",
               not r["hit"] and "无窗口外基线" in r["reason"],
               str(r))

        # 单笔超阈值(混入一笔大额) → 不命中
        deps = [_dep(5, d) for d in range(1, 6)] \
            + [_dep(60, 6), _dep(100, 40)]
        r = detect_small_high_frequency(deps, now=now)
        record("单笔超阈值不命中",
               not r["hit"], str(r))

        # 窗口边界: 基线事件恰好 31 天前 → 不计入频次窗口
        deps = [_dep(5, d) for d in range(1, 7)] \
            + [_dep(100, 31)]
        r = detect_small_high_frequency(deps, now=now)
        record("窗口边界(31天前为基线)",
               r["hit"] and r["count"] == 6
               and r["median"] == 100.0, str(r))

        # 价值错配命中: 高申报 + 组件低分
        r = detect_value_mismatch(500, 50, 0.5)
        record("价值错配命中",
               r["hit"] and r["componentScore"] == 0.5
               and "错配" in r["reason"], str(r))

        # v1(组件 None)零影响——视为无证据信号
        r = detect_value_mismatch(500, 50, None)
        record("v1组件None零影响",
               not r["hit"] and r["componentScore"] == 1.0,
               str(r))

        # 组件高分不命中
        r = detect_value_mismatch(500, 50, 0.9)
        record("组件高分不命中",
               not r["hit"], str(r))

        # 申报=P90 严格大于才命中
        r = detect_value_mismatch(50, 50, 0.5)
        record("申报=P90不命中",
               not r["hit"], str(r))

        # 组件=0.7 严格小于才命中
        r = detect_value_mismatch(500, 50, 0.7)
        record("组件=0.7不命中",
               not r["hit"], str(r))


class TestDepositIntegration:
    async def run(self):
        print("[04 存证接入 E2E]")
        reset_all()
        from services.trust_risk_profile_service import (
            TrustRiskProfileService,
        )
        risk_svc = TrustRiskProfileService()
        u = uuid.uuid4().hex[:8]
        e1 = f"志愿服务官方公示记录材料(编号ZY2026088{u})"
        e2 = f"志愿服务官方公示记录材料(编号ZY2026089{u})"

        # 首次存证: 无复用 + 指纹沉淀入桶
        tid = await new_profile()
        r = await _deposit(tid, e1)
        profile = await risk_svc.repo.get_profile(tid)
        bucket = (profile or {}).get(
            "evidenceFingerprints") or []
        record("首次存证无复用",
               r["verified"] and not r["semanticReuse"]["hit"]
               and r["delta"] == 14.5, str(r.get("delta")))
        record("首次存证指纹沉淀",
               len(bucket) == 1, str(len(bucket)))

        # 改字重放: delta ×0.3 + 画像沉淀
        r2 = await _deposit(tid, e2)
        record("改字重放折损(×0.3)",
               r2["semanticReuse"]["hit"]
               and r2["delta"] == round(14.5 * 0.3, 1),
               str(r2.get("delta")))
        profile = await risk_svc.repo.get_profile(tid)
        # EMA=0.16: deposit 回流 1.0(ema 0.2) 后
        # deposit_merge 零风险回流稀释(0.2×0.8)
        record("复用沉淀画像",
               (profile.get("hitCounts") or {})
               .get("semantic_reuse") == 1
               and profile.get("riskEMA") == 0.16,
               str(profile.get("hitCounts")))

        # 精确重放(同文 SHA 命中 → 1.0)
        r3 = await _deposit(tid, e1)
        record("精确重放E2E(1.0)",
               r3["semanticReuse"]["hit"]
               and r3["semanticReuse"]["similarity"] == 1.0
               and "精确重放" in r3["semanticReuse"]
               ["reason"], str(r3["semanticReuse"]))

        # 拒收存证: 不沉淀指纹不建画像
        tid2 = await new_profile()
        from services.trust_radar_service import (
            TrustRadarService,
        )
        r = await TrustRadarService().submit_deposit(
            tid2, "L3", "contribution_net",
            observed=200, peer_baseline=50,
            evidence=_ev("志愿服务现场公示材料证明"),
            summary="志愿服务(权威源公示)",
            sources=["media"])   # 单非权威源 → 拒收
        profile2 = await risk_svc.repo.get_profile(tid2)
        record("拒收存证不沉淀",
               r["verified"] is False
               and profile2 is None,
               str(r.get("verified")))

        # 价值错配 E2E: v2 高申报低证据 → delta ×0.5
        # (证据须无数字——含数字则内容鉴别 1.0 不触发错配)
        tid3 = await new_profile()
        ev = "社区公益帮扶活动完整公示材料清单公示存档备查专用材料"
        r = await _deposit(tid3, ev, observed=500,
                           verify_mode="v2")
        record("价值错配E2E(×0.5)",
               r["verified"] and r["valueMismatch"]["hit"]
               and r["delta"] == 15.0,
               f"delta={r.get('delta')}")
        profile3 = await risk_svc.repo.get_profile(tid3)
        record("错配沉淀画像",
               (profile3.get("hitCounts") or {})
               .get("value_anomaly") == 1
               and profile3.get("riskEMA") == 0.16,
               str(profile3.get("hitCounts")))

        # v1 高申报: 组件 None 零影响
        tid4 = await new_profile()
        r = await _deposit(tid4, _ev("社区公益帮扶活动公示材料"),
                           observed=500)
        record("v1高申报零影响",
               r["verified"] and not r["valueMismatch"]["hit"]
               and r["delta"] == 30.0,
               f"delta={r.get('delta')}")

        # 净贡献为零: 不触发语义/价值检测
        tid5 = await new_profile()
        r = await _deposit(tid5, _ev("社区公益帮扶活动公示材料"),
                           observed=50)
        record("净贡献为零不检测",
               r["verified"] and r["delta"] == 0
               and not r["semanticReuse"]["hit"]
               and not r["valueMismatch"]["hit"],
               f"delta={r.get('delta')}")

        # 复用+错配叠乘: v2 改字重放 ×0.3 再错配 ×0.5
        # (证据无数字保内容鉴别 0.5; 末字之差保语义相似 >0.8)
        tid6 = await new_profile()
        ev_a = ("社区公益帮扶活动完整公示材料志愿服务时"
                "长达标证明公示版材料清单公示存档备查甲")
        ev_b = ("社区公益帮扶活动完整公示材料志愿服务时"
                "长达标证明公示版材料清单公示存档备查乙")
        await _deposit(tid6, ev_a, observed=500,
                       verify_mode="v2")
        r = await _deposit(tid6, ev_b, observed=500,
                           verify_mode="v2")
        record("复用+错配叠乘(×0.15)",
               r["semanticReuse"]["hit"]
               and r["valueMismatch"]["hit"]
               and r["delta"] == round(30 * 0.15, 1),
               f"delta={r.get('delta')}")
        profile6 = await risk_svc.repo.get_profile(tid6)
        record("叠乘命中画像沉淀",
               (profile6.get("hitCounts") or {})
               .get("semantic_reuse") == 1
               and (profile6.get("hitCounts") or {})
               .get("value_anomaly") == 2,
               str(profile6.get("hitCounts")))


class TestScanService:
    async def run(self):
        print("[05 扫描服务]")
        reset_all()
        from datetime import UTC, datetime, timedelta
        from repositories.trust_value_repository import (
            TrustValue45Repository,
        )
        from services.trust_risk_detector_service import (
            TrustRiskDetectorService,
        )
        repo = TrustValue45Repository()
        svc = TrustRiskDetectorService()

        async def _seed_event(tid, delta, days,
                              factor="contribution_net"):
            await repo.save_event({
                "eventId": await repo.next_event_id(),
                "trustId": tid, "layer": "L3",
                "factor": factor, "delta": delta,
                "severity": "general", "source": "deposit",
                "summary": f"[存证] 扫描测试(delta={delta})",
                "ts": (datetime.now(UTC) - timedelta(
                    days=days)).isoformat()})

        # 小额高频: 6 笔小额(3 日内) + 2 笔大额基线(40 日前)
        tid = await new_profile()
        for d in (1, 2, 3):
            await _seed_event(tid, 5, d)
        for d in (10, 11, 12):
            await _seed_event(tid, 5, d)
        await _seed_event(tid, 100, 40)
        await _seed_event(tid, 100, 45)

        r = await svc.scan(tid)
        from services.trust_risk_profile_service import (
            TrustRiskProfileService,
        )
        profile = await TrustRiskProfileService(
        ).repo.get_profile(tid)
        record("scan小额高频命中沉淀",
               r["success"] and r["valueAnomaly"]["hit"]
               and (profile.get("hitCounts") or {})
               .get("value_anomaly") == 1
               and profile.get("riskEMA") == 0.2,
               str(r.get("valueAnomaly")))

        # 幂等: 二次扫描不重复计数
        r2 = await svc.scan(tid)
        profile = await TrustRiskProfileService(
        ).repo.get_profile(tid)
        record("scan幂等(不重复计数)",
               r2["valueAnomaly"]["hit"]
               and (profile.get("hitCounts") or {})
               .get("value_anomaly") == 1
               and profile.get("riskEMA") == 0.2,
               str((profile.get("hitCounts") or {})
                   .get("value_anomaly")))

        # 增量: 新增第 7 笔小额 → 状态变化重判沉淀
        await _seed_event(tid, 5, 1)
        r3 = await svc.scan(tid)
        profile = await TrustRiskProfileService(
        ).repo.get_profile(tid)
        record("scan增量重判(新存证)",
               r3["valueAnomaly"]["hit"]
               and (profile.get("hitCounts") or {})
               .get("value_anomaly") == 2,
               str((profile.get("hitCounts") or {})
                   .get("value_anomaly")))

        # 正常分布: 无命中零沉淀
        tid2 = await new_profile()
        for d in (1, 5, 10):
            await _seed_event(tid2, 60, d)
        await _seed_event(tid2, 60, 40)
        r = await svc.scan(tid2)
        profile2 = await TrustRiskProfileService(
        ).repo.get_profile(tid2)
        record("scan正常分布零沉淀",
               not r["valueAnomaly"]["hit"]
               and "value_anomaly" not in (
                   profile2.get("hitCounts") or {})
               and profile2.get("riskEMA") == 0.0,
               str(profile2.get("hitCounts")))

        # 未建档 404
        try:
            await svc.scan(99999)
            record("scan未建档404", False, "未抛")
        except KeyError:
            record("scan未建档404", True)


class TestHttp:
    async def run(self):
        print("[06 HTTP 层]")
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

        resp = client.post("/api/trust/roles", json={
            "role": "person", "name": "r1-http",
            "idNumber": "110101r1http4321"})
        tid = resp.json().get("trustId")
        u = uuid.uuid4().hex[:8]
        e1 = f"志愿服务官方公示记录材料(编号ZY2026088{u})"
        e2 = f"志愿服务官方公示记录材料(编号ZY2026089{u})"

        # HTTP 存证改字重放: 回包携带 semanticReuse
        body = {"trustId": tid, "layer": "L3",
                "factor": "contribution_net",
                "observed": 200, "peerBaseline": 50,
                "evidence": e1,
                "summary": "志愿服务(权威源公示)",
                "sources": ["gov_penalty", "media"]}
        resp = client.post("/api/trust/deposits",
                           json=body)
        r1 = resp.json()
        resp = client.post("/api/trust/deposits",
                           json={**body, "evidence": e2})
        r2 = resp.json()
        record("HTTP改字重放回包",
               resp.status_code == 200
               and r2["semanticReuse"]["hit"] is True
               and r2["delta"] < r1["delta"],
               str(r2.get("semanticReuse")))

        # scan 鉴权/正常/404
        resp = client.post(f"/api/trust/risk/{tid}/scan")
        record("scan缺Role403",
               resp.status_code == 403,
               str(resp.status_code))
        resp = client.post(f"/api/trust/risk/{tid}/scan",
                           headers=admin)
        record("scan200",
               resp.status_code == 200
               and resp.json().get("success") is True
               and "valueAnomaly" in resp.json(),
               str(resp.status_code))
        resp = client.post("/api/trust/risk/99999/scan",
                           headers=admin)
        record("scan未建档404",
               resp.status_code == 404,
               str(resp.status_code))

        # 画像展示 semantic_reuse 命中计数
        resp = client.get(f"/api/trust/risk/{tid}",
                          headers=admin)
        body = resp.json()
        record("画像命中计数(HTTP)",
               resp.status_code == 200
               and (body.get("hitCounts") or {})
               .get("semantic_reuse") == 1,
               str(body.get("hitCounts")))


async def run_all():
    await TestGramsMath().run()
    await TestSemanticReuse().run()
    await TestValueDistribution().run()
    await TestDepositIntegration().run()
    await TestScanService().run()
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
