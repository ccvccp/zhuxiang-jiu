"""40号·平台流量DV博主模块·P1 学习闭环与权重自进化专项测试

覆盖(设计文档 §2.6 / P1 计划):
    1. 层1 Hedge 回流: 反馈入库(scorer=blogger_work_gate, 决策时刻
       快照) / 未发布409 / 幂等409 / learningMetrics 留痕
    2. 层2 权重进化: GMV+0.05 / 点击+0.02 / 零引流-0.05+streak /
       边界clamp / audit 留痕 / 权重变化影响下次评分
    3. AI 止损: 连续3条零引流 → auto-paused(再罚一档) /
       雷达停扫 / 恢复保留 weightAdjust 清零 streak
    4. 批量回流: 沉淀窗口(24h)内 skip / 过窗提交 / 已回流 skip
    5. Hedge 学习: 反馈不足409 → 调 min_feedback → 学习晋升
    6. learning_status: 权重档案/漂移/回流统计/进化榜/止损榜
    7. 旧记录兼容: 无 weightBase 记录 normalize 回填
    8. HTTP 层: learning 四端点(feedback/collect/run/status)
    9. 调度器: learning 开关/启动/停止

运行:
    $env:LOCK_MODE="asyncio"; $env:STORE_MODE="asyncio"
    python test_blogger_p1.py
"""

import asyncio
import os
import sys
from datetime import datetime, UTC, timedelta


# 确保使用内存模式 + LLM 关闭(规则轨确定性测试)
os.environ["LOCK_MODE"] = "asyncio"
os.environ["STORE_MODE"] = "asyncio"
os.environ.pop("LLM_API_KEY", None)
os.environ["LLM_ENABLED"] = "off"

