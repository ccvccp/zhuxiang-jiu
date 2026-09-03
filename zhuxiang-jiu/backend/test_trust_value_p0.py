"""45号·信值模块 P0 专项测试

运行方式:
    python test_trust_value_p0.py

覆盖(计划 §三 3.4):
    - 建档: 双角色/证件摘要(明文不落盘)/重复建档拒绝/
      参数校验(角色/名称/证件号)
    - 冷启动基线: L1=80 善意推定/L2=50 中性/L3=0 从零
      → 初始分 55(watch)
    - 三层宪法评分: 全满 100/L1-only 50/L2-only 30/
      L3-only 20(层贡献数学断言)
    - 宪法护栏(层内归一化): 篡改权重后层间贡献恒定
      (50/30/20 数学上不可漂移)/层内 intraWeight 归一
    - 事件: 因子增量/0-100 夹取/参数校验(层符/因子/
      delta 越界/不存在档案)
    - 熔断引擎: general 不熔断/severe 锁 critical(封顶
      29.9)/L2-L3 满分不参与拯救/criminal 永久(α=0)/
      正向事件不计熔断计数/frozen 冻结位
    - 角色差异: person 合同履约 / org 无形资产确权
    - 档案注册: 第28档案 batch=12/default_weights 单一事实源
    - HTTP 层: 建档/查询/重算/事件灌入(鉴权+校验)
"""

import asyncio
import os
import sys

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


class TestCreateRole:
    async def run(self):
        print("[01 建档与冷启动]")
        reset_all()
        from services.trust_scoring_service import (
            TrustProfileService,
        )
        svc = TrustProfileService()

        r = await svc.create_role("person", "张三",
                                  "110101199001011234")
        record("个人建档", r["success"] is True
               and r["trustId"] > 0, str(r)[:80])

        # 冷启动: 0.5×80 + 0.3×50 + 0.2×0 = 55
        record("冷启动分55", r["score"] == 55.0,
               str(r.get("score")))
        record("冷启动watch档", r["grade"] == "watch",
               str(r.get("grade")))
        layers = r["layers"]
        record("冷启动L1=80", layers["L1"]["score"] == 80.0,
               str(layers["L1"]["score"]))
        record("冷启动L2=50", layers["L2"]["score"] == 50.0,
               str(layers["L2"]["score"]))
        record("冷启动L3=0", layers["L3"]["score"] == 0.0,
               str(layers["L3"]["score"]))
        record("冷启动无熔断", r["fused"] is False
               and r["fusedLevel"] == "", str(r.get("fusedLevel")))

        # 证件摘要存储(明文不落盘)
        from repositories.trust_value_repository import (
            id_digest,
        )
        repo = svc.repo
        rec = await repo.get_profile(r["trustId"])
        record("摘要存储", rec["idDigest"] ==
               id_digest("110101199001011234")
               and len(rec["idDigest"]) == 64,
               str(rec.get("idDigest"))[:20])
        record("明文不落盘",
               "110101199001011234" not in str(rec),
               "证件明文出现在存储")

        # 重复建档拒绝(同证件)
        try:
            await svc.create_role("person", "张三二号",
                                  "110101199001011234")
            record("重复建档拒绝", False, "未抛")
        except ValueError as e:
            record("重复建档拒绝", "已建档" in str(e), str(e))

        # 参数校验
        for name, args in (
                ("非法角色拒绝", ("other", "名", "ID1")),
                ("空名称拒绝", ("person", "  ", "ID1")),
                ("空证件拒绝", ("person", "名", "  ")),
                ("超长证件拒绝", ("person", "名", "x" * 33)),
        ):
            try:
                await svc.create_role(*args)
                record(name, False, "未抛")
            except ValueError:
                record(name, True)

        # 企业建档
        r = await svc.create_role("org", "某某科技有限公司",
                                  "91110000MA01X23456")
        record("企业建档", r["success"] is True
               and r["score"] == 55.0, str(r)[:80])


