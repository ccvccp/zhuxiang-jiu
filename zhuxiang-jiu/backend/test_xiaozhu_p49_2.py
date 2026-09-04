"""49号·小竹可信函数调用深化 P2 专项测试
(隐私预算)

运行方式:
    python test_xiaozhu_p49_2.py

覆盖(49号计划 §六 P2):
    - 预算数学: 默认 1.0/限额=预算×偏好/余额=限额-累计
    - 原子扣减: 顺序扣减/精确余额/负值拒绝
    - 超限: 429 话术(剩余/需求)/拒绝后状态不变
    - 只读零成本: 永不检查永不扣减(兜底出口零降级)
    - 偏好调整: 0.5-2.0 边界/越界拒绝/调整后限额变化
    - 日切重置: dayKey 变更 → 清零入史(留 7 日)
    - 网关管道: 高敏超限 fallback(safeMessage 含预算
      提示)/扣减落账
    - 语音指令: "我的隐私预算"(余额+偏好卡片)
    - HTTP 层: budget/preferences 端点/鉴权
    - 预算均等红线: 偏好与信值等级无关(两会员同权)
"""

import asyncio
import os
import sys

os.environ["LOCK_MODE"] = "asyncio"
os.environ["STORE_MODE"] = "asyncio"
os.environ.pop("LLM_API_KEY", None)
os.environ["LLM_ENABLED"] = "off"
os.environ["XIAOZHU_LLM_MODE"] = "off"
os.environ["XIAOZHU_PROACTIVE_MODE"] = "off"

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
    import services.xiaozhu_executor as ex_mod
    ex_mod._EXECUTOR_SINGLETON = None


async def _session(member_id: int) -> int:
    from services.xiaozhu_service import XiaozhuService
    return (await XiaozhuService().open_session(
        member_id))["sessionId"]


async def _text(sid: int, text: str) -> dict:
    from services.xiaozhu_service import XiaozhuService
    return await XiaozhuService().handle_text(sid, text)


class TestBudgetMath:
    async def run(self):
        print("[01 预算数学]")
        reset_all()
        from services.xiaozhu_privacy_service import (
            XiaozhuPrivacyService,
        )
        svc = XiaozhuPrivacyService()
        v = await svc.budget_view(70)
        record("默认预算 1.0/偏好 1.0",
               v["dailyBudget"] == 1.0
               and v["preference"] == 1.0)
        record("初始限额=预算×偏好",
               v["effectiveLimit"] == 1.0
               and v["remaining"] == 1.0)
        record("视图含均等声明",
               "信值等级" in v["note"])

        # 原子扣减
        r = await svc.check_and_spend(70, 0.08)
        record("扣减 0.08(余额 0.92)",
               r["spent"] == 0.08
               and r["remaining"] == 0.92)
        v = await svc.budget_view(70)
        record("扣减落账",
               v["usedToday"] == 0.08
               and v["remaining"] == 0.92)
        # 顺序扣减
        await svc.check_and_spend(70, 0.02)
        v = await svc.budget_view(70)
        record("顺序扣减累计",
               v["usedToday"] == 0.1
               and v["remaining"] == 0.9)
        # 零成本
        r = await svc.check_and_spend(70, 0.0)
        record("零成本短路(不检查不扣减)",
               r["zeroCost"] is True
               and r["spent"] == 0.0)
        v = await svc.budget_view(70)
        record("零成本未扣减",
               v["usedToday"] == 0.1)


class TestOverLimit:
    async def run(self):
        print("[02 超限拒绝与话术]")
        reset_all()
        from services.xiaozhu_privacy_service import (
            XiaozhuPrivacyService,
        )
        svc = XiaozhuPrivacyService()
        # 先耗 0.96
        await svc.check_and_spend(71, 0.96)
        # 超限(需 0.08 > 剩 0.04)
        try:
            await svc.check_and_spend(71, 0.08)
            record("超限拒绝", False, "未抛")
        except ValueError as e:
            record("超限拒绝", True)
            record("429 话术(剩余/需求/偏好引导)",
                   "隐私预算不足" in str(e)
                   and "剩余 0.04" in str(e)
                   and "需 0.08" in str(e)
                   and "隐私偏好" in str(e),
                   str(e)[:60])
        # 拒绝后状态不变
        v = await svc.budget_view(71)
        record("拒绝后余额不变",
               v["usedToday"] == 0.96
               and v["remaining"] == 0.04)
        # 恰好等额可过(剩 0.04 → 花 0.04)
        r = await svc.check_and_spend(71, 0.04)
        record("等额通过(边界)",
               r["remaining"] == 0.0)


