"""40号·平台流量DV博主模块·P5c 自主发布调度器专项测试

覆盖(设计文档《40号 P5 升级方案》§5):
    1. 动态时机决策: 时段曲线 EMA 学习/二次收敛/TOP3 降序/
       冷启动回退静态黄金窗/动态下一发布时间
    2. 账号智能协同: 标签匹配优先/无匹配回退LRU/新号冷启动帽1/
       老号帽3/平局LRU裁决/tags入库
    3. 发布后 1h 调控: 互动低迷推广建议/FAQ置顶回复(合规)/
       负面苗头仅建议/低预算执行/高预算永不自动

运行:
    $env:LOCK_MODE="asyncio"; $env:STORE_MODE="asyncio"
    python test_blogger_p5c.py
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

from services.blogger_service import BloggerService
from services.blogger_auto_publish_service import (
    BloggerAutoPublishService, WINDOW_EMA_ALPHA, TOP_SLOTS,
    NEW_ACCOUNT_PUBS, POSTCHECK_ENGAGE_FLOOR, ENGAGE_LOW_RATIO,
    FAQ_REPLY_THRESHOLD, BOOST_AUTO_MAX, BOOST_PENDING,
    BOOST_EXECUTED, HOOK_TAG_MAP, FAQ_REPLY_TEMPLATE,
    _local_hour,
)
from services.blogger_account_service import BloggerAccountService
from services.blogger_auto_learn_service import (
    BloggerAutoLearnService, SIGNAL_CHANNEL_NEGATIVE,
)
from repositories.blogger_repository import (
    WORK_STATUS_AUTO_FOLLOW, FOLLOW_STATUS_PUBLISHED,
    ACCOUNT_DAILY_CAP,
)
from repositories.promo_repository import (
    REQUIRED_DISCLAIMER, REQUIRED_AGE_TIP,
)

PASS = 0
FAIL = 0
RESULTS = []

PAST = "2000-01-01T00:00:00+00:00"


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


async def _publish_follows(count: int = 1) -> list[dict]:
    """构造已发布跟随内容(走完整 scan→follow→publish 链)"""
    import services.blogger_service as svc_mod
    svc_mod.BLOGGER_FOLLOW_COOLDOWN_HOURS = 0
    svc_mod.FOLLOW_GAP_HOURS = 0
    svc = BloggerService()
    published = []
    while len(published) < count:
        result = await svc.scan()
        works = [d["work"] for d in result["decisions"]
                 if d["work"]["status"] == WORK_STATUS_AUTO_FOLLOW]
        if not works:
            break
        for w in works[:max(1, count - len(published))]:
            follow = await svc.generate_follow(w["workId"])
            await svc.publish_follow(
                follow["followId"], publish_at=PAST)
            await svc.process_publish_queue()
            got = await svc.repo.get_follow(follow["followId"])
            if got.get("status") == FOLLOW_STATUS_PUBLISHED:
                published.append(got)
            if len(published) >= count:
                break
    return published


def _add_clicks(svc: BloggerService, code: str, n: int) -> None:
    """内存库直插 attract 点击(独立 IP, 间隔拉开)"""
    from services.attract_service import AttractService
    from datetime import datetime, UTC, timedelta
    attract = AttractService()
    attract.repo._ensure_store()
    clicks = attract.repo.store.setdefault("attract_clicks", {})
    base = datetime.now(UTC) - timedelta(hours=3)
    start = len(clicks) + 1
    for i in range(n):
        cid = start + i
        clicks[cid] = {
            "clickId": cid, "code": code,
            "ip": f"10.88.{cid}.{cid % 250}",
            "userAgent": "Mozilla/5.0 Chrome/120.0",
            "at": (base + timedelta(seconds=i * 30)).isoformat(),
            "converted": False, "memberId": 0, "orderId": 0,
            "createdAt": base.isoformat(),
        }


# ============================================================
# 1. 动态时机决策(6 断言)
# ============================================================

class TestDynamicTiming:
    async def run(self):
        reset_store()
        svc = BloggerAutoPublishService()
        published = await _publish_follows(2)
        # 注入点击: 第一条 20(高), 第二条 4(低) → 两条不同平台时
        # 曲线含两个槽位; 同平台则同槽位 EMA 收敛
        for i, f in enumerate(published):
            _add_clicks(svc.svc, f.get("shortCode", ""),
                        20 if i == 0 else 4)

        # 1) 首次学习: 观测入曲线(无历史 → 初值=观测)
        r = await svc.learn_windows()
        record("时机-首次学习入曲线",
               r["observations"] == 2 and r["slots"] >= 1,
               f"obs={r['observations']}")

        # 2) 二次学习 EMA 收敛(多观测同槽 → 向观测均值收敛;
        #    观测 4 与 20 均值 12, 8.8 → 11.152 靠近 12)
        repo = svc.repo
        windows = await repo.get_publish_windows()
        some_key = next(iter(windows))
        old_val = float(windows[some_key])
        obs_mean = (20.0 + 4.0) / 2
        await svc.learn_windows()
        windows2 = await repo.get_publish_windows()
        new_val = float(windows2[some_key])
        record("时机-EMA向均值收敛",
               abs(new_val - obs_mean) < abs(old_val - obs_mean),
               f"old={old_val} new={new_val} mean={obs_mean}")

        # 3) TOP3 降序(best_slots)
        plat = some_key.split(":")[0]
        slots = await svc.best_slots(plat)
        vals = [float(windows2[f"{plat}:{h}"]) for h in slots]
        record("时机-TOP3降序",
               1 <= len(slots) <= TOP_SLOTS
               and vals == sorted(vals, reverse=True),
               f"slots={slots}")

        # 4) 冷启动回退: 无数据平台 → 静态黄金窗(非空 ISO)
        t = await svc.next_smart_publish_time("weibo"
                                              if plat != "weibo"
                                              else "xiaohongshu")
        record("时机-冷启动回退静态窗", bool(t))

        # 5) 动态决策: 有数据平台 → TOP3 内下一时段
        t2 = await svc.next_smart_publish_time(plat)
        record("时机-动态下一发布时间",
               bool(t2) and ":" in t2)

        # 6) 曲线视图(按平台分组)
        view = await svc.get_windows(plat)
        record("时机-曲线视图分组",
               plat in view and len(view[plat]) >= 1,
               f"view={list(view.keys())}")


# ============================================================
# 2. 账号智能协同(6 断言)
# ============================================================

class TestAccountCoordination:
    async def run(self):
        reset_store()
        acc = BloggerAccountService()
        svc = BloggerAutoPublishService()

        # 7) tags 入库
        a1 = await acc.create_account(
            "douyin", "品鉴主号A", tags=["tasting", "business"])
        record("协同-tags入库",
               a1.get("tags") == ["tasting", "business"],
               f"tags={a1.get('tags')}")

        # 8) 标签匹配优先: 品鉴钩子 → 选 tasting 号(非 LRU 更早号)
        a2 = await acc.create_account("douyin", "生活号B",
                                      tags=["lifestyle"])
        a1.update({"lastUsedAt": "2026-01-01"})  # A 最近已用
        await acc.repo.save_account(a1)
        a2.update({"lastUsedAt": "2001-01-01"})  # B 更久未用
        await acc.repo.save_account(a2)
        picked = await svc.pick_account_smart(
            "douyin", "hook_tasting_pro")
        record("协同-标签匹配优先",
               picked is not None
               and picked["accountId"] == a1["accountId"],
               f"picked={picked and picked.get('alias')}")

        # 9) 无匹配回退 LRU: 无标签钩子 → 取最久未用(B)
        picked2 = await svc.pick_account_smart("douyin", None)
        record("协同-无匹配回退LRU",
               picked2["accountId"] == a2["accountId"],
               f"picked={picked2.get('alias')}")

        # 10) 新号冷启动帽1: totalPublished=0 <5 → 发1次后不可选
        new_acc = await acc.create_account("douyin", "新号C",
                                           tags=["tasting"])
        new_acc.update({"totalPublished": 0, "dailyPublished": 1})
        await acc.repo.save_account(new_acc)
        cands = await svc._smart_candidates("douyin")
        record("协同-新号冷启动帽1",
               new_acc["accountId"] not in
               [c["accountId"] for c in cands],
               f"cands={[c['alias'] for c in cands]}")

        # 11) 老号帽3(ACCOUNT_DAILY_CAP): 稳定号发3次后不可选
        old_acc = await acc.create_account("douyin", "老号D",
                                           tags=["tasting"])
        old_acc.update({"totalPublished": NEW_ACCOUNT_PUBS + 3,
                        "dailyPublished": ACCOUNT_DAILY_CAP})
        await acc.repo.save_account(old_acc)
        cands2 = await svc._smart_candidates("douyin")
        record("协同-老号帽3",
               old_acc["accountId"] not in
               [c["accountId"] for c in cands2],
               f"cands={[c['alias'] for c in cands2]}")

        # 12) 无可用账号 → None
        empty = await svc.pick_account_smart("weibo",
                                              "hook_tasting_pro")
        record("协同-无号None", empty is None)


# ============================================================
# 3. 发布后 1h 调控(6 断言)
# ============================================================

class TestPostPublishControl:
    async def run(self):
        reset_store()
        svc = BloggerAutoPublishService()
        learn = BloggerAutoLearnService()
        published = await _publish_follows(3)
        f_ok, f_low, f_high = published[0], published[1], published[2]
        # f_ok: 正常互动(≥floor); f_low: 零点击(低迷); f_high: 高预算轨

        # 13) 互动正常 → 无推广建议
        _add_clicks(svc.svc, f_ok.get("shortCode", ""),
                    POSTCHECK_ENGAGE_FLOOR)
        r = await svc.postcheck(f_ok["followId"])
        record("后调控-互动正常无建议",
               not r["engagementLow"]
               and r["boostProposal"] is None,
               f"clicks={r['clicks']}")

        # 14) 互动低迷 → pending 推广建议(幂等)
        r2 = await svc.postcheck(f_low["followId"])
        record("后调控-低迷推广建议",
               r2["engagementLow"]
               and r2["boostProposal"]["status"] == BOOST_PENDING,
               f"r2={r2.get('engagementLow')}")
        r2b = await svc.postcheck(f_low["followId"])
        boosts = await svc.repo.list_boosts(
            follow_id=f_low["followId"])
        record("后调控-建议幂等", len(boosts) == 1
               and r2b["boostProposal"]["boostId"]
               == boosts[0]["boostId"],
               f"n={len(boosts)}")

        # 15) FAQ 置顶回复(高频疑问 ≥2 → 全自动, 合规三件套)
        r3 = await svc.postcheck(
            f_ok["followId"],
            comments=["这个多少钱?", "哪里能买到?",
                      "看着不错"])
        record("后调控-FAQ置顶回复",
               r3["faqReply"] is not None
               and REQUIRED_DISCLAIMER in r3["faqReply"]
               and REQUIRED_AGE_TIP in r3["faqReply"]
               and "AI 创作" in r3["faqReply"],
               f"faq={bool(r3.get('faqReply'))}")

        # 16) 负面苗头(P5a 信号集成) → 隐藏候选仅建议
        await learn.report_negative(
            f_ok["followId"], comments=["骗子平台", "垃圾广告"])
        r4 = await svc.postcheck(f_ok["followId"])
        hide_actions = [a for a in r4["actions"]
                       if a["kind"] == "hide_candidate"]
        record("后调控-负面仅建议",
               r4["negativeFlag"] and len(hide_actions) == 1
               and "仅建议" in hide_actions[0]["note"],
               f"flag={r4.get('negativeFlag')}")

        # 17) 低预算执行(≤100 → mock 轨)
        eb = await svc.execute_boost(
            f_ok["followId"], 50.0, reason="测试低预算")
        record("后调控-低预算执行",
               eb["autoExecuted"]
               and eb["boost"]["status"] == BOOST_EXECUTED,
               f"b={eb['boost'].get('status')}")

        # 18) 高预算永不自动(>100 → pending 待审) + 重复拒绝
        hb = await svc.execute_boost(
            f_high["followId"], 500.0, reason="测试高预算")
        dup_ok = False
        try:
            await svc.execute_boost(f_high["followId"], 80.0)
        except ValueError:
            dup_ok = True
        record("后调控-高预算永不自动",
               not hb["autoExecuted"]
               and hb["boost"]["status"] == BOOST_PENDING
               and "永不自动" in hb["note"] and dup_ok,
               f"note={hb.get('note')}")


async def main():
    tests = [TestDynamicTiming(), TestAccountCoordination(),
             TestPostPublishControl()]
    for t in tests:
        await t.run()
    print("=" * 60)
    print("40号 P5c 自主发布调度器专项测试")
    print("=" * 60)
    print("\n".join(RESULTS))
    print("-" * 60)
    print(f"通过: {PASS} / {PASS + FAIL}")
    if FAIL:
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
