"""37号·AI智能网站同盟大模型·治理层测试(P3 升级)

覆盖:
    1. 模式服务: 默认 assist(业务在线铁律)/env 优先级/
       override 优先/护栏暂停最高优先/override 清除回落
    2. 决策面门槛: off 拒绝(ValueError→409)/
       shadow+assist 放行
    3. 护栏: 正常指标不暂停/恶化>3% 自动暂停+留痕/
       resume 人工恢复/未暂停 resume 拒绝
    4. 白皮书: 四章节固定结构/聚合正确/样本门/
       零个体数据
    5. HTTP 层: 治理端点鉴权与语义/off 决策面 409/
       观测面永不关停/护栏自动暂停→resume 链

运行:
    $env:LOCK_MODE="asyncio"; $env:STORE_MODE="asyncio"
    python test_alliance_mode.py
"""

import asyncio
import os
import sys


# 确保使用内存模式
os.environ["LOCK_MODE"] = "asyncio"
os.environ["STORE_MODE"] = "asyncio"

from services.alliance_mode_service import (
    AllianceModeService,
    MODE_VALUES,
)
from services.alliance_whitepaper_service import (
    AllianceWhitepaperService,
)
from repositories.alliance_repository import (
    AllianceRepository,
)
from repositories.store import reset_store as _reset_store

PASS = 0
FAIL = 0
RESULTS = []


def record(name, passed, detail=""):
    global PASS, FAIL
    if passed:
        PASS += 1
        RESULTS.append(f"  \u2713 {name}")
    else:
        FAIL += 1
        RESULTS.append(f"  \u2717 {name} \u2014 {detail}")


def reset_all():
    _reset_store()
    os.environ.pop("ALLIANCE37_MODE", None)


class TestModeService:
    async def run(self):
        print("[01 模式服务]")
        reset_all()
        svc = AllianceModeService()

        # 默认 assist(业务在线铁律)
        state = await svc.current_mode()
        record("默认assist(业务在线)",
               state["mode"] == "assist"
               and state["source"] == "env", str(state))

        # env off → off
        os.environ["ALLIANCE37_MODE"] = "off"
        state = await svc.current_mode()
        record("env=off生效", state["mode"] == "off")

        # override 优先于 env
        state = await svc.set_override("shadow")
        record("override优先于env",
               state["mode"] == "shadow"
               and state["source"] == "runtime_override")

        # override 清除回落 env
        state = await svc.set_override("")
        record("override清除回落env",
               state["mode"] == "off" and state["source"] == "env")

        # 非法 mode 拒绝
        try:
            await svc.set_override("full")
            record("非法mode拒绝", False)
        except ValueError:
            record("非法mode拒绝", True)

        # 护栏暂停最高优先(等效 off)
        os.environ["ALLIANCE37_MODE"] = "assist"
        await svc.set_override("")
        result = await svc.guard_check(
            refund_rate=0.10, complaint_rate=0.02,
            termination_rate=0.03)
        record("护栏恶化自动暂停",
               result["breached"] and result["pausedNow"])
        state = await svc.current_mode()
        record("暂停态等效off",
               state["mode"] == "off"
               and state["source"] == "guard_pause"
               and "护栏自动暂停" in state["pausedReason"])

        # resume 人工恢复
        state = await svc.resume(operator="admin",
                                note="测试恢复")
        record("resume恢复assist",
               state["mode"] == "assist"
               and state["source"] == "env")

        # 未暂停时 resume 拒绝
        try:
            await svc.resume()
            record("未暂停resume拒绝", False)
        except ValueError:
            record("未暂停resume拒绝", True)

        # 正常指标不暂停
        result = await svc.guard_check(
            refund_rate=0.05, complaint_rate=0.02,
            termination_rate=0.03)
        record("正常指标不暂停",
               not result["breached"])

        # 恶化阈值边界(<3% 不算恶化——浮点安全裕度)
        result = await svc.guard_check(
            refund_rate=0.0514, complaint_rate=0.02,
            termination_rate=0.03)
        record("低于3%不算恶化",
               not result["breached"])

        # 指标留痕累计(3 次 guard_check)
        st = await svc._state()
        record("指标留痕累计",
               len(st["metrics"]) == 3,
               str(len(st["metrics"])))

        # status_view 总览结构
        view = await svc.status_view()
        record("总览结构完整",
               view["modeValues"] == list(MODE_VALUES)
               and "guard" in view
               and view["guard"]["threshold"] == 0.03
               and "observablesNeverOff" in view
               and "decisionSurfaces" in view)


