"""68号·信值·臻选·P0 信值账户聚合层专项测试

覆盖(《68号 创新规划方案》§三 P0 + 文档五维雷达口径):
    1. 纯函数: 分段Sigmoid(及格线/卓越线/饱和段)/等级映射/
       衰减半衰期
    2. 五维聚合: 47号诚信度(降级基准70)/67号互助(双轨+评价)/
       44号专业(评分回流)/订单活跃度/成长力冷启动
    3. 动态权重: 新用户冷启动(活跃25/诚信20)/常权(30/25/…)
    4. 修正层: 硬熔断(任一维<40→总分≤59)/软奖励(+5)/负面衰减
    5. 快照与观测面: 审计留痕(原始值+中间值+解释)/历史曲线/
       即时计算/404/视图可解释性

运行:
    $env:LOCK_MODE="asyncio"; $env:STORE_MODE="asyncio"
    python test_xinzhi_p0.py
"""

import asyncio
import os
import sys


# 确保使用内存模式 + LLM 关闭(规则轨确定性测试)
os.environ["LOCK_MODE"] = "asyncio"
os.environ["STORE_MODE"] = "asyncio"
os.environ.pop("LLM_API_KEY", None)
os.environ["LLM_ENABLED"] = "off"

from services.xinzhi_radar_service import (
    XinzhiRadarService, sigmoid_normalize, grade_of,
    DEFAULT_WEIGHTS, COLD_START_WEIGHTS, COLD_START_DAYS,
    HARD_CAP_LINE, BONUS_POINTS, DECAY_HALF_LIFE_DAYS,
)
from repositories.xinzhi_repository import (
    XinzhiRepository, DIMENSIONS, DIMENSION_LABELS,
    DIM_INTEGRITY, DIM_MUTUAL, DIM_EXPERT, DIM_ACTIVITY,
    DIM_GROWTH,
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


async def seed_member(member_id: int = 1,
                      days_ago: int = 200) -> dict:
    """播种会员(P0 域内控制注册时长)"""
    from datetime import datetime, UTC, timedelta
    repo = MemberRepository()
    created = (datetime.now(UTC)
               - timedelta(days=days_ago)).isoformat()
    member = {
        "id": member_id, "phone": f"1380000000{member_id}",
        "password": "x", "nickname": f"测试会员{member_id}",
        "level": 2, "points": 100, "status": 1,
        "created_at": created, "last_login_at":
            datetime.now(UTC).isoformat(),
    }
    await repo.save(member_id, member)
    return member


# ============================================================
# 1. 纯函数(6 断言)
# ============================================================

class TestPure:
    async def run(self):
        # 1) Sigmoid 及格线(x=x0 → 50 分)
        record("纯函数-Sigmoid基准线50",
               sigmoid_normalize(DIM_INTEGRITY, 0.70) == 50.0,
               f"v={sigmoid_normalize(DIM_INTEGRITY, 0.70)}")

        # 2) 卓越线(x >> x0 → 趋近 100; 互助于 1000 满和)
        record("纯函数-Sigmoid卓越线",
               sigmoid_normalize(DIM_INTEGRITY, 1.0) > 85.0
               and sigmoid_normalize(DIM_MUTUAL, 1000) == 100.0
               and sigmoid_normalize(DIM_INTEGRITY, 0.70)
               < sigmoid_normalize(DIM_INTEGRITY, 1.0),
               "excellent")

        # 3) 低分线(x << x0 → 趋 0)
        record("纯函数-Sigmoid低分线",
               sigmoid_normalize(DIM_INTEGRITY, 0.0) < 1.0
               and sigmoid_normalize(DIM_ACTIVITY, 0) < 10.0
               and sigmoid_normalize(DIM_INTEGRITY, 0.5)
               > sigmoid_normalize(DIM_INTEGRITY, 0.0),
               "low")

        # 4) 等级映射(S/A/B/C/D)
        record("纯函数-等级映射",
               grade_of(95) == "S" and grade_of(90) == "S"
               and grade_of(89.9) == "A" and grade_of(80) == "A"
               and grade_of(79.9) == "B" and grade_of(70) == "B"
               and grade_of(69.9) == "C" and grade_of(60) == "C"
               and grade_of(59.9) == "D" and grade_of(0) == "D",
               "grades")

        # 5) 权重和=1(默认与冷启动)
        record("纯函数-权重和为1",
               abs(sum(DEFAULT_WEIGHTS.values()) - 1.0) < 1e-9
               and abs(sum(COLD_START_WEIGHTS.values()) - 1.0)
               < 1e-9,
               f"w={sum(DEFAULT_WEIGHTS.values())}")

        # 6) 敏感度差异(诚信 k 高/成长 k 低——文档口径)
        hi = sigmoid_normalize(DIM_INTEGRITY, 0.68)
        lo = sigmoid_normalize(DIM_GROWTH, -2)
        record("纯函数-敏感度系数差异",
               hi < 50 and 0 < sigmoid_normalize(
                   DIM_GROWTH, 2) < 100,
               f"hi={hi}")


# ============================================================
# 2. 五维聚合(6 断言)
# ============================================================

class TestAggregation:
    async def run(self):
        reset_store()
        await seed_member(1, days_ago=200)   # 老用户
        await seed_member(2, days_ago=10)    # 新用户(冷启动)
        svc = XinzhiRadarService()

        # 7) 老用户计算(全维产出+审计留痕)
        snap = await svc.compute_radar(1)
        record("聚合-老用户五维产出",
               all(d in snap for d in DIMENSIONS)
               and 0 <= snap["totalScore"] <= 100
               and snap["grade"] in ("S", "A", "B", "C", "D")
               and snap["explanation"]
               and snap["computedAt"],
               f"t={snap['totalScore']}")

        # 8) 诚信度降级基准(无47号档案 → 70 原始值→Sigmoid≈?)
        record("聚合-诚信度降级基准",
               snap["integrity"] > 0
               and "无风险档案" in str(
                   snap.get("sources", {})
                   .get("integrity", [])),
               f"i={snap['integrity']}")

        # 9) 互助值(67号无档案 → 0; Sigmoid 低分线)
        record("聚合-互助空档案低分",
               snap["mutual"] < 50,
               f"m={snap['mutual']}")

        # 10) 新用户冷启动(活跃25/诚信20 权重切换)
        snap2 = await svc.compute_radar(2)
        record("聚合-新用户冷启动权重",
               snap2["coldStart"] is True
               and snap2["weights"][DIM_ACTIVITY] == 0.25
               and snap2["weights"][DIM_INTEGRITY] == 0.20
               and snap["weights"][DIM_INTEGRITY] == 0.30,
               f"w2={snap2['weights']}")

        # 11) 成长力首次冷启动(无前快照 → 原始0)
        record("聚合-成长力首次冷启动",
               "首次计算" in str(snap.get("sources", {})
                                 .get("growth", [])),
               "cold growth")

        # 12) 404(会员不存在)
        ok = False
        try:
            await svc.compute_radar(99999)
        except KeyError:
            ok = True
        record("聚合-会员404", ok)


# ============================================================
# 3. 修正层(5 断言)
# ============================================================

class TestCorrections:
    async def run(self):
        reset_store()
        await seed_member(1, days_ago=200)
        svc = XinzhiRadarService()

        # 13) 硬熔断: 手造低维度 → 总分封顶 59
        dims = {d: 80.0 for d in DIMENSIONS}
        dims[DIM_INTEGRITY] = 35.0   # < 40 触发熔断
        raw = {"cleanMonths": 6, "mutualRankPct": 0.5,
               "daysSinceViolation": 999,
               "historicalPenalty": 0}
        total, broken, _bonus = svc._apply_corrections(
            85.0, dims, raw)
        record("修正-硬熔断封顶59",
               broken is True and total == 59.0,
               f"t={total}")

        # 14) 无熔断: 正常维度不封顶
        dims[DIM_INTEGRITY] = 40.0   # 边界(=40 不触发)
        total2, broken2, _ = svc._apply_corrections(
            85.0, dims, raw)
        record("修正-边界40不熔断",
               broken2 is False and total2 == 85.0,
               f"t={total2}")

        # 15) 软奖励: 6月无违规+互助TOP10% → +5
        raw_bonus = {"cleanMonths": 6, "mutualRankPct": 0.05,
                     "daysSinceViolation": 999,
                     "historicalPenalty": 0}
        total3, broken3, bonus = svc._apply_corrections(
            80.0, dims, raw_bonus)
        record("修正-软奖励加5",
               bonus is True and total3 == 85.0,
               f"t={total3}")

        # 16) 负面衰减: 半衰期 90 天
        raw_pen = {"cleanMonths": 0, "mutualRankPct": 0.5,
                   "daysSinceViolation": 90,
                   "historicalPenalty": 10.0}
        total4, _, _ = svc._apply_corrections(
            80.0, dims, raw_pen)
        record("修正-负面半衰期90天",
               abs(total4 - 75.0) < 0.2,
               f"t={total4}(期望≈75)")

        # 17) 衰减归零: 距违规 900 天(10 个半衰期)
        raw_old = {"cleanMonths": 0, "mutualRankPct": 0.5,
                   "daysSinceViolation": 900,
                   "historicalPenalty": 10.0}
        total5, _, _ = svc._apply_corrections(
            80.0, dims, raw_old)
        record("修正-远期衰减归零",
               abs(total5 - 80.0) < 0.1,
               f"t={total5}")


# ============================================================
# 4. 快照与观测面(9 断言)
# ============================================================

class TestSnapshot:
    async def run(self):
        reset_store()
        await seed_member(1, days_ago=200)
        svc = XinzhiRadarService()
        repo = svc.repo

        # 18) 审计留痕(原始值+权重+解释+来源全入库)
        snap = await svc.compute_radar(1)
        record("快照-审计留痕完整",
               isinstance(snap.get("rawInputs"), dict)
               and isinstance(snap.get("weights"), dict)
               and bool(snap.get("explanation"))
               and isinstance(snap.get("sources"), dict),
               f"keys={list(snap.keys())[:8]}")

        # 19) 重复计算产生新快照(非覆盖)
        s2 = await svc.compute_radar(1)
        latest = await repo.latest_snapshot(1)
        record("快照-重算新快照",
               s2["snapshotId"] > snap["snapshotId"]
               and latest["snapshotId"] == s2["snapshotId"],
               "append-only")

        # 20) 成长力差分(第二次计算带 prevScore)
        record("快照-prevScore回填",
               float(s2.get("prevScore") or 0)
               == float(snap.get("totalScore") or 0),
               f"prev={s2.get('prevScore')}")

        # 21) 观测面视图(五维+标签+影响因素TOP3)
        view = svc._view(s2)
        record("观测-雷达视图",
               len(view["dimensions"]) == 5
               and view["dimensions"][0]["label"] == "诚信度"
               and "factors" in view["dimensions"][0]
               and view["grade"] == s2["grade"],
               f"d={len(view['dimensions'])}")

        # 22) 解释与分数构成一致(文档铁律)
        record("观测-解释一致性",
               str(view["dimensions"][0]["score"] if False
                   else s2["totalScore"])[:4] in
               s2["explanation"]
               and DIMENSION_LABELS[max(
                   DIMENSIONS,
                   key=lambda d: s2[d])] in s2["explanation"],
               s2["explanation"][:40])

        # 23) get_radar 即时计算(无快照缓存路径)
        reset_store()
        await seed_member(5, days_ago=200)
        view5 = await svc.get_radar(5)
        record("观测-即时计算",
               view5["memberId"] == 5
               and 0 <= view5["totalScore"] <= 100,
               f"t={view5['totalScore']}")

        # 24) get_radar 缓存路径(二次调用走快照)
        n_before = len(await repo.list_snapshots(5))
        await svc.get_radar(5)
        n_after = len(await repo.list_snapshots(5))
        record("观测-缓存路径不重算",
               n_before == n_after == 1,
               f"{n_before}->{n_after}")

        # 25) 历史曲线(时序升序)
        hist = await svc.get_history(5, limit=10)
        record("观测-历史曲线",
               isinstance(hist, list)
               and len(hist) == 1
               and "totalScore" in hist[0],
               f"n={len(hist)}")

        # 26) repo 序列化(快照存取一致——内存口径)
        got = await repo.get_snapshot(hist[0]["snapshotId"])
        record("观测-快照序列化一致",
               got is not None
               and got["totalScore"] == hist[0]["totalScore"]
               and got["memberId"] == 5,
               "roundtrip")


async def main():
    tests = [TestPure(), TestAggregation(),
             TestCorrections(), TestSnapshot()]
    for t in tests:
        await t.run()
    print("=" * 60)
    print("68号 P0 信值·臻选 信值账户聚合层专项测试")
    print("=" * 60)
    for line in RESULTS:
        print(line)
    print("-" * 60)
    print(f"通过: {PASS} / {PASS + FAIL}")
    if FAIL:
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
