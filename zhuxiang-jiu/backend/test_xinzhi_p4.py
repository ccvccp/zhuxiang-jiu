"""68号·信值·臻选·P4 商家信值体系专项测试

覆盖(《68号 创新规划方案》§三 P4, ~22 断言):
    1. 认证纯函数: 4+2 分值(满分54)/缺项扣减/
       评级阶梯 S-A-B-C-D
    2. 认证申请: 全过 A 档/缺一必查拒绝/重复申请/
       会员 404/空店名
    3. 动态评级: 冷启动 hold/37号好数据升级生效留痕/
       坏数据降档测算/降级仅生成 46号建议(等级不变)/
       level_view 透明/未认证拒绝
    4. 预演沙盘: 1000 单推演/轨迹单调收敛/TOP3 风险
       排序/成长路线图/merchantId 模式认证分+回写/
       参数校验
    5. 46号总线: SCORER_REGISTRY 注册依赖

运行:
    $env:LOCK_MODE="asyncio"; $env:STORE_MODE="asyncio"
    python test_xinzhi_p4.py
"""

import asyncio
import os
import sys


os.environ["LOCK_MODE"] = "asyncio"
os.environ["STORE_MODE"] = "asyncio"
os.environ.pop("LLM_API_KEY", None)
os.environ["LLM_ENABLED"] = "off"

from services.xinzhi_merchant_service import (
    XinzhiMerchantService, cert_score_of,
    grade_of_merchant, CERT_MAX, REQUIRED_CHECKS,
)
from repositories.member_repository import MemberRepository

PASS = 0
FAIL = 0
RESULTS = []


def record(name, passed, detail=""):
    global PASS, FAIL
    if passed:
        PASS += 1
        RESULTS.append(f"  \u2713 {name}")
    else:
        FAIL += 1
        RESULTS.append(f"  \u2717 {name} \u2014 {detail}")


def reset_store():
    from repositories.store import reset_store as _reset
    _reset()


async def seed_member(member_id: int) -> None:
    from datetime import datetime, UTC, timedelta
    repo = MemberRepository()
    member = {
        "id": member_id, "phone": f"1380000000{member_id}",
        "password": "x", "nickname": f"测试会员{member_id}",
        "level": 2, "points": 100, "status": 1,
        "created_at": (datetime.now(UTC)
                       - timedelta(days=200)).isoformat(),
        "last_login_at": datetime.now(UTC).isoformat(),
    }
    await repo.save(member_id, member)


ALL_CHECKS = {c: True for c in REQUIRED_CHECKS}
ALL_BONUSES = {"eco_contribution": True,
               "external_endorsement": True}


async def seed_alliance_performance(
        merchant_id: int, settled: int, unsettled: int,
        good_reviews: int, bad_reviews: int) -> None:
    """播种 37号同盟履约数据(订单结算+评价——ID 全局唯一)"""
    from repositories.alliance_repository import (
        AllianceRepository)
    global _ORDER_SEQ, _REVIEW_SEQ
    repo = AllianceRepository()
    for _ in range(settled):
        _ORDER_SEQ += 1
        await repo.save_order({
            "orderId": f"P4ALO{_ORDER_SEQ:06d}",
            "merchantId": merchant_id,
            "settled": True, "status": "completed",
            "createdAt": "2026-09-01T00:00:00"})
    for _ in range(unsettled):
        _ORDER_SEQ += 1
        await repo.save_order({
            "orderId": f"P4ALO{_ORDER_SEQ:06d}",
            "merchantId": merchant_id,
            "settled": False, "status": "paid",
            "createdAt": "2026-09-02T00:00:00"})
    for _ in range(good_reviews):
        _REVIEW_SEQ += 1
        await repo.save_review({
            "reviewId": _REVIEW_SEQ,
            "merchantId": merchant_id,
            "score": 5, "content": "很好",
            "folded": False, "createdAt":
                "2026-09-01T00:00:00"})
    for _ in range(bad_reviews):
        _REVIEW_SEQ += 1
        await repo.save_review({
            "reviewId": _REVIEW_SEQ,
            "merchantId": merchant_id,
            "score": 2, "content": "一般",
            "folded": False, "createdAt":
                "2026-09-02T00:00:00"})


_ORDER_SEQ = 0
_REVIEW_SEQ = 0


# ============================================================
# 1. 纯函数(3 断言)
# ============================================================

class TestPure:
    async def run(self):
        # 1) 4+2 满分 54(四必查×10+生态8+背书6)
        score, missing = cert_score_of(
            ALL_CHECKS, ALL_BONUSES)
        record("认证分-满分54",
               score == CERT_MAX == 54 and not missing,
               f"s={score} m={missing}")

        # 2) 缺项扣减(缺 backend → 30+14加分=44;
        #    仅过 entity → 10 无加分)
        s1, m1 = cert_score_of(
            {**ALL_CHECKS, "backend": False}, ALL_BONUSES)
        s2, m2 = cert_score_of(
            {"entity": True}, None)
        record("认证分-缺项扣减",
               s1 == 44 and m1 == ["backend"]
               and s2 == 10 and m2 == ["fulfillment",
                                      "service", "backend"],
               f"s1={s1} s2={s2}")

        # 3) 评级阶梯(S≥90/A≥80/B≥70/C≥60/D)
        record("评级-阶梯映射",
               grade_of_merchant(90) == "S"
               and grade_of_merchant(89.9) == "A"
               and grade_of_merchant(80) == "A"
               and grade_of_merchant(79.9) == "B"
               and grade_of_merchant(70) == "B"
               and grade_of_merchant(69.9) == "C"
               and grade_of_merchant(60) == "C"
               and grade_of_merchant(59.9) == "D",
               "ladder")


