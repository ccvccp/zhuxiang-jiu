"""40号·平台流量DV博主模块·P6f-4 可信度评估专项测试

覆盖(设计文档《40号 P6f 规划方案》§6):
    1. 五因子公式: 权重和/满分分档/阈值分档/主体类型 409
    2. persona 评估: 全满档五因子/总分分档卓越/联动系数
    3. 检测器: spike 命中/drop 命中/正常零告警/
       违规激增/小样本防误报
    4. 主体清单: 三类主体聚合/观测面不受 pause 影响

运行:
    $env:LOCK_MODE="asyncio"; $env:STORE_MODE="asyncio"
    python test_blogger_p6f4.py
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

from services.blogger_trust_service import (
    BloggerTrustService, classify_trust,
    detect_anomalies, detect_violation_surge,
    TRUST_W_DELIVERY, TRUST_W_COMPLIANCE,
    TRUST_W_RESONANCE, TRUST_W_LICENSE, TRUST_W_HEAL,
    TRUST_EXCELLENT, TRUST_GOOD, TRUST_WATCH,
    TRUST_RESTRICTED, TRUST_COEFFICIENTS,
    SUBJECT_PERSONA, SUBJECT_CREATOR, SUBJECT_MEMBER,
)
from services.blogger_av_create_service import (
    BloggerAVCreateService,
)
from services.blogger_av_publish_service import (
    BloggerAVPublishService,
)
from services.blogger_rental_service import BloggerRentalService
from services.blogger_auto_govern_service import (
    BloggerAutoGovernService,
)

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


async def _full_persona() -> dict:
    """构造全满档人设(2 作品已发布+指标满)"""
    create = BloggerAVCreateService()
    pub = BloggerAVPublishService(
        repo=create.repo, blogger_service=create.svc)
    persona = await create.register_persona(
        "可信人设", "original_ip")
    for i in range(2):
        script = await create.generate_script(
            f"选酒指南{i}", "douyin", persona["personaId"],
            "hook_price_anchor")
        work = await create.render_work(script["scriptId"])
        r = await pub.publish_av_work(work["avWorkId"])
        assert r["published"] is True
        await pub.report_av_work_metrics(
            work["avWorkId"], completion_rate=0.6,
            share_rate=0.05, favorite_rate=0.08,
            comment_positive=1.0)
    return persona


# ============================================================
# 1. 五因子公式与分档(4 断言)
# ============================================================

class TestFormula:
    async def run(self):
        reset_store()

        # 1) 五因子权重和 = 1.0
        record("公式-权重和",
               abs(TRUST_W_DELIVERY + TRUST_W_COMPLIANCE
                   + TRUST_W_RESONANCE + TRUST_W_LICENSE
                   + TRUST_W_HEAL - 1.0) < 1e-9)

        # 2) 分档阈值(85/70/50)
        record("公式-分档阈值",
               classify_trust(85) == TRUST_EXCELLENT
               and classify_trust(84.9) == TRUST_GOOD
               and classify_trust(70) == TRUST_GOOD
               and classify_trust(49.9) == TRUST_RESTRICTED
               and classify_trust(50) == TRUST_WATCH,
               f"t={classify_trust(85)}")

        # 3) 系数映射(1.2/1.0/0.8/0.5)
        record("公式-系数映射",
               TRUST_COEFFICIENTS[TRUST_EXCELLENT] == 1.2
               and TRUST_COEFFICIENTS[TRUST_GOOD] == 1.0
               and TRUST_COEFFICIENTS[TRUST_WATCH] == 0.8
               and TRUST_COEFFICIENTS[TRUST_RESTRICTED] == 0.5)

        # 4) 主体类型 409
        svc = BloggerTrustService()
        try:
            await svc.evaluate("robot", 1)
            ok = False
        except ValueError:
            ok = True
        record("公式-主体类型409", ok)


# ============================================================
# 2. persona 评估(3 断言)
# ============================================================

class TestPersonaEval:
    async def run(self):
        reset_store()
        svc = BloggerTrustService()
        persona = await _full_persona()

        # 5) 全满档五因子(履约/共鸣/授权/自愈全 1.0)
        r = await svc.evaluate(
            SUBJECT_PERSONA, persona["personaId"])
        record("人设-五因子满档",
               r["delivery"] == 1.0
               and r["resonance"] == 1.0
               and r["license"] == 1.0
               and r["heal"] == 1.0
               and r["compliance"] > 0.9,
               f"f={r}")

        # 6) 总分分档卓越 + 联动系数
        expected = round(100 * (
            0.30 * 1.0 + 0.25 * r["compliance"]
            + 0.20 * 1.0 + 0.15 * 1.0 + 0.10 * 1.0), 4)
        record("人设-总分卓越档",
               abs(r["trustScore"] - expected) < 0.5
               and r["tier"] == TRUST_EXCELLENT
               and r["quotaCoefficient"] == 1.2,
               f"t={r['trustScore']} exp={expected}")

        # 7) 联动建议书口径注明(永不自动)
        record("人设-联动口径",
               "46号" in r["linkageNote"]
               and "永不自动" in r["linkageNote"],
               f"n={r['linkageNote']}")


# ============================================================
# 3. 检测器(5 断言)
# ============================================================

class TestDetectors:
    async def run(self):
        reset_store()

        # 8) spike 命中(μ=50σ≈0 历史, 当前 100)
        hist = [50.0, 50.0, 50.0, 50.0]
        record("检测-spike命中",
               "spike" in detect_anomalies(hist, 100.0),
               f"a={detect_anomalies(hist, 100.0)}")

        # 9) drop 命中(μ=50 骤降至 25, 绝对值 ≥20 防误报门过)
        record("检测-drop命中",
               "drop" in detect_anomalies(hist, 25.0),
               f"a={detect_anomalies(hist, 25.0)}")

        # 10) 正常零告警(带方差历史 μ=50σ≈1.4, 当前 52 在带内)
        hist_v = [48.0, 50.0, 52.0, 50.0]
        record("检测-正常零告警",
               detect_anomalies(hist_v, 52.0) == [],
               f"a={detect_anomalies(hist_v, 52.0)}")

        # 11) 小样本防误报(历史 <3 跳过检测)
        record("检测-小样本防误报",
               detect_anomalies([50.0], 100.0) == [])

        # 12) 违规激增(×3 且样本 ≥20)
        record("检测-违规激增",
               detect_violation_surge(10, 30) is True
               and detect_violation_surge(10, 25) is False
               and detect_violation_surge(5, 15) is False,
               f"s={detect_violation_surge(10, 30)}")


# ============================================================
# 4. 主体清单与观测面(4 断言)
# ============================================================

class TestSubjects:
    async def run(self):
        reset_store()
        # 底座: 人设 + 转发创作者 + 租用会员
        await _full_persona()
        from services.blogger_fwd_service import BloggerFwdService
        fwd = BloggerFwdService()
        auth = await fwd.register_auth(
            "trust-src", "mcn", "可信创作者", "私信", "MCN")
        GOOD_META = {"originUrl": "https://e.com/w/1",
                     "creatorVerified": True,
                     "platform": "douyin"}
        await fwd.deep_review(
            auth["authId"], "选酒指南", source_meta=GOOD_META)
        rental = BloggerRentalService()
        persona_r = await rental.register_renter_persona(
            501, "租用主体人设")
        await rental.generate_rental_script(
            501, "选酒指南", "douyin", persona_r["personaId"],
            "hook_price_anchor")

        svc = BloggerTrustService()
        subjects = await svc.list_subjects()
        types = {s["subjectType"] for s in subjects}

        # 13) 三类主体聚合(persona/creator/member)
        record("清单-三类主体",
               types == {SUBJECT_PERSONA, SUBJECT_CREATOR,
                         SUBJECT_MEMBER},
               f"t={types}")

        # 14) 创作者去重(1 授权 1 创作者名)
        creators = [s for s in subjects
                    if s["subjectType"] == SUBJECT_CREATOR]
        record("清单-创作者去重",
               len(creators) == 1
               and creators[0]["name"] == "可信创作者",
               f"c={creators}")

        # 15) 观测面不受 pause 影响(pause 后清单/评估照常)
        gov = BloggerAutoGovernService()
        await gov.pause_autonomy("P6f-4 观测面测试")
        subjects2 = await svc.list_subjects()
        r2 = await svc.evaluate(SUBJECT_CREATOR,
                                auth["authId"])
        record("观测-pause不影响",
               len(subjects2) == len(subjects)
               and r2["trustScore"] >= 0,
               f"n={len(subjects2)}")
        await gov.resume_autonomy()

        # 16) member 轨评估(租用会员)
        r3 = await svc.evaluate(SUBJECT_MEMBER, 501)
        record("评估-member轨",
               r3["subjectType"] == SUBJECT_MEMBER
               and r3["delivery"] >= 0.0,
               f"r={r3['subjectType']}")


async def main():
    tests = [TestFormula(), TestPersonaEval(),
             TestDetectors(), TestSubjects()]
    for t in tests:
        await t.run()
    print("=" * 60)
    print("40号 P6f-4 可信度评估专项测试")
    print("=" * 60)
    for line in RESULTS:
        print(line)
    print("-" * 60)
    print(f"通过: {PASS} / {PASS + FAIL}")
    if FAIL:
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
