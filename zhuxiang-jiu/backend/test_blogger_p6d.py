"""40号·平台流量DV博主模块·P6d 自治理扩展专项测试

覆盖(设计文档《40号 P6 升级方案》§6):
    1. 六层漏斗: 全层聚合(曝光→完播→点击→注册→激活→首单)/
       空态/总量合计
    2. 共鸣度看板: 逐作品共鸣/低共鸣预警/高共鸣无预警
    3. 看板聚合: 六区(自治/漏斗/共鸣/策略/干预/自愈)/
       pause 状态联动/自愈流水聚合
    4. AV 专项自愈: 转码分类/音画委托重渲染/上传分类/
       P5d 兜底(限流换时段/下架人工)/耗尽转人工/
       审计留痕/pause 拒绝
    5. 决策可解释: 五链路回放/404 语义

运行:
    $env:LOCK_MODE="asyncio"; $env:STORE_MODE="asyncio"
    python test_blogger_p6d.py
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

from services.blogger_av_govern_service import (
    BloggerAVGovernService, AV_HEAL_RETRY_MAX, RESONANCE_WARN_LINE,
)
from services.blogger_av_publish_service import (
    BloggerAVPublishService,
)
from services.blogger_av_create_service import (
    BloggerAVCreateService,
)
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


async def _published_work(publish_svc: BloggerAVPublishService,
                          platform: str = "douyin") -> dict:
    """构造 1 个已发布 AV 作品"""
    create_svc = BloggerAVCreateService(
        repo=publish_svc.repo, blogger_service=publish_svc.svc)
    persona = await create_svc.register_persona(
        "竹小香", "original_ip", voice_style="medium",
        tone_style="warm")
    script = await create_svc.generate_script(
        "选酒避坑", platform, persona["personaId"],
        "hook_price_anchor")
    work = await create_svc.render_work(script["scriptId"])
    r = await publish_svc.publish_av_work(work["avWorkId"])
    assert r["published"] is True
    return await publish_svc.repo.get_av_work(work["avWorkId"])


# ============================================================
# 1. 六层漏斗(3 断言)
# ============================================================

class TestFunnel:
    async def run(self):
        reset_store()
        pub = BloggerAVPublishService()
        gov = BloggerAVGovernService()

        # 1) 空态(published=0 全零)
        f0 = await gov.av_funnel()
        record("漏斗-空态",
               f0["published"] == 0
               and f0["withExposure"] == 0
               and f0["avgCompletionRate"] == 0.0,
               f"f={f0}")

        # 2) 全层聚合(单作品六层全触达)
        w = await _published_work(pub)
        await pub.report_av_work_metrics(
            w["avWorkId"], exposures=1000, completion_rate=0.6,
            clicks=80, registered=10, activated=5, ordered=2)
        f = await gov.av_funnel()
        record("漏斗-六层聚合",
               f["published"] == 1
               and f["withExposure"] == 1
               and f["withCompletion"] == 1
               and f["withClicks"] == 1
               and f["withRegistered"] == 1
               and f["withActivated"] == 1
               and f["withOrdered"] == 1,
               f"f={f}")

        # 3) 总量合计 + 平均完播
        record("漏斗-总量合计",
               f["totals"]["exposures"] == 1000
               and f["totals"]["clicks"] == 80
               and f["totals"]["registered"] == 10
               and f["totals"]["activated"] == 5
               and f["totals"]["ordered"] == 2
               and f["avgCompletionRate"] == 0.6,
               f"t={f['totals']}")


# ============================================================
# 2. 共鸣度看板(3 断言)
# ============================================================

class TestResonance:
    async def run(self):
        reset_store()
        pub = BloggerAVPublishService()
        gov = BloggerAVGovernService()

        # 4) 低共鸣预警(共鸣 < 0.3 列预警)
        w1 = await _published_work(pub)
        await pub.report_av_work_metrics(
            w1["avWorkId"], completion_rate=0.1, share_rate=0.0,
            favorite_rate=0.0, comment_positive=0.0)
        r = await gov.resonance_view()
        record("共鸣-低共鸣预警",
               r["sampled"] == 1
               and r["avgResonance"] < RESONANCE_WARN_LINE
               and len(r["lowResonanceWorks"]) == 1
               and r["lowResonanceWorks"][0]["avWorkId"]
               == w1["avWorkId"],
               f"r={r}")

        # 5) 高共鸣无预警(完播0.6+分享0.05+收藏0.08+正面1.0 → 1.0)
        w2 = await _published_work(pub)
        await pub.report_av_work_metrics(
            w2["avWorkId"], completion_rate=0.6, share_rate=0.05,
            favorite_rate=0.08, comment_positive=1.0)
        r2 = await gov.resonance_view()
        warn_ids = [x["avWorkId"]
                    for x in r2["lowResonanceWorks"]]
        record("共鸣-高共鸣无预警",
               w2["avWorkId"] not in warn_ids
               and r2["avgResonance"] > 0,
               f"warn={warn_ids}")

        # 6) 趋势榜(共鸣降序 TOP)
        trend_ids = [x["avWorkId"] for x in r2["trend"]]
        record("共鸣-趋势榜降序",
               trend_ids.index(w2["avWorkId"])
               < trend_ids.index(w1["avWorkId"]),
               f"trend={trend_ids}")


# ============================================================
# 3. 看板聚合(3 断言)
# ============================================================

class TestDashboard:
    async def run(self):
        reset_store()
        pub = BloggerAVPublishService()
        gov = BloggerAVGovernService()

        # 7) 六区聚合(自治/漏斗/共鸣/策略/干预/自愈)
        w = await _published_work(pub)
        await pub.report_av_work_metrics(
            w["avWorkId"], exposures=100, completion_rate=0.5,
            clicks=10)
        d = await gov.av_evolution_dashboard()
        record("看板-六区聚合",
               all(k in d for k in
                   ("autonomy", "funnel", "resonance",
                    "strategies", "interventions", "heals"))
               and d["funnel"]["published"] == 1
               and d["autonomy"]["paused"] is False,
               f"keys={list(d.keys())}")

        # 8) pause 状态联动(P5d 状态共享)
        gov5 = BloggerAutoGovernService()
        await gov5.pause_autonomy("P6d 看板测试")
        d2 = await gov.av_evolution_dashboard()
        record("看板-pause联动",
               d2["autonomy"]["paused"] is True
               and "P6d 看板测试" in d2["autonomy"]["reason"],
               f"a={d2['autonomy']}")
        await gov5.resume_autonomy()

        # 9) 自愈流水聚合(auto_heal 审计含 avWorkId)
        await gov.heal_av_failure(w["avWorkId"], "转码失败")
        d3 = await gov.av_evolution_dashboard()
        record("看板-自愈流水聚合",
               len(d3["heals"]) >= 1
               and d3["heals"][0]["category"]
               == "transcode_failed",
               f"h={d3['heals']}")


# ============================================================
# 4. AV 专项自愈(7 断言)
# ============================================================

class TestAVHeal:
    async def run(self):
        reset_store()
        pub = BloggerAVPublishService()
        gov = BloggerAVGovernService()

        # 10) 转码分类 → 换渲染参数重试
        w = await _published_work(pub)
        r = await gov.heal_av_failure(w["avWorkId"], "转码失败")
        record("自愈-转码重试",
               r["category"] == "transcode_failed"
               and r["action"]["kind"] == "retry",
               f"r={r}")

        # 11) 音画不同步 → 委托 P6c 重渲染
        r2 = await gov.heal_av_failure(w["avWorkId"],
                                        "音画不同步")
        record("自愈-音画重渲染",
               r2["category"] == "audio_desync"
               and r2["action"]["kind"] == "re_rendered",
               f"r={r2['action']}")

        # 12) 上传超时分类 + P5d 兜底(限流/下架)
        r3 = await gov.heal_av_failure(w["avWorkId"],
                                        "上传超时")
        w2 = await _published_work(pub)
        r4 = await gov.heal_av_failure(w2["avWorkId"],
                                        "rate limit")
        r5 = await gov.heal_av_failure(w2["avWorkId"],
                                        "content removed")
        record("自愈-上传+P5d兜底",
               r3["category"] == "upload_timeout"
               and r3["action"]["kind"] == "retry"
               and r4["category"] == "rate_limit"
               and r4["action"]["kind"] == "retry"
               and r5["category"] == "content_takedown"
               and r5["action"]["kind"] == "manual_queue",
               f"r3={r3['category']} r4={r4['category']}")

        # 13) 耗尽转人工(永不静默丢弃)
        w3 = await _published_work(pub)
        ok = False
        try:
            await gov.heal_av_failure(
                w3["avWorkId"], "转码失败",
                attempt=AV_HEAL_RETRY_MAX + 1)
        except ValueError as exc:
            ok = "耗尽" in str(exc)
        w3d = await pub.repo.get_av_work(w3["avWorkId"])
        record("自愈-耗尽转人工",
               ok and w3d.get("healStatus") == "manual_queue",
               f"hs={w3d.get('healStatus')}")

        # 14) 审计留痕(auto_heal 动作落 blogger_audits)
        heals = [a for a in await pub.repo.list_audits(limit=100)
                 if a.get("action") == "auto_heal"
                 and "avWorkId" in (a.get("detail") or {})]
        record("自愈-审计留痕", len(heals) >= 5,
               f"n={len(heals)}")

        # 15) pause 拒绝(P6c 口径一致)
        w4 = await _published_work(pub)
        gov5 = BloggerAutoGovernService()
        await gov5.pause_autonomy("P6d 自愈测试")
        try:
            await gov.heal_av_failure(w4["avWorkId"], "转码失败")
            ok = False
        except ValueError as exc:
            ok = "已暂停" in str(exc)
        record("自愈-pause拒绝", ok)
        await gov5.resume_autonomy()

        # 16) 404 语义(作品不存在)
        try:
            await gov.heal_av_failure(99999, "转码失败")
            ok = False
        except KeyError:
            ok = True
        record("自愈-404语义", ok)


# ============================================================
# 5. 决策可解释(2 断言)
# ============================================================

class TestExplain:
    async def run(self):
        reset_store()
        pub = BloggerAVPublishService()
        gov = BloggerAVGovernService()

        # 17) 五链路回放(形式→人设→合规内生→渲染→发布)
        w = await _published_work(pub)
        ex = await gov.av_decision_explain(w["avWorkId"])
        steps = [c["step"] for c in ex["chain"]]
        record("可解释-五链路回放",
               steps == ["form_decision", "persona_match",
                         "compliance_intrinsic", "render",
                         "publish"]
               and "short_video" in ex["chain"][0]["explain"]
               and "竹小香" in ex["chain"][1]["explain"]
               and "已嵌入" in ex["chain"][2]["explain"],
               f"steps={steps}")

        # 18) 404 语义
        try:
            await gov.av_decision_explain(99999)
            ok = False
        except KeyError:
            ok = True
        record("可解释-404语义", ok)


async def main():
    tests = [TestFunnel(), TestResonance(), TestDashboard(),
             TestAVHeal(), TestExplain()]
    for t in tests:
        await t.run()
    print("=" * 60)
    print("40号 P6d 自治理扩展专项测试")
    print("=" * 60)
    for line in RESULTS:
        print(line)
    print("-" * 60)
    print(f"通过: {PASS} / {PASS + FAIL}")
    if FAIL:
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
