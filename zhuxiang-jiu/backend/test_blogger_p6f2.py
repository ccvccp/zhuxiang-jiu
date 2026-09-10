"""40号·平台流量DV博主模块·P6f-2 信值绩效专项测试

覆盖(设计文档《40号 P6f 规划方案》§4):
    1. 绩效公式: 五分项权重和/满分分档/零分分档/
       共鸣权重不低于单一转化项铁律
    2. persona 轨: 人设月报聚合(漏斗/共鸣/合规/
       授权/自愈五分项落库)
    3. creator 轨: 创作者月报(P6e 转发聚合)
    4. 结算建议: settle 生成(pending+建议入账)/
       approve 留痕/reject/重复处置拒绝/404
    5. 防操纵: 无作品主体(低分观察档)

运行:
    $env:LOCK_MODE="asyncio"; $env:STORE_MODE="asyncio"
    python test_blogger_p6f2.py
"""

import asyncio
import os
import sys


# 确保使用内存模式 + LLM 关闭(规则轨确定性测试)
os.environ["LOCK_MODE"] = "asyncio"
os.environ["STORE_MODE"] = "asyncio"
os.environ.pop("LLM_API_KEY", None)
os.environ["LLM_ENABLED"] = "off"
# mock 槽位/日期固定(测试确定性)
os.environ["BLOGGER_MOCK_SLOT"] = "1"
os.environ["BLOGGER_MOCK_DATE"] = "20260910"

from services.blogger_performance_service import (
    BloggerPerformanceService, classify_grade,
    PERF_W_FUNNEL, PERF_W_RESONANCE, PERF_W_COMPLIANCE,
    PERF_W_LICENSE, PERF_W_HEAL,
    GRADE_GOLD, GRADE_SILVER, GRADE_WATCH,
    SUBJECT_PERSONA, SUBJECT_CREATOR,
)
from services.blogger_av_create_service import (
    BloggerAVCreateService,
)
from services.blogger_av_publish_service import (
    BloggerAVPublishService,
)

PASS = 0
FAIL = 0
RESULTS = []

GOOD_META = {"originUrl": "https://example.com/w/1",
             "creatorVerified": True, "platform": "douyin"}


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


async def _persona_with_works(
        n_works: int = 2) -> dict:
    """构造带作品的 AI 人设(指标全满档)"""
    create = BloggerAVCreateService()
    pub = BloggerAVPublishService(
        repo=create.repo, blogger_service=create.svc)
    persona = await create.register_persona(
        "绩效测试人设", "original_ip")
    for i in range(n_works):
        script = await create.generate_script(
            f"选酒避坑{i}", "douyin", persona["personaId"],
            "hook_price_anchor")
        work = await create.render_work(script["scriptId"])
        r = await pub.publish_av_work(work["avWorkId"])
        assert r["published"] is True
        await pub.report_av_work_metrics(
            work["avWorkId"], exposures=1000,
            completion_rate=0.6, share_rate=0.05,
            favorite_rate=0.08, comment_positive=1.0,
            clicks=80, registered=20, activated=20, ordered=10)
    return persona


async def _creator_with_fwd(
        svc_fwd, n: int = 2) -> str:
    """构造带转发内容的创作者(P6e 链)"""
    auth = await svc_fwd.register_auth(
        f"perf-src-{n}", "mcn", "绩效创作者", "私信", "MCN机构",
        revenue_share=0.3)
    for i in range(n):
        fwd = await svc_fwd.deep_review(
            auth["authId"], f"选酒避坑指南{i}",
            source_meta=GOOD_META)
        await svc_fwd.report_fwd_metrics(
            fwd["fwdId"], play_count=1000, convert_count=10)
    return "绩效创作者"


# ============================================================
# 1. 绩效公式与分档(4 断言)
# ============================================================

class TestFormula:
    async def run(self):
        reset_store()

        # 1) 五分项权重和 = 1.0
        record("公式-权重和",
               abs(PERF_W_FUNNEL + PERF_W_RESONANCE
                   + PERF_W_COMPLIANCE + PERF_W_LICENSE
                   + PERF_W_HEAL - 1.0) < 1e-9,
               f"w={PERF_W_FUNNEL + PERF_W_RESONANCE}")

        # 2) 共鸣权重不低于任何单一转化项铁律
        #    (漏斗为复合转化项除外——其内部含两项)
        record("公式-共鸣权重铁律",
               PERF_W_RESONANCE >= PERF_W_COMPLIANCE
               and PERF_W_RESONANCE >= PERF_W_LICENSE
               and PERF_W_RESONANCE >= PERF_W_HEAL,
               f"res={PERF_W_RESONANCE}")

        # 3) 分档阈值(80 金/60 银/观察)
        record("公式-分档阈值",
               classify_grade(80) == GRADE_GOLD
               and classify_grade(79.9) == GRADE_SILVER
               and classify_grade(60) == GRADE_SILVER
               and classify_grade(59.9) == GRADE_WATCH,
               f"g={classify_grade(80)}")

        # 4) 主体类型校验
        svc = BloggerPerformanceService()
        try:
            await svc.generate_report(
                "robot", 1, "名")
            ok = False
        except ValueError:
            ok = True
        record("公式-主体类型409", ok)


# ============================================================
# 2. persona 轨月报(4 断言)
# ============================================================