class TestDecisionGate:
    async def run(self):
        print("[02 决策面门槛]")
        reset_all()
        svc = AllianceModeService()

        # off → ValueError(映射 409)
        os.environ["ALLIANCE37_MODE"] = "off"
        try:
            await svc.require_decision_mode()
            record("off决策面拒绝", False)
        except ValueError as e:
            record("off决策面拒绝",
                   "决策面关闭" in str(e), str(e))

        # shadow → 放行
        os.environ["ALLIANCE37_MODE"] = "shadow"
        state = await svc.require_decision_mode()
        record("shadow决策面放行",
               state["mode"] == "shadow")

        # assist → 放行
        os.environ["ALLIANCE37_MODE"] = "assist"
        state = await svc.require_decision_mode()
        record("assist决策面放行",
               state["mode"] == "assist")

        # 护栏暂停态下决策面拒绝
        await svc.guard_check(refund_rate=0.50,
                              complaint_rate=0.02,
                              termination_rate=0.03)
        try:
            await svc.require_decision_mode()
            record("暂停态决策面拒绝", False)
        except ValueError:
            record("暂停态决策面拒绝", True)


class TestWhitepaper:
    async def run(self):
        print("[03 白皮书]")
        reset_all()
        svc = AllianceWhitepaperService()

        # 空库白皮书(四章节结构)
        wp = await svc.whitepaper(2026)
        record("四章节固定结构",
               all(k in wp for k in (
                   "framework", "annualData",
                   "redlineCases", "openInitiative")))
        record("标题年份",
               "2026" in wp["title"] and wp["year"] == 2026)
        record("红线案例≥5",
               len(wp["redlineCases"]) >= 5)
        record("开放许可CC",
               wp["openInitiative"]["license"]
               == "CC BY-NC-SA 4.0")

        # 造数据: 类目种子+商户+商品+订单+结算
        repo = AllianceRepository()
        await repo.ensure_categories()
        for i, code in enumerate(
                ("water", "tea", "wine", "dish")):
            await repo.save_merchant({
                "merchantId": i + 1,
                "memberId": 100 + i,
                "category": code,
                "shopName": f"测试商铺{i}",
                "status": "active",
                "createdAt": "2026-09-01T00:00:00Z",
            })
        await repo.save_product({
            "productId": 1, "merchantId": 1,
            "name": "测试泉水", "price": 10.0,
            "stock": 5, "status": "active",
        })
        await repo.save_order({
            "orderId": "ALO1", "productId": 1,
            "merchantId": 1, "buyerId": 999,
            "quantity": 2, "amount": 20.0,
            "status": "paid", "settled": False,
            "createdAt": "2026-09-01T00:00:00Z",
        })
        await repo.save_settlement({
            "settlementId": 1, "orderId": "ALO1",
            "merchantId": 1, "orderAmount": 20.0,
            "commission": 3.0, "merchantProceeds": 17.0,
            "status": "settled",
            "settledAt": "2026-09-02T00:00:00Z",
        })

        wp = await svc.whitepaper(2026)
        data = wp["annualData"]
        record("年度商户聚合",
               data["merchants"]["total"] == 4)
        record("订单GMV聚合",
               data["orders"]["total"] == 1
               and data["orders"]["gmv"] == 20.0)
        record("分润聚合",
               data["settlements"]["commissionTotal"] == 3.0
               and data["settlements"]["proceedsTotal"] == 17.0)
        # 样本门: 单类目商户 1 家 <3 → 不出数
        record("样本门<3不出数",
               data["merchants"]["categoryDist"]["water"]
               is None)
        # 年份过滤(2025 无数据)
        wp25 = await svc.whitepaper(2025)
        record("年份过滤",
               wp25["annualData"]["merchants"]
               ["total"] == 0)
        # 零个体数据: 白皮书正文无 shopName 明细
        record("零个体数据",
               "测试商铺" not in str(
                   wp["annualData"]))


