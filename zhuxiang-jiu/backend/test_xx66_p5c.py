"""66号·AI智能工程师大模块 P5c 专项测试
(46号 submit_change 审批真轨)

运行方式:
    python test_xx66_p5c.py

覆盖(P5c):
    - 建议书接 46号 真轨(reversal/compensation/heal 三类
      changeId 回填 + 46号 change 存在 payload 留痕)
    - approve 真轨(config 执行器 ValueError 兜底——radar
      范式 reviewedBy 留痕为裁决准据)
    - reject 轨(46号 rejected + 本地 rejected 不可逆)
    - apply 双重校验(直改库旁路拒绝/真轨 approve 后放行
      完整执行)
    - 存量桥(changeId=0)防旁路拒绝
    - 同档案 pending 互斥透传(46号 语义)

注意: 46号 ai46_changes 为模块级共享存储——用例按序处理
在途 pending(裁决完再提下一类), 与生产串行裁决语义一致。
"""

import asyncio
import os
import sys

os.environ["LOCK_MODE"] = "asyncio"
os.environ["STORE_MODE"] = "asyncio"
os.environ.pop("LLM_API_KEY", None)
os.environ["LLM_ENABLED"] = "off"
os.environ["AI_ENFORCE_MODE"] = "observe"
os.environ["XX66_MODE"] = "off"
os.environ["XX66_LLM_MODE"] = "off"

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


async def seed_diff_recon(run_id=1, diff=5.0):
    """直插 I1 差异对账轮次(repo 层种子)"""
    from repositories.xx66_repository import (
        Xx66Repository,
    )
    from core.helpers import ts as _ts
    await Xx66Repository().save_recon_run({
        "runId": run_id,
        "invariants": {"I1_total_conservation": {
            "diff": diff}},
        "ledgerCount": 0, "profileCount": 0,
        "diffCount": 1, "dangerCount": 1,
        "dangerList": ["I1"], "status": "completed",
        "ranAt": _ts(),
    })


async def seed_recovery(fault_type="service_down",
                        fault_source="db-primary"):
    from services.maintenance_service import (
        MaintenanceService,
    )
    return await MaintenanceService().detect_fault(
        fault_type=fault_type,
        fault_source=fault_source,
        recovery_level="auto")


class TestReversalGov46:
    """01 冲正建议书接 46号 真轨"""

    async def run(self):
        print("[01 冲正真轨]")
        reset_all()
        from services.xx66_recon_service import (
            Xx66ReconService,
        )
        from repositories.ai_governance_repository import (
            AiGovernance46Repository,
        )
        os.environ["XX66_MODE"] = "shadow"
        try:
            await seed_diff_recon(run_id=1, diff=5.0)
            result = await Xx66ReconService() \
                .propose_reversal(1, 999)
            book = result["adviceBook"]
            record("changeId 回填",
                   int(book.get("changeId") or 0) > 0,
                   str(book.get("changeId")))
            change = await AiGovernance46Repository() \
                .get_change(int(book["changeId"]))
            record("46号 change 存在",
                   change is not None)
            record("46号 初始 pending",
                   (change or {}).get("status") == "pending")
            payload = (change or {}).get("payload") or {}
            record("payload 含 adviceId 留痕",
                   str(payload).find(
                       str(book["adviceId"])) >= 0
                   or payload.get("adviceId")
                   == book["adviceId"],
                   str(payload)[:80])
            record("requestedBy 语义",
                   (change or {}).get("requestedBy")
                   == "xx66-recon")
        finally:
            os.environ["XX66_MODE"] = "off"


