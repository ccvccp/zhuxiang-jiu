"""53号·小竹智能登录引擎 P4 专项测试
(驻留激励机制)

运行方式:
    python test_login53_p4.py

覆盖(53号计划 §九 P4):
    - 每日奖励领取: 语音问候互动→微量积分
      (50号 voice_login L1 台账口径)+
      memberId+dayKey 幂等(重复领取不重复发放)
    - 连续登录叙事: streak 天数累计(昨日+1/
      断档重置 1)+成就解锁(3/7/30 里程碑)
    - 退出挽留: 非弹窗拦截(intercepted=False)
      +功能教育+明日成就预告
    - off 铁律+观测面(retention/status)+端点+
      零影响(宪法断言)
"""

import asyncio
import os
import sys
from datetime import datetime, timedelta

os.environ["LOCK_MODE"] = "asyncio"
os.environ["STORE_MODE"] = "asyncio"
os.environ.pop("LLM_API_KEY", None)
os.environ["LLM_ENABLED"] = "off"
os.environ["XIAOZHU_LLM_MODE"] = "off"
os.environ["XIAOZHU_PROACTIVE_MODE"] = "off"
os.environ["LOGIN53_MODE"] = "off"

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


async def seed_retention(member_id: int,
                         day_offset: int,
                         streak: int):
    """种子: 驻留台账(指定日+streak)"""
    from services.login53_service import (
        Login53Service,
    )
    svc = Login53Service()
    day = (datetime.now()
           - timedelta(days=day_offset)
           ).strftime("%Y-%m-%d")
    await svc.repo.save_retention({
        "memberId": member_id, "dayKey": day,
        "rewardPoints": 2.0, "streakDays": streak,
        "claimedAt": f"{day}T09:00:00",
        "milestoneUnlocked": False,
    })


class TestRetentionClaim:
    """01 每日奖励领取"""

    async def run(self):
        print("[01 每日奖励领取]")
        reset_all()
        from services.login53_service import (
            Login53Service,
        )
        svc = Login53Service()

        # off 态拒绝
        try:
            await svc.retention_claim(5600)
            ok, err = False, "未拒绝"
        except ValueError as e:
            ok, err = "off" in str(e), str(e)[:30]
        record("off 态领取拒绝", ok, err)

        os.environ["LOGIN53_MODE"] = "on"

        # 首次领取(无昨日记录 → streak=1)
        r = await svc.retention_claim(
            5600, greeting="小竹早上好")
        record("首次领取(streak=1)",
               r["status"] == "claimed"
               and r["streakDays"] == 1
               and r["rewardPoints"] == 2.0,
               str((r["status"], r["streakDays"])))
        record("奖励微量(2.0 分)",
               r["rewardPoints"]
               == svc.DAILY_REWARD_POINTS,
               str(r["rewardPoints"]))
        record("50号台账落账(voice_login)",
               "已落账" in str(
                   r.get("eventNote")),
               str(r.get("eventNote"))[:40])

        # 50号事件验证(voice_login 行为)
        from repositories.voice50_repository import (
            Voice50Repository,
        )
        events = await (
            Voice50Repository().list_events(
                member_id=5600, limit=10))
        login_events = [e for e in events
                        if e.get("behavior")
                        == "voice_login"]
        record("50号事件留痕(source 标注)",
               len(login_events) == 1
               and login_events[0].get(
                   "source") == "login53_retention",
               str(len(login_events)))

        # 幂等: 当日重复领取
        r2 = await svc.retention_claim(5600)
        record("幂等(当日重复→already_claimed)",
               r2["status"] == "already_claimed"
               and r2["streakDays"] == 1,
               str(r2["status"]))
        # 幂等后无重复事件
        events2 = await (
            Voice50Repository().list_events(
                member_id=5600, limit=10))
        login_events2 = [e for e in events2
                         if e.get("behavior")
                         == "voice_login"]
        record("幂等不重复落账",
               len(login_events2) == 1,
               str(len(login_events2)))

        # 连续: 昨日 streak=2 → 今日 3 → 成就解锁
        reset_all()
        await seed_retention(
            5601, day_offset=1, streak=2)
        r3 = await svc.retention_claim(5601)
        record("连续累计(昨日2→今日3)",
               r3["streakDays"] == 3,
               str(r3["streakDays"]))
        record("成就解锁(3 天里程碑)",
               r3["unlocked"] is True
               and r3["milestone"] == 3
               and (r3["script"] or {}).get("key")
               == "streak_achieved",
               str(r3.get("milestone")))

        # 断档: 昨日无记录 → 重置 1
        reset_all()
        await seed_retention(
            5602, day_offset=2, streak=5)
        r4 = await svc.retention_claim(5602)
        record("断档重置(streak=1)",
               r4["streakDays"] == 1,
               str(r4["streakDays"]))
        os.environ["LOGIN53_MODE"] = "off"


class TestRetentionStatus:
    """02 连续登录状态"""

    async def run(self):
        print("[02 连续登录状态]")
        reset_all()
        from services.login53_service import (
            Login53Service,
        )
        svc = Login53Service()

        # 空状态(观测面——off 可访问)
        s0 = await svc.retention_status(5610)
        record("空状态(streak=0+未领取)",
               s0["streakDays"] == 0
               and s0["todayClaimed"] is False
               and s0["totalClaimedDays"] == 0,
               str(s0["streakDays"]))

        # 7 天成就状态(先种昨日再领取——今日=7)
        os.environ["LOGIN53_MODE"] = "on"
        await seed_retention(
            5610, day_offset=1, streak=6)
        await svc.retention_claim(5610)
        s1 = await svc.retention_status(5610)
        record("状态含里程碑(3/7/30)",
               s1["milestones"] == [3, 7, 30],
               str(s1["milestones"]))
        record("状态含 nextMilestone",
               s1["nextMilestone"] in (3, 7, 30),
               str(s1["nextMilestone"]))
        record("今日已领取标注",
               s1["todayClaimed"] is True,
               str(s1["todayClaimed"]))
        record("奖励规则说明",
               "语音问候" in s1["rewardRule"],
               s1["rewardRule"])

        # 成就解锁列表(streak≥3+≥7: 昨日6→今日7)
        await seed_retention(
            5611, day_offset=1, streak=6)
        await svc.retention_claim(5611)
        s2 = await svc.retention_status(5611)
        record("成就解锁列表(3+7)",
               3 in s2["unlocked"]
               and 7 in s2["unlocked"]
               and 30 not in s2["unlocked"],
               str(s2["unlocked"]))
        os.environ["LOGIN53_MODE"] = "off"


