"""40号·雷达2.0·P7e 自治理与进化层专项测试

覆盖(设计文档《40号 P7 规划方案》§7):
    1. 效果归因闭环: traceId→script→shortCode→attract 漏斗
       (点击/注册/下单)→score 快照回填/命中判定/
       未派发拒绝/404
    2. 违规处置回流: dispatched 标记/重复拒绝/非派发拒绝
    3. 阈值自适应: 无异常不收紧/违规激增(×3 且≥20)收紧
       75→85/再收紧 85→95 封顶/收紧生效(新评分用新线)
    4. 效能周报: 触发数/命中率/误报率聚合/漏报案例库
       (L3 事后高转化入库)/留痕可查
    5. 看板: 四区聚合(事件/分级/任务/效能/阈值状态)

运行:
    $env:LOCK_MODE="asyncio"; $env:STORE_MODE="asyncio"
    python test_radar_p7e.py
"""

import asyncio
import os
import sys


# 确保使用内存模式 + LLM 关闭(规则轨确定性测试)
os.environ["LOCK_MODE"] = "asyncio"
os.environ["STORE_MODE"] = "asyncio"
os.environ.pop("LLM_API_KEY", None)
os.environ["LLM_ENABLED"] = "off"

from services.radar_govern_service import (
    RadarGovernService, funnel_conversion,
    VIOLATION_BASELINE_RATE, L1_LINE_DEFAULT, L1_LINE_CAP,
)
from services.radar_task_service import RadarTaskService
from services.radar_forecast_service import RadarForecastService
from services.radar_score_service import RadarScoreService
from services.attract_service import AttractService
from repositories.radar_repository import (
    RadarRepository, CATEGORY_CURRENT, GRADE_L1, GRADE_L2,
    GRADE_L3, TASK_STATUS_PENDING, TASK_STATUS_REJECTED,
    TASK_STATUS_DISPATCHED,
)
from repositories.blogger_repository import BloggerRepository

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
        "platform": kw.get("platform", "douyin"),
        "title": kw["title"],
        "summary": kw.get("summary", ""),
        "asrTranscript": kw.get("asrTranscript", ""),
        "ocrTags": kw.get("ocrTags", []),
        "bgmFingerprint": kw.get("bgmFingerprint", ""),
        "category": kw.get("category", CATEGORY_CURRENT),
        "heatBase": kw.get("heatBase", 500),
        "emotionDensity": 0.3, "crowdEmotion": "neutral",
        "botFiltered": False, "botShare": 0.0,
        "lifecycle": kw.get("lifecycle", "new"),
        "totalSlots": 0, "grade": kw.get("grade", ""),
        "valueScore": kw.get("valueScore", 0),
        "rehearsalPassed": kw.get("rehearsalPassed", False),
        "rehearsalPlan": kw.get("rehearsalPlan", {}),
        "predictedPhase": kw.get("predictedPhase", ""),
        "firstSeenAt": "2026-09-11T00:00:00+00:00",
        "lastSeenAt": "2026-09-11T00:00:00+00:00",
    }
    return await repo.save_event(event)


async def make_l1(repo, title="极端天气自救指南",
                  summary="暴雨洪涝应对手册") -> dict:
    """造 L1 事件: 评出 L1 → 预演(自动建任务)"""
    for _ in range(3):
        score_id = await repo.next_id("score")
        await repo.save_score({
            "scoreId": score_id, "eventId": 0,
            "fingerprint": f"hist-{score_id:04d}",
            "category": CATEGORY_CURRENT, "title": "历史样本",
            "fit": 0, "fitModules": [], "safety": 1.0,
            "safetyReasons": [], "conversion": 0.0,
            "conversionSamples": 0, "valueScore": 0,
            "grade": "historical", "blockedReasons": [],
            "lifecycle": "decay", "heatBase": 0,
            "clicks": 100, "registered": 100, "activated": 100,
            "scoredAt": "2026-09-11T00:00:00+00:00"})
    event = await seed_event(repo, title=title, summary=summary,
                             heatBase=900)
    await RadarScoreService(repo=repo).score_events(
        event_ids=[event["eventId"]])
    return event


