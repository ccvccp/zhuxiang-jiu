"""44号·P5 治理闭环专项测试

运行方式:
    python test_api_manager_p5.py

覆盖(计划 §八):
    - 生命周期状态机: 全合法链路(development→published→
      deprecated→offline)/非法转换拒绝/同状态拒绝/重新启用/
      不存在拒绝/deprecatedAt 留痕
    - offline 软护栏: 近 7 日存量调用阻断/force 强制下线/
      无存量直接下线
    - 中间件: deprecated 弃用预警头 X-Api-Deprecated/
      offline 410 Gone/恢复正常头移除/410 目录指引
    - 对外目录: published+deprecated 展示/offline+development
      隐藏/日落时间(30 天)/无需鉴权
    - 裁决回流: confirmed/false_positive 语义/eventFed 幂等/
      pending 跳过/学习一轮/状态视图/权重断言
    - HTTP 层: lifecycle/catalog/learning 三连 鉴权与结构
"""

import asyncio
import os
import sys

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
    arls._reset_usage_state()


async def seed_history(template: str, day_totals: list,
                       day_errs: list = None):
    """灌历史桶(首元素=当日, 其后依次为 1~N 天前)"""
    import services.api_rate_limit_service as arls
    from datetime import datetime, UTC, timedelta
    today = datetime.now(UTC).date()
    day_errs = day_errs or [0] * len(day_totals)
    for i, (total, err) in enumerate(zip(day_totals, day_errs)):
        day = (today - timedelta(days=i)).strftime("%Y%m%d")
        arls._MEM_USAGE.setdefault(1, {})[(day, template)] = {
            "total": total, "err": err, "sum": float(total),
            "count": max(1, total), "max": 10,
            "byCode": {"200": total - err, "500": err},
        }


async def seed_registry_entry(method: str, path: str,
                              status: str = "development") -> int:
    """直接灌台账条目, 返回 apiId"""
    from core.helpers import ts
    from repositories.api_manager_repository import (
        ApiManager44Repository,
    )
    repo = ApiManager44Repository()
    api_id = await repo.next_api_id()
    await repo.save_entry(method, path, {
        "apiId": api_id, "method": method, "path": path,
        "module": "P5测试", "moduleSource": "auto",
        "status": status, "summary": "P5 专项测试条目",
        "missing": False, "lastSeenAt": ts(),
        "createdAt": ts(), "updatedAt": ts()})
    return api_id


async def make_decided_events(seeds: list) -> dict:
    """灌多个模板历史 → 一次检测 → 逐个裁决

    seeds: [(template, verdict, (day_totals, day_errs))]
    返回 {template: event(裁决后最新态)}
    """
    from services.api_intelligence_service import ApiAnomalyService
    for template, _verdict, (totals, errs) in seeds:
        await seed_history(template, totals, errs)
    svc = ApiAnomalyService()
    await svc.detect()
    q = await svc.list_events()
    out = {}
    for template, verdict, _seed in seeds:
        event = next(e for e in q["events"]
                     if e.get("template") == template)
        if verdict != "pending":
            event = await svc.decide_event(
                int(event["eventId"]),
                verdict == "confirmed")
        out[template] = event
    return out


# 种子模式: 尖刺(当日 800 vs μ=100) / 错误激增(60% vs 基线 5%)
SPIKE_SEED = ([800, 100, 98, 102, 100, 99, 101],
              [0, 0, 0, 0, 0, 0, 0])
ERROR_SEED = ([100, 100, 100, 100, 100, 100, 100],
              [60, 5, 5, 5, 5, 5, 5])


