"""48号·小竹智能语音中枢 P1 专项测试
(认知层·角色感知大脑)

运行方式:
    python test_xiaozhu_p1.py

覆盖(计划 §五):
    - 绑定表: 绑定/改绑/解除/未绑定 404/档案不存在拒绝/
      会话内快捷绑定流
    - 角色上下文: 等级注入/绑定态/信值余额/偏好标签/
      fail-soft(会员不存在降级)
    - 信值指令升级: 绑定后查信值(45号档案)/查余额
      (45号 balance)
    - 能换吗: 上文商品换算(够/不够双态)/无上文回退热销/
      未绑定引导
    - 修复引导: 有违规(计划呈现)/无违规(正向反馈)/
      未绑定引导
    - LLM 意图轨: 默认 off 规则轨兜底/开关断言/回退
    - 偏好重排序: 只调序不筛除(防信息茧房)
    - HTTP 层: bindings 三端点/context 端点/鉴权
"""

import asyncio
import os
import sys

os.environ["LOCK_MODE"] = "asyncio"
os.environ["STORE_MODE"] = "asyncio"
os.environ.pop("LLM_API_KEY", None)
os.environ["LLM_ENABLED"] = "off"
os.environ["XIAOZHU_LLM_MODE"] = "off"

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


async def _new_trust(role: str = "person") -> int:
    from services.trust_scoring_service import (
        TrustProfileService,
    )
    import uuid
    suffix = uuid.uuid4().hex[:10]
    r = await TrustProfileService().create_role(
        role, f"p1-{suffix}", f"110101{suffix}4321")
    return r["trustId"]


async def _session(member_id: int = 1) -> int:
    from services.xiaozhu_service import XiaozhuService
    return (await XiaozhuService().open_session(
        member_id))["sessionId"]


async def _text(sid: int, text: str) -> dict:
    from services.xiaozhu_service import XiaozhuService
    return await XiaozhuService().handle_text(sid, text)


class TestBinding:
    async def run(self):
        print("[01 绑定表]")
        reset_all()
        from services.xiaozhu_service import XiaozhuService
        svc = XiaozhuService()
        tid = await _new_trust()

        r = await svc.bind_trust(1, tid, note="测试绑定")
        record("绑定成功",
               r["success"] is True
               and r["trustId"] == tid
               and r["boundAt"], str(r)[:60])
        # 绑定视图
        r = await svc.get_binding(1)
        record("绑定视图", r["trustId"] == tid)
        # 改绑(零不可逆)
        tid2 = await _new_trust()
        await svc.bind_trust(1, tid2)
        r = await svc.get_binding(1)
        record("重复绑定=改绑", r["trustId"] == tid2)
        # 档案不存在拒绝
        try:
            await svc.bind_trust(1, 99999)
            record("档案不存在拒绝", False, "未抛")
        except KeyError:
            record("档案不存在拒绝", True)
        # 解除
        r = await svc.unbind(1)
        record("解除绑定", r["success"] is True)
        try:
            await svc.get_binding(1)
            record("解除后404", False, "未抛")
        except KeyError:
            record("解除后404", True)
        # 未绑定再解除 → 404
        try:
            await svc.unbind(1)
            record("重复解除404", False, "未抛")
        except KeyError:
            record("重复解除404", True)


class TestBindFlow:
    async def run(self):
        print("[02 会话内绑定流]")
        reset_all()
        tid = await _new_trust()
        sid = await _session(5)
        r = await _text(sid, f"小竹，绑定信值档案 {tid}")
        record("快捷绑定指令",
               "已绑定" in r["reply"]
               and (r["card"] or {}).get("trustId") == tid,
               r["reply"][:50])
        # 绑定后立即查信值(同会话)
        r = await _text(sid, "查我的信值")
        record("绑定后查信值直读",
               "信值分" in r["reply"]
               and (r["card"] or {}).get("type")
               == "trust_score",
               r["reply"][:50])
        # 错误档案号
        sid2 = await _session(6)
        r = await _text(sid2, "小竹，绑定信值档案 99999")
        record("错误档案号引导",
               "绑定失败" in r["reply"], r["reply"][:40])


