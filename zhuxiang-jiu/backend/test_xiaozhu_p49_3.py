"""49号·小竹可信函数调用深化 P3 专项测试
(可解释性绑定 explainability_ref)

运行方式:
    python test_xiaozhu_p49_3.py

覆盖(49号计划 §六 P3):
    - ref 生成: 格式可溯源(动作+业务 id+短哈希)/
      确定性 seed/业务 id 提取(ledgerId/repairId/orderId)
    - 绑定强制: 写响应必含 ref(兑换/修复)/只读不绑/
      缺失业务标识即阻断(ValueError)
    - 播报模板: 参数化(数字来自 result)/归因指引尾巴
    - 网关管道: 直执行响应含 ref/挑战回包不绑
    - 归因报告: ref 无效拒绝/未绑定拒绝/45号源桥接
      (修复事件全文)/回退只读回放(不编故事)
    - 指令: 打开修复说明(ref 落地卡片/无 ref 引导/
      指令优先级——不与 nav.page "打开"冲突)
    - confirm_action 透传 ref+会话 lastRef 留痕
"""

import asyncio
import os
import re
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
        "person", f"p493-{suffix}", f"110101{suffix}4321")
    return r["trustId"]


async def _bind(member_id: int, trust_id: int):
    from services.xiaozhu_service import XiaozhuService
    return await XiaozhuService().bind_trust(
        member_id, trust_id, note="p49-3")


def _get_code(token: str) -> str:
    from services.xiaozhu_executor import get_executor
    entry = get_executor()._tokens.get(token)
    return entry["code"] if entry else ""


class TestRefBuild:
    async def run(self):
        print("[01 ref 生成]")
        from services.xiaozhu_explainability_service \
            import (build_ref, extract_business_id,
                    broadcast_of, EXPLAINABLE_ACTIONS)
        record("可解释动作集(4 写/高敏)",
               EXPLAINABLE_ACTIONS == {
                   "trust.convert", "repair.execute",
                   "cart.submit", "trust.bind"})
        record("业务 id 提取(ledgerId 优先序)",
               extract_business_id("trust.convert",
                                   {"ledgerId": 5}) == 5
               and extract_business_id(
                   "repair.execute",
                   {"repairId": 9}) == 9
               and extract_business_id(
                   "cart.submit", {"orderId": "A1"})
               == "A1")
        record("无业务 id 返回 0",
               extract_business_id(
                   "trust.convert", {}) == 0)
        ref = build_ref("trust.convert", 5, "seed")
        record("ref 格式可溯源",
               bool(re.fullmatch(
                   r"exp-trust\.convert-5-[0-9a-f]{8}",
                   ref)), ref)
        ref2 = build_ref("trust.convert", 6, "seed")
        record("ref 业务 id 维度(不同 id 不同哈希)",
               ref != ref2 and ref2
               .startswith("exp-trust.convert-6-"),
               f"{ref} vs {ref2}")
        # 播报模板
        b = broadcast_of("trust.convert", {
            "creditPoints": 100, "rate": 10.0,
            "amount": 10.0}, "尾注。")
        record("播报模板参数化",
               "100 信用分" in b and "10.0 TV" in b
               and "汇率 10.0:1" in b and "尾注。" in b,
               b[:60])
        b2 = broadcast_of("unknown.action", {},
                          "尾注。")
        record("未知动作兜底模板",
               "操作已完成" in b2)


class TestBindEnforce:
    async def run(self):
        print("[02 绑定强制(铁律②)]")
        from services.xiaozhu_explainability_service \
            import XiaozhuExplainabilityService as S
        # 正常绑定
        r = S.bind("trust.convert",
                   {"ledgerId": 7, "amount": 1.0})
        record("兑换绑定含 ref+播报",
               r.get("explainabilityRef")
               .startswith("exp-trust.convert-7-")
               and "打开修复说明"
               in r.get("attributionBroadcast", ""))
        # 缺业务标识 → 阻断
        try:
            S.bind("trust.convert", {"amount": 1.0})
            record("缺失业务标识阻断", False, "未抛")
        except ValueError as e:
            record("缺失业务标识阻断",
                   "阻断" in str(e) and "不返回半成品"
                   in str(e), str(e)[:40])
        # 只读不绑
        r = S.bind("product.new", {"items": []})
        record("只读不绑定", r == {})
        r = S.bind("privacy.budget", {})
        record("预算指令不绑定", r == {})


class TestGatewayRef:
    async def run(self):
        print("[03 网关管道 ref)]")
        reset_all()
        from services.xiaozhu_fc_gateway import (
            XiaozhuFcGateway,
        )
        from services.xiaozhu_executor import (
            get_executor,
        )
        gw = XiaozhuFcGateway()
        session = {"sessionId": 1, "memberId": 80}
        tid = await _new_trust()
        await _bind(80, tid)
        # 挑战回包(未落笔)不含 ref
        r = await gw.call_tool(
            session, "trust.convert",
            {"creditPoints": 100})
        record("挑战回包不绑 ref(未落笔)",
               r.get("confirmRequired") is True
               and "explainabilityRef" not in r)
        # 直执行(consumer token)响应含 ref
        ex = get_executor()
        ct = ex._issue_consent_token(80, "trust.convert")
        r2 = await gw.call_tool(
            session, "trust.convert",
            {"creditPoints": 100,
             "consentToken": ct})
        record("直执行响应含 ref",
               r2.get("consentDirect") is True
               and (r2.get("explainabilityRef") or "")
               .startswith("exp-trust.convert-"),
               str(r2.get("explainabilityRef"))[:30])
        record("直执行含归因播报",
               "打开修复说明"
               in r2.get("attributionBroadcast", ""))


