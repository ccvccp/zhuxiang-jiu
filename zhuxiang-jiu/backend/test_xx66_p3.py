"""66号·AI智能工程师大模块 P3 专项测试
(信值安全+补偿: 对账×冲正×DSL×反欺诈)

运行方式:
    python test_xx66_p3.py

覆盖(《66号_AI智能工程师大模型实施计划》P3+附录C):
    - 对账引擎(四不变式/差异注入检测/
      danger 只生成建议书)
    - 冲正轨(propose→apply 46号审批链/
      锚定发行/未审批拒绝)
    - 补偿 DSL(三条规则/tier 乘数/情绪附加/
      先乘后截断封顶)
    - 反欺诈门(幂等重放/连环申请/多账号/
      额度封顶/tier 终审)
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


async def seed_profile(trust_id, score=500.0):
    from repositories.trust_value_repository import (
        TrustValue45Repository,
    )
    await TrustValue45Repository().save_profile({
        "trustId": trust_id, "role": "person",
        "name": f"p3-{trust_id}",
        "idDigest": f"p3-{trust_id}",
        "factors": {}, "score": score,
        "rawScore": score, "grade": "C",
        "fused": False, "frozen": False,
        "createdAt": "2026-01-01T00:00:00",
        "updatedAt": "2026-01-01T00:00:00"})


class TestRecon:
    """01 对账引擎(四不变式)"""

    async def run(self):
        print("[01 对账引擎]")
        reset_all()
        from services.xx66_recon_service import (
            Xx66ReconService,
        )
        svc = Xx66ReconService()

        # off 态对账拒绝(决策面门槛)
        try:
            await svc.run_recon()
            off_rejected = False
        except ValueError:
            off_rejected = True
        record("off 态对账拒绝", off_rejected)
        os.environ["XX66_MODE"] = "shadow"

        # 空站对账——四不变式全过
        r0 = await svc.run_recon()
        record("空站对账成功",
               r0["success"] is True)
        record("空站四不变式全过",
               r0["dangerCount"] == 0,
               str(r0["dangerList"]))
        record("对账只读声明",
               "只读" in r0["note"])

        # 真实账本——issue 后 I1/I3 平衡
        await seed_profile(9701)
        from services.trust_asset_service import (
            TrustAssetService,
        )
        assets = TrustAssetService()
        await assets.issue(9701, 100.0,
                           reserve_ref="seed:1")
        r1 = await svc.run_recon()
        record("发行后对账平衡",
               r1["dangerCount"] == 0,
               str(r1["invariants"]["I1_total_conservation"]))
        i1 = r1["invariants"]["I1_total_conservation"]
        record("I1 期望值=发行额",
               i1["expected"] == 100.0
               and i1["actual"] == 100.0)
        i3 = r1["invariants"]["I3_reserve_anchor"]
        record("I3 准备金锚定",
               i3["expected"] == 100.0
               and i3["actual"] == 100.0)

        # 差异注入: 直改余额行 → I1 破坏
        from repositories.backend import (
            get_in_memory_store,
        )
        store = get_in_memory_store()
        store["trust45_assets"][9701]["balance"] = 150.0
        r2 = await svc.run_recon()
        record("差异注入 I1 danger",
               r2["dangerCount"] >= 1
               and "I1_total_conservation"
               in r2["dangerList"])
        record("danger 只生成建议书",
               any(a["kind"] == "freeze_review"
                   for a in r2["advisories"]))
        record("冻结建议书永不自动",
               "永不自动" in str(
                   r2["advisories"][0]["reason"]))
        record("冲正建议附差异",
               any(a["kind"] == "reversal_review"
                   for a in r2["advisories"]))
        # 恢复
        store["trust45_assets"][9701][
            "balance"] = 100.0

        # I3 注入: 直改准备金池
        store["trust45_assets"][9701][
            "reservePool"] = 80.0
        r3 = await svc.run_recon()
        record("差异注入 I3 danger",
               "I3_reserve_anchor" in r3["dangerList"])
        store["trust45_assets"][9701][
            "reservePool"] = 100.0

        # I4 注入: 孤儿余额(有余额无流水)
        await seed_profile(9702)
        store.setdefault("trust45_assets", {})[9702] = {
            "trustId": 9702, "balance": 50.0,
            "frozen": 0.0, "issuedTotal": 50.0,
            "burnedTotal": 0.0, "reservePool": 50.0}
        r4 = await svc.run_recon()
        record("孤儿余额 I4 danger",
               "I4_ledger_completeness"
               in r4["dangerList"])
        record("I4 孤儿列表留痕",
               9702 in (r4["invariants"]
                        ["I4_ledger_completeness"]
                        .get("orphans") or []))

        # 对账轮次持久化
        from repositories.xx66_repository import (
            Xx66Repository,
        )
        latest = await Xx66Repository() \
            .latest_recon()
        record("对账轮次持久化",
               latest is not None
               and latest["runId"] == r4["runId"])
        report = await svc.recon_report()
        record("报告含历史",
               len(report["history"]) >= 2)
        os.environ["XX66_MODE"] = "off"


class TestReversal:
    """02 冲正轨"""

    async def run(self):
        print("[02 冲正轨]")
        reset_all()
        from services.xx66_recon_service import (
            Xx66ReconService,
        )
        from repositories.xx66_repository import (
            Xx66Repository,
        )
        svc = Xx66ReconService()
        repo = Xx66Repository()
        os.environ["XX66_MODE"] = "shadow"

        # 未知轮次 404
        try:
            await svc.propose_reversal(999, 9701)
            nf = False
        except KeyError:
            nf = True
        record("未知轮次 404", nf)

        # 无差异轮次拒绝冲正
        await seed_profile(9701)
        from services.trust_asset_service import (
            TrustAssetService,
        )
        await TrustAssetService().issue(
            9701, 100.0, reserve_ref="seed:1")
        r1 = await svc.run_recon()
        try:
            await svc.propose_reversal(r1["runId"],
                                        9701)
            no_diff = False
        except ValueError:
            no_diff = True
        record("无差异拒绝冲正", no_diff)

        # 差异轮次——建议书生成
        from repositories.backend import (
            get_in_memory_store,
        )
        store = get_in_memory_store()
        store["trust45_assets"][9701][
            "balance"] = 60.0  # I1 差 -40
        r2 = await svc.run_recon()
        book = await svc.propose_reversal(
            r2["runId"], 9701)
        record("冲正建议书生成",
               book["success"] is True)
        ab = book["adviceBook"]
        record("冲正方向 issue(补发)",
               ab["direction"] == "issue")
        record("冲正金额=差值",
               ab["amount"] == 40.0)
        record("锚定 reserve_ref",
               ab["reserveRef"]
               == f"reconcile:{r2['runId']}")
        record("建议书初始 proposed",
               ab["status"] == "proposed")

        # 未审批拒绝执行
        try:
            await svc.apply_reversal(ab["adviceId"])
            unapproved = False
        except ValueError:
            unapproved = True
        record("未审批拒绝执行", unapproved)

        # 审批后执行——45号 issue 锚定
        await repo.update_advice_status(
            ab["adviceId"], "approved")
        applied = await svc.apply_reversal(
            ab["adviceId"])
        record("冲正执行成功",
               applied["success"] is True)
        record("冲正走 45号账本",
               applied.get("ledgerId") is not None)
        record("冲正锚定留痕",
               applied["reserveRef"]
               == f"reconcile:{r2['runId']}")

        # 冲正后余额修复
        bal = await TrustAssetService().balance(9701)
        record("冲正后余额修复",
               abs(bal["balance"] - 100.0) <= 0.01,
               str(bal["balance"]))

        # 建议书终态 executed
        final_book = await repo.get_advice_book(
            ab["adviceId"])
        record("建议书终态 executed",
               final_book["status"] == "executed")
        # 冲正后对账回归平衡
        r3 = await svc.run_recon()
        record("冲正后对账回归平衡",
               r3["dangerCount"] == 0,
               str(r3["dangerList"]))
        os.environ["XX66_MODE"] = "off"


class TestDsl:
    """03 补偿 DSL"""

    async def run(self):
        print("[03 补偿 DSL]")
        from services.xx66_recon_service import (
            Xx66ReconService, COMPENSATION_RULES,
            CAPS, TIER_MULTIPLIERS,
        )
        svc = Xx66ReconService()

        record("三条规则版本化",
               set(COMPENSATION_RULES.keys()) == {
                   "trust_misdeduct",
                   "downtime_loss", "ticket_severe"})
        record("封顶三重口径",
               CAPS["per_case"] == 50.0
               and CAPS["daily_site"] == 500.0
               and CAPS["monthly_entity"] == 200.0)
        record("tier 乘数口径",
               TIER_MULTIPLIERS["trusted"] == 1.2
               and TIER_MULTIPLIERS["restricted"]
               == 0.6)

        # 基础求值
        ev = svc.evaluate("trust_misdeduct", {
            "lossAmount": 10.0,
            "roleTier": "standard",
            "emotionBand": "calm"})
        record("标准档无附加=损失额",
               ev["compensation"] == 10.0)
        record("求值结构完整",
               all(k in ev for k in (
                   "base", "tierMultiplier",
                   "emotionMultiplier",
                   "grossBeforeCap",
                   "compensation", "capped")))

        # tier 乘数
        ev_t = svc.evaluate("trust_misdeduct", {
            "lossAmount": 10.0,
            "roleTier": "trusted"})
        record("trusted×1.2",
               abs(ev_t["compensation"] - 12.0)
               <= 0.01)
        ev_r = svc.evaluate("trust_misdeduct", {
            "lossAmount": 10.0,
            "roleTier": "restricted"})
        record("restricted×0.6",
               abs(ev_r["compensation"] - 6.0)
               <= 0.01)

        # 情绪附加
        ev_a = svc.evaluate("trust_misdeduct", {
            "lossAmount": 10.0,
            "roleTier": "standard",
            "emotionBand": "angry"})
        record("angry +10%",
               abs(ev_a["compensation"] - 11.0)
               <= 0.01)

        # 先乘后截断(封顶)
        ev_cap = svc.evaluate("trust_misdeduct", {
            "lossAmount": 100.0,
            "roleTier": "trusted",
            "emotionBand": "angry"})
        record("封顶 50 TV",
               ev_cap["compensation"] == 50.0)
        record("封顶标记",
               ev_cap["capped"] is True)
        record("封顶前总额留痕",
               ev_cap["grossBeforeCap"] == 132.0)

        # 宕机规则(affected_users)
        ev_d = svc.evaluate("downtime_loss", {
            "affectedUsers": 30})
        record("宕机=30×1 TV",
               ev_d["compensation"] == 30.0)

        # 非法输入
        for bad_args in (
                {"ruleId": "forged_rule",
                 "lossAmount": 10},
                {"ruleId": "trust_misdeduct",
                 "lossAmount": -5}):
            try:
                svc.evaluate(
                    bad_args["ruleId"],
                    bad_args)
                bad_ok = False
            except ValueError:
                bad_ok = True
            record(f"非法输入拒绝"
                   f"({bad_args['ruleId'][:12]})",
                   bad_ok)

        # 确定性
        ev1 = svc.evaluate("trust_misdeduct", {
            "lossAmount": 20.0,
            "roleTier": "watched",
            "emotionBand": "frustrated"})
        ev2 = svc.evaluate("trust_misdeduct", {
            "lossAmount": 20.0,
            "roleTier": "watched",
            "emotionBand": "frustrated"})
        record("DSL 确定性(同入同出)",
               ev1["compensation"]
               == ev2["compensation"])


class TestFraudGates:
    """04 反欺诈门+建议书主链"""

    async def run(self):
        print("[04 反欺诈门]")
        reset_all()
        from services.xx66_recon_service import (
            Xx66ReconService,
        )
        svc = Xx66ReconService()

        try:
            await svc.propose_compensation(
                "trust_misdeduct", {})
            empty_ctx = False
        except ValueError:
            empty_ctx = True
        record("空上下文拒绝", empty_ctx)

        os.environ["XX66_MODE"] = "shadow"
        try:
            # 正常建议书
            r1 = await svc.propose_compensation(
                "trust_misdeduct", {
                    "entityId": "ent-1",
                    "incidentId": "INC-1",
                    "lossAmount": 10.0,
                    "roleTier": "standard"})
            record("建议书生成",
                   r1["success"] is True)
            record("标准档无欺诈标记",
                   r1["manualReview"] is False)
            record("补偿额=DSL",
                   r1["adviceBook"]["amount"]
                   == 10.0)
            record("46号审批轨声明",
                   "46号" in r1["adviceBook"]
                   ["delivery"])

            # 幂等重放拒
            try:
                await svc.propose_compensation(
                    "trust_misdeduct", {
                        "entityId": "ent-1",
                        "incidentId": "INC-1",
                        "lossAmount": 10.0})
                replay = False
            except ValueError:
                replay = True
            record("同窗同实体重放拒", replay)

            # tier 观察——强制人工终审
            r2 = await svc.propose_compensation(
                "trust_misdeduct", {
                    "entityId": "ent-2",
                    "incidentId": "INC-2",
                    "lossAmount": 10.0,
                    "roleTier": "restricted"})
            record("restricted 强制人工终审",
                   r2["manualReview"] is True
                   and any("tier_review"
                           in f for f in
                           r2["adviceBook"]
                           ["fraudFlags"]))
            record("观察档不拒之门外",
                   r2["success"] is True)

            # 连环申请(7 日≥3 次——3 次历史后
            # 第 4 次申请触发人工终审)
            for i in (3, 4, 5):
                await svc.propose_compensation(
                    "trust_misdeduct", {
                        "entityId": "ent-3",
                        "incidentId": f"INC-{i}",
                        "lossAmount": 5.0})
            r_serial = await \
                svc.propose_compensation(
                    "trust_misdeduct", {
                        "entityId": "ent-3",
                        "incidentId": "INC-6",
                        "lossAmount": 5.0})
            record("连环申请人工终审",
                   r_serial["manualReview"] is True
                   and any("serial" in f
                           for f in
                           r_serial["adviceBook"]
                           ["fraudFlags"]),
                   str(r_serial["adviceBook"]
                       ["fraudFlags"]))

            # 月实体封顶(200 TV——DSL 单案截 50, 累计
            # 4×50=200 后第 5 笔触发 200+50>200)
            for i in (1, 2, 3, 4):
                await svc.propose_compensation(
                    "trust_misdeduct", {
                        "entityId": "ent-4",
                        "incidentId": f"INC-BIG{i}",
                        "lossAmount": 100.0})
            try:
                await svc.propose_compensation(
                    "trust_misdeduct", {
                        "entityId": "ent-4",
                        "incidentId": "INC-BIG5",
                        "lossAmount": 100.0})
                monthly_cap = False
            except ValueError:
                monthly_cap = True
            record("月实体封顶拒绝", monthly_cap)

            # 透明化详情
            detail = await svc.get_compensation(
                r1["adviceBook"]["adviceId"])
            record("补偿详情四栏依据",
                   set(detail["transparency"].keys())
                   >= {"base", "tierMultiplier",
                       "emotionBonus", "caps"})
            record("DSL 版本留痕",
                   detail["basis"]["dslVersion"]
                   == "1.0")

            # engineer_log 留痕
            from services.xx66_heal_service import (
                Xx66HealService,
            )
            chain = await Xx66HealService() \
                .verify_log_chain()
            record("补偿留痕指纹链完好",
                   chain["chainIntact"] is True
                   and chain["total"] >= 5)
        finally:
            os.environ["XX66_MODE"] = "off"

        # off 态拒绝
        try:
            await svc.propose_compensation(
                "trust_misdeduct", {
                    "entityId": "ent-9",
                    "incidentId": "INC-9",
                    "lossAmount": 1.0})
            off_rejected = False
        except ValueError:
            off_rejected = True
        record("off 态补偿拒绝", off_rejected)


async def main():
    print("=" * 62)
    print("66号·AI智能工程师 P3 信值安全+补偿 专项测试")
    print("=" * 62)
    for cls in (TestRecon, TestReversal,
                TestDsl, TestFraudGates):
        await cls().run()
    print(f"\n{'=' * 62}")
    print(f"总计: {PASS} 通过 / {FAIL} 失败")
    print("=" * 62)
    for line in RESULTS:
        print(line)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
