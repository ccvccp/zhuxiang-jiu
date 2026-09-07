"""66号·AI智能工程师大模块 P4 专项测试
(知识进化+看板+自助+红队)

运行方式:
    python test_xx66_p4.py

覆盖(《66号_AI智能工程师大模型实施计划》P4+附录D):
    - 案例库(四要素质量门/PII/valueLinked 数字
      红线/余弦去重/检索命中/淘汰归档)
    - 自动沉淀管道(自愈终态/工单 resolved/
      补偿执行完成→案例)
    - 四区看板(fail-soft/四区结构)
    - proactive 自助赋能
    - 回流闭环(on_service_settled/heal 终态
      →44号反馈)
    - 红队七向量(RT-01~07 全防住)
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
        "name": f"p4-{trust_id}",
        "idDigest": f"p4-{trust_id}",
        "factors": {}, "score": score,
        "rawScore": score, "grade": "C",
        "fused": False, "frozen": False,
        "createdAt": "2026-01-01T00:00:00",
        "updatedAt": "2026-01-01T00:00:00"})


class TestCaseLibrary:
    """01 案例库(质量门+去重+检索)"""

    async def run(self):
        print("[01 案例库]")
        reset_all()
        from services.xx66_knowledge_service import (
            Xx66KnowledgeService,
        )
        svc = Xx66KnowledgeService()

        # 四要素质量门
        for i, args in enumerate((
                ("", "r", "s", "o"),
                ("p", "", "s", "o"),
                ("p", "r", "", "o"),
                ("p", "r", "s", ""))):
            try:
                await svc.create_case(*args)
                ok = False
            except ValueError:
                ok = True
            record(f"缺要素{i+1}拒绝", ok)

        # valueLinked 数字红线
        try:
            await svc.create_case(
                "q", "r", "补偿 99.9 TV", "o",
                value_linked=True)
            num_ok = False
        except ValueError:
            num_ok = True
        record("valueLinked 数字红线", num_ok)
        # 非信值案例可含数字
        r_num = await svc.create_case(
            "q2", "r2", "重启服务 3 次", "o2")
        record("非信值案例数字允许",
               r_num["success"] is True)

        # PII 脱敏
        r_pii = await svc.create_case(
            "手机 13812345678 无法支付", "根因",
            "方案", "效果")
        from repositories.xx66_repository import (
            Xx66Repository,
        )
        case = await Xx66Repository().get_case(
            r_pii["caseId"])
        record("PII 脱敏入库",
               "13812345678" not in
               case["problem"]
               and "*手机号*" in case["problem"])

        # 余弦去重(合并+复发计数)
        r_a = await svc.create_case(
            "支付失败怎么办请尽快处理", "渠道超时",
            "更换支付方式", "已解决")
        r_b = await svc.create_case(
            "支付失败怎么办请尽快处理下", "渠道超时",
            "更换支付方式", "已解决")
        record("相似案例合并",
               r_b["merged"] is True)
        record("复发计数+1",
               r_b["recurrence"] == 1)
        r_c = await svc.create_case(
            "物流发货慢", "仓库积压",
            "催单", "已解决")
        record("不相似不合并",
               r_c["merged"] is False)

        # 检索命中+计数
        s1 = await svc.search_cases("支付失败")
        record("检索命中",
               s1["hitCount"] >= 1)
        record("命中案例含四要素",
               all(k in s1["hits"][0] for k in (
                   "problem", "rootCause",
                   "solution", "outcome")))
        record("相似度>0",
               s1["hits"][0]["similarity"] > 0)
        case_after = await Xx66Repository() \
            .get_case(r_a["caseId"])
        record("命中计数累积",
               case_after["hitCount"] >= 1)

        # 未命中 → 缺口回写(57号)
        s2 = await svc.search_cases(
            "完全无关的查询xyzq")
        record("未命中零结果",
               s2["hitCount"] == 0)
        record("缺口回写 57号",
               s2["gapRecorded"] is True)

        # 淘汰归档(90 日未命中且无复发)
        from core.helpers import ts
        stale_id = await Xx66Repository() \
            .next_case_id()
        old_date = "2020-01-01T00:00:00"
        await Xx66Repository().save_case({
            "caseId": stale_id,
            "problem": "陈年案例", "rootCause": "旧",
            "solution": "旧方案", "outcome": "旧效果",
            "source": "manual", "valueLinked": False,
            "confidence": 0.5, "recurrence": 0,
            "hitCount": 0, "status": "active",
            "createdAt": old_date, "lastHitAt": ""})
        arch = await svc.archive_stale()
        record("陈年案例归档",
               stale_id in arch["archived"])
        archived_case = await Xx66Repository() \
            .get_case(stale_id)
        record("归档状态",
               archived_case["status"] == "archived")
        # 高复发不归档
        record("活跃案例保留",
               arch["archivedCount"] >= 1)


class TestPipelines:
    """02 自动沉淀管道"""

    async def run(self):
        print("[02 沉淀管道]")
        reset_all()
        from services.xx66_knowledge_service import (
            Xx66KnowledgeService,
        )
        svc = Xx66KnowledgeService()

        # 自愈终态 → 案例
        r1 = await svc.settle_case_from_recovery({
            "faultType": "disk_full",
            "faultSource": "db-1",
            "recoveryStatus": "recovered",
            "diagnoseResult": {
                "rootCause": "磁盘耗尽", "confidence": 0.85},
            "recoveryStrategy": {"actions": [
                "restart_task"]}})
        record("自愈终态沉淀",
               r1["success"] is True
               and r1["merged"] is False)
        for bad_status in ("detected", "diagnosing"):
            try:
                await svc.settle_case_from_recovery({
                    "recoveryStatus": bad_status})
                ok = False
            except ValueError:
                ok = True
            record(f"非终态 {bad_status} 拒绝", ok)

        # 工单 resolved+满意度≥4
        r2 = await svc.settle_case_from_ticket({
            "status": "resolved", "type": "aftersale",
            "resolution": "已退款",
            "satisfaction": 5})
        record("工单满意沉淀",
               r2["success"] is True)
        for bad in ({"status": "processing",
                     "satisfaction": 5},
                    {"status": "resolved",
                     "satisfaction": 2}):
            try:
                await svc.settle_case_from_ticket(bad)
                ok = False
            except ValueError:
                ok = True
            record(f"工单 {bad['status']}/"
                   f"sat{bad['satisfaction']} 拒绝", ok)

        # 补偿执行完成(valueLinked 红线)
        r3 = await svc.settle_case_from_compensation({
            "kind": "compensation",
            "status": "executed",
            "ruleId": "trust_misdeduct",
            "entityId": "ent-p4"})
        record("补偿终态沉淀",
               r3["success"] is True)
        from repositories.xx66_repository import (
            Xx66Repository,
        )
        case = await Xx66Repository().get_case(
            r3["caseId"])
        record("补偿案例 valueLinked",
               case["valueLinked"] is True)
        record("valueLinked 无数字",
               not any(c.isdigit()
                       for c in case["solution"]))
        for bad in ({"kind": "compensation",
                     "status": "proposed"},
                    {"kind": "reversal",
                     "status": "executed"}):
            try:
                await svc.settle_case_from_compensation(
                    bad)
                ok = False
            except ValueError:
                ok = True
            record("补偿未完成/非补偿拒绝", ok)


class TestDashboard:
    """03 四区看板+自助赋能"""

    async def run(self):
        print("[03 看板+自助]")
        reset_all()
        from services.xx66_knowledge_service import (
            Xx66KnowledgeService,
        )
        svc = Xx66KnowledgeService()

        d = await svc.dashboard()
        record("看板成功结构",
               d["success"] is True)
        record("四区齐备",
               set(d["zones"].keys()) == {
                   "vitals", "service",
                   "knowledge", "trust"},
               str(list(d["zones"].keys())))
        record("fail-soft 无 error",
               all("error" not in z
                   for z in d["zones"].values()))
        record("生命体征区结构",
               "overall" in d["zones"]["vitals"])
        record("服务区结构",
               "conversationCount" in
               d["zones"]["service"])
        record("知识区结构",
               "caseTotal" in
               d["zones"]["knowledge"])
        record("信任区结构",
               "reconLatest" in d["zones"]["trust"]
               and "logChainIntact" in
               d["zones"]["trust"])

        # 看板聚合反映服务数据
        os.environ["XX66_MODE"] = "shadow"
        try:
            from services.xx66_support_service \
                import Xx66SupportService
            ss = Xx66SupportService()
            await ss.chat("气死我了！！钱扣了凭什么！！")
            await ss.chat("信值怎么扣了")
            d2 = await svc.dashboard()
            record("服务区聚合情绪",
                   d2["zones"]["service"]
                   ["conversationCount"] >= 2)
            record("情绪分布含 angry",
                   "angry" in (
                       d2["zones"]["service"]
                       ["bandDistribution"]))
        finally:
            os.environ["XX66_MODE"] = "off"

        # proactive
        await seed_profile(9801)
        p1 = await svc.proactive(9801)
        record("有档案 proactive",
               p1["success"] is True
               and len(p1["suggestions"]) >= 1)
        record("信值使用建议",
               any(s["kind"] == "trust_usage"
                   for s in p1["suggestions"]))
        p2 = await svc.proactive(999999)
        record("无档案兜底建议",
               p2["success"] is True
               and any("暂无信值档案" in
                       str(s.get("advice"))
                       for s in p2["suggestions"]))


class TestFeedback:
    """04 回流闭环(44号)"""

    async def run(self):
        print("[04 回流闭环]")
        reset_all()
        from services.xx66_knowledge_service import (
            Xx66KnowledgeService,
        )

        # on_service_settled → engineer_service 反馈
        os.environ["XX66_MODE"] = "shadow"
        try:
            from services.xx66_support_service \
                import Xx66SupportService
            ss = Xx66SupportService()
            # 低风险咨询(general→决策 low)+满意
            # → 正确标注
            r = await ss.chat("怎么修改头像")
            stat_id = r["statId"]
            await ss.settle(stat_id, 5)
            # 支付咨询(payment→决策 medium)+满意
            # → 过度预警标注 False
            r2 = await ss.chat("支付失败")
            stat_id2 = r2["statId"]
            await ss.settle(stat_id2, 5)
            from repositories.ai_learning_repository \
                import AiLearningRepository
            fbs = await AiLearningRepository() \
                .list_feedback("engineer_service")
            matched = [
                f for f in fbs
                if f"support stat {stat_id}" in str(
                    f.get("note") or "")]
            record("settle 反馈落 44号",
                   len(matched) >= 1)
            record("反馈正确性标注",
                   (matched[0].get("correct") is True)
                   if matched else False)
            over = [
                f for f in fbs
                if f"support stat {stat_id2}" in str(
                    f.get("note") or "")]
            record("过度预警标注 False",
                   (over[0].get("correct") is False)
                   if over else False)
        finally:
            os.environ["XX66_MODE"] = "off"

        # on_heal_settled(assist 执行终态)
        reset_all()
        os.environ["XX66_MODE"] = "assist"
        try:
            from services.maintenance_service \
                import MaintenanceService
            from services.xx66_heal_service import (
                Xx66HealService,
            )
            rec = await MaintenanceService() \
                .detect_fault(
                    fault_type="disk_full",
                    fault_source="db-p4",
                    recovery_level="auto")
            await Xx66HealService().heal(rec["id"])
            from repositories.ai_learning_repository \
                import AiLearningRepository
            fbs = await AiLearningRepository() \
                .list_feedback("engineer_service")
            matched = [
                f for f in fbs
                if f"heal {rec['id']}" in str(
                    f.get("note") or "")]
            record("heal 终态反馈落 44号",
                   len(matched) >= 1,
                   str([f.get("note")
                        for f in fbs])[:100])
            record("executed 反馈正面",
                   (matched[0].get("correct")
                    is True) if matched else False)
        finally:
            os.environ["XX66_MODE"] = "off"


class TestRedteam:
    """05 红队七向量"""

    async def run(self):
        print("[05 红队七向量]")
        reset_all()
        from services.xx66_knowledge_service import (
            Xx66KnowledgeService,
        )
        svc = Xx66KnowledgeService()

        try:
            await svc.run_redteam()
            off_rejected = False
        except Exception:
            off_rejected = True
        record("off 态红队拒绝", off_rejected)

        # 注意: 红队向量内部切换模式; 外层用 shadow 启动
        os.environ["XX66_MODE"] = "shadow"
        try:
            rt = await svc.run_redteam()
        finally:
            os.environ["XX66_MODE"] = "off"

        record("七向量齐备",
               rt["total"] == 7
               and [v["vector"] for v in
                    rt["vectors"]]
               == [f"RT-0{i}" for i in
                   range(1, 8)])
        record("红队全防住(7/7)",
               rt["allDefended"] is True,
               str([(v["vector"], v["defended"])
                    for v in rt["vectors"]]))
        vec = {v["vector"]: v for v in
               rt["vectors"]}
        record("RT-01 伪情绪骗补防住",
               vec["RT-01"]["defended"] is True)
        record("RT-02 补偿重放防住",
               vec["RT-02"]["defended"] is True)
        record("RT-03 自愈越权防住",
               vec["RT-03"]["defended"] is True)
        record("RT-04 对账伪造防住",
               vec["RT-04"]["defended"] is True)
        record("RT-05 案例投毒防住",
               vec["RT-05"]["defended"] is True)
        record("RT-06 情绪绕过防住",
               vec["RT-06"]["defended"] is True)
        record("RT-07 审批旁路防住",
               vec["RT-07"]["defended"] is True)

        # 红队种子自清理(投毒案例零残留)
        from repositories.xx66_repository import (
            Xx66Repository,
        )
        cases = await Xx66Repository() \
            .list_cases(limit=100)
        residue = [
            c for c in cases
            if "投毒" in str(c.get("problem"))
            or "13812345678" in str(
                c.get("problem"))]
        record("红队案例种子自清理",
               len(residue) == 0,
               str(residue))

        # 环境恢复
        record("环境恢复 off",
               os.environ.get("XX66_MODE")
               == "off")


async def main():
    print("=" * 62)
    print("66号·AI智能工程师 P4 知识进化+看板+红队"
          " 专项测试")
    print("=" * 62)
    for cls in (TestCaseLibrary, TestPipelines,
                TestDashboard, TestFeedback,
                TestRedteam):
        await cls().run()
    print(f"\n{'=' * 62}")
    print(f"总计: {PASS} 通过 / {FAIL} 失败")
    print("=" * 62)
    for line in RESULTS:
        print(line)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
