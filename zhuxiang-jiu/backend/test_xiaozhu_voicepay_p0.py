"""48号三期·小竹支付安全网关(L1-L3)专项测试

运行方式:
    python test_xiaozhu_voicepay_p0.py

覆盖(支付安全 L1-L3 落地细则):
    - 评分器入册: voicepay_risk batch52 可学习型
    - L2 四因子: 金额/频次/时段/声纹 三档判定
    - L1 硬规则: R1 黑名单 / R2 频次 / R3 金额(时段限额)
    - L3 只拦不放: review+risky 提级 block /
      review+safe 维持 review / 失败 fail-open /
      block 不被 GLM 放行(不可触达)
    - 三态: off 409 / shadow 留痕不拦 / assist 拦截生效
    - 端到端: 结算下单 → 支付指令 → L1-L3 过 →
      confirm 4 位码 → 核销 → 订单 PENDING→PAID
    - 无待付订单 clarify / 频次拦截留痕
"""

import asyncio
import os
import sys

os.environ["LOCK_MODE"] = "asyncio"
os.environ["STORE_MODE"] = "asyncio"
os.environ["XIAOZHU_MODE"] = "assist"
os.environ["VOICEPAY_MODE"] = "assist"
os.environ.pop("LLM_API_KEY", None)
os.environ["LLM_ENABLED"] = "off"

PASS = 0
FAIL = 0


def record(name, passed, detail=""):
    global PASS, FAIL
    if passed:
        PASS += 1
        print(f"  \u2713 {name}")
    else:
        FAIL += 1
        print(f"  \u2717 {name} \u2014 {detail}")


def reset_all():
    from repositories.store import reset_store as _reset
    _reset()


async def _mk_pending_order(member_id: int,
                            amount: float = 88.0) -> str:
    """直接经 order_service 造一笔 PENDING 订单"""
    from services.order_service import OrderService
    r = await OrderService().create(
        member_id,
        [{"productId": "ZX42-2026B01",
          "productName": "竹奕·竹香便携 42° 250ml",
          "quantity": 1, "unitPrice": amount}],
        {"name": "测试", "phone": "13800000001",
         "province": "山东省", "city": "泰安市",
         "detail": "竹香路 1 号"},
        age_confirmed=True)
    return r["orderId"]


