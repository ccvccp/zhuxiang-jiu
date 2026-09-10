"""40号·雷达2.0·P7b 三维价值评估专项测试

覆盖(设计文档《40号 P7 规划方案》§4):
    1. 信值契合度: 知识映射表(暴雨85/节日80/酒75/消费70)/
       无映射<40 降级/多命中取最大
    2. 合规安全系数(硬闸): 类别硬映射(时政/军事→L4)/
       政策一票否决词/政治军事扩展词/反转词×0.5/
       价值观×0.6/封禁 BGM 版权/高热度不豁免
    3. 转化潜力: 冷启动回退 0.5/历史漏斗统计/归一化上限
    4. 评分与分级: 公式 fit×safety×conv/L1 全条件/
       L2/L3 档/L4 短路优先(不进乘法)
    5. 留痕与查询: L4 屏蔽原因入库(永不静默丢弃)/
       事件分级回写+过滤/快照查询/生命周期门/
       指定事件评分/404 语义

运行:
    $env:LOCK_MODE="asyncio"; $env:STORE_MODE="asyncio"
    python test_radar_p7b.py
"""

import asyncio
import os
import sys


# 确保使用内存模式 + LLM 关闭(规则轨确定性测试)
os.environ["LOCK_MODE"] = "asyncio"
os.environ["STORE_MODE"] = "asyncio"
os.environ.pop("LLM_API_KEY", None)
os.environ["LLM_ENABLED"] = "off"