class TestApproveTrue:
    """02 approve 真轨(radar 兜底范式)"""

    async def run(self):
        print("[02 approve 真轨]")
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from routes.xx66_routes import router as xx66_router
        from repositories.xx66_repository import (
            Xx66Repository,
        )
        from repositories.ai_governance_repository import (
            AiGovernance46Repository,
        )
        app = FastAPI()
        app.include_router(xx66_router)
        client = TestClient(app)
        admin = {"X-Role": "admin"}

        book = await Xx66Repository().list_advice_books() \
            if False else await Xx66Repository() \
            .get_advice_book(1)
        change_id = int(book.get("changeId") or 0)
        record("接续 01 建议书", change_id > 0)

        r = client.post(
            f"/api/xx66/advice/{book['adviceId']}"
            "/approve?approve=true", headers=admin)
        record("approve 端点 200", r.status_code == 200,
               str(r.status_code))
        body = r.json()
        record("本地 approved",
               body.get("status") == "approved")
        record("返回 changeId 透传",
               body.get("changeId") == change_id)
        change = await AiGovernance46Repository() \
            .get_change(change_id)
        record("46号 reviewedBy 留痕(真轨准据)",
               bool((change or {}).get("reviewedBy")))
        record("46号 状态终态(approved/rejected)",
               (change or {}).get("status")
               in ("approved", "rejected"),
               str((change or {}).get("status")))

        # 不可逆: 重复 approve 409
        r2 = client.post(
            f"/api/xx66/advice/{book['adviceId']}"
            "/approve", headers=admin)
        record("重复裁决 409", r2.status_code == 409)


class TestApplyGuards:
    """03 apply 双重校验(防旁路)"""

    async def run(self):
        print("[03 apply 双重校验]")
        from services.xx66_recon_service import (
            Xx66ReconService,
        )
        from repositories.xx66_repository import (
            Xx66Repository,
        )
        os.environ["XX66_MODE"] = "shadow"
        try:
            # a) 直改库旁路: 新书 46号 未裁决, 直改 status=
            #    approved 模拟旧桥/直改库 → apply 拒绝
            #    (真轨核心断言; 01 的书已真轨裁决不可复用)
            await seed_diff_recon(run_id=3, diff=2.0)
            nb = (await Xx66ReconService()
                  .propose_reversal(3, 997))["adviceBook"]
            await Xx66Repository() \
                .update_advice_status(
                    nb["adviceId"], "approved")
            try:
                await Xx66ReconService().apply_reversal(
                    nb["adviceId"])
                bypass_blocked = False
            except ValueError as exc:
                bypass_blocked = ("未经人工裁决" in str(exc)
                                  or "旁路" in str(exc))
            record("直改库旁路拒绝", bypass_blocked)
            # 清理在途 pending(46号 同档案互斥——裁决后
            # 放行后续用例的提交)
            from services.ai_governance_service import (
                AiGovernanceService,
            )
            await AiGovernanceService().review_change(
                int(nb["changeId"]), approve=False,
                reviewed_by="admin",
                review_note="测试旁路样本清理")

            # b) 真轨裁决后放行: 端点 approve → apply 完整执行
            from fastapi import FastAPI
            from fastapi.testclient import TestClient
            from routes.xx66_routes import (
                router as xx66_router,
            )
            app = FastAPI()
            app.include_router(xx66_router)
            client = TestClient(app)
            admin = {"X-Role": "admin"}
            await Xx66Repository() \
                .update_advice_status(1, "proposed")
            r = client.post("/api/xx66/advice/1/approve",
                            headers=admin)
            record("复裁前重置后 approve",
                   r.status_code == 200)
            result = await Xx66ReconService() \
                .apply_reversal(1)
            record("真轨后 apply 完整执行",
                   result["success"] is True)
            book = await Xx66Repository().get_advice_book(1)
            record("执行后终态 executed",
                   book.get("status") == "executed")

            # c) 存量桥(changeId=0)拒绝
            from core.helpers import ts as _ts
            repo = Xx66Repository()
            await repo.save_advice_book({
                "adviceId": 99, "kind": "reversal",
                "runId": 1, "trustId": 999,
                "direction": "issue", "amount": 1.0,
                "reserveRef": "reconcile:1",
                "status": "approved",
                "changeId": 0,
                "proposedAt": _ts(),
            })
            try:
                await Xx66ReconService().apply_reversal(99)
                legacy_blocked = False
            except ValueError as exc:
                legacy_blocked = "changeId" in str(exc)
            record("存量桥无 changeId 拒绝",
                   legacy_blocked)
        finally:
            os.environ["XX66_MODE"] = "off"


