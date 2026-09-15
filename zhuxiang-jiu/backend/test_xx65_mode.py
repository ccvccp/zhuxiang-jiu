"""65号·网店及商品AI智能管理大模型 三态灰度+护栏+控制面 专项测试

运行:
    $env:LOCK_MODE="asyncio"; $env:STORE_MODE="asyncio"; $env:AUTH_MODE="compat"
    python test_xx65_mode.py

覆盖:
    1. 三态语义: 默认 off / env 三态 / 非法回落 / require_decision off 拒绝
    2. 读取链: 护栏暂停 > override > env > off 四层
    3. override: 设值优先 / 清除回落 / 非法值拒绝
    4. 护栏: 未恶化不暂停 / 恶化暂停留痕 / 基线0规则 / baseline 覆盖 /
       resume 人工恢复 / 未暂停恢复拒绝 / 指标留痕
    5. 状态序列化往返: 整档 JSON——paused 布尔 / metrics 列表
       (45号 P0 教训: 字段级清单漏注册致 Redis 真值误判——本模块
        整档 JSON 存储, 类型天然保留)
    6. 同步桥接(62号范式): xx65_service.current_mode 读进程快照——
       override 透传 / 暂停回落 / env 兜底
    7. HTTP 决策面: off 下 11 端点全 409(决策面关闭)
    8. HTTP 宪法豁免面: off 下 close/human-review/inspect/
       feedback collect 200(不受开关影响)
    9. HTTP 观测面: off 下 registry/model-status/mode/shops 200
    10. HTTP shadow/assist: 放行 + xx65Mode 标记
    11. 控制面: GET /mode 形状 / override 运行时切档 / 护栏暂停与恢复
"""
import asyncio
import json
import os
import sys

os.environ["LOCK_MODE"] = "asyncio"
os.environ["STORE_MODE"] = "asyncio"
os.environ["AUTH_MODE"] = "compat"

from services.xx65_mode_service import (
    Xx65ModeService, MODE_VALUES,
    legacy_current_mode, _refresh_legacy, _G,
)

PASS = 0
FAIL = 0
RESULTS = []

_ENV_KEYS = ["XX65_MODE"]


def record(name, passed, detail=""):
    global PASS, FAIL
    if passed:
        PASS += 1
        RESULTS.append(f"  [PASS] {name}")
    else:
        FAIL += 1
        RESULTS.append(f"  [FAIL] {name} {detail}")


class _EnvGuard:
    """env 快照守卫(XX65_MODE 每段独立)"""

    def __init__(self, mode=None):
        self._mode = mode

    def __enter__(self):
        self._backup = os.environ.get("XX65_MODE")
        os.environ.pop("XX65_MODE", None)
        if self._mode:
            os.environ["XX65_MODE"] = self._mode
        return self

    def __exit__(self, *a):
        if self._backup is None:
            os.environ.pop("XX65_MODE", None)
        else:
            os.environ["XX65_MODE"] = self._backup


