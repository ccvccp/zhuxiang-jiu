"""44号·P2 流量治理专项测试

运行方式:
    python test_api_manager_p2.py

覆盖(计划 §五):
    - 套餐常量: 三档限值/默认 free
    - tier_limits: 套餐基础/per-Key 覆盖优先
    - QPS 固定窗口: 恰好限值通过/超 1 拒绝/被拒计入窗口/
      窗口滑动恢复(内存模式)
    - 日配额: 超限拒绝 retryAfter 至次日秒数/QPS 拒绝不消耗
      日配额
    - per-Key 调参: admin_set_limits 三参/非法值拒绝/清除
      回退套餐
    - 中间件: 双限通过正常/QPS 429 + Retry-After 头/日配额
      429/调参即时生效
    - HTTP 层: tiers 视图/limits PATCH 200/非法 409/
      不存在 404/缺 Role 403
"""

import asyncio
import os
import sys
import time

os.environ["LOCK_MODE"] = "asyncio"
os.environ["STORE_MODE"] = "asyncio"
os.environ.pop("LLM_API_KEY", None)
os.environ["LLM_ENABLED"] = "off"
os.environ.pop("API_MANAGER_MODE", None)
os.environ.pop("API_KEY_AUTO_APPROVE", None)

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
    import services.api_key_service as aks
    aks._KEY_CACHE.clear()
    import core.api_key_middleware as akm
    akm.invalidate_published_cache()
    import services.api_rate_limit_service as arls
    arls._reset_limit_state()


def _svc():
    from services.api_key_service import ApiKeyService
    return ApiKeyService()


class TestTiers:
    async def run(self):
        print("[01 套餐与限值]")
        from services.api_rate_limit_service import (
            TIERS, tier_limits,
        )
        record("三档常量", set(TIERS) == {"free", "basic", "pro"},
               str(TIERS))
        record("free限值", TIERS["free"] == {"qps": 5, "daily": 1000},
               str(TIERS["free"]))
        q, d = tier_limits("free")
        record("free基础", (q, d) == (5, 1000), f"{q}/{d}")
        q, d = tier_limits("pro")
        record("pro基础", (q, d) == (100, 100000), f"{q}/{d}")
        q, d = tier_limits("free", custom_qps=10, custom_daily=99)
        record("覆盖优先", (q, d) == (10, 99), f"{q}/{d}")
        q, d = tier_limits(None)
        record("None套餐兜底free", (q, d) == (5, 1000),
               f"{q}/{d}")
        q, d = tier_limits("unknown_tier")
        record("未知套餐兜底free", (q, d) == (5, 1000),
               f"{q}/{d}")


class TestQpsWindow:
    async def run(self):
        print("[02 QPS 固定窗口]")
        from services.api_rate_limit_service import (
            check_rate_limit,
        )
        reset_all()

        # free qps=5: 前 5 次(同秒)通过
        results = [await check_rate_limit(1, "free")
                   for _ in range(5)]
        record("恰好5次通过", all(r["allowed"]
                                    for r in results),
               str(results[-1]))
        r6 = await check_rate_limit(1, "free")
        record("第6次拒绝", r6["allowed"] is False
               and r6["limitType"] == "qps"
               and r6["retryAfter"] == 1, str(r6))

        # 被拒继续计入窗口(第 7 次仍拒)
        r7 = await check_rate_limit(1, "free")
        record("被拒计入窗口", r7["allowed"] is False,
               str(r7))

        # 不同 keyId 窗口独立
        r_other = await check_rate_limit(2, "free")
        record("keyId窗口独立", r_other["allowed"] is True,
               str(r_other))

        # 窗口滑动恢复(内存模式: 等 1.05s 淘汰旧记录)
        await asyncio.sleep(1.05)
        r_rec = await check_rate_limit(1, "free")
        record("窗口滑动恢复", r_rec["allowed"] is True,
               str(r_rec))

    async def run2(self):
        pass


