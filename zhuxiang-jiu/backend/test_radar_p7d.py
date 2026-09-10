"""40号·雷达2.0·P7d 自主响应触发层专项测试

覆盖(设计文档《40号 P7 规划方案》§6):
    1. 任务包创建: 预演挂接自动入队/幂等/L1+预演门槛/
       46号 submit_change 留痕/溯源 ID
    2. 人机确认流(46号轨): reject 驳回留痕/approve 人工通道
       (P0 范式 reviewedBy+模块终审)/状态幂等门/404
    3. 下游派发(P6b 创作轨): 脚本回执/钩子映射/
       无人设 fail-soft(确认不回滚)/自治暂停仲裁
    4. 队列与决策依据: 状态过滤/三维分展示/溯源唯一

运行:
    $env:LOCK_MODE="asyncio"; $env:STORE_MODE="asyncio"
    python test_radar_p7d.py
"""

import asyncio
import os
import sys


# 确保使用内存模式 + LLM 关闭(规则轨确定性测试)
os.environ["LOCK_MODE"] = "asyncio"
os.environ["STORE_MODE"] = "asyncio"
os.environ.pop("LLM_API_KEY", None)
os.environ["LLM_ENABLED"] = "off"

from services.radar_task_service import (
    RadarTaskService, map_hook_type, build_decision_basis,
    GOV_SCORER_ID,
)
from services.radar_forecast_service import RadarForecastService
from services.radar_score_service import RadarScoreService
from repositories.radar_repository import (
    RadarRepository, CATEGORY_CURRENT, GRADE_L1, GRADE_L2,
    TASK_STATUS_PENDING, TASK_STATUS_CONFIRMED,
    TASK_STATUS_REJECTED, TASK_STATUS_DISPATCHED,
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
        "totalSlots": 0,
        "grade": kw.get("grade", ""),
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
    """造 L1 事件: 评出 L1 → 预演(含自动建任务)"""
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
            "clicks": 100, "registered": 95, "activated": 90,
            "scoredAt": "2026-09-11T00:00:00+00:00"})
    event = await seed_event(repo, title=title, summary=summary,
                             heatBase=900)
    await RadarScoreService(repo=repo).score_events(
        event_ids=[event["eventId"]])
    return event


async def seed_persona() -> None:
    """播种 P6b 原创 IP 人设(dispatch 派发源)"""
    from services.blogger_av_create_service import (
        BloggerAVCreateService)
    await BloggerAVCreateService().register_persona(
        name="小竹生活家", persona_type="original_ip",
        voice_style="medium", tone_style="warm")


# ============================================================
# 1. 任务包创建(5 断言)
# ============================================================

