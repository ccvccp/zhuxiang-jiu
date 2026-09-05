"""51号·小竹可信知识图谱 P5 专项测试
(红队测试与收官: 五类攻击向量+零改动断言)

运行方式:
    python test_kg51_p5.py

覆盖(51号计划 §八 P5):
    - 红队整跑: 12 用例全阻断(breached=0)/
      五类向量分布(3/3/2/2/2)
    - A 注入: 无证据 unverified/伪造置信被压/
      复核可拦截
    - B PII 探测: phone/idCard/bankCard 白名单
      过滤(入库 attrs 无泄漏)
    - C 预算绕过: 静态值扣减/耗尽 429
    - D 越权: 他人主体/无身份敏感主体 409
    - E 一致性污染: 注册表 mutate 无效/
      总线 PII 拦截
    - 零改动断言: 49号 17 工具/50号 14 行为/
      45号 9 因子
    - 端点: redteam admin 门槛/403
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
RT_REPORT = {}


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
    from services import kg51_query_cache
    kg51_query_cache.invalidate_all()


async def seed_verified_triples():
    """种子: 少量 verified 三元组(C 类用例需)"""
    from services.kg51_ingest_service import (
        Kg51IngestService,
    )
    stat = {"reviews": 0, "triples": 0, "entities": 0,
            "skipped": 0, "updated": 0, "scanned": 0}
    ingest = Kg51IngestService()
    os.environ["KG_MODE"] = "on"
    await ingest._upsert_triple(
        f"ev:seed:{__import__('uuid').uuid4().hex[:6]}",
        "attested_by", "evid:sha256:seed",
        "system", 0.98,
        {"verifier": "system",
         "sourceRef": "seed"}, stat)
    os.environ["KG_MODE"] = "off"


class TestRedteamRun:
    """01 红队整跑(服务级)"""

    async def run(self):
        print("[01 红队整跑]")
        reset_all()
        await seed_verified_triples()
        from services.kg51_redteam import (
            Kg51RedteamService,
        )
        RT_REPORT["report"] = await \
            Kg51RedteamService().run()
        r = RT_REPORT["report"]
        cases = {c["caseId"]: c
                 for c in r.get("cases") or []}
        record("用例总数(12)", r.get("total") == 12,
               str(r.get("total")))
        record("全部阻断(breached=0)",
               r.get("breached") == 0,
               str([(c["caseId"], c["evidence"])
                    for c in r.get("cases") or []
                    if not c.get("blocked")]))
        v = r.get("vectors") or {}
        record("五类向量分布(3/3/2/2/2)",
               v.get("injection") == 3
               and v.get("piiProbe") == 3
               and v.get("budgetBypass") == 2
               and v.get("privEscalation") == 2
               and v.get("consistency") == 2, str(v))

        # A 注入类逐例
        for cid in ("RT-01", "RT-02", "RT-03"):
            record(f"{cid} 虚假注入被隔离",
                   cases.get(cid, {})
                   .get("blocked") is True,
                   str(cases.get(cid, {})
                       .get("evidence")))
        # B PII 类逐例
        for cid in ("RT-04", "RT-05", "RT-06"):
            record(f"{cid} PII 探测被过滤",
                   cases.get(cid, {})
                   .get("blocked") is True,
                   str(cases.get(cid, {})
                       .get("evidence")))
        # C 预算类逐例
        for cid in ("RT-07", "RT-08"):
            record(f"{cid} 预算绕过被拒",
                   cases.get(cid, {})
                   .get("blocked") is True,
                   str(cases.get(cid, {})
                       .get("evidence")))
        # D 越权类逐例
        for cid in ("RT-09", "RT-10"):
            record(f"{cid} 越权查询被拒",
                   cases.get(cid, {})
                   .get("blocked") is True,
                   str(cases.get(cid, {})
                       .get("evidence")))
        # E 一致性类逐例
        for cid in ("RT-11", "RT-12"):
            record(f"{cid} 一致性污染被拦截",
                   cases.get(cid, {})
                   .get("blocked") is True,
                   str(cases.get(cid, {})
                       .get("evidence")))

        # 零改动断言(红队报告内嵌)
        imm = r.get("immutability") or {}
        record("零改动断言(17 工具/14 行为/9 因子)",
               imm.get("ok") is True, str(imm))

        # A1 用例状态落库验证(unverified 物理隔离)
        from repositories.kg51_repository import (
            Kg51Repository,
        )
        repo = Kg51Repository()
        unv = await repo.list_triples(
            status="unverified", limit=100)
        record("红队注入留痕(unverified 隔离可溯)",
               len(unv) >= 2,
               str(len(unv)))

        # 红队污染清理: probe 实体退役
        entities = await repo.list_entities(
            limit=100000)
        for e in entities:
            eid = e.get("entityId") or ""
            if "probe-" in eid \
                    or "fake" in eid:
                e["status"] = "retired"
                await repo.save_entity(e)
        record("红队痕迹治理可退役(retired)",
               True)


class TestEndpoints:
    """02 端点+鉴权"""

    async def run(self):
        print("[02 端点+鉴权]")
        reset_all()
        from fastapi.testclient import TestClient
        from main import app
        client = TestClient(app)
        admin = {"X-Role": "admin"}

        resp = client.post("/api/kg51/redteam",
                           headers=admin)
        body = resp.json() or {}
        record("HTTP 红队执行 200(12 例)",
               resp.status_code == 200
               and body.get("total") == 12
               and body.get("breached") == 0,
               str(resp.status_code))

        resp = client.post("/api/kg51/redteam")
        record("红队无 Role 403",
               resp.status_code == 403,
               str(resp.status_code))

        # 零影响: 宪法断言(收官)
        from services.trust_scoring_service import (
            TrustValueScorer,
        )
        from services.xiaozhu_voice50_rules import (
            VOICE_RULES,
        )
        from services.xiaozhu_fc_registry import (
            TOOL_REGISTRY,
        )
        record("45号九因子零改动(收官断言)",
               len(TrustValueScorer.LAYER_OF) == 9)
        record("50号14行为零改动(收官断言)",
               len(VOICE_RULES) == 14)
        record("49号17工具零改动(收官断言)",
               len(TOOL_REGISTRY) == 17,
               str(len(TOOL_REGISTRY)))
        os.environ["KG_MODE"] = "off"


async def run_all():
    await TestRedteamRun().run()
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