class TestConstitution:
    async def run(self):
        print("[02 三层宪法评分]")
        reset_all()
        from services.trust_scoring_service import (
            TrustValueScorer,
        )

        record("宪法常量50/30/20",
               TrustValueScorer.CONSTITUTION ==
               {"L1": 0.5, "L2": 0.3, "L3": 0.2},
               str(TrustValueScorer.CONSTITUTION))

        all100 = {n: 100.0 for n in TrustValueScorer.WEIGHTS}
        r = TrustValueScorer.score(all100)
        record("全满100分", r["score"] == 100.0
               and r["grade"] == "healthy", str(r["score"]))
        record("全满层贡献50/30/20",
               r["layers"]["L1"]["contribution"] == 50.0
               and r["layers"]["L2"]["contribution"] == 30.0
               and r["layers"]["L3"]["contribution"] == 20.0,
               str({k: v["contribution"]
                    for k, v in r["layers"].items()}))

        # L1-only 满分 → 恰 50(L2/L3 为 0 救不了)
        l1_only = {n: (100.0 if TrustValueScorer.LAYER_OF[n]
                       == "L1" else 0.0)
                   for n in TrustValueScorer.WEIGHTS}
        r = TrustValueScorer.score(l1_only)
        record("L1-only恰50", r["score"] == 50.0,
               str(r["score"]))

        l2_only = {n: (100.0 if TrustValueScorer.LAYER_OF[n]
                       == "L2" else 0.0)
                   for n in TrustValueScorer.WEIGHTS}
        r = TrustValueScorer.score(l2_only)
        record("L2-only恰30", r["score"] == 30.0,
               str(r["score"]))

        l3_only = {n: (100.0 if TrustValueScorer.LAYER_OF[n]
                       == "L3" else 0.0)
                   for n in TrustValueScorer.WEIGHTS}
        r = TrustValueScorer.score(l3_only)
        record("L3-only恰20", r["score"] == 20.0,
               str(r["score"]))

        # 分层贡献 = 层权 × 层分(数学断言)
        mixed = {"legal_record": 60.0, "regulatory": 90.0,
                 "asset_integrity": 30.0,
                 "platform_conduct": 70.0,
                 "community_standing": 40.0,
                 "ethics_evidence": 100.0,
                 "contribution_net": 80.0,
                 "impact_radius": 20.0, "longtail_good": 50.0}
        r = TrustValueScorer.score(mixed)
        ok = True
        for layer, w in TrustValueScorer.CONSTITUTION.items():
            expect = round(w * r["layers"][layer]["score"], 1)
            if r["layers"][layer]["contribution"] != expect:
                ok = False
        record("贡献=层权×层分", ok,
               str({k: v["contribution"]
                    for k, v in r["layers"].items()}))

        # 档位映射
        record("档位critical",
               TrustValueScorer.grade_of(10) == "critical"
               and TrustValueScorer.grade_of(29.9) == "critical")
        record("档位strained/healthy",
               TrustValueScorer.grade_of(30) == "strained"
               and TrustValueScorer.grade_of(75) == "healthy")