class TestCreate:
    async def run(self):
        reset_store()
        repo = RadarRepository()
        tasks_svc = RadarTaskService(repo=repo)
        forecast = RadarForecastService(repo=repo)

        # 1) 预演挂接: L1 rehearse 自动建任务
        #    (46号 submit_change 留痕 + 溯源 ID + pending)
        event = await make_l1(repo)
        r = await forecast.rehearse_event(event["eventId"])
        task = r["task"]
        record("创建-预演自动入队",
               r["passed"] is True
               and task["taskId"] > 0
               and task["traceId"].startswith("RADAR-")
               and task["changeId"] > 0
               and task["status"] == TASK_STATUS_PENDING,
               f"task={task}")

        # 2) 幂等: 同事件重复 ensure → 同任务
        again = await tasks_svc.ensure_task(event["eventId"])
        all_tasks = await repo.list_tasks(limit=100)
        record("创建-幂等",
               again["taskId"] == task["taskId"]
               and len(all_tasks) == 1,
               f"n={len(all_tasks)}")

        # 3) 门槛: L2 / 未预演 → 拒绝
        l2 = await seed_event(repo, title="节日囤货观察",
                              summary="礼盒消费",
                              grade=GRADE_L2)
        ungraded = await seed_event(repo, title="未评分事件",
                                    summary="观察")
        l1_unrehearsed = await make_l1(repo, title="暴雨防灾指南",
                                       summary="极端天气应对")
        ok_l2 = ok_un = ok_unre = False
        try:
            await tasks_svc.ensure_task(l2["eventId"])
        except ValueError:
            ok_l2 = True
        try:
            await tasks_svc.ensure_task(ungraded["eventId"])
        except ValueError:
            ok_un = True
        try:
            await tasks_svc.ensure_task(
                l1_unrehearsed["eventId"])
        except ValueError:
            ok_unre = True
        record("创建-L2与未预演拒绝",
               ok_l2 and ok_un and ok_unre)

        # 4) 46号留痕: pending 变更含 payload.traceId
        from services.ai_governance_service import (
            AiGovernanceService)
        changes = await AiGovernanceService().list_changes(
            status="pending", scorer_id=GOV_SCORER_ID)
        payload_ok = any(
            (c.get("payload") or {}).get("traceId")
            == task["traceId"] for c in changes["changes"])
        record("创建-46号留痕",
               changes["total"] >= 1 and payload_ok,
               f"n={changes['total']}")

        # 5) 任务包内容: 预案四件套+决策依据(三维分)
        stored = await repo.get_task(task["taskId"])
        plan = stored.get("plan") or {}
        basis = stored.get("decisionBasis") or {}
        record("创建-任务包内容",
               "recommendedAngles" in plan
               and "bannedPhrasings" in plan
               and "materialSuggestions" in plan
               and "hookDirection" in plan
               and basis.get("fit") == 85
               and basis.get("safety") == 1.0
               and basis.get("conversion") == 0.92
               and basis.get("valueScore") == 78.2
               and "riskNotes" in basis,
               f"basis={basis}")


# ============================================================
# 2. 人机确认流·46号轨(6 断言)
# ============================================================

class TestConfirm:
    async def run(self):
        reset_store()
        repo = RadarRepository()
        tasks_svc = RadarTaskService(repo=repo)
        forecast = RadarForecastService(repo=repo)
        from repositories.ai_governance_repository import (
            AiGovernance46Repository)

        # 6) reject 流: 46号驳回留痕+任务 rejected(决策回流)
        event = await make_l1(repo)
        r = await forecast.rehearse_event(event["eventId"])
        task_id = r["task"]["taskId"]
        change_id = r["task"]["changeId"]
        rr = await tasks_svc.confirm_task(
            task_id, approve=False, reviewer="admin",
            note="测试否决")
        change = await AiGovernance46Repository().get_change(
            change_id)
        record("确认-reject驳回流",
               rr["task"]["status"] == TASK_STATUS_REJECTED
               and rr["dispatched"] is False
               and change.get("status") == "rejected"
               and change.get("reviewedBy") == "admin"
               and rr["task"]["confirmedBy"] == "admin",
               f"t={rr['task'].get('status')} "
               f"c={change.get('status')}")

        # 7) 幂等门: rejected 后重复确认 → 拒绝
        ok = False
        try:
            await tasks_svc.confirm_task(task_id, approve=True)
        except ValueError:
            ok = True
        record("确认-状态幂等门", ok)

        # 8) approve 流: 46号 P0 人工通道(reviewer 留痕)+
        #    模块终审 → dispatched + 脚本回执
        await seed_persona()
        event2 = await make_l1(repo, title="暴雨囤货指南",
                              summary="应急物资准备")
        r2 = await forecast.rehearse_event(event2["eventId"])
        task2_id = r2["task"]["taskId"]
        change2_id = r2["task"]["changeId"]
        ar = await tasks_svc.confirm_task(
            task2_id, approve=True, reviewer="admin",
            note="测试确认")
        change2 = await AiGovernance46Repository().get_change(
            change2_id)
        record("确认-approve人工通道",
               ar["task"]["status"] == TASK_STATUS_DISPATCHED
               and ar["dispatched"] is True
               and change2.get("reviewedBy") == "admin"
               and int(ar["task"]["dispatchScriptId"]) > 0
               and ar["task"]["dispatchExecuted"] is True,
               f"t={ar['task'].get('status')} "
               f"sid={ar['task'].get('dispatchScriptId')}")

        # 9) 生成的脚本在 P6b 库(分镜+AI 水印)
        script_id = int(ar["task"]["dispatchScriptId"])
        script = await BloggerRepository().get_av_script(
            script_id)
        record("确认-脚本回执在库",
               script is not None
               and isinstance(script.get("storyboards"), list)
               and len(script.get("storyboards") or []) >= 3
               and script.get("personaName") == "小竹生活家",
               f"keys={list((script or {}).keys())[:8]}")

        # 10) 钩子映射: 应急事件 → hook_emotional(情感陪伴)
        record("确认-钩子映射",
               script.get("hookType") == "hook_emotional"
               and map_hook_type("情感陪伴钩+场景种草钩")
               == "hook_emotional"
               and map_hook_type("礼赠体面钩") == "hook_gift_face"
               and map_hook_type("品鉴专业钩") == "hook_tasting_pro"
               and map_hook_type("价格锚点钩") == "hook_price_anchor"
               and map_hook_type("通用") == "hook_scene_grass",
               f"hook={script.get('hookType')}")

        # 11) 404: 不存在任务
        ok404 = False
        try:
            await tasks_svc.confirm_task(99999, approve=True)
        except KeyError:
            ok404 = True
        record("确认-404语义", ok404)


