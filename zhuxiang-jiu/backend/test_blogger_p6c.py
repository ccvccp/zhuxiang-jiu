"""40号·平台流量DV博主模块·P6c 音视频自主发布调度器专项测试

覆盖(设计文档《40号 P6 升级方案》§5):
    1. 渲染参数表: 六平台种子/参数正确/播客纯音频/热更新/无效平台
    2. 完播加权黄金时段: 观测值公式/EMA 收敛/TOP3 时段/
       冷启动回退静态窗
    3. 发布: 常规直接发布/新平台首发审批(前10条)/放行后发布/
       重复发布拒绝/未渲染拒绝
    4. 互动自愈: 完播低迷推广建议/高频疑问FAQ/负面仅建议/
       音画不同步自愈重渲染/耗尽转人工
    5. 推广: 低预算自动/高预算pending/幂等拒绝/pause拒绝

运行:
    $env:LOCK_MODE="asyncio"; $env:STORE_MODE="asyncio"
    python test_blogger_p6c.py
"""

import asyncio
import os
import sys


# 确保使用内存模式 + LLM 关闭(规则轨确定性测试)
os.environ["LOCK_MODE"] = "asyncio"
# mock 槽位/日期固定(测试确定性: 跨槽评分分布无三档保证)
os.environ["BLOGGER_MOCK_SLOT"] = "1"
os.environ["BLOGGER_MOCK_DATE"] = "20260910"
os.environ["STORE_MODE"] = "asyncio"
os.environ.pop("LLM_API_KEY", None)
os.environ["LLM_ENABLED"] = "off"

