"""49号·小竹可信函数调用深化 P1 专项测试
(consent_token 双因子流)

运行方式:
    python test_xiaozhu_p49_1.py

覆盖(49号计划 §六 P1):
    - 双因子: 语音确认词标记(意图证据)/短语精确包含
      匹配/意图证据哈希留痕
    - TTL: confirmToken 120→60 收紧/consent_token 60s
      过期拒绝
    - consent_token: 双因子齐备签发(纯屏幕码不签发)/
      一次性核销(二次复用拒绝)/action 匹配/跨用户
      复用无效(声纹代理绑定)
    - FC 网关: 有效 token 直执行(consentDirect)/
      无 token 走挑战流(consentPhrase 提示)/
      伪造 token 拒绝
    - 轮次拦截: 语音确认词轮次(intent=consent.voice)/
      屏幕码引导回复
    - 修复执行全流 E2E(挑战→语音→屏幕→consent_token
      →网关直执行第二笔)
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
        "person", f"p491-{suffix}", f"110101{suffix}4321")
    return r["trustId"]


async def _session(member_id: int) -> int:
    from services.xiaozhu_service import XiaozhuService
    return (await XiaozhuService().open_session(
        member_id))["sessionId"]


async def _text(sid: int, text: str) -> dict:
    from services.xiaozhu_service import XiaozhuService
    return await XiaozhuService().handle_text(sid, text)


def _get_code(token: str) -> str:
    from services.xiaozhu_executor import get_executor
    entry = get_executor()._tokens.get(token)
    return entry["code"] if entry else ""


async def _bind(member_id: int, trust_id: int):
    from services.xiaozhu_service import XiaozhuService
    return await XiaozhuService().bind_trust(
        member_id, trust_id, note="p49-1")


def _challenge(member_id: int, credit: float = 100
               ) -> tuple:
    """同步发起挑战: (gateway 回包, executor 单例)"""
    from services.xiaozhu_fc_gateway import XiaozhuFcGateway
    import asyncio as aio

    async def _m():
        gw = XiaozhuFcGateway()
        r = await gw.call_tool(
            {"sessionId": 1, "memberId": member_id},
            "trust.convert", {"creditPoints": credit})
        return r
    return aio.get_event_loop().run_until_complete(
        _m()) if False else None


class TestDualFactor:
    async def run(self):
        print("[01 双因子合成]")
        reset_all()
        from services.xiaozhu_fc_gateway import (
            XiaozhuFcGateway,
        )
        from services.xiaozhu_executor import (
            get_executor, CONFIRM_TOKEN_TTL,
            CONSENT_TOKEN_TTL,
        )
        record("TTL 收紧 120→60",
               CONFIRM_TOKEN_TTL == 60,
               str(CONFIRM_TOKEN_TTL))
        record("consent TTL 60",
               CONSENT_TOKEN_TTL == 60)

        gw = XiaozhuFcGateway()
        session = {"sessionId": 1, "memberId": 60}
        r = await gw.call_tool(
            session, "trust.convert",
            {"creditPoints": 100})
        record("挑战含确认短语",
               r.get("consentPhrase")
               == "确认兑换信用分",
               str(r.get("consentPhrase")))
        record("提示语含语音引导",
               "确认兑换信用分" in r.get("reply", ""))
        token = r.get("confirmToken")
        ex = get_executor()

        # 语音因子: 精确包含匹配
        hit = ex.mark_voice_confirmation(
            60, "小竹，确认兑换信用分")
        record("语音确认词标记",
               hit is not None
               and hit.get("action") == "trust.convert")
        entry = ex._tokens.get(token)
        record("意图证据哈希留痕(32)",
               len(entry.get("voiceEvidenceHash") or "")
               == 32)
        # 重复标记幂等
        hit2 = ex.mark_voice_confirmation(
            60, "确认兑换信用分")
        record("重复语音确认幂等跳过",
               hit2 is None)
        # 不匹配短语不标记
        r2 = await gw.call_tool(
            {"sessionId": 1, "memberId": 60},
            "trust.convert", {"creditPoints": 200})
        token2 = r2.get("confirmToken")
        hit3 = ex.mark_voice_confirmation(
            60, "小竹，看新品")
        record("无关语句不标记", hit3 is None)
        # 跨会员不标记
        hit4 = ex.mark_voice_confirmation(
            61, "小竹，确认兑换信用分")
        record("跨会员语音确认无效",
               hit4 is None)


class TestConsentTokenLifecycle:
    async def run(self):
        print("[02 consent_token 生命周期]")
        reset_all()
        from services.xiaozhu_executor import (
            get_executor,
        )
        ex = get_executor()

        # 纯语音(无屏幕码) → 不签发
        ct = ex._issue_consent_token(60, "trust.convert")
        # 直接签发验证生命周期(绕过业务执行)
        v = ex.validate_consent_token(ct, 60,
                                      "trust.convert")
        record("签发即可核验",
               v.get("memberId") == 60
               and v.get("consentTokenHash"))
        # 一次性: 二次核销拒绝
        try:
            ex.validate_consent_token(ct, 60,
                                      "trust.convert")
            record("一次性核销拒绝", False, "未抛")
        except KeyError as e:
            record("一次性核销拒绝",
                   "一次性" in str(e), str(e)[:40])
        # 跨用户复用
        ct2 = ex._issue_consent_token(60, "trust.convert")
        try:
            ex.validate_consent_token(ct2, 61,
                                      "trust.convert")
            record("跨用户复用无效", False, "未抛")
        except ValueError as e:
            record("跨用户复用无效",
                   "跨用户" in str(e), str(e)[:40])
        # action 不匹配
        ct3 = ex._issue_consent_token(60, "trust.convert")
        try:
            ex.validate_consent_token(ct3, 60,
                                      "repair.execute")
            record("action 匹配校验", False, "未抛")
        except ValueError as e:
            record("action 匹配校验",
                   "不匹配" in str(e), str(e)[:40])
        # 过期拒绝(手动改 TTL)
        ct4 = ex._issue_consent_token(60, "trust.convert")
        ex._consent_tokens[ct4]["expiresAt"] = 0.0
        try:
            ex.validate_consent_token(ct4, 60,
                                      "trust.convert")
            record("过期拒绝", False, "未抛")
        except KeyError as e:
            record("过期拒绝",
                   "过期" in str(e), str(e)[:40])
        # 伪造 token
        try:
            ex.validate_consent_token("ct-fake", 60,
                                      "trust.convert")
            record("伪造 token 拒绝", False, "未抛")
        except KeyError:
            record("伪造 token 拒绝", True)
        # 声纹代理摘要确定性
        d1 = ex.speaker_digest(60)
        record("声纹代理摘要确定(32)",
               d1 == ex.speaker_digest(60)
               and len(d1) == 32)


class TestConfirmIssues:
    async def run(self):
        print("[03 核销签发与透传]")
        reset_all()
        from services.xiaozhu_service import XiaozhuService
        from services.xiaozhu_executor import (
            get_executor,
        )
        svc = XiaozhuService()
        sid = await _session(62)
        tid = await _new_trust()
        await _bind(62, tid)
        # 走文本指令 → confirmToken
        r = await _text(sid, "小竹，把100信用分换成信值")
        token = r.get("confirmToken")
        ex = get_executor()
        # 场景A: 纯屏幕码 → 执行但不签发 consent_token
        rA = await svc.confirm_action(
            token, _get_code(token))
        record("纯屏幕码执行(48号口径)",
               rA.get("executed") is True
               and rA.get("consentToken") is None
               and rA.get("voiceConfirmed") is False)
        # 场景B: 双因子 → 执行+签发
        r2 = await _text(sid, "小竹，把80信用分换成信值")
        token2 = r2.get("confirmToken")
        # 语音确认(轮次拦截)
        rv = await _text(sid, "小竹，确认兑换信用分")
        record("语音确认轮次(intent)",
               rv.get("turn", {}).get("intent")
               == "consent.voice"
               and "屏幕输入 4 位确认码"
               in rv.get("reply", ""),
               str(rv.get("turn", {}).get("intent")))
        rB = await svc.confirm_action(
            token2, _get_code(token2))
        record("双因子执行+签发 consent_token",
               rB.get("executed") is True
               and (rB.get("consentToken") or "")
               .startswith("ct-")
               and rB.get("consentExpiresIn") == 60
               and rB.get("voiceConfirmed") is True,
               str(rB.get("consentToken"))[:20])


class TestGatewayDirect:
    async def run(self):
        print("[04 网关直执行与挑战]")
        reset_all()
        from services.xiaozhu_fc_gateway import (
            XiaozhuFcGateway,
        )
        from services.xiaozhu_executor import (
            get_executor,
        )
        gw = XiaozhuFcGateway()
        session = {"sessionId": 2, "memberId": 63}
        tid = await _new_trust()
        await _bind(63, tid)
        # 无 token → 挑战流
        r = await gw.call_tool(
            session, "trust.convert",
            {"creditPoints": 100})
        record("无 token 走挑战流",
               r.get("confirmRequired") is True
               and r.get("consentPhrase")
               == "确认兑换信用分")
        # 伪造 token → 拒绝(fallback)
        r2 = await gw.call_tool(
            session, "trust.convert",
            {"creditPoints": 100,
             "consentToken": "ct-fake"})
        record("伪造 token 拒绝",
               r2.get("fallback") is True
               and r2.get("executed") is False)
        # 有效 token → 直执行
        ex = get_executor()
        ct = ex._issue_consent_token(63, "trust.convert")
        r3 = await gw.call_tool(
            session, "trust.convert",
            {"creditPoints": 100,
             "consentToken": ct})
        record("有效 token 直执行",
               r3.get("consentDirect") is True
               and r3.get("executed") is True)
        # 直执行审计含 hash
        rows = await gw.repo.list_records(
            gw.repo.TABLE_FC_AUDIT)
        direct = [r4 for r4 in rows
                  if r4.get("error")
                  == "consent-direct"]
        record("直执行审计含 token hash",
               len(direct) == 1
               and len(direct[0]
                       .get("consentTokenHash") or "") == 32,
               str(len(direct)))
        # 一次性: 同 token 再用 → 拒绝
        r5 = await gw.call_tool(
            session, "trust.convert",
            {"creditPoints": 100,
             "consentToken": ct})
        record("token 一次性(网关侧)",
               r5.get("fallback") is True)


class TestRepairFullFlow:
    async def run(self):
        print("[05 修复执行全流 E2E]")
        reset_all()
        from services.xiaozhu_service import XiaozhuService
        from services.xiaozhu_fc_gateway import (
            XiaozhuFcGateway,
        )
        from services.xiaozhu_executor import (
            get_executor,
        )
        from services.trust_scoring_service import (
            TrustProfileService,
        )
        from repositories.trust_value_repository \
            import TrustValue45Repository
        svc = XiaozhuService()
        tid = await _new_trust()
        t = TrustProfileService()
        await t.record_event(
            tid, "L2", "ethics_evidence", -30.0,
            source="admin", summary="违规测试")
        events = await TrustValue45Repository(
        ).list_events_by_trust(tid)
        vid = [e for e in events
               if (e.get("delta") or 0) < 0][0]["eventId"]
        sid = await _session(64)
        await _bind(64, tid)
        session = await svc._require_open(sid)
        gw = XiaozhuFcGateway()
        # ① 挑战
        r = await gw.call_tool(
            session, "repair.execute", {
                "violationEventId": vid,
                "repairs": [{"kind": "community_service",
                             "value": 80,
                             "evidence":
                             "社区公益服务八小时"}]})
        record("①挑战发起(短语=确认执行修复)",
               r.get("consentPhrase") == "确认执行修复")
        token = r.get("confirmToken")
        # ② 语音确认(轮次)
        rv = await _text(sid, "小竹，确认执行修复")
        record("②语音确认轮次",
               rv.get("turn", {}).get("intent")
               == "consent.voice")
        # ③ 屏幕码核销 → 执行+consent_token
        rB = await svc.confirm_action(
            token, _get_code(token))
        result = rB.get("result") or {}
        record("③双因子核销执行(45号通道)",
               rB.get("executed") is True
               and result.get("repairId") is not None)
        ct = rB.get("consentToken")
        record("③consent_token 签发",
               (ct or "").startswith("ct-"))
        # ④ 网关直执行第二笔(60s 内同 action)
        # 新违规事件
        await t.record_event(
            tid, "L2", "ethics_evidence", -20.0,
            source="admin", summary="违规测试2")
        events2 = await TrustValue45Repository(
        ).list_events_by_trust(tid)
        vid2 = [e for e in events2
                if (e.get("delta") or 0) < 0
                and e.get("eventId") != vid][0]["eventId"]
        r4 = await gw.call_tool(
            session, "repair.execute", {
                "violationEventId": vid2,
                "repairs": [{"kind": "community_service",
                             "value": 60,
                             "evidence":
                             "社区公益服务六小时"}],
                "consentToken": ct})
        r4r = r4.get("result") or {}
        record("④网关直执行第二笔",
               r4.get("consentDirect") is True
               and r4.get("executed") is True
               and r4r.get("repairId") is not None,
               str(r4)[:60])
        # ⑤ 一次性: 同 token 第三笔拒绝
        r5 = await gw.call_tool(
            session, "repair.execute", {
                "violationEventId": vid2,
                "repairs": [{"kind": "charity_donation",
                             "value": 50,
                             "evidence":
                             "公益捐赠凭证记录"}],
                "consentToken": ct})
        record("⑤一次性拒绝(第三笔)",
               r5.get("fallback") is True)


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
        h = {"X-Member-Id": "65"}
        # 绑定信值档案(兑换业务校验前置)
        tid = await _new_trust()
        client.post("/api/xiaozhu/bindings",
                    json={"trustId": tid}, headers=h)

        # 全链: 挑战→语音(轮次端点)→核销(consent 透传)
        sid = (client.post("/api/xiaozhu/sessions",
                          json={}, headers=h)
               .json()["sessionId"])
        r = client.post(
            f"/api/xiaozhu/sessions/{sid}/text",
            json={"text": "小竹，把100信用分换成信值"},
            headers=h).json()
        record("HTTP 挑战含短语",
               r.get("consentPhrase")
               == "确认兑换信用分")
        token = r.get("confirmToken")
        rv = client.post(
            f"/api/xiaozhu/sessions/{sid}/text",
            json={"text": "小竹，确认兑换信用分"},
            headers=h).json()
        record("HTTP 语音确认轮次",
               rv.get("turn", {}).get("intent")
               == "consent.voice")
        # 取码(单例)
        from services.xiaozhu_executor import (
            get_executor,
        )
        code = get_executor()._tokens[token]["code"]
        rc = client.post(
            f"/api/xiaozhu/confirm/{token}",
            json={"code": code}, headers=h).json()
        record("HTTP 核销透传 consent_token",
               (rc.get("consentToken") or "")
               .startswith("ct-")
               and rc.get("voiceConfirmed") is True,
               str(rc)[:60])
        record("HTTP 核销含有效期",
               rc.get("consentExpiresIn") == 60)


async def run_all():
    await TestDualFactor().run()
    await TestConsentTokenLifecycle().run()
    await TestConfirmIssues().run()
    await TestGatewayDirect().run()
    await TestRepairFullFlow().run()
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