class TestDailyQuota:
    async def run(self):
        print("[03 日配额]")
        from services.api_rate_limit_service import (
            check_rate_limit,
        )
        reset_all()

        # custom_daily=3, qps 高不干扰
        results = [await check_rate_limit(
            10, "pro", custom_daily=3) for _ in range(3)]
        record("恰好3次通过", all(r["allowed"] for r in results),
               str(results[-1]))
        r4 = await check_rate_limit(10, "pro", custom_daily=3)
        record("第4次日配额拒绝", r4["allowed"] is False
               and r4["limitType"] == "daily", str(r4))
        record("retryAfter至次日", isinstance(
            r4.get("retryAfter"), int)
            and 0 < r4["retryAfter"] <= 86400,
            str(r4.get("retryAfter")))

        # QPS 拒绝不消耗日配额(keyId=11 qps=1 daily=100)
        await check_rate_limit(11, "free", custom_qps=1,
                               custom_daily=100)
        r = await check_rate_limit(11, "free", custom_qps=1,
                                   custom_daily=100)
        record("QPS被拒不消耗日配额", r["limitType"] == "qps",
               str(r))
        r = await check_rate_limit(11, "free", custom_qps=1,
                                   custom_daily=100)
        record("仍为QPS拒绝", r["limitType"] == "qps", str(r))


class TestAdminLimits:
    async def run(self):
        print("[04 限值调参]")
        reset_all()
        svc = _svc()
        k = await svc.apply_key(5, "调参测试")
        kid = k["keyId"]

        # 三参设置
        r = await svc.admin_set_limits(
            kid, tier="basic", custom_qps=50, custom_daily=50000)
        record("三参设置", r["tier"] == "basic"
               and r["qpsLimit"] == 50
               and r["dailyLimit"] == 50000, str(r))

        # 生效验证(缓存失效后校验携带新值)
        v = await svc.validate_key(k["apiKey"], k["appCode"])
        record("校验携带新限值", v.get("tier") == "basic"
               and v.get("customQps") == 50
               and v.get("customDaily") == 50000, str(v))

        # 非法 tier
        try:
            await svc.admin_set_limits(kid, tier="vip")
            record("非法tier拒绝", False, "未抛")
        except ValueError:
            record("非法tier拒绝", True)

        # 非法限值
        for field, bad in (("custom_qps", 0), ("custom_qps", -1),
                           ("custom_qps", "x"),
                           ("custom_daily", 10_000_001)):
            try:
                await svc.admin_set_limits(
                    kid, **{field: bad})
                record(f"非法{field}={bad}拒绝", False, "未抛")
            except ValueError:
                record(f"非法{field}={bad}拒绝", True)

        # 空参数
        try:
            await svc.admin_set_limits(kid)
            record("空参数拒绝", False, "未抛")
        except ValueError:
            record("空参数拒绝", True)

        # 清除回退
        await svc.admin_set_limits(kid, tier="free",
                                   custom_qps=50)
        r = await svc.admin_clear_limit(kid, "customQps")
        record("清除回退套餐", r["qpsLimit"] == 5, str(r))
        try:
            await svc.admin_clear_limit(kid, "tier")
            record("非法清除字段拒绝", False, "未抛")
        except ValueError:
            record("非法清除字段拒绝", True)

        # 不存在 key
        try:
            await svc.admin_set_limits(99999, tier="basic")
            record("不存在key拒绝", False, "未抛")
        except KeyError:
            record("不存在key拒绝", True)

        # 列表视图含限值
        r = await svc.list_my_keys(5)
        item = r["keys"][0]
        record("列表含限值视图", "qpsLimit" in item
               and "dailyLimit" in item, str(item)[:90])


