"""46号·P6 AI 治理巡检调度器专项测试

运行方式:
    python test_ai_governance_scheduler.py

覆盖:
    - 开关与周期: 默认 off/环境 on/周期默认·下限·非法值
    - 一轮调度(无告警): 巡检执行/零通知(不发"一切正常"
      骚扰信)/统计留痕持久化
    - 一轮调度(新告警→触达): 三信号告警生成/管理员站内信
      (标题/分类/优先级/信号分组渲染)/普通会员不收/
      当日去重后零新告警零通知
    - 消息内容: 聚合单封/信号分组/处置指引
    - fail-soft: 巡检异常→errors 留痕统计仍落库;
      触达异常不影响巡检
    - start/stop: off 返回 False/on 幂等启动/停止
"""

import asyncio
import os
import sys
from datetime import UTC, datetime, timedelta

os.environ["LOCK_MODE"] = "asyncio"
os.environ["STORE_MODE"] = "asyncio"
os.environ.pop("LLM_API_KEY", None)
os.environ["LLM_ENABLED"] = "off"

PASS = 0
FAIL = 0
RESULTS = []


def record(name, passed, detail=""):
    global PASS, FAIL
    if passed:
        PASS += 1
        RESULTS.append(f"  ✓ {name}")
    else:
        FAIL += 1
        RESULTS.append(f"  ✗ {name} — {detail}")


def reset_all():
    from repositories.store import reset_store as _reset
    _reset()


async def seed_admins(count: int = 2) -> list[int]:
    """造管理员会员(43号测试范式, 幂等)+一个普通会员"""
    from repositories.member_repository import (
        MemberRepository,
    )
    repo = MemberRepository()
    ids = []
    for i in range(count):
        phone = f"1390000{8000 + i}"
        m = await repo.get_by_phone(phone)
        if m is None:
            m = await repo.create({
                "phone": phone, "nickname": f"admin{i}",
                "role": "admin", "status": "active"})
        ids.append(int(m["id"]))
    member = await repo.get_by_phone("13900009999")
    if member is None:
        await repo.create({
            "phone": "13900009999", "nickname": "member",
            "role": "member", "status": "active"})
    return ids


async def latest_ai46_messages(user_id: int) -> list[dict]:
    """查收件箱里 CATEGORY_SECURITY 最新消息"""
    from repositories.message_repository import (
        MessageRepository,
    )
    msgs = await MessageRepository().list_messages(
        user_id=user_id, limit=30)
    return [m for m in (msgs or [])
            if m.get("category") == "security"]


async def seed_stagnation(scorer: str = "trust_value"):
    """灌停滞数据(40天前冠军+近反馈→三信号全命中)"""
    from repositories.ai_learning_repository import (
        AiLearningRepository,
    )
    repo = AiLearningRepository()
    old = (datetime.now(UTC)
           - timedelta(days=40)).isoformat()
    await repo.save_profile(scorer, {
        "champion": {"version": "v1", "weights": {},
                     "source": "default",
                     "parentVersion": "-", "stats": {},
                     "note": "", "createdAt": old}})
    for _ in range(2):
        await repo.add_feedback({
            "scorerId": scorer, "weightVersion": "v1",
            "scoreAtDecision": 50.0, "actualAction": "pass",
            "expectedAction": "pass", "correct": True,
            "factors": [], "note": "", "source": "manual",
            "status": "pending",
            "createdAt": datetime.now(UTC).isoformat()})
    await repo.save_drift(scorer, {
        "count": 5, "baselineScore": 50, "emaScore": 55,
        "baselineFactors": {}, "emaFactors": {},
        "driftScore": 0.31, "driftLevel": "high",
        "lastFeedbackAt": datetime.now(UTC).isoformat()})


class TestSwitches:
    async def run(self):
        print("[01 开关与周期]")
        import services.ai_governance_scheduler as sched
        record("默认关闭", sched.scheduler_enabled() is False)
        os.environ["AI_GOV_SCHEDULER_MODE"] = "on"
        record("环境开启",
               sched.scheduler_enabled() is True)
        del os.environ["AI_GOV_SCHEDULER_MODE"]
        record("周期默认86400",
               sched.scheduler_interval_seconds() == 86400)
        os.environ["AI_GOV_SCAN_INTERVAL"] = "60"
        record("周期下限300",
               sched.scheduler_interval_seconds() == 300)
        os.environ["AI_GOV_SCAN_INTERVAL"] = "7200"
        record("周期可调",
               sched.scheduler_interval_seconds() == 7200)
        os.environ["AI_GOV_SCAN_INTERVAL"] = "abc"
        record("非法周期回退默认",
               sched.scheduler_interval_seconds() == 86400)
        del os.environ["AI_GOV_SCAN_INTERVAL"]


class TestQuietRound:
    async def run(self):
        print("[02 一轮调度(无告警)]")
        reset_all()
        from services.ai_governance_service import (
            AiGovernanceService,
        )
        from services.ai_governance_scheduler import (
            run_scheduled_governance_tasks,
        )
        from repositories.ai_governance_repository import (
            AiGovernance46Repository,
        )
        await AiGovernanceService().sync_registry()
        await seed_admins(1)

        r = await run_scheduled_governance_tasks()
        record("调度执行成功", r.get("runs") == 1,
               str(r)[:60])
        scan = r.get("lastScan") or {}
        record("巡检执行(28档案)",
               scan.get("scorerCount") == 28
               and scan.get("scanId") is not None,
               str(scan)[:60])
        record("零新告警", scan.get("alertsNew") == 0,
               str(scan.get("alertsNew")))
        record("零通知(不发骚扰信)",
               r.get("lastNotification") is None,
               str(r.get("lastNotification")))
        record("零错误", r.get("lastErrors") == [],
               str(r.get("lastErrors")))

        # 统计留痕持久化
        stored = await AiGovernance46Repository(
        ).get_scheduler_stats()
        record("统计留痕可读回",
               stored is not None
               and stored.get("runs") == 1
               and stored.get("lastScan") is not None,
               str(stored)[:60])

        # 再跑一轮: runs 累加
        r2 = await run_scheduled_governance_tasks()
        record("runs累加", r2.get("runs") == 2,
               str(r2.get("runs")))


