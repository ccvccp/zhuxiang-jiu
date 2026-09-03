"""43号·AI智能安全管理模块 P2 专项测试(UEBA 行为基线)

运行方式:
    python test_security_p2.py

覆盖(方案 §8.1):
    - 采集: path→module映射/三维计数/UEBA off跳过
    - 基线: 直方图归一化/P95/moduleDist/双层基线/冷启动豁免/
      角色全局合并/rebuild幂等
    - 检测: D1时段偏离/D2频率偏离/D3敏感功能首次/D4试探堆积/
      合议分值/无偏离返回None
    - 联动: 网关采集identity_risk只降不升/behavior_alert入事件
      流水/无基线零误报
    - HTTP: rebuild/baselines/deviations 三端点+鉴权
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


class TestCollector:
    async def run(self, ueba):
        print("[01 采集层]")
        from services.ueba_service import path_to_module
        record("path映射-order",
               path_to_module("/api/order/create") == "order")
        record("path映射-admin",
               path_to_module("/api/admin/stats") == "admin")
        record("path映射-finance",
               path_to_module("/api/finance/report") == "finance")
        record("path映射-other",
               path_to_module("/api/unknown/x") == "other")

        # 三维计数
        c1 = await ueba.record_behavior(1, "/api/order/create", hour=14)
        c2 = await ueba.record_behavior(1, "/api/order/pay", hour=14)
        record("三维计数累加", c2 == 2, f"{c1}→{c2}")
        counts = await ueba.repo.get_behavior(1)
        record("计数直方图存储", counts.get("14|order") == 2,
               str(counts))
        c3 = await ueba.record_behavior(1, "/api/admin/stats", hour=3)
        record("不同模块独立计数", c3 == 1
               and counts.get("14|order") == 2)

        # member_id=0 跳过
        record("无会员跳过",
               await ueba.record_behavior(0, "/api/order/x") == 0)

        # UEBA off 跳过
        os.environ["SECURITY_UEBA_MODE"] = "off"
        try:
            record("UEBA off跳过采集",
                   await ueba.record_behavior(
                       1, "/api/order/x", hour=14) == 0)
        finally:
            os.environ["SECURITY_UEBA_MODE"] = "on"


class TestBaseline:
    async def run(self, ueba):
        print("[02 基线层]")
        # 会员 1: 14时order×2 + 3时admin×1 → 个人基线
        r = await ueba.rebuild_baselines()
        record("rebuild成功", r["success"] is True
               and r["personal"] >= 1, str(r)[:100])

        bl = await ueba.repo.get_baseline("member:1")
        record("个人基线存在", bl is not None)
        record("直方图归一化", abs(
            sum(bl["hours"]) - 1.0) < 0.01,
            str(sum(bl["hours"])))
        record("时段权重正确", bl["hours"][14] > bl["hours"][8],
               f"14时={bl['hours'][14]}")
        record("moduleDist", bl["moduleDist"].get("order") == 2
               and bl["moduleDist"].get("admin") == 1,
               str(bl["moduleDist"]))
        record("sensitiveTouches",
               bl["sensitiveTouches"].get("admin") == 1)

        # 角色全局基线(member)
        gl = await ueba.repo.get_baseline("role:member_global")
        record("角色全局基线", gl is not None)
        record("全局直方图归一", abs(
            sum(gl["hours"]) - 1.0) < 0.01)

        # 幂等: 重复 rebuild 结果一致
        await ueba.rebuild_baselines()
        bl2 = await ueba.repo.get_baseline("member:1")
        record("rebuild幂等", bl2["hours"] == bl["hours"])

        # 生效基线双层取数
        effective = await ueba.get_effective_baseline(1)
        record("个人基线生效", effective is not None
               and effective["actorKey"] == "member:1")
        # 无个人基线的会员 → 全局兜底
        effective = await ueba.get_effective_baseline(999)
        record("冷启动全局兜底", effective is not None
               and effective["actorKey"] == "role:member_global")

        # 缓存命中(重建后缓存清空, 查询后写入)
        cached = UebaServiceClass._BASELINE_CACHE.get("member:999")
        record("60s缓存写入", cached is not None)


class TestDetector:
    async def run(self, ueba):
        print("[03 检测层]")
        # 基线: 会员1在14时操作, 3时极少量(admin 1次)
        # D1: 3时(基线权重=1/3≈0.33>0.05) 不触发; 用会员999(全局)
        # D1 时段偏离: 全局基线 3 时权重可能高于阈值 → 直测函数层
        bl = await ueba.repo.get_baseline("member:1")
        # 构造: 会员 2 仅 14 时活动 → 3 时为冷门时段
        for _ in range(5):
            await ueba.record_behavior(2, "/api/order/x", hour=14)
        await ueba.rebuild_baselines()

        # D1: 会员2 在 3 时操作(基线权重 0)
        dev = await ueba.compute_deviation(2, "/api/order/x", hour=3)
        record("D1时段偏离", dev is not None and any(
            d["code"] == "D1_hour" for d in dev["deviations"]),
            str(dev)[:100] if dev else "None")

        # D3: 会员2 首次触碰敏感功能(admin)
        dev = await ueba.compute_deviation(2, "/api/admin/stats",
                                           hour=14)
        record("D3敏感功能首次", dev is not None and any(
            d["code"] == "D3_sensitive_first"
            for d in dev["deviations"]),
            str(dev)[:100] if dev else "None")
        # D3+D1 叠加(3时+admin): 1+2=3点 → 100-120 → 0分
        record("合议分值累加",
               dev is not None and dev["score"] == 20.0,
               str(dev["score"]) if dev else "None")

        # D2: 频率偏离(当前小时操作数远超 P95×3)
        dev = await ueba.compute_deviation(
            2, "/api/order/x", hour=14, current_hour_ops=100)
        record("D2频率偏离", dev is not None and any(
            d["code"] == "D2_burst" for d in dev["deviations"]),
            str(dev["deviations"])[:100] if dev else "None")

        # D4: 403 堆积
        dev = await ueba.compute_deviation(
            2, "/api/order/x", hour=14, forbidden_hits=5)
        record("D4试探堆积", dev is not None and any(
            d["code"] == "D4_probe" for d in dev["deviations"]),
            str(dev)[:100] if dev else "None")

        # 无偏离: 常规时段常规频率
        dev = await ueba.compute_deviation(
            2, "/api/order/x", hour=14, current_hour_ops=1)
        record("常规行为无偏离", dev is None, str(dev)[:80])

        # 冷启动豁免: 无任何基线的新角色会员(UEBA 个人+全局缺)
        # (member 全局已建 → 用不存在角色的口径直测)
        from services.ueba_service import UebaService
        record("无会员豁免",
               await ueba.compute_deviation(
                   0, "/api/order/x") is None)


class TestGatewayIntegration:
    async def run(self):
        print("[04 网关联动]")
        from services.security_service import Security43Service

        svc = Security43Service()
        # 会员 3 常规行为: 14 时 order(无基线→先豁免零误报)
        r = await svc.process_request(
            "8.8.8.1", method="GET", path="/api/order/list",
            ua="Mozilla/5.0", member_id=3, hour=14)
        record("无基线请求正常(零误报)",
               r["action"] == "allow", str(r["action"]))

        # 建立会员3基线(仅14时) → 3时请求触发 D1
        from services.ueba_service import UebaService
        ueba = UebaService()
        for _ in range(5):
            await ueba.record_behavior(3, "/api/order/x", hour=14)
        await ueba.rebuild_baselines()
        # 清缓存确保新基线生效
        UebaService._BASELINE_CACHE.clear()

        r = await svc.process_request(
            "8.8.8.1", method="GET", path="/api/order/list",
            ua="Mozilla/5.0", member_id=3, hour=3)
        record("偏离请求注入identity_risk",
               r["scoring"] is not None
               and any(f["name"] == "identity_risk"
                       and f["score"] < 100
                       for f in r["scoring"]["factors"]),
               str([f for f in (r["scoring"] or {}).get(
                   "factors", []) if f["name"] == "identity_risk"]))

        # D1+D3(3时+admin) → 分 0 → behavior_alert
        r = await svc.process_request(
            "8.8.8.1", method="GET", path="/api/admin/stats",
            ua="Mozilla/5.0", member_id=3, hour=3)
        alert = r.get("behaviorAlert")
        record("behavior_alert入事件", alert is not None
               and alert.get("action") == "behavior_alert",
               str(alert)[:80] if alert else "None")
        record("alert含检测器明细", alert is not None and any(
            d.get("name") == "D1_hour"
            for d in alert.get("factors", [])),
            str(alert.get("factors"))[:80] if alert else "")

        # 事件流水可查(复用P1裁决链)
        events = await svc.list_events(limit=50)
        alerts = [e for e in events
                  if e.get("action") == "behavior_alert"]
        record("alert事件流水可查", len(alerts) >= 1)

        # UEBA off: 请求照常(零影响)
        os.environ["SECURITY_UEBA_MODE"] = "off"
        try:
            r = await svc.process_request(
                "8.8.8.1", method="GET", path="/api/admin/stats",
                ua="Mozilla/5.0", member_id=3, hour=3)
            record("UEBA off零影响", r["action"] == "allow"
                   and r.get("behaviorAlert") is None)
        finally:
            os.environ["SECURITY_UEBA_MODE"] = "on"


class TestHttpRoutes:
    async def run(self):
        print("[05 HTTP层]")
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from routes.security_routes import register_security_routes
        from services.ueba_service import UebaService

        app = FastAPI()
        register_security_routes(app)
        client = TestClient(app)
        admin = {"X-Role": "admin"}

        resp = client.post("/api/security/admin/behavior/rebuild")
        record("HTTP-rebuild缺Role403", resp.status_code == 403)

        resp = client.post("/api/security/admin/behavior/rebuild",
                           headers=admin)
        record("HTTP-rebuild", resp.status_code == 200
               and resp.json().get("personal", 0) >= 1,
               str(resp.json())[:100])

        resp = client.get("/api/security/admin/behavior/baselines",
                          headers=admin)
        record("HTTP-baselines", resp.status_code == 200
               and resp.json().get("total", 0) >= 1,
               str(resp.json().get("total")))

        resp = client.get(
            "/api/security/admin/behavior/baselines?role=member",
            headers=admin)
        record("HTTP-baselines角色过滤",
               resp.json().get("total", 0) >= 1)

        resp = client.get(
            "/api/security/admin/behavior/deviations",
            headers=admin)
        record("HTTP-deviations", resp.status_code == 200
               and "deviations" in resp.json())


UebaServiceClass = None


async def run_all():
    global UebaServiceClass
    from services.ueba_service import UebaService
    UebaServiceClass = UebaService
    ueba = UebaService()
    await TestCollector().run(ueba)
    await TestBaseline().run(ueba)
    await TestDetector().run(ueba)
    await TestGatewayIntegration().run()
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