from services.radar_score_service import (
    RadarScoreService, compute_fit, compute_safety,
    grade_event, SAFETY_HARD_GATE, L1_VALUE_LINE,
    L2_VALUE_LINE, L3_FIT_DEMOTE_LINE, FIT_NO_MAP_SCORE,
    CONVERSION_COLD_START,
)
from services.radar_hub_service import RadarHubService
from repositories.radar_repository import (
    RadarRepository, CATEGORY_POLITICS, CATEGORY_MILITARY,
    CATEGORY_FINANCE, CATEGORY_HISTORY, CATEGORY_CURRENT,
    LIFECYCLE_NEW, LIFECYCLE_PEAK, GRADE_L1, GRADE_L2,
    GRADE_L3, GRADE_L4,
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


async def seed_event(repo, **kw) -> dict:
    """构造受控测试事件并直接入库"""
    event_id = await repo.next_id("event")
    event = {
        "eventId": event_id,
        "fingerprint": f"fp-{event_id:04d}",
        "channelId": kw.get("channelId", 1),
        "channelName": kw.get("channelName", "测试频道"),
        "platform": "douyin",
        "title": kw["title"],
        "summary": kw.get("summary", ""),
        "asrTranscript": kw.get("asrTranscript", ""),
        "ocrTags": kw.get("ocrTags", []),
        "bgmFingerprint": kw.get("bgmFingerprint", ""),
        "category": kw["category"],
        "heatBase": kw.get("heatBase", 500),
        "emotionDensity": 0.3, "crowdEmotion": "neutral",
        "botFiltered": False, "botShare": 0.0,
        "lifecycle": kw.get("lifecycle", LIFECYCLE_NEW),
        "totalSlots": 1,
        "firstSeenAt": "2026-09-10T00:00:00+00:00",
        "lastSeenAt": "2026-09-10T00:00:00+00:00",
    }
    return await repo.save_event(event)


async def seed_conversion(repo, category, clicks, registered,
                          activated) -> None:
    """播种转化历史样本(radar_scores 漏斗字段口径)"""
    score_id = await repo.next_id("score")
    await repo.save_score({
        "scoreId": score_id, "eventId": 0,
        "fingerprint": f"hist-{score_id:04d}",
        "category": category, "title": "历史样本",
        "fit": 0, "fitModules": [], "safety": 1.0,
        "safetyReasons": [], "conversion": 0.0,
        "conversionSamples": 0, "valueScore": 0,
        "grade": "historical", "blockedReasons": [],
        "lifecycle": "decay", "heatBase": 0,
        "clicks": clicks, "registered": registered,
        "activated": activated,
        "scoredAt": "2026-09-10T00:00:00+00:00",
    })


class State:
    """跨测试组共享的事件 ID(阶段推进)"""
    ids = {}


# ============================================================
# 1. 信值契合度(6 断言)
# ============================================================

class TestFit:
    async def run(self):
        reset_store()
        repo = RadarRepository()

        # 1) 暴雨应急映射 85
        evt = await seed_event(
            repo, title="极端天气自救指南",
            summary="暴雨洪涝应对手册",
            category=CATEGORY_CURRENT)
        fit, modules = compute_fit(evt)
        record("契合-暴雨应急映射85",
               fit == 85 and any("应急物资" in m for m in modules),
               f"fit={fit} m={modules}")

        # 2) 节日送礼映射 80
        evt = await seed_event(
            repo, title="节假日出行新变化",
            summary="文旅消费观察",
            category=CATEGORY_CURRENT)
        fit, modules = compute_fit(evt)
        record("契合-节日送礼映射80",
               fit == 80 and any("礼品" in m for m in modules),
               f"fit={fit} m={modules}")

        # 3) 酒/美食映射 75
        evt = await seed_event(
            repo, title="古代酒文化溯源",
            summary="从祭祀到餐桌的千年演变",
            category=CATEGORY_HISTORY)
        fit, modules = compute_fit(evt)
        record("契合-酒美食映射75",
               fit == 75 and any("好店" in m for m in modules),
               f"fit={fit} m={modules}")

        # 4) 消费理性映射 70
        evt = await seed_event(
            repo, title="消费降级还是理性回归",
            summary="性价比时代来临",
            category=CATEGORY_FINANCE)
        fit, modules = compute_fit(evt)
        record("契合-消费理性映射70",
               fit == 70 and any("理性消费" in m for m in modules),
               f"fit={fit} m={modules}")

        # 5) 无映射命中 → 契合<40 自动降级
        evt = await seed_event(
            repo, title="普通人财富保值指南",
            summary="通胀时代的钱包守护",
            category=CATEGORY_FINANCE)
        fit, modules = compute_fit(evt)
        record("契合-无映射降级",
               fit == FIT_NO_MAP_SCORE and fit < L3_FIT_DEMOTE_LINE
               and modules == [],
               f"fit={fit}")

        # 6) 多命中取最大(节日 80 vs 消费 70)
        evt = await seed_event(
            repo, title="节日送礼消费指南",
            summary="礼盒性价比之选",
            category=CATEGORY_CURRENT)
        fit, modules = compute_fit(evt)
        record("契合-多命中取最大",
               fit == 80 and len(modules) >= 2,
               f"fit={fit} m={modules}")


# ============================================================
# 2. 合规安全系数·硬闸(8 断言)
# ============================================================

class TestSafety:
    async def run(self):
        reset_store()
        repo = RadarRepository()

        # 7) 时政类别硬映射 → L4(宪法域)
        evt = await seed_event(
            repo, title="国际局势新动向",
            summary="专家解读", category=CATEGORY_POLITICS,
            heatBase=5000)
        safety, reasons, blocked = compute_safety(evt, set())
        record("安全-时政类别硬映射",
               safety == 0.0 and blocked
               and any("类别禁区" in b for b in blocked),
               f"s={safety} b={blocked}")

        # 8) 军事类别硬映射 → L4
        evt = await seed_event(
            repo, title="周边军情动态",
            summary="海上力量部署观察",
            category=CATEGORY_MILITARY)
        safety, reasons, blocked = compute_safety(evt, set())
        record("安全-军事类别硬映射",
               safety == 0.0 and blocked, f"s={safety}")

        # 9) 政策一票否决词(地震) → L4
        evt = await seed_event(
            repo, title="历史地震故事",
            summary="灾难记忆与重建",
            category=CATEGORY_HISTORY)
        safety, reasons, blocked = compute_safety(evt, set())
        record("安全-政策一票否决词",
               safety == 0.0
               and any("政策风险" in b for b in blocked),
               f"b={blocked}")

        # 10) 政治军事扩展词(台海) → L4(current 类别内容级捕捉)
        evt = await seed_event(
            repo, title="台海形势专家解读",
            summary="两岸关系走向分析",
            category=CATEGORY_CURRENT)
        safety, reasons, blocked = compute_safety(evt, set())
        record("安全-政治军事扩展词",
               safety == 0.0
               and any("政治军事扩展词" in b for b in blocked),
               f"b={blocked}")

        # 11) 反转词 ×0.5 → 硬闸 L4(0.5<0.6)
        evt = await seed_event(
            repo, title="辟谣:暴雨预警信息",
            summary="极端天气应对反转",
            category=CATEGORY_CURRENT)
        safety, reasons, blocked = compute_safety(evt, set())
        record("安全-反转词降半硬闸",
               safety == 0.5 and not blocked
               and safety < SAFETY_HARD_GATE
               and any("反转词" in r for r in reasons),
               f"s={safety} r={reasons}")

        # 12) 价值观词 ×0.6 → 降权但不屏蔽
        evt = await seed_event(
            repo, title="消费炫富指南",
            summary="攀比晒单风向",
            category=CATEGORY_FINANCE)
        safety, reasons, blocked = compute_safety(evt, set())
        record("安全-价值观降权不屏蔽",
               abs(safety - 0.6) < 1e-9 and not blocked
               and any("价值观" in r for r in reasons),
               f"s={safety} r={reasons}")

        # 13) 封禁 BGM 指纹 → L4 版权风险(P6b 库复用)
        evt = await seed_event(
            repo, title="社区互助新模式",
            summary="邻里守望在身边",
            category=CATEGORY_CURRENT,
            bgmFingerprint="bannedbgm001")
        safety, reasons, blocked = compute_safety(
            evt, {"bannedbgm001"})
        record("安全-封禁BGM版权",
               safety == 0.0
               and any("版权风险" in b for b in blocked),
               f"b={blocked}")

        # 14) RT-03 攻击归一(插符绕词表在归一后命中)
        evt = await seed_event(
            repo, title="疫?情期间囤货指南",
            summary="居家应急",
            category=CATEGORY_CURRENT)
        safety, reasons, blocked = compute_safety(evt, set())
        record("安全-攻击归一命中",
               safety == 0.0
               and any("政策风险" in b for b in blocked),
               f"b={blocked}")


# ============================================================
# 3. 转化潜力(3 断言)
# ============================================================

class TestConversion:
    async def run(self):
        reset_store()
        repo = RadarRepository()
        svc = RadarScoreService()
        State.ids["rain"] = (
            await seed_event(
                repo, title="极端天气自救指南",
                summary="暴雨洪涝应对手册",
                category=CATEGORY_CURRENT)).get("eventId")

        # 15) 冷启动回退 0.5(<3 样本)
        conv, n = await svc._conversion_potential(
            CATEGORY_CURRENT)
        record("转化-冷启动回退",
               conv == CONVERSION_COLD_START
               and n == 0, f"conv={conv} n={n}")

        # 16) 历史漏斗统计((注册×0.4+激活×0.6)/点击)
        for _ in range(3):
            await seed_conversion(
                repo, CATEGORY_CURRENT,
                clicks=100, registered=95, activated=90)
        conv, n = await svc._conversion_potential(
            CATEGORY_CURRENT)
        record("转化-历史漏斗统计",
               conv == 0.92 and n == 3, f"conv={conv} n={n}")

        # 17) 归一化上限(超率样本 cap 至 1.0)
        for _ in range(3):
            await seed_conversion(
                repo, CATEGORY_CURRENT,
                clicks=10, registered=20, activated=20)
        conv, n = await svc._conversion_potential(
            CATEGORY_CURRENT)
        record("转化-归一化上限",
               conv == round((0.92 * 3 + 1.0 * 3) / 6, 4)
               and conv <= 1.0,
               f"conv={conv} n={n}")


# ============================================================
# 4. 评分与 L1-L4 分级(8 断言)
# ============================================================

class TestGrading:
    async def run(self):
        reset_store()
        repo = RadarRepository()
        svc = RadarScoreService()

        State.ids["rain"] = (await seed_event(
            repo, title="极端天气自救指南",
            summary="暴雨洪涝应对手册",
            category=CATEGORY_CURRENT,
            heatBase=900))["eventId"]
        State.ids["gift"] = (await seed_event(
            repo, title="节假日出行新变化",
            summary="文旅消费观察",
            category=CATEGORY_CURRENT))["eventId"]
        State.ids["nomap"] = (await seed_event(
            repo, title="普通人财富保值指南",
            summary="通胀时代的钱包守护",
            category=CATEGORY_FINANCE))["eventId"]
        State.ids["peak"] = (await seed_event(
            repo, title="暴雨出行安全提示",
            summary="极端天气防灾指引",
            category=CATEGORY_CURRENT,
            lifecycle=LIFECYCLE_PEAK))["eventId"]
        State.ids["pol"] = (await seed_event(
            repo, title="国际局势新动向",
            summary="大国博弈进入新阶段",
            category=CATEGORY_POLITICS,
            heatBase=5000))["eventId"]
        State.ids["rumor"] = (await seed_event(
            repo, title="辟谣:暴雨预警信息",
            summary="极端天气应对反转",
            category=CATEGORY_CURRENT))["eventId"]

        # 18) L4 短路优先: 屏蔽事件价值分直接 0(不进乘法)
        r = await svc.score_events()
        by_id = {x["eventId"]: x for x in r["results"]}
        record("分级-L4短路不进乘法",
               by_id[State.ids["pol"]]["valueScore"] == 0.0
               and by_id[State.ids["pol"]]["grade"] == GRADE_L4,
               f"v={by_id[State.ids['pol']]['valueScore']}")

        # 19) 高热度不豁免(heatBase=5000 时政仍 L4)
        record("分级-高热度不豁免",
               by_id[State.ids["pol"]]["grade"] == GRADE_L4
               and r["grades"][GRADE_L4] >= 2,
               f"g={r['grades']}")

        # 20) 冷启动转化 0.5: 暴雨 85×1×0.5=42.5 → L3
        record("分级-冷启动价值42.5入L3",
               by_id[State.ids["rain"]]["valueScore"] == 42.5
               and by_id[State.ids["rain"]]["grade"] == GRADE_L3,
               f"v={by_id[State.ids['rain']]['valueScore']}")

        # 21) 无映射降级 L3(25×1×0.5=12.5)
        record("分级-无映射L3",
               by_id[State.ids["nomap"]]["valueScore"] == 12.5
               and by_id[State.ids["nomap"]]["grade"] == GRADE_L3,
               f"v={by_id[State.ids['nomap']]['valueScore']}")

        # 22) 播种历史转化 0.92 → 暴雨 85×0.92=78.2 → L1
        for _ in range(3):
            await seed_conversion(
                repo, CATEGORY_CURRENT,
                clicks=100, registered=95, activated=90)
        r2 = await svc.score_events(
            event_ids=[State.ids["rain"],
                       State.ids["gift"],
                       State.ids["peak"]])
        by2 = {x["eventId"]: x for x in r2["results"]}
        record("分级-公式与L1全条件",
               by2[State.ids["rain"]]["valueScore"] == 78.2
               and by2[State.ids["rain"]]["grade"] == GRADE_L1,
               f"v={by2[State.ids['rain']]['valueScore']}")

        # 23) L2 档(节日 80×0.92=73.6 ≥50 <75)
        record("分级-L2档",
               by2[State.ids["gift"]]["valueScore"] == 73.6
               and by2[State.ids["gift"]]["grade"] == GRADE_L2,
               f"v={by2[State.ids['gift']]['valueScore']}")

        # 24) 生命周期门(peak 高价值仍不 L1——爆发/发酵期限定)
        record("分级-生命周期门",
               by2[State.ids["peak"]]["valueScore"] == 78.2
               and by2[State.ids["peak"]]["grade"] == GRADE_L2,
               f"v={by2[State.ids['peak']]['valueScore']}"
               f" g={by2[State.ids['peak']]['grade']}")

        # 25) 纯函数分级矩阵(阈值边界确定性)
        record("分级-阈值矩阵",
               grade_event(75, 70, 0.8, "new", []) == GRADE_L1
               and grade_event(74.9, 70, 0.8, "new", []) == GRADE_L2
               and grade_event(49.9, 70, 0.9, "new", []) == GRADE_L3
               and grade_event(100, 100, 1.0, "peak", []) == GRADE_L2
               and grade_event(100, 100, 1.0, "decay", []) == GRADE_L2
               and grade_event(100, 100, 0.59, "new", []) == GRADE_L4
               and grade_event(100, 100, 1.0, "new",
                               ["x"]) == GRADE_L4,
               "阈值矩阵")


# ============================================================
# 5. 留痕与查询面(6 断言)
# ============================================================

class TestTrace:
    async def run(self):
        reset_store()
        repo = RadarRepository()
        svc = RadarScoreService()
        hub = RadarHubService()

        pol = (await seed_event(
            repo, title="国际局势新动向", summary="专家解读",
            category=CATEGORY_POLITICS, heatBase=5000))["eventId"]
        mil = (await seed_event(
            repo, title="周边军情动态", summary="海上观察",
            category=CATEGORY_MILITARY))["eventId"]
        word = (await seed_event(
            repo, title="历史地震故事", summary="灾难记忆",
            category=CATEGORY_HISTORY))["eventId"]
        rain = (await seed_event(
            repo, title="极端天气自救指南",
            summary="暴雨洪涝应对手册",
            category=CATEGORY_CURRENT))["eventId"]

        # 26) L4 屏蔽留痕备查(永不静默丢弃)
        r = await svc.score_events()
        by_id = {x["eventId"]: x for x in r["results"]}
        record("留痕-L4屏蔽原因入库",
               all(by_id[i]["blockedReasons"] for i in
                   (pol, mil, word))
               and r["grades"][GRADE_L4] == 3,
               f"g={r['grades']}")

        # 27) 事件分级回写 + grade 过滤
        l4_events = await hub.list_events(grade=GRADE_L4,
                                          limit=50)
        record("查询-事件grade过滤",
               {e["eventId"] for e in l4_events}
               == {pol, mil, word}
               and all(e["grade"] == GRADE_L4
                       for e in l4_events),
               f"n={len(l4_events)}")

        # 28) 快照查询过滤(grade/event_id)
        l1_scores = await svc.list_scores(
            grade=GRADE_L3, limit=10)
        rain_scores = await svc.list_scores(event_id=rain,
                                            limit=10)
        record("查询-快照过滤",
               len(l1_scores) >= 1
               and len(rain_scores) >= 1
               and all(s["grade"] == GRADE_L3
                       for s in l1_scores)
               and all(s["eventId"] == rain
                       for s in rain_scores),
               f"l1={len(l1_scores)} rain={len(rain_scores)}")

        # 29) 指定事件评分(单事件)
        r2 = await svc.score_events(event_ids=[rain])
        record("查询-指定事件评分",
               r2["scored"] == 1
               and r2["results"][0]["eventId"] == rain,
               f"n={r2['scored']}")

        # 30) 404 语义(事件不存在)
        try:
            await svc.score_events(event_ids=[99999])
            ok = False
        except KeyError:
            ok = True
        record("查询-404语义", ok)

        # 31) 重评产生新快照(审计轨迹, 非覆盖)
        before = len(await repo.list_scores(limit=1000))
        await svc.score_events(event_ids=[rain])
        after = len(await repo.list_scores(limit=1000))
        record("查询-重评新快照",
               after == before + 1, f"{before}->{after}")


async def main():
    tests = [TestFit(), TestSafety(), TestConversion(),
             TestGrading(), TestTrace()]
    for t in tests:
        await t.run()
    print("=" * 60)
    print("40号 P7b 雷达2.0 三维价值评估专项测试")
    print("=" * 60)
    for line in RESULTS:
        print(line)
    print("-" * 60)
    print(f"通过: {PASS} / {PASS + FAIL}")
    if FAIL:
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
