"""43号·AI智能安全管理模块 P2b 专项测试(态势三态 + 学习回流)

运行方式:
    python test_security_p2b.py

覆盖:
    - 态势: 冷启动peace/EMA更新/防抖(连续2窗口才升)/升级链
      peace→alert→wartime/降级链/pinned不自动转换/手动切换/
      非法态势409/系数缩放(peace1.5-alert1.0-wartime0.3)
    - 网关联动: 态势缓存刷新/频次缩放生效
    - 学习回流: 事件真值collect(confirmed正反馈/false_positive
      负反馈/幂等eventFed/pending跳过)/run一轮/status视图
    - HTTP: posture三端点+learning三端点+鉴权
"""

import asyncio
import os
import sys

os.environ["LOCK_MODE"] = "asyncio"
os.environ["STORE_MODE"] = "asyncio"
os.environ.pop("LLM_API_KEY", None)
os.environ["LLM_ENABLED"] = "off"
os.environ["SECURITY_GATEWAY_MODE"] = "on"
os.environ["SECURITY_ENFORCE_LEVEL"] = "observe"
os.environ["SECURITY_UEBA_MODE"] = "on"
os.environ["SECURITY_POSTURE_MODE"] = "auto"
os.environ["SECURITY_POSTURE_WINDOW"] = "300"

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


def reset_store():
    from repositories.store import reset_store as _reset
    _reset()


async def make_decided_event(svc, action="challenge",
                             verdict="confirmed"):
    """造一个已裁决的评分事件(带六因子)"""
    r = await svc.process_request(
        "9.9.9.1", method="GET", path="/api/product/search",
        query="kw=%27%20OR%201%3D1%20--", ua="Mozilla/5.0", hour=14)
    event = r.get("event")
    if event is None:
        return None
    svc_repo = svc.repo
    loaded = await svc_repo.get_event(event["eventId"])
    loaded["verdict"] = verdict
    await svc_repo.save_event(loaded)
    return loaded


class TestPosture:
    async def run(self):
        print("[01 态势三态]")
        from services.posture_service import (
            PostureService, POSTURE_PEACE, POSTURE_ALERT,
            POSTURE_WARTIME, POSTURE_RATE_FACTOR,
        )
        ps = PostureService()

        # 冷启动
        current = await ps.current()
        record("冷启动peace", current["posture"] == POSTURE_PEACE,
               str(current))
        record("peace系数1.5",
               current["rateFactor"] == 1.5)

        # EMA 更新 + 单窗口不升级(防抖)
        r = await ps.observe_window(100)   # 大量可疑事件
        record("EMA更新", r["densityEma"] > 0, str(r))
        record("单窗口不升级(防抖)",
               r["posture"] == POSTURE_PEACE
               and r["pendingDirection"] == "up", str(r))

        # 第二窗口 → 升级 alert
        r = await ps.observe_window(100)
        record("连续2窗口升级alert",
               r["posture"] == POSTURE_ALERT, str(r))
        record("alert系数1.0",
               (await ps.current())["rateFactor"] == 1.0)

        # 再两窗口高密度 → wartime
        await ps.observe_window(100)
        r = await ps.observe_window(100)
        record("升级wartime",
               r["posture"] == POSTURE_WARTIME, str(r))
        record("wartime系数0.3",
               (await ps.current())["rateFactor"] == 0.3)

        # 降级链: 连续2窗口低密度(需 EMA < 阈值×0.5)
        for _ in range(10):
            await ps.observe_window(0)
        current = await ps.current()
        record("低密度降级",
               current["posture"] in (POSTURE_ALERT,
                                      POSTURE_PEACE),
               current["posture"])

        # pinned: 不自动转换
        await ps.set_posture(POSTURE_WARTIME, pin=True)
        for _ in range(6):
            r = await ps.observe_window(0)
        record("pinned不降级",
               (await ps.current())["posture"] == POSTURE_WARTIME,
               str(r))
        # 解钉后恢复自动
        await ps.set_pinned(False)
        for _ in range(6):
            await ps.observe_window(0)
        record("解钉恢复自动",
               (await ps.current())["posture"] != POSTURE_WARTIME)

        # 手动切换 + 非法值
        r = await ps.set_posture(POSTURE_ALERT)
        record("手动切换", r["posture"] == POSTURE_ALERT)
        try:
            await ps.set_posture("invalid")
            record("非法态势拒绝", False, "应抛 ValueError")
        except ValueError:
            record("非法态势拒绝", True)

        # manual 模式: 不自动
        os.environ["SECURITY_POSTURE_MODE"] = "manual"
        try:
            await ps.set_posture(POSTURE_PEACE)
            await ps.observe_window(100)
            await ps.observe_window(100)
            record("manual不自动升级",
                   (await ps.current())["posture"]
                   == POSTURE_PEACE)
        finally:
            os.environ["SECURITY_POSTURE_MODE"] = "auto"

        # 转换历史
        record("系数表三态", POSTURE_RATE_FACTOR == {
            "peace": 1.5, "alert": 1.0, "wartime": 0.3})


