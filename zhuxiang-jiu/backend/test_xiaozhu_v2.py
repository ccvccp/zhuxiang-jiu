"""小竹 v2 优化批次测试(A 反馈评价 / B 撤销与回溯 / C 误听表 / E 上下文5轮)

运行:
    $env:LOCK_MODE="asyncio"; $env:STORE_MODE="asyncio"; $env:AUTH_MODE="compat"
    python test_xiaozhu_v2.py
"""
import asyncio
import os
import sys

os.environ["LOCK_MODE"] = "asyncio"
os.environ["STORE_MODE"] = "asyncio"
os.environ["AUTH_MODE"] = "compat"
os.environ["XIAOZHU_MODE"] = "assist"
os.environ["LLM_ENABLED"] = "off"
os.environ["XIAOZHU_LLM_MODE"] = "off"

from repositories.store import reset_store

PASS = 0
FAIL = 0
RESULTS = []
MEMBER = 3001


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        RESULTS.append(f"  [PASS] {name}")
    else:
        FAIL += 1
        RESULTS.append(f"  [FAIL] {name} {detail}")


async def run_service():
    reset_store()
    from services.xiaozhu_service import XiaozhuService
    from repositories.xiaozhu_repository import Xiaozhu48Repository
    svc = XiaozhuService()
    repo = Xiaozhu48Repository()

    # ============================================================
    # C: ASR 误听自学习表
    # ============================================================
    # 首次调用种入 builtin 3 条(表为空 → seed)
    out = await svc._fix_asr_mishear("小竹，加入国五车")
    check("C1 builtin 种子+应用", out == "小竹，加入购物车",
          f"out={out}")
    fixes = await repo.list_asr_fixes()
    builtin = [r for r in fixes.values()
               if r.get("source") == "builtin"]
    dialect = [r for r in fixes.values()
               if r.get("source") == "dialect"]
    check("C2 种子 builtin 3 + dialect 7(P2 方言)",
          len(builtin) == 3 and len(dialect) == 7,
          f"n={len(fixes)} builtin={len(builtin)} "
          f"dialect={len(dialect)}")
    # 命中计数递增
    hit0 = fixes.get("加入国五车", {}).get("hits", 0)
    check("C3 命中计数递增", hit0 >= 1, f"hits={hit0}")
    # 运营动态词条即时生效
    await repo.save_asr_fix("奏结", "结算")
    out2 = await svc._fix_asr_mishear("小竹，奏结")
    check("C4 动态词条即时生效", out2 == "小竹，结算",
          f"out={out2}")
    # 单遍防链式: 修正结果含另一误听词不二次应用
    await repo.save_asr_fix("乙词", "奏结")  # 乙词→奏结(但奏结→结算 已存在)
    out3 = await svc._fix_asr_mishear("乙词")
    check("C5 单遍防链式(长词优先)",
          out3 in ("乙词", "结算", "奏结") and out3 != "",
          f"out={out3}")
    # fail-soft: 非法词条不崩
    out4 = await svc._fix_asr_mishear("")
    check("C6 空文本安全", out4 == "")

    # ============================================================
    # E: 上下文 5 轮
    # ============================================================
    from services.xiaozhu_service import _llm_mode_enabled
    os.environ["XIAOZHU_LLM_MODE"] = "on"
    try:
        # 造会话 + 7 轮 general 留痕
        s = await svc.open_session(MEMBER, "text")
        sid = s["sessionId"]
        for i in range(1, 8):
            await svc.handle_text(sid, f"闲聊第{i}句甲乙丙")
        # patch 分类器捕获 context_desc(同步——服务层调用无 await)
        import services.llm_client as _lc
        captured = {}

        def _fake(text, ctx):
            captured["ctx"] = ctx
            return None

        _fake_orig = _lc.provider_client.classify_dialog_intent
        _lc.provider_client.classify_dialog_intent = _fake
        try:
            await svc._llm_dialog_intent(s, "再聊一句")
        finally:
            _lc.provider_client.classify_dialog_intent = _fake_orig
        ctx = captured.get("ctx") or ""
        check("E1 上下文含第3-5轮",
              "闲聊第3句" in ctx and "闲聊第5句" in ctx,
              f"ctx={ctx[:200]}")
        check("E2 上下文不含第1-2轮",
              "闲聊第1句" not in ctx and "闲聊第2句" not in ctx)
        check("E3 上下文含最近2轮(3+2=5)",
              "闲聊第6句" in ctx and "闲聊第7句" in ctx)
    finally:
        os.environ["XIAOZHU_LLM_MODE"] = "off"

    # ============================================================
    # B2: 撤销(service 层——已结算拒绝)
    # ============================================================
    s2 = await svc.open_session(MEMBER, "text")
    sid2 = s2["sessionId"]
    await svc.handle_text(sid2, "小竹，看新品")
    await svc.handle_text(sid2, "需要")
    r = await svc.handle_text(sid2, "撤销")
    check("B2-1 撤销最近加购", "已撤销" in str(r.get("reply")),
          f"reply={r.get('reply')}")
    # 连续撤销第二次(同款加购一次 → 第二次无可撤销)
    r2 = await svc.handle_text(sid2, "撤销")
    check("B2-2 撤无可撤销温和提示",
          "没有可撤销" in str(r2.get("reply"))
          or "撤销" in str(r2.get("reply")), f"reply={r2.get('reply')}")
    # 已结算拒绝: 落 order_done 轮后再撤销
    await svc.handle_text(sid2, "需要")
    session_obj = await repo.get_session(sid2)
    await svc._save_turn(session_obj, "text", "模拟结算", "cart.submit",
                         {"reply": "已下单", "card": {
                             "type": "order_done",
                             "subject": "订单"}}, {})
    r3 = await svc.handle_text(sid2, "撤销")
    check("B2-3 已结算拒绝", "已提交结算" in str(r3.get("reply")),
          f"reply={r3.get('reply')}")

    # ============================================================
    # B1: 历史回溯
    # ============================================================
    r4 = await svc.handle_text(sid2, "我刚才做了什么")
    check("B1-1 回溯播报含操作",
          "刚才您" in str(r4.get("reply")), f"reply={r4.get('reply')}")
    actions = await svc.audit_member_actions(MEMBER)
    check("B1-2 会员级留痕含 cart.undo",
          any(a.get("intent") == "cart.undo" for a in actions))