class TestAlertRound:
    async def run(self):
        print("[03 一轮调度(新告警→触达)]")
        reset_all()
        from services.ai_governance_service import (
            AiGovernanceService,
        )
        from services.ai_governance_scheduler import (
            run_scheduled_governance_tasks,
        )
        await AiGovernanceService().sync_registry()
        admins = await seed_admins(2)
        await seed_stagnation("trust_value")

        r = await run_scheduled_governance_tasks()
        scan = r.get("lastScan") or {}
        record("三信号新告警", scan.get("alertsNew") == 3,
               str(scan.get("alertsNew")))
        record("检测器命中",
               (scan.get("hits") or {}).get("stagnation")
               == 1,
               str(scan.get("hits")))
        note = r.get("lastNotification") or {}
        record("触达记录",
               note.get("freshAlerts") == 3
               and note.get("sent") >= 1
               and note.get("admins") >= 2,
               str(note))

        # 管理员站内信
        msgs = await latest_ai46_messages(admins[0])
        record("管理员站内信落库", len(msgs) >= 1,
               f"{len(msgs)}条")
        if msgs:
            m = msgs[0]
            record("标题口径",
                   "档案健康巡检告警" in str(m.get("title")),
                   str(m.get("title")))
            record("P1优先级",
                   m.get("priority") == "P1",
                   str(m.get("priority")))
            record("security分类",
                   m.get("category") == "security",
                   str(m.get("category")))
            content = str(m.get("content"))
            record("信号分组渲染",
                   "学习停滞" in content
                   and "反馈枯竭" in content
                   and "因子漂移高" in content,
                   content[:80])
            record("处置指引",
                   "治理看板" in content,
                   content[-80:])

        # 普通会员不收
        from repositories.member_repository import (
            MemberRepository,
        )
        member = await MemberRepository(
        ).get_by_phone("13900009999")
        m_msgs = await latest_ai46_messages(
            int(member["id"]))
        record("普通会员不收", len(m_msgs) == 0,
               f"{len(m_msgs)}条")

        # 同日再跑: 告警当日去重→零新告警零通知
        r2 = await run_scheduled_governance_tasks()
        scan2 = r2.get("lastScan") or {}
        record("当日去重零新告警",
               scan2.get("alertsNew") == 0
               and scan2.get("alertsUpdated") == 3,
               f"new={scan2.get('alertsNew')} "
               f"updated={scan2.get('alertsUpdated')}")
        record("去重后零通知",
               r2.get("lastNotification") is None,
               str(r2.get("lastNotification")))
        # 管理员收件不重复膨胀
        msgs2 = await latest_ai46_messages(admins[0])
        record("触达不重复膨胀",
               len(msgs2) == len(msgs),
               f"{len(msgs)}→{len(msgs2)}")


class TestFailSoft:
    async def run(self):
        print("[04 fail-soft]")
        reset_all()
        import services.ai_governance_scheduler as sched
        from services.ai_governance_service import (
            AiGovernanceService,
        )
        await AiGovernanceService().sync_registry()

        # 巡检异常 → errors 留痕统计仍落库
        import services.ai_governance_health as health
        orig_scan = health.AiGovernanceHealthService.scan

        async def _boom(self):
            raise RuntimeError("巡检链路瞬断")
        health.AiGovernanceHealthService.scan = _boom
        try:
            r = await sched \
                .run_scheduled_governance_tasks()
            record("巡检异常不崩溃",
                   r.get("runs") >= 1
                   and any("scan" in str(e)
                           for e in r.get("lastErrors")
                           or []),
                   str(r.get("lastErrors"))[:70])
        finally:
            health.AiGovernanceHealthService.scan = orig_scan

        # 触达异常不影响巡检留痕(成员查询挂→通知跳过)
        r = await sched.run_scheduled_governance_tasks()
        record("恢复后正常巡检",
               (r.get("lastScan") or {})
               .get("scorerCount") == 28,
               str(r.get("lastScan"))[:60])


class TestStartStop:
    async def run(self):
        print("[05 start/stop]")
        import services.ai_governance_scheduler as sched

        record("off启动返回False",
               sched.start_scheduler() is False)
        record("off状态未运行",
               sched.scheduler_running() is False)

        os.environ["AI_GOV_SCHEDULER_MODE"] = "on"
        try:
            ok = sched.start_scheduler()
            record("on启动True", ok is True, str(ok))
            record("幂等重复启动",
                   sched.start_scheduler() is True)
            record("运行状态True",
                   sched.scheduler_running() is True)
            sched.stop_scheduler()
            record("停止后未运行",
                   sched.scheduler_running() is False)
        finally:
            os.environ.pop("AI_GOV_SCHEDULER_MODE",
                           None)
            sched.stop_scheduler()


async def run_all():
    await TestSwitches().run()
    await TestQuietRound().run()
    await TestAlertRound().run()
    await TestFailSoft().run()
    await TestStartStop().run()


def main():
    asyncio.run(run_all())
    print()
    print("=" * 62)
    print("\n".join(RESULTS))
    print("=" * 62)
    print(f"总计: {PASS} 通过 / {FAIL} 失败")
    return FAIL


if __name__ == "__main__":
    sys.exit(1 if main() else 0)