class TestContext:
    async def run(self):
        print("[03 角色上下文]")
        reset_all()
        from services.xiaozhu_service import XiaozhuService
        svc = XiaozhuService()
        # 未绑定游客(0)
        ctx = await svc.build_context(0)
        record("游客零上下文",
               ctx["bound"] is False and ctx["level"] == 1
               and ctx["preferenceTags"] == [])
        # 未绑定会员
        ctx = await svc.build_context(1)
        record("未绑定会员上下文",
               ctx["bound"] is False
               and isinstance(ctx["level"], int))
        # 绑定后
        tid = await _new_trust()
        await svc.bind_trust(7, tid)
        ctx = await svc.build_context(7)
        record("绑定后上下文",
               ctx["bound"] is True
               and ctx["trustId"] == tid
               and ctx["trustBalance"] is not None,
               str(ctx)[:80])
        # 调试视图
        r = await svc.get_context_view(7)
        record("上下文调试视图",
               r["success"] is True
               and r["llmMode"] is False
               and r["bound"] is True)


class TestTrustCommands:
    async def run(self):
        print("[04 信值指令升级]")
        reset_all()
        from services.xiaozhu_service import XiaozhuService
        svc = XiaozhuService()
        tid = await _new_trust()
        await svc.bind_trust(8, tid)
        sid = await _session(8)

        r = await _text(sid, "小竹，查我的信值")
        record("查信值(绑定后)",
               "信值分" in r["reply"]
               and "等级" in r["reply"],
               r["reply"][:50])
        r = await _text(sid, "小竹，我的信值余额")
        record("查余额(绑定后)",
               "余额" in r["reply"]
               and (r["card"] or {}).get("type")
               == "trust_balance",
               r["reply"][:50])
        # 未绑定会话对照
        sid2 = await _session(9)
        r = await _text(sid2, "小竹，查我的信值")
        record("未绑定引导(对照)",
               "绑定" in r["reply"]
               and (r["card"] or {}).get("guide")
               == "trust-bind")


class TestExchange:
    async def run(self):
        print("[05 能换吗(换算)]")
        reset_all()
        from services.xiaozhu_service import XiaozhuService
        svc = XiaozhuService()
        tid = await _new_trust()
        await svc.bind_trust(10, tid)
        sid = await _session(10)

        # 上文商品(看新品后指代)
        await _text(sid, "小竹，看新品")
        r = await _text(sid, "这个能用信值换吗")
        record("能换吗(上文商品)",
               "TV" in r["reply"]
               and (r["card"] or {}).get("type")
               == "trust_exchange",
               r["reply"][:60])
        card = r["card"] or {}
        record("换算数学(enough 口径一致)",
               card.get("enough")
               == (card.get("balance", 0)
                   >= card.get("price", 0)),
               str(card)[:80])
        # 无上文 → 回退热销
        sid2 = await _session(11)
        await svc.bind_trust(11, tid)
        r = await _text(sid2, "小竹，能用信值换吗")
        record("无上文回退热销",
               "TV" in r["reply"] or "元" in r["reply"],
               r["reply"][:50])
        # 未绑定对照
        sid3 = await _session(12)
        await _text(sid3, "小竹，看新品")
        r = await _text(sid3, "这个能换吗")
        record("未绑定换算引导",
               "绑定" in r["reply"],
               r["reply"][:50])


class TestRepair:
    async def run(self):
        print("[06 修复引导]")
        reset_all()
        from services.xiaozhu_service import XiaozhuService
        svc = XiaozhuService()
        tid = await _new_trust()

        # 无违规 → 正向反馈
        await svc.bind_trust(13, tid)
        sid = await _session(13)
        r = await _text(sid, "小竹，我上次违章怎么修复")
        record("无违规正向反馈",
               "没有待修复" in r["reply"],
               r["reply"][:50])

        # 有违规 → 计划呈现
        from services.trust_scoring_service import (
            TrustProfileService,
        )
        await TrustProfileService().record_event(
            tid, "L1", "legal_record", -10.0)
        sid2 = await _session(14)
        await svc.bind_trust(14, tid)
        r = await _text(sid2, "小竹，违章怎么修复")
        record("有违规计划呈现",
               "待修复" in r["reply"]
               and "β" in r["reply"]
               and (r["card"] or {}).get("type")
               == "repair",
               r["reply"][:60])
        # 未绑定对照
        sid3 = await _session(15)
        r = await _text(sid3, "小竹，怎么修复")
        record("未绑定修复引导",
               "绑定" in r["reply"], r["reply"][:40])