class TestConstitutionGuard:
    async def run(self):
        print("[03 宪法护栏(权重漂移不变性)]")
        from services.trust_scoring_service import (
            TrustValueScorer,
        )
        cls = TrustValueScorer
        all100 = {n: 100.0 for n in cls.WEIGHTS}
        l1_only = {n: (100.0 if cls.LAYER_OF[n] == "L1" else 0.0)
                   for n in cls.WEIGHTS}

        # 篡改: 层内相对漂移 + 层组和漂移(0.45+0.02+0.13=0.60
        # ≠ 0.50 —— 层内归一化吸收一切漂移, 宪法不可破)
        drifted = dict(cls.WEIGHTS)
        drifted["legal_record"] = 0.45
        drifted["regulatory"] = 0.02
        drifted["asset_integrity"] = 0.13
        cls.WEIGHTS = drifted
        try:
            r = cls.score(all100)
            record("漂移后全满仍100", r["score"] == 100.0,
                   str(r["score"]))
            record("漂移后L1贡献仍50",
                   r["layers"]["L1"]["contribution"] == 50.0,
                   str(r["layers"]["L1"]["contribution"]))
            record("漂移后L2贡献仍30",
                   r["layers"]["L2"]["contribution"] == 30.0,
                   str(r["layers"]["L2"]["contribution"]))
            r2 = cls.score(l1_only)
            record("漂移后L1-only仍50", r2["score"] == 50.0,
                   str(r2["score"]))
            # 层内 intraWeight 归一(每层和=1)
            ok = all(
                abs(sum(f["intraWeight"] for f in v["factors"])
                    - 1.0) < 1e-6
                for v in r2["layers"].values())
            record("层内intraWeight归一", ok, "层内权重和≠1")
        finally:
            cls.WEIGHTS = {  # 还原默认(单一事实源)
                "legal_record": 0.20, "regulatory": 0.17,
                "asset_integrity": 0.13,
                "platform_conduct": 0.12,
                "community_standing": 0.10,
                "ethics_evidence": 0.08,
                "contribution_net": 0.09,
                "impact_radius": 0.07,
                "longtail_good": 0.04,
            }


class TestEvents:
    async def run(self):
        print("[04 事件与因子更新]")
        reset_all()
        from services.trust_scoring_service import (
            TrustProfileService,
        )
        svc = TrustProfileService()
        p = await svc.create_role("person", "李四", "ID-777")
        tid = p["trustId"]

        # L3 正向事件 → 因子上升 → 分数上升
        # (contribution_net 层内权重 0.45: 20×0.45=9 层分,
        #  贡献 0.2×9=1.8 → 55+1.8=56.8)
        r = await svc.record_event(
            tid, "L3", "contribution_net", 20,
            summary="志愿服务 40 小时")
        record("正向事件提分", r["score"] == 56.8,
               str(r["score"]))
        record("L3层内加权9", r["layers"]["L3"]["score"] == 9.0,
               str(r["layers"]["L3"]["score"]))

        # 0-100 夹取(连续正向累加超界)
        await svc.record_event(tid, "L3", "contribution_net", 80)
        r = await svc.record_event(tid, "L3", "contribution_net",
                                   80)
        record("因子100夹取",
               r["layers"]["L3"]["factors"][0]["value"] == 100.0,
               str(r["layers"]["L3"]["factors"][0]["value"]))

        # delta 越界拒绝
        try:
            await svc.record_event(tid, "L2",
                                   "platform_conduct", 101)
            record("delta越界拒绝", False, "未抛")
        except ValueError:
            record("delta越界拒绝", True)

        # 因子与层不符拒绝
        try:
            await svc.record_event(tid, "L2", "legal_record",
                                   -10)
            record("层符不符拒绝", False, "未抛")
        except ValueError as e:
            record("层符不符拒绝", "不属于" in str(e), str(e))

        # 非法因子/非法层
        for name, args in (
                ("非法因子拒绝", (tid, "L1", "unknown_factor",
                                 -10, "general")),
                ("非法层拒绝", (tid, "L9", "legal_record", -10,
                              "general")),
        ):
            try:
                await svc.record_event(*args)
                record(name, False, "未抛")
            except ValueError:
                record(name, True)

        # 不存在档案
        try:
            await svc.record_event(99999, "L1", "legal_record",
                                   -10)
            record("事件档案不存在拒绝", False, "未抛")
        except KeyError:
            record("事件档案不存在拒绝", True)
        try:
            await svc.get_profile(99999)
            record("查询不存在拒绝", False, "未抛")
        except KeyError:
            record("查询不存在拒绝", True)

        # 事件流水留痕(审计; 校验失败的事件不落库)
        p = await svc.get_profile(tid)
        record("事件审计留痕", p["eventCount"] == 3
               and len(p["recentEvents"]) == 3,
               str(p.get("eventCount")))