class TestExitFarewell:
    """03 退出挽留"""

    async def run(self):
        print("[03 退出挽留]")
        reset_all()
        from services.login53_service import (
            Login53Service,
        )
        svc = Login53Service()

        try:
            await svc.exit_farewell(5620)
            ok, err = False, "未拒绝"
        except ValueError as e:
            ok, err = "off" in str(e), str(e)[:30]
        record("off 态挽留拒绝", ok, err)

        os.environ["LOGIN53_MODE"] = "on"

        # 无历史挽留
        f = await svc.exit_farewell(5620)
        record("非弹窗拦截(intercepted=False)",
               f["intercepted"] is False,
               str(f["intercepted"]))
        record("挽留话术(功能教育)",
               (f["script"] or {}).get("key")
               == "proactive_exit"
               and "查信值" in (f["script"]
                                or {}).get("text", ""),
               str((f["script"] or {}).get("key")))
        record("明日预告(无记录→3 天里程碑)",
               "成就" in f["tomorrowHint"]
               and "3" in f["tomorrowHint"],
               f["tomorrowHint"][:30])

        # 有 streak 的明日成就预告(今日已领 7
        # → next=30: 再 23 天解锁)
        await seed_retention(
            5621, day_offset=1, streak=6)
        await svc.retention_claim(5621)
        f2 = await svc.exit_farewell(5621)
        record("明日成就预告(30 天里程碑)",
               "成就" in f2["tomorrowHint"]
               and "30" in f2["tomorrowHint"],
               f2["tomorrowHint"][:40])
        os.environ["LOGIN53_MODE"] = "off"


class TestEndpoints:
    """04 端点+鉴权+零影响"""

    async def run(self):
        print("[04 端点+鉴权]")
        reset_all()
        from fastapi.testclient import TestClient
        from main import app
        client = TestClient(app)
        member = {"X-Member-Id": "5630"}

        # off 态 409
        resp = client.post(
            "/api/login53/retention/claim",
            headers=member)
        record("HTTP claim off 409",
               resp.status_code == 409,
               str(resp.status_code))
        resp = client.post(
            "/api/login53/exit/farewell",
            headers=member)
        record("HTTP farewell off 409",
               resp.status_code == 409,
               str(resp.status_code))

        # 观测面: status off 可访问
        resp = client.get(
            "/api/login53/retention/status",
            headers=member)
        record("HTTP status 观测面可访问",
               resp.status_code == 200,
               str(resp.status_code))

        # on 态端到端
        os.environ["LOGIN53_MODE"] = "on"
        resp = client.post(
            "/api/login53/retention/claim",
            json={"greeting": "小竹早上好"},
            headers=member)
        body = resp.json() or {}
        record("HTTP claim 200(领取)",
               resp.status_code == 200
               and ((body.get("claim") or {})
                    .get("status")) == "claimed",
               str(resp.status_code))

        # 幂等(HTTP)
        resp = client.post(
            "/api/login53/retention/claim",
            json={}, headers=member)
        body = resp.json() or {}
        record("HTTP claim 幂等",
               ((body.get("claim") or {})
                .get("status")) == "already_claimed",
               str((body.get("claim") or {})
                   .get("status")))

        # status 回读
        resp = client.get(
            "/api/login53/retention/status",
            headers=member)
        body = resp.json() or {}
        record("HTTP status 200(今日已领)",
               resp.status_code == 200
               and ((body.get("status") or {})
                    .get("todayClaimed")) is True,
               str(resp.status_code))

        # farewell
        resp = client.post(
            "/api/login53/exit/farewell",
            headers=member)
        body = resp.json() or {}
        record("HTTP farewell 200(非拦截)",
               resp.status_code == 200
               and ((body.get("farewell") or {})
                    .get("intercepted")) is False,
               str(resp.status_code))

        # 鉴权
        resp = client.post(
            "/api/login53/retention/claim", json={})
        record("claim 无 Member 403",
               resp.status_code == 403,
               str(resp.status_code))
        resp = client.get(
            "/api/login53/retention/status")
        record("status 无 Member 403",
               resp.status_code == 403,
               str(resp.status_code))
        resp = client.post(
            "/api/login53/exit/farewell")
        record("farewell 无 Member 403",
               resp.status_code == 403,
               str(resp.status_code))

        # 零影响: 宪法断言
        from routes.entry_routes import (
            router as entry_router,
        )
        entry_count = sum(
            1 for r in entry_router.routes)
        record("39号 entry 路由零改动(24)",
               entry_count == 24, str(entry_count))
        from services.xiaozhu_voice50_rules import (
            VOICE_RULES,
        )
        record("50号14行为零改动",
               len(VOICE_RULES) == 14)
        os.environ["LOGIN53_MODE"] = "off"


async def run_all():
    await TestRetentionClaim().run()
    await TestRetentionStatus().run()
    await TestExitFarewell().run()
    await TestEndpoints().run()


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