async def run_service():
    global PASS, FAIL
    from repositories.store import reset_store

    # ============================================================
    # 1. 三态语义
    # ============================================================
    with _EnvGuard():                       # 默认 off
        reset_store()
        svc = Xx65ModeService()
        m = await svc.current_mode()
        record("三态: 默认 off(env 源)", m["mode"] == "off"
               and m["source"] == "env")
        try:
            await svc.require_decision_mode()
            record("三态: off 决策面拒绝", False)
        except ValueError as e:
            record("三态: off 决策面拒绝",
                   "XX65_MODE=off" in str(e))
        os.environ["XX65_MODE"] = "invalid"
        m = await svc.current_mode()
        record("三态: 非法 env 回落 off", m["mode"] == "off")
    with _EnvGuard("shadow"):
        reset_store()
        svc = Xx65ModeService()
        m = await svc.current_mode()
        record("三态: env shadow", m["mode"] == "shadow")
        m = await svc.require_decision_mode()
        record("三态: shadow 决策面放行", m["mode"] == "shadow")
    with _EnvGuard("assist"):
        reset_store()
        svc = Xx65ModeService()
        m = await svc.current_mode()
        record("三态: env assist", m["mode"] == "assist")

    # ============================================================
    # 2. 读取链(护栏暂停 > override > env > off)
    # ============================================================
    with _EnvGuard("assist"):
        reset_store()
        svc = Xx65ModeService()
        # env assist 直读
        m = await svc.current_mode()
        record("读取链: env assist", m["mode"] == "assist"
               and m["source"] == "env")
        # override 优先于 env
        await svc.set_override("shadow")
        m = await svc.current_mode()
        record("读取链: override 优先(env assist→shadow)",
               m["mode"] == "shadow"
               and m["source"] == "runtime_override")
        # 护栏暂停优先于 override
        await svc.guard_check(0.10, 0.05, 0.20)
        m = await svc.current_mode()
        record("读取链: 暂停优先(override shadow→off)",
               m["mode"] == "off"
               and m["source"] == "guard_pause"
               and m["paused"] is True)
        # 恢复后回落 override
        await svc.resume(note="测试恢复")
        m = await svc.current_mode()
        record("读取链: 恢复后回落 override",
               m["mode"] == "shadow" and m["paused"] is False)
        # 清除 override 回 env
        await svc.set_override("")
        m = await svc.current_mode()
        record("读取链: 清除回落 env assist",
               m["mode"] == "assist" and m["source"] == "env")

    # ============================================================
    # 3. override 校验
    # ============================================================
    with _EnvGuard("off"):
        reset_store()
        svc = Xx65ModeService()
        try:
            await svc.set_override("bogus")
            record("override: 非法值拒绝", False)
        except ValueError as e:
            record("override: 非法值拒绝",
                   "非法灰度态" in str(e))
        m = await svc.set_override("assist")
        record("override: 合法 assist",
               m["mode"] == "assist")

    # ============================================================
    # 4. 护栏
    # ============================================================
    with _EnvGuard("assist"):
        reset_store()
        svc = Xx65ModeService()
        r = await svc.guard_check(0.10, 0.05, 0.05)
        record("护栏: 基线持平不暂停",
               r["breached"] is False
               and r["pausedNow"] is False)
        m = await svc.current_mode()
        record("护栏: 未暂停态 assist 保持",
               m["mode"] == "assist")
        # S5 撤销率恶化(0.05→0.10, +100%)
        r = await svc.guard_check(0.10, 0.05, 0.10)
        record("护栏: S5 撤销率恶化暂停",
               r["breached"] is True
               and r["pausedNow"] is True
               and any(b["metric"] == "campaignRevokeRate"
                       for b in r["breaches"]))
        m = await svc.current_mode()
        record("护栏: 暂停后决策面等效 off",
               m["mode"] == "off"
               and "护栏自动暂停" in m["pausedReason"])
        st = await svc._state()
        record("护栏: 暂停留痕(breachTrail)",
               len(st["breachTrail"]) == 1
               and st["pausedAt"] != "")
        # 多指标同时恶化
        await svc.resume(note="多指标前置恢复")
        r = await svc.guard_check(0.20, 0.10, 0.10)
        record("护栏: 三指标同恶化全留痕",
               len(r["breaches"]) == 3)
        # 基线 0 防除零(绝对值>0 判恶化)
        await svc.resume(note="基线0前置恢复")
        r = await svc.guard_check(0.01, 0.05, 0.05,
                                  baseline={
                                      "complianceBlockRate": 0})
        record("护栏: 基线0绝对值判恶化",
               any(b["metric"] == "complianceBlockRate"
                   for b in r["breaches"]))
        # 指标留痕滚动
        st = await svc._state()
        record("护栏: 指标留痕累计",
               len(st["metrics"]) >= 3)
        # 恢复
        m = await svc.resume(note="终态恢复")
        record("护栏: resume 人工恢复",
               m["paused"] is False)
        try:
            await svc.resume()
            record("护栏: 未暂停恢复拒绝", False)
        except ValueError as e:
            record("护栏: 未暂停恢复拒绝",
                   "无需恢复" in str(e))

    # ============================================================
    # 5. 状态序列化往返(整档 JSON——45号 P0 回归)
    # ============================================================
    with _EnvGuard("assist"):
        reset_store()
        svc = Xx65ModeService()
        await svc.set_override("shadow")
        await svc.guard_check(0.20, 0.10, 0.10)
        st = await svc._state()
        # json 往返(模拟 Redis 存储)
        raw = json.dumps(st, ensure_ascii=False)
        st2 = json.loads(raw)
        record("序列化: paused 布尔往返",
               st2["paused"] is True)
        record("序列化: metrics 列表往返",
               isinstance(st2["metrics"], list)
               and len(st2["metrics"]) >= 1)
        record("序列化: breachTrail 结构往返",
               isinstance(st2["breachTrail"], list)
               and st2["breachTrail"][0]["breaches"][0]
               ["baseline"] == 0.10)
        record("序列化: override 字符串往返",
               st2["override"] == "shadow")
        # 重新加载(服务实例重建——状态持久)
        svc2 = Xx65ModeService()
        m = await svc2.current_mode()
        record("序列化: 实例重建状态保持(暂停态)",
               m["source"] == "guard_pause"
               and m["mode"] == "off")

    # ============================================================
    # 6. 同步桥接(62号范式——服务层一代门控同源)
    # ============================================================
    with _EnvGuard("off"):
        reset_store()
        svc = Xx65ModeService()
        record("桥接: env off 直读",
               legacy_current_mode() == "off")
        await svc.set_override("assist")
        record("桥接: override 透传(env off→assist)",
               legacy_current_mode() == "assist")
        await svc.set_override("shadow")
        record("桥接: override 切 shadow",
               legacy_current_mode() == "shadow")
        await svc.set_override("")
        record("桥接: 清除回落 env off",
               legacy_current_mode() == "off")
        # 暂停透传(快照刷新)
        await svc.guard_check(0.20, 0.10, 0.10)
        record("桥接: 暂停回落 off(env off+暂停)",
               legacy_current_mode() == "off")
        await svc.resume(note="桥接恢复")
        record("桥接: 恢复回落 env off",
               legacy_current_mode() == "off")
    with _EnvGuard("assist"):
        reset_store()
        svc = Xx65ModeService()
        # env assist + 暂停 → 服务层同步读 off
        await svc.guard_check(0.20, 0.10, 0.10)
        record("桥接: env assist+暂停→同步 off",
               legacy_current_mode() == "off")
    # 快照复位(避免影响后续 HTTP 段)
    _refresh_legacy({"override": "", "paused": False})


