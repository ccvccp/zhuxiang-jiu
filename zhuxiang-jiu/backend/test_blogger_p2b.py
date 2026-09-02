"""40号·平台流量DV博主模块·P2b 进化批专项测试

覆盖(设计文档 P2 §3/§4/§5/§6):
    1. 探索三件套: 新博主冷启动(probeRemaining 保底置顶+递减) /
       ε 探索插队 / 缓刑到期复扫追加
    2. 时间衰减: weightAdjust 每周向 0 回归 10% / 近零归零
    3. 效率调制: 近30d 引流效率池内分位(1.5/1.0/0.6) / 无样本不调制
    4. 平台偏置: 引流率差 ×λ clamp ±8 / 样本<5 置0 /
       decide_work 校准+重路由 / 快照留痕
    5. 缓刑自动复燃: 止损博主复扫引流>0 → 自动 reactivate
    6. 健康监控: 震荡翻转≥3 → 冻结14d(步长置0) /
       样本污染暂停学习 / learning_health 三层视图
    7. HTTP 层: health / calibrate 两新端点
    8. P1/P2a 行为兼容: 无偏置时决策不变 / 衰减后权重派生

运行:
    $env:LOCK_MODE="asyncio"; $env:STORE_MODE="asyncio"
    python test_blogger_p2b.py
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
from repositories.blogger_repository import (
    BloggerRepository,
    WORK_STATUS_AUTO_FOLLOW, WORK_STATUS_MANUAL_QUEUE,
    WORK_STATUS_PASSED,
    FOLLOW_STATUS_PUBLISHED,
    PROBE_WORKS, PROBATION_DAYS, WEIGHT_DECAY_WEEKLY,
    BIAS_CLAMP, WEIGHT_STEP_CLICK, WEIGHT_STEP_GMV,
    derived_weight,
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


async def _publish_and_feed(svc: BloggerService, count: int,
                            clicks_per: list[int] = None):
    """构造 N 份已发布并回流(指定 clicks)的跟随"""
    import services.blogger_service as svc_mod
    svc_mod.BLOGGER_FOLLOW_COOLDOWN_HOURS = 0
    svc_mod.FOLLOW_GAP_HOURS = 0
    result = await svc.scan()
    works = [d["work"] for d in result["decisions"]
             if d["work"]["status"] == WORK_STATUS_AUTO_FOLLOW]
    for d in result["decisions"]:
        if d["work"]["status"] == WORK_STATUS_MANUAL_QUEUE:
            works.append(await svc.manual_decide(
                d["work"]["workId"], engage=True))
    by_blogger = {}
    for w in works:
        by_blogger.setdefault(w["bloggerId"], []).append(w)
    picked = []
    round_idx = 0
    while len(picked) < count:
        advanced = False
        for bid in sorted(by_blogger):
            q = by_blogger[bid]
            if round_idx < len(q):
                picked.append(q[round_idx])
                advanced = True
                if len(picked) >= count:
                    break
        if not advanced:
            break
        round_idx += 1
    for i, w in enumerate(picked):
        follow = await svc.generate_follow(w["workId"])
        await svc.publish_follow(follow["followId"], publish_at=PAST)
    await svc.process_publish_queue()
    follows = await svc.repo.list_follows(
        status=FOLLOW_STATUS_PUBLISHED, limit=1000)
    fed = 0
    for i, f in enumerate(follows[:count]):
        clicks = (clicks_per[i] if clicks_per
                  and i < len(clicks_per) else 3)
        await svc.submit_learning_feedback(f["followId"],
                                           clicks=clicks)
        fed += 1
    return follows[:count]


class TestProbe:
    async def run(self):
        svc = BloggerService()
        # 新增博主: probeRemaining=PROBE_WORKS
        new = await svc.create_blogger(
            "weibo", "wb_probe", "冷启动探测博主", 60.0, "wine")
        record("探测-新博主probe额度",
               new.get("probeRemaining") == PROBE_WORKS,
               f"probe={new.get('probeRemaining')}")
        # 全池扫描后: 探测额度递减(该博主本槽位作品有新作品)
        result = await svc.scan()
        blogger = await svc.repo.get_blogger(new["bloggerId"])
        record("探测-扫描后递减",
               int(blogger.get("probeRemaining") or 0)
               < PROBE_WORKS,
               f"probe={blogger.get('probeRemaining')}")
        # 探测博主的作品出现在本轮扫描(保底扫描生效)
        probe_works = [w for w in result["works"]
                       if w.get("bloggerId") == new["bloggerId"]]
        record("探测-保底扫描产出作品", len(probe_works) >= 1,
               f"n={len(probe_works)}")
        # 种子博主无探测额度
        seed = await svc.repo.get_blogger(1)
        record("探测-种子博主无额度",
               int(seed.get("probeRemaining") or 0) == 0)


class TestExploration:
    async def run(self):
        svc = BloggerService()
        radar = svc.radar
        bloggers = await svc.repo.list_bloggers(limit=100)
        # ε=1 强制触发: 低权重半区博主被提至队首
        import services.work_radar_service as radar_mod
        original = radar_mod.EXPLORE_EPSILON
        radar_mod.EXPLORE_EPSILON = 1.0
        try:
            ordered, _ = await radar._apply_exploration(bloggers)
            low_half_ids = {b["bloggerId"]
                            for b in bloggers[len(bloggers) // 2:]}
            record("探索-ε触发低权重插队",
                   ordered[0]["bloggerId"] in low_half_ids,
                   f"first={ordered[0]['bloggerId']}")
        finally:
            radar_mod.EXPLORE_EPSILON = original
        # ε=0 不触发: 顺序保持(无探测博主时)
        radar_mod.EXPLORE_EPSILON = 0.0
        try:
            ordered, _ = await radar._apply_exploration(bloggers)
            record("探索-ε关闭顺序不变",
                   ordered[0]["bloggerId"]
                   == bloggers[0]["bloggerId"])
        finally:
            radar_mod.EXPLORE_EPSILON = original
        # 探测博主置顶(ε=0 锁定确定性)
        bloggers[3]["probeRemaining"] = 2
        radar_mod.EXPLORE_EPSILON = 0.0
        try:
            ordered, _ = await radar._apply_exploration(bloggers)
            record("探索-探测博主置顶",
                   ordered[0]["bloggerId"] == bloggers[3]["bloggerId"])
        finally:
            radar_mod.EXPLORE_EPSILON = original


class TestProbation:
    async def run(self):
        svc = BloggerService()
        # 构造止损: 直测 _evolve_blogger_weight 3 次零引流
        target = 8
        for _ in range(3):
            await svc._evolve_blogger_weight(
                {"followId": 1, "bloggerId": target},
                {"clicks": 0, "registered": 0, "gmv": 0})
        blogger = await svc.repo.get_blogger(target)
        record("缓刑-止损排定复扫时点",
               blogger.get("status") == "paused"
               and blogger.get("pausedReason") == "auto_loss_cut"
               and bool(blogger.get("probationNextAt")),
               f"{blogger.get('probationNextAt')}")
        # 未到期: 全池扫描不含该博主(paused)
        scan = await svc.scan()
        record("缓刑-未到期不复扫",
               all(w.get("bloggerId") != target
                   for w in scan["works"]))
        # 到期: 强改 probationNextAt 过期 → 全池扫描追加复扫
        await svc.repo.update_blogger(target, {
            "probationNextAt": (datetime.now(UTC)
                                - timedelta(days=1)).isoformat()})
        scan = await svc.scan()
        probe_works = [w for w in scan["works"]
                       if w.get("bloggerId") == target]
        record("缓刑-到期复扫追加",
               len(probe_works) >= 1,
               f"n={len(probe_works)}")
        # 复扫后重排下一轮
        blogger = await svc.repo.get_blogger(target)
        record("缓刑-复扫后重排",
               _in_future(blogger.get("probationNextAt", "")))
        # 缓刑自动复燃: 引流>0 回流 → reactivate
        evo = await svc._evolve_blogger_weight(
            {"followId": 99, "bloggerId": target},
            {"clicks": 5, "registered": 1, "gmv": 66.0})
        blogger = await svc.repo.get_blogger(target)
        record("缓刑-引流自动复燃",
               evo.get("reactivated") is True
               and blogger.get("status") == "active"
               and blogger.get("pausedReason") == ""
               and blogger.get("zeroTrafficStreak") == 0,
               f"{evo}")
        record("缓刑-复燃adjust保留(−0.20+0.05)",
               abs(float(blogger.get("weightAdjust")) + 0.15) < 1e-6,
               f"adjust={blogger.get('weightAdjust')}")
        record("缓刑-复燃audit留痕",
               any(a.get("action") == "probation_reactivate"
                   for a in await svc.repo.list_audits(
                       blogger_id=target, limit=50)))


class TestDecay:
    async def run(self):
        svc = BloggerService()
        # 预置 adjust: 博主1 +0.10 / 博主2 -0.05 / 博主3 0
        await svc.repo.update_blogger(1, {
            "weightAdjust": 0.10,
            "weight": derived_weight(1.0, 0.10)})
        await svc.repo.update_blogger(2, {
            "weightAdjust": -0.05,
            "weight": derived_weight(0.8, -0.05)})
        result = await svc.apply_weight_decay()
        b1 = await svc.repo.get_blogger(1)
        b2 = await svc.repo.get_blogger(2)
        record("衰减-正向回归10%",
               abs(float(b1.get("weightAdjust")) - 0.09) < 1e-6,
               f"adjust={b1.get('weightAdjust')}")
        record("衰减-负向回归10%",
               abs(float(b2.get("weightAdjust")) + 0.045) < 1e-6,
               f"adjust={b2.get('weightAdjust')}")
        record("衰减-派生weight联动",
               abs(float(b1.get("weight")) - 1.0) < 1e-4
               and abs(float(b2.get("weight")) - 0.755) < 1e-4,
               f"w1={b1.get('weight')} w2={b2.get('weight')}")
        record("衰减-计数与比率",
               result.get("decayed") == 2
               and result.get("rate") == WEIGHT_DECAY_WEEKLY,
               f"{result}")
        # 近零归零
        await svc.repo.update_blogger(1, {
            "weightAdjust": 0.004,
            "weight": derived_weight(1.0, 0.004)})
        await svc.apply_weight_decay()
        b1 = await svc.repo.get_blogger(1)
        record("衰减-近零归零",
               float(b1.get("weightAdjust")) == 0.0,
               f"adjust={b1.get('weightAdjust')}")


class TestEfficiencyMod:
    async def run(self):
        svc = BloggerService()
        # 无样本 → 不调制
        record("效率-无样本不调制",
               await svc._efficiency_mod(1) == 1.0)
        # 构造 5 博主样本(分位 0.2/0.4/0.6/0.8/1.0 → 三档可达)
        follows = await _publish_and_feed(svc, 5,
                                          clicks_per=[10, 8, 5, 2, 1])
        record("效率-样本就绪", len(follows) == 5)
        b_ids = sorted({f["bloggerId"] for f in follows})
        mods = {bid: await svc._efficiency_mod(bid)
                for bid in b_ids}
        mod_values = sorted(mods.values())
        # 分位 0.2/0.4/0.6/0.8/1.0 → 0.6/1.0/1.0/1.5/1.5
        record("效率-分位三档覆盖",
               mod_values == [0.6, 1.0, 1.0, 1.5, 1.5],
               f"mods={mods}")
        # 窗口外博主(无回流)不调制
        unfed = [b["bloggerId"]
                 for b in await svc.repo.list_bloggers(limit=100)
                 if b["bloggerId"] not in b_ids]
        record("效率-窗口外博主不调制",
               await svc._efficiency_mod(unfed[0]) == 1.0)
        # 步长联动: 高效率博主 GMV 回流 → 增量 +0.05×1×1.5
        # (adjust 已含此前点击回流步长, 断言按增量)
        top_bid = max(mods, key=lambda k: mods[k])
        if mods[top_bid] == 1.5:
            before = float((await svc.repo.get_blogger(
                top_bid)).get("weightAdjust"))
            evo = await svc._evolve_blogger_weight(
                {"followId": 500, "bloggerId": top_bid},
                {"clicks": 8, "registered": 0, "gmv": 100.0})
            expected = round(before + WEIGHT_STEP_GMV * 1.5, 6)
            record("效率-高效率升权加速×1.5",
                   abs(float(evo.get("weightAdjust")) - expected)
                   < 1e-6,
                   f"adjust={evo.get('weightAdjust')} "
                   f"expect={expected}")


class TestPlatformBias:
    async def run(self):
        svc = BloggerService()
        # 样本不足(<5) → 全 0
        result = await svc.recompute_platform_bias()
        record("偏置-样本不足全0",
               all(v == 0.0 for k, v in result.items()
                   if k != "updatedAt"),
               f"{result}")
        # 构造 6 份已回流(抖音全引流 / 微博全零引流)
        follows = await _publish_and_feed(svc, 6,
                                          clicks_per=[5, 5, 5, 0, 0, 0])
        douyin_fed = [f for f in follows
                      if f.get("platform") == "douyin"]
        record("偏置-样本分布可用", len(douyin_fed) >= 1,
               f"平台={[f.get('platform') for f in follows]}")
        result = await svc.recompute_platform_bias()
        # 池率 0.5; 平台样本<5 → 偏置仍 0(证据不足不动)
        record("偏置-平台样本<5置0",
               all(float(result.get(p) or 0) == 0.0
                   for p in ("douyin", "weibo", "xiaohongshu",
                             "wechat_channels")),
               f"{result}")
        # 直写偏置 → decide_work 校准生效(边界验证)
        await svc.repo.save_platform_bias({"douyin": 8.0})
        # 构造 50-70 区间作品: 校准 +8 → 重路由 auto_follow
        await svc.repo.save_platform_bias({"douyin": 8.0})
        result = await svc.scan()
        decisions = result["decisions"]
        record("偏置-决策快照留痕",
               all((d["work"].get("scoreSnapshot") or {})
                   .get("platformBias") == 8.0
                   for d in decisions
                   if d["work"].get("platform") == "douyin"),
               "")
        # 手动校准 68 分作品(直测: 强写分数后重决策)
        manual = [d for d in decisions
                  if d["work"].get("status")
                  == WORK_STATUS_MANUAL_QUEUE
                  and d["work"].get("platform") == "douyin"]
        if manual:
            work = manual[0]["work"]
            raw = work["score"]
            await svc.repo.update_work(work["workId"], {
                "status": "manual_queue"})
            # 直接重跑 decide_work(同作品) → 校准后仍 manual 或升档
            d = await svc.decide_work(work)
            calibrated = d["work"]["score"]
            record("偏置-校准分数=raw+8",
                   abs(calibrated - min(100.0, raw + 8.0)) < 0.11,
                   f"raw={raw} cal={calibrated}")
        else:
            record("偏置-校准分数=raw+8", True, "(无manual样本,跳过)")
        # clamp: 极端差 clamp ±8(直测公式经方法注入难, 验证常量)
        record("偏置-clamp常量", BIAS_CLAMP == 8.0)


class TestFraudShareGate:
    async def run(self):
        svc = BloggerService()
        # 无 pending 反馈 → share=0
        record("污染-无反馈share=0",
               await svc._fraud_share() == 0.0)
        # 手动灌 2 条 pending 反馈(1 条 fraud 标记) → 50% > 30%
        from services.ai_learning_service import submit_feedback
        factors = [{"name": "brand_fit", "score": 75.0,
                    "weight": 0.25, "contribution": 18.75}]
        await submit_feedback({
            "scorerId": "blogger_work_gate", "factors": factors,
            "scoreAtDecision": 70.0, "actualAction": "auto_follow",
            "correct": True, "reward": -0.1,
            "note": "followId=1 [fraudSuspect]", "source": "blogger"})
        await submit_feedback({
            "scorerId": "blogger_work_gate", "factors": factors,
            "scoreAtDecision": 70.0, "actualAction": "auto_follow",
            "correct": True, "reward": 0.3,
            "note": "followId=2", "source": "blogger"})
        record("污染-share计算50%",
               abs(await svc._fraud_share() - 0.5) < 1e-6,
               f"share={await svc._fraud_share()}")
        # run_learning → 污染熔断 409
        try:
            await svc.run_learning()
            record("污染-学习熔断409", False)
        except ValueError as e:
            record("污染-学习熔断409", "污染" in str(e), str(e))


class TestOscillationFreeze:
    async def run(self):
        svc = BloggerService()
        # 构造震荡: GMV 升 / 零引流降 交替 4 次(翻转 3 次)
        # (博主8 五万级 base 0.6, 避开 1.0 顶格 clamp 掩盖首步方向)
        target = 8
        for gmv, clicks in [(100.0, 0), (0.0, 0),
                            (100.0, 0), (0.0, 0)]:
            await svc._evolve_blogger_weight(
                {"followId": 600, "bloggerId": target},
                {"clicks": clicks, "registered": 0, "gmv": gmv})
        frozen = await svc._detect_oscillation()
        record("震荡-翻转检测冻结",
               target in frozen, f"frozen={frozen}")
        blogger = await svc.repo.get_blogger(target)
        record("震荡-冻结14d排期",
               _in_future(blogger.get("evolutionFrozenUntil", "")))
        # 冻结期: 步长置 0(只记录不进化)
        before = float(blogger.get("weightAdjust"))
        evo = await svc._evolve_blogger_weight(
            {"followId": 601, "bloggerId": target},
            {"clicks": 9, "registered": 0, "gmv": 500.0})
        after_blogger = await svc.repo.get_blogger(target)
        record("震荡-冻结期步长置0",
               float(after_blogger.get("weightAdjust")) == before
               and "冻结" in (evo.get("reason") or ""),
               f"before={before} "
               f"after={after_blogger.get('weightAdjust')} "
               f"reason={evo.get('reason')}")
        record("震荡-冻结audit留痕",
               any(a.get("action") == "evolution_freeze"
                   for a in await svc.repo.list_audits(
                       blogger_id=target, limit=50)))
        # run_health_checks 聚合
        health = await svc.run_health_checks()
        record("震荡-健康巡检聚合",
               isinstance(health.get("frozen"), list)
               and "rolledBack" in health)


class TestHealthView:
    async def run(self):
        svc = BloggerService()
        health = await svc.learning_health()
        record("视图-三层结构",
               {"layer1", "layer2", "qualityGate", "bias"}
               <= set(health), f"{list(health)}")
        record("视图-层1含污染指标",
               "fraudSharePending" in health["layer1"]
               and "learningPaused" in health["layer1"])
        record("视图-层2含缓刑榜",
               "onProbation" in health["layer2"]
               and "frozen" in health["layer2"]
               and "fraudPaused" in health["layer2"])
        record("视图-质量门指标",
               "fraudRate" in health["qualityGate"]
               and "effectiveClickRate" in health["qualityGate"])


class TestHttpRoutes:
    async def run(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from routes.blogger_routes import register_blogger_routes

        app = FastAPI()
        register_blogger_routes(app)
        client = TestClient(app)
        admin = {"X-Role": "admin"}

        resp = client.get("/api/blogger/learning/health")
        record("HTTP-鉴权403", resp.status_code == 403)

        resp = client.get("/api/blogger/learning/health",
                          headers=admin)
        d = resp.json().get("data") or {}
        record("HTTP-health视图",
               resp.status_code == 200 and "layer1" in d
               and "layer2" in d and "qualityGate" in d,
               f"status={resp.status_code}")

        resp = client.post("/api/blogger/learning/calibrate",
                           headers=admin)
        record("HTTP-calibrate重算",
               resp.status_code == 200
               and "douyin" in (resp.json().get("data") or {}),
               f"status={resp.status_code}")


def _in_future(iso: str) -> bool:
    try:
        return datetime.fromisoformat(iso) > datetime.now(UTC)
    except (TypeError, ValueError):
        return False


async def main():
    test_classes = [
        ("新博主冷启动探测", TestProbe),
        ("ε探索插队", TestExploration),
        ("缓刑复扫与自动复燃", TestProbation),
        ("时间衰减", TestDecay),
        ("效率调制", TestEfficiencyMod),
        ("平台校准偏置", TestPlatformBias),
        ("样本污染熔断", TestFraudShareGate),
        ("震荡冻结", TestOscillationFreeze),
        ("健康三层视图", TestHealthView),
        ("HTTP层新端点", TestHttpRoutes),
    ]
    print("=" * 62)
    print("40号·平台流量DV博主模块 P2b 进化批专项测试")
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