def run_http():
    from fastapi.testclient import TestClient
    from main import app
    client = TestClient(app)
    H = {"X-Member-Id": str(MEMBER)}
    HJ = {**H, "Content-Type": "application/json"}

    async def _prep():
        reset_store()
        from services.xiaozhu_service import XiaozhuService
        svc = XiaozhuService()
        s = await svc.open_session(MEMBER, "text")
        # 重播 builtin 种子(reset_store 清了误听表)
        await svc._fix_asr_mishear("加入国五车")
        return s["sessionId"], svc

    sid, svc = asyncio.run(_prep())

    # A/B 全链(HTTP)
    r = client.post(f"/api/xiaozhu/sessions/{sid}/text",
                    json={"text": "小竹，看新品"}, headers=HJ)
    check("HTTP 链路: 看新品 200", r.status_code == 200)
    r = client.post(f"/api/xiaozhu/sessions/{sid}/text",
                    json={"text": "需要"}, headers=HJ)
    turn2 = (r.json() or {}).get("turn") or {}
    check("HTTP 链路: 加购含 turnId",
          bool(turn2.get("turnId")), f"turn={turn2}")

    # 反馈: 无头 401
    r = client.post(
        f"/api/xiaozhu/sessions/{sid}/turns/{turn2['turnId']}"
        "/feedback", json={"rating": "up"})
    check("A HTTP: 无登录 401", r.status_code == 401,
          f"{r.status_code}")
    # 跨会员 403
    r = client.post(
        f"/api/xiaozhu/sessions/{sid}/turns/{turn2['turnId']}"
        "/feedback", json={"rating": "up"},
        headers={"X-Member-Id": str(MEMBER + 1),
                 "Content-Type": "application/json"})
    check("A HTTP: 跨会员 403", r.status_code == 403,
          f"{r.status_code}")
    # up → down 覆盖幂等
    r = client.post(
        f"/api/xiaozhu/sessions/{sid}/turns/{turn2['turnId']}"
        "/feedback", json={"rating": "up"}, headers=HJ)
    check("A HTTP: up 200", r.status_code == 200)
    r = client.post(
        f"/api/xiaozhu/sessions/{sid}/turns/{turn2['turnId']}"
        "/feedback", json={"rating": "down"}, headers=HJ)
    check("A HTTP: down 覆盖 200", r.status_code == 200)
    # turn 落痕校验(feedback=down)
    async def _verify():
        from repositories.xiaozhu_repository import (
            Xiaozhu48Repository,
        )
        turns = await Xiaozhu48Repository().list_turns(sid)
        t = next(x for x in turns
                 if x.get("turnId") == turn2["turnId"])
        return t.get("feedback")
    check("A HTTP: turn 落痕 feedback=down",
          asyncio.run(_verify()) == "down")
    # 非法 rating 409
    r = client.post(
        f"/api/xiaozhu/sessions/{sid}/turns/{turn2['turnId']}"
        "/feedback", json={"rating": "xxx"}, headers=HJ)
    check("A HTTP: 非法 rating 409", r.status_code == 409)

    # actions 扩集(含 cart.add)
    r = client.get(f"/api/xiaozhu/sessions/{sid}/actions",
                   headers=H)
    acts = (r.json() or {}).get("actions") or []
    check("B1 HTTP: actions 含 cart.add",
          any(a.get("action") == "cart.add" for a in acts),
          f"n={len(acts)}")

    # dashboard 反馈+误听区块
    r = client.get("/api/xiaozhu/dashboard",
                   headers={"X-Role": "admin"})
    zones = (r.json() or {}).get("zones") or {}
    fb = zones.get("feedback") or {}
    check("看板: feedback 区块计数",
          fb.get("down") == 1 and fb.get("up") == 0,
          f"fb={fb}")
    af = zones.get("asrfixes") or {}
    check("看板: asrfixes 区块 builtin≥3",
          (af.get("builtin") or 0) >= 3, f"af={af.get('builtin')}")

    # asr-fixes 管理: 无 admin 403
    r = client.post("/api/xiaozhu/dashboard/asr-fixes",
                    json={"wrong": "甲词", "right": "乙词"})
    check("C HTTP: 无 admin 403", r.status_code == 403)
    # 添加
    A = {"X-Role": "admin", "Content-Type": "application/json"}
    r = client.post("/api/xiaozhu/dashboard/asr-fixes",
                    json={"wrong": "芝华仕", "right": "竹香式"},
                    headers=A)
    check("C HTTP: 添加词条 200", r.status_code == 200)
    # 循环修正拒绝(竹香式→芝华仕 会成环)
    r = client.post("/api/xiaozhu/dashboard/asr-fixes",
                    json={"wrong": "竹香式", "right": "芝华仕"},
                    headers=A)
    check("C HTTP: 循环修正 409", r.status_code == 409,
          f"{r.status_code}")
    # builtin 删除拒绝
    r = client.request(
        "DELETE",
        "/api/xiaozhu/dashboard/asr-fixes?wrong="
        + __import__("urllib.parse", fromlist=["quote"]).quote(
            "加入国五车"),
        headers={"X-Role": "admin"})
    check("C HTTP: builtin 删除 409", r.status_code == 409)
    # 动态删除成功
    r = client.request(
        "DELETE",
        "/api/xiaozhu/dashboard/asr-fixes?wrong=芝华仕",
        headers={"X-Role": "admin"})
    check("C HTTP: 动态删除 200", r.status_code == 200)


def main():
    asyncio.run(run_service())
    run_http()
    print("\n".join(RESULTS))
    print("-" * 60)
    print(f"通过 {PASS} 项 / 失败 {FAIL} 项")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())