class TestPersonaReport:
    async def run(self):
        reset_store()
        svc = BloggerPerformanceService()
        persona = await _persona_with_works(2)

        # 5) 人设月报五分项落库(全满档)
        report = await svc.generate_report(
            SUBJECT_PERSONA, persona["personaId"],
            persona["name"])
        record("人设-五分项满档",
               report["funnelContrib"] == 1.0
               and report["resonanceQuality"] == 1.0
               and report["complianceScore"] > 0.9
               and report["licenseContrib"] == 1.0
               and report["healStability"] == 1.0,
               f"f={report}")

        # 6) 总分与分档(百分制: 五分项加权和×100)
        expected = round(100 * (
            0.35 * 1.0 + 0.25 * 1.0
            + 0.20 * report["complianceScore"]
            + 0.10 * 1.0 + 0.10 * 1.0), 4)
        record("人设-总分分档",
               abs(report["totalScore"] - expected) < 0.5
               and report["grade"] == GRADE_GOLD
               and report["quotaCoefficient"] == 1.2,
               f"t={report['totalScore']} exp={expected}")

        # 7) 双轨隔离(persona 轨与 creator 轨同月共存)
        from services.blogger_fwd_service import BloggerFwdService
        fwd = BloggerFwdService()
        await _creator_with_fwd(fwd, 2)
        r2 = await svc.generate_report(
            SUBJECT_CREATOR, 1, "绩效创作者")
        records = await svc.repo.list_perf_reports(limit=10)
        types = {r["subjectType"] for r in records}
        record("双轨-同月共存",
               types == {SUBJECT_PERSONA, SUBJECT_CREATOR}
               and r2["monthKey"] == report["monthKey"],
               f"types={types}")


# ============================================================
# 3. creator 轨月报(3 断言)
# ============================================================

class TestCreatorReport:
    async def run(self):
        reset_store()
        svc = BloggerPerformanceService()
        from services.blogger_fwd_service import BloggerFwdService
        fwd = BloggerFwdService()
        await _creator_with_fwd(fwd, 3)

        # 8) creator 月报(转化 30/基数 10 → funnel=1.0;
        #    深审 auto 占比 1.0; 授权 active)
        report = await svc.generate_report(
            SUBJECT_CREATOR, 1, "绩效创作者")
        record("创作者-五分项",
               report["funnelContrib"] == 1.0
               and report["complianceScore"] == 1.0
               and report["licenseContrib"] == 1.0
               and report["healStability"] == 1.0
               and report["resonanceQuality"] == 1.0,
               f"f={report}")

        # 9) 防操纵: 无作品主体 → 低分观察档
        r2 = await svc.generate_report(
            SUBJECT_PERSONA, 999, "空主体")
        record("防操纵-空主体观察档",
               r2["funnelContrib"] == 0.0
               and r2["resonanceQuality"] == 0.0
               and r2["totalScore"] < 60
               and r2["grade"] == GRADE_WATCH,
               f"t={r2['totalScore']}")

        # 10) 撤回授权 → licenseContrib 降为 0
        auths = await fwd.repo.list_fwd_auths(limit=10)
        await fwd.revoke_auth(auths[0]["authId"])
        r3 = await svc.generate_report(
            SUBJECT_CREATOR, 1, "绩效创作者")
        record("创作者-撤回降档",
               r3["licenseContrib"] == 0.0
               and r3["healStability"] < 1.0,
               f"l={r3['licenseContrib']}")


# ============================================================
# 4. 结算建议(4 断言)
# ============================================================

class TestSettlement:
    async def run(self):
        reset_store()
        svc = BloggerPerformanceService()
        persona = await _persona_with_works(1)
        report = await svc.generate_report(
            SUBJECT_PERSONA, persona["personaId"],
            persona["name"])

        # 11) settle 生成(pending 保持+建议入账=总分×0.1)
        s = await svc.settle_report(report["reportId"])
        expected_pay = round(report["totalScore"] * 0.1, 2)
        record("结算-建议生成",
               s["settleStatus"] == "pending"
               and s["suggestedPayout"] == expected_pay
               and "47号" in s["settleNote"],
               f"p={s['suggestedPayout']}")

        # 12) approve 留痕
        a = await svc.approve_report(report["reportId"])
        record("结算-approve留痕",
               a["settleStatus"] == "approved"
               and "47号" in a["settleNote"],
               f"n={a['settleNote'][-30:]}")

        # 13) 重复处置拒绝(approve+reject+settle)
        errs = 0
        for fn in (svc.approve_report, svc.reject_report,
                   svc.settle_report):
            try:
                await fn(report["reportId"])
            except ValueError:
                errs += 1
        record("结算-重复处置拒绝", errs == 3, f"errs={errs}")

        # 14) 404 + reject 轨
        try:
            await svc.approve_report(99999)
            ok = False
        except KeyError:
            ok = True
        persona2 = await _persona_with_works(1)
        r2 = await svc.generate_report(
            SUBJECT_PERSONA, persona2["personaId"],
            persona2["name"])
        rej = await svc.reject_report(r2["reportId"])
        record("结算-404与reject轨",
               ok and rej["settleStatus"] == "rejected")


async def main():
    tests = [TestFormula(), TestPersonaReport(),
             TestCreatorReport(), TestSettlement()]
    for t in tests:
        await t.run()
    print("=" * 60)
    print("40号 P6f-2 信值绩效专项测试")
    print("=" * 60)
    for line in RESULTS:
        print(line)
    print("-" * 60)
    print(f"通过: {PASS} / {PASS + FAIL}")
    if FAIL:
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
