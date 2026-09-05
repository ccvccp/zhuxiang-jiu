"""51号·小竹可信知识图谱 P0 专项测试
(本体奠基: 注册表+启动自检+变更审批总线)

运行方式:
    python test_kg51_p0.py

覆盖(51号计划 §八 P0):
    - 注册表完整性: 9 实体/9 关系/idPattern 唯一/
      敏感度-隐私成本对齐/PII 禁入基线全实体/
      白黑名单互斥/启动自检可重跑
    - domain/range 约束: 类型全部已注册/
      attested_by 强制下限(证据链)/
      performed_by 单一归属/基数合法
    - 覆盖报告: ≥0.9(动态对照 45号九因子/
      50号 14 行为)
    - 审批总线: 提交/非法 kind/空理由/
      payload 缺必填/PII 红线总线拦截/
      patch_attr 未注册实体/PII 属性/
      重复 pending 冲突/approve 留痕/
      重复裁决拒绝/reject 留痕/byStatus 统计
    - 端点+鉴权+零影响: 403 门槛/视图/
      HTTP 提交裁决/404/
      45号+50号注册表宪法断言零改动
"""

import asyncio
import os
import sys

os.environ["LOCK_MODE"] = "asyncio"
os.environ["STORE_MODE"] = "asyncio"
os.environ.pop("LLM_API_KEY", None)
os.environ["LLM_ENABLED"] = "off"
os.environ["XIAOZHU_LLM_MODE"] = "off"
os.environ["XIAOZHU_PROACTIVE_MODE"] = "off"
os.environ["KG_MODE"] = "off"

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


class TestRegistry:
    """01 注册表完整性"""

    def run(self):
        print("[01 注册表完整性]")
        from services.kg51_ontology import (
            ONTOLOGY_REGISTRY, PII_FORBIDDEN_BASE,
            SENSITIVITY_TIERS, _validate_ontology,
        )
        entities = ONTOLOGY_REGISTRY["entities"]
        relations = ONTOLOGY_REGISTRY["relations"]

        record("实体总数(9)", len(entities) == 9,
               str(len(entities)))
        record("关系总数(9)", len(relations) == 9,
               str(len(relations)))

        patterns = [m["idPattern"] for m in
                    entities.values()]
        record("idPattern 唯一且含占位符",
               len(set(patterns)) == 9
               and all("{" in p and "}" in p
                       for p in patterns))

        cost_ok = all(
            m["sensitivity"] in SENSITIVITY_TIERS
            and m["privacyCost"]
            == SENSITIVITY_TIERS[m["sensitivity"]]
            for m in entities.values())
        l0_zero = all(m["privacyCost"] == 0.0
                      for m in entities.values()
                      if m["sensitivity"] == "L0")
        record("敏感度合法+隐私成本对齐(L0=0)",
               cost_ok and l0_zero)

        pii_ok = all(
            set(PII_FORBIDDEN_BASE)
            <= set(m["forbiddenAttrs"])
            for m in entities.values())
        record("PII 禁入基线全实体覆盖",
               pii_ok)

        disjoint = all(
            not (set(m["allowedAttrs"])
                 & set(m["forbiddenAttrs"]))
            for m in entities.values())
        record("白黑名单互斥", disjoint)

        try:
            _validate_ontology()
            ok = True
            err = ""
        except RuntimeError as e:
            ok, err = False, str(e)
        record("启动自检可重跑(导入即过)", ok, err)


class TestConstraints:
    """02 domain/range 约束"""

    def run(self):
        print("[02 domain/range 约束]")
        from services.kg51_ontology import (
            ONTOLOGY_REGISTRY,
        )
        entities = ONTOLOGY_REGISTRY["entities"]
        relations = ONTOLOGY_REGISTRY["relations"]

        typed_ok = all(
            all(t in entities for t in
                (meta["domain"] + meta["range"]))
            for meta in relations.values())
        record("domain/range 全部已注册", typed_ok)

        attested = relations.get("attested_by") or {}
        record("attested_by 强制下限(证据链)",
               (attested.get("minCard") or 0) >= 1)

        performed = relations.get("performed_by") or {}
        record("performed_by 单一归属(maxCard=1)",
               performed.get("maxCard") == 1)

        card_ok = all(
            meta["minCard"] >= 0
            and (meta["maxCard"] is None
                 or meta["minCard"]
                 <= meta["maxCard"])
            for meta in relations.values())
        record("基数全量合法(min≤max)", card_ok)


class TestCoverage:
    """03 覆盖报告(动态对照)"""

    def run(self):
        print("[03 覆盖报告]")
        from services.kg51_ontology import (
            coverage_report,
        )
        report = coverage_report()
        record("覆盖率 ≥0.9(实为全量)",
               report["ratio"] >= 0.9,
               str(report))
        record("45号九因子+50号14行为+主体证据全表达",
               report["total"] == 9 + 14 + 3
               and report["covered"]
               == report["total"],
               str(report))