class TestFuse:
    async def run(self):
        print("[05 熔断引擎]")
        reset_all()
        from services.trust_scoring_service import (
            TrustProfileService,
        )
        svc = TrustProfileService()

        # general 违规: 不熔断, 仅扣分
        p = await svc.create_role("person", "王五", "ID-101")
        tid = p["trustId"]
        r = await svc.record_event(
            tid, "L1", "legal_record", -10,
            severity="general", summary="行政处罚 1 起")
        record("general不熔断", r["fused"] is False
               and r["fusedLevel"] == "",
               str(r.get("fusedLevel")))
        record("general扣分", r["score"] < 55.0,
               str(r["score"]))
        p = await svc.get_profile(tid)
        record("general计数", p["l1Severity"].get("general") == 1,
               str(p.get("l1Severity")))

        # severe 违规: 熔断锁 critical(封顶 29.9)
        p = await svc.create_role("person", "赵六", "ID-102")
        tid = p["trustId"]
        r = await svc.record_event(
            tid, "L1", "legal_record", -50,
            severity="severe", summary="严重违法")
        record("severe熔断", r["fused"] is True
               and r["fusedLevel"] == "severe",
               str(r.get("fusedLevel")))
        record("severe锁critical", r["grade"] == "critical",
               str(r.get("grade")))
        record("severe封顶29.9", r["score"] == 29.9,
               str(r.get("score")))
        record("rawScore保留", r["rawScore"] == 45.0,
               str(r.get("rawScore")))
        record("severeα=0.3", r["fuseAlpha"] == 0.3,
               str(r.get("fuseAlpha")))
        record("severe冻结位", r["frozen"] is True,
               str(r.get("frozen")))

        # L2/L3 满分不参与拯救(硬约束——上层不可覆盖)
        for f in ("platform_conduct", "community_standing",
                  "ethics_evidence"):
            await svc.record_event(tid, "L2", f, 50)
        for f in ("contribution_net", "impact_radius",
                  "longtail_good"):
            await svc.record_event(tid, "L3", f, 100)
        r = await svc.compute_score(tid)
        record("L2L3满分不拯救", r["rawScore"] == 80.0
               and r["score"] == 29.9,
               f"raw={r['rawScore']} score={r['score']}")

        # criminal: 永久熔断 α=0
        p = await svc.create_role("person", "钱七", "ID-103")
        tid = p["trustId"]
        r = await svc.record_event(
            tid, "L1", "legal_record", -80,
            severity="criminal", summary="刑事犯罪")
        record("criminal永久熔断", r["fused"] is True
               and r["fusedLevel"] == "criminal"
               and r["fuseAlpha"] == 0.0,
               str(r.get("fusedLevel")))

        # 正向事件不计熔断计数
        p = await svc.create_role("person", "孙八", "ID-104")
        tid = p["trustId"]
        await svc.record_event(tid, "L1", "regulatory", 5,
                               summary="纳税 A 级")
        p = await svc.get_profile(tid)
        record("正向不计熔断计数",
               p["l1Severity"] == {},
               str(p.get("l1Severity")))


class TestRoleDiff:
    async def run(self):
        print("[06 角色差异]")
        reset_all()
        from services.trust_scoring_service import (
            TrustProfileService,
        )
        svc = TrustProfileService()

        p1 = await svc.create_role("person", "张三", "ID-201")
        p2 = await svc.create_role("org", "某某公司", "ID-202")

        l1_person = p1["layers"]["L1"]["factors"]
        l1_org = p2["layers"]["L1"]["factors"]
        ai_p = next(f for f in l1_person
                    if f["name"] == "asset_integrity")
        ai_o = next(f for f in l1_org
                    if f["name"] == "asset_integrity")
        record("个人=合同履约", "合同履约" in ai_p["label"],
               str(ai_p.get("label")))
        record("企业=无形资产确权", "无形资产确权" in
               ai_o["label"], str(ai_o.get("label")))

        # 摘要脱敏展示(前8…后4)
        p = await svc.get_profile(p1["trustId"])
        masked = p.get("idDigestMasked") or ""
        record("摘要脱敏", masked.endswith("…") or "…" in masked,
               str(masked))
        record("摘要非明文", "ID-201" not in str(p),
               "证件明文出现在视图")