class TestAutoGuard:
    async def run(self):
        print("[05 实测护栏自动聚合]")
        reset_all()
        svc = AllianceModeService()
        repo = AllianceRepository()

        # 空库: 三指标全 0 + 全部样本门关闭
        computed = await svc.auto_metrics()
        record("空库三指标全0",
               computed["metrics"] == {
                   "refundRate": 0.0,
                   "complaintRate": 0.0,
                   "terminationRate": 0.0})
        record("空库样本门全关",
               all(computed["gated"].values()))

        # 空库实测护栏: 无恶化不暂停
        result = await svc.auto_guard_check()
        record("空库实测不暂停",
               not result["guard"]["breached"])

        # 窗口参数非法拒绝
        try:
            await svc.auto_metrics(0)
            record("窗口0拒绝", False)
        except ValueError:
            record("窗口0拒绝", True)

        # 造数据: 6 结算(1 冲正) + 6 评价(2 差评) + 4 商户(1 终止)
        now = "2026-09-15T00:00:00+00:00"
        for i in range(5):
            await repo.save_settlement({
                "settlementId": i + 1,
                "orderId": f"ALO{i + 1}",
                "merchantId": 1, "orderAmount": 10.0,
                "commission": 1.5, "merchantProceeds": 8.5,
                "status": "settled", "settledAt": now})
        await repo.save_settlement({
            "settlementId": 6, "orderId": "ALO6",
            "merchantId": 1, "orderAmount": 10.0,
            "commission": 1.5, "merchantProceeds": 8.5,
            "status": "reversed",
            "reversedAt": now})
        for i in range(4):
            await repo.save_review({
                "reviewId": i + 1, "orderId": f"ALO{i + 1}",
                "merchantId": 1, "reviewerId": 999,
                "score": 5, "content": "好",
                "folded": False, "createdAt": now})
        for i in range(2):
            await repo.save_review({
                "reviewId": 5 + i, "orderId": f"ALO{5 + i}",
                "merchantId": 1, "reviewerId": 998,
                "score": 1, "content": "差",
                "folded": False, "createdAt": now})
        for i in range(3):
            await repo.save_merchant({
                "merchantId": i + 1, "memberId": 100 + i,
                "category": "tea", "shopName": f"商铺{i}",
                "status": "active", "createdAt": now})
        await repo.save_merchant({
            "merchantId": 4, "memberId": 104,
            "category": "tea", "shopName": "终止铺",
            "status": "terminated",
            "terminatedAt": now})

        computed = await svc.auto_metrics()
        m = computed["metrics"]
        record("退款率聚合(1/6)",
               abs(m["refundRate"] - 1 / 6) < 0.001,
               str(m["refundRate"]))
        record("客诉率聚合(2/6)",
               abs(m["complaintRate"] - 2 / 6) < 0.001,
               str(m["complaintRate"]))
        record("清退率聚合(1/4)",
               abs(m["terminationRate"] - 0.25) < 0.001,
               str(m["terminationRate"]))
        record("样本门全开",
               not any(computed["gated"].values()))

        # 差评 33% >> 基线 2%×1.03 → 恶化自动暂停
        result = await svc.auto_guard_check()
        record("差评恶化实测自动暂停",
               result["guard"]["breached"]
               and result["guard"]["pausedNow"])
        state = await svc.current_mode()
        record("实测暂停后等效off",
               state["mode"] == "off"
               and state["source"] == "guard_pause")

        # resume 恢复
        await svc.resume(note="实测测试恢复")

        # 窗口外数据不计入(老结算单排除)
        old = "2025-01-01T00:00:00+00:00"
        await repo.save_settlement({
            "settlementId": 7, "orderId": "ALO7",
            "merchantId": 1, "orderAmount": 10.0,
            "commission": 1.5, "merchantProceeds": 8.5,
            "status": "reversed", "reversedAt": old})
        computed = await svc.auto_metrics()
        record("窗口外冲正不计入",
               abs(computed["metrics"]["refundRate"]
                   - 1 / 6) < 0.001)

        # HTTP: 实测护栏端点
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from routes.alliance_routes import (
            register_alliance_routes,
        )
        app = FastAPI()
        register_alliance_routes(app)
        client = TestClient(app)
        admin = {"X-Role": "admin"}

        resp = client.post(
            "/api/alliance/mode/guard/auto",
            json={"windowDays": 30}, headers=admin)
        body = resp.json()["data"]
        record("guard/auto端点200",
               resp.status_code == 200
               and body["computed"]["windowDays"] == 30)
        record("guard/auto同链路",
               "breaches" in body["guard"])

        resp = client.post(
            "/api/alliance/mode/guard/auto",
            json={"windowDays": 30})
        record("guard/auto缺Role403",
               resp.status_code == 403)