async def main():
    reset_all()
    from services.ai_learning_service import (
        SCORER_REGISTRY, GOVERNANCE_ONLY_SCORERS,
    )
    from services.xiaozhu_voicepay_service import (
        FREQ_MAX, VoicePayRiskScorer, get_gateway,
        voicepay_mode, amount_limit_now,
    )
    from services.xiaozhu_service import XiaozhuService

    print("[01 评分器入册]")
    record("voicepay_risk 入册 batch52 可学习型",
           SCORER_REGISTRY.get("voicepay_risk", {}).get(
               "batch") == 52
           and "voicepay_risk"
           not in GOVERNANCE_ONLY_SCORERS,
           str(SCORER_REGISTRY.get("voicepay_risk")))

    print("[02 L2 四因子三档]")
    scorer = VoicePayRiskScorer()
    r = await scorer.score({"amount": 88.0, "memberId": 1,
                            "attemptsInWindow": 0,
                            "voiceprintVerified": True})
    record("低额+已验证声纹 → allow",
           r.get("decision") == "allow"
           and r.get("score") >= 70,
           f"{r.get('decision')}|{r.get('score')}")
    r = await scorer.score({"amount": 4000.0, "memberId": 1,
                            "attemptsInWindow": 2,
                            "voiceprintVerified": False,
                            "hour": 23})
    record("近限+高频+未验证 → review/block",
           r.get("decision") in ("review", "block"),
           f"{r.get('decision')}|{r.get('score')}")
    r = await scorer.score({"amount": 6000.0, "memberId": 1,
                            "attemptsInWindow": 2,
                            "voiceprintVerified": False,
                            "hour": 2,
                            "amountLimit": 1000.0})
    record("深夜超限+全高危因子 → block",
           r.get("decision") == "block",
           f"{r.get('decision')}|{r.get('score')}")
    record("四因子齐备",
           len(r.get("factors") or []) == 4,
           str(len(r.get("factors") or [])))

    print("[03 L1 硬规则]")
    gw = get_gateway()
    l1 = await gw.check_l1(1, 88.0)
    record("正常会员小额 → L1 allow",
           l1.get("action") == "allow"
           and l1.get("frequency") == 0,
           str(l1))
    # R2 频次: 预灌 2 次尝试 → 第 3 次 block
    gw.record_attempt(1)
    gw.record_attempt(1)
    l1 = await gw.check_l1(1, 88.0)
    r2 = [x for x in l1.get("rules") or []
          if x.get("rule") == "R2_frequency"]
    record("R2 频次窗口第3次 → block",
           l1.get("action") == "block" and bool(r2),
           str(l1.get("rules")))
    gw._attempts.clear()
    # R1 黑名单: 会员禁用
    from repositories.member_repository import (
        MemberRepository,
    )
    await MemberRepository().update_fields(
        1, {"status": 0})
    l1 = await gw.check_l1(1, 88.0)
    r1 = [x for x in l1.get("rules") or []
          if x.get("rule") == "R1_blacklist"]
    record("R1 黑名单(禁用会员) → block",
           l1.get("action") == "block" and bool(r1),
           str(l1.get("rules")))
    await MemberRepository().update_fields(
        1, {"status": 1})
    # R3 金额: 超时段限额
    l1 = await gw.check_l1(1, amount_limit_now() + 1)
    r3 = [x for x in l1.get("rules") or []
          if x.get("rule") == "R3_amount"]
    record("R3 金额超限 → block",
           l1.get("action") == "block" and bool(r3),
           str(l1.get("rules")))

    print("[04 L3 只拦不放]")
    from services import xiaozhu_voicepay_service as vps

    async def fake_llm_risky(*a, **k):
        return "risky"

    async def fake_llm_safe(*a, **k):
        return "safe"

    async def fake_llm_fail(*a, **k):
        return None
    # review + risky → 提级 block
    orig = vps._llm_review
    vps._llm_review = fake_llm_risky
    risk = await gw.precheck({"sessionId": 1}, 1,
                             {"priceDetail": {
                                 "actualAmount": 4200.0}})
    record("review+GLM risky → 提级 block",
           risk.get("decision") == "block"
           and (risk.get("l2") or {}).get("l3Escalated")
           is True
           and risk.get("l3", {}).get("action")
           == "escalate",
           f"{risk.get('decision')}|"
           f"{(risk.get('l2') or {}).get('decision')}")
    # review + safe → 维持 review(不直接执行)
    vps._llm_review = fake_llm_safe
    risk = await gw.precheck({"sessionId": 1}, 1,
                             {"priceDetail": {
                                 "actualAmount": 4200.0}})
    record("review+GLM safe → 维持 review(加强确认)",
           risk.get("decision") == "review",
           str(risk.get("decision")))
    # review + GLM 失败 → fail-open 维持 review
    vps._llm_review = fake_llm_fail
    risk = await gw.precheck({"sessionId": 1}, 1,
                             {"priceDetail": {
                                 "actualAmount": 4200.0}})
    record("review+GLM 失败 → 回退 L2 结论",
           risk.get("decision") == "review",
           str(risk.get("decision")))
    vps._llm_review = orig

    print("[05 三态门控]")
    record("测试档位 assist 生效",
           voicepay_mode() == "assist",
           voicepay_mode())
    os.environ["VOICEPAY_MODE"] = "shadow"
    risk = await gw.precheck({"sessionId": 1}, 1,
                             {"priceDetail": {
                                 "actualAmount": 9000.0}})
    record("shadow 档: block 留痕不拦(shadowOverride)",
           risk.get("decision") == "block"
           and risk.get("shadowOverride") is True,
           f"{risk.get('decision')}|"
           f"{risk.get('shadowOverride')}")
    os.environ["VOICEPAY_MODE"] = "assist"
    risk = await gw.precheck({"sessionId": 1}, 1,
                             {"priceDetail": {
                                 "actualAmount": 9000.0}})
    record("assist 档: block 生效(无 override)",
           risk.get("decision") == "block"
           and risk.get("shadowOverride") is False,
           str(risk.get("shadowOverride")))

    print("[06 端到端(assist): 下单→支付→4位码→PAID]")
    svc = XiaozhuService()
    sid = (await svc.open_session(1))["sessionId"]
    await svc.handle_text(sid, "小竹，看新品")
    await svc.handle_text(sid, "小竹，把这个加入购物车")
    r = await svc.handle_text(sid, "小竹，结算")
    record("语音下单成功",
           (r.get("turn") or {}).get("intent")
           == "cart.submit", str(r.get("reply"))[:50])
    # 语音下单走 45号域——本端到端用订单域单
    order_id = await _mk_pending_order(1)
    r = await svc.handle_text(sid, "小竹，支付订单")
    turn = r.get("turn") or {}
    record("支付指令 → confirm 高敏流",
           turn.get("intent") == "order.pay"
           and r.get("confirmRequired") is True
           and (r.get("card") or {}).get("type")
           == "confirm"
           and bool(r.get("confirmToken")),
           f"{turn.get('intent')}|"
           f"{r.get('confirmRequired')}")
    record("确认摘要含订单号",
           order_id in str((r.get("card") or {})
                           .get("subject") or ""),
           str((r.get("card") or {}).get("subject")))
    # 频次留痕(第一次尝试已计数)
    record("支付尝试已计数",
           get_gateway().attempts_in_window(1) >= 1,
           str(get_gateway().attempts_in_window(1)))
    # 核销(4 位码——进程内单例令牌池读真实码) → 订单 PAID
    from services.xiaozhu_executor import get_executor
    real_code = get_executor()._tokens[
        r.get("confirmToken")]["code"]
    r = await get_executor().confirm(
        r.get("confirmToken"), real_code)
    result = (r or {}).get("result") or {}
    record("4 位码核销 → 真实支付(PAID)",
           r.get("executed") is True
           and result.get("success") is True
           and result.get("status") == "PAID"
           and result.get("orderId") == order_id,
           f"{str(result)[:90]}")
    # 查单确认已支付(PAID 状态名=待发货——按订单号断言)
    r = await svc.handle_text(sid, "小竹，查我的订单")
    items = ((r.get("card") or {}).get("items") or [])
    record("查单确认支付态",
           any(x.get("orderId") == order_id
               for x in items),
           str([(x.get("orderId"), x.get("name"))
                for x in items][:3]))
    await svc.delete_session(sid)

    print("[07 边界]")
    # off 档 409 语义
    os.environ["VOICEPAY_MODE"] = "off"
    sid2 = (await svc.open_session(1))["sessionId"]
    await _mk_pending_order(1)
    try:
        await svc.handle_text(sid2, "小竹，支付订单")
        record("off 档 → 409 语义", False, "未抛 ValueError")
    except ValueError as exc:
        record("off 档 → 409 语义",
               "未开放" in str(exc), str(exc)[:60])
    await svc.delete_session(sid2)
    # 无待付订单 clarify(member2 无任何订单)
    os.environ["VOICEPAY_MODE"] = "assist"
    sid3 = (await svc.open_session(2))["sessionId"]
    r = await svc.handle_text(sid3, "小竹，付款")
    record("无待付订单 → clarify 引导",
           r.get("clarify") is not None
           and "待支付" in str(r.get("reply")),
           f"{r.get('clarify')}|{str(r.get('reply'))[:50]}")
    await svc.delete_session(sid3)

    print("-" * 64)
    print(f"通过 {PASS} 项 / 失败 {FAIL} 项")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