class TestLifecycle:
    async def run(self):
        print("[01 生命周期状态机]")
        reset_all()
        from services.api_lifecycle_service import (
            ApiLifecycleService,
        )
        svc = ApiLifecycleService()

        # 全合法链路: development→published→deprecated→offline
        api_id = await seed_registry_entry("GET", "/api/lc1")
        r = await svc.transition(api_id, "published")
        record("开发→发布", r["status"] == "published", str(r)[:80])
        r = await svc.transition(api_id, "deprecated")
        record("发布→弃用", r["status"] == "deprecated",
               str(r)[:80])
        record("deprecatedAt留痕", bool(r.get("deprecatedAt")),
               str(r.get("deprecatedAt")))
        r = await svc.transition(api_id, "offline")
        record("弃用→下线", r["status"] == "offline", str(r)[:80])

        # offline→development 重新启用(显式回开发态)
        r = await svc.transition(api_id, "development")
        record("下线→重新启用", r["status"] == "development",
               str(r)[:80])

        # 同状态拒绝
        try:
            await svc.transition(api_id, "development")
            record("同状态拒绝", False, "未抛")
        except ValueError as e:
            record("同状态拒绝", "无转换" in str(e), str(e))

        # 非法转换: offline→published 必须先回开发态
        api2 = await seed_registry_entry("GET", "/api/lc2",
                                         "offline")
        try:
            await svc.transition(api2, "published")
            record("非法转换拒绝", False, "未抛")
        except ValueError as e:
            record("非法转换拒绝", "非法转换" in str(e), str(e))

        # 不存在 apiId
        try:
            await svc.transition(99999, "published")
            record("不存在apiId拒绝", False, "未抛")
        except KeyError:
            record("不存在apiId拒绝", True)

        # 回退: published→development(development 可达)
        api3 = await seed_registry_entry("GET", "/api/lc3",
                                         "published")
        r = await svc.transition(api3, "development")
        record("发布→回退开发", r["status"] == "development",
               str(r)[:80])


class TestOfflineGuard:
    async def run(self):
        print("[02 offline 软护栏]")
        reset_all()
        from services.api_lifecycle_service import (
            ApiLifecycleService,
        )
        svc = ApiLifecycleService()

        # 近 7 日有存量调用 → 阻断
        api_id = await seed_registry_entry("GET", "/api/guard1",
                                           "published")
        await seed_history("/api/guard1",
                           [100, 100, 98, 102, 100, 99, 101])
        try:
            await svc.transition(api_id, "offline")
            record("存量调用阻断", False, "未抛")
        except ValueError as e:
            record("存量调用阻断",
                   "近 7 日" in str(e) and "force=true" in str(e),
                   str(e))

        # force=true 强制下线(留痕)
        r = await svc.transition(api_id, "offline", force=True)
        record("force强制下线", r["status"] == "offline",
               str(r)[:80])

        # 无存量 → 直接下线
        api2 = await seed_registry_entry("GET", "/api/guard2",
                                         "published")
        r = await svc.transition(api2, "offline")
        record("无存量直接下线", r["status"] == "offline",
               str(r)[:80])


def build_mw_app():
    """构造中间件测试应用(ApiKeyMiddleware + 回声路由)"""
    from fastapi import FastAPI, Header
    from core.api_key_middleware import ApiKeyMiddleware

    app = FastAPI()

    @app.get("/api/echo")
    async def echo(x_member_id: str = Header(default="",
                                             alias="X-Member-Id")):
        return {"memberId": x_member_id}

    app.add_middleware(ApiKeyMiddleware)
    return app


