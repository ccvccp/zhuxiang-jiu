"""56号·AI智能升级管理 受控自治(智能判断自主审批+
交付前验证) 专项测试

运行方式:
    python test_aiup56_auto.py

覆盖(56号 P5 后续方向③落地):
    - 开关铁律: AIUP56_AUTO_REVIEW=off 时自主审批拒绝
    - 智能判断全绿: 五判据通过 → 自主 approved
      (reviewer=aiup56-auto-agent+判据快照留痕)
    - escalate 级: dualReview 提案永不自主(转人工)
    - 沙箱红灯: gates 有 fail → 转人工
    - 交付前验证: 无瑕疵正常交付 / 瑕疵阻断留痕
    - 人工通道共存: 开关 on 时人工审批面板仍可用
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
os.environ["AIUP56_AUTO_REVIEW"] = "off"

PASS = 0
FAIL = 0
RESULTS = []

ALL_CONFIRMATIONS = (
    "readAuditReport", "reviewedSandbox",
    "acknowledgedRollback", "acknowledgedBudget")


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


async def seed_audited_proposal():
    """种一个 audited 提案(全链: 信号→提案→规划→编码
    →测试→审计——复用 P3 seed 范式)"""
    from core.helpers import ts
    from repositories.qr55_repository import (
        Qr55Repository,
    )
    repo55 = Qr55Repository()
    for snap in (
            {"satisfactionScore": 80.0,
             "clarifyEfficiency": 0.8,
             "penetrationRate": 0.7},
            {"satisfactionScore": 60.0,
             "clarifyEfficiency": 0.5,
             "penetrationRate": 0.4}):
        meid = await repo55.next_model_event_id()
        await repo55.save_model_event({
            "modelEventId": meid,
            "eventType": "metrics_snapshot",
            "detail": {"metrics": snap},
            "createdAt": ts(),
        })
    from services.aiup56_service import Aiup56Service
    from services.aiup56_plan_service import (
        Aiup56PlanService,
    )
    from services.aiup56_code_service import (
        Aiup56CodeService,
    )
    from services.aiup56_test_service import (
        Aiup56TestService,
    )
    from services.aiup56_audit_service import (
        Aiup56AuditService,
    )
    r = await Aiup56Service().evaluate_and_propose()
    pid = r["proposalId"]
    await Aiup56PlanService().plan(pid)
    await Aiup56CodeService().code(pid)
    await Aiup56TestService().test(pid)
    await Aiup56AuditService().audit(pid)
    return pid


class TestAutoReview:
    """受控自治·智能判断自主审批"""

    async def run(self):
        print("[受控自治·智能判断自主审批]")
        from services.aiup56_review_service import (
            AUTO_REVIEWER, Aiup56ReviewService,
            auto_review_enabled,
        )
        from repositories.aiup56_repository import (
            Aiup56Repository,
        )
        repo = Aiup56Repository()

        # ---- 01 开关铁律: off 拒绝 ----
        reset_all()
        os.environ["AIUP56_MODE"] = "shadow"
        os.environ["AIUP56_AUTO_REVIEW"] = "off"
        record("开关读取 off",
               auto_review_enabled() is False)
        pid = await seed_audited_proposal()
        try:
            await Aiup56ReviewService().auto_review(pid)
            record("off 拒绝自主审批", False,
                   "未抛 ValueError")
        except ValueError as e:
            record("off 拒绝自主审批",
                   "受控自治未开启" in str(e))

        # ---- 02 全绿自主批准 ----
        reset_all()
        os.environ["AIUP56_AUTO_REVIEW"] = "on"
        pid = await seed_audited_proposal()
        r = await Aiup56ReviewService().auto_review(pid)
        record("全绿自主批准",
               r.get("autoApproved") is True
               and r.get("needsManual") is False,
               str(r)[:120])
        record("自主批准 reviewer 留痕",
               r.get("reviewer") == AUTO_REVIEWER)
        record("五判据快照",
               isinstance(r.get("checks"), list)
               and len(r["checks"]) == 5
               and all(c.get("passed")
                       for c in r["checks"]))
        p = await repo.get_proposal(pid)
        record("状态 audited→approved",
               p.get("status") == "approved"
               and p.get("reviewedBy")
               == AUTO_REVIEWER)

        # ---- 03 自主批准后交付(验证无瑕疵) ----
        from services.aiup56_deliver_service import (
            Aiup56DeliverService,
        )
        d = await Aiup56DeliverService().deliver(pid)
        record("交付前验证通过→delivered",
               d.get("status") == "delivered")

        # ---- 04 escalate 级永不自主 ----
        reset_all()
        pid = await seed_audited_proposal()
        p = await repo.get_proposal(pid)
        p["dualReview"] = True       # 构造 escalate 级
        await repo.save_proposal(p, create=False)
        r = await Aiup56ReviewService().auto_review(pid)
        record("escalate 级转人工",
               r.get("autoApproved") is False
               and r.get("needsManual") is True)
        esc_check = [
            c for c in r.get("checks", [])
            if c.get("key") == "notEscalated"]
        record("escalate 判据红灯",
               bool(esc_check)
               and esc_check[0].get("passed") is False)
        p = await repo.get_proposal(pid)
        record("状态保持 audited(未被自主翻转)",
               p.get("status") == "audited")

        # ---- 05 沙箱红灯转人工 ----
        reset_all()
        pid = await seed_audited_proposal()
        sandboxes = await repo.list_sandboxes(
            proposal_id=pid)
        sb = sandboxes[0]
        # 平铺三关: 构造 staticGate 红灯(内存态引用)
        if sb.get("staticGate"):
            sb["staticGate"]["passed"] = False
        r = await Aiup56ReviewService().auto_review(pid)
        sb_check = [
            c for c in r.get("checks", [])
            if c.get("key") == "sandboxGreen"]
        record("沙箱红灯判据捕获",
               bool(sb_check)
               and sb_check[0].get("passed") is False,
               str(sb_check)[:120])

        # ---- 06 交付前验证瑕疵阻断 ----
        reset_all()
        pid = await seed_audited_proposal()
        os.environ["AIUP56_AUTO_REVIEW"] = "on"
        await Aiup56ReviewService().auto_review(pid)
        p = await repo.get_proposal(pid)
        for t in p.get("tasks") or []:
            t["rollbackPlan"] = None   # 清空回滚预案
        await repo.save_proposal(p, create=False)
        try:
            await Aiup56DeliverService().deliver(pid)
            record("瑕疵交付阻断", False, "未抛 ValueError")
        except ValueError as e:
            record("瑕疵交付阻断",
                   "交付前验证未通过" in str(e))

        # ---- 07 人工通道共存(开关 on 时铁律不破) ----
        reset_all()
        pid = await seed_audited_proposal()
        r = await Aiup56ReviewService().review(
            pid, reviewer="admin-human",
            approved=True,
            confirmations=list(ALL_CONFIRMATIONS),
            note="人工通道共存验证")
        record("人工审批通道共存",
               r.get("verdict") == "approved"
               and r.get("reviewer") == "admin-human")

        # ---- 08 交付前验证全链绿正常交付(人工路径) ----
        d = await Aiup56DeliverService().deliver(pid)
        record("人工批准+验证通过交付",
               d.get("status") == "delivered")


async def main():
    await TestAutoReview().run()
    print("\n" + "=" * 60)
    print(f"  总计: {PASS + FAIL}  "
          f"通过: {PASS}  失败: {FAIL}")
    print("=" * 60)
    for line in RESULTS:
        print(line)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