class TestHttp:
    async def run(self):
        print("[04 HTTP 层]")
        reset_all()
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from routes.alliance_routes import (
            register_alliance_routes,
        )
        app = FastAPI()
        register_alliance_routes(app)
        client = TestClient(app)
        admin = {"X-Role": "admin"}
        member = {"X-Member-Id": "1"}

        # 观测面: mode 总览 200
        resp = client.get("/api/alliance/mode")
        body = resp.json()
        record("mode总览200观测面",
               resp.status_code == 200
               and body["data"]["mode"] == "assist")

        # 治理端点鉴权 403(带合法 body——Pydantic
        # 先于 handler 校验, 空 body 会 422)
        resp = client.post(
            "/api/alliance/mode/override?mode=off")
        record("override缺Role403",
               resp.status_code == 403)
        resp = client.post(
            "/api/alliance/mode/guard",
            json={"refundRate": 0.05,
                  "complaintRate": 0.02,
                  "terminationRate": 0.03})
        record("guard缺Role403",
               resp.status_code == 403)
        resp = client.post(
            "/api/alliance/mode/resume")
        record("resume缺Role403",
               resp.status_code == 403)

        # override 切 off(留痕)
        resp = client.post(
            "/api/alliance/mode/override?mode=off",
            headers=admin)
        record("override切off200",
               resp.status_code == 200
               and resp.json()["data"]["mode"] == "off")

        # off: 决策面 409(入盟申请/下单/审核)
        resp = client.post("/api/alliance/apply", json={
            "memberId": 1, "category": "tea",
            "shopName": "测试茶庄"})
        record("off入盟申请409",
               resp.status_code == 409,
               str(resp.status_code))
        resp = client.post("/api/alliance/order", json={
            "productId": 1, "quantity": 1},
            headers=member)
        record("off下单409",
               resp.status_code == 409)
        resp = client.post(
            "/api/alliance/applications/1/audit",
            json={"approved": True}, headers=admin)
        record("off审批409",
               resp.status_code == 409)

        # off: 观测面永不关停(类目/报表/评价)
        resp = client.get("/api/alliance/categories")
        record("off类目仍200",
               resp.status_code == 200)
        resp = client.get("/api/alliance/report/overview",
                          headers=admin)
        record("off报表仍200",
               resp.status_code == 200)
        resp = client.get("/api/alliance/merchants",
                          headers=admin)
        record("off商户列表仍200",
               resp.status_code == 200)

        # off: 白皮书仍 200(观测面)
        resp = client.get("/api/alliance/whitepaper")
        record("off白皮书仍200",
               resp.status_code == 200
               and "framework" in resp.json()["data"])

        # 恢复 assist
        resp = client.post(
            "/api/alliance/mode/override?mode=assist",
            headers=admin)
        record("override回assist",
               resp.status_code == 200
               and resp.json()["data"]["mode"] == "assist")

        # assist: 决策面放行(业务校验报 409 但
        # 非 mode 拒绝——detail 不含决策面关闭)
        resp = client.post("/api/alliance/apply", json={
            "memberId": 1, "category": "tea",
            "shopName": "测试茶庄"})
        detail = str(resp.json().get("detail", ""))
        record("assist决策面放行(非模式拒绝)",
               "决策面关闭" not in detail, detail[:60])

        # 护栏: 恶化→自动暂停→mode=off
        resp = client.post("/api/alliance/mode/guard",
                          json={"refundRate": 0.20,
                                "complaintRate": 0.02,
                                "terminationRate": 0.03},
                          headers=admin)
        record("guard恶化暂停",
               resp.status_code == 200
               and resp.json()["data"]["breached"])
        resp = client.get("/api/alliance/mode")
        record("暂停后mode=off",
               resp.json()["data"]["mode"] == "off"
               and resp.json()["data"]["source"]
               == "guard_pause")

        # resume 人工恢复
        resp = client.post(
            "/api/alliance/mode/resume?note=测试恢复",
            headers=admin)
        record("resume后回assist",
               resp.status_code == 200
               and resp.json()["data"]["mode"] == "assist")


async def main():
    await TestModeService().run()
    await TestDecisionGate().run()
    await TestWhitepaper().run()
    await TestHttp().run()
    await TestAutoGuard().run()
    print()
    print(f"通过: {PASS} / 失败: {FAIL} / 总计: {PASS + FAIL}")
    for line in RESULTS:
        print(line)
    return FAIL == 0


if __name__ == "__main__":
    sys.exit(0 if asyncio.run(main()) else 1)