class TestMiddlewareLifecycle:
    async def run(self):
        print("[03 中间件弃用预警/410]")
        reset_all()
        from core.api_key_middleware import (
            invalidate_published_cache,
        )
        from fastapi.testclient import TestClient
        from services.api_key_service import ApiKeyService
        from services.api_lifecycle_service import (
            ApiLifecycleService,
        )

        os.environ["API_MANAGER_MODE"] = "on"
        try:
            svc = ApiKeyService()
            k = await svc.apply_key(21, "P5中间件测试")
            headers = {"X-Api-Key": k["apiKey"],
                       "X-App-Code": k["appCode"]}
            lc = ApiLifecycleService()
            client = TestClient(build_mw_app())

            # published: 正常响应无弃用头
            api_id = await seed_registry_entry("GET", "/api/echo")
            await lc.transition(api_id, "published")
            resp = client.get("/api/echo", headers=headers)
            record("published正常200", resp.status_code == 200,
                   str(resp.status_code))
            record("published无弃用头",
                   resp.headers.get("x-api-deprecated") is None,
                   str(resp.headers.get("x-api-deprecated")))

            # deprecated: 200 + X-Api-Deprecated 预警头
            await lc.transition(api_id, "deprecated")
            resp = client.get("/api/echo", headers=headers)
            record("deprecated仍可调用",
                   resp.status_code == 200,
                   str(resp.status_code))
            record("弃用预警头注入",
                   resp.headers.get("x-api-deprecated")
                   == "true",
                   str(resp.headers.get("x-api-deprecated")))

            # 恢复 published: 头移除(缓存即时失效)
            await lc.transition(api_id, "published")
            resp = client.get("/api/echo", headers=headers)
            record("恢复后头移除",
                   resp.headers.get("x-api-deprecated") is None,
                   str(resp.headers.get("x-api-deprecated")))

            # offline: 410 Gone(无存量路径——不走流量)
            gone_id = await seed_registry_entry("GET", "/api/gone")
            await lc.transition(gone_id, "offline")
            resp = client.get("/api/gone", headers=headers)
            record("offline返回410", resp.status_code == 410,
                   str(resp.status_code))
            record("410目录指引", "已下线" in
                   resp.json().get("detail", "")
                   and "catalog" in resp.json().get("detail", ""),
                   str(resp.json()))
        finally:
            os.environ.pop("API_MANAGER_MODE", None)


class TestCatalog:
    async def run(self):
        print("[04 对外目录]")
        reset_all()
        from services.api_lifecycle_service import (
            ApiLifecycleService,
        )
        lc = ApiLifecycleService()

        await seed_registry_entry("GET", "/api/cat/pub",
                                  "published")
        dep_id = await seed_registry_entry("GET", "/api/cat/dep")
        await lc.transition(dep_id, "published")
        await lc.transition(dep_id, "deprecated")
        await seed_registry_entry("GET", "/api/cat/dev",
                                  "development")
        await seed_registry_entry("GET", "/api/cat/off",
                                  "offline")

        r = await lc.catalog()
        record("目录成功", r["success"] is True, str(r)[:80])
        paths = {a["path"] for a in r["apis"]}
        record("published在册", "/api/cat/pub" in paths,
               str(sorted(paths)))
        record("deprecated在册", "/api/cat/dep" in paths,
               str(sorted(paths)))
        record("development隐藏", "/api/cat/dev" not in paths,
               str(sorted(paths)))
        record("offline隐藏", "/api/cat/off" not in paths,
               str(sorted(paths)))

        dep_entry = next(a for a in r["apis"]
                         if a["path"] == "/api/cat/dep")
        record("弃用标记", dep_entry["deprecated"] is True,
               str(dep_entry))
        record("日落时间=+30天", bool(dep_entry.get("sunsetAt")),
               str(dep_entry.get("sunsetAt")))
        pub_entry = next(a for a in r["apis"]
                         if a["path"] == "/api/cat/pub")
        record("published无弃用标记",
               pub_entry["deprecated"] is False, str(pub_entry))
        record("鉴权说明", "X-Api-Key" in
               (pub_entry.get("auth") or ""), str(pub_entry))

        # deprecatedAt 未记录时日落为空(容错)
        from repositories.api_manager_repository import (
            ApiManager44Repository,
        )
        await ApiManager44Repository().update_entry_fields(
            "GET", "/api/cat/dep", {"deprecatedAt": ""})
        r = await lc.catalog()
        dep_entry = next(a for a in r["apis"]
                         if a["path"] == "/api/cat/dep")
        record("无deprecatedAt日落空",
               dep_entry.get("sunsetAt") == "",
               str(dep_entry.get("sunsetAt")))


