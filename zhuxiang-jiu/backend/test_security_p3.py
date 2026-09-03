"""43号·P3-1 网关响应钩子专项测试(D4 试探偏离完整落地)

运行方式:
    python test_security_p3.py

覆盖(计划 §二):
    - _SendWrapper: 状态码捕获/多消息透传/首消息生效
    - 堆积计数: 403计1.0/401计0.5/24h滚动/查询不记数
    - observe_response: 403/401计数/200不计数/未认证不累积
    - D4 完整接入: 网关管线实时查询 forbidden_hits(不再参数化)
    - E2E: 连续 403 → D4_probe 命中 → identity_risk 降分
    - HTTP E2E: FastAPI 真实响应(403/409/404)经网关计数
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


class TestSendWrapper:
    async def run(self):
        print("[01 _SendWrapper]")
        from core.security_gateway import _SendWrapper

        sent = []

        async def fake_send(message):
            sent.append(message)

        wrapper = _SendWrapper(fake_send)
        await wrapper({"type": "http.response.start", "status": 403,
                       "headers": []})
        await wrapper({"type": "http.response.body", "body": b"x"})
        await wrapper({"type": "http.response.body", "body": b"",
                       "more_body": False})
        record("状态码捕获", wrapper.status_code == 403,
               str(wrapper.status_code))
        record("三消息全透传", len(sent) == 3, str(len(sent)))
        record("消息体不改写",
               sent[1]["body"] == b"x" and sent[2].get("more_body") is False)

        # 首个 start 生效(后续不覆盖)
        await wrapper({"type": "http.response.start", "status": 200})
        record("首消息生效", wrapper.status_code == 403)

        # 无 start(理论不发生): 状态码为 None
        wrapper2 = _SendWrapper(fake_send)
        await wrapper2({"type": "http.response.body", "body": b""})
        record("无start为None", wrapper2.status_code is None)


class TestForbiddenCount:
    async def run(self, svc):
        print("[02 堆积计数]")
        from repositories.security_repository import \
            Security43Repository
        repo = Security43Repository()

        # 403 计 1.0
        total = await repo.count_forbidden(101, weight=1.0)
        record("403计1.0", total == 1.0, str(total))
        # 401 计 0.5
        total = await repo.count_forbidden(101, weight=0.5)
        record("401计0.5", total == 1.5, str(total))
        # 查询不记数
        record("查询不记数",
               await repo.get_forbidden(101) == 1.5)
        # 其他会员独立
        total = await repo.count_forbidden(202, weight=1.0)
        record("会员独立计数", total == 1.0, str(total))
        # 未认证 member 0
        record("无会员0.0",
               await repo.get_forbidden(0) == 0.0)

        # observe_response 语义
        await svc.observe_response("1.1.1.1", 303, 403)
        record("403计入响应观测",
               await repo.get_forbidden(303) == 1.0)
        await svc.observe_response("1.1.1.1", 303, 401)
        record("401半权计入",
               await repo.get_forbidden(303) == 1.5)
        await svc.observe_response("1.1.1.1", 303, 200)
        record("200不计数",
               await repo.get_forbidden(303) == 1.5)
        await svc.observe_response("1.1.1.1", 303, 500)
        record("500不计数",
               await repo.get_forbidden(303) == 1.5)
        # 未认证(member 0)不累积
        await svc.observe_response("1.1.1.1", 0, 403)
        record("未认证不累积",
               await repo.get_forbidden(0) == 0.0)


class TestD4Integration:
    async def run(self, svc):
        print("[03 D4完整接入]")
        from services.ueba_service import UebaService
        ueba = UebaService()

        # 建会员 404 基线(仅 14 时活动)
        for _ in range(5):
            await ueba.record_behavior(404, "/api/order/x", hour=14)
        await ueba.rebuild_baselines()
        UebaService._BASELINE_CACHE.clear()

        # 无 403 堆积: 常规请求无 D4
        dev = await ueba.compute_deviation(404, "/api/order/x",
                                           hour=14,
                                           current_hour_ops=1)
        record("无堆积无D4", dev is None, str(dev)[:60])

        # 造 3.5 堆积 → D4 命中(阈值 3.0)
        from repositories.security_repository import \
            Security43Repository
        repo = Security43Repository()
        await repo.count_forbidden(404, weight=1.0)
        await repo.count_forbidden(404, weight=1.0)
        await repo.count_forbidden(404, weight=1.0)
        await repo.count_forbidden(404, weight=0.5)
        dev = await ueba.compute_deviation(404, "/api/order/x",
                                           hour=14,
                                           current_hour_ops=1,
                                           forbidden_hits=3.5)
        record("堆积3.5命中D4", dev is not None and any(
            d["code"] == "D4_probe" for d in dev["deviations"]),
            str(dev)[:80] if dev else "None")

        # 网关管线实时查询(非参数化): process_request 内部
        # 自动查 forbidden_hits → identity_risk 降分
        from services.security_service import Security43Service
        svc2 = Security43Service()
        r = await svc2.process_request(
            "7.7.7.4", method="GET", path="/api/order/list",
            ua="Mozilla/5.0", member_id=404, hour=14)
        identity = [f for f in (r.get("scoring") or {}).get(
            "factors", []) if f["name"] == "identity_risk"]
        record("管线实时查询D4降分",
               identity and identity[0]["score"] < 100,
               str(identity)[:100])
        record("行为预警触发(3点=0分)",
               r.get("behaviorAlert") is not None,
               str(r.get("behaviorAlert"))[:80])


class TestHttpE2E:
    async def run(self):
        print("[04 HTTP端到端]")
        # 独立 FastAPI app + 安全网关中间件: 真实响应经
        # _SendWrapper → observe_response → 计数
        from fastapi import FastAPI, HTTPException
        from fastapi.testclient import TestClient
        from core.security_gateway import SecurityGatewayMiddleware

        app = FastAPI()

        @app.get("/api/test/ok")
        async def ok():
            return {"success": True}

        @app.get("/api/test/forbidden")
        async def forbidden():
            raise HTTPException(status_code=403, detail="无权限")

        @app.get("/api/test/missing")
        async def missing():
            raise HTTPException(status_code=404, detail="不存在")

        app.add_middleware(SecurityGatewayMiddleware)
        client = TestClient(app)

        resp = client.get("/api/test/ok")
        record("200正常透传", resp.status_code == 200,
               str(resp.status_code))

        resp = client.get("/api/test/missing",
                          headers={"X-Member-Id": "505"})
        record("404正常透传", resp.status_code == 404)
        from services.security_service import Security43Service
        svc = Security43Service()
        record("404不计数", await svc.get_forbidden_hits(505) == 0.0)

        # 连续 3 次 403(带会员头) → 堆积 3.0
        for _ in range(3):
            resp = client.get("/api/test/forbidden",
                              headers={"X-Member-Id": "505"})
            record_check = resp.status_code == 403
        record("403正常透传", record_check)
        record("403堆积3.0",
               await svc.get_forbidden_hits(505) == 3.0,
               str(await svc.get_forbidden_hits(505)))

        # 401 半权(TestClient 模拟鉴权失败响应: 直接端点返回)
        @app.get("/api/test/unauth")
        async def unauth():
            raise HTTPException(status_code=401, detail="未登录")

        resp = client.get("/api/test/unauth",
                          headers={"X-Member-Id": "505"})
        record("401透传", resp.status_code == 401)
        record("401半权累积3.5",
               await svc.get_forbidden_hits(505) == 3.5,
               str(await svc.get_forbidden_hits(505)))

        # 响应体完整性(send 包装不破坏负载)
        resp = client.get("/api/test/ok")
        record("响应体完整", resp.json() == {"success": True},
               str(resp.text)[:80])


async def run_all():
    from services.security_service import Security43Service
    svc = Security43Service()
    await TestSendWrapper().run()
    await TestForbiddenCount().run(svc)
    await TestD4Integration().run(svc)
    await TestHttpE2E().run()


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