class TestLLMTrack:
    async def run(self):
        print("[07 LLM 意图轨]")
        reset_all()
        from services.xiaozhu_service import (
            XiaozhuService, _llm_mode_enabled,
        )
        record("默认off", _llm_mode_enabled() is False)
        # off 且规则轨未中 → general 兜底(不调 LLM)
        sid = await _session(16)
        r = await _text(sid, "小竹，帮我看看天气")
        record("off规则轨兜底",
               r.get("track") == "rule"
               and "还不会" in r["reply"])
        # 开关 on(未配 key) → 回退规则轨零影响
        os.environ["XIAOZHU_LLM_MODE"] = "on"
        try:
            record("on开关生效",
                   _llm_mode_enabled() is True)
            llm_hit = await XiaozhuService()._llm_match(
                "看看有什么新品")
            record("无key回退None", llm_hit is None)
            r = await _text(sid, "小竹，帮我看看天气")
            record("无key回退general",
                   "还不会" in r["reply"])
        finally:
            os.environ["XIAOZHU_LLM_MODE"] = "off"


class TestPreference:
    async def run(self):
        print("[08 偏好重排序]")
        reset_all()
        from services.product_service import ProductService
        # 看新品基线(无偏好)
        sid = await _session(17)
        r = await _text(sid, "小竹，看新品")
        base_names = [i.get("name") for i in
                      (r["card"] or {}).get("items") or []]
        # 直接验证重排序纯逻辑(订单偏好经 build_context)
        from services.xiaozhu_service import XiaozhuService
        svc = XiaozhuService()
        ctx = {"preferenceTags": ["珍藏"],
               "levelTitle": "竹林会员"}
        out = await svc._exec_product_new(ctx)
        ranked = [i.get("name") for i in
                  (out["card"] or {}).get("items") or []]
        record("重排序只调序不筛除",
               sorted(ranked) == sorted(base_names)
               and len(ranked) == len(base_names),
               f"{len(ranked)} vs {len(base_names)}")
        record("偏好话术注入",
               "珍藏" in out["reply"]
               and "竹林会员" in out["reply"],
               out["reply"][:60])
        # 无偏好零变化
        out2 = await svc._exec_product_new({})
        record("无偏好零变化",
               "排序" not in out2["reply"]
               and "您好——" not in out2["reply"])


class TestHttp:
    async def run(self):
        print("[09 HTTP 层]")
        reset_all()
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from routes.xiaozhu_routes import (
            register_xiaozhu_routes,
        )
        app = FastAPI()
        register_xiaozhu_routes(app)
        client = TestClient(app)
        h = {"X-Member-Id": "77"}

        tid = await _new_trust()
        # POST bindings
        resp = client.post("/api/xiaozhu/bindings",
                           json={"trustId": tid}, headers=h)
        body = resp.json()
        record("POST bindings 200",
               resp.status_code == 200
               and body.get("trustId") == tid,
               str(resp.status_code))
        # GET bindings
        resp = client.get("/api/xiaozhu/bindings", headers=h)
        record("GET bindings 200",
               resp.status_code == 200
               and resp.json().get("trustId") == tid)
        # GET context
        resp = client.get("/api/xiaozhu/context", headers=h)
        body = resp.json()
        record("GET context 200",
               resp.status_code == 200
               and body.get("bound") is True
               and body.get("llmMode") is False,
               str(body)[:60])
        # DELETE bindings
        resp = client.delete("/api/xiaozhu/bindings",
                             headers=h)
        record("DELETE bindings 200",
               resp.status_code == 200
               and resp.json().get("bound") is False)
        # 解除后 404
        resp = client.get("/api/xiaozhu/bindings", headers=h)
        record("解除后 bindings 404",
               resp.status_code == 404, str(resp.status_code))
        # 档案不存在 404
        resp = client.post("/api/xiaozhu/bindings",
                           json={"trustId": 99999}, headers=h)
        record("绑定档案不存在 404",
               resp.status_code == 404, str(resp.status_code))
        # 参数校验
        resp = client.post("/api/xiaozhu/bindings",
                           json={}, headers=h)
        record("缺 trustId 409",
               resp.status_code == 409)
        # 鉴权
        resp = client.post("/api/xiaozhu/bindings",
                          json={"trustId": 1})
        record("缺 Member-Id 401",
               resp.status_code == 401)


async def run_all():
    await TestBinding().run()
    await TestBindFlow().run()
    await TestContext().run()
    await TestTrustCommands().run()
    await TestExchange().run()
    await TestRepair().run()
    await TestLLMTrack().run()
    await TestPreference().run()
    await TestHttp().run()
    os.environ["XIAOZHU_LLM_MODE"] = "off"


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