class TestRegistry:
    async def run(self):
        print("[07 档案注册]")
        from services.ai_learning_service import (
            SCORER_REGISTRY, default_weights,
        )
        from services.trust_scoring_service import (
            TrustValueScorer,
        )
        meta = SCORER_REGISTRY.get("trust_value")
        record("第28档案注册", meta is not None
               and meta.get("batch") == 12,
               str(meta))
        record("default_weights单一事实源",
               default_weights("trust_value") ==
               TrustValueScorer.WEIGHTS,
               str(default_weights("trust_value"))[:60])


class TestHttp:
    async def run(self):
        print("[08 HTTP 层]")
        reset_all()
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from routes.trust_value_routes import (
            register_trust_value_routes,
        )
        app = FastAPI()
        register_trust_value_routes(app)
        client = TestClient(app)
        admin = {"X-Role": "admin"}

        # 建档 200(冷启动 55)
        resp = client.post("/api/trust/roles", json={
            "role": "person", "name": "HTTP测试",
            "idNumber": "ID-HTTP-1"})
        body = resp.json()
        record("建档200", resp.status_code == 200
               and body.get("score") == 55.0, str(body)[:80])
        tid = body.get("trustId")

        # 建档参数缺 409
        resp = client.post("/api/trust/roles", json={
            "role": "person", "name": "", "idNumber": ""})
        record("建档参数缺409", resp.status_code == 409,
               str(resp.status_code))

        # 查询 200 含分层明细
        resp = client.get(f"/api/trust/roles/{tid}")
        body = resp.json()
        record("查询200", resp.status_code == 200
               and "layers" in body and "constitution" in body,
               str(resp.status_code))
        record("查询含最近事件", "recentEvents" in body,
               str(list(body))[:80])

        # 查询 404
        resp = client.get("/api/trust/roles/99999")
        record("查询404", resp.status_code == 404,
               str(resp.status_code))

        # 事件缺 Role 403
        resp = client.post(f"/api/trust/roles/{tid}/events",
                           json={"layer": "L1",
                                 "factor": "legal_record",
                                 "delta": -30})
        record("事件缺Role403", resp.status_code == 403,
               str(resp.status_code))

        # 事件非法层 409
        resp = client.post(f"/api/trust/roles/{tid}/events",
                           json={"layer": "L9",
                                 "factor": "legal_record",
                                 "delta": -30},
                           headers=admin)
        record("事件非法层409", resp.status_code == 409,
               str(resp.status_code))

        # 事件 200 → severe 熔断
        resp = client.post(f"/api/trust/roles/{tid}/events",
                           json={"layer": "L1",
                                 "factor": "legal_record",
                                 "delta": -60,
                                 "severity": "severe",
                                 "summary": "HTTP 严重违法"},
                           headers=admin)
        body = resp.json()
        record("事件200熔断", resp.status_code == 200
               and body.get("fused") is True
               and body.get("score") == 29.9,
               str(body)[:80])

        # 重算 200(幂等; legal 80-60=20 → L1=56 → raw=28+15=43)
        resp = client.post(f"/api/trust/roles/{tid}/score")
        body = resp.json()
        record("重算200", resp.status_code == 200
               and body.get("score") == 29.9,
               str(body)[:60])
        record("重算幂等", body.get("rawScore") == 43.0,
               str(body.get("rawScore")))


async def run_all():
    await TestCreateRole().run()
    await TestConstitution().run()
    await TestConstitutionGuard().run()
    await TestEvents().run()
    await TestFuse().run()
    await TestRoleDiff().run()
    await TestRegistry().run()
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