class TestRejectTrack:
    """04 reject 轨"""

    async def run(self):
        print("[04 reject 轨]")
        from services.xx66_recon_service import (
            Xx66ReconService,
        )
        from repositories.xx66_repository import (
            Xx66Repository,
        )
        from repositories.ai_governance_repository import (
            AiGovernance46Repository,
        )
        from core.helpers import ts as _ts
        os.environ["XX66_MODE"] = "shadow"
        try:
            await seed_diff_recon(run_id=2, diff=3.0)
            result = await Xx66ReconService() \
                .propose_reversal(2, 998)
            book = result["adviceBook"]
            from fastapi import FastAPI
            from fastapi.testclient import TestClient
            from routes.xx66_routes import (
                router as xx66_router,
            )
            app = FastAPI()
            app.include_router(xx66_router)
            client = TestClient(app)
            admin = {"X-Role": "admin"}
            r = client.post(
                f"/api/xx66/advice/{book['adviceId']}"
                "/approve?approve=false&review_note"
                "=测试驳回", headers=admin)
            record("reject 端点 200", r.status_code == 200)
            record("本地 rejected",
                   r.json().get("status") == "rejected")
            change = await AiGovernance46Repository() \
                .get_change(int(book["changeId"]))
            record("46号 rejected 留痕",
                   (change or {}).get("status")
                   == "rejected")
            try:
                await Xx66ReconService().apply_reversal(
                    book["adviceId"])
                rejected_blocked = False
            except ValueError:
                rejected_blocked = True
            record("rejected 不可 apply", rejected_blocked)
        finally:
            os.environ["XX66_MODE"] = "off"


class TestCompensationGov46:
    """05 补偿建议书接 46号 真轨"""

    async def run(self):
        print("[05 补偿真轨]")
        reset_all()
        from services.xx66_recon_service import (
            Xx66ReconService,
        )
        from repositories.ai_governance_repository import (
            AiGovernance46Repository,
        )
        os.environ["XX66_MODE"] = "shadow"
        try:
            result = await Xx66ReconService() \
                .propose_compensation(
                    "trust_misdeduct",
                    {"entityId": "member-88",
                     "incidentId": "inc-p5c-1",
                     "lossAmount": 10.0,
                     "roleTier": "standard",
                     "emotionBand": "calm"})
            book = result.get("adviceBook") or {}
            record("补偿 changeId 回填",
                   int(book.get("changeId") or 0) > 0,
                   str(book.get("changeId")))
            change = await AiGovernance46Repository() \
                .get_change(int(book.get("changeId") or 0))
            record("补偿 46号 change 存在",
                   change is not None)
            record("补偿 status 初始 proposed",
                   book.get("status") == "proposed")
        finally:
            os.environ["XX66_MODE"] = "off"


class TestHealAdviceGov46:
    """06 heal 建议书落表+接真轨"""

    async def run(self):
        print("[06 heal 建议书真轨]")
        reset_all()
        from services.xx66_heal_service import (
            Xx66HealService,
        )
        from repositories.xx66_repository import (
            Xx66Repository,
        )
        from repositories.ai_governance_repository import (
            AiGovernance46Repository,
        )
        os.environ["XX66_MODE"] = "shadow"
        try:
            rec = await seed_recovery(
                "disk_full", "db-primary")
            out = await Xx66HealService().heal(rec["id"])
            record("heal shadow 走建议书轨",
                   out.get("route") == "advice_book")
            ab = out.get("adviceBook") or {}
            advice_id = int(ab.get("adviceId") or 0)
            record("建议书落表有 adviceId",
                   advice_id > 0)
            book = await Xx66Repository() \
                .get_advice_book(advice_id)
            record("落表 kind=heal_advice",
                   (book or {}).get("kind")
                   == "heal_advice")
            change_id = int((book or {}).get("changeId")
                           or 0)
            record("heal changeId 回填",
                   change_id > 0,
                   str(change_id))
            change = await AiGovernance46Repository() \
                .get_change(change_id)
            record("heal 46号 change 存在",
                   change is not None)
            record("heal requestedBy=xx66-heal",
                   (change or {}).get("requestedBy")
                   == "xx66-heal")
        finally:
            os.environ["XX66_MODE"] = "off"


async def main():
    print("=" * 60)
    print("66号·AI智能工程师 P5c 46号审批真轨 专项测试")
    print("=" * 60)
    for cls in (TestReversalGov46, TestApproveTrue,
                TestApplyGuards, TestRejectTrack,
                TestCompensationGov46,
                TestHealAdviceGov46):
        await cls().run()
    print("-" * 60)
    for line in RESULTS:
        print(line)
    print("=" * 60)
    print(f"总计: {PASS} 通过 / {FAIL} 失败")
    print("=" * 60)
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