# ============================================================
# 3. 派发韧性(3 断言)
# ============================================================

class TestDispatch:
    async def run(self):
        reset_store()
        repo = RadarRepository()
        tasks_svc = RadarTaskService(repo=repo)
        forecast = RadarForecastService(repo=repo)

        # 12) 无人设 → fail-soft(confirmed 保留+留痕, 不回滚)
        event = await make_l1(repo)
        r = await forecast.rehearse_event(event["eventId"])
        ar = await tasks_svc.confirm_task(
            r["task"]["taskId"], approve=True, reviewer="admin")
        record("派发-无人设fail-soft",
               ar["task"]["status"] == TASK_STATUS_CONFIRMED
               and ar["task"]["dispatchExecuted"] is False
               and "人设" in ar["task"]["dispatchError"]
               and "P6b" in ar["task"]["dispatchError"],
               f"err={ar['task'].get('dispatchError')}")

        # 13) 溯源 ID 唯一(两任务 traceId 不同)
        await seed_persona()
        event2 = await make_l1(repo, title="台风应急手册",
                              summary="防灾物资清单")
        r2 = await forecast.rehearse_event(event2["eventId"])
        record("派发-溯源唯一",
               r["task"]["traceId"] != r2["task"]["traceId"]
               and r2["task"]["changeId"] > 0,
               f"t1={r['task']['traceId']} "
               f"t2={r2['task']['traceId']}")

        # 14) 自治暂停仲裁: pause 后派发拒绝(P5d 铁律),
        #     确认保留+失败留痕
        from services.blogger_auto_govern_service import (
            BloggerAutoGovernService)
        gov = BloggerAutoGovernService()
        await gov.pause_autonomy(reason="P7d 测试暂停")
        ar2 = await tasks_svc.confirm_task(
            r2["task"]["taskId"], approve=True, reviewer="admin")
        record("派发-自治暂停仲裁",
               ar2["task"]["status"] == TASK_STATUS_CONFIRMED
               and ar2["task"]["dispatchExecuted"] is False
               and bool(ar2["task"]["dispatchError"]),
               f"err={ar2['task'].get('dispatchError')[:60]}")
        await gov.resume_autonomy(operator="admin")


# ============================================================
# 4. 队列与查询(3 断言)
# ============================================================