async def dispatch_l1(repo, forecast, tasks_svc,
                      title, summary) -> dict:
    """L1 全链: 评分→预演→确认(派发)→返回任务"""
    event = await make_l1(repo, title=title, summary=summary)
    r = await forecast.rehearse_event(event["eventId"])
    ar = await tasks_svc.confirm_task(
        r["task"]["taskId"], approve=True, reviewer="admin")
    return ar["task"]


async def seed_persona() -> None:
    """播种 P6b 原创 IP 人设(dispatch 派发源)"""
    from services.blogger_av_create_service import (
        BloggerAVCreateService)
    await BloggerAVCreateService().register_persona(
        name="小竹生活家", persona_type="original_ip",
        voice_style="medium", tone_style="warm")


async def seed_funnel(short_code: str, clicks: int,
                      registered: int, activated: int) -> None:
    """播种 attract 漏斗(点击→注册→下单)"""
    attract = AttractService()
    for i in range(clicks):
        r = await attract.resolve_click(short_code)
        if i < registered:
            await attract.attach_registration(
                r["clickId"], member_id=1000 + i)
        if i < activated:
            await attract.attach_order(
                r["clickId"], order_id=f"ORD-{i}",
                order_amount=99.0)


# ============================================================
# 1. 效果归因闭环(5 断言)
# ============================================================

class TestAttribution:
    async def run(self):
        reset_store()
        repo = RadarRepository()
        gov = RadarGovernService(repo=repo)
        tasks_svc = RadarTaskService(repo=repo)
        forecast = RadarForecastService(repo=repo)
        await seed_persona()

        # 1) 全链归因: traceId→script→shortCode→漏斗→回填
        task = await dispatch_l1(repo, forecast, tasks_svc,
                                 "暴雨自救指南", "应急物资手册")
        script = await BloggerRepository().get_av_script(
            int(task["dispatchScriptId"]))
        await seed_funnel(str(script.get("shortCode")), 10, 8, 5)
        r = await gov.attribute_task(
            trace_id=task["traceId"])
        funnel = r["funnel"]
        record("归因-全链漏斗",
               funnel["clicks"] == 10
               and funnel["registered"] == 8
               and funnel["activated"] == 5
               and r["task"]["funnelTrace"]["clicks"] == 10,
               f"f={funnel}")

        # 2) 命中判定: 转化率 (8×0.4+5×0.6)/10=0.62 ≥0.5
        record("归因-命中判定",
               r["hit"] is True
               and funnel["conversionRate"] == 0.62
               and funnel_conversion(0, 0, 0) == 0.0,
               f"c={funnel['conversionRate']}")

        # 3) score 快照回填(P7b 转化统计源)
        scores = await repo.list_scores(
            event_id=int(task["eventId"]), limit=1)
        record("归因-score快照回填",
               scores and scores[0]["clicks"] == 10
               and scores[0]["registered"] == 8
               and scores[0]["activated"] == 5,
               f"s={scores[0] if scores else None}")

        # 4) 未派发任务拒绝(pending)
        # (归因已回填真实漏斗 0.62——补纯净历史样本拉回
        #  类目转化均值, 保证本事件仍 L1 可预演)
        for _ in range(10):
            score_id = await repo.next_id("score")
            await repo.save_score({
                "scoreId": score_id, "eventId": 0,
                "fingerprint": f"h2-{score_id}",
                "category": CATEGORY_CURRENT, "title": "h2",
                "fit": 0, "fitModules": [], "safety": 1.0,
                "safetyReasons": [], "conversion": 0.0,
                "conversionSamples": 0, "valueScore": 0,
                "grade": "historical", "blockedReasons": [],
                "lifecycle": "decay", "heatBase": 0,
                "clicks": 100, "registered": 100,
                "activated": 100,
                "scoredAt": "2026-09-11T00:00:00+00:00"})
        event = await make_l1(repo, title="暴雨互助观察",
                              summary="邻里应急")
        r2 = await forecast.rehearse_event(event["eventId"])
        ok_pending = False
        try:
            await gov.attribute_task(
                task_id=r2["task"]["taskId"])
        except ValueError:
            ok_pending = True
        record("归因-未派发拒绝", ok_pending)

        # 5) 404 + 参数缺失
        ok404 = ok_arg = False
        try:
            await gov.attribute_task(task_id=99999)
        except KeyError:
            ok404 = True
        try:
            await gov.attribute_task()
        except ValueError:
            ok_arg = True
        record("归因-404与参数缺失", ok404 and ok_arg)


