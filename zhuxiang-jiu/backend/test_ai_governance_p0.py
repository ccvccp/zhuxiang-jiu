"""46号·AI 治理与合规中枢 P0 专项测试

运行方式:
    python test_ai_governance_p0.py

覆盖(计划 §三):
    - 注册中心同步: 29 档案全量入册/幂等 diff 归零/
      治理状态保留(frozen 不被重扫覆盖)/分布统计
    - 变更审批总线: 提交 pending/同档案重复 pending 拒绝/
      freeze 幂等冲突/参数校验(类型/理由/payload)
    - 审批执行: freeze 批准→注册中心生效/unfreeze 解冻/
      驳回留痕/重复审批拒绝/不存在拒绝/promote 执行器
      (无挑战者时 rejected+error 留痕)
    - 冻结守卫: is_frozen 判定/未入册不干预/
      run_learning 冻结拦截/fail-soft(治理异常放行)
    - 台账视图: 单档案聚合(live 学习侧状态)/batch 过滤
    - HTTP 层: 五端点结构与鉴权
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


class TestSync:
    async def run(self):
        print("[01 注册中心同步]")
        reset_all()
        from services.ai_governance_service import (
            AiGovernanceService,
        )
        from services.ai_learning_service import SCORER_REGISTRY
        svc = AiGovernanceService()

        r = await svc.sync_registry()
        record("29档案全量入册",
               r["discovered"] == len(SCORER_REGISTRY)
               and r["added"] == len(SCORER_REGISTRY)
               and len(SCORER_REGISTRY) == 32,
               f"added={r.get('added')} "
               f"total={len(SCORER_REGISTRY)}")
        record("批次覆盖1-16", r["discovered"] == 32,
               str(r.get("discovered")))

        # 幂等: 再同步 diff 归零
        r2 = await svc.sync_registry()
        record("重扫幂等diff零", r2["added"] == 0
               and r2["retired"] == 0
               and r2["labelUpdated"] == 0,
               str(r2)[:80])

        # 台账分布
        reg = await svc.list_registry()
        record("台账统计", reg["total"] == 32
               and reg["byStatus"].get("active") == 32,
               str(reg.get("byStatus")))
        record("批次分布", sum(
            (reg.get("byBatch") or {}).values()) == 32,
               str(reg.get("byBatch")))

        # 治理状态保留: 手动 frozen 后重扫不覆盖
        gov = await svc.repo.get_gov("trust_value")
        gov["status"] = "frozen"
        gov["ownerNote"] = "人工冻结测试"
        await svc.repo.save_gov(gov)
        await svc.sync_registry()
        gov2 = await svc.repo.get_gov("trust_value")
        record("治理状态保留", gov2["status"] == "frozen"
               and gov2["ownerNote"] == "人工冻结测试",
               str(gov2.get("status")))

        # 状态过滤
        reg = await svc.list_registry(status="frozen")
        record("状态过滤", reg["total"] == 1
               and reg["entries"][0]["scorerId"]
               == "trust_value", str(reg.get("total")))
        # 批次过滤(batch=12 只有 trust_value)
        reg = await svc.list_registry(batch=12)
        record("批次过滤", reg["total"] == 1,
               str(reg.get("total")))
        # 还原
        gov2["status"] = "active"
        gov2["ownerNote"] = ""
        await svc.repo.save_gov(gov2)


class TestSubmitChange:
    async def run(self):
        print("[02 变更提交]")
        reset_all()
        from services.ai_governance_service import (
            AiGovernanceService,
        )
        svc = AiGovernanceService()
        await svc.sync_registry()

        r = await svc.submit_change(
            "trust_value", "freeze",
            {"note": "权重漂移调查"},
            "45号档案漂移告警, 申请冻结调查")
        record("提交pending", r["success"] is True
               and r["status"] == "pending", str(r)[:70])

        # 同档案重复 pending 拒绝
        try:
            await svc.submit_change(
                "trust_value", "config",
                {}, "重复申请")
            record("重复pending拒绝", False, "未抛")
        except ValueError as e:
            record("重复pending拒绝",
                   "先处置" in str(e), str(e))

        # 未入册档案拒绝(绕过 sync 的档案)
        try:
            await svc.submit_change(
                "unknown_scorer", "freeze", {}, "理由")
            record("未入册拒绝", False, "未抛")
        except KeyError:
            record("未入册拒绝", True)

        # 参数校验
        for name, args in (
                ("非法类型拒绝", ("trust_value", "bad_kind",
                                 {}, "理由")),
                ("空理由拒绝", ("trust_value", "config",
                               {}, "  ")),
                ("超长理由拒绝", ("trust_value", "config",
                                {}, "x" * 501)),
                ("payload非对象", ("trust_value", "config",
                                 "not-dict", "理由")),
        ):
            try:
                await svc.submit_change(*args)
                record(name, False, "未抛")
            except ValueError:
                record(name, True)


class TestReview:
    async def run(self):
        print("[03 审批执行]")
        reset_all()
        from services.ai_governance_service import (
            AiGovernanceService,
        )
        svc = AiGovernanceService()
        await svc.sync_registry()

        # freeze 批准 → 注册中心生效
        r = await svc.submit_change(
            "trust_value", "freeze", {},
            "冻结调查")
        cid = r["changeId"]
        r = await svc.review_change(cid, True,
                                    review_note="同意冻结")
        record("freeze批准生效", r["status"] == "approved"
               and "frozen" in r.get("executed", ""),
               str(r)[:80])
        gov = await svc.repo.get_gov("trust_value")
        record("注册中心frozen", gov["status"] == "frozen"
               and bool(gov.get("frozenAt")),
               str(gov.get("status")))

        # 冻结后 freeze 再申请 → 幂等冲突
        try:
            await svc.submit_change(
                "trust_value", "freeze", {}, "再冻结")
            record("冻结幂等冲突", False, "未抛")
        except ValueError as e:
            record("冻结幂等冲突", "已是冻结态" in str(e),
                   str(e))

        # 重复审批拒绝
        try:
            await svc.review_change(cid, True)
            record("重复审批拒绝", False, "未抛")
        except ValueError as e:
            record("重复审批拒绝", "已裁决" in str(e), str(e))

        # unfreeze 解冻
        r = await svc.submit_change(
            "trust_value", "unfreeze", {},
            "调查完成解冻")
        r = await svc.review_change(r["changeId"], True)
        gov = await svc.repo.get_gov("trust_value")
        record("unfreeze恢复active",
               gov["status"] == "active"
               and gov.get("frozenAt") == "",
               str(gov.get("status")))

        # active 态 unfreeze 再申请 → 幂等冲突
        try:
            await svc.submit_change(
                "trust_value", "unfreeze", {}, "重复解冻")
            record("解冻幂等冲突", False, "未抛")
        except ValueError as e:
            record("解冻幂等冲突", "已是活跃态" in str(e),
                   str(e))

        # 驳回留痕
        r = await svc.submit_change(
            "trust_value", "config",
            {"after": {"min_feedback": 5}},
            "调低学习阈值")
        cid2 = r["changeId"]
        r = await svc.review_change(cid2, False,
                                    review_note="风险不明确")
        record("驳回留痕", r["status"] == "rejected",
               str(r)[:60])
        # 驳回后档案仍 active(无副作用)
        gov = await svc.repo.get_gov("trust_value")
        record("驳回无副作用", gov["status"] == "active",
               str(gov.get("status")))

        # patch 类型: 批准但执行器不支持 → rejected+error
        r = await svc.submit_change(
            "trust_value", "patch",
            {"factor": "regulatory"}, "β 补丁")
        try:
            await svc.review_change(r["changeId"], True)
            record("patch执行器留痕", False, "未抛")
        except ValueError as e:
            record("patch执行器留痕",
                   "执行器未支持" in str(e), str(e))
        ch = await svc.repo.get_change(r["changeId"])
        record("执行失败留痕", ch["status"] == "rejected",
               str(ch.get("status")))

        # 不存在变更拒绝
        try:
            await svc.review_change(99999, True)
            record("审批不存在拒绝", False, "未抛")
        except KeyError:
            record("审批不存在拒绝", True)


class TestFreezeGuard:
    async def run(self):
        print("[04 冻结守卫]")
        reset_all()
        from services.ai_governance_service import (
            AiGovernanceService,
        )
        from services.ai_learning_service import (
            run_learning_cycle, update_learning_config,
        )
        svc = AiGovernanceService()
        await svc.sync_registry()

        # 未冻结不干预(反馈不足语义——非治理拦截)
        await update_learning_config("trust_value",
                                     {"min_feedback": 1})
        try:
            await run_learning_cycle("trust_value")
            record("未冻结不干预(可学习)", True)
        except ValueError as e:
            record("未冻结不干预(可学习)",
                   "冻结" not in str(e), str(e))

        # 冻结 → run_learning 拦截
        r = await svc.submit_change(
            "trust_value", "freeze", {}, "冻结测试")
        await svc.review_change(r["changeId"], True)
        try:
            await run_learning_cycle("trust_value")
            record("冻结拦截学习", False, "未抛")
        except ValueError as e:
            record("冻结拦截学习", "冻结" in str(e), str(e))

        # is_frozen 判定
        record("is_frozen判定",
               await svc.is_frozen("trust_value") is True)
        record("未入册不干预",
               await svc.is_frozen("unknown") is False)

        # fail-soft: 治理设施异常放行(is_frozen 内建捕获)
        orig = svc.repo.get_gov

        async def _boom(scorer_id):
            raise RuntimeError("治理存储瞬断")
        svc.repo.get_gov = _boom
        frozen = await svc.is_frozen("trust_value")
        record("fail-soft异常放行", frozen is False,
               str(frozen))
        svc.repo.get_gov = orig
        # 解冻收尾
        r = await svc.submit_change(
            "trust_value", "unfreeze", {}, "测试完成")
        await svc.review_change(r["changeId"], True)


class TestRegistryView:
    async def run(self):
        print("[05 台账聚合视图]")
        reset_all()
        from services.ai_governance_service import (
            AiGovernanceService,
        )
        svc = AiGovernanceService()
        await svc.sync_registry()

        r = await svc.get_registry_entry("trust_value")
        record("单档案聚合", r["success"] is True
               and r["scorerId"] == "trust_value"
               and r["batch"] == 12, str(r)[:80])
        record("live学习侧聚合", "activeVersion" in
               (r.get("live") or {}),
               str(r.get("live"))[:60])
        record("label正确", "信值三层评分" == r.get("label"),
               str(r.get("label")))

        try:
            await svc.get_registry_entry("unknown_scorer")
            record("未入册查询拒绝", False, "未抛")
        except KeyError:
            record("未入册查询拒绝", True)


class TestHttp:
    async def run(self):
        print("[06 HTTP 层]")
        reset_all()
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from routes.ai_governance_routes import (
            register_ai_governance_routes,
        )
        app = FastAPI()
        register_ai_governance_routes(app)
        client = TestClient(app)
        admin = {"X-Role": "admin"}

        # 鉴权
        resp = client.get("/api/ai-gov/registry")
        record("台账缺Role403", resp.status_code == 403,
               str(resp.status_code))
        resp = client.post("/api/ai-gov/registry/sync")
        record("同步缺Role403", resp.status_code == 403,
               str(resp.status_code))

        # 同步 200(32 档案)
        resp = client.post("/api/ai-gov/registry/sync",
                           headers=admin)
        body = resp.json()
        record("同步200", resp.status_code == 200
               and body.get("added") == 32,
               str(body)[:70])

        # 台账 200 + 过滤
        resp = client.get("/api/ai-gov/registry",
                          headers=admin)
        record("台账200", resp.status_code == 200
               and resp.json().get("total") == 32,
               str(resp.json().get("total")))
        resp = client.get(
            "/api/ai-gov/registry?status=active&batch=12",
            headers=admin)
        record("台账过滤200", resp.status_code == 200
               and resp.json().get("total") == 1,
               str(resp.json().get("total")))

        # 提交变更 200
        resp = client.post("/api/ai-gov/changes", json={
            "scorerId": "trust_value", "kind": "freeze",
            "payload": {"note": "HTTP 冻结"},
            "reason": "HTTP 测试冻结"}, headers=admin)
        body = resp.json()
        record("提交200pending", resp.status_code == 200
               and body.get("status") == "pending",
               str(body)[:70])
        cid = body.get("changeId")

        # 提交参数缺 409
        resp = client.post("/api/ai-gov/changes", json={
            "scorerId": "trust_value", "kind": "bad",
            "reason": "x"}, headers=admin)
        record("提交非法类型409", resp.status_code == 409,
               str(resp.status_code))

        # 队列 200
        resp = client.get("/api/ai-gov/changes?status=pending",
                          headers=admin)
        record("队列200", resp.status_code == 200
               and resp.json().get("total") == 1,
               str(resp.json().get("total")))

        # 审批缺字段 409
        resp = client.post(
            f"/api/ai-gov/changes/{cid}/review",
            json={}, headers=admin)
        record("审批缺字段409", resp.status_code == 409,
               str(resp.status_code))

        # 审批 200(freeze 生效)
        resp = client.post(
            f"/api/ai-gov/changes/{cid}/review",
            json={"approve": True, "reviewNote": "HTTP 批准"},
            headers=admin)
        body = resp.json()
        record("审批200生效", resp.status_code == 200
               and body.get("status") == "approved",
               str(body)[:70])

        # 台账反映 frozen
        resp = client.get(
            "/api/ai-gov/registry?status=frozen",
            headers=admin)
        record("台账frozen反映",
               resp.json().get("total") == 1,
               str(resp.json().get("total")))

        # 审批不存在 404
        resp = client.post(
            "/api/ai-gov/changes/99999/review",
            json={"approve": True}, headers=admin)
        record("审批404", resp.status_code == 404,
               str(resp.status_code))


async def run_all():
    await TestSync().run()
    await TestSubmitChange().run()
    await TestReview().run()
    await TestFreezeGuard().run()
    await TestRegistryView().run()
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
