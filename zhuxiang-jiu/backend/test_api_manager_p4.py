"""44号·P4 AI 智能自治专项测试

运行方式:
    python test_api_manager_p4.py

覆盖(计划 §七):
    - 基线检测: 尖刺(>μ+3σ)/骤降(<μ-3σ 且绝对量)/错误激增
      (×3 且样本≥20)/样本不足跳过/正常不触发
    - 事件: 幂等落库/队列/裁决 confirmed/false_positive/
      不存在拒绝
    - 配额推荐: P95×1.3/档位匹配/贴顶建议/低利用降档建议/
      无历史拒绝
    - NL 助手: 意图路由(延迟/Top/我的/搜索/帮助兜底)/
      mock 确定性回答/数字来自查询层
    - HTTP 层: detect/list/decide/recommend/assistant 鉴权
      与结构
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
        # 直接构造内存桶(单 Key=1)
        arls._MEM_USAGE.setdefault(1, {})[(day, template)] = {
            "total": total, "err": err, "sum": float(total),
            "count": max(1, total), "max": 10,
            "byCode": {"200": total - err, "500": err},
        }


class TestAnomalyDetection:
    async def run(self):
        print("[01 基线检测]")
        reset_all()
        from services.api_intelligence_service import (
            ApiAnomalyService,
        )
        svc = ApiAnomalyService()

        # 样本不足(<3 天): 跳过
        await seed_history("/api/a", [100, 90])
        r = await svc.detect()
        record("样本不足跳过", r["detected"] == 0, str(r))

        # 正常波动: 不触发(当日带内)
        reset_all()
        await seed_history(
            "/api/b", [100, 100, 100, 98, 102, 100, 100])
        r = await svc.detect()
        record("稳定不触发", r["detected"] == 0, str(r))

        # 尖刺: 当日 500(历史 6 天 μ=100, σ>0)
        reset_all()
        await seed_history(
            "/api/c", [500, 98, 102, 100, 99, 101, 100])
        r = await svc.detect()
        record("尖刺触发", r["detected"] == 1
               and r["events"][0]["kind"] == "spike",
               str(r["events"])[:100])
        record("尖刺含倍数归因", "倍" in
               r["events"][0]["summary"],
               r["events"][0]["summary"])

        # 骤降: 当日 30(μ=100, 绝对量 30 ≥ 20)
        reset_all()
        await seed_history(
            "/api/d", [30, 98, 102, 100, 99, 101, 100])
        r = await svc.detect()
        record("骤降触发", r["detected"] == 1
               and r["events"][0]["kind"] == "drop",
               str(r["events"])[:100])

        # 骤降绝对量不足: 当日 5(<20)不触发
        reset_all()
        await seed_history(
            "/api/e", [5, 98, 102, 100, 99, 101, 100])
        r = await svc.detect()
        record("骤降绝对量防误报", r["detected"] == 0,
               str(r["detected"]))

        # 错误激增: 基线 5%(历史), 当日 60%(样本 100)
        reset_all()
        await seed_history(
            "/api/f",
            [100, 100, 100, 100, 100, 100, 100],
            [60, 5, 5, 5, 5, 5, 5])
        r = await svc.detect()
        record("错误激增触发", r["detected"] == 1
               and r["events"][0]["kind"] == "error_burst",
               str(r["events"])[:120])

        # 错误激增样本不足: 当日 10(<20)不触发
        reset_all()
        await seed_history(
            "/api/g",
            [10, 100, 100, 100, 100, 100, 100],
            [8, 5, 5, 5, 5, 5, 5])
        r = await svc.detect()
        record("错误激增样本硬阈值", r["detected"] == 0,
               str(r["detected"]))


class TestAnomalyEvents:
    async def run(self):
        print("[02 事件队列与裁决]")
        reset_all()
        from services.api_intelligence_service import (
            ApiAnomalyService,
        )
        svc = ApiAnomalyService()
        await seed_history(
            "/api/h", [800, 100, 98, 102, 100, 99, 101])

        r1 = await svc.detect()
        record("检测落库", r1["detected"] == 1, str(r1))
        # 幂等: 再检测同日同类型不重复
        r2 = await svc.detect()
        record("重复检测幂等", r2["detected"] == 1,
               str(r2["detected"]))

        q = await svc.list_events()
        record("队列一条", q["total"] == 1, str(q["total"]))
        event = q["events"][0]
        record("事件pending", event["status"] == "pending",
               str(event.get("status")))

        # 状态过滤
        q = await svc.list_events(status="confirmed")
        record("状态过滤空", q["total"] == 0, str(q["total"]))

        # 裁决
        r = await svc.decide_event(event["eventId"], True)
        record("裁决confirmed", r["status"] == "confirmed",
               str(r["status"]))
        q = await svc.list_events(status="confirmed")
        record("过滤confirmed", q["total"] == 1,
               str(q["total"]))

        r = await svc.decide_event(event["eventId"], False)
        record("改判false_positive",
               r["status"] == "false_positive", str(r))

        # 不存在拒绝
        try:
            await svc.decide_event(99999, True)
            record("不存在事件拒绝", False, "未抛")
        except KeyError:
            record("不存在事件拒绝", True)


class TestRecommend:
    async def run(self):
        print("[03 配额推荐]")
        reset_all()
        from services.api_intelligence_service import (
            ApiRecommendService,
        )
        from services.api_key_service import ApiKeyService
        from datetime import datetime, UTC, timedelta
        import services.api_rate_limit_service as arls

        ks = ApiKeyService()
        k = await ks.apply_key(5, "推荐测试")
        kid = k["keyId"]

        # 无历史: 拒绝
        try:
            await ApiRecommendService().recommend(kid)
            record("无历史拒绝", False, "未抛")
        except ValueError:
            record("无历史拒绝", True)

        # 灌该 Key 历史(直接写 _MEM_USAGE keyId 维度)
        today = datetime.now(UTC).date()
        for i, total in enumerate([80, 90, 85, 95, 88, 92, 0][:6]):
            day = (today - timedelta(days=i + 1)
                   ).strftime("%Y%m%d")
            arls._MEM_USAGE.setdefault(kid, {})[
                (day, "/api/r")] = {
                "total": total, "err": 0,
                "sum": float(total), "count": max(1, total),
                "max": 10, "byCode": {"200": total}}

        r = await ApiRecommendService().recommend(kid)
        record("P95×1.3", r["recommendedDaily"] ==
               int(r["p95Daily"] * 1.3), str(r))
        record("推荐档位free(123<1000)", r["recommendedTier"]
               == "free", str(r["recommendedTier"]))
        record("advice文案", "P95" in r["advice"]
               and "1.3" in r["advice"], str(r["advice"])[:80])

        # 贴顶建议: 用量常年 990/1000
        reset_all()
        k2 = await ks.apply_key(6, "贴顶测试")
        for i in range(6):
            day = (today - timedelta(days=i + 1)
                   ).strftime("%Y%m%d")
            arls._MEM_USAGE.setdefault(k2["keyId"], {})[
                (day, "/api/r")] = {
                "total": 990, "err": 0, "sum": 990.0,
                "count": 990, "max": 10,
                "byCode": {"200": 990}}
        r = await ApiRecommendService().recommend(k2["keyId"])
        record("贴顶升档建议", "贴顶" in r["advice"]
               and "升档" in r["advice"], str(r["advice"])[:90])

        # 低利用降档
        reset_all()
        k3 = await ks.apply_key(7, "低利用")
        await ApiKeyService().admin_set_limits(
            k3["keyId"], tier="pro")
        for i in range(6):
            day = (today - timedelta(days=i + 1)
                   ).strftime("%Y%m%d")
            arls._MEM_USAGE.setdefault(k3["keyId"], {})[
                (day, "/api/r")] = {
                "total": 50, "err": 0, "sum": 50.0,
                "count": 50, "max": 10, "byCode": {"200": 50}}
        r = await ApiRecommendService().recommend(k3["keyId"])
        record("低利用降档建议", "降档" in r["advice"],
               str(r["advice"])[:90])

        # 不存在 key
        try:
            await ApiRecommendService().recommend(99999)
            record("不存在key拒绝", False, "未抛")
        except KeyError:
            record("不存在key拒绝", True)


class TestAssistant:
    async def run(self):
        print("[04 NL 助手]")
        reset_all()
        from services.api_intelligence_service import (
            ApiAssistantService,
        )
        svc = ApiAssistantService()

        # 空问题拒绝
        try:
            await svc.answer("  ")
            record("空问题拒绝", False, "未抛")
        except ValueError:
            record("空问题拒绝", True)

        # 帮助兜底
        r = await svc.answer("你好")
        record("帮助兜底", r["intent"] == "help"
               and "哪个接口最慢" in r["answer"],
               str(r["intent"]))

        # 空数据延迟意图
        r = await svc.answer("哪个接口最慢?")
        record("空数据延迟意图", r["intent"] == "latency"
               and "无" in r["answer"], str(r["answer"])[:60])

        # 灌数据: top/latency 意图
        from services.api_rate_limit_service import (
            record_usage_event,
        )
        from services.api_key_service import ApiKeyService
        k = await ApiKeyService().apply_key(8, "助手测试")
        for _ in range(10):
            await record_usage_event(
                k["keyId"], "/api/fast", 10.0, 200)
        await record_usage_event(
            k["keyId"], "/api/slow", 900.0, 200)
        await record_usage_event(
            k["keyId"], "/api/slow", 500.0, 200)

        r = await svc.answer("哪个接口最慢")
        record("延迟意图", r["intent"] == "latency"
               and "/api/slow" in r["answer"]
               and "900" in r["answer"],
               str(r["answer"])[:90])
        r = await svc.answer("调用最多的接口")
        record("Top意图", r["intent"] == "top_api"
               and "/api/fast" in r["answer"]
               and "10" in r["answer"],
               str(r["answer"])[:90])

        # 我的用量(带 memberId): 10(fast) + 2(slow) = 12 次
        r = await svc.answer("我的用量", member_id=8)
        record("我的用量意图", r["intent"] == "my_usage"
               and "12" in r["answer"], str(r["answer"])[:80])

        # 搜索意图(台账)
        r = await svc.answer("有没有物流相关的接口")
        record("搜索意图", r["intent"] == "search",
               str(r["intent"]))

        # mock 模式声明
        record("mock模式声明", r.get("mode") == "mock",
               str(r.get("mode")))


class TestHttp:
    async def run(self):
        print("[05 HTTP 层]")
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
        resp = client.post(
            "/api/api-manager/admin/apis/anomalies/detect")
        record("detect缺Role403", resp.status_code == 403,
               str(resp.status_code))
        resp = client.get(
            "/api/api-manager/admin/apis/anomalies")
        record("list缺Role403", resp.status_code == 403,
               str(resp.status_code))
        resp = client.post(
            "/api/api-manager/admin/apis/anomalies/1/decide",
            json={"confirm": True})
        record("decide缺Role403", resp.status_code == 403,
               str(resp.status_code))
        resp = client.get(
            "/api/api-manager/admin/apis/keys/1/recommend")
        record("recommend缺Role403", resp.status_code == 403,
               str(resp.status_code))

        # detect 200
        resp = client.post(
            "/api/api-manager/admin/apis/anomalies/detect",
            headers={"X-Role": "admin"})
        body = resp.json()
        record("detect200", resp.status_code == 200
               and body.get("detected") == 0
               and "events" in body, str(body)[:80])

        # list 200
        resp = client.get(
            "/api/api-manager/admin/apis/anomalies",
            headers={"X-Role": "admin"})
        record("list200", resp.status_code == 200
               and "events" in resp.json(),
               str(resp.status_code))

        # decide 409(缺 confirm)
        resp = client.post(
            "/api/api-manager/admin/apis/anomalies/1/decide",
            json={}, headers={"X-Role": "admin"})
        record("decide缺confirm409", resp.status_code == 409,
               str(resp.status_code))

        # decide 404
        resp = client.post(
            "/api/api-manager/admin/apis/anomalies/99999/"
            "decide",
            json={"confirm": True},
            headers={"X-Role": "admin"})
        record("decide不存在404", resp.status_code == 404,
               str(resp.status_code))

        # recommend 404(不存在 key)
        resp = client.get(
            "/api/api-manager/admin/apis/keys/99999/recommend",
            headers={"X-Role": "admin"})
        record("recommend不存在404", resp.status_code == 404,
               str(resp.status_code))

        # assistant 200(mock)
        resp = client.post(
            "/api/api-manager/apis/assistant",
            json={"q": "哪个接口最慢"})
        body = resp.json()
        record("assistant200", resp.status_code == 200
               and body.get("mode") == "mock"
               and "answer" in body, str(body)[:90])

        # assistant 空问题 409
        resp = client.post(
            "/api/api-manager/apis/assistant",
            json={"q": ""})
        record("assistant空409", resp.status_code == 409,
               str(resp.status_code))

        # assistant 带 memberId(我的用量)
        resp = client.post(
            "/api/api-manager/apis/assistant",
            json={"q": "我的用量"},
            headers={"X-Member-Id": "99"})
        record("assistant会员意图", resp.status_code == 200
               and resp.json().get("intent") == "my_usage",
               str(resp.json().get("intent")))


async def run_all():
    await TestAnomalyDetection().run()
    await TestAnomalyEvents().run()
    await TestRecommend().run()
    await TestAssistant().run()
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