class TestLearningLoop:
    async def run(self):
        print("[05 裁决回流学习闭环]")
        reset_all()
        from services.api_lifecycle_service import (
            ApiLearningService,
        )
        svc = ApiLearningService()

        # 造 2 个已裁决事件(spike 拦对 + error_burst 误报——
        # 不同 kind 因子快照不同, 权重更新不抵消)
        evs = await make_decided_events([
            ("/api/learn1", "confirmed", SPIKE_SEED),
            ("/api/learn2", "false_positive", ERROR_SEED),
        ])
        ev1, ev2 = evs["/api/learn1"], evs["/api/learn2"]
        record("前置-已裁决事件",
               ev1["status"] == "confirmed"
               and ev2["status"] == "false_positive",
               f"{ev1['status']}/{ev2['status']}")

        # collect: 2 submitted, 语义正确
        r = await svc.collect_anomaly_feedback()
        record("回流提交数", r["submitted"] == 2, str(r)[:100])
        record("回流语义", any(x.get("correct") is True
                              for x in r["results"])
               and any(x.get("correct") is False
                       for x in r["results"]),
               str(r["results"])[:120])

        # 幂等: 再 collect 全跳过(eventFed)
        r = await svc.collect_anomaly_feedback()
        record("回流幂等", r["submitted"] == 0
               and r["skipped"] >= 2, str(r)[:80])

        # pending 事件跳过(重复检测不重置已裁决状态——真值保护)
        await make_decided_events([
            ("/api/learn3", "pending", SPIKE_SEED)])
        r = await svc.collect_anomaly_feedback()
        record("pending跳过", r["submitted"] == 0, str(r)[:80])
        q = (await svc._anomalies.list_events()).get("events")
        st1 = next(e for e in q
                   if e.get("template") == "/api/learn1")
        record("真值保护(重复检测不重置)",
               st1["status"] == "confirmed"
               and str(st1.get("eventFed") or "") != "",
               str(st1.get("status")))

        # run 一轮(测试口径: 先调低 min_feedback)
        from services.ai_learning_service import (
            update_learning_config, default_weights,
        )
        await update_learning_config("api_health",
                                     {"min_feedback": 1})
        r = await svc.run_learning()
        record("学习一轮成功", r.get("success") is True,
               str(r)[:120])
        record("新版本角色", r.get("newStatus") in
               ("champion", "challenger"),
               str(r.get("newStatus")))
        record("权重因子完整",
               set(r.get("weights") or {}) ==
               set(default_weights("api_health")),
               str(r.get("weights")))
        record("权重变化断言", any(
            v != 0 for v in (r.get("weightDelta")
                             or {}).values()),
               str(r.get("weightDelta")))

        # status 视图
        r = await svc.learning_status()
        record("状态视图", r["success"] is True
               and r["scorer"] == "api_health"
               and "weights" in r, str(r)[:100])
        record("事件计数", r["decided"] == 2 and r["fed"] == 2
               and r["pending"] == 1, str(
                   {k: r[k] for k in
                    ("decided", "fed", "pending")}))