class TestMiddlewareLimits:
    async def run(self):
        print("[05 中间件双限]")
        reset_all()
        from core.api_key_middleware import (
            invalidate_published_cache,
        )
        from fastapi.testclient import TestClient
        svc = _svc()
        app = None
        from fastapi import FastAPI, Header

        app = FastAPI()

        @app.get("/api/echo")
        async def echo(x_member_id: str = Header(
                default="", alias="X-Member-Id")):
            return {"memberId": x_member_id}

        from core.api_key_middleware import ApiKeyMiddleware
        app.add_middleware(ApiKeyMiddleware)
        client = TestClient(app)

        os.environ["API_MANAGER_MODE"] = "on"
        try:
            k = await svc.apply_key(11, "中间件双限")
            await svc.repo.set_published(
                "GET", "/api/echo", True)
            invalidate_published_cache()
            # 限 qps=2
            await svc.admin_set_limits(
                k["keyId"], custom_qps=2)

            headers = {"X-Api-Key": k["apiKey"],
                       "X-App-Code": k["appCode"]}
            r1 = client.get("/api/echo", headers=headers)
            r2 = client.get("/api/echo", headers=headers)
            record("前2次通过", r1.status_code == 200
                   and r2.status_code == 200,
                   f"{r1.status_code}/{r2.status_code}")

            r3 = client.get("/api/echo", headers=headers)
            record("第3次QPS 429", r3.status_code == 429
                   and "QPS 超限" in r3.json().get("detail", ""),
                   str(r3.status_code) + str(r3.json()))
            record("Retry-After头", r3.headers.get("retry-after")
                   == "1", str(r3.headers.get("retry-after")))

            # 日配额 429(清 QPS 窗口后调 custom_daily=2)
            from services.api_rate_limit_service import (
                _reset_limit_state,
            )
            _reset_limit_state()
            await svc.admin_set_limits(
                k["keyId"], custom_qps=100, custom_daily=2)
            client.get("/api/echo", headers=headers)
            client.get("/api/echo", headers=headers)
            r5 = client.get("/api/echo", headers=headers)
            record("第3次日配额429", r5.status_code == 429
                   and "日配额" in r5.json().get("detail", ""),
                   str(r5.json()))
            record("日配额RetryAfter>1",
                   int(r5.headers.get("retry-after", "0")) > 1,
                   str(r5.headers.get("retry-after")))

            # 调参即时生效(admin_set_limits 失效缓存)
            _reset_limit_state()
            await svc.admin_set_limits(
                k["keyId"], custom_daily=100000)
            r6 = client.get("/api/echo", headers=headers)
            record("调参即时恢复", r6.status_code == 200,
                   str(r6.status_code))
        finally:
            os.environ.pop("API_MANAGER_MODE", None)
            invalidate_published_cache()


class TestHttp:
    async def run(self):
        print("[06 HTTP层]")
        reset_all()
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from routes.api_manager_routes import (
            register_api_manager_routes,
        )

        app = FastAPI()
        register_api_manager_routes(app)
        client = TestClient(app)

        # 鉴权
        resp = client.get("/api/api-manager/admin/apis/tiers")
        record("tiers缺Role403", resp.status_code == 403,
               str(resp.status_code))
        resp = client.patch(
            "/api/api-manager/admin/apis/keys/1/limits",
            json={"tier": "basic"})
        record("limits缺Role403", resp.status_code == 403,
               str(resp.status_code))

        # tiers 视图
        resp = client.get("/api/api-manager/admin/apis/tiers",
                          headers={"X-Role": "admin"})
        body = resp.json()
        record("tiers200结构", resp.status_code == 200
               and "tiers" in body and "default" in body
               and set(body["tiers"]) == {
                   "free", "basic", "pro"},
               str(body)[:120])
        record("tiers含activeKeys", all(
            "activeKeys" in t for t in body["tiers"].values()),
               str(body["tiers"])[:100])

        # 申请 key 后 activeKeys 计数
        resp = client.post("/api/api-manager/keys",
                           json={"name": "tiers计数"},
                           headers={"X-Member-Id": "7"})
        kid = resp.json()["keyId"]
        resp = client.get("/api/api-manager/admin/apis/tiers",
                          headers={"X-Role": "admin"})
        body = resp.json()
        record("activeKeys计数", body["tiers"]["free"][
            "activeKeys"] >= 1, str(body["tiers"]["free"]))

        # limits PATCH
        resp = client.patch(
            f"/api/api-manager/admin/apis/keys/{kid}/limits",
            json={"tier": "basic", "customQps": 30},
            headers={"X-Role": "admin"})
        body = resp.json()
        record("limits200", resp.status_code == 200
               and body.get("tier") == "basic"
               and body.get("qpsLimit") == 30, str(body)[:90])

        # null 清除
        resp = client.patch(
            f"/api/api-manager/admin/apis/keys/{kid}/limits",
            json={"customQps": None},
            headers={"X-Role": "admin"})
        record("null清除覆盖", resp.status_code == 200
               and resp.json().get("qpsLimit") == 20,
               str(resp.json())[:80])

        # 非法值 409
        resp = client.patch(
            f"/api/api-manager/admin/apis/keys/{kid}/limits",
            json={"customQps": -5},
            headers={"X-Role": "admin"})
        record("非法限值409", resp.status_code == 409,
               str(resp.status_code))

        # 不存在 404
        resp = client.patch(
            "/api/api-manager/admin/apis/keys/99999/limits",
            json={"tier": "basic"},
            headers={"X-Role": "admin"})
        record("不存在404", resp.status_code == 404,
               str(resp.status_code))


async def run_all():
    await TestTiers().run()
    await TestQpsWindow().run()
    await TestDailyQuota().run()
    await TestAdminLimits().run()
    await TestMiddlewareLimits().run()
    await TestHttp().run()


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