# ============================================================
# 2. 违规处置回流 + 阈值自适应(6 断言)
# ============================================================

class TestTighten:
    async def run(self):
        reset_store()
        repo = RadarRepository()
        gov = RadarGovernService(repo=repo)
        tasks_svc = RadarTaskService(repo=repo)
        forecast = RadarForecastService(repo=repo)
        await seed_persona()

        # 6) 违规标记: dispatched 任务 + 重复拒绝
        task = await dispatch_l1(repo, forecast, tasks_svc,
                                 "暴雨防灾指南", "极端天气应对")
        marked = await gov.report_violation(
            task["taskId"], note="测试违规回流")
        ok_dup = False
        try:
            await gov.report_violation(task["taskId"])
        except ValueError:
            ok_dup = True
        record("违规-标记与重复拒绝",
               marked.get("violationMarked") is True
               and marked.get("violationNote") == "测试违规回流"
               and ok_dup,
               f"m={marked.get('violationMarked')}")

        # 7) 无异常不收紧: 样本 1<20 → 阈值不变 75
        r = await gov.tighten_threshold()
        record("收紧-无异常不变",
               r["tightened"] is False
               and r["newLine"] == L1_LINE_DEFAULT
               and r["currentLine"] == L1_LINE_DEFAULT,
               f"r={r['newLine']}")

        # 8) 违规激增(≥20 样本 ×3 基线)→ 75→85
        for i in range(21):
            t = await dispatch_l1(
                repo, forecast, tasks_svc,
                f"暴雨应急手册第{i}期", "极端天气互助")
            if i < 7:   # 7/22 ≈ 0.318 > 3×0.05=0.15
                await gov.report_violation(t["taskId"])
        r = await gov.tighten_threshold()
        record("收紧-违规激增75→85",
               r["tightened"] is True
               and r["currentLine"] == 75
               and r["newLine"] == 85
               and r["stats"]["sample"] == 22
               and r["stats"]["violations"] == 8
               and r["stats"]["violationRate"] > 3
               * VIOLATION_BASELINE_RATE
               and "只紧不松" in r["auditNote"],
               f"r={r.get('newLine')} s={r['stats']}")

        # 9) 再收紧 85→95(封顶) + 已封顶不再收
        for i in range(3):
            t = await dispatch_l1(
                repo, forecast, tasks_svc,
                f"暴雨二次危机{i}", "应急响应")
            await gov.report_violation(t["taskId"])
        r2 = await gov.tighten_threshold()
        r3 = await gov.tighten_threshold()
        record("收紧-封顶95",
               r2["newLine"] == 95
               and r3["tightened"] is False
               and r3["newLine"] == L1_LINE_CAP
               and "上限" in r3["auditNote"],
               f"r2={r2['newLine']} r3={r3['newLine']}")

        # 10) 收紧生效: 85 分事件(满转化)重评 → L2(<95)
        event = await make_l1(repo, title="暴雨新观察",
                              summary="应急物资")
        s = await RadarScoreService(repo=repo).score_events(
            event_ids=[event["eventId"]])
        record("收紧-生效链",
               s["results"][0]["valueScore"] == 85.0
               and s["results"][0]["grade"] == GRADE_L2,
               f"v={s['results'][0]['valueScore']} "
               f"g={s['results'][0]['grade']}")

        # 11) 只紧不松: 阈值留痕全是 tighten 且线单调不降
        history = await repo.list_efficiency(kind="threshold",
                                             limit=100)
        lines = [75] + [int(h["newLine"]) for h in history]
        monotonic = all(lines[i] <= lines[i + 1]
                        for i in range(len(lines) - 1))
        record("收紧-只紧不松留痕",
               len(history) == 2
               and all(h["action"] == "tighten"
                       for h in history)
               and monotonic
               and lines == [75, 85, 95],
               f"lines={lines}")


