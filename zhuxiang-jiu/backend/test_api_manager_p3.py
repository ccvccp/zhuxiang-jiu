"""44号·P3 调用观测 + 健康评分专项测试

运行方式:
    python test_api_manager_p3.py

覆盖(计划 §六):
    - 留痕桶: record_usage_event 累计/错误计入(429)/延迟
      sum/count/max/状态码分布
    - load_usage_window: 桶读回/key_ids 过滤/内存模式
    - 三视图: per-API 聚合(callers 计数)/per-Key 聚合
      (apis 计数/errorRate)/配额命中率(used/dailyLimit)
    - 会员自查: 仅 active Key/聚合
    - 第27档案 ApiHealthScorer: 零样本/healthy(完美 ctx)/
      critical(高错误+慢+尖刺)/四档边界/五因子结构
    - 档案注册: SCORER_REGISTRY api_health/
      default_weights 读 WEIGHTS
    - 中间件留痕: send 包装捕获状态码(200/500)与延迟
    - HTTP 层: usage/health/my usage 鉴权与结构
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


class TestUsageRecording:
    async def run(self):
        print("[01 留痕桶]")
        reset_all()
        from services.api_rate_limit_service import (
            record_usage_event, load_usage_window,
        )

        # 3 次 200 + 1 次 429 + 1 次 500
        for _ in range(3):
            await record_usage_event(1, "/api/x", 10.0, 200)
        await record_usage_event(1, "/api/x", 20.0, 429)
        await record_usage_event(1, "/api/x", 30.0, 500)
        # 另一模板
        await record_usage_event(1, "/api/y", 5.0, 200)

        rows = await load_usage_window()
        x = next(r for r in rows
                 if r["template"] == "/api/x")
        record("总数5次", x["total"] == 5, str(x["total"]))
        record("错误2次(429+500)", x["err"] == 2, str(x["err"]))
        record("avgMs=16", abs(x["avgMs"] - 16.0) < 0.5,
               str(x["avgMs"]))
        record("maxMs=30", x["maxMs"] == 30, str(x["maxMs"]))
        record("状态码分布", x["byCode"].get("200") == 3
               and x["byCode"].get("429") == 1
               and x["byCode"].get("500") == 1, str(x["byCode"]))

        # key_ids 过滤
        rows = await load_usage_window(key_ids=[2])
        record("key过滤空", rows == [], str(rows))
        rows = await load_usage_window(key_ids=[1])
        record("key过滤命中", len(rows) == 2, str(len(rows)))


class TestUsageViews:
    async def run(self):
        print("[02 三视图聚合]")
        reset_all()
        from services.api_rate_limit_service import (
            record_usage_event,
        )
        from services.api_usage_service import ApiUsageService
        from services.api_key_service import ApiKeyService
        svc = ApiUsageService()

        # 两个 Key 一个模板
        k1 = await ApiKeyService().apply_key(51, "视图K1")
        k2 = await ApiKeyService().apply_key(52, "视图K2")
        for _ in range(6):
            await record_usage_event(
                k1["keyId"], "/api/p", 10.0, 200)
        for _ in range(2):
            await record_usage_event(
                k2["keyId"], "/api/p", 5.0, 404)

        views = await svc.usage_views()
        record("总调用8次", views["totalCalls"] == 8,
               str(views["totalCalls"]))
        record("总错误2次", views["totalErrors"] == 2,
               str(views["totalErrors"]))

        api = next(a for a in views["byApi"]
                   if a["template"] == "/api/p")
        record("per-API聚合total=8", api["total"] == 8,
               str(api["total"]))
        record("per-API errorRate=25%", abs(
            api["errorRate"] - 0.25) < 0.001,
            str(api["errorRate"]))
        record("per-API callers=2", api["callers"] == 2,
               str(api["callers"]))

        key_rows = {k["keyId"]: k
                    for k in views["byKey"]}
        record("per-Key两名消费方", k1["keyId"] in key_rows
               and k2["keyId"] in key_rows,
               str(list(key_rows)))
        record("per-Key K1 total=6",
               key_rows[k1["keyId"]]["total"] == 6,
               str(key_rows.get(k1["keyId"])))
        record("per-Key含name",
               key_rows[k1["keyId"]]["name"] == "视图K1",
               str(key_rows.get(k1["keyId"]))[:80])

        # 配额命中率: K1 用 6/1000
        quota = {q["keyId"]: q for q in views["quota"]}
        record("配额命中率K1", abs(quota[k1["keyId"]][
            "hitRate"] - 6 / 1000) < 0.001,
            str(quota.get(k1["keyId"])))


class TestMyUsage:
    async def run(self):
        print("[03 会员自查]")
        reset_all()
        from services.api_rate_limit_service import (
            record_usage_event,
        )
        from services.api_usage_service import ApiUsageService
        from services.api_key_service import ApiKeyService
        ks = ApiKeyService()

        k = await ks.apply_key(61, "自查")
        await record_usage_event(
            k["keyId"], "/api/m", 10.0, 200)
        # 吊销的 Key 不计入
        k2 = await ks.apply_key(61, "吊销不查")
        await record_usage_event(
            k2["keyId"], "/api/m", 10.0, 200)
        await ks.revoke_key(61, k2["keyId"])

        r = await ApiUsageService().my_usage(61)
        record("仅active计入", r["total"] == 1, str(r["total"]))
        record("无active零返回", (
            await ApiUsageService().my_usage(99))["total"] == 0,
            "ok")


class TestHealthScorer:
    async def run(self):
        print("[04 第27档案评分]")
        from services.api_usage_service import ApiHealthScorer

        r = ApiHealthScorer.score({"total": 0})
        record("零样本watch", r["grade"] == "watch"
               and r["score"] == 0, str(r))

        # 完美 ctx → healthy 满分
        r = ApiHealthScorer.score({
            "total": 100, "err": 0, "avgMs": 50, "maxMs": 100,
            "quotaHitRate": 0.1, "recentChanges": 0})
        record("完美healthy", r["grade"] == "healthy"
               and r["score"] == 100.0, str(r))
        record("五因子结构", len(r["factors"]) == 5
               and {f["name"] for f in r["factors"]} == {
                   "success_rate", "latency", "stability",
                   "quota_hit", "change_freq"},
               str([f["name"] for f in r["factors"]]))

        # 高错误+慢+尖刺+贴顶+多变 → critical/strained
        r = ApiHealthScorer.score({
            "total": 100, "err": 40, "avgMs": 400,
            "maxMs": 4000, "quotaHitRate": 1.0,
            "recentChanges": 10})
        record("劣化低分", r["score"] < 30
               and r["grade"] in ("critical", "strained"),
               str(r["score"]))

        # 四档边界
        r75 = ApiHealthScorer.score({
            "total": 100, "err": 5, "avgMs": 200,
            "maxMs": 400, "quotaHitRate": 0.5,
            "recentChanges": 2})
        record("边界样本分档", r75["grade"] in (
            "healthy", "watch"), str(r75["grade"]))
        record("score为float", isinstance(
            r75["score"], float), str(r75["score"]))

        # 配额贴顶提示
        r = ApiHealthScorer.score({
            "total": 100, "err": 0, "avgMs": 50,
            "maxMs": 100, "quotaHitRate": 0.95,
            "recentChanges": 0})
        qf = next(f for f in r["factors"]
                  if f["name"] == "quota_hit")
        record("贴顶建议升档", "建议升档" in qf["detail"]
               and qf["value"] < 1.0, str(qf))


class TestScorerRegistration:
    async def run(self):
        print("[05 档案注册]")
        from services.ai_learning_service import (
            SCORER_REGISTRY, default_weights,
        )
        record("registry注册", "api_health" in SCORER_REGISTRY,
               str("api_health" in SCORER_REGISTRY))
        record("registry元数据", SCORER_REGISTRY["api_health"][
            "module"] == "44API管理",
            str(SCORER_REGISTRY.get("api_health")))
        w = default_weights("api_health")
        from services.api_usage_service import ApiHealthScorer
        record("默认权重读WEIGHTS", w == ApiHealthScorer.WEIGHTS,
               str(w))
        record("权重和=1", abs(sum(w.values()) - 1.0) < 0.001,
               str(sum(w.values())))


class TestMiddlewareObservation:
    async def run(self):
        print("[06 中间件观测留痕]")
        reset_all()
        from fastapi import FastAPI, Header
        from fastapi.testclient import TestClient
        from core.api_key_middleware import (
            ApiKeyMiddleware, invalidate_published_cache,
        )
        from services.api_key_service import ApiKeyService
        import services.api_rate_limit_service as arls

        app = FastAPI()

        @app.get("/api/ok")
        async def ok(x_member_id: str = Header(
                default="", alias="X-Member-Id")):
            return {"memberId": x_member_id}

        @app.get("/api/fail")
        async def fail():
            raise RuntimeError("业务异常")

        app.add_middleware(ApiKeyMiddleware)
        client = TestClient(app)
        os.environ["API_MANAGER_MODE"] = "on"
        try:
            ks = ApiKeyService()
            k = await ks.apply_key(71, "观测留痕")
            await ks.repo.set_published("GET", "/api/ok", True)
            await ks.repo.set_published(
                "GET", "/api/fail", True)
            invalidate_published_cache()
            kh = {"X-Api-Key": k["apiKey"],
                  "X-App-Code": k["appCode"]}

            # 成功调用 → 留痕 200
            resp = client.get("/api/ok", headers=kh)
            record("业务200", resp.status_code == 200,
                   str(resp.status_code))
            # 500 调用(TestClient raise_server_exceptions=False)
            client2 = TestClient(
                app, raise_server_exceptions=False)
            resp = client2.get("/api/fail", headers=kh)
            record("业务500透传", resp.status_code == 500,
                   str(resp.status_code))

            # 等异步留痕落地
            await asyncio.sleep(0.2)
            rows = await arls.load_usage_window()
            by_tpl = {r["template"]: r for r in rows}
            ok_row = by_tpl.get("/api/ok")
            record("成功留痕200", ok_row
                   and ok_row["total"] == 1
                   and ok_row["byCode"].get("200") == 1
                   and ok_row["avgMs"] >= 0,
                   str(ok_row))
            fail_row = by_tpl.get("/api/fail")
            record("异常留痕500", fail_row
                   and fail_row["total"] == 1
                   and fail_row["err"] == 1,
                   str(fail_row))
        finally:
            os.environ.pop("API_MANAGER_MODE", None)
            invalidate_published_cache()


class TestHttp:
    async def run(self):
        print("[07 HTTP层]")
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
        for path in ("/api/api-manager/admin/apis/usage",
                     "/api/api-manager/admin/apis/health"):
            resp = client.get(path)
            record(f"{path.split('/')[-1]}缺Role403",
                   resp.status_code == 403,
                   str(resp.status_code))
        resp = client.get("/api/api-manager/keys/usage")
        record("myusage缺MemberId401", resp.status_code == 401,
               str(resp.status_code))

        # usage 空数据结构
        resp = client.get("/api/api-manager/admin/apis/usage",
                          headers={"X-Role": "admin"})
        body = resp.json()
        record("usage200结构", resp.status_code == 200
               and "byApi" in body and "byKey" in body
               and "quota" in body and "totalCalls" in body,
               str(list(body)))

        # health 空数据
        resp = client.get("/api/api-manager/admin/apis/health",
                          headers={"X-Role": "admin"})
        body = resp.json()
        record("health200结构", resp.status_code == 200
               and "overall" in body and "apis" in body
               and body["overall"].get("grade") == "watch",
               str(body)[:100])

        # my usage 200
        resp = client.get("/api/api-manager/keys/usage",
                          headers={"X-Member-Id": "81"})
        body = resp.json()
        record("myusage200", resp.status_code == 200
               and body.get("total") == 0, str(body)[:80])


async def run_all():
    await TestUsageRecording().run()
    await TestUsageViews().run()
    await TestMyUsage().run()
    await TestHealthScorer().run()
    await TestScorerRegistration().run()
    await TestMiddlewareObservation().run()
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
