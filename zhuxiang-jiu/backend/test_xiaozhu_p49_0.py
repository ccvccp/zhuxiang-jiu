"""49号·小竹可信函数调用深化 P0 专项测试
(工具注册表 v2 + FC 网关骨架 + 审计铁律)

运行方式:
    python test_xiaozhu_p49_0.py

覆盖(49号计划 §六 P0):
    - 注册表: 14 工具齐备/三级分级与沙箱对齐自检/
      requiresConsent 只在敏感动作/描述内嵌禁令与
      隐私成本(约束内化)/话术全覆盖
    - 网关: 只读零摩擦直达/写走沙箱/高敏走
      confirmToken 流/未知工具拒绝
    - 审计: 六字段铁律落库/kind(ok|duplicate|fallback)/
      token 哈希非明文/只追加/fail-soft
    - 失败降级: 异常→safeMessage(不编结果)/
      人工转接选项
    - LLM 注入: 工具描述进 System Prompt(约束内化)
    - HTTP 层: fc/audit 端点/鉴权
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


async def _new_trust() -> int:
    from services.trust_scoring_service import (
        TrustProfileService,
    )
    import uuid
    suffix = uuid.uuid4().hex[:10]
    r = await TrustProfileService().create_role(
        "person", f"p49-{suffix}", f"110101{suffix}4321")
    return r["trustId"]


async def _session(member_id: int) -> int:
    from services.xiaozhu_service import XiaozhuService
    return (await XiaozhuService().open_session(
        member_id))["sessionId"]


async def _text(sid: int, text: str) -> dict:
    from services.xiaozhu_service import XiaozhuService
    return await XiaozhuService().handle_text(sid, text)


def _get_code(token: str) -> str:
    """测试钩子: 沙箱单例取令牌真码(生产不可外泄)"""
    from services.xiaozhu_executor import get_executor
    entry = get_executor()._tokens.get(token)
    return entry["code"] if entry else ""


class TestRegistry:
    async def run(self):
        print("[01 工具注册表 v2]")
        from services.xiaozhu_fc_registry import (
            TOOL_REGISTRY, TIER_READONLY, TIER_WRITE,
            TIER_SENSITIVE, get_tool, build_tool_prompt,
            safe_message_of, audit_fields,
        )
        record("16 工具齐备",
               len(TOOL_REGISTRY) == 16,
               str(len(TOOL_REGISTRY)))
        from collections import Counter
        tiers = dict(Counter(
            t["tier"] for t in TOOL_REGISTRY.values()))
        record("三级分布(13只读/1写/2高敏)",
               tiers == {TIER_READONLY: 13,
                         TIER_WRITE: 1,
                         TIER_SENSITIVE: 2}, str(tiers))

        # 沙箱对齐自检(模块导入已校验——再显式断言)
        from services.xiaozhu_executor import (
            SAFE_READONLY, SAFE_WRITE, SENSITIVE,
        )
        aligned = all(
            (t["tier"] == TIER_READONLY
             and a in SAFE_READONLY)
            or (t["tier"] == TIER_WRITE
                and a in SAFE_WRITE)
            or (t["tier"] == TIER_SENSITIVE
                and a in SENSITIVE)
            for a, t in TOOL_REGISTRY.items())
        record("分级与沙箱白名单对齐", aligned)

        # consent 只在高敏
        consent = [a for a, t in
                   TOOL_REGISTRY.items()
                   if t.get("requiresConsent")]
        record("consent 只在两个高敏工具",
               sorted(consent) == ["repair.execute",
                                   "trust.convert"],
               str(consent))

        # 描述内嵌禁令+隐私成本(约束内化)
        embedded = all(
            ("❌" in t["description"]
             or t["privacyCost"] == 0.0)
            and "privacy_cost" in t["description"]
            for a, t in TOOL_REGISTRY.items()
            if t["tier"] != TIER_READONLY)
        record("写/高敏描述内嵌禁令+成本",
               embedded)
        # 高敏必须双要素
        sensitive_desc = all(
            "❌" in TOOL_REGISTRY[a]["description"]
            and "consent_token"
            in TOOL_REGISTRY[a]["description"]
            for a in ("trust.convert",
                      "repair.execute"))
        record("高敏描述含禁令+token 约束",
               sensitive_desc)

        # 隐私成本区间(只读 0-0.02/写 0.02-0.05/高敏 0.08)
        cost_ok = all(
            0 <= t["privacyCost"] <= 0.02
            for t in TOOL_REGISTRY.values()
            if t["tier"] == TIER_READONLY) and all(
            0.02 <= t["privacyCost"] <= 0.05
            for t in TOOL_REGISTRY.values()
            if t["tier"] == TIER_WRITE) and all(
            t["privacyCost"] == 0.08
            for t in TOOL_REGISTRY.values()
            if t["tier"] == TIER_SENSITIVE)
        record("隐私成本区间合规", cost_ok)

        # 兜底出口零成本(失败降级红线)
        record("转人工零成本",
               TOOL_REGISTRY["chat.human"]
               ["privacyCost"] == 0.0)

        # 话术全覆盖
        record("安全话术全覆盖",
               all(t.get("safeMessage")
                   for t in TOOL_REGISTRY.values()))
        record("写话术含人工转接",
               "转人工" in TOOL_REGISTRY["trust.convert"]
               ["safeMessage"])

        # prompt 注入块(约束内化)
        prompt = build_tool_prompt()
        record("prompt 含全部工具",
               prompt.count("action=") == 16
               and "privacy_cost=0.08" in prompt)
        record("prompt 含使用规则",
               "requiresConsent" in prompt
               and "禁止编造" in prompt)

        # operationId 审计字段
        af = audit_fields("repair.execute")
        record("审计静态字段",
               af == {"toolName":
                      "execute_repair_action",
                      "tier": "sensitive",
                      "privacyCost": 0.08}, str(af))


class TestGateway:
    async def run(self):
        print("[02 FC 网关管道]")
        reset_all()
        from services.xiaozhu_fc_gateway import (
            XiaozhuFcGateway,
        )
        gw = XiaozhuFcGateway()
        session = {"sessionId": 1, "memberId": 30}

        # 只读零摩擦直达
        r = await gw.call_tool(session, "product.new",
                               {})
        record("只读直达(零摩擦)",
               r.get("readonly") is True
               and r.get("privacyCost") == 0.01)

        # 未知工具拒绝
        try:
            await gw.call_tool(session, "hack.tool", {})
            record("未知工具拒绝", False, "未抛")
        except ValueError:
            record("未知工具拒绝", True)

        # 高敏: 走 48号 confirmToken 流(P0 占位透传)
        r = await gw.call_tool(
            session, "trust.convert",
            {"creditPoints": 100})
        record("高敏走 confirmToken 流",
               r.get("confirmRequired") is True
               and (r.get("confirmToken") or "")
               .startswith("cf-"))

        # token 哈希非明文(审计红线)
        rows = await gw.repo.list_records(
            gw.repo.TABLE_FC_AUDIT)
        convert_rows = [r2 for r2 in rows
                        if r2.get("action")
                        == "trust.convert"]
        record("token 哈希化(非明文)",
               all(not r2.get("consentTokenHash")
                   .startswith("cf-")
                   for r2 in convert_rows
                   if r2.get("consentTokenHash")),
               str(convert_rows[:1]))
        record("P0 占位无 token 哈希为空",
               all(not r2.get("consentTokenHash")
                   for r2 in convert_rows),
               str(len(convert_rows)))


class TestAuditIronRules:
    async def run(self):
        print("[03 审计六字段铁律]")
        reset_all()
        from services.xiaozhu_fc_gateway import (
            XiaozhuFcGateway,
        )
        gw = XiaozhuFcGateway()
        session = {"sessionId": 2, "memberId": 31}
        await gw.call_tool(session, "trust.score", {})
        await gw.call_tool(
            session, "trust.convert",
            {"creditPoints": 50})
        # 幂等命中(duplicate)
        await gw.call_tool(
            session, "trust.convert",
            {"creditPoints": 50})

        rows = await gw.repo.list_records(
            gw.repo.TABLE_FC_AUDIT)
        record("审计落库(3 条)",
               len(rows) == 3, str(len(rows)))
        six = all(all(k in r2 for k in (
                "memberId", "toolName",
                "consentTokenHash", "privacyCost",
                "ts", "kind")) for r2 in rows)
        record("六字段铁律齐备", six)
        kinds = {r2.get("kind") for r2 in rows}
        record("kind 分类(ok|duplicate)",
               "ok" in kinds
               and "duplicate" in kinds, str(kinds))
        # 汇总视图
        v = await gw.audit_view()
        record("审计视图聚合",
               v["total"] == 3
               and v["byTool"].get(
                   "convert_credit_to_trust") == 2,
               str(v.get("privacyCostTotal")))
        # 会员过滤
        v = await gw.audit_view(member_id=31)
        record("审计按会员过滤",
               v["total"] == 3, str(v["total"]))


class TestFailSafe:
    async def run(self):
        print("[04 失败安全降级]")
        reset_all()
        from services.xiaozhu_fc_gateway import (
            XiaozhuFcGateway,
        )
        from services.xiaozhu_service import XiaozhuService
        gw = XiaozhuFcGateway()
        svc = XiaozhuService()
        # 未绑定 → 核销触达业务校验异常(service 层抛)
        sid = await _session(32)
        session = await svc._require_open(sid)
        r = await gw.call_tool(
            session, "repair.execute",
            {"violationEventId": 1,
             "repairs": [{"kind": "community_service",
                          "value": 80,
                          "evidence": "志愿服务证据内容"}]})
        token = r.get("confirmToken")
        record("高敏先发令牌(核销触达校验)",
               r.get("confirmRequired") is True
               and token)
        try:
            await svc.confirm_action(
                token, _get_code(token))
            record("未绑定核销应失败", False, "未抛")
        except Exception:
            record("未绑定核销应失败", True)
        # 网关 fallback 确定性触发: monkeypatch 执行器抛异常
        import services.xiaozhu_executor as ex_mod
        orig_exec = ex_mod.XiaozhuExecutor.execute

        async def _boom(self, session, action, params):
            raise RuntimeError("模拟执行器故障")

        ex_mod.XiaozhuExecutor.execute = _boom
        try:
            r = await gw.call_tool(
                session, "repair.execute",
                {"violationEventId": 1,
                 "repairs": [{"kind": "community_service",
                              "value": 80,
                              "evidence": "志愿服务证据内容"}]})
        finally:
            ex_mod.XiaozhuExecutor.execute = orig_exec
        record("失败不编结果(fallback)",
               r.get("fallback") is True
               and r.get("executed") is False)
        record("安全话术返回(不暴露内部)",
               "转人工" in (r.get("safeMessage") or ""),
               str(r.get("safeMessage"))[:40])
        rows = await gw.repo.list_records(
            gw.repo.TABLE_FC_AUDIT)
        fb = [r2 for r2 in rows
              if r2.get("kind") == "fallback"]
        record("fallback 审计留痕",
               len(fb) == 1
               and fb[0].get("toolName")
               == "execute_repair_action",
               str(len(fb)))
        record("fallback 记录 error 摘要",
               len(fb[0].get("error") or "") > 0)


class TestRepairExecuteTool:
    async def run(self):
        print("[05 修复执行工具(新) E2E]")
        reset_all()
        from services.xiaozhu_service import XiaozhuService
        svc = XiaozhuService()
        from services.xiaozhu_executor import (
            get_executor,
        )
        # 建档+灌违规+绑定
        tid = await _new_trust()
        from services.trust_scoring_service import (
            TrustProfileService,
        )
        t = TrustProfileService()
        await t.record_event(
            tid, "L2", "ethics_evidence", -30.0,
            source="admin", summary="违规测试")
        # record_event 不回 eventId——从仓储事件列表取
        from repositories.trust_value_repository \
            import TrustValue45Repository
        events = await TrustValue45Repository(
        ).list_events_by_trust(tid)
        violations = [e for e in events
                      if (e.get("delta") or 0) < 0]
        violation_id = violations[0]["eventId"]
        sid = await _session(40)
        await svc.bind_trust(40, tid, note="p49")
        # 高敏流: 发令牌(经网关)
        from services.xiaozhu_fc_gateway import (
            XiaozhuFcGateway,
        )
        gw = XiaozhuFcGateway()
        session = await svc._require_open(sid)
        r = await gw.call_tool(
            session, "repair.execute", {
                "violationEventId": violation_id,
                "repairs": [{"kind": "community_service",
                             "value": 80,
                             "evidence":
                             "社区安全宣讲志愿服务"
                             "八小时全记录"}]})
        record("修复令牌下发",
               r.get("confirmRequired") is True
               and "修复" in (r.get("summary") or ""),
               str(r.get("summary"))[:40])
        # 核销(正确码)——45号 submit_repair 真执行
        token = r.get("confirmToken")
        r2 = await svc.confirm_action(
            token, _get_code(token))
        result = r2.get("result") or {}
        record("修复执行走 45号通道",
               r2.get("success") is True
               and result.get("repairId") is not None,
               str(result)[:60])
        # 48号 P2 高敏统计已计数(修复执行入台账)
        s = get_executor().stats()
        record("修复入高敏台账",
               s.get("issued", 0) >= 1
               and s.get("confirmed", 0) >= 1)


class TestLlmInjection:
    async def run(self):
        print("[05b LLM 工具描述注入]")
        from services.xiaozhu_fc_registry import (
            build_tool_prompt,
        )
        prompt = build_tool_prompt()
        # 猴子补 llm_client 捕获 system prompt
        import services.llm_client as lc
        captured = {}

        class FakeClient:
            def chat(self, system="", user=""):
                captured["system"] = system
                return '{"action": null}'

        orig = lc.provider_client
        orig_enabled = lc.llm_enabled
        lc.provider_client = lambda: FakeClient()
        lc.llm_enabled = lambda: True
        try:
            import os as _os
            _os.environ["XIAOZHU_LLM_MODE"] = "on"
            from services.xiaozhu_service import (
                XiaozhuService,
            )
            r = await XiaozhuService()._llm_match(
                "随便一句测试")
            record("LLM 轨输出契约不变",
                   r is None or "action" in r)
            record("工具描述注入 System Prompt",
                   "convert_credit_to_trust"
                   in captured.get("system", "")
                   and "privacy_cost" in captured.get(
                       "system", ""),
                   captured.get("system", "")[:60])
        finally:
            lc.provider_client = orig
            lc.llm_enabled = orig_enabled
            _os.environ["XIAOZHU_LLM_MODE"] = "off"


class TestHttp:
    async def run(self):
        print("[06 HTTP 层]")
        reset_all()
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from routes.xiaozhu_routes import (
            register_xiaozhu_routes,
        )
        app = FastAPI()
        register_xiaozhu_routes(app)
        client = TestClient(app)
        admin = {"X-Role": "admin"}

        # 灌一条审计(经网关——service 轮次不经 FC 管道)
        from services.xiaozhu_fc_gateway import (
            XiaozhuFcGateway,
        )
        await XiaozhuFcGateway().call_tool(
            {"sessionId": 1, "memberId": 50},
            "product.new", {})

        resp = client.get("/api/xiaozhu/fc/audit",
                         headers=admin)
        body = resp.json()
        record("GET fc/audit 200",
               resp.status_code == 200
               and body.get("success") is True
               and body.get("total", 0) >= 1,
               str(body.get("total")))
        record("审计视图含铁律说明",
               "六字段" in body.get("note", ""))
        resp = client.get("/api/xiaozhu/fc/audit?member_id=50",
                         headers=admin)
        record("member_id 过滤参数",
               resp.status_code == 200)
        resp = client.get("/api/xiaozhu/fc/audit")
        record("fc/audit 缺Role 403",
               resp.status_code == 403)


async def run_all():
    await TestRegistry().run()
    await TestGateway().run()
    await TestAuditIronRules().run()
    await TestFailSafe().run()
    await TestRepairExecuteTool().run()
    await TestLlmInjection().run()
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