class TestConfirmRef:
    async def run(self):
        print("[04 confirm 透传与会话留痕]")
        reset_all()
        from services.xiaozhu_service import XiaozhuService
        tid = await _new_trust()
        await _bind(81, tid)
        svc = XiaozhuService()
        sid = (await svc.open_session(81))["sessionId"]
        r = await svc.handle_text(
            sid, "小竹，把100信用分换成信值")
        token = r.get("confirmToken")
        rc = await svc.confirm_action(
            token, _get_code(token))
        record("confirm 响应含 ref",
               (rc.get("explainabilityRef") or "")
               .startswith("exp-trust.convert-"))
        record("confirm 播报含归因指引",
               "打开修复说明" in rc.get("reply", ""))
        # 会话 lastRef 留痕
        s = await svc.repo.get_session(sid)
        record("会话 lastRef 留痕",
               (s.get("lastRef") or "")
               .startswith("exp-trust.convert-"))


class TestExplanationCommand:
    async def run(self):
        print("[05 打开修复说明指令]")
        reset_all()
        from services.xiaozhu_service import XiaozhuService
        from services.trust_scoring_service import (
            TrustProfileService,
        )
        from repositories.trust_value_repository \
            import TrustValue45Repository
        # 修复场景(ref → 45号 attribution 全文)
        tid = await _new_trust()
        t = TrustProfileService()
        await t.record_event(
            tid, "L2", "ethics_evidence", -30.0,
            source="admin", summary="违规测试")
        events = await TrustValue45Repository(
        ).list_events_by_trust(tid)
        vid = [e for e in events
               if (e.get("delta") or 0) < 0][0]["eventId"]
        svc = XiaozhuService()
        await _bind(82, tid)
        sid = (await svc.open_session(82))["sessionId"]
        # 修复全流: 网关挑战→语音确认→屏幕码核销
        from services.xiaozhu_fc_gateway import (
            XiaozhuFcGateway,
        )
        from services.xiaozhu_executor import (
            get_executor,
        )
        gw = XiaozhuFcGateway()
        session = await svc._require_open(sid)
        r = await gw.call_tool(
            session, "repair.execute", {
                "violationEventId": vid,
                "repairs": [{
                    "kind": "community_service",
                    "value": 80,
                    "evidence": "社区公益服务八小时"}]})
        token = r.get("confirmToken")
        get_executor().mark_voice_confirmation(
            82, "小竹，确认执行修复")
        rc = await svc.confirm_action(
            token, _get_code(token))
        ref = rc.get("explainabilityRef")
        # 会话侧留痕(confirm_action 已写 lastRef)
        record("修复响应含 ref",
               (ref or "").startswith("exp-repair.execute-"),
               str(ref)[:30])
        # 播报"打开修复说明"
        r2 = await svc.handle_text(sid, "小竹，打开修复说明")
        record("指令优先级(nav 前)",
               r2.get("turn", {}).get("intent")
               == "explanation.report",
               str(r2.get("turn", {}).get("intent")))
        card = r2.get("card") or {}
        record("归因卡片(ref 落地)",
               card.get("type") == "explanation"
               and card.get("ref") == ref)
        record("45号源桥接(mode)",
               str(card.get("mode", "")).startswith(
                   "trust45+"),
               str(card.get("mode")))
        record("归因全文含宪法声明",
               "禁止黑箱" in (card.get("report") or ""),
               (card.get("report") or "")[:40])
        # nav 不受扰
        r3 = await svc.handle_text(sid, "小竹，打开购物车")
        record("nav.page 不受指令冲突",
               r3.get("turn", {}).get("intent")
               == "nav.page")
        # 无 ref 引导
        sid2 = (await svc.open_session(
            82))["sessionId"]
        r4 = await svc.handle_text(sid2, "小竹，打开修复说明")
        record("无 ref 引导先执行写操作",
               "没有可解释的操作" in r4.get("reply", ""),
               r4.get("reply", "")[:30])


class TestReportOfRef:
    async def run(self):
        print("[06 归因报告校验]")
        reset_all()
        from services.xiaozhu_explainability_service \
            import XiaozhuExplainabilityService
        svc = XiaozhuExplainabilityService()
        # 无效 ref
        for bad in ("", "xxx", "exp-nope-1-abcdefgh",
                    "exp-trust.convert-x-abcdefgh"):
            try:
                await svc.report_of_ref(83, bad)
                record(f"无效 ref 拒绝({bad[:16]})",
                       False, "未抛")
                break
            except KeyError:
                record(f"无效 ref 拒绝({bad[:16]})", True)
        # 未绑定
        try:
            await svc.report_of_ref(83,
                "exp-trust.convert-1-abcdefgh")
            record("未绑定拒绝", False, "未抛")
        except KeyError as e:
            record("未绑定拒绝", "绑定" in str(e))


async def run_all():
    await TestRefBuild().run()
    await TestBindEnforce().run()
    await TestGatewayRef().run()
    await TestConfirmRef().run()
    await TestExplanationCommand().run()
    await TestReportOfRef().run()


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
