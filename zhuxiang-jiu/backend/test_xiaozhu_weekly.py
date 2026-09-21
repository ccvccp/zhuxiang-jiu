"""小竹 P4 语音数据周报测试(聚合/环比/快照/幂等/站内信/调度)

运行:
    $env:LOCK_MODE="asyncio"; $env:STORE_MODE="asyncio"; $env:AUTH_MODE="compat"
    python test_xiaozhu_weekly.py
"""
import asyncio
import os
import sys
from unittest.mock import AsyncMock, patch

os.environ["LOCK_MODE"] = "asyncio"
os.environ["STORE_MODE"] = "asyncio"
os.environ["AUTH_MODE"] = "compat"
os.environ["XIAOZHU_MODE"] = "assist"
os.environ["LLM_ENABLED"] = "off"

from repositories.store import reset_store

PASS = 0
FAIL = 0
RESULTS = []


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        RESULTS.append(f"  [PASS] {name}")
    else:
        FAIL += 1
        RESULTS.append(f"  [FAIL] {name} {detail}")


async def run_tests():
    reset_store()
    from repositories.xiaozhu_repository import (
        Xiaozhu48Repository,
    )
    from services.xiaozhu_service import XiaozhuService
    from services.xiaozhu_weekly_service import (
        XiaozhuWeeklyService,
    )
    from fastapi.testclient import TestClient
    from main import app

    print("[W 语音数据周报]")
    svc = XiaozhuService()
    repo = Xiaozhu48Repository()
    weekly = XiaozhuWeeklyService()
    client = TestClient(app)

    # 造数据: 两轮语音(1 正常 cart.add + 1 asr_failed) + 👎 +
    # learn 词条命中
    sid = (await svc.open_session(3001, "voice"))["sessionId"]
    r = client.post(f"/api/xiaozhu/sessions/{sid}/voice",
                    headers={"X-Member-Id": "3001"},
                    json={"textTranscript": "小竹，加入国五车",
                          "durationSec": 2.0, "streamBytes": 64000})
    j = r.json()
    turn = j.get("turn") or {}
    client.post(
        f"/api/xiaozhu/sessions/{sid}/turns/{turn.get('turnId')}"
        "/feedback",
        headers={"X-Member-Id": "3001"}, json={"rating": "down"})
    # asr_failed 轮(空文本走守卫? 直接造: textTranscript=""),
    # 更直接: 转写失败轮由 handle_voice 空转写守卫产生
    client.post(f"/api/xiaozhu/sessions/{sid}/voice",
                headers={"X-Member-Id": "3001"},
                json={"textTranscript": "#",
                      "durationSec": 1.0, "streamBytes": 32000})
    # learn 词条 + 命中
    await repo.save_asr_fix("一平", "一瓶", source="learn")
    await repo.hit_asr_fix("一平")
    await repo.hit_asr_fix("一平")

    # W1 聚合
    report = await weekly.build_weekly_report()
    u, a = report["usage"], report["asr"]
    check("W1 使用聚合(1 会话 2 语音轮)",
          u["sessions"] == 1 and u["voiceTurns"] == 2
          and u["members"] == 1, str(u))
    check("W1b 识别(asr_failed 1 轮)",
          a["failedTurns"] == 1 and a["failRate"] == 0.5,
          str(a))
    fb = report["feedback"]
    check("W1c 反馈(👎1)",
          fb["down"] == 1 and fb["downRate"] == 1.0, str(fb))
    fx = report["fixes"]
    check("W1d 词条(learn 命中 2)",
          fx["bySource"].get("learn") == 1
          and fx["learnHits"] == 2
          and fx["topHits"][0]["hits"] >= 2, str(fx["topHits"]))
    it = report["intents"]
    check("W1e 意图 top 含 asr_failed",
          any(i["intent"] == "asr_failed" for i in it["top"]),
          str(it))

    # W2 环比(无上期数据 → None)
    check("W2 环比无上期 None",
          report["mom"]["sessions"] is None
          and report["mom"]["voiceTurns"] is None,
          str(report["mom"]))

    # W3 run_weekly_round + 幂等 + force
    with patch("services.message_service.MessageService"
               ".send_message",
               AsyncMock(return_value={"success": True})) as sm, \
            patch("repositories.member_repository"
                 ".MemberRepository.list_all",
                 AsyncMock(return_value=[
                     {"id": 1, "role": "admin"},
                     {"id": 2, "role": "admin"}])):
        r1 = await weekly.run_weekly_round()
        check("W3 生成+站内信(2 admin)",
              r1["generated"] is True
              and r1["report"]["notify"]["sent"] == 2
              and sm.call_count == 2,
              f"notify={r1['report'].get('notify')}")
        r2 = await weekly.run_weekly_round()
        check("W3b 当日幂等跳过",
              r2["generated"] is False
              and "已生成" in r2["skipReason"], str(r2.get("skipReason")))
        r3 = await weekly.run_weekly_round(force=True)
        check("W3c force 覆盖生成",
              r3["generated"] is True)
    snap = await repo.load_weekly_snapshot()
    check("W3d 快照已存", snap is not None
          and snap.get("usage", {}).get("voiceTurns") == 2,
          str(snap and snap.get("usage")))

    # W4 HTTP 端点: 现算(不动快照) + snapshot=1
    before = await repo.load_weekly_snapshot()
    r = client.get("/api/xiaozhu/dashboard/voice-weekly",
                   headers={"X-Role": "admin"})
    j = r.json()
    check("W4 现算端点",
          r.status_code == 200 and j["generated"] is True
          and j["report"]["usage"]["voiceTurns"] == 2,
          str(j)[:150])
    after = await repo.load_weekly_snapshot()
    check("W4b 现算不动快照", before == after, "snapshot mutated")
    r = client.get(
        "/api/xiaozhu/dashboard/voice-weekly?snapshot=1",
        headers={"X-Role": "admin"})
    j = r.json()
    check("W4c snapshot=1 返回快照",
          r.status_code == 200 and j["generated"] is False
          and j["report"] and "notify" in j["report"],
          str(j)[:120])

    # W5 调度: 开关 + 非周一跳过
    import services.xiaozhu_scheduler as sched

    check("W5 开关默认 on", sched.weekly_enabled() is True)
    called = []

    class _FakeWeekly:
        async def run_weekly_round(self, force=False):
            called.append(1)
            return {"generated": True}

    with patch.object(sched, "weekly_enabled",
                      return_value=True), \
            patch("services.xiaozhu_weekly_service"
                 ".XiaozhuWeeklyService", _FakeWeekly):
        # 非周一(今天可能任意)——用 monkeypatch datetime?
        # 简化: 若今天恰是周一 08:00 后会真跑——直接断言行为可控
        await sched._weekly_maybe_run()
        today_is_monday_8 = (
            __import__("datetime").datetime.now().weekday() == 0
            and __import__("datetime").datetime.now().hour >= 8)
        check("W5b 调度窗口判定行为一致",
              (len(called) == 1) == today_is_monday_8,
              f"called={len(called)} monday8={today_is_monday_8}")
    # 调度循环可启动(秒级不 sleep——只验证 start 幂等)
    ok = sched.start_weekly_loop()
    check("W5c 周报循环启动幂等", ok is True
          and sched.start_weekly_loop() is True)
    sched._WEEKLY_TASK.cancel()

    await svc.delete_session(sid)


async def main():
    await run_tests()
    print()
    print(f"总计: {PASS} 通过, {FAIL} 失败")
    for line in RESULTS:
        if "[FAIL]" in line:
            print(line)
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    asyncio.run(main())