class TestPreference:
    async def run(self):
        print("[03 偏好调整(会员自主)]")
        reset_all()
        from services.xiaozhu_privacy_service import (
            XiaozhuPrivacyService,
        )
        svc = XiaozhuPrivacyService()
        v = await svc.set_preference(72, 1.5)
        record("偏好 1.5 生效",
               v["preference"] == 1.5
               and v["effectiveLimit"] == 1.5)
        v = await svc.set_preference(72, 0.5)
        record("偏好下界 0.5",
               v["effectiveLimit"] == 0.5)
        v = await svc.set_preference(72, 2.0)
        record("偏好上界 2.0",
               v["effectiveLimit"] == 2.0)
        # 越界
        for bad in (0.3, 2.5, -1):
            try:
                await svc.set_preference(72, bad)
                record(f"越界拒绝({bad})", False, "未抛")
                break
            except ValueError:
                record(f"越界拒绝({bad})", True)
        # 调整后扣减能力变化
        await svc.set_preference(72, 2.0)
        r = await svc.check_and_spend(72, 1.5)
        record("偏好上调后大额可过",
               r["remaining"] == 0.5)
        # 非数值
        try:
            await svc.set_preference(72, "abc")
            record("非数值拒绝", False, "未抛")
        except ValueError:
            record("非数值拒绝", True)


class TestDayReset:
    async def run(self):
        print("[04 日切重置]")
        reset_all()
        from services.xiaozhu_privacy_service import (
            XiaozhuPrivacyService,
        )
        svc = XiaozhuPrivacyService()
        await svc.check_and_spend(73, 0.3)
        # 手动改 dayKey 模拟跨日
        from repositories.xiaozhu_repository import (
            Xiaozhu48Repository,
        )
        repo = Xiaozhu48Repository()
        rec = await repo.get_privacy_budget(73)
        rec["dayKey"] = "2000-01-01"
        await repo.save_privacy_budget(rec)
        v = await svc.budget_view(73)
        record("日切清零",
               v["usedToday"] == 0.0
               and v["remaining"] == 1.0)
        record("日切入史(7 日)",
               len(v["history"]) == 1
               and v["history"][0]["used"] == 0.3,
               str(v["history"]))
        # 历史留 7 日
        for i in range(9):
            rec = await repo.get_privacy_budget(73)
            rec["dayKey"] = f"2000-01-0{i + 2:02d}"
            await repo.save_privacy_budget(rec)
            await svc.check_and_spend(73, 0.1)
        v = await svc.budget_view(73)
        record("历史滚动保留 7 条",
               len(v["history"]) == 7,
               str(len(v["history"])))


