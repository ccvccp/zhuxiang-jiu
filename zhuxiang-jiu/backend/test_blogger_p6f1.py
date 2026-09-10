"""40号·平台流量DV博主模块·P6f-1 引流能力开放专项测试

覆盖(设计文档《40号 P6f 规划方案》§3):
    1. 租用资格: tier 校验/选题推荐(只读+领域过滤)/参数 409
    2. 命名空间隔离: 人设仅自己/脚本越权 404/渲染越权 404/
       指标上报越权 404/漏斗仅自己聚合
    3. 计费与账本: 脚本 1 单位/mock 渲染 5 单位/
       账本 endpoint 维度
    4. real 双闸: 未审批拒绝/admin 审批后放行(50 单位)
    5. 租金建议书: 无用量拒绝/聚合生成(pending)/
       待审幂等拒绝/approve 留痕/reject/重复处置拒绝
    6. pause 降级: 受控写冻结(人设/脚本)/只读不冻结(选题/漏斗)

运行:
    $env:LOCK_MODE="asyncio"; $env:STORE_MODE="asyncio"
    python test_blogger_p6f1.py
"""

import asyncio
import os
import sys


# 确保使用内存模式 + LLM 关闭(规则轨确定性测试)
os.environ["LOCK_MODE"] = "asyncio"
os.environ["STORE_MODE"] = "asyncio"
os.environ.pop("LLM_API_KEY", None)
os.environ["LLM_ENABLED"] = "off"
# mock 槽位/日期固定(测试确定性: 跨槽评分分布无三档保证)
os.environ["BLOGGER_MOCK_SLOT"] = "1"
os.environ["BLOGGER_MOCK_DATE"] = "20260910"

from services.blogger_rental_service import (
    BloggerRentalService, RENTAL_REQUIRED_TIER,
    UNIT_PRICE_SCRIPT, UNIT_PRICE_RENDER_MOCK,
    UNIT_PRICE_RENDER_REAL,
)
from services.blogger_auto_govern_service import (
    BloggerAutoGovernService,
)

PASS = 0
FAIL = 0
RESULTS = []

MEMBER_A = 101   # 会员 A(pro 档租用者)
MEMBER_B = 202   # 会员 B(隔离验证)


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


async def _rented_script(svc: BloggerRentalService,
                         member_id: int,
                         topic: str = "新手选酒避坑指南"
                         ) -> dict:
    """为会员构造 1 个租用脚本(含人设+计费)"""
    persona = await svc.register_renter_persona(
        member_id, f"租用小助手{member_id}")
    return await svc.generate_rental_script(
        member_id, topic, "douyin", persona["personaId"],
        "hook_price_anchor")


# ============================================================
# 1. 租用资格与选题(3 断言)
# ============================================================

class TestAccess:
    async def run(self):
        reset_store()
        svc = BloggerRentalService()

        # 1) tier 硬门(非 pro 拒绝)
        try:
            await svc.require_rental_access(MEMBER_A, "basic")
            ok = False
        except ValueError as exc:
            ok = "pro" in str(exc)
        record("资格-tier硬门", ok)
        ok2 = False
        try:
            await svc.require_rental_access(
                MEMBER_A, RENTAL_REQUIRED_TIER)
            ok2 = True
        except ValueError:
            pass
        record("资格-pro放行", ok2)

        # 2) 选题推荐(只读: 全量+领域过滤——wine 域 3 条)
        r = await svc.recommend_topics(MEMBER_A)
        r2 = await svc.recommend_topics(MEMBER_A, domain="wine")
        record("选题-推荐与过滤",
               r["count"] == 8
               and all(t["domain"] == "wine" for t in r2["topics"])
               and r2["count"] == 3,
               f"n={r['count']} wine={r2['count']}")

        # 3) 参数 409(无效领域)
        try:
            await svc.recommend_topics(MEMBER_A, domain="crypto")
            ok = False
        except ValueError:
            ok = True
        record("选题-参数409", ok)


# ============================================================
# 2. 命名空间隔离(4 断言)
# ============================================================