class TestGatewayPostureLink:
    async def run(self):
        print("[02 网关联动]")
        from services.security_service import Security43Service
        from services.posture_service import (
            PostureService, POSTURE_WARTIME, POSTURE_RATE_FACTOR,
        )
        svc = Security43Service()

        # 切 wartime → 缓存刷新 → 频次上限 ×0.3
        await PostureService().set_posture(POSTURE_WARTIME)
        Security43Service._refresh_posture_cache(
            POSTURE_RATE_FACTOR[POSTURE_WARTIME])
        record("wartime系数缓存",
               svc._posture_factor() == 0.3)
        # 缓存过期回退默认
        Security43Service._POSTURE_CACHE = (0.0, 1.5)
        record("缓存过期回退peace",
               svc._posture_factor() == 1.5)
        await PostureService().set_posture("peace")


class TestLearning:
    async def run(self):
        print("[03 学习回流]")
        from services.security_service import (
            Security43Service, VERDICT_CONFIRMED,
        )
        svc = Security43Service()

        # 造 2 个已裁决事件(1 confirmed 拦对 + 1 false_positive 拦错)
        ev1 = await make_decided_event(svc, verdict="confirmed")
        ev2 = await make_decided_event(svc,
                                       verdict="false_positive")
        record("前置-已裁决事件", ev1 is not None
               and ev2 is not None)

        # collect: 2 submitted
        r = await svc.collect_event_feedback()
        record("回流提交数", r["submitted"] == 2, str(r)[:100])
        record("回流语义", any(x.get("correct") is True
                              for x in r["results"])
               and any(x.get("correct") is False
                       for x in r["results"]),
               str(r["results"])[:120])

        # 幂等: 再 collect 全跳过
        r = await svc.collect_event_feedback()
        record("回流幂等", r["submitted"] == 0
               and r["skipped"] >= 2, str(r)[:80])

        # pending 事件跳过
        await make_decided_event(svc, verdict="pending")
        r = await svc.collect_event_feedback()
        record("pending跳过", r["submitted"] == 0)

        # run 一轮(测试口径: 先调低 min_feedback)
        from services.ai_learning_service import (
            update_learning_config,
        )
        await update_learning_config("security_threat_gate",
                                     {"min_feedback": 1})
        r = await svc.run_learning()
        record("学习一轮成功", r.get("success") is True
               or "cycle" in str(r).lower(), str(r)[:120])

        # status
        r = await svc.learning_status()
        record("状态视图", r["success"] is True
               and r["scorer"] == "security_threat_gate"
               and "weights" in r, str(r)[:100])
        record("事件计数", r["events"]["fed"] == 2,
               str(r["events"]))


class TestHttpRoutes:
    async def run(self):
        print("[04 HTTP层]")
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from routes.security_routes import register_security_routes

        app = FastAPI()
        register_security_routes(app)
        client = TestClient(app)
        admin = {"X-Role": "admin"}

        # 态势端点
        resp = client.get("/api/security/admin/posture")
        record("HTTP-posture缺Role403", resp.status_code == 403)
        resp = client.get("/api/security/admin/posture",
                          headers=admin)
        record("HTTP-态势查询", resp.status_code == 200
               and "posture" in resp.json())
        resp = client.post("/api/security/admin/posture",
                           json={"posture": "alert"},
                           headers=admin)
        record("HTTP-态势切换", resp.status_code == 200
               and resp.json()["posture"] == "alert")
        resp = client.post("/api/security/admin/posture",
                           json={"posture": "bad"}, headers=admin)
        record("HTTP-非法态势409", resp.status_code == 409)
        resp = client.post("/api/security/admin/posture/pin",
                           json={"pinned": True}, headers=admin)
        record("HTTP-态势钉住", resp.status_code == 200
               and resp.json()["pinned"] is True)
        client.post("/api/security/admin/posture/pin",
                    json={"pinned": False}, headers=admin)

        # 学习端点
        resp = client.post("/api/security/admin/learning/collect",
                           headers=admin)
        record("HTTP-collect回流", resp.status_code == 200
               and "submitted" in resp.json(),
               str(resp.json())[:80])
        resp = client.get("/api/security/admin/learning/status",
                          headers=admin)
        record("HTTP-status状态", resp.status_code == 200
               and "events" in resp.json())
        resp = client.post("/api/security/admin/learning/run",
                           headers=admin)
        record("HTTP-run学习(409=不足正常)",
               resp.status_code in (200, 409), str(resp.status_code))
        resp = client.post("/api/security/admin/learning/run")
        record("HTTP-run缺Role403", resp.status_code == 403)


async def run_all():
    await TestPosture().run()
    await TestGatewayPostureLink().run()
    await TestLearning().run()
    await TestHttpRoutes().run()


def main():
    reset_store()
    asyncio.run(run_all())
    print()
    print("=" * 62)
    print("\n".join(RESULTS))
    print("=" * 62)
    print(f"总计: {PASS} 通过 / {FAIL} 失败")
    return FAIL


if __name__ == "__main__":
    sys.exit(1 if main() else 0)