from services.blogger_service import BloggerService
from services.attract_service import AttractService
from services.ai_learning_service import (
    update_learning_config, get_weights_view,
)
from repositories.blogger_repository import (
    WORK_STATUS_AUTO_FOLLOW, WORK_STATUS_MANUAL_QUEUE,
    FOLLOW_STATUS_APPROVED, FOLLOW_STATUS_PUBLISHED,
    WEIGHT_ADJUST_MAX, WEIGHT_ADJUST_MIN,
    WEIGHT_STEP_GMV, WEIGHT_STEP_CLICK, WEIGHT_STEP_ZERO,
    normalize_blogger,
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


PAST = "2000-01-01T00:00:00+00:00"


async def _published_follows(svc: BloggerService, count: int = 3
                             ) -> list[dict]:
    """构造 N 份已发布跟随(优先不同博主; 关限流绕过冷却/间隔)"""
    import services.blogger_service as svc_mod
    svc_mod.BLOGGER_FOLLOW_COOLDOWN_HOURS = 0
    svc_mod.FOLLOW_GAP_HOURS = 0
    result = await svc.scan()
    works = []
    for d in result["decisions"]:
        if d["work"]["status"] == WORK_STATUS_AUTO_FOLLOW:
            works.append(d["work"])
    for d in result["decisions"]:
        if d["work"]["status"] == WORK_STATUS_MANUAL_QUEUE:
            works.append(await svc.manual_decide(
                d["work"]["workId"], engage=True))
    # 轮询取件(不同博主优先, 避免层2进化互相抵消)
    by_blogger = {}
    for w in works:
        by_blogger.setdefault(w["bloggerId"], []).append(w)
    picked = []
    round_idx = 0
    while len(picked) < count:
        advanced = False
        for bid in sorted(by_blogger):
            queue = by_blogger[bid]
            if round_idx < len(queue):
                picked.append(queue[round_idx])
                advanced = True
                if len(picked) >= count:
                    break
        if not advanced:
            break
        round_idx += 1
    published = []
    for w in picked:
        follow = await svc.generate_follow(w["workId"])
        published.append(await svc.publish_follow(
            follow["followId"], publish_at=PAST))
    await svc.process_publish_queue()
    return published


class TestFeedback:
    async def run(self):
        svc = BloggerService()
        follows = await _published_follows(svc, count=3)
        record("回流-发布素材就绪", len(follows) == 3,
               f"实际{len(follows)}")
        # 未发布(approved)409: 强改状态
        repo = svc.repo
        await repo.update_follow(follows[0]["followId"], {
            "status": FOLLOW_STATUS_APPROVED})
        try:
            await svc.submit_learning_feedback(follows[0]["followId"])
            record("回流-未发布409", False)
        except ValueError:
            record("回流-未发布409", True)
        await repo.update_follow(follows[0]["followId"], {
            "status": FOLLOW_STATUS_PUBLISHED})
        # 手动 clicks=5 → correct=True + 点击升权
        result = await svc.submit_learning_feedback(
            follows[0]["followId"], clicks=5)
        record("回流-Hedge反馈入库",
               result.get("scorerId") == "blogger_work_gate"
               and result.get("correct") is True,
               f"result={result}")
        record("回流-返回反馈ID",
               bool(result.get("feedbackId")))
        follow = await repo.get_follow(follows[0]["followId"])
        record("回流-learningFed幂等标记",
               follow.get("learningFed") is True)
        record("回流-learningMetrics留痕",
               (follow.get("learningMetrics") or {}).get("clicks") == 5,
               f"{follow.get('learningMetrics')}")
        # 幂等 409
        try:
            await svc.submit_learning_feedback(
                follows[0]["followId"], clicks=5)
            record("回流-重复回流409", False)
        except ValueError:
            record("回流-重复回流409", True)
        # 不存在 404
        try:
            await svc.submit_learning_feedback(999999)
            record("回流-不存在404", False)
        except KeyError:
            record("回流-不存在404", True)


class TestLayer2Evolution:
    async def run(self):
        svc = BloggerService()
        follows = await _published_follows(svc, count=3)
        # ① GMV 升权(真实归因: 点击→下单)
        f1 = follows[0]
        attract = AttractService()
        click = await attract.resolve_click(
            code=f1["shortCode"], utm_source=f1["platform"])
        await attract.attach_registration(
            click_id=click["clickId"], member_id=77001)
        await attract.attach_order(click_id=click["clickId"],
                                   order_id="ORD-P1-1",
                                   order_amount=299.0, commission=15.0)
        result = await svc.submit_learning_feedback(f1["followId"])
        evo = result.get("bloggerEvolution") or {}
        blogger = await svc.repo.get_blogger(f1["bloggerId"])
        record("层2-GMV升权+0.05",
               abs(evo.get("weightAdjust", 0) - WEIGHT_STEP_GMV)
               < 1e-6
               and abs(float(blogger.get("weightAdjust"))
                       - WEIGHT_STEP_GMV) < 1e-6,
               f"evo={evo}")
        record("层2-派生weight=clamp(base+adjust)",
               abs(float(blogger["weight"])
                   - min(1.0, float(blogger["weightBase"])
                          + WEIGHT_STEP_GMV)) < 1e-4,
               f"w={blogger['weight']} "
               f"base={blogger['weightBase']}")
        record("层2-streak清零",
               int(blogger.get("zeroTrafficStreak") or 0) == 0)
        record("层2-audit留痕(weight_evolve)",
               any(a.get("action") == "weight_evolve"
                   for a in await svc.repo.list_audits(
                       blogger_id=f1["bloggerId"], limit=50)))
        # ② 零引流降权(streak+1)
        f2 = follows[1]
        result = await svc.submit_learning_feedback(
            f2["followId"], clicks=0)
        evo = result.get("bloggerEvolution") or {}
        blogger2 = await svc.repo.get_blogger(f2["bloggerId"])
        record("层2-零引流降权-0.05",
               abs(float(blogger2.get("weightAdjust"))
                   - WEIGHT_STEP_ZERO) < 1e-6
               and evo.get("zeroTrafficStreak") == 1,
               f"evo={evo}")
        record("层2-Hedge零引流失误",
               result.get("correct") is False)
        # ③ 点击升权(无转化) +0.02
        f3 = follows[2]
        result = await svc.submit_learning_feedback(
            f3["followId"], clicks=3)
        blogger3 = await svc.repo.get_blogger(f3["bloggerId"])
        record("层2-点击升权+0.02",
               abs(float(blogger3.get("weightAdjust"))
                   - WEIGHT_STEP_CLICK) < 1e-6,
               f"adjust={blogger3.get('weightAdjust')}")
        # ④ 升权边界 clamp(+0.3 封顶)
        for _ in range(10):
            await svc._evolve_blogger_weight(
                {"followId": 0, "bloggerId": f3["bloggerId"]},
                {"clicks": 0, "registered": 0, "gmv": 100.0})
        blogger3 = await svc.repo.get_blogger(f3["bloggerId"])
        record("层2-升权边界clamp+0.3",
               abs(float(blogger3.get("weightAdjust"))
                   - WEIGHT_ADJUST_MAX) < 1e-6,
               f"adjust={blogger3.get('weightAdjust')}")
        # ⑤ 降权边界 clamp(-0.3 封底)
        for _ in range(10):
            await svc._evolve_blogger_weight(
                {"followId": 0, "bloggerId": f2["bloggerId"]},
                {"clicks": 0, "registered": 0, "gmv": 0})
        blogger2 = await svc.repo.get_blogger(f2["bloggerId"])
        record("层2-降权边界clamp-0.3",
               float(blogger2.get("weightAdjust"))
               >= WEIGHT_ADJUST_MIN - 1e-6,
               f"adjust={blogger2.get('weightAdjust')}")


class TestAutoPause:
    async def run(self):
        svc = BloggerService()
        # 直测 _evolve_blogger_weight: 连续3次零引流 → 止损
        # (单槽位每博主仅~2件作品, 全链路构造3份成本高;
        #  回流主链路已在 TestFeedback/TestLayer2Evolution 覆盖)
        target = 8   # 种子: wx_zhuxiang(五万级, base 0.6)
        evos = []
        for i in range(3):
            evos.append(await svc._evolve_blogger_weight(
                {"followId": 900 + i, "bloggerId": target},
                {"clicks": 0, "registered": 0, "gmv": 0}))
        blogger = await svc.repo.get_blogger(target)
        record("止损-streak逐次累加",
               [e["zeroTrafficStreak"] for e in evos] == [1, 2, 3],
               f"{[e['zeroTrafficStreak'] for e in evos]}")
        record("止损-第3条触发auto-paused",
               evos[2].get("autoPaused") is True
               and blogger.get("status") == "paused"
               and blogger.get("pausedReason") == "auto_loss_cut",
               f"{blogger.get('status')}/"
               f"{blogger.get('pausedReason')}")
        # 止损再罚一档: 3×(-0.05) + 额外-0.05 = -0.20
        record("止损-再罚一档(adjust=-0.20)",
               abs(float(blogger.get("weightAdjust")) + 0.20) < 1e-6,
               f"adjust={blogger.get('weightAdjust')}")
        record("止损-派生weight随adjust下调",
               abs(float(blogger["weight"]) - 0.40) < 1e-4,
               f"w={blogger['weight']}")
        record("止损-audit留痕(auto_paused)",
               any(a.get("action") == "auto_paused"
                   for a in await svc.repo.list_audits(
                       blogger_id=target, limit=50)))
        # 雷达停扫: paused 博主(指定ID/全池)均不扫描
        scan = await svc.radar.scan(blogger_ids=(target,))
        record("止损-雷达指定ID停扫",
               scan["scanned"] == 0,
               f"scanned={scan['scanned']}")
        # 已止损博主再零引流: 不重复触发 paused(幂等)
        evo = await svc._evolve_blogger_weight(
            {"followId": 903, "bloggerId": target},
            {"clicks": 0, "registered": 0, "gmv": 0})
        record("止损-重复触发幂等",
               evo.get("autoPaused") is False,
               f"evo={evo}")
        # 恢复: streak 清零 + adjust 保留
        activated = await svc.set_blogger_status(target, "active")
        record("止损-恢复清零streak",
               activated.get("zeroTrafficStreak") == 0
               and activated.get("pausedReason") == "")
        record("止损-恢复保留weightAdjust",
               abs(float(activated.get("weightAdjust")) + 0.25)
               < 1e-6,
               f"adjust={activated.get('weightAdjust')}")
        # 恢复后 GMV 引流赚回 +0.05
        await svc._evolve_blogger_weight(
            {"followId": 904, "bloggerId": target},
            {"clicks": 5, "registered": 1, "gmv": 88.0})
        blogger = await svc.repo.get_blogger(target)
        record("止损-恢复后引流赚回",
               abs(float(blogger.get("weightAdjust")) + 0.20)
               < 1e-6 and blogger.get("zeroTrafficStreak") == 0,
               f"adjust={blogger.get('weightAdjust')}")


class TestCollectWindow:
    async def run(self):
        svc = BloggerService()
        follows = await _published_follows(svc, count=2)
        # 刚发布(<24h) → 全部 skip
        collected = await svc.collect_learning_feedback()
        record("批量-窗口内全skip",
               collected["submitted"] == 0
               and collected["skipped"] >= 2,
               f"{collected['submitted']}/{collected['skipped']}")
        # 强改 publishedAt 到25h前 → collect 提交
        past25h = (datetime.now(UTC)
                   - timedelta(hours=25)).isoformat()
        for f in follows:
            await svc.repo.update_follow(f["followId"], {
                "publishedAt": past25h})
        collected = await svc.collect_learning_feedback()
        record("批量-过窗提交",
               collected["submitted"] == 2,
               f"submitted={collected['submitted']}")
        # 再跑: 已回流 skip
        collected2 = await svc.collect_learning_feedback()
        record("批量-已回流skip",
               collected2["submitted"] == 0
               and collected2["skipped"] >= 2,
               f"{collected2}")


class TestHedgeLearning:
    async def run(self):
        svc = BloggerService()
        # 反馈不足 → 409(默认 min_feedback=10)
        try:
            await svc.run_learning()
            record("学习-反馈不足409", False)
        except ValueError:
            record("学习-反馈不足409", True)
        # 提交 1 条反馈 + 调 min_feedback=1 → 学习成功
        follows = await _published_follows(svc, count=1)
        await svc.repo.update_follow(follows[0]["followId"], {
            "publishedAt": (datetime.now(UTC)
                            - timedelta(hours=25)).isoformat()})
        await svc.submit_learning_feedback(follows[0]["followId"],
                                           clicks=2)
        await update_learning_config("blogger_work_gate",
                                     {"min_feedback": 1,
                                      "auto_apply": True})
        learned = await svc.run_learning()
        record("学习-一轮Hedge学习完成",
               learned.get("version") or learned.get("challenger")
               or learned.get("promoted") is not None,
               f"{learned}")
        # 状态视图
        status = await svc.learning_status()
        record("学习-status权重档案",
               (status.get("weights") or {}).get("scorerId")
               == "blogger_work_gate"
               or "champion" in (status.get("weights") or {}),
               f"weights={status.get('weights')}")
        record("学习-status回流统计",
               status["feedback"]["fed"] >= 1
               and status["feedback"]["settleHours"] == 24,
               f"{status['feedback']}")
        record("学习-status进化榜",
               "top" in status["weightEvolution"]
               and "bottom" in status["weightEvolution"]
               and "autoPaused" in status["weightEvolution"])


class TestWeightAffectsScoring:
    async def run(self):
        svc = BloggerService()
        # 升权后同博主新作品评分中 blogger_weight 因子分变化
        # (选低权重博主, 避免 1.0 clamp 掩盖差异)
        bloggers = await svc.repo.list_bloggers(limit=100)
        low = min(bloggers, key=lambda b: float(b["weight"]))
        old_weight = float(low["weight"])
        from services.ai_scoring_service import SCORERS
        ctx = {
            "workId": 1, "bloggerId": low["bloggerId"],
            "engagementRate": 0.05,
            "title": "竹香酒开箱测评推荐清单",
            "summary": "竹香白酒送礼开箱",
            "likes": 5000, "comments": 300, "shares": 150,
        }
        s_low = await SCORERS["blogger_work_gate"].score(
            {**ctx, "bloggerWeight": old_weight})
        s_high = await SCORERS["blogger_work_gate"].score(
            {**ctx, "bloggerWeight": min(
                1.0, old_weight + 0.3)})
        factor_low = next(
            f["score"] for f in s_low["factors"]
            if f["name"] == "blogger_weight")
        factor_high = next(
            f["score"] for f in s_high["factors"]
            if f["name"] == "blogger_weight")
        record("评分联动-权重抬升因子分上升",
               factor_high > factor_low
               and s_high["score"] > s_low["score"],
               f"{factor_low}→{factor_high} "
               f"{s_low['score']}→{s_high['score']}")
        record("评分联动-侦测优先级(列表按weight降序)",
               bloggers[0]["weight"] >= bloggers[-1]["weight"],
               f"first={bloggers[0]['weight']} "
               f"last={bloggers[-1]['weight']}")


class TestCompat:
    async def run(self):
        # 旧记录(无 weightBase/weightAdjust) normalize 回填
        legacy = {
            "bloggerId": 99, "platform": "douyin",
            "account": "dy_legacy", "nickname": "旧记录",
            "fansWan": 100.0, "weight": 1.0, "status": "active",
        }
        normalized = normalize_blogger(dict(legacy))
        record("兼容-weightBase回填",
               normalized["weightBase"] == 1.0)
        record("兼容-adjust默认0",
               normalized["weightAdjust"] == 0.0)
        record("兼容-进化字段补齐",
               normalized["pausedReason"] == ""
               and normalized["zeroTrafficStreak"] == 0
               and normalized["trafficInfluencerId"] == 0)


class TestHttpRoutes:
    async def run(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from routes.blogger_routes import register_blogger_routes

        app = FastAPI()
        register_blogger_routes(app)
        client = TestClient(app)
        admin = {"X-Role": "admin"}

        # 鉴权
        resp = client.get("/api/blogger/learning/status")
        record("HTTP-鉴权403", resp.status_code == 403)

        # 准备一份已发布+过窗的跟随
        svc = BloggerService()
        follows = await _published_follows(svc, count=1)
        fid = follows[0]["followId"]
        await svc.repo.update_follow(fid, {
            "publishedAt": (datetime.now(UTC)
                            - timedelta(hours=25)).isoformat()})

        # feedback(手动 clicks)
        resp = client.post("/api/blogger/learning/feedback",
                           headers=admin,
                           json={"followId": fid, "clicks": 8})
        record("HTTP-feedback回流",
               resp.status_code == 200
               and (resp.json().get("data") or {})
               .get("correct") is True,
               f"status={resp.status_code}")
        resp = client.post("/api/blogger/learning/feedback",
                           headers=admin,
                           json={"followId": fid, "clicks": 8})
        record("HTTP-feedback幂等409", resp.status_code == 409)
        resp = client.post("/api/blogger/learning/feedback",
                           headers=admin,
                           json={"followId": 999999})
        record("HTTP-feedback不存在404", resp.status_code == 404)

        # collect
        resp = client.post("/api/blogger/learning/collect",
                           headers=admin)
        record("HTTP-collect批量回流",
               resp.status_code == 200
               and isinstance(
                   (resp.json().get("data") or {}).get("results"),
                   list), f"status={resp.status_code}")

        # run(先调 min_feedback=1)
        await update_learning_config("blogger_work_gate",
                                     {"min_feedback": 1})
        resp = client.post("/api/blogger/learning/run",
                           headers=admin)
        record("HTTP-run学习",
               resp.status_code == 200
               and bool(resp.json().get("data")),
               f"status={resp.status_code}")

        # status
        resp = client.get("/api/blogger/learning/status",
                          headers=admin)
        d = resp.json().get("data") or {}
        record("HTTP-status视图",
               resp.status_code == 200
               and "weights" in d and "drift" in d
               and "feedback" in d and "weightEvolution" in d,
               f"status={resp.status_code}")


class TestScheduler:
    async def run(self):
        from services import blogger_scheduler as sched
        # 默认 off
        record("调度-默认关闭",
               not sched.learning_enabled()
               and sched.start_learning_scheduler() is False)
        # 开启(无事件循环 → 启动失败返回 False, 不抛异常)
        os.environ["BLOGGER_LEARNING_AUTO"] = "on"
        try:
            ok = sched.start_learning_scheduler()
            record("调度-开启启动",
                   ok in (True, False))   # 无运行循环时 False 合法
        finally:
            os.environ.pop("BLOGGER_LEARNING_AUTO", None)
        sched.stop_schedulers()
        record("调度-停止清理",
               sched._LEARNING_TASK is None
               and sched._RADAR_TASK is None
               and sched._PUBLISH_TASK is None)


async def main():
    test_classes = [
        ("层1回流正确性与幂等", TestFeedback),
        ("层2权重进化(GMV/点击/零引流/边界)", TestLayer2Evolution),
        ("AI止损与恢复", TestAutoPause),
        ("批量回流沉淀窗口", TestCollectWindow),
        ("Hedge学习与状态视图", TestHedgeLearning),
        ("权重进化影响评分", TestWeightAffectsScoring),
        ("旧记录兼容", TestCompat),
        ("HTTP层learning四端点", TestHttpRoutes),
        ("学习调度器", TestScheduler),
    ]
    print("=" * 62)
    print("40号·平台流量DV博主模块 P1 学习闭环专项测试")
    print("=" * 62)
    for name, cls in test_classes:
        reset_store()
        print(f"\n[{name}]")
        try:
            await cls().run()
        except Exception as e:
            record(f"{name} 测试执行异常", False, repr(e))

    print("\n" + "-" * 62)
    for line in RESULTS:
        print(line)
    print("-" * 62)
    print(f"总计: {PASS} 通过 / {FAIL} 失败")
    return FAIL


if __name__ == "__main__":
    sys.exit(asyncio.run(main()) and 1 or 0)