# ============================================================
# 2. 认证申请(5 断言)
# ============================================================

class TestApply:
    async def run(self):
        reset_store()
        await seed_member(1)
        await seed_member(2)
        svc = XinzhiMerchantService()

        # 4) 全过+两加分 → A 档(54+冷启动34.9=88.9)
        m = await svc.apply_certification(
            1, "竹香臻选旗舰店", ALL_CHECKS, ALL_BONUSES,
            alliance_merchant_id=901)
        record("认证-全过A档",
               m["certified"] is True
               and m["status"] == "certified"
               and m["certScore"] == 54
               and m["grade"] == "A"
               and abs(m["merchantScore"] - 88.9) < 0.2,
               f"g={m['grade']} s={m['merchantScore']}")

        # 5) 缺一必查 → 拒绝(留痕)
        m2 = await svc.apply_certification(
            2, "缺资质小铺",
            {**ALL_CHECKS, "entity": False}, None)
        record("认证-缺必查拒绝",
               m2["certified"] is False
               and m2["status"] == "rejected"
               and "主体资质" in m2["missingChecks"]
               and m2["grade"] == "D",
               f"{m2['status']} {m2['missingChecks']}")

        # 6) 重复申请拒绝(一人一档)
        ok = False
        try:
            await svc.apply_certification(
                1, "第二家店", ALL_CHECKS, None)
        except ValueError:
            ok = True
        record("认证-重复申请拒绝", ok)

        # 7) 会员 404
        ok = False
        try:
            await svc.apply_certification(
                999, "幽灵店", ALL_CHECKS, None)
        except KeyError:
            ok = True
        record("认证-会员404", ok)

        # 8) 空店名拒绝
        ok = False
        try:
            await svc.apply_certification(2, "  ",
                                          ALL_CHECKS, None)
        except ValueError:
            ok = True
        record("认证-空店名拒绝", ok)


# ============================================================
# 3. 动态评级(7 断言)
# ============================================================

class TestRegrade:
    async def run(self):
        reset_store()
        await seed_member(1)
        svc = XinzhiMerchantService()
        m = await svc.apply_certification(
            1, "竹香臻选旗舰店", ALL_CHECKS, ALL_BONUSES,
            alliance_merchant_id=901)
        mid = m["merchantId"]

        # 9) 冷启动 hold(无 37号数据, 等级不变)
        r0 = await svc.regrade(mid)
        record("评级-冷启动hold",
               r0["changed"] is False
               and r0["action"] == "hold"
               and r0["newGrade"] == "A",
               f"{r0['action']} {r0['newGrade']}")

        # 10) 37号好数据 → S 升级自动生效
        #     (结算率 100% + 好评率 100% → 履约分 46)
        await seed_alliance_performance(
            901, settled=5, unsettled=0,
            good_reviews=4, bad_reviews=0)
        r1 = await svc.regrade(mid)
        record("评级-好数据升级S",
               r1["changed"] is True
               and r1["action"] == "upgrade_applied"
               and r1["newGrade"] == "S"
               and abs(r1["newScore"] - 100.0) < 0.1,
               f"g={r1['newGrade']} s={r1['newScore']}")

        # 11) 升级留痕(history 追加)
        m1 = await svc.repo.get_merchant(mid)
        record("评级-升级留痕",
               len(m1["gradeHistory"]) == 2
               and m1["gradeHistory"][-1]["from"] == "A"
               and m1["gradeHistory"][-1]["to"] == "S"
               and m1["gradeHistory"][-1]["auto"] is True,
               f"n={len(m1['gradeHistory'])}")

        # 12) 37号坏数据 → 测算降档 B(54+23=77, 落 70-79 档)
        #     (结算率 50% + 好评率 50%)
        #     (先入册 46号——降档建议须走审批总线)
        from services.ai_governance_service import (
            AiGovernanceService)
        await AiGovernanceService().sync_registry()
        await seed_alliance_performance(
            901, settled=0, unsettled=5,
            good_reviews=0, bad_reviews=4)
        r2 = await svc.regrade(mid)
        record("评级-坏数据测算降档",
               r2["action"] == "demotion_proposed"
               and r2["newGrade"] == "B"
               and abs(r2["newScore"] - 77.0) < 0.2,
               f"g={r2['newGrade']} s={r2['newScore']}")

        # 13) 降级永不自动(等级保持 S + 46号 pending)
        m2 = await svc.repo.get_merchant(mid)
        proposal = r2.get("changeProposal") or {}
        record("评级-降级走46号建议",
               m2["grade"] == "S"
               and proposal.get("changeId", 0) > 0
               and proposal.get("status") == "pending",
               f"grade={m2['grade']} "
               f"chg={proposal.get('changeId')}")
        # 46号留痕核对(重复降档建议被串行约束拦截)
        r3 = await svc.regrade(mid)
        p3 = r3.get("changeProposal") or {}
        m3 = await svc.repo.get_merchant(mid)
        record("评级-降级等级恒定",
               m3["grade"] == "S"
               and (p3.get("changeId", 0) > 0
                    or p3.get("status") in (
                        "pending", "deferred")),
               f"grade={m3['grade']} p={p3}")

        # 14) level_view 透明(归因+历史+处罚公示)
        view = await svc.level_view(mid)
        record("评级-查询透明",
               view["grade"] == "S"
               and len(view["gradeHistory"]) == 2
               and len(view["gradeLadder"]) == 5
               and "永不自动" in view[
                   "punishmentPolicy"]
               and isinstance(view["completionRate"],
                              (int, float)),
               f"g={view['grade']} "
               f"h={len(view['gradeHistory'])}")

        # 15) 未认证档案 regrade 拒绝
        await seed_member(2)
        bad = await svc.apply_certification(
            2, "缺资质小铺",
            {**ALL_CHECKS, "service": False}, None)
        ok = False
        try:
            await svc.regrade(bad["merchantId"])
        except ValueError:
            ok = True
        record("评级-未认证拒绝", ok)

        # 16) 档案 404
        ok = False
        try:
            await svc.level_view(9999)
        except KeyError:
            ok = True
        record("评级-档案404", ok)