# ============================================================
# 3. 效能周报(4 断言)
# ============================================================

class TestWeekly:
    async def run(self):
        reset_store()
        repo = RadarRepository()
        gov = RadarGovernService(repo=repo)
        tasks_svc = RadarTaskService(repo=repo)
        forecast = RadarForecastService(repo=repo)
        await seed_persona()

        # 12) 周报聚合: 触发/命中/误报
        t1 = await dispatch_l1(repo, forecast, tasks_svc,
                               "暴雨自救指南", "应急手册")
        await dispatch_l1(repo, forecast, tasks_svc,
                          "暴雨囤货指南", "应急物资")
        # 1 个 reject(误报)
        event = await make_l1(repo, title="暴雨出行提示",
                              summary="极端天气")
        r = await forecast.rehearse_event(event["eventId"])
        await tasks_svc.confirm_task(
            r["task"]["taskId"], approve=False, reviewer="admin")
        # t1 播种漏斗并归因(命中)
        script = await BloggerRepository().get_av_script(
            int(t1["dispatchScriptId"]))
        await seed_funnel(str(script.get("shortCode")), 10, 8, 5)
        await gov.attribute_task(task_id=t1["taskId"])
        report = await gov.generate_weekly_report()
        record("周报-聚合",
               report["triggered"] == 3
               and report["dispatched"] == 2
               and report["rejected"] == 1
               and report["hits"] == 1
               and report["hitRate"] == 0.5
               and report["falsePositiveRate"]
               == round(1 / 3, 4),
               f"r={report['triggered']}/"
               f"{report['dispatched']}/{report['hits']} "
               f"hr={report['hitRate']} "
               f"fp={report['falsePositiveRate']}")

        # 13) 漏报案例库: L3 事后高转化入库
        l3_score_id = await repo.next_id("score")
        await repo.save_score({
            "scoreId": l3_score_id, "eventId": 888,
            "fingerprint": f"missed-{l3_score_id:04d}",
            "category": CATEGORY_CURRENT,
            "title": "漏报观察事件", "fit": 25,
            "fitModules": [], "safety": 1.0,
            "safetyReasons": [], "conversion": 0.5,
            "conversionSamples": 0, "valueScore": 12.5,
            "grade": GRADE_L3, "blockedReasons": [],
            "lifecycle": "decay", "heatBase": 300,
            "clicks": 50, "registered": 45, "activated": 40,
            "scoredAt": "2026-09-11T00:00:00+00:00"})
        report = await gov.generate_weekly_report()
        missed = report["missedCases"]
        record("周报-漏报案例库",
               len(missed) == 1
               and missed[0]["eventId"] == 888
               and missed[0]["conversionRate"] == 0.84
               and "46号" in missed[0]["lesson"],
               f"m={missed}")

        # 14) 留痕可查(kind 过滤)
        weeklies = await gov.list_reports(kind="weekly")
        record("周报-留痕可查",
               len(weeklies) == 2
               and all(w["kind"] == "weekly" for w in weeklies)
               and weeklies[0]["reportId"] > 0,
               f"n={len(weeklies)}")