class TestQueue:
    async def run(self):
        reset_store()
        repo = RadarRepository()
        tasks_svc = RadarTaskService(repo=repo)
        forecast = RadarForecastService(repo=repo)

        # 15) 队列状态过滤(46号串行: 确认后再建下一任务)
        e1 = await make_l1(repo)
        r1 = await forecast.rehearse_event(e1["eventId"])
        await tasks_svc.confirm_task(
            r1["task"]["taskId"], approve=False, reviewer="admin")
        # 15b) 46号串行约束: pending 未处置时 rehearse 第二任务
        #      → fail-soft 延迟创建(预演仍成功+留痕)
        e_mid = await make_l1(repo, title="暴雨互助串行观察",
                              summary="应急物资与总线测试")
        from services.ai_governance_service import (
            AiGovernanceService)
        await AiGovernanceService().submit_change(
            scorer_id=GOV_SCORER_ID, kind="config",
            payload={"module": "占位"},
            reason="46号串行约束占位变更(测试)")
        r_mid = await forecast.rehearse_event(e_mid["eventId"])
        deferred_ok = (r_mid["passed"] is True
                        and r_mid["task"]["taskId"] == 0
                        and "延迟创建" in r_mid["task"]["note"])
        from repositories.ai_governance_repository import (
            AiGovernance46Repository)
        gov_repo = AiGovernance46Repository()
        for c in await gov_repo.list_changes(limit=100):
            if (c.get("payload") or {}).get("module") == "占位":
                await gov_repo.update_change_fields(
                    c["changeId"], {
                        "status": "rejected",
                        "reviewedBy": "admin", "reviewNote": "测试",
                        "reviewedAt": "2026-09-11T00:00:00+00:00"})
        # 15c) 占位处置后显式补建(延迟创建轨闭环)
        task_mid = await tasks_svc.ensure_task(
            e_mid["eventId"])
        deferred_ok = deferred_ok and (
            task_mid["taskId"] > 0
            and task_mid["traceId"].startswith("RADAR-"))
        await tasks_svc.confirm_task(
            task_mid["taskId"], approve=False, reviewer="admin")
        await seed_persona()
        e2 = await make_l1(repo, title="暴雨出行安全",
                          summary="极端天气提示")
        r2 = await forecast.rehearse_event(e2["eventId"])
        await tasks_svc.confirm_task(
            r2["task"]["taskId"], approve=True, reviewer="admin")
        pending = await tasks_svc.list_tasks(
            status=TASK_STATUS_PENDING)
        rejected = await tasks_svc.list_tasks(
            status=TASK_STATUS_REJECTED)
        dispatched = await tasks_svc.list_tasks(
            status=TASK_STATUS_DISPATCHED)
        record("队列-状态过滤与延迟轨",
               deferred_ok
               and len(pending) == 0 and len(rejected) == 2
               and len(dispatched) == 1
               and rejected[0]["traceId"] == r1["task"]["traceId"]
               and dispatched[0]["dispatchScriptId"] > 0,
               f"def={deferred_ok} p={len(pending)} "
               f"r={len(rejected)} d={len(dispatched)}")

        # 16) 决策依据纯函数(确认界面数据面)
        e3 = await make_l1(repo, title="暴雨社区互助新观察",
                          summary="邻里守望应急指南")
        r3 = await forecast.rehearse_event(e3["eventId"])
        task3 = await repo.get_task(r3["task"]["taskId"])
        basis = task3["decisionBasis"]
        record("队列-决策依据",
               basis.get("grade") == GRADE_L1
               and basis.get("valueScore") == 78.2
               and basis.get("lifecycle") in ("new", "rising")
               and "hookDirection" in basis
               and len(basis.get("riskNotes") or []) == 2,
               f"b={basis}")

        # 17) build_decision_basis 空快照兜底
        empty = build_decision_basis(
            {"grade": GRADE_L1, "valueScore": 80,
             "lifecycle": "new", "rehearsalPlan": {}}, None)
        record("队列-空快照兜底",
               empty["fit"] == 0 and empty["safety"] == 0.0
               and empty["grade"] == GRADE_L1,
               f"e={empty}")


async def main():
    tests = [TestCreate(), TestConfirm(), TestDispatch(),
             TestQueue()]
    for t in tests:
        await t.run()
    print("=" * 60)
    print("40号 P7d 雷达2.0 自主响应触发层专项测试")
    print("=" * 60)
    for line in RESULTS:
        print(line)
    print("-" * 60)
    print(f"通过: {PASS} / {PASS + FAIL}")
    if FAIL:
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