class TestApprovalBus:
    """04 变更审批总线(服务级)"""

    async def run(self):
        print("[04 变更审批总线]")
        reset_all()
        from services.kg51_schema_service import (
            Kg51SchemaService,
        )
        svc = Kg51SchemaService()

        r = await svc.submit_change(
            kind="add_entity", target="Winery",
            payload={
                "idPattern": "org:winery:{orgId}",
                "sensitivity": "L1",
                "allowedAttrs": ["orgId", "region"],
            },
            reason="P4 机构扩展预演: 酒庄实体")
        record("提交合法 add_entity → pending",
               r.get("success") is True
               and r.get("status") == "pending"
               and r.get("changeId") == 1, str(r))

        try:
            await svc.submit_change(
                kind="drop_all", target="Member",
                payload={}, reason="非法类型")
            ok, err = False, "未拒绝"
        except ValueError as e:
            ok, err = True, ""
        record("非法 kind 拒绝", ok, err)

        try:
            await svc.submit_change(
                kind="retire", target="Member",
                payload={}, reason="")
            ok, err = False, "未拒绝"
        except ValueError:
            ok, err = True, ""
        record("空理由拒绝", ok, err)

        try:
            await svc.submit_change(
                kind="add_entity", target="Foo",
                payload={"sensitivity": "L1"},
                reason="缺必填字段")
            ok, err = False, "未拒绝"
        except ValueError:
            ok, err = True, ""
        record("add_entity payload 缺必填拒绝",
               ok, err)

        try:
            await svc.submit_change(
                kind="add_entity", target="Bar",
                payload={
                    "idPattern": "x:{id}",
                    "sensitivity": "L2",
                    "allowedAttrs": ["phone"],
                },
                reason="PII 探测")
            ok, err = False, "未拒绝"
        except ValueError:
            ok, err = True, ""
        record("PII 属性总线拦截(digest-only)",
               ok, err)

        try:
            await svc.submit_change(
                kind="patch_attr",
                target="Ghost.attr", payload={},
                reason="未注册实体")
            ok, err = False, "未拒绝"
        except ValueError:
            ok, err = True, ""
        record("patch_attr 未注册实体拒绝", ok, err)

        try:
            await svc.submit_change(
                kind="patch_attr",
                target="Member.phone", payload={},
                reason="PII 属性")
            ok, err = False, "未拒绝"
        except ValueError:
            ok, err = True, ""
        record("patch_attr PII 属性拒绝", ok, err)

        try:
            await svc.submit_change(
                kind="add_entity", target="Winery",
                payload={
                    "idPattern": "org:w2:{id}",
                    "sensitivity": "L1",
                    "allowedAttrs": ["orgId"],
                },
                reason="重复 pending")
            ok, err = False, "未拒绝"
        except ValueError:
            ok, err = True, ""
        record("同 target 重复 pending 冲突拒绝",
               ok, err)

        r = await svc.decide_change(1, approve=True,
                                    review_note="通过")
        record("裁决 approve → approved",
               r.get("status") == "approved", str(r))
        decided = (await svc.repo.get_change(1)) or {}
        record("approve 留痕(reviewer/时间)",
               bool(decided.get("reviewedBy"))
               and bool(decided.get("reviewedAt")),
               str(decided.get("reviewedBy")))

        try:
            await svc.decide_change(1, approve=False)
            ok, err = False, "未拒绝"
        except ValueError:
            ok, err = True, ""
        record("重复裁决拒绝", ok, err)

        r2 = await svc.submit_change(
            kind="patch_attr", target="Member.trustTier",
            payload={"note": "枚举扩充"},
            reason="trustTier 增加档位说明")
        cid2 = r2.get("changeId")
        r = await svc.decide_change(cid2, approve=False,
                                    review_note="驳回")
        record("裁决 reject → rejected",
               r.get("status") == "rejected", str(r))

        r = await svc.list_changes()
        by = r.get("byStatus") or {}
        record("byStatus 统计(approved=1/rejected=1)",
               by.get("approved") == 1
               and by.get("rejected") == 1
               and by.get("pending") == 0, str(by))


class TestEndpoints:
    """05 端点+鉴权+零影响"""

    async def run(self):
        print("[05 端点+鉴权+零影响]")
        reset_all()
        from fastapi.testclient import TestClient
        from main import app
        client = TestClient(app)
        admin = {"X-Role": "admin"}

        resp = client.get("/api/kg51/schema")
        record("schema 无 Role 403",
               resp.status_code == 403,
               str(resp.status_code))

        resp = client.get("/api/kg51/schema",
                          headers=admin)
        body = resp.json() or {}
        record("schema admin 200(9实体/9关系/off)",
               resp.status_code == 200
               and body.get("entityCount") == 9
               and body.get("relationCount") == 9
               and body.get("mode") == "off",
               str(resp.status_code))

        resp = client.post(
            "/api/kg51/schema/changes", headers=admin,
            json={"kind": "retire", "target": "Product",
                  "payload": {}, "reason": "HTTP 通道"})
        record("HTTP 提交变更 200",
               resp.status_code == 200
               and (resp.json()
                    or {}).get("changeId") == 1,
               str(resp.status_code))

        resp = client.get("/api/kg51/schema/changes",
                          headers=admin)
        body = resp.json() or {}
        record("HTTP 队列可见(pending=1)",
               resp.status_code == 200
               and body.get("total") == 1,
               str(resp.status_code))

        resp = client.post(
            "/api/kg51/schema/changes/1/decide",
            headers=admin,
            json={"approve": True, "reviewNote": "通过"})
        record("HTTP 裁决 approved",
               resp.status_code == 200
               and (resp.json()
                    or {}).get("status") == "approved",
               str(resp.status_code))

        resp = client.post(
            "/api/kg51/schema/changes/999/decide",
            headers=admin,
            json={"approve": True})
        record("裁决不存在 404",
               resp.status_code == 404,
               str(resp.status_code))

        # 零影响: 45号/50号注册表宪法断言零改动
        from services.trust_scoring_service import (
            TrustValueScorer,
        )
        from services.xiaozhu_voice50_rules import (
            VOICE_RULES,
        )
        record("45号九因子零改动(宪法断言)",
               len(TrustValueScorer.LAYER_OF) == 9)
        record("50号14行为零改动(宪法断言)",
               len(VOICE_RULES) == 14,
               str(len(VOICE_RULES)))


async def run_all():
    TestRegistry().run()
    TestConstraints().run()
    TestCoverage().run()
    await TestApprovalBus().run()
    await TestEndpoints().run()


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