from services.blogger_av_publish_service import (
    BloggerAVPublishService, RENDER_PROFILE_SEEDS,
    NEW_PLATFORM_FIRST_N, AV_REHEAL_RETRY_MAX,
    completion_weighted_obs,
)
from services.blogger_av_create_service import (
    BloggerAVCreateService,
)
from services.blogger_auto_govern_service import (
    BloggerAutoGovernService,
)
from services.blogger_auto_publish_service import (
    BOOST_AUTO_MAX,
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


async def _rendered_work(publish_svc: BloggerAVPublishService,
                         platform: str = "douyin") -> dict:
    """构造 1 个已渲染未发布 AV 作品"""
    create_svc = BloggerAVCreateService(
        repo=publish_svc.repo, blogger_service=publish_svc.svc)
    persona = await create_svc.register_persona(
        "竹小香", "original_ip", voice_style="medium",
        tone_style="warm")
    script = await create_svc.generate_script(
        "选酒避坑", platform, persona["personaId"],
        "hook_price_anchor")
    return await create_svc.render_work(script["scriptId"])


async def _published_work(
        publish_svc: BloggerAVPublishService,
        platform: str = "douyin") -> dict:
    """构造 1 个已发布 AV 作品(带指标)"""
    work = await _rendered_work(publish_svc, platform)
    r = await publish_svc.publish_av_work(work["avWorkId"])
    assert r["published"] is True
    return await publish_svc.repo.get_av_work(work["avWorkId"])


# ============================================================
# 1. 渲染参数表(4 断言)
# ============================================================

class TestRenderProfiles:
    async def run(self):
        reset_store()
        svc = BloggerAVPublishService()

        # 1) 六平台种子(惰性灌入幂等)
        p1 = await svc.ensure_render_profiles()
        p2 = await svc.ensure_render_profiles()
        record("参数表-六平台种子",
               set(p1.keys()) == set(RENDER_PROFILE_SEEDS.keys())
               and len(p1) == 6 and p1 == p2,
               f"n={len(p1)}")

        # 2) 参数正确(抖音竖屏8M / B站横屏 / 播客纯音频128k)
        dy, bili, xyz = (p1["douyin"], p1["bilibili"],
                         p1["xiaoyuzhou"])
        record("参数表-参数正确",
               dy["resolution"] == "1080x1920"
               and dy["bitrateKbps"] == 8000
               and bili["resolution"] == "1920x1080"
               and bili["durationRange"] == [180, 480]
               and xyz["video"] is False
               and xyz["audioKbps"] == 128,
               f"dy={dy.get('resolution')}")

        # 3) 热更新(save 覆盖 + get 生效)
        await svc.save_render_profile(
            "douyin", {"video": True, "resolution": "720x1280",
                       "bitrateKbps": 4000, "audioKbps": 128,
                       "durationRange": [15, 45],
                       "subtitleStyle": "large-center"})
        updated = await svc.get_render_profile("douyin")
        record("参数表-热更新",
               updated["bitrateKbps"] == 4000,
               f"b={updated.get('bitrateKbps')}")

        # 4) 无效平台/参数拒绝
        errs = 0
        try:
            await svc.get_render_profile("tiktok")
        except ValueError:
            errs += 1
        try:
            await svc.save_render_profile("douyin", {"foo": 1})
        except ValueError:
            errs += 1
        record("参数表-无效拒绝", errs == 2, f"errs={errs}")


# ============================================================
# 2. 完播加权黄金时段(5 断言)
# ============================================================

class TestGoldenWindows:
    async def run(self):
        reset_store()
        svc = BloggerAVPublishService()

        # 5) 观测值公式(点击×0.5+完播×100×0.5)
        record("时段-观测值公式",
               completion_weighted_obs(80, 0.6) == 70.0
               and completion_weighted_obs(0, 1.0) == 50.0
               and completion_weighted_obs(100, 0) == 50.0,
               f"obs={completion_weighted_obs(80, 0.6)}")

        # 6) EMA 收敛(同槽位二次观测 α=0.3)
        work = await _published_work(svc, "douyin")
        await svc.report_av_work_metrics(
            work["avWorkId"], clicks=80, completion_rate=0.6)
        r1 = await svc.learn_av_windows()
        key = f"douyin:{_local_hour(work['publishedAt'])}"
        v1 = r1["windows"].get(key)
        await svc.report_av_work_metrics(
            work["avWorkId"], clicks=100, completion_rate=0.8)
        r2 = await svc.learn_av_windows()
        v2 = r2["windows"].get(key)
        # obs2=90; v2 = 70 + 0.3×(90−70) = 76
        record("时段-EMA收敛",
               v1 == 70.0 and v2 == 76.0,
               f"v1={v1} v2={v2}")

        # 7) TOP3 时段(值降序)
        slots = await svc.best_av_slots("douyin")
        record("时段-TOP3返回", isinstance(slots, list)
               and len(slots) <= 3,
               f"slots={slots}")

        # 8) 冷启动回退(无数据平台→静态黄金窗 P5c 口径)
        t = await svc.next_av_publish_time("xiaoyuzhou")
        record("时段-冷启动回退",
               isinstance(t, str) and "T" in t,
               f"t={t}")

        # 9) 曲线与 P5c 图文分离(独立 key 防语义污染)
        p5c_windows = await svc.repo.get_publish_windows()
        av_windows = await svc.repo.get_av_windows()
        record("时段-AV曲线独立",
               key in av_windows
               and key not in p5c_windows,
               f"av={list(av_windows.keys())[:2]}")


def _local_hour(iso: str) -> int:
    from datetime import datetime
    return datetime.fromisoformat(iso).astimezone().hour


# ============================================================
# 3. 发布与首发审批(5 断言)
# ============================================================

class TestPublish:
    async def run(self):
        reset_store()
        svc = BloggerAVPublishService()

        # 10) 常规平台直接发布(状态+时间落库)
        work = await _rendered_work(svc, "douyin")
        r = await svc.publish_av_work(work["avWorkId"])
        record("发布-常规直接",
               r["published"] is True
               and r["work"]["publishStatus"] == "published"
               and r["work"]["publishedAt"] != "",
               f"r={r['work'].get('publishStatus')}")

        # 11) 新平台首发 → 审批提单(不发布)
        w2 = await _rendered_work(svc, "bilibili")
        r2 = await svc.publish_av_work(w2["avWorkId"])
        record("发布-首发审批",
               r2["published"] is False
               and r2["pendingApproval"] is True
               and r2["work"]["publishStatus"]
               == "pending_approval",
               f"r2={r2['work'].get('publishStatus')}")

        # 12) 放行后发布(approved=True)
        r3 = await svc.publish_av_work(w2["avWorkId"],
                                        approved=True)
        record("发布-放行后发布",
               r3["published"] is True,
               f"r3={r3['work'].get('publishStatus')}")

        # 13) 重复发布拒绝 / 未渲染拒绝
        errs = 0
        try:
            await svc.publish_av_work(w2["avWorkId"])
        except ValueError:
            errs += 1
        fresh = await _rendered_work(svc, "douyin")
        await svc.repo.update_av_work(
            fresh["avWorkId"], {"renderStatus": "failed"})
        try:
            await svc.publish_av_work(fresh["avWorkId"])
        except ValueError:
            errs += 1
        record("发布-状态拒绝", errs == 2, f"errs={errs}")

        # 14) 指标注入(完播率越界拒绝 + 正常合并)
        await svc.report_av_work_metrics(
            work["avWorkId"], clicks=50, completion_rate=0.7)
        w = await svc.repo.get_av_work(work["avWorkId"])
        try:
            await svc.report_av_work_metrics(
                work["avWorkId"], completion_rate=1.5)
            ok = False
        except ValueError:
            ok = True
        record("发布-指标注入",
               w["metrics"]["clicks"] == 50
               and w["metrics"]["completionRate"] == 0.7
               and ok,
               f"m={w['metrics']}")


# ============================================================
# 4. 互动自愈(5 断言)
# ============================================================

class TestPostcheck:
    async def run(self):
        reset_store()
        svc = BloggerAVPublishService()

        # 15) 完播低迷 → 推广建议(pending)
        work = await _published_work(svc, "douyin")
        await svc.report_av_work_metrics(
            work["avWorkId"], clicks=10, completion_rate=0.1)
        pc = await svc.postcheck_av(work["avWorkId"])
        record("自愈-完播低迷推广",
               pc["engagementLow"] is True
               and pc["boostProposal"] is not None
               and pc["boostProposal"]["status"] == "pending",
               f"pc={pc.get('engagementLow')}")

        # 16) 高频疑问 → FAQ 置顶(合规三件套)
        pc2 = await svc.postcheck_av(
            work["avWorkId"],
            comments=["哪里买？", "多少钱？", "好看"])
        record("自愈-高频疑问FAQ",
               pc2["faqReply"] is not None
               and "饮酒" in pc2["faqReply"]
               and "AI 创作" in pc2["faqReply"],
               f"faq={bool(pc2['faqReply'])}")

        # 17) 弹幕负面 → 仅建议(AI 永不自动隐藏)
        pc3 = await svc.postcheck_av(
            work["avWorkId"],
            danmaku=["无聊", "跳过", "好看", "推荐"])
        kinds = [a["kind"] for a in pc3["actions"]]
        record("自愈-负面仅建议",
               pc3["negativeFlag"] is True
               and "hide_candidate" in kinds,
               f"kinds={kinds}")

        # 18) 音画不同步 → 自愈重渲染
        w2 = await _published_work(svc, "douyin")
        pc4 = await svc.postcheck_av(
            w2["avWorkId"], playback_error="音画不同步")
        record("自愈-音画重渲染",
               pc4["reheal"] is not None
               and pc4["reheal"]["attempt"] == 1
               and pc4["reheal"]["status"] == "re_rendered",
               f"r={pc4['reheal']}")

        # 19) 自愈耗尽 → 转人工(永不静默丢弃)
        #     断言18已重渲染 1 次(attempt=1), 再补至 3 次
        for _ in range(AV_REHEAL_RETRY_MAX - 1):
            await svc.heal_av_desync(w2["avWorkId"])
        try:
            await svc.heal_av_desync(w2["avWorkId"])
            ok = False
        except ValueError as exc:
            ok = "耗尽" in str(exc)
        w3 = await svc.repo.get_av_work(w2["avWorkId"])
        record("自愈-耗尽转人工",
               ok and w3.get("healStatus") == "manual_queue",
               f"hs={w3.get('healStatus')}")


# ============================================================
# 5. 推广分轨(4 断言)
# ============================================================

class TestBoost:
    async def run(self):
        reset_store()
        svc = BloggerAVPublishService()

        # 20) 低预算自动执行(mock 轨留痕)
        work = await _published_work(svc, "douyin")
        r = await svc.execute_av_boost(work["avWorkId"], 50)
        record("推广-低预算自动",
               r["autoExecuted"] is True
               and r["boost"]["status"] == "executed"
               and r["boost"]["receipt"]["mode"] == "mock",
               f"r={r['boost'].get('status')}")

        # 21) 幂等拒绝(已有推广)
        try:
            await svc.execute_av_boost(work["avWorkId"], 60)
            ok = False
        except ValueError:
            ok = True
        record("推广-幂等拒绝", ok)

        # 22) 高预算 → pending 建议书(永不自动)
        w2 = await _published_work(svc, "douyin")
        r2 = await svc.execute_av_boost(w2["avWorkId"], 500)
        record("推广-高预算pending",
               r2["autoExecuted"] is False
               and r2["boost"]["status"] == "pending"
               and "质押" in r2["note"],
               f"r2={r2['boost'].get('status')}")

        # 23) pause 拒绝(低预算自动轨)
        gov = BloggerAutoGovernService()
        w3 = await _published_work(svc, "douyin")
        await gov.pause_autonomy("P6c 测试暂停")
        try:
            await svc.execute_av_boost(w3["avWorkId"], 50)
            ok = False
        except ValueError as exc:
            ok = "已暂停" in str(exc)
        # 高预算建议书不受限(给人工审批留通道)
        r3 = await svc.execute_av_boost(w3["avWorkId"], 500)
        record("推广-pause拒绝+高预算通道",
               ok and r3["autoExecuted"] is False,
               f"pause={ok} hb={r3['autoExecuted']}")
        await gov.resume_autonomy()


async def main():
    tests = [TestRenderProfiles(), TestGoldenWindows(),
             TestPublish(), TestPostcheck(), TestBoost()]
    for t in tests:
        await t.run()
    print("=" * 60)
    print("40号 P6c 音视频自主发布调度器专项测试")
    print("=" * 60)
    for line in RESULTS:
        print(line)
    print("-" * 60)
    print(f"通过: {PASS} / {PASS + FAIL}")
    if FAIL:
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