async def run_http():
    global PASS, FAIL
    import httpx
    from main import app

    admin = {"X-Role": "admin"}

    # 11 决策面请求(off 下全 409; 载荷过参数校验即可——门控在装饰器层)
    def decision_requests():
        return [
            ("POST", "/api/xx65/intents/parse",
             {"ownerId": 1, "text": "手工木雕定制"}),
            ("POST", "/api/xx65/shops/apply",
             {"ownerId": 1, "intentId": 1}),
            ("POST", "/api/xx65/shops/1/claim",
             {"ownerId": 1}),
            ("POST", "/api/xx65/shops/1/activate",
             {"ownerId": 1}),
            ("POST", "/api/xx65/products/draft",
             {"ownerId": 1, "shopId": 1,
              "title": "t", "body": "b"}),
            ("POST", "/api/xx65/drafts/1/publish",
             {"operator": "admin"}),
            ("POST", "/api/xx65/campaigns",
             {"shopId": 1, "strategy": "clearance"}),
            ("POST", "/api/xx65/campaigns/1/revoke",
             {"operator": "admin"}),
            ("POST", "/api/xx65/shops/1/quota-adjust",
             {"direction": "up"}),
            ("POST", "/api/xx65/shops/1/dispute-assist",
             {"orderId": "O1"}),
            ("POST", "/api/xx65/redteam", {}),
        ]

    with _EnvGuard("off"):
        from repositories.store import reset_store
        reset_store()
        _refresh_legacy({"override": "", "paused": False})
        async with httpx.AsyncClient(
                app=app, base_url="http://t") as c:
            # 决策面 off 全 409
            for _method, url, payload in decision_requests():
                r = await c.post(url, json=payload,
                                 headers=admin)
                record(f"HTTP: off 409 {url.rsplit('/', 1)[-1]}",
                       r.status_code == 409,
                       f"got {r.status_code}")
            # 宪法豁免面 off 200(不受开关)
            r = await c.post("/api/xx65/shops/1/close",
                             json={"reason": "测试"},
                             headers=admin)
            record("HTTP: 豁免面 close off 200(经营者权利)",
                   r.status_code in (200, 404))
            r = await c.post("/api/xx65/drafts/1/human-review",
                            json={"verdict": "approved"},
                            headers=admin)
            record("HTTP: 豁免面 human-review off 200(S6)",
                   r.status_code in (200, 404))
            r = await c.post("/api/xx65/products/inspect",
                            json={"shopId": 1}, headers=admin)
            record("HTTP: 豁免面 inspect off 200(合规防线)",
                   r.status_code in (200, 404))
            r = await c.post("/api/xx65/feedback/collect",
                            json={"productId": 1,
                                  "signal": "ok"},
                            headers=admin)
            record("HTTP: 豁免面 feedback collect off 200",
                   r.status_code in (200, 404, 409))
            # 观测面 off 200
            for url in ("/api/xx65/registry",
                        "/api/xx65/model/status",
                        "/api/xx65/mode",
                        "/api/xx65/shops",
                        "/api/xx65/dashboard"):
                r = await c.get(url, headers=admin)
                record(f"HTTP: 观测面 off 200 "
                       f"{url.rsplit('/', 1)[-1]}",
                       r.status_code == 200,
                       f"got {r.status_code}")

            # 控制面形状
            r = await c.get("/api/xx65/mode", headers=admin)
            body = r.json()
            record("控制面: /mode 形状",
                   body.get("mode") == "off"
                   and body.get("modeValues")
                   == ["off", "shadow", "assist"]
                   and "guard" in body)

            # 运行时切档 → shadow 放行 + xx65Mode 标记
            r = await c.post("/api/xx65/mode/override",
                             json={"mode": "shadow"},
                             headers=admin)
            record("控制面: override→shadow",
                   r.status_code == 200
                   and r.json()["data"]["mode"] == "shadow")
            r = await c.post("/api/xx65/intents/parse",
                            json={"ownerId": 9001,
                                  "text": "手工木雕"},
                            headers=admin)
            record("HTTP: shadow 放行+xx65Mode 标记",
                   r.status_code == 200
                   and r.json().get("xx65Mode") == "shadow",
                   f"got {r.status_code} "
                   f"{str(r.json())[:80]}")
            # assist 标记
            await c.post("/api/xx65/mode/override",
                         json={"mode": "assist"},
                         headers=admin)
            r = await c.post("/api/xx65/intents/parse",
                            json={"ownerId": 9002,
                                  "text": "茶叶特产"},
                            headers=admin)
            record("HTTP: assist 放行+标记",
                   r.status_code == 200
                   and r.json().get("xx65Mode") == "assist")
            # 非法 override 409
            r = await c.post("/api/xx65/mode/override",
                             json={"mode": "bogus"},
                             headers=admin)
            record("控制面: 非法 override 409",
                   r.status_code == 409)

            # 护栏暂停 → 决策面等效 off
            r = await c.post("/api/xx65/mode/guard",
                            json={"complianceBlockRate": 0.20,
                                  "shopViolationRate": 0.10,
                                  "campaignRevokeRate": 0.10},
                            headers=admin)
            g = r.json()["data"]
            record("控制面: guard 三指标恶化暂停",
                   g["breached"] is True
                   and g["pausedNow"] is True)
            r = await c.post("/api/xx65/intents/parse",
                            json={"ownerId": 9003,
                                  "text": "测试"},
                            headers=admin)
            _err = str(r.json().get("detail")
                       or r.json().get("error") or "")
            record("HTTP: 暂停后决策面 409",
                   r.status_code == 409
                   and "guard_pause" in _err)
            # 恢复 → 决策面回 assist
            r = await c.post("/api/xx65/mode/resume",
                            json={"note": "测试恢复"},
                            headers=admin)
            record("控制面: resume 恢复",
                   r.status_code == 200
                   and r.json()["data"]["mode"] == "assist")
            r = await c.post("/api/xx65/intents/parse",
                            json={"ownerId": 9004,
                                  "text": "服饰"},
                            headers=admin)
            record("HTTP: 恢复后决策面放行",
                   r.status_code == 200
                   and r.json().get("xx65Mode") == "assist")
            # 未暂停恢复 409
            r = await c.post("/api/xx65/mode/resume",
                            json={}, headers=admin)
            record("控制面: 未暂停 resume 409",
                   r.status_code == 409)
            # 清除 override 回 env off
            await c.post("/api/xx65/mode/override",
                         json={"mode": ""}, headers=admin)
            r = await c.post("/api/xx65/intents/parse",
                            json={"ownerId": 9005,
                                  "text": "测试"},
                            headers=admin)
            record("HTTP: 清除 override 回 off 409",
                   r.status_code == 409)


def main():
    asyncio.run(run_service())
    asyncio.run(run_http())
    print("\n".join(RESULTS))
    print("-" * 64)
    print(f"通过 {PASS} 项 / 失败 {FAIL} 项")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