class TestGatewayBudget:
    async def run(self):
        print("[05 网关管道预算)")
        reset_all()
        from services.xiaozhu_fc_gateway import (
            XiaozhuFcGateway,
        )
        from services.xiaozhu_privacy_service import (
            XiaozhuPrivacyService,
        )
        gw = XiaozhuFcGateway()
        session = {"sessionId": 1, "memberId": 74}
        # 高敏: 挑战流扣减 0.08
        r = await gw.call_tool(session, "trust.convert",
                               {"creditPoints": 100})
        v = await XiaozhuPrivacyService().budget_view(74)
        record("高敏挑战扣减 0.08",
               v["usedToday"] == 0.08)
        # 写: cart.submit 扣减 0.05
        await gw.call_tool(session, "cart.submit",
                          {"items": [{"skuId": "A1",
                                      "qty": 1}]})
        v = await XiaozhuPrivacyService().budget_view(74)
        record("写执行扣减 0.05",
               v["usedToday"] == 0.13)
        # 只读: 零成本不扣减
        await gw.call_tool(session, "product.new", {})
        v = await XiaozhuPrivacyService().budget_view(74)
        record("只读零成本不扣减",
               v["usedToday"] == 0.13)
        # 超限 → fallback: 直写账户将当日累计顶满
        from repositories.xiaozhu_repository import (
            Xiaozhu48Repository,
        )
        repo = Xiaozhu48Repository()
        rec = await repo.get_privacy_budget(74)
        rec["usedToday"] = 0.97   # 剩 0.03 < 高敏 0.08
        await repo.save_privacy_budget(rec)
        r = await gw.call_tool(session, "trust.convert",
                               {"creditPoints": 50})
        record("网关超限 fallback",
               r.get("fallback") is True
               and "隐私预算不足" in (
                   r.get("safeMessage") or ""),
               str(r.get("safeMessage"))[:40])


class TestVoiceCommand:
    async def run(self):
        print("[06 语音指令]")
        reset_all()
        from services.xiaozhu_privacy_service import (
            XiaozhuPrivacyService,
        )
        await XiaozhuPrivacyService().check_and_spend(
            75, 0.2)
        sid = await _session(75)
        r = await _text(sid, "小竹，我的隐私预算")
        record("指令直达(intent=privacy.budget)",
               r.get("turn", {}).get("intent")
               == "privacy.budget",
               str(r.get("turn", {}).get("intent")))
        record("播报含余额",
               "剩余 0.8" in r.get("reply", ""),
               str(r.get("reply"))[:50])
        card = r.get("card") or {}
        record("卡片含偏好/限额",
               card.get("remaining") == 0.8
               and card.get("preference") == 1.0
               and card.get("effectiveLimit") == 1.0)
        # 预算均等红线: 两会员(不同等级)同权
        r2 = await _text(sid, "小竹，查我的信值")
        v1 = await XiaozhuPrivacyService().budget_view(1)
        v2 = await XiaozhuPrivacyService().budget_view(75)
        record("预算均等(与信值等级无关)",
               v1["dailyBudget"] == v2["dailyBudget"]
               and v1["preference"] == v2["preference"])


class TestHttp:
    async def run(self):
        print("[07 HTTP 层]")
        reset_all()
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from routes.xiaozhu_routes import (
            register_xiaozhu_routes,
        )
        app = FastAPI()
        register_xiaozhu_routes(app)
        client = TestClient(app)
        h = {"X-Member-Id": "76"}

        resp = client.get("/api/xiaozhu/privacy/budget",
                         headers=h)
        body = resp.json()
        record("GET budget 200",
               resp.status_code == 200
               and body.get("remaining") == 1.0,
               str(body.get("remaining")))
        record("视图含历史与声明",
               isinstance(body.get("history"), list)
               and "信值等级" in body.get("note", ""))
        resp = client.get("/api/xiaozhu/privacy/budget")
        record("budget 缺 Member 401",
               resp.status_code == 401)
        # 偏好调整
        resp = client.put(
            "/api/xiaozhu/privacy/preferences",
            json={"preference": 1.5}, headers=h)
        record("PUT preference 200",
               resp.status_code == 200
               and resp.json()
               .get("effectiveLimit") == 1.5)
        resp = client.put(
            "/api/xiaozhu/privacy/preferences",
            json={"preference": 5}, headers=h)
        record("偏好越界 409", resp.status_code == 409)
        resp = client.put(
            "/api/xiaozhu/privacy/preferences",
            json={}, headers=h)
        record("请求体缺字段 409",
               resp.status_code == 409)
        resp = client.put(
            "/api/xiaozhu/privacy/preferences",
            json={"preference": 1.0})
        record("preferences 缺 Member 401",
               resp.status_code == 401)


async def run_all():
    await TestBudgetMath().run()
    await TestOverLimit().run()
    await TestPreference().run()
    await TestDayReset().run()
    await TestGatewayBudget().run()
    await TestVoiceCommand().run()
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