# ============================================================
# 4. 预演沙盘(6 断言)
# ============================================================

class TestSimulate:
    async def run(self):
        reset_store()
        await seed_member(1)
        svc = XinzhiMerchantService()
        m = await svc.apply_certification(
            1, "竹香臻选旗舰店", ALL_CHECKS, ALL_BONUSES)
        mid = m["merchantId"]

        # 17) 1000 单推演数(确定性: 0.95/0.03/0.92)
        sim = await svc.simulate_voyage(
            fulfillment_rate=0.95, complaint_rate=0.03,
            on_time_rate=0.92, cert_score=54)
        record("沙盘-推演数正确",
               sim["simulatedOrders"] == 1000
               and sim["violations"] == 50
               and sim["complaints"] == 30
               and sim["lateOrders"] == 80,
               f"v={sim['violations']} "
               f"c={sim['complaints']} "
               f"l={sim['lateOrders']}")

        # 18) 轨迹 10 段单调递增收敛稳态
        scores = [t["cumulativeScore"]
                  for t in sim["trajectory"]]
        record("沙盘-轨迹单调收敛",
               len(scores) == 10
               and scores == sorted(scores)
               and abs(scores[-1]
                      - sim["steadyScore"]) < 0.1,
               f"n={len(scores)} "
               f"end={scores[-1]}")

        # 19) TOP3 风险排序(绝对量降序: 迟发80>违约50>客诉30)
        risks = sim["topRisks"]
        counts = [r["simulatedCount"] for r in risks]
        record("沙盘-TOP3风险排序",
               counts == sorted(counts, reverse=True)
               and risks[0]["key"] == "lateOrders"
               and len(risks) == 3,
               f"{counts}")

        # 20) 成长路线图(三杠杆+分数弹性)
        roadmap = sim["roadmap"]
        record("沙盘-成长路线图",
               len(roadmap) == 3
               and {r["lever"] for r in roadmap}
               == {"履约率", "客诉率", "时效达标率"}
               and all(r["scoreGain"] >= 0
                       for r in roadmap),
               f"n={len(roadmap)}")

        # 21) merchantId 模式(实际认证分+档案回写)
        sim2 = await svc.simulate_voyage(
            merchant_id=mid,
            fulfillment_rate=0.98,
            complaint_rate=0.01, on_time_rate=0.97)
        m_after = await svc.repo.get_merchant(mid)
        record("沙盘-商家模式回写",
               sim2["config"]["certScore"] == 54
               and m_after["simulation"]["simId"]
               == sim2["simId"]
               and sim2["steadyGrade"] == "S",
               f"cert={sim2['config']['certScore']} "
               f"g={sim2['steadyGrade']}")

        # 22) 参数校验(区间非法/商家404)
        e1 = e2 = False
        try:
            await svc.simulate_voyage(
                fulfillment_rate=1.5)
        except ValueError:
            e1 = True
        try:
            await svc.simulate_voyage(
                merchant_id=9999)
        except KeyError:
            e2 = True
        record("沙盘-参数校验", e1 and e2, "range/404")


async def main():
    tests = [TestPure(), TestApply(), TestRegrade(),
             TestSimulate()]
    for t in tests:
        await t.run()
    print("=" * 60)
    print("68号 P4 信值·臻选 商家信值体系专项测试")
    print("=" * 60)
    for line in RESULTS:
        print(line)
    print("-" * 60)
    print(f"通过: {PASS} / {PASS + FAIL}")
    if FAIL:
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
