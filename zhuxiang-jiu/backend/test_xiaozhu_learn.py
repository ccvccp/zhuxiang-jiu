"""小竹 P3 学习进化测试(👎→队列→建议→采纳→词条生效闭环)

覆盖:
    - 👎 入队(HTTP feedback, 含轮次上下文)/同轮防重/👍 不入队
    - 队列列表+统计(HTTP GET dashboard/learn-queue)
    - LLM 建议轨: 开关 off 拒 / on+mock chat 落 suggestion
    - 采纳闭环: 词条落表(source=learn) + 队列 adopted +
      下一轮转写修正即时生效(修正+热词双通道)
    - 忽略 / learn 词条可删(非保护来源) / 队列上限

运行:
    $env:LOCK_MODE="asyncio"; $env:STORE_MODE="asyncio"; $env:AUTH_MODE="compat"
    python test_xiaozhu_learn.py
"""
import asyncio
import json
import os
import sys

os.environ["LOCK_MODE"] = "asyncio"
os.environ["STORE_MODE"] = "asyncio"
os.environ["AUTH_MODE"] = "compat"
os.environ["XIAOZHU_MODE"] = "assist"
os.environ["LLM_ENABLED"] = "off"
os.environ["XIAOZHU_LLM_MODE"] = "off"
os.environ.pop("XIAOZHU_LEARN_LLM", None)

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
    from unittest.mock import patch
    from fastapi.testclient import TestClient
    from main import app
    from services.xiaozhu_service import XiaozhuService
    from repositories.xiaozhu_repository import Xiaozhu48Repository

    print("[L 学习进化闭环]")
    svc = XiaozhuService()
    repo = Xiaozhu48Repository()
    client = TestClient(app)

    # 造一轮含误听词的语音轮(流式轨 textTranscript)
    sid = (await svc.open_session(3001, "voice"))["sessionId"]
    r = client.post(f"/api/xiaozhu/sessions/{sid}/voice",
                    headers={"X-Member-Id": "3001"},
                    json={"textTranscript": "小竹，加入国五车",
                          "durationSec": 2.0, "streamBytes": 64000})
    j = r.json()
    turn_id = (j.get("turn") or {}).get("turnId")
    seq = (j.get("turn") or {}).get("seq")
    check("L0 基线轮(内置修正已应用)",
          (j.get("turn") or {}).get("rawText")
          == "小竹，加入购物车", str((j.get("turn") or {}).get("rawText")))

    # L1 👎 入队(HTTP feedback → 队列, 含上下文)
    r = client.post(
        f"/api/xiaozhu/sessions/{sid}/turns/{turn_id}/feedback",
        headers={"X-Member-Id": "3001"},
        json={"rating": "down"})
    check("L1 👎 反馈 200", r.status_code == 200, str(r.status_code))
    queue = await repo.list_learn_queue("pending")
    key = f"{sid}:{seq}"
    entry = queue.get(key) or {}
    check("L1b 入队(含上下文)",
          key in queue and entry.get("rawText")
          == "小竹，加入购物车"
          and entry.get("channel") == "voice",
          f"key={key} keys={list(queue)[:3]}")

    # L2 同轮再 down 防重
    await svc.learn_enqueue({"sessionId": sid, "memberId": 3001},
                            {"sessionId": sid, "seq": seq,
                             "turnId": turn_id, "rawText": "x",
                             "intent": "chat", "reply": "y",
                             "channel": "voice"}, "down")
    queue2 = await repo.list_learn_queue()
    same = [e for e in queue2.values()
            if e.get("seq") == seq]
    check("L2 同轮防重(只一条)", len(same) == 1,
          f"n={len(same)}")

    # L3 👍 不入队
    r = client.post(
        f"/api/xiaozhu/sessions/{sid}/turns/{turn_id}/feedback",
        headers={"X-Member-Id": "3001"},
        json={"rating": "up"})
    queue3 = await repo.list_learn_queue()
    check("L3 👍 不产生新条目", len(queue3) == len(queue2),
          f"{len(queue3)} vs {len(queue2)}")

    # L4 列表+统计(HTTP)
    r = client.get("/api/xiaozhu/dashboard/learn-queue",
                   headers={"X-Role": "admin"})
    j = r.json()
    check("L4 队列统计",
          r.status_code == 200
          and j.get("stats", {}).get("pending") >= 1
          and j.get("llmSuggestOn") is False,
          str(j.get("stats")))

    # L5 LLM 建议轨: 开关 off → error
    sug = await svc.learn_suggest(key)
    check("L5 开关 off 明确提示",
          sug and "未开启" in sug.get("error", ""), str(sug))
    # 开关 on + mock chat → suggestion 落 entry
    os.environ["XIAOZHU_LEARN_LLM"] = "on"
    try:
        with patch("services.llm_client.provider_client") as mc:
            mc.chat.return_value = json.dumps(
                {"wrong": "加入购物车", "right": "加入购物车",
                 "confidence": 0.9})
            sug2 = await svc.learn_suggest(key)
        check("L5b LLM 建议落 entry",
              (sug2 or {}).get("suggestion", {}).get("wrong")
              == "加入购物车", str(sug2))
    finally:
        os.environ.pop("XIAOZHU_LEARN_LLM")

    # L6 采纳闭环(新误听词——非内置词演示闭环)
    # 先造一轮带新误听词的轮次
    r = client.post(f"/api/xiaozhu/sessions/{sid}/voice",
                    headers={"X-Member-Id": "3001"},
                    json={"textTranscript": "小竹，来一平竹香",
                          "durationSec": 2.0, "streamBytes": 64000})
    j = r.json()
    turn2 = (j.get("turn") or {})
    client.post(
        f"/api/xiaozhu/sessions/{sid}/turns/{turn2.get('turnId')}"
        "/feedback",
        headers={"X-Member-Id": "3001"},
        json={"rating": "down"})
    key2 = f"{sid}:{turn2.get('seq')}"
    r = client.post(
        f"/api/xiaozhu/dashboard/learn-queue/{key2}/adopt",
        headers={"X-Role": "admin"},
        json={"wrong": "一平", "right": "一瓶"})
    j = r.json()
    check("L6 采纳 200(词条落表)",
          r.status_code == 200
          and (j.get("record") or {}).get("to") == "一瓶",
          str(j)[:150])
    fixes = await repo.list_asr_fixes()
    check("L6b source=learn",
          fixes.get("一平", {}).get("source") == "learn",
          str(fixes.get("一平")))
    # 闭环: 下轮同误听文本 → 修正即时生效
    r = client.post(f"/api/xiaozhu/sessions/{sid}/voice",
                    headers={"X-Member-Id": "3001"},
                    json={"textTranscript": "小竹，来一平竹香",
                          "durationSec": 2.0, "streamBytes": 64000})
    j = r.json()
    check("L6c 闭环: 下一轮修正即时生效",
          (j.get("turn") or {}).get("rawText") == "小竹，来一瓶竹香",
          str((j.get("turn") or {}).get("rawText")))
    # 热词双通道: 修正词进热词
    hw = await svc._asr_hotwords()
    check("L6d 修正词进热词", "一瓶" in hw, str(hw[:8]))

    # L7 忽略
    r = client.post(
        f"/api/xiaozhu/dashboard/learn-queue/{key}/dismiss",
        headers={"X-Role": "admin"})
    check("L7 忽略 200", r.status_code == 200, str(r.status_code))
    entry = (await repo.list_learn_queue()).get(key) or {}
    check("L7b 状态 dismissed",
          entry.get("status") == "dismissed", str(entry.get("status")))

    # L8 learn 词条可删(非保护来源)
    from urllib.parse import quote

    r = client.delete(
        "/api/xiaozhu/dashboard/asr-fixes?wrong=" + quote("一平"),
        headers={"X-Role": "admin"})
    check("L8 learn 词条可删 200",
          r.status_code == 200, str(r.status_code))

    # L9 队列上限(repo 层)
    for i in range(205):
        await repo.enqueue_learn({
            "sessionId": 90000 + i, "seq": 1, "turnId": f"t-{i}",
            "rawText": "x", "intent": "chat", "reply": "y",
            "channel": "voice", "feedbackAt": f"t{i}",
            "status": "pending", "suggestion": None})
    total = len(await repo.list_learn_queue())
    check("L9 上限 200 拒新", total == 200, f"total={total}")

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