class TestIsolation:
    async def run(self):
        reset_store()
        svc = BloggerRentalService()
        script_a = await _rented_script(svc, MEMBER_A)

        # 4) 人设仅自己(A 1 个, B 0 个)
        pa = await svc.renter_personas(MEMBER_A)
        pb = await svc.renter_personas(MEMBER_B)
        record("隔离-人设仅自己",
               len(pa) == 1 and len(pb) == 0
               and pa[0]["ownerId"] == MEMBER_A,
               f"pa={len(pa)} pb={len(pb)}")

        # 5) 脚本越权(B 用 A 的 persona 生成 → 404 语义)
        try:
            await svc.generate_rental_script(
                MEMBER_B, "标题", "douyin",
                script_a["personaId"], "hook_price_anchor")
            ok = False
        except KeyError:
            ok = True
        record("隔离-脚本越权404", ok)

        # 6) 渲染越权(B 渲染 A 的脚本 → 404)
        try:
            await svc.render_rental_work(
                MEMBER_B, script_a["scriptId"])
            ok = False
        except KeyError:
            ok = True
        record("隔离-渲染越权404", ok)

        # 7) 作品/漏斗仅自己(A 渲染后 B 看不到)
        work_a = await svc.render_rental_work(
            MEMBER_A, script_a["scriptId"])
        wa = await svc.renter_works(MEMBER_A)
        wb = await svc.renter_works(MEMBER_B)
        fa = await svc.renter_funnel(MEMBER_A)
        fb = await svc.renter_funnel(MEMBER_B)
        try:
            await svc.report_rental_metrics(
                MEMBER_B, work_a["avWorkId"], clicks=10)
            cross = False
        except KeyError:
            cross = True
        record("隔离-作品漏斗指标",
               len(wa) == 1 and len(wb) == 0
               and fa["works"] == 1 and fb["works"] == 0
               and cross,
               f"wa={len(wa)} wb={len(wb)}")


# ============================================================
# 3. 计费与账本(3 断言)
# ============================================================

class TestBilling:
    async def run(self):
        reset_store()
        svc = BloggerRentalService()
        script = await _rented_script(svc, MEMBER_A)

        # 8) 脚本计费 1 单位(endpoint 维度)
        entries = await svc.repo.list_rental_entries(
            member_id=MEMBER_A)
        record("计费-脚本1单位",
               len(entries) == 1
               and entries[0]["endpoint"] == "open/av/scripts"
               and entries[0]["units"] == UNIT_PRICE_SCRIPT,
               f"e={entries}")

        # 9) mock 渲染计费 5 单位
        await svc.render_rental_work(MEMBER_A,
                                      script["scriptId"])
        entries2 = await svc.repo.list_rental_entries(
            member_id=MEMBER_A)
        record("计费-mock渲染5单位",
               len(entries2) == 2
               and entries2[0]["endpoint"] == "open/av/renders"
               and entries2[0]["units"] == UNIT_PRICE_RENDER_MOCK,
               f"n={len(entries2)}")

        # 10) 租用者指标上报(漏斗聚合生效)
        script2 = await _rented_script(svc, MEMBER_A,
                                       "周末家宴配酒攻略")
        work2 = await svc.render_rental_work(
            MEMBER_A, script2["scriptId"])
        await svc.report_rental_metrics(
            MEMBER_A, work2["avWorkId"],
            exposures=500, clicks=30, registered=3,
            activated=2, ordered=1)
        f = await svc.renter_funnel(MEMBER_A)
        record("计费-指标漏斗聚合",
               f["works"] == 2
               and f["withExposure"] == 1
               and f["totals"]["exposures"] == 500
               and f["totals"]["ordered"] == 1,
               f"f={f['totals']}")


# ============================================================
# 4. real 双闸(3 断言)
# ============================================================

class TestRealGate:
    async def run(self):
        reset_store()
        svc = BloggerRentalService()
        script = await _rented_script(svc, MEMBER_A)

        # 11) real 未审批 → 拒绝
        try:
            await svc.render_rental_work(
                MEMBER_A, script["scriptId"], real=True)
            ok = False
        except ValueError as exc:
            ok = "审批" in str(exc)
        record("real-未审批拒绝", ok)

        # 12) admin 审批后放行(50 单位)
        await svc.admin_approve_real_render(script["scriptId"])
        w = await svc.render_rental_work(
            MEMBER_A, script["scriptId"], real=True)
        entries = await svc.repo.list_rental_entries(
            member_id=MEMBER_A, endpoint="open/av/renders")
        record("real-审批后放行",
               w["renderStatus"] == "rendered"
               and entries[0]["units"]
               == UNIT_PRICE_RENDER_REAL,
               f"u={entries[0]['units']}")

        # 13) real 审批 404(脚本不存在)
        try:
            await svc.admin_approve_real_render(99999)
            ok = False
        except KeyError:
            ok = True
        record("real-审批404", ok)


