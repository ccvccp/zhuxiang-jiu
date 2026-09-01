"""36号·AI智能推广模块·雷达与决策专项测试(Service 层)

覆盖(设计文档 §3.1/§3.2):
    1. 扫描: 5平台×5条 / 评分档位确定性 / 风险一票否决
    2. 去重: 同槽位重复扫描全跳过(指纹去重)
    3. 决策三档: ≥70 自动跟进 / 50-70 人工队列 / <50 留痕放弃
    4. 人工裁决: 跟进/放弃 + 非待裁决状态 409
    5. 查询: 列表排序 / min_score 过滤 / 详情分项

运行:
    $env:LOCK_MODE="asyncio"; $env:STORE_MODE="asyncio"
    python test_promo_radar_routes.py
"""

import asyncio
import os
import sys


# 确保使用内存模式
os.environ["LOCK_MODE"] = "asyncio"
os.environ["STORE_MODE"] = "asyncio"
os.environ.pop("LLM_API_KEY", None)   # Agent 走规则轨(雷达不依赖 LLM)

from services.promo_service import PromoService
from repositories.promo_repository import (
    HOTSPOT_STATUS_ACTIVE, HOTSPOT_STATUS_ENGAGED,
    HOTSPOT_STATUS_PASSED, HOTSPOT_STATUS_DISCARDED,
    DECISION_AUTO_ENGAGE, DECISION_MANUAL_QUEUE, DECISION_PASS,
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


class TestRadarScan:
    async def run(self):
        svc = PromoService()
        result = await svc.scan()
        # 5 平台 × 5 条 = 25 扫描; 其中风险条目 5 条 discarded
        record("扫描-总量(5平台×5条)", result["scanned"] == 25,
               f"实际{result['scanned']}")
        record("扫描-新增(20条入池)", result["new"] == 20,
               f"实际{result['new']}")
        record("扫描-风险否决(5条)", result["discarded"] == 5,
               f"实际{result['discarded']}")

        # 决策三档: 每平台 2 auto + 1 manual + 1 pass
        decisions = result["decisions"]
        record("决策-总数(20)", len(decisions) == 20, f"实际{len(decisions)}")
        auto = [d for d in decisions if d["decision"] == DECISION_AUTO_ENGAGE]
        manual = [d for d in decisions if d["decision"] == DECISION_MANUAL_QUEUE]
        passed = [d for d in decisions if d["decision"] == DECISION_PASS]
        record("决策-自动跟进(10)", len(auto) == 10, f"实际{len(auto)}")
        record("决策-人工队列(5)", len(manual) == 5, f"实际{len(manual)}")
        record("决策-留痕放弃(5)", len(passed) == 5, f"实际{len(passed)}")
        record("决策-reason可解释", all(d.get("reason") for d in decisions))
        record("决策-人工队列pending态",
               all(d.get("status") == "pending" for d in manual))

        # 风险条目: 状态 discarded + riskFlags 含命中词
        discarded = await svc.list_hotspots(
            status=HOTSPOT_STATUS_DISCARDED)
        record("风险-状态discarded(5)", len(discarded) == 5,
               f"实际{len(discarded)}")
        record("风险-标记命中词",
               all("地震" in (h.get("riskFlags") or []) for h in discarded))

        # 评分档位确定性: 高相关条目(auto)分数≥70, 人工队列 50-70
        record("评分-auto档≥70",
               all(d["score"] >= 70 for d in auto),
               f"min={min((d['score'] for d in auto), default=0)}")
        record("评分-人工档50-70",
               all(50 <= d["score"] < 70 for d in manual),
               f"scores={[d['score'] for d in manual]}")
        record("评分-pass档<50",
               all(d["score"] < 50 for d in passed))


class TestDedup:
    async def run(self):
        svc = PromoService()
        first = await svc.scan()
        # 同槽位内立即重扫: 同指纹全部跳过
        second = await svc.scan()
        record("去重-重扫全跳过", second["skipped"] == 25 and second["new"] == 0,
               f"skipped={second['skipped']} new={second['new']}")
        record("去重-无新增决策", len(second["decisions"]) == 0)

        # 首扫标题集合与重扫前一致(确定性模拟源: 池内5种标题×5平台)
        titles = {h["title"] for h in first["hotspots"]}
        record("去重-确定性标题集合(5种)", len(titles) == 5,
               f"实际{len(titles)}")


class TestHotspotQuery:
    async def run(self):
        svc = PromoService()
        await svc.scan()
        hotspots = await svc.list_hotspots()
        record("查询-列表按评分降序",
               all(hotspots[i]["score"] >= hotspots[i + 1]["score"]
                   for i in range(len(hotspots) - 1)))
        high = await svc.list_hotspots(min_score=70,
                                       status=HOTSPOT_STATUS_ENGAGED)
        record("查询-min_score过滤", len(high) == 10, f"实际{len(high)}")
        detail = await svc.get_hotspot(high[0]["hotspotId"])
        record("查询-详情含分项",
               set(detail.get("scoreComponents", {})) ==
               {"heat", "velocity", "brandRelevance", "persistence"})
        record("查询-品牌命中词非空",
               len(detail.get("brandHits") or []) >= 2)
        try:
            await svc.get_hotspot(999999)
            record("查询-不存在404", False)
        except KeyError:
            record("查询-不存在404", True)


class TestManualDecide:
    async def run(self):
        svc = PromoService()
        await svc.scan()
        pending = await svc.list_decisions(pending_only=True)
        record("裁决-待裁决列表(5)", len(pending) == 5, f"实际{len(pending)}")
        target = pending[0]
        hotspot_id = target["hotspotId"]

        # 跟进
        decided = await svc.manual_decide(
            hotspot_id, engage=True, note="运营确认跟进")
        record("裁决-跟进成功",
               decided["decision"] == DECISION_AUTO_ENGAGE
               and decided["status"] == "resolved")
        hotspot = await svc.get_hotspot(hotspot_id)
        record("裁决-热点转engaged",
               hotspot["status"] == HOTSPOT_STATUS_ENGAGED)

        # 重复裁决 → 冲突
        try:
            await svc.manual_decide(hotspot_id, engage=False)
            record("裁决-重复裁决409", False)
        except ValueError:
            record("裁决-重复裁决409", True)

        # 放弃路径
        other = [d for d in await svc.list_decisions(pending_only=True)][0]
        passed = await svc.manual_decide(other["hotspotId"], engage=False)
        record("裁决-放弃成功", passed["decision"] == DECISION_PASS)
        hotspot2 = await svc.get_hotspot(other["hotspotId"])
        record("裁决-热点转passed",
               hotspot2["status"] == HOTSPOT_STATUS_PASSED)

        # 不存在的热点
        try:
            await svc.manual_decide(999999, engage=True)
            record("裁决-热点不存在404", False)
        except KeyError:
            record("裁决-热点不存在404", True)


async def main():
    test_classes = [
        ("雷达扫描与决策三档", TestRadarScan),
        ("指纹去重与确定性", TestDedup),
        ("热点查询", TestHotspotQuery),
        ("人工裁决", TestManualDecide),
    ]
    print("=" * 62)
    print("36号·AI智能推广模块 雷达与决策专项测试")
    print("=" * 62)
    for name, cls in test_classes:
        reset_store()
        print(f"\n[{name}]")
        try:
            await cls().run()
        except Exception as e:
            record(f"{name} 测试执行异常", False, str(e))

    print("\n" + "-" * 62)
    for line in RESULTS:
        print(line)
    print("-" * 62)
    print(f"总计: {PASS} 通过 / {FAIL} 失败")
    return FAIL


if __name__ == "__main__":
    sys.exit(asyncio.run(main()) and 1 or 0)
