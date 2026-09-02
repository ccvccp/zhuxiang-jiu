"""40号·平台流量DV博主模块·P2a 质量门与连续奖励专项测试

覆盖(设计文档 P2 §1/§2):
    1. 引擎连续奖励兼容: _feedback_reward ±1 回退 / reward 携带
       / _hedge_update 幅值敏感 / submit_feedback 入库
    2. 点击质量门(纯函数): L1 同IP去重 / L2 /24聚簇 / L3 爬虫特征
       / 双命中 fraudSuspect / 小样本豁免
    3. 连续奖励公式: 零引流-0.1 / 小流量弱正 / 爆款+0.9 /
       quality 折扣 / clip
    4. 回流主链路: 自动路径质量门接入 / learningMetrics 留痕
       (clickQuality/reward) / 干净回流清零 fraudStreak
    5. fraud 止损: fraudStreak 累加 → 连续2次 fraud_suspect 出池
       / audit 留痕 / activate 恢复清零 / reward 强制-0.1
    6. 层2质量调制: 正向步长 × clickQuality(cluster-only 0.3)
    7. eta 覆盖: run_learning 前幂等写入 0.3(不覆盖已有配置)
    8. P1 行为兼容: 手动 clicks 路径 quality=1 步长不变

运行:
    $env:LOCK_MODE="asyncio"; $env:STORE_MODE="asyncio"
    python test_blogger_p2.py
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

from services.blogger_service import (
    BloggerService, gate_clicks, compute_reward,
)
from services.attract_service import AttractService
from services.ai_learning_service import (
    _feedback_reward, _hedge_update, submit_feedback,
    update_learning_config,
)
from repositories.blogger_repository import (
    WORK_STATUS_AUTO_FOLLOW, WORK_STATUS_MANUAL_QUEUE,
    FOLLOW_STATUS_PUBLISHED,
    WEIGHT_STEP_CLICK, QUALITY_CLUSTER,
)
from repositories.ai_learning_repository import AiLearningRepository

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
UA_OK = "Mozilla/5.0 (Windows NT 10.0) Chrome/120.0"


def _mk_click(ip: str, ua: str = UA_OK, at: str = "") -> dict:
    return {"ip": ip, "userAgent": ua, "at": at}


async def _publish_follows(svc: BloggerService, same_blogger: int = 0,
                           count: int = 1) -> list[dict]:
    """构造已发布跟随(same_blogger>0 时取同博主 N 件作品)"""
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
    if same_blogger:
        by_blogger = {}
        for w in works:
            by_blogger.setdefault(w["bloggerId"], []).append(w)
        bid = next(b for b, ws in sorted(by_blogger.items(),
                                         key=lambda kv: -len(kv[1]))
                   if len(ws) >= same_blogger)
        works = by_blogger[bid][:same_blogger]
    else:
        # 跨博主轮询取件(避免 Mock 槽位数据下前 N 件同属一博主,
        # 干扰层2进化断言)
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
        works = picked
    published = []
    for w in works:
        follow = await svc.generate_follow(w["workId"])
        published.append(await svc.publish_follow(
            follow["followId"], publish_at=PAST))
    await svc.process_publish_queue()
    return published


def _space_click_times(code: str, seconds_gap: int = 10) -> None:
    """将短码点击的 at 拉开间隔(绕开快速连点特征, 内存库直改)"""
    attract = AttractService()
    clicks = attract.repo.store.get("attract_clicks", {})
    base = datetime.now(UTC) - timedelta(hours=2)
    idx = 0
    for c in sorted(clicks.values(), key=lambda x: x.get("clickId", 0)):
        if c.get("code") != code:
            continue
        c["at"] = (base + timedelta(seconds=idx * seconds_gap)).isoformat()
        idx += 1


# ============================================================
# 1. 引擎连续奖励兼容
# ============================================================

class TestEngineReward:
    async def run(self):
        record("引擎-±1回退(correct=True)",
               _feedback_reward({"correct": True}) == 1.0)
        record("引擎-±1回退(correct=False)",
               _feedback_reward({"correct": False}) == -1.0)
        record("引擎-reward携带",
               _feedback_reward({"correct": True, "reward": 0.35})
               == 0.35)
        record("引擎-reward非法回退",
               _feedback_reward({"correct": False, "reward": "bad"})
               == -1.0)
        record("引擎-reward截断clip",
               _feedback_reward({"correct": True, "reward": 5.0})
               == 1.0)
        # Hedge 幅值敏感: reward 0.2 的权重变化 < reward 1.0
        defaults = {"a": 0.5, "b": 0.5}
        factors = [{"name": "a", "score": 80.0, "contribution": 40.0},
                   {"name": "b", "score": 20.0, "contribution": 10.0}]
        fb_strong = {"correct": True, "factors": factors}
        fb_weak = {"correct": True, "reward": 0.2, "factors": factors}
        w_strong = _hedge_update(dict(defaults), defaults,
                                 [fb_strong], 0.5, 3.0)
        w_weak = _hedge_update(dict(defaults), defaults,
                               [fb_weak], 0.5, 3.0)
        record("引擎-幅值敏感(弱奖励变化小)",
               abs(w_weak["a"] - 0.5) < abs(w_strong["a"] - 0.5)
               and w_weak["a"] > 0.5,
               f"strong={w_strong['a']} weak={w_weak['a']}")
        # submit_feedback 入库 reward
        result = await submit_feedback({
            "scorerId": "blogger_work_gate",
            "factors": [{"name": "brand_fit", "score": 75.0,
                         "weight": 0.25, "contribution": 18.75}],
            "scoreAtDecision": 70.0, "actualAction": "auto_follow",
            "correct": True, "reward": 0.42, "source": "test"})
        repo = AiLearningRepository()
        feedbacks = await repo.list_feedback(
            "blogger_work_gate", limit=10)
        stored = next((f for f in feedbacks
                       if f["feedbackId"] == result["feedbackId"]),
                      None)
        record("引擎-反馈入库携带reward",
               stored is not None
               and stored.get("reward") == 0.42,
               f"stored={stored}")


# ============================================================
# 2. 点击质量门(纯函数)
# ============================================================

class TestGateClicks:
    async def run(self):
        # 空点击
        g = gate_clicks([])
        record("门-空点击", g["effective"] == 0
               and g["quality"] == 1.0 and not g["fraudSuspect"])
        # L1 同IP去重(5次同IP → 1)
        g = gate_clicks([_mk_click("1.2.3.4") for _ in range(5)])
        record("门-L1同IP去重", g["effective"] == 1
               and g["dedupDropped"] == 4, f"{g}")
        # L1 空IP不去重
        g = gate_clicks([_mk_click("") for _ in range(3)])
        record("门-L1空IP独立计数", g["effective"] == 3, f"{g}")
        # L2 /24聚簇(6点击5个同段, UA正常, 时间拉开)
        clicks = ([_mk_click(f"10.0.0.{i}") for i in range(1, 6)]
                  + [_mk_click("200.1.1.9")])
        for i, c in enumerate(clicks):
            c["at"] = (datetime.now(UTC)
                       + timedelta(seconds=i * 30)).isoformat()
        g = gate_clicks(clicks)
        record("门-L2聚簇标记", g["clusterFlag"] is True
               and g["quality"] == QUALITY_CLUSTER
               and not g["fraudSuspect"],
               f"quality={g['quality']}")
        # L3 爬虫UA(4点击不同IP, 3个bot UA)
        clicks = ([_mk_click(f"1.2.{i}.1", "python-requests/2.31")
                   for i in range(1, 4)]
                  + [_mk_click("9.8.7.6")])
        for i, c in enumerate(clicks):
            c["at"] = (datetime.now(UTC)
                       + timedelta(seconds=i * 30)).isoformat()
        g = gate_clicks(clicks)
        record("门-L3爬虫特征标记", g["featureFlag"] is True
               and g["quality"] == 0.2 and not g["fraudSuspect"],
               f"quality={g['quality']}")
        # 双命中 → fraudSuspect(quality 0.3×0.2=0.06)
        clicks = ([_mk_click(f"10.0.0.{i}",
                             "python-requests/2.31")
                   for i in range(1, 6)]
                  + [_mk_click("10.0.0.9", "curl/8.0")])
        g = gate_clicks(clicks)
        record("门-双命中fraudSuspect",
               g["fraudSuspect"] is True and g["quality"] == 0.06,
               f"{g}")
        # 小样本豁免(2条同IP段不判聚簇)
        g = gate_clicks([_mk_click("10.0.0.1"), _mk_click("10.0.0.2")])
        record("门-小样本豁免", g["clusterFlag"] is False
               and g["quality"] == 1.0, f"{g}")


# ============================================================
# 3. 连续奖励公式
# ============================================================

class TestComputeReward:
    async def run(self):
        record("奖励-零引流-0.1",
               compute_reward(0, 0.0, 1.0, 20.0) == -0.1)
        r_small = compute_reward(5, 0.0, 1.0, 20.0)
        record("奖励-小流量弱正",
               0 < r_small < 0.5, f"r={r_small}")
        r_hit = compute_reward(1000, 1000.0, 1.0, 20.0)
        record("奖励-爆款+0.9", abs(r_hit - 0.9) < 1e-6,
               f"r={r_hit}")
        r_q = compute_reward(1000, 1000.0, 0.3, 20.0)
        record("奖励-quality折扣",
               abs(r_q - (0.3 * 1.0 - 0.1)) < 1e-6, f"r={r_q}")
        record("奖励-clip上界",
               compute_reward(10000, 99999.0, 1.0, 20.0) <= 1.0)
        r_mag = compute_reward(20, 0.0, 1.0, 20.0)
        record("奖励-P90封顶归一",
               abs(r_mag - (0.7 * 1.0 - 0.1)) < 1e-6, f"r={r_mag}")


# ============================================================
# 4/5/6. 回流主链路: 质量门接入 + fraud 止损 + 层2调制
# ============================================================

class TestFeedbackE2E:
    async def run(self):
        svc = BloggerService()
        follows = await _publish_follows(svc, count=2)
        record("E2E-发布素材就绪", len(follows) == 2)
        # ① 无点击: 零引流弱惩罚
        f1 = follows[0]
        result = await svc.submit_learning_feedback(f1["followId"])
        record("E2E-零引流reward=-0.1",
               result.get("reward") == -0.1
               and result.get("correct") is False,
               f"reward={result.get('reward')}")
        # ② 干净多点击: 质量门通过 quality=1
        f2 = follows[1]
        attract = AttractService()
        ips = ["1.2.3.4", "5.6.7.8", "9.10.11.12",
               "13.14.15.16", "17.18.19.20", "21.22.23.24"]
        for ip in ips:
            await attract.resolve_click(
                code=f2["shortCode"], utm_source=f2["platform"],
                ip=ip, user_agent=UA_OK)
        _space_click_times(f2["shortCode"])
        result = await svc.submit_learning_feedback(f2["followId"])
        metrics = (await svc.repo.get_follow(
            f2["followId"])).get("learningMetrics") or {}
        record("E2E-质量门接入(effective=raw)",
               result.get("correct") is True
               and metrics.get("clicks") == 6
               and metrics.get("clickRaw") == 6,
               f"{metrics}")
        record("E2E-clickQuality=1",
               metrics.get("clickQuality") == 1.0)
        record("E2E-正向reward",
               0 < result.get("reward", 0) < 0.9,
               f"reward={result.get('reward')}")
        # 层2: 点击升权 +0.02(quality=1, 无GMV)
        blogger = await svc.repo.get_blogger(f2["bloggerId"])
        record("E2E-层2点击步长(quality=1)",
               abs(float(blogger.get("weightAdjust"))
                   - WEIGHT_STEP_CLICK) < 1e-6,
               f"adjust={blogger.get('weightAdjust')}")


class TestFraudStop:
    async def run(self):
        svc = BloggerService()
        follows = await _publish_follows(svc, same_blogger=2)
        record("fraud-同博主2份素材", len(follows) == 2)
        attract = AttractService()
        target = follows[0]["bloggerId"]
        # 第1份: 刷量点击(同/24段 + 爬虫UA → 双命中)
        f1 = follows[0]
        for i in range(1, 6):
            await attract.resolve_click(
                code=f1["shortCode"], utm_source=f1["platform"],
                ip=f"10.0.0.{i}",
                user_agent="python-requests/2.31")
        await attract.resolve_click(
            code=f1["shortCode"], utm_source=f1["platform"],
            ip="10.0.0.9", user_agent="curl/8.0")
        result = await svc.submit_learning_feedback(f1["followId"])
        blogger = await svc.repo.get_blogger(target)
        record("fraud-第1次标记(fraudStreak=1)",
               blogger.get("fraudStreak") == 1
               and blogger.get("status") == "active",
               f"streak={blogger.get('fraudStreak')}")
        record("fraud-reward强制-0.1",
               result.get("reward") == -0.1
               and result.get("correct") is True,
               f"reward={result.get('reward')}")
        # fraud 等效零引流: zeroTrafficStreak +1
        record("fraud-等效零引流streak",
               blogger.get("zeroTrafficStreak") == 1,
               f"streak={blogger.get('zeroTrafficStreak')}")
        record("fraud-audit留痕(fraud_flag)",
               any(a.get("action") == "fraud_flag"
                   for a in await svc.repo.list_audits(
                       blogger_id=target, limit=50)))
        # 第2份: 再刷 → fraud_suspect 出池
        f2 = follows[1]
        for i in range(1, 7):
            await attract.resolve_click(
                code=f2["shortCode"], utm_source=f2["platform"],
                ip=f"172.16.0.{i}",
                user_agent="scrapy-2.11 bot")
        result = await svc.submit_learning_feedback(f2["followId"])
        blogger = await svc.repo.get_blogger(target)
        record("fraud-第2次出池(fraud_suspect)",
               blogger.get("status") == "paused"
               and blogger.get("pausedReason") == "fraud_suspect"
               and blogger.get("fraudStreak") == 2,
               f"{blogger.get('status')}/"
               f"{blogger.get('pausedReason')}")
        fraud_info = (result.get("bloggerEvolution") or {}).get("fraud")
        record("fraud-返回处置信息",
               (fraud_info or {}).get("autoPaused") is True,
               f"{fraud_info}")
        # 雷达停扫
        scan = await svc.radar.scan(blogger_ids=(target,))
        record("fraud-雷达停扫", scan["scanned"] == 0,
               f"scanned={scan['scanned']}")
        # activate 恢复: fraudStreak 清零 + adjust 保留(两次-0.05)
        activated = await svc.set_blogger_status(target, "active")
        record("fraud-恢复清零fraudStreak",
               activated.get("fraudStreak") == 0
               and activated.get("pausedReason") == "")
        record("fraud-恢复保留weightAdjust",
               abs(float(activated.get("weightAdjust")) + 0.10)
               < 1e-6,
               f"adjust={activated.get('weightAdjust')}")


class TestQualityModulation:
    async def run(self):
        svc = BloggerService()
        follows = await _publish_follows(svc, count=1)
        f1 = follows[0]
        attract = AttractService()
        # 聚簇-only(同/24段, 正常UA, 时间拉开): quality=0.3 非fraud
        for i in range(1, 7):
            await attract.resolve_click(
                code=f1["shortCode"], utm_source=f1["platform"],
                ip=f"192.168.1.{i}", user_agent=UA_OK)
        _space_click_times(f1["shortCode"])
        result = await svc.submit_learning_feedback(f1["followId"])
        metrics = (await svc.repo.get_follow(
            f1["followId"])).get("learningMetrics") or {}
        record("调制-聚簇only quality=0.3",
               metrics.get("clickQuality") == 0.3
               and metrics.get("fraudSuspect") is False,
               f"{metrics}")
        record("调制-非fraud reward按quality折扣",
               0 < result.get("reward", 0)
               < compute_reward(6, 0.0, 1.0, 20.0),
               f"reward={result.get('reward')}")
        # 层2: 点击步长 ×0.3 = 0.006
        blogger = await svc.repo.get_blogger(f1["bloggerId"])
        expected = round(WEIGHT_STEP_CLICK * 0.3, 6)
        record("调制-层2步长×quality",
               abs(float(blogger.get("weightAdjust")) - expected)
               < 1e-6,
               f"adjust={blogger.get('weightAdjust')} "
               f"expect={expected}")


class TestEtaOverride:
    async def run(self):
        svc = BloggerService()
        # 配置未自定义 → run_learning 前写入 eta(即使反馈不足409,
        # 配置已幂等落库)
        try:
            await svc.run_learning()
        except ValueError:
            pass   # 反馈不足, 预期
        repo = AiLearningRepository()
        config = await repo.get_config("blogger_work_gate")
        record("eta-未自定义时写入0.3",
               (config or {}).get("eta") == 0.3,
               f"config={config}")
        # 已自定义 → 不覆盖
        await update_learning_config("blogger_work_gate",
                                     {"eta": 0.5})
        try:
            await svc.run_learning()
        except ValueError:
            pass
        config = await repo.get_config("blogger_work_gate")
        record("eta-已自定义不覆盖",
               (config or {}).get("eta") == 0.5,
               f"config={config}")


class TestP1Compat:
    async def run(self):
        # 手动 clicks 路径: quality=1, 步长不变(P1 行为)
        svc = BloggerService()
        follows = await _publish_follows(svc, count=1)
        result = await svc.submit_learning_feedback(
            follows[0]["followId"], clicks=5)
        metrics = (await svc.repo.get_follow(
            follows[0]["followId"])).get("learningMetrics") or {}
        record("兼容-手动clicks quality=1",
               metrics.get("clickQuality") == 1.0
               and metrics.get("clicks") == 5,
               f"{metrics}")
        blogger = await svc.repo.get_blogger(
            follows[0]["bloggerId"])
        record("兼容-手动clicks步长不变",
               abs(float(blogger.get("weightAdjust"))
                   - WEIGHT_STEP_CLICK) < 1e-6,
               f"adjust={blogger.get('weightAdjust')}")
        record("兼容-手动路径reward按公式",
               0 < result.get("reward", 0) < 0.9,
               f"reward={result.get('reward')}")
        # learning_status 含 fraudSuspect 榜
        status = await svc.learning_status()
        record("兼容-status含fraudSuspect榜",
               "fraudSuspect" in status["weightEvolution"],
               f"{list(status['weightEvolution'])}")


async def main():
    test_classes = [
        ("引擎连续奖励兼容", TestEngineReward),
        ("点击质量门(纯函数)", TestGateClicks),
        ("连续奖励公式", TestComputeReward),
        ("回流主链路质量门接入", TestFeedbackE2E),
        ("fraud止损与恢复", TestFraudStop),
        ("层2步长质量调制", TestQualityModulation),
        ("eta覆盖幂等", TestEtaOverride),
        ("P1行为兼容", TestP1Compat),
    ]
    print("=" * 62)
    print("40号·平台流量DV博主模块 P2a 质量门与连续奖励专项测试")
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