# ============================================================
# 4. 中枢看板(3 断言)
# ============================================================

class TestDashboard:
    async def run(self):
        reset_store()
        repo = RadarRepository()
        gov = RadarGovernService(repo=repo)
        scorer = RadarScoreService(repo=repo)

        # 15) 造数据: 3 事件(L1/L2/L4) + 2 任务 + 1 周报
        for _ in range(3):
            score_id = await repo.next_id("score")
            await repo.save_score({
                "scoreId": score_id, "eventId": 0,
                "fingerprint": f"h-{score_id}",
                "category": CATEGORY_CURRENT, "title": "h",
                "fit": 0, "fitModules": [], "safety": 1.0,
                "safetyReasons": [], "conversion": 0.0,
                "conversionSamples": 0, "valueScore": 0,
                "grade": "historical", "blockedReasons": [],
                "lifecycle": "decay", "heatBase": 0,
                "clicks": 100, "registered": 95,
                "activated": 90,
                "scoredAt": "2026-09-11T00:00:00+00:00"})
        e1 = await seed_event(repo, title="暴雨自救指南",
                              summary="应急手册")
        e2 = await seed_event(repo, title="节日囤货观察",
                              summary="礼盒消费")
        e3 = await seed_event(repo, title="国际局势动向",
                              summary="专家解读",
                              category="politics")
        await scorer.score_events(event_ids=[
            e1["eventId"], e2["eventId"], e3["eventId"]])
        task_id = await repo.next_id("task")
        await repo.save_task({
            "taskId": task_id, "traceId": "RADAR-000001",
            "eventId": e1["eventId"], "changeId": 1,
            "status": TASK_STATUS_DISPATCHED, "plan": {},
            "decisionBasis": {}, "downstream": "p6b_create",
            "dispatchError": "", "dispatchScriptId": 7,
            "dispatchExecuted": True, "requestedBy": "t",
            "confirmedBy": "admin", "confirmNote": "",
            "createdAt": "2026-09-11T00:00:00+00:00",
            "confirmedAt": "2026-09-11T00:00:00+00:00"})
        await repo.update_task(task_id, {
            "violationMarked": True,
            "violationNote": "看板测试"})
        await gov.generate_weekly_report()
        d = await gov.dashboard()

        record("看板-四区聚合",
               d["events"]["total"] == 3
               and d["events"]["byGrade"].get("L1") == 1
               and d["events"]["byGrade"].get("L2") == 1
               and d["events"]["byGrade"].get("L4") == 1
               and d["tasks"]["total"] == 1
               and d["tasks"]["byStatus"].get("dispatched") == 1
               and d["tasks"]["violations"] == 1
               and d["efficiency"].get("kind") == "weekly",
               f"d={d['events']} t={d['tasks']}")

        # 16) 阈值状态区
        record("看板-阈值状态",
               d["threshold"]["currentL1Line"] == 75
               and d["threshold"]["cap"] == L1_LINE_CAP
               and d["threshold"]["tightenHistory"] == 0,
               f"t={d['threshold']}")

        # 17) 生命周期分布 + 空周报兜底口径
        reset_store()
        d2 = await RadarGovernService().dashboard()
        record("看板-空库兜底",
               d2["events"]["total"] == 0
               and "暂无周报" in d2["efficiency"].get(
                   "note", "")
               and d2["threshold"]["currentL1Line"] == 75,
               f"d2={d2['efficiency']}")


async def main():
    tests = [TestAttribution(), TestTighten(), TestWeekly(),
             TestDashboard()]
    for t in tests:
        await t.run()
    print("=" * 60)
    print("40号 P7e 雷达2.0 自治理与进化层专项测试")
    print("=" * 60)
    for line in RESULTS:
        print(line)
    print("-" * 60)
    print(f"通过: {PASS} / {PASS + FAIL}")
    if FAIL:
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