class TestHttpRoutes:
    async def run(self):
        print("[06 HTTP 层]")
        reset_all()
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from routes.api_manager_routes import (
            register_api_manager_routes,
        )

        app = FastAPI()
        register_api_manager_routes(app)
        client = TestClient(app)
        admin = {"X-Role": "admin"}

        # 鉴权: lifecycle/learning 缺 Role 403
        resp = client.post(
            "/api/api-manager/admin/apis/1/lifecycle",
            json={"status": "published"})
        record("lifecycle缺Role403", resp.status_code == 403,
               str(resp.status_code))
        resp = client.post(
            "/api/api-manager/admin/apis/learning/collect")
        record("collect缺Role403", resp.status_code == 403,
               str(resp.status_code))
        resp = client.post(
            "/api/api-manager/admin/apis/learning/run")
        record("run缺Role403", resp.status_code == 403,
               str(resp.status_code))
        resp = client.get(
            "/api/api-manager/admin/apis/learning/status")
        record("status缺Role403", resp.status_code == 403,
               str(resp.status_code))

        # catalog: 公开访问(无任何头)
        resp = client.get("/api/api-manager/apis/catalog")
        record("catalog公开200", resp.status_code == 200
               and "apis" in resp.json(),
               str(resp.status_code))

        # lifecycle: 缺 status 409
        resp = client.post(
            "/api/api-manager/admin/apis/1/lifecycle",
            json={}, headers=admin)
        record("lifecycle缺status409", resp.status_code == 409,
               str(resp.status_code))

        # lifecycle: 不存在 404
        resp = client.post(
            "/api/api-manager/admin/apis/99999/lifecycle",
            json={"status": "published"}, headers=admin)
        record("lifecycle不存在404", resp.status_code == 404,
               str(resp.status_code))

        # lifecycle: 正常转换 200 + 目录联动
        api_id = await seed_registry_entry("GET", "/api/http/lc")
        resp = client.post(
            f"/api/api-manager/admin/apis/{api_id}/lifecycle",
            json={"status": "published"}, headers=admin)
        record("lifecycle发布200",
               resp.status_code == 200
               and resp.json().get("status") == "published",
               str(resp.json())[:80])
        resp = client.get("/api/api-manager/apis/catalog")
        record("目录含新发布",
               any(a["path"] == "/api/http/lc"
                   for a in resp.json().get("apis") or []),
               str(len(resp.json().get("apis") or [])))

        # lifecycle: 非法转换 409
        resp = client.post(
            f"/api/api-manager/admin/apis/{api_id}/lifecycle",
            json={"status": "published"}, headers=admin)
        record("lifecycle同状态409", resp.status_code == 409,
               str(resp.status_code))

        # lifecycle: 软护栏 409(有存量调用)
        await seed_history("/api/http/lc",
                           [50, 50, 50, 50, 50, 50, 50])
        resp = client.post(
            f"/api/api-manager/admin/apis/{api_id}/lifecycle",
            json={"status": "offline"}, headers=admin)
        record("HTTP软护栏409", resp.status_code == 409
               and "force=true" in resp.json().get("detail", ""),
               str(resp.json())[:100])

        # lifecycle: force 下线 200 + 目录移除
        resp = client.post(
            f"/api/api-manager/admin/apis/{api_id}/lifecycle",
            json={"status": "offline", "force": True},
            headers=admin)
        record("HTTP强制下线200",
               resp.status_code == 200
               and resp.json().get("status") == "offline",
               str(resp.json())[:80])
        resp = client.get("/api/api-manager/apis/catalog")
        record("下线后目录移除",
               not any(a["path"] == "/api/http/lc"
                       for a in resp.json().get("apis") or []),
               str(len(resp.json().get("apis") or [])))

        # learning 三连: 造已裁决事件 → collect → run → status
        await make_decided_events([
            ("/api/http/learn", "confirmed", SPIKE_SEED)])
        resp = client.post(
            "/api/api-manager/admin/apis/learning/collect",
            headers=admin)
        record("HTTP-collect200", resp.status_code == 200
               and resp.json().get("submitted") == 1,
               str(resp.json())[:80])
        resp = client.get(
            "/api/api-manager/admin/apis/learning/status",
            headers=admin)
        record("HTTP-status200", resp.status_code == 200
               and resp.json().get("scorer") == "api_health",
               str(resp.json())[:80])
        from services.ai_learning_service import (
            update_learning_config,
        )
        await update_learning_config("api_health",
                                     {"min_feedback": 1})
        resp = client.post(
            "/api/api-manager/admin/apis/learning/run",
            headers=admin)
        record("HTTP-run200", resp.status_code == 200
               and resp.json().get("success") is True,
               str(resp.status_code) + str(resp.json())[:60])


async def run_all():
    await TestLifecycle().run()
    await TestOfflineGuard().run()
    await TestMiddlewareLifecycle().run()
    await TestCatalog().run()
    await TestLearningLoop().run()
    await TestHttpRoutes().run()


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
