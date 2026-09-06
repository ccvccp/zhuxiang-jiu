"""57号·AI智能知识库模块 P1 专项测试
(定向采集+三重合规鉴别中心)

运行方式:
    python test_kb57_p1.py

覆盖(57号计划 §十一 P1):
    - 采集运行器: 源白名单定向采集+quarantined
      隔离态+缺口状态翻转+观察队列
    - 三重合规鉴别: 版权/隐私/内容安全三关+
      合规指纹+脱敏管线+预算计量
    - verdict 四态: passed/blocked/quarantined/halted
    - HTTP 层: 3 端点+鉴权+9 端点计数
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
os.environ["QR55_MODE"] = "off"
os.environ["QR55_LEARN_MODE"] = "off"
os.environ["AIUP56_MODE"] = "off"
os.environ["KB57_MODE"] = "off"

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


async def seed_gap(priority: str = "medium",
                   suggested: list = None) -> int:
    """直建最小缺口(采集测试输入)"""
    from core.helpers import ts
    from repositories.kb57_repository import (
        Kb57Repository,
    )
    repo = Kb57Repository()
    gap_id = await repo.next_gap_id()
    await repo.save_gap({
        "gapId": gap_id,
        "status": "open",
        "priority": priority,
        "topic": "kb57-p1-test-gap",
        "decision": "collect",
        "signalSnapshot": {
            "hits": [{"signalId": "kb_gap_open"}],
            "necessityScore": 45.0,
            "sideCoverage": 0.2},
        "necessityScore": 45.0,
        "trustScore": 60.0,
        "suggestedSources": suggested
        or ["gov_policy_official", "ops_manual"],
        "budgetCap": 0.1,
        "budgetSpent": 0.0,
        "llmCalls": 0,
        "createdAt": ts(),
        "updatedAt": ts(),
    })
    return gap_id


async def seed_resource(gap_id: int,
                        source_id: str = "ops_manual",
                        content_text: str = None,
                        credibility: float = 0.9,
                        license_: str = "站内自有",
                        content_hash: str = None
                        ) -> int:
    """直建 quarantined 资源(鉴别测试输入)"""
    import hashlib
    from core.helpers import ts
    from repositories.kb57_repository import (
        Kb57Repository,
    )
    from services.kb57_registry import SOURCE_REGISTRY
    repo = Kb57Repository()
    resource_id = await repo.next_resource_id()
    text = content_text or (
        "标准作业流程: 第一步提交申请; 第二步"
        "审核材料; 第三步反馈结果。")
    if content_hash is None:
        content_hash = "sha256:" + hashlib.sha256(
            f"{source_id}:{text}".encode(
                "utf-8")).hexdigest()[:32]
    source_meta = SOURCE_REGISTRY.get(source_id) or {}
    await repo.save_resource({
        "resourceId": resource_id,
        "gapId": gap_id,
        "sourceId": source_id,
        "sourceType": source_meta.get(
            "sourceType", "internal"),
        "sourceCredibility": credibility,
        "license": license_,
        "title": "p1-test-resource",
        "contentText": text,
        "maskedText": "",
        "contentHash": content_hash,
        "status": "quarantined",
        "reviewRequired": False,
        "budgetHalted": False,
        "resourceVersion": 1,
        "complianceReports": [],
        "createdAt": ts(),
        "updatedAt": ts(),
    })
    return resource_id


class TestCollect:
    """01 定向采集运行器"""

    async def run(self):
        print("[01 定向采集]")
        reset_all()
        from services.kb57_collect_service import (
            Kb57CollectService,
        )
        collect = Kb57CollectService()

        # off 拒绝
        try:
            await collect.run_collect()
            ok, err = False, "未拒绝"
        except ValueError as e:
            ok, err = "off" in str(e), str(e)[:30]
        record("off 态采集拒绝", ok, err)

        os.environ["KB57_MODE"] = "shadow"

        # 无缺口空转
        r = await collect.run_collect()
        record("无缺口空转(collected=0)",
               r.get("collected") == 0
               and r.get("scanned") == 0,
               str((r.get("collected"),
                    r.get("scanned"))))

        # 指定缺口不存在 404
        try:
            await collect.run_collect(gap_id=999)
            ok, err = False, "未拒绝"
        except KeyError:
            ok, err = True, ""
        record("缺口不存在 404", ok, err)

        # 正常采集
        gap_id = await seed_gap()
        r = await collect.run_collect(gap_id=gap_id)
        resources = r.get("resources") or []
        record("采集成功(2 建议源)",
               r.get("collected") == 2
               and len(resources) == 2,
               str(r.get("collected")))

        # 资源结构(quarantined 铁律入口)
        from repositories.kb57_repository import (
            Kb57Repository,
        )
        repo = Kb57Repository()
        stored = await repo.get_resource(
            resources[0]["resourceId"])
        record("资源 quarantined 隔离态",
               stored.get("status")
               == "quarantined",
               str(stored.get("status")))
        record("资源结构(指纹+可信度+授权)",
               str(stored.get("contentHash")
                   or "").startswith("sha256:")
               and stored.get(
                   "sourceCredibility") == 0.95
               and bool(stored.get("license")),
               str((str(stored.get("contentHash")
                        or "")[:16],
                    stored.get(
                        "sourceCredibility"))))

        # 缺口状态翻转 open→collecting
        gap = await repo.get_gap(gap_id)
        record("缺口状态翻转(collecting)",
               gap.get("status") == "collecting",
               str(gap.get("status")))

        # collect 事件留痕
        events = await repo.list_events(
            gap_id=gap_id, limit=20)
        collect_evs = [e for e in events
                       if e.get("eventType")
                       == "collect"]
        record("collect 事件留痕(2 资源)",
               len(collect_evs) == 2,
               str(len(collect_evs)))

        # 状态机非法(resolved 缺口采集拒绝)
        gap["status"] = "resolved"
        await repo.save_gap(gap, create=False)
        try:
            await collect.run_collect(gap_id=gap_id)
            ok, err = False, "未拒绝"
        except ValueError as e:
            ok, err = "open" in str(e), str(e)[:40]
        record("resolved 缺口采集拒绝", ok, err)

        # 低优先级观察队列
        reset_all()
        low_id = await seed_gap(priority="low")
        r = await collect.run_collect()
        record("低优先级观察(不采集)",
               r.get("collected") == 0
               and r.get("observed") == 1,
               str((r.get("collected"),
                    r.get("observed"))))
        events = await repo.list_events(
            gap_id=low_id, limit=10)
        observe_evs = [e for e in events
                       if e.get("eventType")
                       == "collect_observe"]
        record("观察队列留痕(collect_observe)",
               len(observe_evs) == 1,
               str(len(observe_evs)))

        # 白名单外建议源跳过
        reset_all()
        gap_id2 = await seed_gap(
            suggested=["darkweb_source"])
        r = await collect.run_collect(
            gap_id=gap_id2)
        record("白名单外源跳过(0 资源)",
               r.get("collected") == 0,
               str(r.get("collected")))
        os.environ["KB57_MODE"] = "off"


class TestCompliance:
    """02 三重合规鉴别中心"""

    async def run(self):
        print("[02 三重合规鉴别]")
        reset_all()
        from services.kb57_compliance_service import (
            Kb57ComplianceService, SCAN_COST,
        )
        compliance = Kb57ComplianceService()

        # off 拒绝
        try:
            await compliance.run_compliance(1)
            ok, err = False, "未拒绝"
        except ValueError as e:
            ok, err = "off" in str(e), str(e)[:30]
        record("off 态鉴别拒绝", ok, err)

        os.environ["KB57_MODE"] = "shadow"

        # 资源不存在 404
        try:
            await compliance.run_compliance(999)
            ok, err = False, "未拒绝"
        except KeyError:
            ok, err = True, ""
        record("资源不存在 404", ok, err)

        # ① 干净资源 → passed
        gap_id = await seed_gap()
        rid = await seed_resource(gap_id)
        r = await compliance.run_compliance(rid)
        record("干净资源 passed(compliant)",
               r.get("verdict") == "passed"
               and r.get("status") == "compliant",
               str((r.get("verdict"),
                    r.get("status"))))

        # 三关明细
        gates = r.get("gates") or {}
        record("版权关通过(白名单+无重复+授权)",
               (gates.get("copyright") or {})
               .get("passed") is True
               and (gates.get("copyright") or {})
               .get("sourceWhitelisted") is True,
               str(gates.get("copyright"))[:60])
        record("隐私关通过(无 PII)",
               (gates.get("privacy") or {})
               .get("passed") is True
               and (gates.get("privacy") or {})
               .get("piiFound") == 0,
               str(gates.get("privacy"))[:50])
        record("内容安全关(low 风险)",
               (gates.get("contentSafety") or {})
               .get("riskLevel") == "low",
               str((gates.get("contentSafety")
                    or {}).get("riskLevel")))
        record("预算关通过(49号计量)",
               (gates.get("budget") or {})
               .get("halted") is False
               and (gates.get("budget") or {})
               .get("spent") == SCAN_COST,
               str(gates.get("budget"))[:60])

        # 合规指纹
        record("合规指纹生成(sha256 前缀)",
               str(r.get("fingerprint")
                   or "").startswith("sha256:"),
               str(r.get("fingerprint"))[:20])

        # 资源留痕
        from repositories.kb57_repository import (
            Kb57Repository,
        )
        repo = Kb57Repository()
        stored = await repo.get_resource(rid)
        record("资源 compliant+指纹留痕",
               stored.get("status") == "compliant"
               and str(stored.get("fingerprint")
                       or "").startswith("sha256:")
               and len(stored.get(
                   "complianceReports") or []) == 1,
               str((stored.get("status"),
                    stored.get("fingerprint"))[:16]))

        # 缺口预算扣减
        gap = await repo.get_gap(gap_id)
        record("缺口预算扣减(0.01)",
               gap.get("budgetSpent") == SCAN_COST,
               str(gap.get("budgetSpent")))

        # 状态机(已鉴别资源拒绝重复鉴别)
        try:
            await compliance.run_compliance(rid)
            ok, err = False, "未拒绝"
        except ValueError as e:
            ok, err = "quarantined" in str(e), \
                str(e)[:40]
        record("重复鉴别拒绝(状态机)", ok, err)

        # ② PII 资源 → masked 脱敏
        gap_id2 = await seed_gap()
        pii_text = ("联系人身份证 110101199001011234"
                    "手机 13800138000 卡号 "
                    "6222020200112233445 请回电")
        rid2 = await seed_resource(
            gap_id2, content_text=pii_text)
        r2 = await compliance.run_compliance(rid2)
        record("PII 资源脱敏后 passed",
               r2.get("verdict") == "passed"
               and r2.get("status") == "compliant",
               str(r2.get("verdict")))
        record("PII 三型检出(maskedFields=3)",
               len(r2.get("maskedFields") or []) == 3,
               str(len(r2.get("maskedFields")
                        or [])))
        stored2 = await repo.get_resource(rid2)
        masked = str(stored2.get("maskedText") or "")
        record("脱敏掩码(无明文 PII)",
               "110101199001011234" not in masked
               and "13800138000" not in masked
               and "6222020200112233445"
               not in masked
               and "****" in masked,
               masked[:50])

        # ③ 版权违规(白名单外源) → blocked
        gap_id3 = await seed_gap()
        rid3 = await seed_resource(
            gap_id3, source_id="darkweb_crawler",
            license_="")
        r3 = await compliance.run_compliance(rid3)
        record("白名单外源 blocked(rejected)",
               r3.get("verdict") == "blocked"
               and r3.get("status") == "rejected",
               str((r3.get("verdict"),
                    r3.get("status"))))
        record("blocked 无指纹(铁律)",
               not r3.get("fingerprint"),
               str(r3.get("fingerprint")))

        # ④ 内容指纹重复 → blocked
        gap_id4 = await seed_gap()
        rid4a = await seed_resource(gap_id4)
        rid4b = await seed_resource(
            gap_id4,
            content_hash=(await repo.get_resource(
                rid4a)).get("contentHash"))
        await compliance.run_compliance(rid4a)
        r4b = await compliance.run_compliance(rid4b)
        record("内容指纹重复 blocked",
               r4b.get("verdict") == "blocked",
               str(r4b.get("verdict")))
        dup_detail = (r4b.get("gates") or {}) \
            .get("copyright") or {}
        record("重复明细(换皮重采嫌疑)",
               (dup_detail.get("violations")
                or [""])[0].__contains__("重复"),
               str(dup_detail.get("violations")))

        # ⑤ 高危内容 → blocked
        gap_id5 = await seed_gap()
        rid5 = await seed_resource(
            gap_id5,
            content_text="该内容涉及色情与暴恐"
                         "信息传播渠道")
        r5 = await compliance.run_compliance(rid5)
        record("高危内容 blocked(rejected)",
               r5.get("verdict") == "blocked"
               and r5.get("status") == "rejected",
               str(r5.get("verdict")))

        # ⑥ 中风险内容 → quarantined
        gap_id6 = await seed_gap()
        rid6 = await seed_resource(
            gap_id6,
            content_text="网传未经证实的谣言称"
                         "补贴政策已变更")
        r6 = await compliance.run_compliance(rid6)
        record("中风险 quarantined(待人工)",
               r6.get("verdict")
               == "quarantined"
               and r6.get("status")
               == "quarantined",
               str(r6.get("verdict")))
        stored6 = await repo.get_resource(rid6)
        record("待人工复审标记(reviewRequired)",
               stored6.get("reviewRequired") is True,
               str(stored6.get(
                   "reviewRequired")))

        # ⑦ 低可信度源 → quarantined
        gap_id7 = await seed_gap()
        rid7 = await seed_resource(
            gap_id7, source_id="media_whitelist",
            credibility=0.70)
        r7 = await compliance.run_compliance(rid7)
        record("低可信度源 quarantined",
               r7.get("verdict") == "quarantined",
               str(r7.get("verdict")))

        # ⑧ 预算熔断(缺口级封顶) → halted
        gap_id8 = await seed_gap()
        gap8 = await repo.get_gap(gap_id8)
        gap8["budgetSpent"] = 0.1   # cap 已满
        await repo.save_gap(gap8, create=False)
        rid8 = await seed_resource(gap_id8)
        r8 = await compliance.run_compliance(rid8)
        record("缺口级预算熔断 halted",
               r8.get("verdict") == "halted",
               str(r8.get("verdict")))
        stored8 = await repo.get_resource(rid8)
        record("halted 资源保持隔离(budgetHalted)",
               stored8.get("status")
               == "quarantined"
               and stored8.get("budgetHalted")
               is True,
               str((stored8.get("status"),
                    stored8.get(
                        "budgetHalted"))))
        record("halted 无指纹(不降级铁律)",
               not r8.get("fingerprint"),
               str(r8.get("fingerprint")))

        # ⑨ 鉴别报告详情(观测面)
        report_id = r.get("complianceId")
        detail = await compliance.get_compliance(
            report_id)
        report = detail.get("report") or {}
        record("鉴别报告详情(三关+指纹)",
               report.get("verdict") == "passed"
               and bool(report.get("fingerprint"))
               and "copyright" in report
               and "privacy" in report
               and "contentSafety" in report,
               str(report.get("verdict")))

        # compliance 事件留痕
        events = await repo.list_events(limit=50)
        comp_evs = [e for e in events
                    if e.get("eventType")
                    == "compliance"]
        record("compliance 事件留痕",
               len(comp_evs) >= 8,
               str(len(comp_evs)))
        os.environ["KB57_MODE"] = "off"


class TestHttp:
    """03 HTTP 层"""

    async def run(self):
        print("[03 HTTP]")
        reset_all()
        from fastapi.testclient import TestClient
        from main import app
        client = TestClient(app)
        admin = {"X-Role": "admin"}

        # 决策面 off 409
        resp = client.post("/api/kb57/collect/run",
                           headers=admin)
        record("HTTP collect/run off 409",
               resp.status_code == 409,
               str(resp.status_code))
        resp = client.post(
            "/api/kb57/resources/1/compliance",
            headers=admin)
        record("HTTP compliance off 409",
               resp.status_code == 409,
               str(resp.status_code))

        # shadow 态全链
        os.environ["KB57_MODE"] = "shadow"
        gap_id = await seed_gap()
        resp = client.post(
            "/api/kb57/collect/run",
            json={"gapId": gap_id},
            headers=admin)
        body = resp.json() or {}
        record("HTTP collect/run 200(2 资源)",
               resp.status_code == 200
               and body.get("collected") == 2,
               str((resp.status_code,
                    body.get("collected"))))
        resource_id = (body.get("resources")
                       or [{}])[0].get("resourceId")

        resp = client.post(
            f"/api/kb57/resources/{resource_id}"
            f"/compliance",
            headers=admin)
        body = resp.json() or {}
        record("HTTP compliance 200(passed)",
               resp.status_code == 200
               and body.get("verdict") == "passed",
               str((resp.status_code,
                    body.get("verdict"))))
        compliance_id = body.get("complianceId")

        # 鉴别报告观测面(off 可用)
        os.environ["KB57_MODE"] = "off"
        resp = client.get(
            f"/api/kb57/compliance/{compliance_id}",
            headers=admin)
        body = resp.json() or {}
        record("HTTP compliance/{id} 200(观测面)",
               resp.status_code == 200
               and (body.get("report") or {})
               .get("verdict") == "passed",
               str(resp.status_code))

        # 404(shadow 态——off 态先触 409 决策面门槛)
        os.environ["KB57_MODE"] = "shadow"
        resp = client.post(
            "/api/kb57/resources/999/compliance",
            headers=admin)
        record("HTTP compliance 404",
               resp.status_code == 404,
               str(resp.status_code))
        resp = client.get(
            "/api/kb57/compliance/999",
            headers=admin)
        record("HTTP 报告详情 404",
               resp.status_code == 404,
               str(resp.status_code))
        os.environ["KB57_MODE"] = "off"

        # 鉴权 403
        for method, path in (
                ("POST", "/api/kb57/collect/run"),
                ("POST",
                 f"/api/kb57/resources/1"
                 f"/compliance"),
                ("GET",
                 "/api/kb57/compliance/1")):
            resp = client.request(
                method, path, json={})
            record(f"HTTP {path.split('/')[-1]}"
                   f" 无 Role 403",
                   resp.status_code == 403,
                   str(resp.status_code))

        # 路由累计 9 端点(P2 扩至 14——基线语义)
        from routes.kb57_routes import (
            router as kb_router,
        )
        count = sum(1 for r in kb_router.routes)
        record("57号路由累计 ≥9 端点",
               count >= 9, str(count))


async def run_all():
    await TestCollect().run()
    await TestCompliance().run()
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