# ============================================================
# 5. 租金建议书(6 断言)
# ============================================================

class TestBills:
    async def run(self):
        reset_store()
        svc = BloggerRentalService()

        # 14) 无用量拒绝
        try:
            await svc.generate_rental_bill(MEMBER_A)
            ok = False
        except ValueError:
            ok = True
        record("账单-无用量拒绝", ok)

        # 15) 聚合生成(pending; 1+5=6 单位×0.1=0.6)
        script = await _rented_script(svc, MEMBER_A)
        await svc.render_rental_work(MEMBER_A,
                                      script["scriptId"])
        bill = await svc.generate_rental_bill(MEMBER_A)
        record("账单-聚合生成",
               bill["status"] == "pending"
               and bill["scriptUnits"] == 1
               and bill["renderUnits"] == 5
               and bill["totalUnits"] == 6
               and bill["amount"] == 0.6,
               f"b={bill}")

        # 16) 待审幂等拒绝
        try:
            await svc.generate_rental_bill(MEMBER_A)
            ok = False
        except ValueError:
            ok = True
        record("账单-待审幂等拒绝", ok)

        # 17) approve 留痕(47号口径注明)
        approved = await svc.approve_rental_bill(bill["billId"])
        record("账单-approve留痕",
               approved["status"] == "approved"
               and approved["approvedAt"] != ""
               and "47号" in approved["note"],
               f"n={approved['note']}")

        # 18) 重复处置拒绝(approve+reject)
        errs = 0
        try:
            await svc.approve_rental_bill(bill["billId"])
        except ValueError:
            errs += 1
        try:
            await svc.reject_rental_bill(bill["billId"])
        except ValueError:
            errs += 1
        record("账单-重复处置拒绝", errs == 2, f"errs={errs}")

        # 19) reject 轨(新账单驳回)
        script2 = await _rented_script(svc, MEMBER_B)
        await svc.generate_rental_bill(MEMBER_B)
        bills_b = await svc.repo.list_rental_bills(
            member_id=MEMBER_B)
        rejected = await svc.reject_rental_bill(
            bills_b[0]["billId"])
        record("账单-reject轨",
               rejected["status"] == "rejected",
               f"s={rejected['status']}")

        # 20) 账单 404
        try:
            await svc.approve_rental_bill(99999)
            ok = False
        except KeyError:
            ok = True
        record("账单-404", ok)


# ============================================================
# 6. pause 降级(2 断言)
# ============================================================

class TestPauseDowngrade:
    async def run(self):
        reset_store()
        svc = BloggerRentalService()
        gov = BloggerAutoGovernService()

        # 21) 受控写冻结(人设/脚本) + 只读不冻结(选题/漏斗)
        await gov.pause_autonomy("P6f-1 降级测试")
        try:
            await svc.register_renter_persona(MEMBER_A, "冻结人设")
            write_frozen = False
        except ValueError as exc:
            write_frozen = "只读" in str(exc) or "暂停" in str(exc)
        topics = await svc.recommend_topics(MEMBER_A)
        funnel = await svc.renter_funnel(MEMBER_A)
        record("降级-受控写冻结只读不冻结",
               write_frozen
               and topics["count"] == 8
               and funnel["works"] == 0,
               f"w={write_frozen} t={topics['count']}")
        await gov.resume_autonomy()

        # 22) 恢复后受控写放行
        persona = await svc.register_renter_persona(
            MEMBER_A, "恢复人设")
        record("降级-恢复后放行",
               persona["ownerId"] == MEMBER_A,
               f"p={persona.get('ownerId')}")


async def main():
    tests = [TestAccess(), TestIsolation(), TestBilling(),
             TestRealGate(), TestBills(), TestPauseDowngrade()]
    for t in tests:
        await t.run()
    print("=" * 60)
    print("40号 P6f-1 引流能力开放专项测试")
    print("=" * 60)
    for line in RESULTS:
        print(line)
    print("-" * 60)
    print(f"通过: {PASS} / {PASS + FAIL}")
    if FAIL:
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
